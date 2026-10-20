"""Original task settings, objective consumption and guarded search references."""
import copy
import csv
import importlib.util
import json
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from calibration_objectives import objective_terms, platform_objectives
from calibration_search import run_search
from common import MODEL, read_json, write_json
from catalog import case_catalog
from contracts import normalize_request
import joint_calibration
from original_calibration_plan import freeze_source_plan, validate_plan, validate_original_request, source_input, SOURCE_QC, effective_initial_candidates
import test_20261012_candidate_import as fixtures
from test_continuous_calibration import continuous_request
from routing_versions import RoutingVersions
from input_products import InputProducts
from science_context import digest_file

DELIVERED_SOURCE_MODEL = Path(__file__).resolve().parents[2] / (
    '后端文件/南安溪产流汇流模型_20261012终结版/'
    '南安溪产流汇流模型_20261012终结版/空间可视化人工调参版/model'
)
DELIVERED_SOURCE_TASK = DELIVERED_SOURCE_MODEL / (
    'validation/platform_calibration_tasks/CAL-20261011-104708-95c5368f'
)


class OriginalPlanTests(unittest.TestCase):
    def test_source_files_and_search_settings_are_frozen_and_tampering_fails(self):
        fixture = fixtures.CandidateImportTests()
        fixture.setUp()
        try:
            result = fixtures.MODULE.import_candidates(fixture.source, fixture.runtime, fixture.hashes)
            row = read_json(fixture.runtime / 'parameters' / (result['candidates'][0]['parameterVersionId'] + '.json'))
            plan = row['originalCalibrationPlan']
            self.assertEqual(plan['calibration']['optimizer']['seed'], 20260820)
            self.assertEqual(plan['calibration']['optimizer']['maxEvaluations'], 960)
            self.assertEqual(plan['calibration']['optimizer']['initialCandidates'], [])
            self.assertEqual(plan['effectiveInitialCandidates'][0]['source'], 'CURRENT_ACTIVE_PARAMETERS')
            self.assertEqual(plan['effectiveInitialCandidates'], plan['calibration']['optimizer']['effectiveInitialCandidates'])
            self.assertEqual(plan['sourceSplitSeed'], 42)
            self.assertEqual(len(plan['sourceSearchSpace']), 16)
            self.assertEqual(len(plan['sourceConfiguration']), 10)
            self.assertEqual(plan['calibration']['objectives']['weights']['kge'], .2)
            self.assertNotEqual(plan['originalRoutingParameters'], row['bestRoutingParameters'])
            altered = copy.deepcopy(plan)
            altered['calibration']['optimizer']['seed'] += 1
            with self.assertRaisesRegex(ValueError, '摘要'):
                validate_plan(altered)
        finally:
            fixture.tearDown()

    def test_original_population_preserves_out_of_bounds_audit_without_simulating_it(self):
        sys.path.insert(0, str(MODEL / 'code'))
        from calibration_platform.algorithms import search
        calls, passed = [], []
        def optimizer(specs, objective, config):
            passed.append(config)
            return search(specs, objective, config)
        specs = [{'name': 'runoff.SM', 'minimum': 1, 'maximum': 10}]
        config = {'algorithm': 'DIFFERENTIAL_EVOLUTION', 'strategy': 'joint', 'maxEvaluations': 8,
                  'populationSize': 4, 'seed': 20260820, 'generations': 1,
                  'differentialWeight': .35, 'crossoverProbability': .15,
                  'initializationPolicy': 'SOURCE_INITIAL_CANDIDATES', 'initialCandidates': [],
                  'effectiveInitialCandidates': [{'source': 'CURRENT_ACTIVE_PARAMETERS', 'parameters': {'runoff.SM': -1}}]}
        def evaluate(values):
            calls.append(values.copy())
            if values['runoff.SM'] == -1:
                raise AssertionError('Unsafe baseline was simulated')
            return values['runoff.SM'] ** 2
        best, outcome = run_search(specs, config, {'runoff.SM': -1}, evaluate,
            lambda base, values: {**base, **values}, dict, optimizer, lambda *args: None)
        self.assertEqual(len(calls), 8)
        self.assertFalse(outcome['baselineReevaluated'])
        self.assertNotIn('initial_evaluation', passed[0])
        self.assertEqual(passed[0]['differential_weight'], .35)
        self.assertEqual(passed[0]['crossover_probability'], .15)
        self.assertEqual(passed[0]['generations'], 1)
        self.assertGreater(best['runoff.SM'], 0)
        self.assertFalse(outcome['initial_candidates'][0]['accepted'])

    @unittest.skipUnless(
        (DELIVERED_SOURCE_MODEL / 'code/calibration_platform/algorithms.py').is_file()
        and (DELIVERED_SOURCE_TASK / 'output/algorithm_history.csv').is_file(),
        'optional historical delivery fixtures are not part of the Git checkout',
    )
    def test_seeded_de_matches_delivered_algorithm_and_saved_first_eight_vectors(self):
        source_model = DELIVERED_SOURCE_MODEL
        source_task = DELIVERED_SOURCE_TASK
        package_name = '_delivered_seed_comparison'
        package = types.ModuleType(package_name)
        package.__path__ = [str(source_model / 'code/calibration_platform')]
        sys.modules[package_name] = package
        specification = importlib.util.spec_from_file_location(package_name + '.algorithms', source_model / 'code/calibration_platform/algorithms.py')
        source_algorithm = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(source_algorithm)
        sys.path[:0] = [str(MODEL / 'code'), str(MODEL / 'scripts')]
        from calibration_platform.algorithms import search
        from runoff_platform.config_loader import load_parameter_set
        specs = read_json(source_task / 'parameter_space_snapshot.json')['parameters']
        base_parameters = read_json(source_task / 'base_parameter_snapshot.json')['parameters']
        routing = read_json(source_task / 'routing_parameter_snapshot.json')
        initial = joint_calibration.initial_candidate(load_parameter_set(source_task / 'base_parameter_snapshot.json'), routing)
        seeds = effective_initial_candidates(base_parameters, routing, specs, read_json(source_task / 'initial_candidates.json'))
        guard_count, model_count = [], []
        def candidate(base, values):
            guard_count.append(copy.deepcopy(values))
            return joint_calibration.candidate_payload(base, values)
        def objective(payload):
            model_count.append(payload)
            return sum(value ** 2 for value in joint_calibration.resolved(payload).values())
        # Four and eight evaluations include the genuine capacity/routing guards
        # but no hourly simulations. Both algorithms consume exactly the same RNG.
        for budget in (4, 8):
            config = {'algorithm': 'DIFFERENTIAL_EVOLUTION', 'strategy': 'joint', 'maxEvaluations': budget,
                      'populationSize': 4, 'generations': 1, 'seed': 20260820,
                      'differentialWeight': .8, 'crossoverProbability': .7,
                      'initializationPolicy': 'SOURCE_INITIAL_CANDIDATES', 'initialCandidates': [],
                      'effectiveInitialCandidates': seeds}
            _best, actual = run_search(specs, config, initial, objective, candidate,
                                      joint_calibration.resolved, search, lambda *args: None)
            def direct(values):
                return {'loss': objective(joint_calibration.candidate_payload(copy.deepcopy(initial), values))}
            expected = source_algorithm.search(specs, direct, actual['consumedConfiguration'])
            self.assertEqual(actual['history'], expected['history'])
            self.assertEqual(actual['initial_candidates'], expected['initial_candidates'])
            self.assertEqual(actual['best_parameters'], expected['best_parameters'])
            self.assertIsNone(actual['history'][0]['loss'])
            self.assertIn('Muskingum', actual['history'][0]['error'])
            self.assertEqual(actual['history'][0]['parameters'], seeds[0]['parameters'])
            self.assertEqual(actual['evaluations'], budget)
        guard_count.clear()
        model_count.clear()
        config.update(maxEvaluations=8, populationSize=48, generations=19)
        _best, actual = run_search(specs, config, initial, objective, candidate,
                                  joint_calibration.resolved, search, lambda *args: None)
        with (source_task / 'output/algorithm_history.csv').open(encoding='utf-8-sig', newline='') as stream:
            saved = list(csv.DictReader(stream))[:8]
        self.assertEqual(len(actual['history']), len(saved))
        for observed, original in zip(actual['history'], saved):
            original_parameters = {row['name']: float(original[row['name']]) for row in specs}
            self.assertEqual(observed['parameters'], original_parameters)
            self.assertEqual(observed['loss'] is None, original['loss'] == '')
        self.assertEqual(len(guard_count), 9)  # Eight candidates plus final winner materialization.
        self.assertEqual(len(model_count), 3)

    def test_kge_pbias_and_legacy_volume_reach_real_objective(self):
        sys.path.insert(0, str(MODEL / 'scripts'))
        import run_formal_multievent_calibration as kernels
        import pandas as pd
        criteria = platform_objectives({'method': 'WEIGHTED_SUM', 'terms': [
            {'metric': 'NSE', 'weight': .3}, {'metric': 'KGE', 'weight': .2}, {'metric': 'PBIAS', 'weight': .2},
            {'metric': 'PEAK_ERROR', 'weight': .2}, {'metric': 'PEAK_TIME_ERROR', 'weight': .1}]})
        terms = objective_terms(criteria)
        frame = pd.DataFrame([{'NSE': .8, 'KGE': .6, 'volume_error_percent': 10,
                               'peak_error_percent': 20, 'peak_time_error_hours': 6}])
        self.assertAlmostEqual(kernels.objective_loss(frame, terms), -(.3*.8+.2*.6-.2*.1-.2*.2-.1*.25))
        changed = frame.copy()
        changed['KGE'] = .9
        self.assertAlmostEqual(kernels.objective_loss(changed, terms) - kernels.objective_loss(frame, terms), -.06)
        legacy = {'metrics': {'volume': True}, 'weights': {'volume': 1}}
        self.assertEqual(objective_terms(legacy)[0]['metric'], 'VOLUME_ERROR')

    def test_four_term_objective_penalizes_water_bias_without_cross_event_cancellation(self):
        sys.path.insert(0, str(MODEL / 'scripts'))
        import run_formal_multievent_calibration as kernels
        import pandas as pd
        criteria = {'metrics': {'nse': True, 'kge': False, 'peak': True, 'time': True, 'volume': True},
                    'weights': {'nse': .4, 'peak': .2, 'time': .1, 'volume': .3},
                    'waterBalanceMetric': 'VOLUME_ERROR'}
        terms = objective_terms(criteria)
        frame = pd.DataFrame([
            {'NSE': .8, 'volume_error_percent': bias, 'peak_error_percent': 20, 'peak_time_error_hours': 6}
            for bias in (-40, 40)])
        original = kernels.objective_loss(frame, terms)
        self.assertAlmostEqual(original, -(.4 * .8 - .3 * .4 - .2 * .2 - .1 * 6 / 24))
        improved = frame.copy()
        improved['volume_error_percent'] = [-10, 10]
        self.assertAlmostEqual(kernels.objective_loss(improved, terms) - original, -.09)
        improved['NSE'] = .7
        self.assertLess(kernels.objective_loss(improved, terms), original)

    def test_unregistered_initialization_flag_cannot_bypass_routing_guard(self):
        request = continuous_request(False)
        request['calibration']['optimizer'].update(initializationPolicy='SOURCE_INITIAL_CANDIDATES', initialCandidates=[])
        with self.assertRaisesRegex(ValueError, '核验'):
            normalize_request(request, case_catalog())

    def test_source_marker_checks_registered_plan_conditions_and_limits_small_budget(self):
        fixture = fixtures.CandidateImportTests()
        fixture.setUp()
        try:
            result = fixtures.MODULE.import_candidates(fixture.source, fixture.runtime, fixture.hashes)
            version = result['candidates'][0]['parameterVersionId']
            candidate = read_json(fixture.runtime / 'parameters' / (version + '.json'))
            plan = candidate['originalCalibrationPlan']
            request = {'runType': 'calibration', 'context': candidate['context'],
                       'inputs': {'bundleVersionId': 'INPUT-SOURCE', 'bundleManifestSHA256': 'f'*64,
                                  'evaporation': {'mode': 'science-version'}},
                       'routing': {'versionId': plan['originalRoutingVersionId']}, 'science': plan['science'],
                       'period': {'mode': 'event', 'warmupDays': 7, 'events': plan['sourceEvents'],
                                  'eventSet': {'selectedEventIds': plan['sourceAcceptedEventIds']}},
                       'calibration': copy.deepcopy(plan['calibration']),
                       'sourcePlan': {'parameterVersionId': version, 'planId': plan['planId'],
                                      'planSHA256': plan['planSHA256'], 'operation': 'search'}}
            with patch('original_calibration_plan.build_original_request', return_value=request):
                small = copy.deepcopy(request)
                small['calibration']['optimizer'].update(maxEvaluations=4, populationSize=4, generations=1)
                validated = validate_original_request(small, fixture.runtime)
                self.assertEqual(validated['lineage']['originalCalibrationPlanSHA256'], plan['planSHA256'])
                for mutator in (lambda row: row['period']['events'][0].update(purpose='独立检验'),
                                lambda row: row['calibration']['objectives']['weights'].update(kge=.3),
                                lambda row: row['calibration']['optimizer'].update(seed=1),
                                lambda row: row['calibration']['optimizer'].update(maxEvaluations=961),
                                lambda row: row['routing'].update(versionId=candidate['routingCandidate']['routingVersionId']),
                                lambda row: row['sourcePlan'].update(planSHA256='0'*64)):
                    changed = copy.deepcopy(small)
                    mutator(changed)
                    with self.subTest(mutator=mutator), self.assertRaises(ValueError):
                        validate_original_request(changed, fixture.runtime)
        finally:
            fixture.tearDown()

    def test_wide_profile_uses_delivered_caps_and_keeps_reference_defaults(self):
        wide = joint_calibration.resolve_space({'jointProfile': 'all-physical-16'})
        self.assertEqual(len(wide), 16)
        by_name = {row['name']: row for row in wide}
        self.assertEqual(by_name['runoff.KC']['maximum'], 3)
        self.assertEqual(by_name['runoff.WM']['maximum'], 1000)
        self.assertEqual(by_name['routing.RS_k_hours']['maximum'], 24)
        reference = {row['name']: row for row in joint_calibration.resolve_space({})}
        self.assertEqual(reference['runoff.KC']['maximum'], 1.5)
        self.assertEqual(reference['routing.RS_k_hours']['maximum'], 3)
        request = continuous_request(False)
        request['calibration']['initialParameters'].update(KC=2, WM=900, WUM=100, WLM=500, C=1, KI=0, KG=0)
        request['calibration']['evaluationMode'] = 'manual'
        normalize_request(request, case_catalog())
        request['calibration']['initialParameters']['WM'] = 600
        with self.assertRaisesRegex(ValueError, 'WM'):
            normalize_request(request, case_catalog())

    def test_source_input_requires_original_qc_and_original_target_together(self):
        fixture = fixtures.CandidateImportTests()
        fixture.setUp()
        try:
            result = fixtures.MODULE.import_candidates(fixture.source, fixture.runtime, fixture.hashes)
            candidate = read_json(fixture.runtime / 'parameters' / (result['candidates'][0]['parameterVersionId'] + '.json'))
            plan = candidate['originalCalibrationPlan']
            quality = fixture.task / 'event_quality_config_snapshot.json'
            manifest = {'sourceId': 'SRC-MDB-' + 'a'*64, 'sourceQcPolicy': SOURCE_QC,
                        'targetCoordinatePolicy': 'LEGACY_CLOSED_RING_VERTEX_MEAN', 'forcingValueDtype': 'float64',
                        'eventQualityConfigurationSHA256': digest_file(quality)}
            bundle = {'bundleVersionId': 'INPUT-SOURCE', 'manifest': manifest, 'paths': {'quality': quality}}
            products = Mock(spec=InputProducts)
            products.list_versions.return_value = {'versions': [{'bundleVersionId': 'INPUT-SOURCE', 'sourceQcPolicy': SOURCE_QC}]}
            products.resolve.return_value = bundle
            self.assertIs(source_input(plan, products), bundle)
            for key, value in (('targetCoordinatePolicy', 'NC_GRID_AXIS_CENTRE'),
                               ('forcingValueDtype', 'float32'), ('eventQualityConfigurationSHA256', '0'*64)):
                altered = copy.deepcopy(bundle)
                altered['manifest'][key] = value
                products.resolve.return_value = altered
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, '原 QC'):
                    source_input(plan, products)
        finally:
            fixture.tearDown()

    def test_original_plan_http_returns_settings_without_enqueuing_work(self):
        from server import TaskManager, ThreadingHTTPServer, handler
        fixture = fixtures.CandidateImportTests()
        fixture.setUp()
        manager = server = thread = None
        try:
            imported = fixtures.MODULE.import_candidates(fixture.source, fixture.runtime, fixture.hashes)
            parameter = imported['candidates'][0]['parameterVersionId']
            candidate = read_json(fixture.runtime / 'parameters' / (parameter + '.json'))
            plan = candidate['originalCalibrationPlan']
            event_file = fixture.root / 'events.json'
            write_json(event_file, {'packages': [{'eventId': row['id'], 'start': row['start'], 'end': row['end'],
                       'status': 'ready', 'observedInputs': {'observedPeakFlowM3s': 1, 'observedPeakTime': row['start'],
                                                            'rainCompleteness': 1, 'riverCompleteness': 1}}
                       for row in plan['sourceEventWindows']]})
            quality = fixture.task / 'event_quality_config_snapshot.json'
            bundle = {'bundleVersionId': 'INPUT-SOURCE', 'manifestSHA256': 'f'*64, 'forcingVersion': 'test-rain',
                      'observationVersion': 'test-flow', 'modelInputVersion': 'test-source',
                      'paths': {'events': event_file, 'quality': quality},
                      'manifest': {'sourceId': 'SRC-MDB-' + 'a'*64, 'sourceQcPolicy': SOURCE_QC,
                          'targetCoordinatePolicy': 'LEGACY_CLOSED_RING_VERTEX_MEAN', 'forcingValueDtype': 'float64',
                          'eventQualityConfigurationSHA256': digest_file(quality)}}
            products = Mock(spec=InputProducts)
            products.resolve.return_value = bundle
            products.list_versions.return_value = {'versions': [{'bundleVersionId': 'INPUT-SOURCE', 'sourceQcPolicy': SOURCE_QC}]}
            with patch('server.ThreadPoolExecutor') as executor, patch('server.InputService'), patch('input_products.InputProducts', return_value=products):
                manager = TaskManager(fixture.runtime / 'tasks', products=products)
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler(manager, fixture.root))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                url = f'http://127.0.0.1:{server.server_port}/api/model/parameter-versions/{parameter}/original-plan'
                try:
                    with urllib.request.urlopen(url, timeout=30) as response:
                        payload = json.load(response)
                except urllib.error.HTTPError as error:
                    self.fail(error.read().decode('utf-8'))
                self.assertEqual(payload['request']['calibration']['optimizer']['maxEvaluations'], 960)
                self.assertEqual(payload['request']['calibration']['optimizer']['seed'], 20260820)
                self.assertEqual(payload['request']['routing']['versionId'], plan['originalRoutingVersionId'])
                self.assertEqual(payload['request']['sourcePlan']['operation'], 'search')
                self.assertEqual(payload['plan']['requiredInputBundleVersionId'], 'INPUT-SOURCE')
                with urllib.request.urlopen(url + '?operation=fixed-replay', timeout=30) as response:
                    fixed = json.load(response)
                self.assertEqual(fixed['request']['calibration']['evaluationMode'], 'manual')
                self.assertEqual(fixed['request']['calibration']['initialParameters'], candidate['bestParameters'])
                self.assertEqual(fixed['request']['routing']['versionId'], candidate['routingCandidate']['routingVersionId'])
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url + '?operation=unknown', timeout=30)
                self.assertEqual(error.exception.code, 400)
                executor.return_value.submit.assert_not_called()
                self.assertEqual(len(list((fixture.runtime / 'tasks').glob('*/status.json'))), 2)
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=3)
            if manager:
                manager.close()
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
