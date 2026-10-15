"""Quiet HTTP queries and safe browser opening after platform startup."""
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import PlatformHTTPServer, handler, main


class RequestLogTests(unittest.TestCase):
    def request(self, path, *, verbose=False, host='127.0.0.1', manager_error=None):
        manager = Mock()
        manager.products.resolve.return_value = {'bundleVersionId': 'test', 'manifestSHA256': 'test'}
        manager.list.side_effect = manager_error
        with patch('server.InputService'):
            request_type = handler(manager, Path.cwd(), input_manager=Mock(), verbose_requests=verbose)
        request = request_type.__new__(request_type)
        request.command = 'GET'
        request.path = path
        request.headers = {'Host': host}
        request.request_version = 'HTTP/1.1'
        request.requestline = f'GET {path} HTTP/1.1'
        request.client_address = ('127.0.0.1', 12345)
        request.wfile = io.BytesIO()
        logs = io.StringIO()
        with redirect_stderr(logs):
            request.dispatch()
        headers, body = request.wfile.getvalue().split(b'\r\n\r\n', 1)
        return int(headers.split(b' ', 2)[1]), json.loads(body), logs.getvalue()

    def test_successful_health_query_is_quiet_by_default(self):
        code, response, logs = self.request('/api/model/health')
        self.assertEqual(code, 200)
        self.assertEqual(response['status'], 'ok')
        self.assertEqual(logs, '')

    def test_http_errors_and_exception_tracebacks_remain_visible(self):
        cases = [
            (400, '/api/model/health', {'host': 'external.invalid'}),
            (404, '/api/model/missing', {}),
            (409, '/api/model/tasks', {'manager_error': RuntimeError('计算尚未完成')}),
            (500, '/api/model/tasks', {'manager_error': OSError('test storage failure')}),
        ]
        for expected, path, options in cases:
            with self.subTest(status=expected):
                code, response, logs = self.request(path, **options)
                self.assertEqual(code, expected)
                self.assertIn('error', response)
                self.assertIn(f'"GET {path} HTTP/1.1" {expected}', logs)
                if expected == 500:
                    self.assertIn('Traceback', logs)
                    self.assertIn('test storage failure', logs)

    def test_verbose_mode_keeps_successful_access_logs(self):
        code, _, logs = self.request('/api/model/health', verbose=True)
        self.assertEqual(code, 200)
        self.assertIn('"GET /api/model/health HTTP/1.1" 200', logs)


class StartupTests(unittest.TestCase):
    def run_main(self, *options, browser_error=None, browser_result=True, import_error=None):
        events = []
        server = Mock(server_port=8878)
        manager = Mock()
        request_type = object()

        def import_database():
            events.append('import')
            if import_error is not None:
                raise import_error

        def prepare_handler(*args, **kwargs):
            events.append('handler')
            return request_type

        def open_browser(address):
            events.append('browser')
            self.assertIs(server.RequestHandlerClass, request_type)
            if browser_error is not None:
                raise browser_error
            return browser_result

        manager.database.import_existing.side_effect = import_database
        server.serve_forever.side_effect = lambda: events.append('serve')
        output, logs = io.StringIO(), io.StringIO()
        with (patch('sys.argv', ['server.py', *options]),
              patch('server.PlatformHTTPServer', return_value=server),
              patch('server.WorkspaceLock') as ownership,
              patch('server.workspace_identity'),
              patch('server.TaskManager', return_value=manager),
              patch('delivered_schemes.migrate_delivered_schemes', return_value=[]),
              patch('server.handler', side_effect=prepare_handler) as prepared,
              patch('server.webbrowser.open', side_effect=open_browser) as browser,
              redirect_stdout(output), redirect_stderr(logs)):
            failure = None
            try:
                main()
            except Exception as error:
                failure = error
        return SimpleNamespace(server=server, manager=manager, ownership=ownership.return_value,
                               prepared=prepared, browser=browser, events=events,
                               output=output.getvalue(), logs=logs.getvalue(), failure=failure)

    def test_port_has_one_owner(self):
        server = PlatformHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            with self.assertRaises(OSError):
                duplicate = PlatformHTTPServer(server.server_address, BaseHTTPRequestHandler)
                duplicate.server_close()
        finally:
            server.server_close()

    def test_conflict_does_not_recover_runtime(self):
        server = PlatformHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            with (patch('sys.argv', ['server.py', '--port', str(server.server_port), '--open-browser']),
                  patch('server.TaskManager') as manager, patch('server.WorkspaceLock') as ownership,
                  patch('server.webbrowser.open') as browser):
                with self.assertRaises(OSError):
                    main()
                manager.assert_not_called()
                ownership.assert_not_called()
                browser.assert_not_called()
        finally:
            server.server_close()

    def test_direct_cli_does_not_open_a_browser(self):
        run = self.run_main()
        self.assertIsNone(run.failure)
        self.assertEqual(run.events, ['import', 'handler', 'serve'])
        run.browser.assert_not_called()
        self.assertFalse(run.prepared.call_args.kwargs['verbose_requests'])

    def test_browser_opens_once_after_import_and_handler_are_ready(self):
        run = self.run_main('--open-browser', '--verbose-requests', '--host', 'localhost')
        self.assertIsNone(run.failure)
        self.assertEqual(run.events, ['import', 'handler', 'browser', 'serve'])
        run.browser.assert_called_once_with('http://localhost:8878')
        self.assertTrue(run.prepared.call_args.kwargs['verbose_requests'])
        run.server.server_close.assert_called_once_with()
        run.manager.close.assert_called_once_with()
        run.ownership.__exit__.assert_called_once_with()

    def test_browser_failure_still_serves_and_prints_manual_address(self):
        for error, result in [(None, False), (OSError('no default browser'), True)]:
            with self.subTest(error=error):
                run = self.run_main('--open-browser', browser_error=error, browser_result=result)
                self.assertIsNone(run.failure)
                self.assertEqual(run.events, ['import', 'handler', 'browser', 'serve'])
                self.assertIn('请手动访问：http://127.0.0.1:8878', run.logs)
                run.server.serve_forever.assert_called_once_with()
                run.manager.close.assert_called_once_with()

    def test_failed_import_stops_before_browser_and_keeps_failure(self):
        failure = OSError('existing observations unavailable')
        run = self.run_main('--open-browser', import_error=failure)
        self.assertIs(run.failure, failure)
        self.assertEqual(run.events, ['import'])
        run.prepared.assert_not_called()
        run.browser.assert_not_called()
        run.server.serve_forever.assert_not_called()
        run.server.server_close.assert_called_once_with()
        run.manager.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main(verbosity=2)
