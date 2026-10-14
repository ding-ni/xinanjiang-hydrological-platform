"""Joint routing candidates, group trajectories and budgeted baseline trials."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration_groups import group_windows
from calibration_search import run_search
from common import MODEL, read_json, write_json
from contracts import normalize_request
from catalog import case_catalog
import joint_calibration as joint
from routing_versions import RoutingVersions
from test_continuous_calibration import continuous_request


def request_for_joint():
    request = continuous_request(False)
    request['calibration'].update(target='joint', jointProfile='reference-16',
        searchParameters={'SM': {'minimum': 8, 'maximum': 60}, 'KC': {'minimum': .4, 'maximum': 1.5}},
        routingSearchParameters={'RS_k_hours': {'minimum': .05, 'maximum': 3},
                                 'muskingum_k_scale': {'minimum': .5, 'maximum': 1.4},
                                 'muskingum_x': {'minimum': .05, 'maximum': .3}})
    return request


class JointContractTests(unittest.TestCase):
    def test_joint_and_fourteen_profile_and_fixed_values(self):
        request = request_for_joint()
        frozen = normalize_request(request, case_catalog())
        self.assertEqual(frozen, normalize_request(frozen, case_catalog()))
        self.assertEqual(len(joint.resolve_space({'target': 'joint'})), 16)
        self.assertEqual(len(joint.resolve_space({'target': 'joint', 'jointProfile': 'fixed-kc-c-14'})), 14)
        request['calibration']['jointProfile'] = 'fixed-kc-c-14'
        with self.assertRaisesRegex(ValueError, 'KC'):
            normalize_request(request, case_catalog())
        request['calibration']['searchParameters'].pop('KC')
        normalize_request(request, case_catalog())

    def test_channel_reference_scale_not_repeated_and_unit_k_preserved(self):
        sys.path[:0] = [str(MODEL / 'code'), str(MODEL / 'scripts')]
        from runoff_platform.config_loader import load_parameter_set
        initial = joint.initial_candidate(load_parameter_set(MODEL / 'config/parameter_set_land.json'),
                                          RoutingVersions().resolve()['parameters'])
        initial['routing']['unit_component_slope_k_hours'] = {'U_R0020_UP_NON_GLACIER': {'RS': 9.0}}
        snapshot = copy.deepcopy(initial)
        candidate = joint.candidate_payload(initial, {'runoff.KC': .7, 'routing.muskingum_k_scale': 1.2, 'routing.muskingum_x': .1})
        second = joint.candidate_payload(candidate, {'routing.muskingum_k_scale': 1.1})
        reach = next(iter(initial['routing']['reach_muskingum']))
        self.assertAlmostEqual(second['routing']['reach_muskingum'][reach]['k_hours'],
                               initial['routingReference']['reach_muskingum'][reach]['k_hours'] * 1.1)
        self.assertEqual(second['routing']['unit_component_slope_k_hours'], initial['routing']['unit_component_slope_k_hours'])
        self.assertEqual(initial['routing'], snapshot['routing'])
        self.assertEqual(second['runoff'].KC, .7)
        with self.assertRaisesRegex(ValueError, 'Muskingum'):
            joint.candidate_payload(initial, {'routing.muskingum_k_scale': 2, 'routing.muskingum_x': .45})

    def test_transitive_overlap_same_split_and_closed_hour(self):
        rows = [{'id': str(i), 'start': start, 'end': end, 'purpose': '率定'} for i, start, end in [
            (1, '2021-01-01T00:00', '2021-01-02T00:00'),
            (2, '2021-01-02T00:00', '2021-01-03T00:00'),
            (3, '2021-01-02T12:00', '2021-01-04T00:00')]]
        grouped = group_windows(rows)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]['uniqueHours'], 73)
        rows[2]['purpose'] = '独立检验'
        with self.assertRaisesRegex(ValueError, '重叠洪水'):
            group_windows(rows)

    def test_joint_baseline_counts_and_remains_best_when_trials_worse(self):
        sys.path.insert(0, str(MODEL / 'code'))
        from calibration_platform.algorithms import search
        for algorithm in ('RANDOM_SEARCH', 'GRID_SEARCH', 'DIFFERENTIAL_EVOLUTION'):
            calls = []
            def evaluate(values):
                calls.append(values.copy())
                return values['runoff.SM'] ** 2
            best, outcome = run_search([{'name': 'runoff.SM', 'minimum': 0, 'maximum': 2}],
                {'maxEvaluations': 8, 'populationSize': 4, 'algorithm': algorithm}, {'runoff.SM': 0},
                evaluate, lambda base, values: {**base, **values}, dict, search, lambda *args: None)
            self.assertEqual(best['runoff.SM'], 0)
            self.assertEqual(outcome['evaluations'], len(calls))
            self.assertLessEqual(len(calls), 8)
            self.assertEqual(outcome['history'][0]['kind'], 'initial')
            self.assertTrue(outcome['baselineReevaluated'])
            self.assertEqual(outcome['validEvaluations'] + outcome['invalidEvaluations'], len(calls))


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'real model acceptance')
class JointRealModelTests(unittest.TestCase):
    def test_continuous_origin_uses_data_coverage_and_explicit_origin_replays(self):
        import worker
        from worker import ModelJob
        from test_unified_backend import simulation
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            request = continuous_request(False)
            request['calibration'].update(evaluationMode='manual', statePolicy='continuous-origin')
            folder = Path(temp) / 'tasks/JOB-origin'
            write_json(folder / 'request.json', normalize_request(request, case_catalog()))
            write_json(folder / 'status.json', {'taskId': 'JOB-origin'})
            # Long trajectories are checked against actual driving data, never
            # rejected by an arbitrary calendar span in the worker constructor.
            implicit = ModelJob(folder, publish_parameters=False)
            self.assertLess(implicit.continuous_origin.year, 2017)
            request['calibration']['continuousOrigin'] = '2017-06-18T00:00:00'
            write_json(folder / 'request.json', normalize_request(request, case_catalog()))
            job = ModelJob(folder, publish_parameters=False)
            outcome = job.calibrate()
            self.assertEqual(outcome['continuousOrigin'], '2017-06-18T00:00:00')
            replay = simulation('2017-06-19T00:00', '2017-06-19T23:00')
            replay['period']['continuousOrigin'] = outcome['continuousOrigin']
            replay['parameters'] = outcome['bestParameters']
            replay['science'] = outcome['science']
            replay['inputs']['evaporation'] = {'mode': 'science-version'}
            replay['calibration'] = None
            replay_folder = Path(temp) / 'tasks/JOB-origin-replay'
            write_json(replay_folder / 'request.json', normalize_request(replay, case_catalog()))
            write_json(replay_folder / 'status.json', {'taskId': 'JOB-origin-replay'})
            compared = ModelJob(replay_folder).simulate()
            self.assertEqual(compared['hydrograph'], outcome['hydrographs']['CALIBRATION'])

    def test_joint_candidate_replays_same_science_and_holdout_does_not_select(self):
        import worker
        from worker import ModelJob
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)), patch('routing_versions.RUNTIME', Path(temp)):
            def make(request, identifier):
                folder = Path(temp) / 'tasks' / identifier
                write_json(folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(folder / 'status.json', {'taskId': identifier})
                return ModelJob(folder, publish_parameters=True)
            job = make(request_for_joint(), 'JOB-joint')
            job.run()
            result = read_json(job.folder / 'result.json')
            self.assertEqual(result['calibrationTarget'], 'joint')
            self.assertEqual(result['optimizer']['evaluations'], 4)
            self.assertEqual(result['evaluationPolicy'], 'EQUAL_PHYSICAL_GROUP_UNIQUE_HOURS')
            other = make(request_for_joint(), 'JOB-joint-holdout')
            other.publish_parameters = False
            other.observed.loc['2017-06-20':'2017-06-21'] *= 3
            changed = other.calibrate()
            self.assertEqual(changed['bestParameters'], result['bestParameters'])
            self.assertEqual(changed['bestRoutingParameters'], result['bestRoutingParameters'])
            self.assertEqual(changed['optimizer'], result['optimizer'])
            self.assertNotEqual(changed['validationMetrics']['nse'], result['validationMetrics']['nse'])
            replay = continuous_request(False)
            replay['calibration'].update(initialParameters=result['bestParameters'], evaluationMode='manual')
            replay['routing'] = {'versionId': result['routingCandidate']['routingVersionId']}
            replay['science'] = result['science']
            replay['inputs']['evaporation'] = {'mode': 'science-version'}
            reproduced = make(replay, 'JOB-joint-replay').calibrate()
            self.assertEqual(reproduced['hydrographs'], result['hydrographs'])
            self.assertEqual(reproduced['overlapGroupMetrics'], result['overlapGroupMetrics'])

    def test_overlap_uses_union_trajectory_and_cut_window_matches_exactly(self):
        import worker
        from worker import ModelJob
        import pandas as pd
        import numpy as np
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            request = continuous_request(False)
            request['calibration']['evaluationMode'] = 'manual'
            normalized = normalize_request(request, case_catalog())
            # Deliberately overlapping registered-length synthetic scoring rows
            # exercise the actual model, bypassing only the catalog name lookup.
            normalized['period']['mode'] = 'event'
            normalized['period']['events'] = [
                {'id': 'A', 'purpose': '率定', 'start': '2017-06-19T00:00', 'end': '2017-06-19T18:00'},
                {'id': 'B', 'purpose': '率定', 'start': '2017-06-19T12:00', 'end': '2017-06-19T23:00'},
                {'id': 'C', 'purpose': '验证', 'start': '2017-06-20T00:00', 'end': '2017-06-20T23:00'}]
            folder = Path(temp) / 'tasks/JOB-overlap-real'
            write_json(folder / 'request.json', normalized)
            write_json(folder / 'status.json', {'taskId': 'JOB-overlap-real'})
            job = ModelJob(folder, publish_parameters=False)
            # Existing physical event gates target historical long floods; this
            # fixture tests trajectory sharing, leaving gate tests independent.
            with patch('worker.evaluate_event_quality', return_value={'accepted': True, 'warning_reasons': [], 'exclusion_reasons': []}):
                result = job.calibrate()
            series = {}
            for key, hydrograph in result['hydrographs'].items():
                series[key] = pd.Series({pd.Timestamp(p['time']): p['value'] for p in hydrograph['forecast']})
            np.testing.assert_array_equal(series['A'].loc['2017-06-19T12:00':], series['B'].loc[:'2017-06-19T18:00'])
            self.assertEqual(len(result['overlapGroupMetrics']), 2)
            self.assertEqual(result['overlapGroupMetrics'][0]['uniqueHours'], 24)
            self.assertEqual(result['calibrationMetrics']['event_count'], 1)
            self.assertEqual(result['windowSummary']['CALIBRATION']['event_count'], 2)


if __name__ == '__main__':
    unittest.main()
