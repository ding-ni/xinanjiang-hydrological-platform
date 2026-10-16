"""Staff workflows use real numeric tasks, independent of a connected page."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json, write_json
from server import TaskManager, InputTaskManager
from hydrological_operations import HydrologicalOperations, flood_catalog
from model_schemes import ModelSchemes, input_definition
from hydro_database import HydroDatabase
from test_unified_backend import simulation, calibration


class SchemeEditorTests(unittest.TestCase):
    def test_replayed_search_uses_optimal_routing_for_final_states(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary, patch('hydrological_operations.ThreadPoolExecutor'):
            root=Path(temporary)
            candidate={'routingVersionId':'ROUTING-optimum'}
            manager=SimpleNamespace(runtime_root=root,root=root/'tasks',
                routing_versions=SimpleNamespace(create=Mock(return_value=candidate)),
                science_versions=SimpleNamespace(resolve=Mock(return_value={'statePolicy':{'strategy':'independent-warmup'}})))
            operations=HydrologicalOperations(manager,None)
            operations.check_cancelled=Mock()
            graph={'forecast':[{'time':'2024-06-01T00:00:00','value':12}]}
            output={'taskId':'JOB-search','bestParameters':{'WM':150},'bestRoutingParameters':{'actual':'optimum'},'hydrographs':{'A':graph}}
            frozen={'inputs':{},'routing':{'versionId':'ROUTING-search-baseline'},'science':{'versionId':'SCIENCE-saved'},'period':{'events':[{'id':'A','start':'2024-06-01T00:00:00','end':'2024-06-01T00:00:00','purpose':'率定'}]}}
            operations.child=Mock(return_value={'taskId':'JOB-final','endStateVersionId':'STATE-final','hydrograph':graph})
            final=operations.final_states('OP-replay',output,frozen)
            self.assertEqual(operations.child.call_args.args[1]['routing'],{'versionId':'ROUTING-optimum'})
            self.assertEqual(final['routingCandidate'],candidate)
            self.assertEqual(frozen['routing']['versionId'],'ROUTING-search-baseline')

    def test_event_result_rerun_keeps_every_flood_instead_of_only_the_primary(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary, patch('hydrological_operations.ThreadPoolExecutor'):
            manager=SimpleNamespace(runtime_root=Path(temporary),get=Mock(return_value={'runConfig':{'period':{}}}))
            operations=HydrologicalOperations(manager,None)
            rows=[{'eventId':key,'taskId':'JOB-'+key,'start':'2020-01-01T00:00','end':'2020-01-01T01:00'} for key in ('A','B')]
            graphs={key:{'forecast':[{'time':'2020-01-01T00:00','value':value},{'time':'2020-01-01T01:00','value':value+1}]} for key,value in (('A',1),('B',10))}
            outputs=[{'taskId':'JOB-new-'+key,'endStateVersionId':'STATE-new-'+key,'hydrograph':graphs[key]} for key in ('A','B')]
            operations.child=Mock(side_effect=outputs)
            request={'sourceEventResult':{'primaryTaskId':'JOB-A','hydrographs':graphs,'eventStates':rows},'frozenEventRequests':[{'taskId':'JOB-'+key,'request':{}} for key in ('A','B')]}
            result=operations.rerun_event_result('OP-repeat',request)
            self.assertEqual(set(result['hydrographs']),{'A','B'})
            self.assertEqual([row['taskId'] for row in result['eventStates']],['JOB-new-A','JOB-new-B'])
            self.assertEqual(operations.child.call_count,2)

    def test_missing_grid_process_prepares_only_from_its_bound_station_source(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from hydrological_operations import PreparationIssues
        for source_id in ('SRC-bound',None):
            with self.subTest(source=source_id), tempfile.TemporaryDirectory() as temporary, patch('hydrological_operations.ThreadPoolExecutor'):
                manager=SimpleNamespace(runtime_root=Path(temporary),products=SimpleNamespace(resolve=Mock(return_value={'manifest':{'sourceId':source_id}})))
                inputs=SimpleNamespace(get=Mock(return_value={'registeredBundleVersionId':'INPUT-ready'}))
                operations=HydrologicalOperations(manager,inputs)
                operations.update=Mock()
                issues=[{'eventId':'A','missingIntervals':[{'start':'2020-01-01','end':'2020-01-02'}]}]
                operations.wait=Mock(side_effect=[{'issues':issues},{'issues':[]}])
                request=simulation();request['inputs']['bundleVersionId']='INPUT-original'
                with patch('scheme_inputs.reuse_preparation',return_value={'taskId':'DATA-check'}) as prepare:
                    if source_id:
                        output=operations.prepare('OP-check',request,'reuse')
                        self.assertEqual(prepare.call_count,2)
                        self.assertEqual(prepare.call_args.args[1]['stationSourceId'],source_id)
                        self.assertEqual(output['inputs']['stationSourceId'],source_id)
                        self.assertEqual(output['lineage']['automaticInputPreparation']['rainfallMethod'],'stationIdw')
                    else:
                        with self.assertRaises(PreparationIssues): operations.prepare('OP-check',request,'reuse')
                        self.assertEqual(prepare.call_count,1)

    def test_rerun_uses_frozen_request_without_current_defaults_or_draft(self):
        with tempfile.TemporaryDirectory() as temporary, patch('hydrological_operations.ThreadPoolExecutor'):
            manager = TaskManager(Path(temporary) / 'tasks')
            manager.operations = HydrologicalOperations(manager, None)
            try:
                request = simulation()
                request.update(inputs={'bundleVersionId':'frozen-A'}, routing={'versionId':'routing-A'}, science={'versionId':'science-A'})
                write_json(manager.root / 'JOB-saved' / 'status.json', {'taskId':'JOB-saved','status':'completed','runConfig':request})
                write_json(manager.root / 'JOB-saved' / 'result.json', {'taskId':'JOB-saved'})
                with patch.object(manager, 'available_artifact', return_value=True), patch.object(manager.schemes, 'defaults', side_effect=AssertionError('must not load defaults')):
                    row = manager.operations.submit({'operation':'rerun','sourceTaskId':'JOB-saved','context':request['context']})
                self.assertEqual(row['runConfig']['frozenRequest']['inputs']['bundleVersionId'], 'frozen-A')
                self.assertEqual(row['runConfig']['frozenRequest']['routing'], request['routing'])
                self.assertNotIn('preparationDefaults', row['runConfig'])
                self.assertNotIn('scheme', row['runConfig']['frozenRequest'])
            finally:
                manager.close()
    def test_state_replay_rejects_changed_flow_and_accepts_roundoff(self):
        original = {'forecast': [{'time': '2024-07-01T00:00', 'value': 10}]}
        HydrologicalOperations.assert_same_flow(original, {'forecast': [{'time': '2024-07-01T00:00:00', 'value': 10 + 1e-10}]})
        for changed in ({'forecast': []}, {'forecast': [{'time': '2024-07-01T01:00', 'value': 10}]}, {'forecast': [{'time': '2024-07-01T00:00', 'value': 11}]}):
            with self.assertRaisesRegex(ValueError, '原成果不一致'):
                HydrologicalOperations.assert_same_flow(original, changed)
    def test_scheme_summary_counts_selected_windows_and_latest_calculation(self):
        with tempfile.TemporaryDirectory() as temporary:
            schemes = ModelSchemes(HydroDatabase(temporary))
            row = schemes.create({'name': '洪水整定'})
            editor = {'events': [{'id': 'A', 'purpose': '率定'}, {'id': 'B', 'purpose': '验证'}, {'id': 'C', 'purpose': '未使用'}],
                      'schemeWindows': [{'id': 'D', 'purpose': '独立检验'}], 'originalCalibrationPlan': {'planSHA256': 'a' * 64}}
            schemes.save_editor(row['id'], {'editor': editor})
            write_json(Path(temporary) / 'operations/OP-test/status.json', {'taskId': 'OP-test', 'status': 'completed', 'createdAt': '2026-10-15T00:00', 'runConfig': {'request': {'scheme': {'id': row['id']}}}})
            listed = schemes.list()['schemes'][0]
            self.assertEqual(listed['summary'], {'mode': 'event', 'selectedCount': 3, 'calibration': 1, 'validation': 1, 'independent': 1})
            self.assertEqual(listed['latestCalculation']['status'], 'completed')
            self.assertEqual(listed['originalPlanSHA256'], 'a' * 64)

    def test_manual_trial_drops_original_search_lock_and_retains_routing(self):
        with tempfile.TemporaryDirectory() as temporary, patch('hydrological_operations.ThreadPoolExecutor'):
            manager = TaskManager(Path(temporary) / 'tasks')
            manager.operations = HydrologicalOperations(manager, None)
            try:
                model = calibration()
                model['sourcePlan'] = {'operation': 'search'}
                model['calibration'].update(target='joint', jointProfile='reference-16', searchParameters={'SM': {}}, routingSearchParameters={'RS_k_hours': {}})
                model['calibration']['optimizer'].update(initializationPolicy='SOURCE_INITIAL_CANDIDATES')
                routing = model.get('routing')
                submitted = manager.operations.submit({'operation': 'manualTrial', 'request': model})['runConfig']['request']
                self.assertNotIn('sourcePlan', submitted)
                for key in ('target', 'jointProfile', 'searchParameters', 'routingSearchParameters'):
                    self.assertNotIn(key, submitted['calibration'])
                self.assertEqual(submitted['calibration']['optimizer'], {'algorithm': 'FIXED_PARAMETERS', 'maxEvaluations': 1})
                self.assertEqual(submitted.get('routing'), routing)
            finally:
                manager.close()
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
                    with patch('station_network.local_station_network', return_value={'stations': [{'id': 'test', 'lon': 119, 'lat': 30}], 'flowStation': '70115800'}):
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
    def test_local_git_kernel_snapshot_replays_with_its_original_identity(self):
        from implementation_versions import register_git_implementation
        with tempfile.TemporaryDirectory() as temporary:
            manager=TaskManager(Path(temporary)/'tasks')
            manager.operations=HydrologicalOperations(manager,None)
            def finish(task):
                deadline=time.monotonic()+240
                while time.monotonic()<deadline:
                    row=manager.get(task['taskId'])
                    if row['status'] not in {'queued','running'}:
                        self.assertEqual(row['status'],'completed',row.get('errorMessage'))
                        return manager.result(task['taskId'])
                    time.sleep(.25)
                self.fail('frozen executable timed out')
            try:
                snapshot=register_git_implementation(manager.runtime_root,'c570027')
                current=finish(manager.submit(simulation()))
                request=simulation();request['requestId']='OLD-KERNEL'
                request['lineage']['modelImplementationSHA256']=snapshot['modelImplementationSHA256']
                legacy=finish(manager.submit(request))
                self.assertEqual(legacy['lineage']['modelImplementationSHA256'],snapshot['modelImplementationSHA256'])
                self.assertEqual(current['hydrograph'],legacy['hydrograph'])
                state=manager.states.checkpoint(legacy['endStateVersionId'])
                continuation=simulation('2017-06-20T12:00','2017-06-20T13:00')
                continuation['requestId']='OLD-KERNEL-CONTINUE'
                continuation['period'].update(initialStateVersionId=state['stateVersionId'],warmupDays=0)
                continued=finish(manager.submit(continuation))
                self.assertEqual(continued['lineage']['modelImplementationSHA256'],snapshot['modelImplementationSHA256'])
            finally: manager.close()

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
                # The original numeric calibration record has no stored state
                # arrays. Supplement it through the durable fixed-parameter job.
                source_id = result['primaryTaskId']
                original = manager.result(source_id)
                for key in ('eventStates', 'stateCalculationTaskId', 'endStateVersionId'):
                    original.pop(key, None)
                write_json(manager.root / source_id / 'result.json', original)
                replay_job = manager.operations.submit({'operation': 'stateReplay', 'sourceTaskId': source_id, 'context': request['context']})
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    status = manager.operations.get(replay_job['taskId'])
                    if status['status'] not in {'queued', 'running'}:
                        break
                    time.sleep(.15)
                self.assertEqual(status['status'], 'completed', status)
                supplemented = manager.result(source_id)
                self.assertEqual(supplemented['hydrographs'], result['hydrographs'])
                self.assertEqual(len(supplemented['eventStates']), len(result['eventStates']))
                self.assertEqual(manager.operations.submit({'operation': 'stateReplay', 'sourceTaskId': source_id, 'context': request['context']})['taskId'], replay_job['taskId'])
                self.assertEqual(manager.parameter_schemes.list()['schemes'], [])
                parameter = manager.parameter_schemes.save({'name': '验收参数', 'sourceTaskId': result['primaryTaskId']})
                self.assertEqual(parameter['parameters'], result['bestParameters'])
            finally:
                manager.close()

    def test_saved_parameter_fixed_simulation_hindcast_rolling_and_continuation(self):
        from operation_fixtures import seed_daily_observations
        import copy
        with tempfile.TemporaryDirectory() as temporary:
            manager = TaskManager(Path(temporary) / 'tasks')
            seed_daily_observations(manager)
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
                replay_status = manager.replayability(task['taskId'])
                self.assertTrue(replay_status['available'], replay_status)
                replay = finish(manager.operations.submit({'operation':'rerun', 'sourceTaskId':task['taskId'], 'context':simulation()['context']}), manager.operations)
                self.assertEqual(replay['hydrograph'], original['hydrograph'])
                first_state = manager.states.checkpoint(original['endStateVersionId'])
                replay_state = manager.states.checkpoint(replay['endStateVersionId'])
                for field in ('time','land','routing','parameters','routingTopology'):
                    self.assertEqual(first_state[field], replay_state[field], field)
                parameter = manager.parameter_schemes.save({'name': '已保存参数', 'sourceTaskId': task['taskId']})
                self.assertEqual(len(manager.parameter_schemes.list()['schemes']), 1)
                before_name = dict(parameter)
                renamed = manager.parameter_schemes.rename(parameter['id'], {'name':'改名后的参数'})
                self.assertEqual({k:v for k,v in renamed.items() if k not in {'name','updatedAt'}}, {k:v for k,v in before_name.items() if k not in {'name','updatedAt'}})
                body = {'parameterSchemeId': parameter['id'], 'context': simulation()['context']}
                modeled = finish(manager.operations.submit({**body, 'operation': 'continuousSimulation',
                    'period': {'startTime': '2017-06-19T11:00', 'endTime': '2017-06-20T11:00'}}), manager.operations)
                direct_request = copy.deepcopy(manager.get(modeled['primaryTaskId'])['runConfig'])
                direct_request['requestId'] = 'DIRECT-SAVED-PARAMETER-CHECK'
                direct = finish(manager.submit(direct_request), manager)
                self.assertEqual(modeled['hydrograph']['forecast'], direct['hydrograph']['forecast'])
                self.assertEqual(direct_request['parameters'], parameter['parameters'])
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
