"""Direct saved parameters, durable scheduling and observation/forecast separation."""
import copy
import os
import sys
import tempfile
import time
import threading
import json
import urllib.request
import urllib.error
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automatic_forecasts import AutomaticForecasts
from common import read_json, write_json
from forecast_products import ForecastProducts, freeze_forecast_parameters
from hydrological_operations import CONTEXT, HydrologicalOperations
from test_forecast import ISSUE, forecast_request, rain_body


def product_body(values=None):
    return {**rain_body(values), 'sourceId': 'weather-product', 'sourceLabel': '导入逐小时降雨产品',
            'unit': 'mm', 'timeConvention': 'hour-ending'}


class ImportedProductsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.products = ForecastProducts(self.temp.name)

    def test_valid_time_slicing_and_latest_source_no_future_issue(self):
        first = self.products.register(product_body([1, 2, 3, 4]))
        second = self.products.register({**product_body([5, 6, 7]), 'issueTime': '2017-06-19T12:00:00'})
        self.assertEqual(self.products.resolve(first['versionId']), first)
        self.assertEqual(self.products.latest('weather-product', ISSUE, 2), first)
        self.assertEqual(self.products.latest('weather-product', '2017-06-19T12:00:00', 2), second)
        self.assertEqual(self.products.window(first, '2017-06-19T12:00:00', 2)['hourlyRainfallMm'], [2, 3])
        self.assertIsNone(self.products.latest('weather-product', '2017-06-19T15:00:00', 2))
        self.assertIsNone(self.products.latest('other', ISSUE, 2))

    def test_grids_reorder_exactly_and_reject_silent_spatial_substitution(self):
        import numpy as np
        body = product_body()
        body.pop('hourlyRainfallMm')
        body.update(gridIds=['B', 'A'], hourlyGridRainfallMm=[[1, 5], [2, 6]])
        row = self.products.register(body)
        self.assertEqual(self.products.resolve(row['versionId']), row)
        np.testing.assert_array_equal(self.products.precipitation(row, ['A', 'B']), [[5, 1], [6, 2]])
        for ids in (['A'], ['A', 'C']):
            with self.assertRaisesRegex(ValueError, '网格'):
                self.products.precipitation(row, ids)
        for change in ({'gridIds': ['A', 'A']}, {'hourlyGridRainfallMm': [[1]]},
                       {'hourlyGridRainfallMm': [[1, -1]]}, {'unit': 'mm/h'}, {'timeConvention': 'hour-starting'}):
            with self.assertRaises(ValueError):
                self.products.register({**body, **change})

    def test_automatic_saved_parameter_needs_no_review_or_release(self):
        from catalog import case_catalog
        params = case_catalog()['cases']['nananxi-xufan:1km']['parameters']
        saved = {'id': 'PARAMSET-test', 'context': CONTEXT, 'parameters': params,
                 'routing': {'versionId': 'routing'}, 'science': {'versionId': 'science'}}
        manager = SimpleNamespace(parameter_schemes=SimpleNamespace(get=Mock(return_value=saved)))
        state = {'time': ISSUE, 'stateVersionId': 'STATE-JOB-test', 'routingVersionId': 'routing',
                 'parameters': {'runoff.'+k: v for k, v in params.items()}}
        request = forecast_request()
        request['issue']['mode'] = 'automatic'
        request['inputs']['parameterVersionId'] = saved['id']
        request['science'] = saved['science']
        with patch('state_integrity.assert_state_input_compatibility'):
            freeze_forecast_parameters(request, state, manager)
            self.assertEqual(request['parameters'], params)
            for key, value in [('routingVersionId', 'wrong'), ('parameters', {'runoff.WM': 1})]:
                with self.assertRaises((ValueError, KeyError)):
                    freeze_forecast_parameters(copy.deepcopy(request), {**state, key: value}, manager)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.instant = datetime.fromisoformat(ISSUE)
        self.products = ForecastProducts(self.temp.name)
        self.products.register(product_body([1, 2, 3, 4]))
        self.parameter = {'id': 'PARAMSET-test', 'name': '直接采用', 'context': CONTEXT}
        self.operations = Mock()
        self.operations.submit.return_value = {'taskId': 'OP-first'}
        self.manager = SimpleNamespace(runtime_root=Path(self.temp.name), forecast_products=self.products,
            parameter_schemes=SimpleNamespace(get=Mock(return_value=self.parameter)),
            schemes=SimpleNamespace(history_defaults=Mock(return_value={'rainfallMethod': 'stationIdw'})),
            database=Mock(), operations=self.operations)
        self.scheduler = AutomaticForecasts(self.manager, clock=lambda: self.instant, start_thread=False)
        self.body = {'parameterSchemeId': self.parameter['id'], 'sourceId': 'weather-product',
                     'firstIssueTime': ISSUE, 'issueIntervalHours': 1, 'leadHours': 2, 'requestId': 'START-test'}
        with patch('station_network.local_station_network', return_value={}):
            self.row = self.scheduler.create(self.body)

    def test_waits_no_rain_or_future_issue_and_never_substitutes_observed_rain(self):
        self.instant -= timedelta(hours=1)
        self.scheduler.tick()
        self.assertIn('起报时刻', self.scheduler.get(self.row['id'])['message'])
        self.operations.submit.assert_not_called()
        self.instant += timedelta(hours=1)
        self.scheduler.update(self.scheduler.get(self.row['id']), sourceId='not-yet-supplied')
        self.scheduler.tick()
        self.assertIn('等待降雨产品', self.scheduler.get(self.row['id'])['message'])
        self.operations.submit.assert_not_called()

    def test_restart_submission_is_idempotent_and_only_observed_state_is_carried(self):
        self.scheduler.tick()
        request = self.operations.submit.call_args.args[0]
        self.operations.get.return_value = {'status': 'running'}
        self.scheduler.tick()
        self.assertEqual(self.operations.submit.call_count, 1)
        # Simulate crash between server submission and persisting its ID.
        self.scheduler.update(self.scheduler.get(self.row['id']), activeOperationId=None)
        restored = AutomaticForecasts(self.manager, clock=lambda: self.instant, start_thread=False)
        restored.tick()
        self.assertEqual(self.operations.submit.call_args.args[0], request)
        self.operations.get.return_value = {'status': 'completed'}
        self.operations.result.return_value = {'primaryTaskId': 'JOB-forecast', 'observationStateVersionId': 'STATE-JOB-observed',
                                              'forecastRain': {'versionId': 'QPF-test'}, 'endStateVersionId': 'DO-NOT-CARRY'}
        restored.tick()
        row = restored.get(self.row['id'])
        self.assertEqual(row['completedCount'], 1)
        self.assertEqual(row['observationStateVersionId'], 'STATE-JOB-observed')
        self.assertIsNone(row['activeOperationId'])
        self.instant += timedelta(hours=1)
        restored.tick()
        request = self.operations.submit.call_args.args[0]
        self.assertEqual(request['observationStateVersionId'], 'STATE-JOB-observed')
        self.assertEqual(request['issueTime'], '2017-06-19T12:00:00')
        restored.control(self.row['id'], 'stop')
        self.operations.cancel.assert_called_once_with('OP-first')
        count = self.operations.submit.call_count
        restored.tick()
        self.assertEqual(count, self.operations.submit.call_count)

    def test_missing_data_retry_preserves_gap_message_and_is_not_success(self):
        self.scheduler.tick()
        self.operations.get.return_value = {'status': 'failed', 'errorMessage': '缺实测资料',
            'issues': [{'missingIntervals': [{'start': ISSUE, 'end': ISSUE}]}]}
        self.scheduler.tick()
        row = self.scheduler.get(self.row['id'])
        self.assertEqual(row['completedCount'], 0)
        self.assertEqual(row['nextIssueTime'], ISSUE)
        self.assertEqual(row['message'], '缺实测资料')
        self.assertEqual(len(row['issues']), 1)
        count = self.operations.submit.call_count
        self.scheduler.tick()
        self.assertEqual(count, self.operations.submit.call_count)
        self.instant += timedelta(minutes=2)
        self.scheduler.tick()
        self.assertEqual(count+1, self.operations.submit.call_count)

    def test_create_idempotent_rejects_ambiguous_second_active_and_bad_settings(self):
        with patch('station_network.local_station_network', return_value={}):
            self.assertEqual(self.scheduler.create(self.body), self.row)
            with self.assertRaisesRegex(ValueError, '先停止'):
                self.scheduler.create({**self.body, 'requestId': 'NEW'})
            for change in ({'issueIntervalHours': True}, {'issueIntervalHours': 0}, {'leadHours': 0},
                           {'leadHours': 73}, {'sourceId': 'unknown'}, {'review': True}):
                with self.assertRaises(ValueError):
                    self.scheduler.create({**self.body, **change})

    def test_catch_up_freezes_end_resumes_state_and_stops_without_duplicate_issues(self):
        self.scheduler.control(self.row['id'], 'stop')
        self.instant += timedelta(hours=1, minutes=35)
        catch_up = self.scheduler.control(self.row['id'], 'catch-up')
        self.assertEqual(catch_up['catchUpUntil'], '2017-06-19T12:00:00')
        self.assertEqual(catch_up['runMode'], 'catchup')
        self.scheduler.tick()
        self.operations.get.return_value = {'status':'completed'}
        self.operations.result.return_value = {'primaryTaskId':'JOB-first', 'observationStateVersionId':'STATE-first',
                                               'forecastRain':{'versionId':'QPF-first'}}
        # Advancing the clock and restarting must not extend this batch.
        self.instant += timedelta(hours=5)
        restored = AutomaticForecasts(self.manager, clock=lambda:self.instant, start_thread=False)
        restored.tick()
        self.assertEqual(self.operations.submit.call_args.args[0]['issueTime'], '2017-06-19T12:00:00')
        self.assertEqual(self.operations.submit.call_args.args[0]['observationStateVersionId'], 'STATE-first')
        self.operations.result.return_value = {'primaryTaskId':'JOB-second', 'observationStateVersionId':'STATE-second',
                                               'forecastRain':{'versionId':'QPF-second'}}
        restored.tick()
        row = restored.get(self.row['id'])
        self.assertFalse(row['enabled'])
        self.assertIn('补做完成', row['message'])
        self.assertEqual(row['completedCount'], 2)
        self.assertEqual(row['latestIssueTime'], '2017-06-19T12:00:00')
        self.assertEqual(len(restored.issues(row['id'])['issues']), 2)
        restored.tick()
        self.assertEqual(self.operations.submit.call_count, 2)
        resumed = restored.control(row['id'], 'start')
        self.assertIsNone(resumed['catchUpUntil'])
        self.assertEqual(resumed['nextIssueTime'], '2017-06-19T13:00:00')
        restored.tick()
        self.assertEqual(self.operations.submit.call_args.args[0]['observationStateVersionId'], 'STATE-second')
        self.assertEqual(self.operations.submit.call_args.args[0]['issueTime'], '2017-06-19T13:00:00')

    def test_stop_reconciles_a_completed_issue_but_never_starts_another(self):
        self.scheduler.tick()
        self.scheduler.control(self.row['id'], 'stop')
        self.operations.get.return_value = {'status':'completed'}
        self.operations.result.return_value = {'primaryTaskId':'JOB-done', 'observationStateVersionId':'STATE-done',
                                               'forecastRain':{'versionId':'QPF-done'}}
        self.instant += timedelta(hours=1)
        self.scheduler.tick()
        row = self.scheduler.get(self.row['id'])
        self.assertFalse(row['enabled'])
        self.assertEqual(row['completedCount'], 1)
        self.assertEqual(row['status'], 'stopped')
        self.assertEqual(self.operations.submit.call_count, 1)
        # The next issue must resume at 12, not repeat the successful 11 issue.
        self.assertEqual(row['nextIssueTime'], '2017-06-19T12:00:00')

    def test_catch_up_no_gap_or_missing_archive_is_explicit(self):
        with self.assertRaisesRegex(ValueError, '先关闭'):
            self.scheduler.control(self.row['id'], 'catch-up')
        self.scheduler.control(self.row['id'], 'stop')
        self.instant -= timedelta(hours=1)
        with self.assertRaisesRegex(ValueError, '没有需要补做'):
            self.scheduler.control(self.row['id'], 'catch-up')
        self.instant += timedelta(hours=1)
        self.scheduler.update(self.scheduler.get(self.row['id']), sourceId='missing-archive')
        self.scheduler.control(self.row['id'], 'catch-up')
        self.scheduler.tick()
        row = self.scheduler.get(self.row['id'])
        self.assertIn('历史产品', row['message'])
        self.assertEqual(row['completedCount'], 0)
        self.assertEqual(row['nextIssueTime'], ISSUE)
        self.operations.submit.assert_not_called()

    def test_http_start_stop_and_cross_origin_boundary(self):
        from server import TaskManager, ThreadingHTTPServer, handler
        with tempfile.TemporaryDirectory() as temp:
            manager = TaskManager(Path(temp)/'tasks')
            manager.automatic_forecasts = self.scheduler
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(manager, Path(temp)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(path, body=None, origin=None):
                headers = {'Content-Type':'application/json'}
                if origin: headers['Origin'] = origin
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/model/'+path,
                    data=json.dumps(body).encode() if body is not None else None, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read())
            try:
                path = 'automatic-forecasts/'+self.row['id']
                self.assertEqual(request('automatic-forecasts')[0], 200)
                self.assertEqual(request(path+'/stop', {}, 'https://foreign.example')[0], 400)
                self.assertTrue(self.scheduler.get(self.row['id'])['enabled'])
                self.assertFalse(request(path+'/stop', {})[1]['enabled'])
                self.assertTrue(request(path+'/start', {})[1]['enabled'])
                self.assertEqual(request(path+'/catch-up', {})[0], 400)
                request(path+'/stop', {})
                self.assertEqual(request(path+'/catch-up', {})[1]['runMode'], 'catchup')
                self.assertEqual(request(path+'/issues')[1], {'issues':[]})
                self.assertEqual(request(path+'/stop', {'review':True})[0], 400)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3); manager.close()


class ForecastImplementationIdentityTests(unittest.TestCase):
    def test_forecast_helper_is_hashed_without_breaking_legacy_snapshot_manifest(self):
        import shutil
        from implementation_versions import (snapshot_current, frozen_worker, implementation_records,
                                               digest, LEGACY_PLATFORM_FILES)
        from science_context import model_implementation_identity
        with tempfile.TemporaryDirectory() as temp:
            current = snapshot_current(temp)
            self.assertEqual(current, model_implementation_identity()['modelImplementationSHA256'])
            self.assertIn('platform/forecast_products.py', [r['path'] for r in implementation_records()])
            folder = Path(temp)/'implementations'/current
            legacy = digest(implementation_records(folder/'backend', platform_files=LEGACY_PLATFORM_FILES))
            old = Path(temp)/'implementations'/legacy
            shutil.copytree(folder, old)
            manifest = read_json(old/'manifest.json')
            manifest.pop('identityPlatformFiles')
            manifest['modelImplementationSHA256'] = legacy
            write_json(old/'manifest.json', manifest)
            self.assertEqual(frozen_worker(legacy, temp), old/'backend/worker.py')


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real automatic forecasting regression')
class AutomaticNumericalTests(unittest.TestCase):
    def test_two_real_issues_continue_observation_state_and_forecast_full_basin(self):
        import numpy as np
        from operation_fixtures import seed_daily_observations
        from server import TaskManager, InputTaskManager
        from test_unified_backend import simulation
        with tempfile.TemporaryDirectory() as temp:
            manager = TaskManager(Path(temp)/'tasks')
            try:
                seed_daily_observations(manager)
                manager.input_tasks = InputTaskManager(Path(temp)/'input-tasks', manager.products)
                manager.operations = HydrologicalOperations(manager, manager.input_tasks)
                def finish(task, service):
                    deadline = time.monotonic()+180
                    while time.monotonic() < deadline:
                        row = service.get(task['taskId'])
                        if row['status'] not in {'queued', 'running'}:
                            self.assertEqual(row['status'], 'completed', {'taskId':row['taskId'],'error':row.get('errorMessage'),'issues':row.get('issues')})
                            return service.result(task['taskId'])
                        time.sleep(.1)
                    self.fail('automatic forecast timeout')
                baseline = finish(manager.submit(simulation()), manager)
                saved = manager.parameter_schemes.save({'name': '无需审核直接全流域', 'sourceTaskId': baseline['taskId']})
                original = copy.deepcopy(saved)
                state = manager.states.checkpoint(baseline['endStateVersionId'])
                body = product_body()
                body.pop('hourlyRainfallMm')
                body.update(gridIds=list(reversed(state['gridIds'])),
                            hourlyGridRainfallMm=[[float((i+h)%5) for i in range(len(state['gridIds']))] for h in range(3)])
                product = manager.forecast_products.register(body)
                first = finish(manager.operations.submit({'operation': 'manualForecast', 'parameterSchemeId': saved['id'],
                    'context': CONTEXT, 'issueTime': ISSUE, 'leadHours': 2, 'forecastRainVersionId': product['versionId']}), manager.operations)
                second = finish(manager.operations.submit({'operation': 'automaticForecast', 'parameterSchemeId': saved['id'],
                    'context': CONTEXT, 'issueTime': '2017-06-19T12:00:00', 'leadHours': 2,
                    'observationStateVersionId': first['observationStateVersionId'], 'forecastRainVersionId': product['versionId']}), manager.operations)
                self.assertEqual(manager.parameter_schemes.get(saved['id']), original)
                self.assertEqual(first['issue']['mode'], 'manual')
                self.assertEqual(second['issue']['mode'], 'automatic')
                self.assertFalse((Path(temp)/'automatic-forecasts').exists())
                self.assertEqual(first['gridCount'], 110)
                self.assertEqual(len(second['hydrograph']['forecast']), 2)
                self.assertIsNone(first['endStateVersionId'])
                self.assertIsNone(second['stateVersionId'])
                self.assertEqual(second['parentStateVersionId'], second['observationStateVersionId'])
                checkpoint = manager.states.checkpoint(second['observationStateVersionId'])
                self.assertEqual(checkpoint['parentStateVersionId'], first['observationStateVersionId'])
                self.assertEqual(checkpoint['time'], '2017-06-19T12:00:00')
                self.assertEqual(manager.get(second['observationTaskId'])['stateRole'], 'observationBase')
                with np.load(manager.root/second['primaryTaskId']/'grid-history.npz', allow_pickle=False) as arrays:
                    expected = ForecastProducts.precipitation(manager.forecast_products.window(product, checkpoint['time'], 2), checkpoint['gridIds'])
                    np.testing.assert_allclose(arrays['precipitation'], expected)
                    self.assertLess(float(np.abs(arrays['water_balance_error']).max()), 1e-8)
                # The observation trajectory may not contain any future forecast forcing.
                self.assertEqual(checkpoint['drivingIdentity']['end'], checkpoint['time'])
                self.assertFalse((manager.root/second['primaryTaskId']/'checkpoint.json').exists())
                print('AUTO_REAL two issues / 110 grids / observation continuation / spatial QPF / no review passed', flush=True)
            finally:
                manager.close()


if __name__ == '__main__':
    unittest.main()
