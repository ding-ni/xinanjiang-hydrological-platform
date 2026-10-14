"""Restored source plans keep their registered numerical input after checks."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from common import MODEL, read_json, write_json
from input_products import InputProducts, hourly_time
from model_schemes import input_definition
from original_calibration_plan import original_plan_payload, validate_original_request, SOURCE_QC
from hydrological_operations import HydrologicalOperations
from science_context import digest_file
from server import TaskManager
import test_20261012_candidate_import as fixtures


class OriginalOperationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CandidateImportTests()
        self.fixture.setUp()
        imported = fixtures.MODULE.import_candidates(self.fixture.source, self.fixture.runtime, self.fixture.hashes)
        self.parameter = imported['candidates'][0]['parameterVersionId']
        self.candidate = read_json(self.fixture.runtime / 'parameters' / (self.parameter + '.json'))
        self.plan = self.candidate['originalCalibrationPlan']
        events = self.fixture.root / 'registered-events.json'
        write_json(events, {'packages': [{'eventId': row['id'], 'start': row['start'], 'end': row['end'],
            'status': 'ready', 'observedInputs': {'rainCompleteness': 1, 'riverCompleteness': 1}}
            for row in self.plan['sourceEventWindows']]})
        self.bundle = {'bundleVersionId': 'INPUT-SOURCE', 'manifestSHA256': 'f' * 64,
            'forcingVersion': 'source-rain', 'observationVersion': 'source-flow', 'modelInputVersion': 'source-plan-input',
            'paths': {'events': events, 'quality': self.fixture.task / 'event_quality_config_snapshot.json',
                'topology': MODEL / 'data_interface/spatial/nan_an_topology_control_split_v2.json',
                'mapping': MODEL / 'data_interface/spatial/grid_to_unit_weight_control_split_v2.csv'},
            'manifest': {'sourceId': 'SRC-MDB-' + 'a' * 64, 'sourceQcPolicy': SOURCE_QC,
                'targetCoordinatePolicy': 'LEGACY_CLOSED_RING_VERTEX_MEAN', 'forcingValueDtype': 'float64',
                'eventQualityConfigurationSHA256': digest_file(self.fixture.task / 'event_quality_config_snapshot.json')}}
        self.products = Mock(spec=InputProducts)
        self.products.resolve.return_value = self.bundle
        self.products.list_versions.return_value = {'versions': [{'bundleVersionId': 'INPUT-SOURCE', 'sourceQcPolicy': SOURCE_QC}]}
        self.input_patch = patch('input_products.InputProducts', return_value=self.products)
        self.input_patch.start()
        self.executor_patch = patch('server.ThreadPoolExecutor')
        self.executor_patch.start()
        self.manager = TaskManager(self.fixture.runtime / 'tasks', products=self.products)
        self.request = original_plan_payload(self.parameter, self.fixture.runtime, self.products)['request']

    def tearDown(self):
        self.manager.close()
        self.executor_patch.stop()
        self.input_patch.stop()
        self.fixture.tearDown()

    def test_missing_derived_population_rebuilds_only_from_frozen_source(self):
        from original_calibration_plan import _build
        plan = copy.deepcopy(self.plan)
        expected = plan['calibration']['optimizer'].pop('effectiveInitialCandidates')
        request = _build(self.candidate, plan, self.bundle, 'search')
        self.assertEqual(request['calibration']['optimizer']['effectiveInitialCandidates'], expected)
        plan['calibration']['optimizer']['effectiveInitialCandidates'] = []
        with self.assertRaisesRegex(ValueError, '保存快照不一致'):
            _build(self.candidate, plan, self.bundle, 'search')

    def test_imported_candidate_state_job_freezes_fixed_replay_conditions(self):
        with patch('hydrological_operations.ThreadPoolExecutor'):
            operation = HydrologicalOperations(self.manager, None)
            try:
                submitted = operation.submit({'operation': 'stateReplay', 'sourceTaskId': self.candidate['taskId'], 'context': self.candidate['context']})
                request = submitted['runConfig']['frozenReplay']
                self.assertEqual(request['calibration']['optimizer']['algorithm'], 'FIXED_PARAMETERS')
                self.assertEqual(request['calibration']['initialParameters'], self.candidate['bestParameters'])
                self.assertEqual(request['routing']['versionId'], self.candidate['routingCandidate']['routingVersionId'])
                self.assertEqual(request['sourcePlan']['operation'], 'fixed-replay')
            finally:
                operation.close()

    def test_checked_preparation_cannot_replace_original_search_input_or_budget(self):
        scheme = self.manager.schemes.save({'name': '原来源率定', 'definition': input_definition(self.request),
                                           'calculationDraft': self.request})
        self.request['scheme'] = {'id': scheme['id'], 'revision': scheme['revision']}
        operation = object.__new__(HydrologicalOperations)
        operation.manager = self.manager
        operation.cancelled = set()
        prepared = copy.deepcopy(self.request)
        prepared['inputs'].update(bundleVersionId='INPUT-DERIVED', bundleManifestSHA256='d' * 64)
        prepared.setdefault('lineage', {})['preparationTaskId'] = 'DATA-check-only'
        operation.prepare = Mock(return_value=prepared)
        captures = []
        first = min(hourly_time(row['start']) for row in self.plan['sourceEvents'])
        last = max(hourly_time(row['end']) for row in self.plan['sourceEvents'])
        forecast = [{'time': (first + timedelta(hours=index)).isoformat(), 'value': 1.0}
                    for index in range(int((last - first).total_seconds() / 3600) + 1)]
        def child(identifier, request, stage, role='calculation'):
            captures.append(copy.deepcopy(request))
            task = self.manager.submit(request, operation_id=identifier, state_role=role)
            if request['runType'] == 'calibration':
                return {'taskId': task['taskId'], 'lineage': task['runConfig']['lineage'],
                    'bestParameters': self.candidate['bestParameters'], 'statePolicy': 'continuous-origin',
                    'continuousOrigin': self.plan['continuousOrigin'],
                    'hydrographs': {row['id']: {'forecast': [point for point in forecast if hourly_time(row['start']) <= hourly_time(point['time']) <= hourly_time(row['end'])]} for row in self.plan['sourceEvents']}}
            return {'taskId': task['taskId'], 'endStateVersionId': 'STATE-' + task['taskId'], 'hydrograph': {'forecast': forecast}}
        operation.child = child
        result = operation.calibration('OP-restored-source', {'operation': 'calibration', 'request': self.request,
                                                             'rainfallMethod': 'reuse'})
        self.assertEqual(captures[0]['sourcePlan'], self.request['sourcePlan'])
        self.assertEqual(captures[0]['calibration'], self.request['calibration'])
        self.assertEqual(captures[0]['inputs'], self.request['inputs'])
        self.assertEqual(captures[0]['calibration']['optimizer']['maxEvaluations'], 960)
        self.assertEqual(validate_original_request(captures[0], self.fixture.runtime)['sourcePlan'], self.request['sourcePlan'])
        frozen = self.manager.get(result['primaryTaskId'])['runConfig']
        self.assertEqual(frozen['inputs']['bundleVersionId'], 'INPUT-SOURCE')
        self.assertEqual(frozen['inputs']['bundleManifestSHA256'], 'f' * 64)
        self.assertEqual(frozen['science']['versionId'], self.request['science']['versionId'])
        self.assertEqual(frozen['routing']['versionId'], self.request['routing']['versionId'])
        self.assertEqual(captures[1]['inputs']['bundleVersionId'], 'INPUT-SOURCE')
        self.assertEqual(captures[1]['science']['versionId'], self.request['science']['versionId'])
        self.assertEqual(captures[1]['period']['continuousOrigin'], self.plan['continuousOrigin'])
        self.assertEqual(len(result['eventStates']), len(self.request['period']['events']))
        self.assertNotIn('sourceBundleVersionId', captures[0]['inputs'])

    def test_source_request_rejects_reprocessing_and_tampered_identity(self):
        operation = object.__new__(HydrologicalOperations)
        operation.manager = self.manager
        operation.prepare = Mock()
        operation.child = Mock()
        with self.assertRaisesRegex(ValueError, '已登记网格降雨'):
            operation.calibration('OP-no-interpolation', {'operation': 'calibration', 'request': self.request,
                                                          'rainfallMethod': 'stationIdw'})
        altered = copy.deepcopy(self.request)
        altered['inputs']['bundleVersionId'] = 'INPUT-DERIVED'
        with self.assertRaisesRegex(ValueError, '输入、汇流或科学版本被改变'):
            operation.calibration('OP-no-rebind', {'operation': 'calibration', 'request': altered})
        operation.prepare.assert_not_called()
        operation.child.assert_not_called()


@unittest.skipUnless(os.environ.get('XAJ_SOURCE_ACCEPTANCE_RUNTIME'), 'Opt-in registered source acceptance workspace')
class RegisteredOriginalOperationTests(unittest.TestCase):
    def test_original_grid_fixed_parameters_match_existing_numerical_kernel(self):
        import numpy as np
        import pandas as pd
        from worker import ModelJob, kernels
        runtime = Path(os.environ['XAJ_SOURCE_ACCEPTANCE_RUNTIME']).resolve()
        self.assertIn('acceptance', runtime.name)
        parameter = 'PARAM-JOB-import-20261012-3ec0d6785b8da64ea30fd172'
        candidate = read_json(runtime / 'parameters' / (parameter + '.json'))
        original = original_plan_payload(parameter, runtime, InputProducts(runtime_root=runtime))['request']
        context = original['context']
        request = {'runType': 'simulation', 'context': context, 'inputs': original['inputs'],
            'science': candidate['science'], 'routing': {'versionId': candidate['routingCandidate']['routingVersionId']},
            'parameters': candidate['bestParameters'], 'period': {'startTime': '2024-06-17T09:00:00',
                'endTime': '2024-06-17T11:00:00', 'warmupDays': 7, 'continuousOrigin': '2024-06-10T09:00:00'}}
        with tempfile.TemporaryDirectory() as temporary, patch('worker.RUNTIME', runtime), patch('routing_versions.RUNTIME', runtime), patch('worker.InputProducts', return_value=InputProducts(runtime_root=runtime)):
            folder = Path(temporary) / 'JOB-original-fixed-acceptance'
            write_json(folder / 'request.json', request)
            write_json(folder / 'status.json', {'taskId': folder.name})
            job = ModelJob(folder)
            job.checkpoint = lambda *args: 'STATE-JOB-numerical-comparison-only'
            job.progress = lambda *args: None
            first, last = pd.Timestamp(request['period']['startTime']), pd.Timestamp(request['period']['endTime'])
            event = job.load([{'event_id': 'ORIGINAL-FIXED', 'start': first, 'end': last, 'group': 'SIMULATION'}], 168)[0]
            event.pet_mm_h = job.pet_provider.values(event.times)
            expected, _, _ = kernels.route_event_fast(event, job.params, job.routing, job.router,
                                                     job.mapping, job.initial_state(job.params))
            with patch('worker.run_search', side_effect=AssertionError('Fixed source replay optimized parameters')):
                result = job.simulate()
            np.testing.assert_allclose([row['value'] for row in result['hydrograph']['forecast']],
                expected.loc[first:last].values, rtol=1e-12, atol=1e-12)
            self.assertEqual(job.bundle['bundleVersionId'], original['inputs']['bundleVersionId'])
            self.assertEqual(job.bundle['manifestSHA256'], original['inputs']['bundleManifestSHA256'])
            self.assertEqual(job.bundle['manifest']['forcingValueDtype'], 'float64')

    def test_original_grid_coverage_checks_reuse_exact_registered_product(self):
        from scheme_inputs import normalize_preparation, build_prepared
        runtime = Path(os.environ['XAJ_SOURCE_ACCEPTANCE_RUNTIME']).resolve()
        self.assertIn('acceptance', runtime.name)
        parameter = 'PARAM-JOB-import-20261012-3ec0d6785b8da64ea30fd172'
        products = InputProducts(runtime_root=runtime)
        original = original_plan_payload(parameter, runtime, products)['request']
        calibration = original['calibration']
        prepared = normalize_preparation({'operation': 'schemeInput', 'requestId': 'SOURCE-COVERAGE-CHECK',
            'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'parentBundleVersionId': original['inputs']['bundleVersionId'],
            'period': original['period'], 'rainfallMethod': 'reuse', 'operationPurpose': 'calibration',
            'science': original['science'], 'evaporation': original['inputs']['evaporation'],
            'statePolicy': calibration['statePolicy'], 'continuousOrigin': calibration['continuousOrigin']}, products)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'DATA-source-coverage-check'
            write_json(folder / 'request.json', prepared)
            checked = build_prepared(folder, products)
        self.assertEqual(checked['issues'], [])
        self.assertTrue(checked['reused'])
        self.assertEqual(checked['preparedBundleVersionId'], original['inputs']['bundleVersionId'])

    def test_real_registered_plan_freezes_small_explicit_budget_on_original_input(self):
        runtime = Path(os.environ['XAJ_SOURCE_ACCEPTANCE_RUNTIME']).resolve()
        self.assertIn('acceptance', runtime.name)
        parameter = 'PARAM-JOB-import-20261012-3ec0d6785b8da64ea30fd172'
        products = InputProducts(runtime_root=runtime)
        original = original_plan_payload(parameter, runtime, products)['request']
        original['calibration']['optimizer'].update(maxEvaluations=4, populationSize=4, generations=1)
        with patch('server.ThreadPoolExecutor'):
            manager = TaskManager(runtime / 'tasks', products=products)
            try:
                operation = object.__new__(HydrologicalOperations)
                operation.manager = manager
                operation.cancelled = set()
                prepared = copy.deepcopy(original)
                prepared['inputs'].update(bundleVersionId='INPUT-PREPARED-CHECK', bundleManifestSHA256='d' * 64)
                prepared.setdefault('lineage', {})['preparationTaskId'] = 'DATA-source-read-check'
                operation.prepare = Mock(return_value=prepared)
                captured = []
                candidate = read_json(runtime / 'parameters' / (parameter + '.json'))
                first = min(hourly_time(row['start']) for row in original['period']['events'])
                last = max(hourly_time(row['end']) for row in original['period']['events'])
                forecast = [{'time': (first + timedelta(hours=index)).isoformat(), 'value': 1.0}
                            for index in range(int((last - first).total_seconds() / 3600) + 1)]
                def child(identifier, request, stage, role='calculation'):
                    task = manager.submit(request, operation_id=identifier, state_role=role)
                    captured.append(task)
                    if request['runType'] == 'calibration':
                        return {'taskId': task['taskId'], 'lineage': task['runConfig']['lineage'],
                            'bestParameters': candidate['bestParameters'],
                            'bestRoutingParameters': candidate['bestRoutingParameters'],
                            'routingCandidate': candidate['routingCandidate'],
                            'statePolicy': 'continuous-origin', 'continuousOrigin': candidate['originalCalibrationPlan']['continuousOrigin'],
                            'hydrographs': {row['id']: {'forecast': [point for point in forecast if hourly_time(row['start']) <= hourly_time(point['time']) <= hourly_time(row['end'])]} for row in original['period']['events']}}
                    return {'taskId': task['taskId'], 'endStateVersionId': 'STATE-' + task['taskId'], 'hydrograph': {'forecast': forecast}}
                operation.child = child
                output = operation.calibration('OP-source-queue-check', {'operation': 'calibration', 'request': original})
                frozen = captured[0]['runConfig']
                self.assertEqual(frozen['inputs']['bundleVersionId'], original['inputs']['bundleVersionId'])
                self.assertEqual(frozen['inputs']['bundleManifestSHA256'], original['inputs']['bundleManifestSHA256'])
                self.assertEqual(frozen['sourcePlan'], original['sourcePlan'])
                self.assertEqual(frozen['calibration'], original['calibration'])
                self.assertEqual(frozen['calibration']['optimizer']['maxEvaluations'], 4)
                self.assertEqual(frozen['science']['versionId'], original['science']['versionId'])
                self.assertEqual(output['eventStates'][0]['taskId'], captured[1]['taskId'])
                self.assertEqual(captured[1]['runConfig']['inputs']['bundleVersionId'], original['inputs']['bundleVersionId'])
                self.assertEqual(captured[1]['runConfig']['routing']['versionId'], candidate['routingCandidate']['routingVersionId'])
            finally:
                manager.close()


if __name__ == '__main__':
    unittest.main()
