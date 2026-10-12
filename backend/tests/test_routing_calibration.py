"""Slope-only search, immutable routing candidates and exact replay."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import routing_calibration as scope
from catalog import case_catalog
from common import MODEL, read_json, write_json
from contracts import normalize_request
from routing_versions import RoutingVersions
from test_continuous_calibration import continuous_request


def routing_request():
    request = continuous_request(False)
    request['calibration'].update(target='routing', routingSearchParameters={'RS_k_hours': {'minimum': .5, 'maximum': 4}})
    return request


class RoutingSearchContractTests(unittest.TestCase):
    def test_default_and_single_dimension_are_distinct_from_runoff(self):
        request = routing_request()
        normalized = normalize_request(request, case_catalog())
        self.assertEqual(normalize_request(normalized, case_catalog()), normalized)
        self.assertEqual([row['name'] for row in scope.resolve_space(normalized['calibration'])], ['routing.RS_k_hours'])
        request['calibration'].pop('routingSearchParameters')
        self.assertEqual(len(scope.resolve_space(request['calibration'])), 3)
        self.assertEqual(len(case_catalog()['cases']['nananxi-xufan:1km']['routingSearchParameterDefinitions']), 3)

    def test_rejects_invalid_ranges_and_mixed_modes(self):
        invalid = [{}, [], True, {'KI': {}}, {'RS_k_hours': None}, {'RS_k_hours': {'minimum': 1}},
                   {'RS_k_hours': {'minimum': False, 'maximum': 4}}, {'RS_k_hours': {'minimum': float('nan'), 'maximum': 4}},
                   {'RS_k_hours': {'minimum': .01, 'maximum': 4}}, {'RS_k_hours': {'minimum': 4, 'maximum': 1}},
                   {'RS_k_hours': {'minimum': 1, 'maximum': 721}}]
        for selected in invalid:
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                request = routing_request()
                request['calibration']['routingSearchParameters'] = selected
                normalize_request(request, case_catalog())
        for changes in [{'target': 'wrong'}, {'target': []}, {'target': None}, {'target': 'runoff'},
                        {'searchParameters': {'SM': {'minimum': 8, 'maximum': 60}}},
                        {'evaluationMode': 'manual'}, {'wlmUpperBound': 120}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                request = routing_request()
                request['calibration'].update(changes)
                normalize_request(request, case_catalog())
        request = routing_request()
        request['calibration']['optimizer'].update(strategy='staged', maxEvaluations=10)
        with self.assertRaisesRegex(ValueError, '联合'):
            normalize_request(request, case_catalog())

    def test_only_selected_global_k_changes_and_unit_overrides_survive(self):
        base = RoutingVersions().resolve()['parameters']
        base['unit_component_slope_k_hours'] = {'U0001': {'RS': 3.2}}
        frozen = copy.deepcopy(base)
        updated = scope.candidate_payload(base, {'routing.RS_k_hours': 3.5})
        self.assertEqual(base, frozen)
        self.assertEqual(updated['unit_component_slope_k_hours'], base['unit_component_slope_k_hours'])
        self.assertEqual(updated['reach_muskingum'], base['reach_muskingum'])
        self.assertEqual(updated['default_slope_k_hours']['RI'], base['default_slope_k_hours']['RI'])
        self.assertEqual(updated['default_slope_k_hours']['RS'], 3.5)

    def test_queue_checks_current_bounds_and_clone_keeps_routing(self):
        from server import TaskManager
        from diagnostic_jobs import normalize_diagnostic
        with tempfile.TemporaryDirectory() as temp, patch('server.ThreadPoolExecutor'):
            manager = TaskManager(Path(temp) / 'tasks')
            try:
                bad = routing_request()
                bad['calibration']['routingSearchParameters']['RS_k_hours'] = {'minimum': 3, 'maximum': 4}
                with self.assertRaisesRegex(ValueError, '当前参数'):
                    manager.submit(bad)
                task = manager.submit(routing_request())
                self.assertEqual(task['runTypeLabel'], '坡面汇流率定')
                manager.cancel(task['taskId'])
                clone = manager.clone(task['taskId'])
                self.assertEqual(clone['runConfig']['routing'], task['runConfig']['routing'])
                self.assertEqual(clone['runConfig']['calibration'], task['runConfig']['calibration'])
                with self.assertRaisesRegex(ValueError, '产流率定'):
                    normalize_diagnostic({'runType': 'diagnosticExperiment', 'baseRequest': routing_request(),
                                          'mode': 'multiSeed', 'values': [1, 2, 3]}, manager)
            finally:
                manager.close()

    def test_fully_overridden_parameter_cannot_be_searched_as_global(self):
        payload = RoutingVersions().resolve()['parameters']
        units = read_json(MODEL / 'data_interface/spatial/nan_an_topology_control_split_v2.json')['units']
        payload['unit_component_slope_k_hours'] = {row['unit_id']: {'RS': 3} for row in units}
        with self.assertRaisesRegex(ValueError, '覆盖'):
            scope.validate(routing_request()['calibration'], payload)


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real model regression')
class RoutingNumericalTests(unittest.TestCase):
    def test_exact_manual_replay_and_held_out_isolation(self):
        import numpy as np
        from worker import ModelJob
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)), patch('routing_versions.RUNTIME', Path(temp)):
            def make(request, name):
                folder = Path(temp) / 'tasks' / name
                write_json(folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(folder / 'status.json', {'taskId': name})
                return ModelJob(folder)
            request = routing_request()
            job = make(request, 'JOB-routing-search')
            base = copy.deepcopy(job.routing_payload)
            job.run()
            result = read_json(job.folder / 'result.json')
            self.assertEqual(result['bestParameters'], request['calibration']['initialParameters'])
            self.assertEqual(result['bestRoutingParameters']['reach_muskingum'], base['reach_muskingum'])
            self.assertEqual(result['bestRoutingParameters']['unit_component_slope_k_hours'], base['unit_component_slope_k_hours'])
            k = result['bestRoutingParameters']['default_slope_k_hours']['RS']
            self.assertNotEqual(k, base['default_slope_k_hours']['RS'])
            self.assertTrue(.5 <= k <= 4)
            self.assertEqual(result['optimizer']['evaluations'], 4)
            self.assertEqual([row['key'] for row in result['searchPlan']['rows'] if row['active']], ['RS_k_hours'])
            candidate = result['routingCandidate']
            self.assertEqual(RoutingVersions(temp).resolve(candidate['routingVersionId']), candidate)
            self.assertEqual(RoutingVersions(temp).resolve()['parameters'], base)
            for row in result['optimizer']['history']:
                self.assertEqual(set(row['parameters']), {'routing.RS_k_hours'})
            other = make(request, 'JOB-routing-heldout')
            other.publish_parameters = False
            other.observed.loc['2017-06-20':'2017-06-21'] *= 3
            altered = other.calibrate()
            self.assertEqual(altered['bestRoutingParameters'], result['bestRoutingParameters'])
            self.assertEqual(altered['optimizer'], result['optimizer'])
            self.assertNotEqual(altered['validationMetrics']['nse'], result['validationMetrics']['nse'])
            self.assertNotIn('routingCandidate', altered)
            replay = continuous_request(False)
            replay['calibration'].update(initialParameters=result['bestParameters'], evaluationMode='manual')
            replay['routing'] = {'versionId': candidate['routingVersionId']}
            replay_job = make(replay, 'JOB-routing-replay')
            replay_job.run()
            reproduced = read_json(replay_job.folder / 'result.json')
            for key in result['hydrographs']:
                self.assertEqual(reproduced['hydrographs'][key], result['hydrographs'][key])
            self.assertEqual(reproduced['lineage']['routingVersionId'], candidate['routingVersionId'])
            self.assertEqual(result['lineage']['routingVersionId'], 'control-split-v2')
            self.assertIn('routing_calibration.py', read_json(job.folder / 'input-manifest.json')['calibrationImplementation'])
            all_three = routing_request()
            all_three['calibration'].pop('routingSearchParameters')
            all_job = make(all_three, 'JOB-routing-three')
            all_job.publish_parameters = False
            outcome = all_job.calibrate()
            self.assertEqual(len(outcome['searchSpace']), 3)
            self.assertEqual(outcome['bestParameters'], request['calibration']['initialParameters'])
            self.assertTrue(np.isfinite([p['value'] for p in outcome['hydrograph']['forecast']]).all())


if __name__ == '__main__':
    unittest.main()
