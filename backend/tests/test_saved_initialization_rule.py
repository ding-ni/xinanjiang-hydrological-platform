"""Saved initialization rules execute at the new simulation or issue hour."""
import copy
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json
from hydrological_operations import HydrologicalOperations
from parameter_schemes import ParameterSchemes
from state_initialization_adapter import freeze_initialization


class SavedInitializationRules(unittest.TestCase):
    def test_named_parameter_save_waits_before_preparing_related_records(self):
        repository = ParameterSchemes(SimpleNamespace(runtime_root=Path('unused')))
        entered, finished = threading.Event(), threading.Event()
        def build(body):
            entered.set()
            return {'name': body['name']}
        repository._save = build
        answer = []
        def save():
            answer.append(repository.save({'name': '备份后保存'}))
            finished.set()
        with repository.lock:
            peer = threading.Thread(target=save)
            peer.start()
            self.assertFalse(entered.wait(.1))
            self.assertFalse(finished.is_set())
        peer.join(timeout=2)
        self.assertFalse(peer.is_alive())
        self.assertTrue(entered.is_set())
        self.assertEqual(answer, [{'name': '备份后保存'}])

    def test_saved_rule_uses_current_anchor_and_missing_anchor_stops_before_simulation(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'flow.csv'
            operation = object.__new__(HydrologicalOperations)
            captured = []
            def child(identifier, request, stage, role):
                captured.append(copy.deepcopy(request))
                freeze_initialization(request, {'paths': {'flow': path}, 'observationVersion': 'current-flow'})
                return {'endStateVersionId': 'STATE-JOB-anchored', 'stateTime': request['period']['endTime']}
            operation.child = child
            model = {'runType': 'simulation', 'period': {'startTime': '2024-07-01T01:00:00',
                      'endTime': '2024-07-01T12:00:00'}, 'parameters': {'SM': 20}}
            parameter = {'warmupDays': 7, 'initialization': {'strategy': 'OBSERVED_FLOW_ANCHORED',
                          'anchor': {'time': '2000-01-01T00:00', 'flowCms': 9999}}}
            instant = datetime.fromisoformat('2024-07-01T00:00:00')
            pd.DataFrame({'time': [instant.isoformat()], 'flow_m3_s': [12.5]}).to_csv(path, index=False)
            state = operation.initialize_saved_rule('OP-test', model, parameter, instant, 'continuousInitial')
            self.assertEqual(state['stateTime'], instant.isoformat())
            self.assertEqual(captured[0]['initialization'], {'strategy': 'OBSERVED_FLOW_ANCHORED'})
            self.assertEqual(captured[0]['period']['warmupDays'], 7)
            self.assertEqual(model['runType'], 'simulation')
            for value in (None, -1):
                pd.DataFrame({'time': [instant.isoformat()], 'flow_m3_s': [value]}).to_csv(path, index=False)
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, '缺测|小于零'):
                    operation.initialize_saved_rule('OP-test', model, parameter, instant, 'continuousInitial')

    def test_rule_inherits_checkpoint_trajectory_without_stale_anchor_values(self):
        manager = SimpleNamespace(runtime_root=Path('unused'),
            states=SimpleNamespace(checkpoint=lambda state: {'drivingIdentity': {'observedFlowAnchor': {
                'time': '2020-01-01T00:00', 'flowCms': 10}}}))
        repository = ParameterSchemes(manager)
        rule = repository.initialization_rule({'period': {}}, {'endStateVersionId': 'STATE-JOB-test'},
            {'statePolicy': {'strategy': 'independent-warmup'}})
        self.assertEqual(rule, {'strategy': 'OBSERVED_FLOW_ANCHORED'})


@unittest.skipUnless(os.environ.get('XAJ_OPERATION_NUMERIC_TEST') == '1', 'Isolated staff operation numerics')
class SavedInitializationNumerics(unittest.TestCase):
    def test_continuous_and_hindcast_apply_saved_anchor_and_resaved_rule_survives(self):
        from server import TaskManager, InputTaskManager
        from test_unified_backend import simulation
        from operation_fixtures import seed_daily_observations
        with tempfile.TemporaryDirectory() as temp:
            manager = TaskManager(Path(temp) / 'tasks')
            seed_daily_observations(manager)
            manager.input_tasks = InputTaskManager(Path(temp) / 'input-tasks', manager.products)
            manager.operations = HydrologicalOperations(manager, manager.input_tasks)
            manager.schemes.defaults({'rainfallMethod': 'reuse', 'stationSourceId': None})
            def finish(task, service):
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    status = service.get(task['taskId'])
                    if status['status'] not in {'queued', 'running'}:
                        self.assertEqual(status['status'], 'completed', status)
                        return service.result(task['taskId'])
                    time.sleep(.1)
                self.fail('saved initialization job did not finish')
            try:
                initial = simulation('2017-06-19T10:00', '2017-06-19T10:00')
                initial.update(runType='stateInitialization', initialization={'strategy': 'OBSERVED_FLOW_ANCHORED'})
                source = manager.submit(initial)
                finish(source, manager)
                parameter = manager.parameter_schemes.save({'name': '流量起算参数', 'sourceTaskId': source['taskId']})
                self.assertEqual(parameter['initialization'], {'strategy': 'OBSERVED_FLOW_ANCHORED'})
                body = {'context': initial['context'], 'parameterSchemeId': parameter['id']}
                submitted = manager.operations.submit({**body, 'operation': 'continuousSimulation',
                    'period': {'startTime': '2017-06-19T11:00', 'endTime': '2017-06-19T13:00'}})
                result = finish(submitted, manager.operations)
                request = manager.get(result['primaryTaskId'])['runConfig']
                self.assertEqual(request['period']['warmupDays'], 0)
                parent = manager.states.checkpoint(request['period']['initialStateVersionId'])
                self.assertEqual(parent['initialization']['strategy'], 'OBSERVED_FLOW_ANCHORED')
                self.assertEqual(parent['initialization']['anchor']['time'], '2017-06-19T10:00:00')
                repeated = manager.parameter_schemes.save({'name': '续用流量起算参数', 'sourceTaskId': result['primaryTaskId']})
                self.assertEqual(repeated['warmupDays'], 1)
                self.assertEqual(repeated['initialization'], {'strategy': 'OBSERVED_FLOW_ANCHORED'})
                hindcast = finish(manager.operations.submit({**body, 'operation': 'hindcast',
                    'issueTime': '2017-06-19T11:00', 'endTime': '2017-06-19T14:00'}), manager.operations)
                issue_state = manager.states.checkpoint(hindcast['issues'][0]['issueStateVersionId'])
                self.assertEqual(issue_state['time'], '2017-06-19T11:00:00')
                self.assertEqual(issue_state['initialization']['anchor']['time'], issue_state['time'])
                self.assertEqual(issue_state['parameters'], parent['parameters'])
                import json
                event = {'eventId': 'F-ANCHOR-TEST', 'start': '2017-06-19T11:00',
                         'end': '2017-06-19T13:00', 'floodCode': 'anchor'}
                with manager.database.connect() as database:
                    database.execute('INSERT OR IGNORE INTO projects VALUES (?,?)', ('nananxi-xufan', '南安溪'))
                    database.execute('INSERT INTO flood_events VALUES (?,?,?,?,?,?)',
                        ('nananxi-xufan', event['eventId'], event['start'], event['end'], 'anchor', json.dumps(event)))
                event_result = finish(manager.operations.submit({**body, 'operation': 'eventSimulation',
                    'events': [event['eventId']]}), manager.operations)
                event_request = manager.get(event_result['primaryTaskId'])['runConfig']
                event_parent = manager.states.checkpoint(event_request['period']['initialStateVersionId'])
                self.assertEqual(event_parent['initialization']['anchor']['time'], '2017-06-19T10:00:00')
                self.assertEqual(len(event_result['hydrograph']['forecast']), 3)
            finally:
                manager.close()


if __name__ == '__main__':
    unittest.main()
