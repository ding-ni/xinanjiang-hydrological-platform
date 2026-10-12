"""Portable state handoff and immutable numerical results with SQLite annotations."""
import copy
import os
import sys
import tempfile
import json
import threading
import urllib.request
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json, write_json
from contracts import normalize_request
from catalog import case_catalog
from server import TaskManager, ThreadingHTTPServer, handler
from state_import import import_checkpoint, validate_checkpoint
from test_unified_backend import simulation


class ResultMetadataTests(unittest.TestCase):
    def test_names_groups_notes_are_durable_and_never_change_numerical_results(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = TaskManager(Path(folder) / 'tasks')
            self.addCleanup(manager.close)
            write_json(manager.root / 'JOB-result/status.json', {'taskId': 'JOB-result', 'status': 'completed'})
            write_json(manager.root / 'JOB-result/result.json', {'hydrograph': [1.123456789, 0]})
            before = (manager.root / 'JOB-result/result.json').read_bytes()
            body = {'name': '  洪水试调 ', 'collection': '六月', 'notes': 'SM=60', 'favorite': True, 'revision': 0}
            result = manager.results.update('JOB-result', body)
            self.assertEqual(result['name'], '洪水试调')
            self.assertEqual(manager.results.metadata()['JOB-result'], result)
            with self.assertRaises(RuntimeError):
                manager.results.update('JOB-result', body)
            for key, value in [('notes', 'x'*2001), ('favorite', 1), ('revision', True), ('collection', [])]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    manager.results.update('JOB-result', {**body, key: value})
            self.assertEqual((manager.root / 'JOB-result/result.json').read_bytes(), before)
            manager.close()


class ResultsHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = TaskManager(Path(self.temp.name) / 'tasks')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.manager, Path(self.temp.name)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        self.manager.close(); self.temp.cleanup()

    def call(self, path, body=None, headers=None):
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/api/model/{path}',
            data=None if body is None else json.dumps(body).encode(), headers=headers or {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_metadata_roundtrip_and_conflict(self):
        write_json(self.manager.root / 'JOB-http/status.json', {'taskId': 'JOB-http', 'status': 'completed'})
        write_json(self.manager.root / 'JOB-http/result.json', {'taskId': 'JOB-http'})
        body = {'name': '汇总', 'collection': '试调', 'notes': '记录', 'favorite': False, 'revision': 0}
        self.assertEqual(self.call('result-metadata/JOB-http', body)[0], 200)
        self.assertEqual(self.call('result-catalog')[1]['metadata']['JOB-http']['name'], '汇总')
        self.assertEqual(self.call('result-metadata/JOB-http', body)[0], 409)
        self.assertEqual(self.call('result-metadata/../../outside', body)[0], 400)

    def test_import_schema_origin_and_size_boundaries(self):
        self.assertTrue(self.call('state-catalog')[1]['checkpointImport'])
        self.assertEqual(self.call('states/import', {'checkpoint': {}})[0], 400)
        self.assertEqual(self.call('states/import', {'checkpoint': {}}, {'Content-Type': 'application/json', 'Origin': 'http://external.invalid'})[0], 400)
        self.assertEqual(self.call('states/import', {'checkpoint': {}, 'name': 'x' * (2 * 1024 * 1024)})[0], 400)


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real checkpoint portability regression')
class StateImportTests(unittest.TestCase):
    def test_import_export_idempotence_validation_and_exact_continuation(self):
        import worker
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source_root, target_root = Path(source_dir), Path(target_dir)
            manager = TaskManager(target_root / 'tasks')
            try:
                def run(root, request, suffix):
                    task_id = 'JOB-portability-' + suffix
                    path = root / 'tasks' / task_id
                    write_json(path / 'request.json', normalize_request(request, case_catalog()))
                    write_json(path / 'status.json', {'taskId': task_id, 'status': 'running'})
                    with patch('worker.RUNTIME', root):
                        worker.ModelJob(path).run()
                    write_json(path / 'status.json', {'taskId': task_id, 'status': 'completed'})
                    return read_json(path / 'checkpoint.json'), read_json(path / 'result.json')
                source, _ = run(source_root, simulation('2017-06-20T00:00', '2017-06-20T02:00'), 'source')
                original = copy.deepcopy(source)
                with patch.object(manager, 'update', side_effect=RuntimeError('interrupted import')):
                    with self.assertRaises(RuntimeError):
                        import_checkpoint(manager, {'checkpoint': source, 'name': '移机状态'})
                self.assertFalse(any(manager.available_artifact(row['taskId']) for row in manager.list()))
                imported = import_checkpoint(manager, {'checkpoint': source, 'name': '移机状态'})
                state = imported['state']
                self.assertEqual(source, original)
                for key in ('land', 'routing', 'parameters', 'gridIds', 'gridGeometry', 'time'):
                    self.assertEqual(state[key], source[key])
                self.assertTrue(import_checkpoint(manager, {'checkpoint': source})['reused'])
                self.assertEqual(manager.states.checkpoint(state['stateVersionId']), state)
                self.assertEqual(manager.states.metadata()[state['stateVersionId']]['name'], '移机状态')
                with self.assertRaises(ValueError):
                    manager.clone(imported['task']['taskId'])
                for mutate in [lambda c: c['gridIds'].reverse(), lambda c: c['gridGeometry'].reverse(),
                               lambda c: c['land']['WU'].__setitem__(0, True), lambda c: c['land']['S'].__setitem__(0, 1000),
                               lambda c: c['land']['WU'].__setitem__(0, float('nan')),
                               lambda c: c['routing']['prev_in'].pop(), lambda c: c['routing']['prev_out'].__setitem__(0, -1),
                               lambda c: c.update(inputBundleManifestSHA256='other'), lambda c: c.update(routingManifestSHA256='other'),
                               lambda c: c['parameters'].update({'runoff.WM': 1}), lambda c: c.update(time='2017-06-20T01:01'),
                               lambda c: c.update(petMode='different'), lambda c: c.update(parameters=[]), lambda c: c.update(land=None),
                               lambda c: c.update(modelImplementationSHA256='0' * 64),
                               lambda c: c.update(scienceConfigurationSHA256='0' * 64),
                               lambda c: c.update(petConfigurationSHA256='0' * 64), lambda c: c.pop('modelImplementationSHA256')]:
                    invalid = copy.deepcopy(source); mutate(invalid)
                    with self.assertRaises(ValueError):
                        validate_checkpoint(manager, invalid)
                request = simulation('2017-06-20T03:00', '2017-06-20T06:00')
                request['runType'] = 'stateContinuation'
                request['period']['initialStateVersionId'] = source['stateVersionId']
                final_source, result_source = run(source_root, request, 'original-next')
                request['period']['initialStateVersionId'] = state['stateVersionId']
                final_target, result_target = run(target_root, request, 'imported-next')
                self.assertEqual(result_source['hydrograph'], result_target['hydrograph'])
                self.assertEqual(final_source['land'], final_target['land'])
                self.assertEqual(final_source['routing'], final_target['routing'])
                manager.close()
                manager = TaskManager(target_root / 'tasks')
                self.assertTrue(import_checkpoint(manager, {'checkpoint': source})['reused'])
                original_path = manager.root / imported['task']['taskId'] / 'source-checkpoint.json'
                altered = copy.deepcopy(source); altered['land']['S'][0] += 1
                write_json(original_path, altered)
                with self.assertRaises(ValueError):
                    manager.states.checkpoint(state['stateVersionId'])
            finally:
                manager.close()
