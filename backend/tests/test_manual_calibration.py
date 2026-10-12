import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from contracts import normalize_request
from common import write_json, read_json
from calibration_search import run_search
from test_continuous_calibration import continuous_request


class ManualCalibrationTests(unittest.TestCase):
    def test_single_event_manual_trial_does_not_require_validation_group(self):
        from test_unified_backend import calibration
        request = calibration()
        selected = next(row for row in request['period']['events'] if row['purpose'] == '率定')
        request['period']['events'] = [selected]
        request['period']['eventSet']['selectedEventIds'] = [selected['id']]
        request['calibration']['evaluationMode'] = 'manual'
        normalized = normalize_request(request, case_catalog())
        self.assertEqual(normalized['period']['events'], [selected])
        request['calibration']['evaluationMode'] = 'automatic'
        with self.assertRaisesRegex(ValueError, '验证洪水'):
            normalize_request(request, case_catalog())

    def test_manual_freezes_exact_parameters_and_no_search_budget(self):
        request = continuous_request()
        request['calibration']['evaluationMode'] = 'manual'
        normalized = normalize_request(request, case_catalog())
        self.assertEqual(normalized['calibration']['initialParameters'], request['calibration']['initialParameters'])
        self.assertEqual(normalized['calibration']['optimizer']['algorithm'], 'FIXED_PARAMETERS')
        self.assertEqual(normalized['calibration']['optimizer']['maxEvaluations'], 1)
        self.assertEqual(normalize_request(normalized, case_catalog()), normalized)
        normalized['calibration']['initialParameters'].update(WM=50, WUM=50, WLM=50)
        with self.assertRaisesRegex(ValueError, 'WM'):
            normalize_request(normalized, case_catalog())

    def test_fixed_parameter_evaluation_never_calls_optimizer(self):
        initial = {'SM': 20}
        calls = []
        def forbidden(*args):
            self.fail('Manual trial called an optimizer or candidate transformation')
        best, outcome = run_search([], {'algorithm': 'FIXED_PARAMETERS', 'maxEvaluations': 1}, initial,
                                   lambda p: calls.append(copy.deepcopy(p)) or 2.5,
                                   forbidden, dict, forbidden, lambda *args: None)
        self.assertEqual(calls, [initial])
        self.assertEqual(best, initial)
        self.assertIsNot(best, initial)
        self.assertEqual(outcome['evaluations'], 1)
        self.assertEqual(outcome['best_loss'], 2.5)

    @unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real model regression')
    def test_real_manual_changes_only_requested_parameter_and_preserves_original(self):
        import numpy as np
        from worker import ModelJob
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            results = []
            request = continuous_request()
            request['calibration']['evaluationMode'] = 'manual'
            parameters = case_catalog()['cases']['nananxi-xufan:1km']['parameters']
            for index, sm in enumerate([20, 60]):
                request['calibration']['initialParameters'] = {**parameters, 'SM': sm}
                folder = Path(temp) / 'tasks' / f'JOB-manual-{index}'
                write_json(folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(folder / 'status.json', {'taskId': folder.name})
                ModelJob(folder).run()
                result = read_json(folder / 'result.json')
                self.assertEqual(result['bestParameters'], request['calibration']['initialParameters'])
                self.assertEqual(result['optimizer']['evaluations'], 1)
                self.assertEqual(result['evaluationMode'], 'manual')
                self.assertEqual(len(result['hydrographs']), 3)
                results.append(result)
            self.assertEqual(read_json(Path(temp) / 'tasks/JOB-manual-0/result.json'), results[0])
            a, b = (np.array([p['value'] for p in result['hydrographs']['CALIBRATION']['forecast']]) for result in results)
            self.assertTrue(np.isfinite(a).all() and np.isfinite(b).all())
            self.assertGreater(np.max(np.abs(a-b)), 1e-6)
            from test_unified_backend import calibration
            event_request = calibration()
            event_request['calibration']['evaluationMode'] = 'manual'
            selected = next(row for row in event_request['period']['events'] if row['purpose'] == '率定')
            event_request['period']['events'] = [selected]
            event_request['period']['eventSet']['selectedEventIds'] = [selected['id']]
            folder = Path(temp) / 'tasks/JOB-manual-event'
            write_json(folder / 'request.json', normalize_request(event_request, case_catalog()))
            write_json(folder / 'status.json', {'taskId': folder.name})
            ModelJob(folder).run()
            event_result = read_json(folder / 'result.json')
            self.assertEqual(event_result['bestParameters'], event_request['calibration']['initialParameters'])
            self.assertEqual(event_result['petMode'], 'CONSTANT_ZERO_EXPLICIT_REQUEST')
            self.assertEqual(event_result['optimizer']['evaluations'], 1)
            self.assertEqual(list(event_result['hydrographs']), [selected['id']])
            self.assertEqual(event_result['validationMetrics']['event_count'], 0)


if __name__ == '__main__':
    unittest.main()
