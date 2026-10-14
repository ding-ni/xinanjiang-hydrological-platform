"""Continuous windows, stage budgets, held-out data and real vendor equivalence."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from common import read_json, write_json
from contracts import normalize_request
from test_unified_backend import calibration


def continuous_request(staged=True):
    request = calibration()
    request['period'] = {'mode': 'continuous', 'warmupDays': 1,
                         'calibrationStart': '2017-06-19T00:00', 'calibrationEnd': '2017-06-19T23:00',
                         'validationStart': '2017-06-20T00:00', 'validationEnd': '2017-06-20T23:00',
                         'independentStart': '2017-06-21T00:00', 'independentEnd': '2017-06-21T23:00'}
    request['inputs']['evaporation'] = {'mode': 'monthly-climatology-assumption'}
    request['calibration']['optimizer'].update(strategy='staged' if staged else 'joint', maxEvaluations=10 if staged else 4, seed=17)
    return request


class ContinuousContractTests(unittest.TestCase):
    def test_freezes_three_groups_and_is_idempotent(self):
        request = continuous_request()
        request['period']['events'] = [{'id': 'stale-event'}]
        request['period']['eventSet'] = {'version': 'old'}
        request['lineage']['eventSetVersionId'] = 'old'
        normalized = normalize_request(request, case_catalog())
        self.assertEqual([p['id'] for p in normalized['period']['windows']], ['CALIBRATION', 'VALIDATION', 'INDEPENDENT'])
        self.assertNotIn('events', normalized['period'])
        self.assertNotIn('eventSet', normalized['period'])
        self.assertIsNone(normalized['lineage']['eventSetVersionId'])
        self.assertEqual(normalize_request(normalized, case_catalog()), normalized)
        self.assertIn('events', request['period'])

    def test_optional_independent_window(self):
        request = continuous_request()
        request['period'].update(independentStart=None, independentEnd='')
        self.assertEqual(len(normalize_request(request, case_catalog())['period']['windows']), 2)

    def test_rejects_overlap_inclusive_edges_missing_and_invalid_times(self):
        cases = [dict(validationStart='2017-06-19T23:00'), dict(independentEnd=None),
                 dict(calibrationStart='2017-06-19T00:30'), dict(validationStart='2017-06-20T00:00Z'),
                 dict(initialStateVersionId='STATE-JOB-existing'), dict(windows=[])]
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                request = continuous_request()
                request['period'].update(updates)
                normalize_request(request, case_catalog())

    def test_calendar_does_not_replace_actual_coverage_validation(self):
        request = continuous_request()
        request['period'].update(calibrationStart='2010-01-01T00:00', independentEnd='2026-01-01T00:00')
        self.assertEqual(normalize_request(request, case_catalog())['period']['windows'][-1]['end'], '2026-01-01T00:00:00')
        request['period'].update(calibrationStart='2018-01-01T00:00', calibrationEnd='2018-01-02T00:00')
        request['period']['independentEnd'] = '2017-06-21T23:00'
        with self.assertRaisesRegex(ValueError, '顺序'):
            normalize_request(request, case_catalog())

    def test_rejects_wrong_pet_and_search_config(self):
        for changes in ({'strategy': 'unknown'}, {'maxEvaluations': 9}, {'seed': True}, {'seed': 4294967296}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                request = continuous_request()
                request['calibration']['optimizer'].update(changes)
                normalize_request(request, case_catalog())
        request = continuous_request()
        request['inputs']['evaporation'] = {'mode': 'constant-zero', 'valueMmPerHour': 0}
        with self.assertRaises(ValueError):
            normalize_request(request, case_catalog())

    def test_task_type_clone_and_archive_preserve_windows(self):
        from server import TaskManager
        with tempfile.TemporaryDirectory() as temp, patch('server.ThreadPoolExecutor'):
            manager = TaskManager(Path(temp) / 'tasks')
            try:
                task = manager.submit(continuous_request())
                self.assertEqual(task['runType'], 'continuousCalibration')
                self.assertEqual(task['runTypeLabel'], '连续时段率定')
                manager.cancel(task['taskId'])
                cloned = manager.clone(task['taskId'])
                self.assertEqual(cloned['runConfig']['period'], task['runConfig']['period'])
                self.assertEqual(cloned['runConfig']['calibration']['optimizer'], task['runConfig']['calibration']['optimizer'])
            finally:
                manager.close()

    def test_continuous_diagnostic_uses_frozen_windows(self):
        from server import TaskManager
        from diagnostic_jobs import normalize_diagnostic
        with tempfile.TemporaryDirectory() as temp, patch('server.ThreadPoolExecutor'):
            manager = TaskManager(Path(temp) / 'tasks')
            try:
                frozen = normalize_diagnostic({'runType': 'diagnosticExperiment', 'baseRequest': continuous_request(),
                                               'mode': 'multiSeed', 'values': [1, 2, 3]}, manager)
                self.assertEqual(len(frozen['baseRequest']['period']['windows']), 3)
                request = continuous_request()
                request['period'].update(independentStart=None, independentEnd=None)
                with self.assertRaisesRegex(ValueError, '独立检验'):
                    normalize_diagnostic({'runType': 'diagnosticExperiment', 'baseRequest': request,
                                          'mode': 'multiSeed', 'values': [1, 2, 3]}, manager)
            finally:
                manager.close()


class SearchTests(unittest.TestCase):
    def test_stages_keep_incumbent_and_budget_including_failed_candidates(self):
        from calibration_search import run_search
        from common import MODEL
        sys.path.insert(0, str(MODEL / 'code'))
        from calibration_platform.algorithms import search
        specs = [{'name': 'runoff.WM', 'minimum': 1, 'maximum': 2}, {'name': 'runoff.SM', 'minimum': 1, 'maximum': 2}]
        initial = {'runoff.WM': 0, 'runoff.SM': 0}
        progress = []
        def candidate(base, updates):
            return {**base, **updates}
        best, outcome = run_search(specs, {'maxEvaluations': 10, 'populationSize': 4, 'algorithm': 'DIFFERENTIAL_EVOLUTION', 'strategy': 'staged'},
                                   initial, lambda params: sum(v * v for v in params.values()), candidate, dict, search,
                                   lambda value, label: progress.append(value))
        self.assertEqual(best, initial)
        self.assertEqual(outcome['evaluations'], 10)
        self.assertEqual([row['evaluation'] for row in outcome['history']], list(range(1, 11)))
        self.assertTrue(all(row['retainedInitial'] for row in outcome['stages']))
        self.assertEqual(outcome['stages'][1]['initialParameters'], outcome['stages'][0]['bestParameters'])
        self.assertEqual(progress[-1], 80)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(initial, {'runoff.WM': 0, 'runoff.SM': 0})


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Opt-in real model numerical regression')
class ContinuousNumericalTests(unittest.TestCase):
    def test_shared_trajectory_is_candidate_specific_and_held_out_is_not_optimized(self):
        import numpy as np
        import pandas as pd
        from worker import ModelJob, kernels
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            request = continuous_request(staged=False)
            request['period']['warmupDays'] = 30  # shared origin overrides the independent-period warmup
            request['calibration'].update(statePolicy='continuous-origin', continuousOrigin='2017-06-18T00:00')
            def make(name):
                folder = Path(temp) / 'tasks' / name
                write_json(folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(folder / 'status.json', {'taskId': name})
                return ModelJob(folder, publish_parameters=False)
            job = make('JOB-shared')
            original_load = job.load
            def exact_interval(rows, warmup):
                self.assertEqual(warmup, 24 if rows[0]['event_id'] == 'CONTINUOUS-TRAJECTORY' else 0)
                return original_load(rows, warmup)
            original = kernels.route_event_fast
            initial = []
            def tracked(event, params, routing, router, mapping, state):
                initial.append((state, {key: values.copy() for key, values in state.items()}))
                self.assertEqual(event.times[0], pd.Timestamp('2017-06-18T00:00'))
                self.assertEqual(event.times[-1], pd.Timestamp('2017-06-21T23:00'))
                return original(event, params, routing, router, mapping, state)
            with patch.object(kernels, 'route_event_fast', side_effect=tracked), patch.object(job, 'load', side_effect=exact_interval):
                result = job.calibrate()
            self.assertEqual(result['actualWarmupHours'], 24)
            self.assertEqual(len(initial), 5)  # four search candidates, one frozen best trajectory
            self.assertEqual(len({id(state) for state, _ in initial}), 5)
            for state, snapshot in initial:
                for key in state:
                    np.testing.assert_array_equal(state[key], snapshot[key])
            other = make('JOB-shared-held-out')
            other.observed.loc['2017-06-20':'2017-06-21'] *= 3
            changed = other.calibrate()
            self.assertEqual(changed['bestParameters'], result['bestParameters'])
            self.assertEqual(changed['optimizer'], result['optimizer'])
            self.assertNotEqual(changed['validationMetrics']['nse'], result['validationMetrics']['nse'])

    def test_real_stages_isolate_validation_and_match_vendor(self):
        import numpy as np
        import pandas as pd
        import worker
        from worker import ModelJob, kernels, PetProvider
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            def job_for(request, name):
                folder = Path(temp) / 'tasks' / name
                write_json(folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(folder / 'status.json', {'taskId': name})
                return ModelJob(folder)
            request = continuous_request()
            job = job_for(request, 'JOB-continuous')
            job.run()
            result = read_json(job.folder / 'result.json')
            self.assertEqual(result['optimizer']['evaluations'], 10)
            self.assertEqual(result['optimizationGroups'], ['CALIBRATION'])
            self.assertEqual(result['statePolicy'], 'independent-warmup')
            self.assertEqual(result['petMode'], 'MONTHLY_CLIMATOLOGY_ASSUMPTION')
            self.assertEqual(set(result['hydrographs']), {'CALIBRATION', 'VALIDATION', 'INDEPENDENT'})
            self.assertEqual(result['optimizer']['stages'][0]['bestParameters'], result['optimizer']['stages'][1]['initialParameters'])
            self.assertTrue((Path(temp) / 'parameters' / (result['parameterVersionId'] + '.json')).is_file())
            manifest = read_json(job.folder / 'input-manifest.json')
            self.assertIn('calibration_search.py', manifest['calibrationImplementation'])
            self.assertTrue(any('2017.nc' in row['path'] for row in manifest['files']))
            for period in result['periods']:
                row = {'event_id': period['id'], 'year': 2017, 'start': pd.Timestamp(period['start']), 'end': pd.Timestamp(period['end']), 'group': 'TEST'}
                event = job.load([row], 24)[0]
                event.pet_mm_h = PetProvider.from_file(worker.MODEL / 'config/pet_forcing_assumed_monthly.json').values(event.times)
                params = kernels.candidate_params(copy.deepcopy(job.params), {f'runoff.{k}': v for k, v in result['bestParameters'].items()})
                series = kernels.route_event_fast(event, params, job.routing, job.router, job.mapping, job.initial_state(params))[0].loc[event.start:event.end]
                process = result['hydrographs'][period['id']]
                np.testing.assert_allclose([p['value'] for p in process['forecast']], series.to_numpy(), rtol=1e-12, atol=1e-12)
                self.assertEqual(len(process['forecast']), 24)
                self.assertEqual(len(process['forcing']['precipitation']), 24)
            # Perturb only held-out observations. Neither chosen parameters nor search
            # history may change; validation scores must actually change.
            other = job_for(request, 'JOB-held-out')
            other.observed.loc['2017-06-20':'2017-06-21'] *= 3
            changed = other.calibrate()
            self.assertEqual(changed['bestParameters'], result['bestParameters'])
            self.assertEqual(changed['optimizer'], result['optimizer'])
            self.assertNotEqual(changed['validationMetrics']['nse'], result['validationMetrics']['nse'])
            # Actual observation gaps fail before any optimization.
            other.observed.loc['2017-06-20T03:00'] = np.nan
            with self.assertRaisesRegex(ValueError, '缺测'):
                other.calibrate()


if __name__ == '__main__':
    unittest.main(verbosity=2)
