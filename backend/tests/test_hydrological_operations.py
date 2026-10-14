"""Staff workflows use real numeric tasks, independent of a connected page."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json
from server import TaskManager, InputTaskManager
from hydrological_operations import HydrologicalOperations, flood_catalog
from model_schemes import ModelSchemes, input_definition
from hydro_database import HydroDatabase
from test_unified_backend import simulation, calibration


class SchemeEditorTests(unittest.TestCase):
    def test_hindcast_lead_is_checked_against_data_instead_of_a_fixed_year_limit(self):
        with tempfile.TemporaryDirectory() as temporary, patch('hydrological_operations.ThreadPoolExecutor'):
            manager = TaskManager(Path(temporary) / 'tasks')
            manager.operations = HydrologicalOperations(manager, None)
            try:
                parameter = {'id': 'PARAMSET-test', 'name': '验收参数', 'context': simulation()['context']}
                body = {'operation': 'rollingHindcast', 'parameterSchemeId': parameter['id'],
                        'context': parameter['context'], 'issueTime': '2023-12-31T00:00',
                        'lastIssueTime': '2024-01-01T00:00', 'leadHours': 10000}
                with patch.object(manager.parameter_schemes, 'get', return_value=parameter):
                    submitted = manager.operations.submit(body)
                    self.assertEqual(submitted['runConfig']['leadHours'], 10000)
                    for lead in (0, -1, 2.5, True):
                        with self.subTest(lead=lead), self.assertRaisesRegex(ValueError, '正整数'):
                            manager.operations.submit({**body, 'leadHours': lead})
            finally:
                manager.close()

    def test_name_before_selection_and_incomplete_editor_preserve_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            schemes = ModelSchemes(HydroDatabase(temporary))
            row = schemes.save({'name': '汛期率定', 'context': simulation()['context']})
            self.assertEqual(row['revision'], 0)
            row = schemes.save_editor(row['id'], {'editor': {'events': [], 'algorithm': 'RANDOM_SEARCH'}})
            self.assertEqual(row['editor']['events'], [])
            request = simulation()
            request['inputs']['bundleVersionId'] = 'NANANXI_QC_20260819_CONTROL_V2'
            saved = schemes.save({'id': row['id'], 'revision': 0, 'name': row['name'],
                'definition': input_definition(request), 'calculationDraft': request})
            self.assertEqual(saved['revision'], 1)
            schemes.save_editor(row['id'], {'editor': {'events': [{'id': 'new'}]}})
            self.assertEqual(schemes.get(row['id'], 1)['calculationDraft'], request)

    def test_complete_catalog_is_not_the_selected_bundle_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = HydroDatabase(temporary)
            with database.connect() as db:
                import json
                db.execute('INSERT INTO projects VALUES (?,?)', ('nananxi-xufan', '南安溪'))
                for i in range(226):
                    event = {'eventId': 'F-' + str(i), 'start': '2024-07-01T00:00', 'end': '2024-07-02T00:00', 'observedInputs': {'observedPeakFlowM3s': i}}
                    db.execute('INSERT INTO flood_events VALUES (?,?,?,?,?,?)', ('nananxi-xufan', event['eventId'], event['start'], event['end'], str(i), json.dumps(event)))
            catalog = flood_catalog(database)
            self.assertEqual(catalog['count'], 226)
            self.assertTrue(all(row['purpose'] == '未使用' for row in catalog['events']))


@unittest.skipUnless(os.environ.get('XAJ_OPERATION_NUMERIC_TEST') == '1', 'explicit real model acceptance')
class RealOperationTests(unittest.TestCase):
    def test_named_scheme_calibration_prepares_and_keeps_final_event_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = TaskManager(Path(temporary) / 'tasks')
            manager.input_tasks = InputTaskManager(Path(temporary) / 'input-tasks', manager.products)
            manager.operations = HydrologicalOperations(manager, manager.input_tasks)
            try:
                request = calibration()
                request['inputs']['bundleVersionId'] = 'NANANXI_QC_20260819_CONTROL_V2'
                scheme = manager.schemes.save({'name': '场次率定验收', 'definition': input_definition(request), 'calculationDraft': request})
                request['scheme'] = {'id': scheme['id'], 'revision': scheme['revision']}
                submitted = manager.operations.submit({'operation': 'calibration', 'request': request, 'rainfallMethod': 'reuse'})
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    task = manager.operations.get(submitted['taskId'])
                    if task['status'] not in {'queued', 'running'}:
                        break
                    time.sleep(.15)
                self.assertEqual(task['status'], 'completed', task)
                result = manager.operations.result(task['taskId'])
                self.assertEqual(result['optimizer']['evaluations'], 4)
                self.assertEqual(result['optimizationGroups'], ['CALIBRATION'])
                self.assertEqual(len(result['eventStates']), len(request['period']['events']))
                self.assertEqual(manager.parameter_schemes.list()['schemes'], [])
                self.assertFalse(any((manager.runtime_root / 'parameters').glob('*.json')))
                for event in result['eventStates']:
                    replay = manager.result(event['taskId'])
                    expected = result['hydrographs'][event['eventId']]['forecast']
                    actual = replay['hydrograph']['forecast']
                    self.assertEqual(actual, expected)
                parameter = manager.parameter_schemes.save({'name': '验收参数', 'sourceTaskId': result['primaryTaskId']})
                self.assertEqual(parameter['parameters'], result['bestParameters'])
            finally:
                manager.close()

    def test_saved_parameter_fixed_simulation_hindcast_rolling_and_continuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = TaskManager(Path(temporary) / 'tasks')
            manager.input_tasks = InputTaskManager(Path(temporary) / 'input-tasks', manager.products)
            manager.operations = HydrologicalOperations(manager, manager.input_tasks)
            manager.schemes.defaults({'rainfallMethod': 'reuse', 'stationSourceId': None})

            def finish(task, service):
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    current = service.get(task['taskId'])
                    if current['status'] in {'completed', 'failed', 'cancelled'}:
                        self.assertEqual(current['status'], 'completed', current)
                        return service.result(task['taskId'])
                    time.sleep(.1)
                self.fail('operation timed out')

            try:
                task = manager.submit(simulation())
                original = finish(task, manager)
                parameter = manager.parameter_schemes.save({'name': '已保存参数', 'sourceTaskId': task['taskId']})
                self.assertEqual(len(manager.parameter_schemes.list()['schemes']), 1)
                body = {'parameterSchemeId': parameter['id'], 'context': simulation()['context']}
                modeled = finish(manager.operations.submit({**body, 'operation': 'continuousSimulation',
                    'period': {'startTime': '2017-06-19T11:00', 'endTime': '2017-06-20T11:00'}}), manager.operations)
                self.assertEqual(modeled['hydrograph']['forecast'], original['hydrograph']['forecast'])
                continued = finish(manager.operations.submit({'operation': 'continuation', 'context': body['context'],
                    'sourceTaskId': modeled['primaryTaskId'], 'period': {'endTime': '2017-06-20T13:00'}}), manager.operations)
                self.assertEqual(continued['parentStateVersionId'], modeled['endStateVersionId'])
                single = finish(manager.operations.submit({**body, 'operation': 'hindcast',
                    'issueTime': '2017-06-19T11:00', 'endTime': '2017-06-23T11:00'}), manager.operations)
                self.assertEqual(len(single['hydrograph']['forecast']), 96)
                rolling = finish(manager.operations.submit({**body, 'operation': 'rollingHindcast',
                    'issueTime': '2017-06-19T11:00', 'lastIssueTime': '2017-06-19T13:00', 'leadHours': 3}), manager.operations)
                self.assertEqual(len(rolling['issues']), 3)
                for row in rolling['issues']:
                    self.assertEqual(manager.get(row['taskId'])['stateRole'], 'forecastBranch')
                    self.assertNotEqual(row['issueStateVersionId'], row['endStateVersionId'])
                checkpoints = [read_json(manager.runtime_root / 'states' / (row['issueStateVersionId'] + '.json')) for row in rolling['issues']]
                self.assertEqual([row['time'] for row in checkpoints], [row['issueTime'] for row in rolling['issues']])
                from workspace_service import workspace_identity
                from workspace_backup import create_backup, verify_backup
                workspace_identity(manager.runtime_root)
                backup = create_backup(manager.runtime_root)
                verify_backup(Path(backup['path']))
                self.assertTrue((Path(backup['path']) / 'parameter-schemes' / (parameter['id'] + '.json')).exists())
            finally:
                manager.close()


if __name__ == '__main__':
    unittest.main()
