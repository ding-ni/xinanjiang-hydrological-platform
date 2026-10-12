"""Selected search dimensions preserve fixed parameters and frozen requests."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration_space import coordinates, definitions, resolve_space
from catalog import case_catalog
from common import read_json, write_json
from contracts import normalize_request
from test_continuous_calibration import continuous_request


def selected_request(keys=('SM',), staged=False):
    request = continuous_request(staged)
    request['calibration']['searchParameters'] = {
        row['key']: {'minimum': row['minimum'], 'maximum': row['maximum']}
        for row in definitions() if row['key'] in keys}
    return request


class SearchSpaceTests(unittest.TestCase):
    def test_default_and_custom_are_idempotent_and_do_not_mutate(self):
        request = selected_request()
        frozen = normalize_request(request, case_catalog())
        self.assertEqual(normalize_request(frozen, case_catalog()), frozen)
        self.assertEqual([row['name'] for row in resolve_space(frozen['calibration'])], ['runoff.SM'])
        request['calibration']['searchParameters']['SM']['minimum'] = 20
        self.assertEqual(frozen['calibration']['searchParameters']['SM']['minimum'], 8)
        default = continuous_request(False)
        self.assertEqual(len(resolve_space(normalize_request(default, case_catalog())['calibration'])), 9)
        self.assertEqual(len(case_catalog()['cases']['nananxi-xufan:1km']['searchParameterDefinitions']), 9)

    def test_invalid_selection_is_rejected(self):
        values = [{}, [], True, {'UNKNOWN': {}}, {'SM': None}, {'SM': {'minimum': 8}},
                  {'SM': {'minimum': True, 'maximum': 60}}, {'SM': {'minimum': float('nan'), 'maximum': 60}},
                  {'SM': {'minimum': 8, 'maximum': float('inf')}}, {'SM': {'minimum': 60, 'maximum': 8}},
                  {'SM': {'minimum': 4, 'maximum': 60}}, {'SM': {'minimum': 8, 'maximum': 101}},
                  {'SM': {'minimum': 90, 'maximum': 100}}, {'KI': {'minimum': .001, 'maximum': .05}},
                  {'SM': {'minimum': 8, 'maximum': 60, 'extra': 1}}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                request = selected_request()
                request['calibration']['searchParameters'] = value
                normalize_request(request, case_catalog())

    def test_staged_requires_both_groups_and_wlm_does_not_override_custom(self):
        with self.assertRaisesRegex(ValueError, '分阶段'):
            normalize_request(selected_request(staged=True), case_catalog())
        frozen = normalize_request(selected_request(('WM', 'SM'), True), case_catalog())
        self.assertEqual(len(resolve_space(frozen['calibration'])), 2)
        request = selected_request()
        request['calibration']['wlmUpperBound'] = 110
        with self.assertRaisesRegex(ValueError, 'WLM'):
            normalize_request(request, case_catalog())

    def test_manual_ignores_search_choices_and_reports_all_fixed(self):
        from calibration_space import search_summary
        request = selected_request()
        request['calibration'].update(evaluationMode='manual', searchParameters={})
        frozen = normalize_request(request, case_catalog())
        params = frozen['calibration']['initialParameters']
        plan = search_summary(frozen['calibration'], params, params, resolve_space(frozen['calibration']))
        self.assertEqual(plan['mode'], 'manual')
        self.assertFalse(any(row['active'] for row in plan['rows']))
        self.assertTrue(all(row['value'] == row['initial'] for row in plan['rows']))

    def test_cloning_and_diagnostics_preserve_selection(self):
        from server import TaskManager
        from diagnostic_jobs import normalize_diagnostic
        with tempfile.TemporaryDirectory() as temp, patch('server.ThreadPoolExecutor'):
            manager = TaskManager(Path(temp) / 'tasks')
            try:
                request = selected_request()
                task = manager.submit(request)
                manager.cancel(task['taskId'])
                cloned = manager.clone(task['taskId'])
                self.assertEqual(cloned['runConfig']['calibration']['searchParameters'], request['calibration']['searchParameters'])
                diagnostic = normalize_diagnostic({'runType': 'diagnosticExperiment', 'baseRequest': request,
                                                   'mode': 'multiSeed', 'values': [1, 2, 3]}, manager)
                self.assertEqual(diagnostic['baseRequest']['calibration']['searchParameters'], request['calibration']['searchParameters'])
                with self.assertRaisesRegex(ValueError, '预设'):
                    normalize_diagnostic({'runType': 'diagnosticExperiment', 'baseRequest': request,
                                          'mode': 'wlmBoundary', 'values': [100, 110, 120]}, manager)
            finally:
                manager.close()


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real model regression')
class SearchSpaceNumericalTests(unittest.TestCase):
    def test_real_partial_searches_change_only_selected_coordinates(self):
        from worker import ModelJob
        with tempfile.TemporaryDirectory() as temp, patch('worker.RUNTIME', Path(temp)):
            for index, (keys, staged) in enumerate([
                    (('SM',), False), (('WM', 'SM'), True),
                    (('K_total_day',), False), (('groundwater_fraction',), False)]):
                with self.subTest(keys=keys):
                    request = normalize_request(selected_request(keys, staged), case_catalog())
                    folder = Path(temp) / 'tasks' / f'JOB-search-{index}'
                    write_json(folder / 'request.json', request)
                    write_json(folder / 'status.json', {'taskId': folder.name})
                    job = ModelJob(folder)
                    job.run()
                    result = read_json(folder / 'result.json')
                    before, after = coordinates(request['calibration']['initialParameters']), coordinates(result['bestParameters'])
                    plan = result['searchPlan']
                    self.assertEqual(plan['mode'], 'custom')
                    self.assertEqual({row['key'] for row in plan['rows'] if row['active']}, set(keys))
                    self.assertEqual(result['optimizer']['evaluations'], 10 if staged else 4)
                    for row in plan['rows']:
                        if row['active']:
                            self.assertLessEqual(row['minimum'], row['value'])
                            self.assertGreaterEqual(row['maximum'], row['value'])
                        else:
                            self.assertAlmostEqual(row['initial'], row['value'], places=12)
                    for key in result['bestParameters']:
                        if key not in keys and not (key in {'KI', 'KG'} and set(keys) & {'K_total_day', 'groundwater_fraction'}):
                            self.assertEqual(before[key], after[key], key)
                    for history in result['optimizer']['history']:
                        if history.get('kind') != 'initial':
                            self.assertLessEqual(set(history['parameters']), {f'runoff.{key}' for key in keys})
                    self.assertEqual(set(result['hydrographs']), {'CALIBRATION', 'VALIDATION', 'INDEPENDENT'})
                    self.assertIn('calibration_space.py', read_json(folder / 'input-manifest.json')['calibrationImplementation'])
                    if keys == ('SM',):
                        self.assertNotEqual(before['SM'], after['SM'])


if __name__ == '__main__':
    unittest.main()
