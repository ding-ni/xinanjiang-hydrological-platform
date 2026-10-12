"""Rainfall-scenario contracts and real forecast/state separation."""
import copy
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from common import read_json, write_json
from contracts import normalize_request
from forecast_products import CONTEXT_KEY, ForecastProducts, freeze_forecast_parameters
from server import TaskManager
from workspace_backup import create_backup, restore_backup, verify_backup

CONTEXT = dict(contextKey=CONTEXT_KEY, projectId='nananxi-xufan', resolutionId='1km', modelConfigId='nananxi-xufan-1km')
ISSUE = '2017-06-19T11:00:00'


def rain_body(values=None):
    return dict(name='六小时降雨情景', contextKey=CONTEXT_KEY, issueTime=ISSUE,
                hourlyRainfallMm=values if values is not None else [0, 1, 5, 10, 2, 0])


def forecast_request(parent='STATE-JOB-test', rain=None, issue=ISSUE, lead=6):
    stamp = datetime.fromisoformat(issue)
    end = (stamp + timedelta(hours=lead)).isoformat()
    return dict(schemaVersion='2.0', requestId='FORECAST-TEST', runType='forecast', context=dict(CONTEXT),
                issue=dict(mode='manual', sectionId='70115800', issueTime=issue, leadHours=lead, forecastEndTime=end),
                period=dict(startTime=(stamp + timedelta(hours=1)).isoformat(), endTime=end,
                            warmupDays=1, initialStateVersionId=parent), parameters={}, lineage={},
                inputs=dict(evaporation=dict(mode='monthly-climatology-assumption'),
                            forecastPrecipitation={k: rain[k] for k in ('versionId', 'manifestSHA256')} if rain else {}))


class ForecastContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.products = ForecastProducts(self.temp.name)

    def test_rainfall_immutable_idempotent_and_persistent(self):
        row = self.products.register(rain_body())
        self.assertEqual(row, self.products.register(rain_body()))
        self.assertEqual(row, ForecastProducts(self.temp.name).list()[0])
        self.assertEqual(row['times'][0], '2017-06-19T12:00:00')
        self.assertEqual(row['times'][-1], '2017-06-19T17:00:00')
        self.assertEqual(row['totalMm'], 18)
        self.assertNotEqual(row['versionId'], self.products.register(rain_body([0] * 6))['versionId'])

    def test_invalid_rainfall(self):
        for values in ([], [0] * 73, [True], [None], [float('nan')], [float('inf')], [-1], [1001], '1 2'):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.products.register(rain_body(values))
        for key, value in [('issueTime', '2017-06-19'), ('issueTime', '2017-06-19T11:30'),
                           ('issueTime', ISSUE + 'Z'), ('contextKey', 'selinco'), ('name', ' '), ('name', 'x' * 81)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.products.register({**rain_body(), key: value})

    def test_hash_tamper_and_path_rejected(self):
        row = self.products.register(rain_body())
        row['hourlyRainfallMm'][0] = 99
        write_json(self.products.root / (row['versionId'] + '.json'), row)
        for version in (row['versionId'], '../state', None):
            with self.assertRaises(ValueError):
                self.products.resolve(version)

    def test_frozen_rain_selection(self):
        row = self.products.register(rain_body())
        request = forecast_request(rain=row)
        self.products.freeze(request)
        self.assertEqual(request['lineage']['forecastRainVersionId'], row['versionId'])
        for change in (lambda r: r['inputs']['forecastPrecipitation'].update(manifestSHA256='wrong'),
                       lambda r: r['inputs']['forecastPrecipitation'].update(hourlyRainfallMm=[0]),
                       lambda r: r['issue'].update(leadHours=7),
                       lambda r: r['issue'].update(issueTime='2017-06-19T12:00:00'),
                       lambda r: r['context'].update(contextKey='selinco'),
                       lambda r: r['lineage'].update(forecastRainVersionId='other')):
            bad = copy.deepcopy(request)
            change(bad)
            with self.assertRaises(ValueError):
                self.products.freeze(bad)

    def test_forecast_contract_and_future_dates(self):
        request = normalize_request(forecast_request(issue='2027-01-01T00:00:00'), case_catalog())
        self.assertEqual(request['period']['startTime'], '2027-01-01T01:00:00')
        for change in (lambda r: r['issue'].update(leadHours=True),
                       lambda r: r['issue'].update(leadHours=73),
                       lambda r: r['issue'].update(sectionId='OTHER'),
                       lambda r: r['issue'].update(mode='unknown'),
                       lambda r: r['issue'].update(forecastEndTime=ISSUE),
                       lambda r: r['period'].update(startTime=ISSUE),
                       lambda r: r['period'].update(initialStateVersionId=None),
                       lambda r: r['inputs'].update(evaporation={'mode': 'constant-zero'})):
            bad = forecast_request()
            change(bad)
            with self.assertRaises(ValueError):
                normalize_request(bad, case_catalog())

    def test_parameters_follow_state_and_automatic_requires_release(self):
        parameters = case_catalog()['cases']['nananxi-xufan:1km']['parameters']
        checkpoint = dict(time=ISSUE, stateVersionId='STATE-JOB-test',
                          parameters={'runoff.' + key: value for key, value in parameters.items()})
        with patch('state_integrity.assert_state_input_compatibility'):
            request = forecast_request()
            freeze_forecast_parameters(request, checkpoint, None)
            self.assertEqual(request['parameters'], parameters)
            self.assertEqual(request['lineage']['stateVersionId'], checkpoint['stateVersionId'])
            # A saved forecast sends the server-generated state-parameter ID back.
            freeze_forecast_parameters(request, checkpoint, None)
            self.assertEqual(request['inputs']['parameterVersionId'], 'STATE-PARAMETERS-STATE-JOB-test')
            for change in (lambda r: r.update(parameters={'SM': 60}),
                           lambda r: r['issue'].update(issueTime='2017-06-19T12:00:00'),
                           lambda r: r['issue'].update(mode='automatic'),
                           lambda r: r['lineage'].update(stateVersionId='STATE-JOB-other')):
                bad = forecast_request()
                change(bad)
                with self.assertRaises(ValueError):
                    freeze_forecast_parameters(bad, checkpoint, None)


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real forecast regression')
class ForecastNumericalTests(unittest.TestCase):
    def test_real_scenarios_restart_backup_and_state_separation(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'workspace'
            manager = TaskManager(root / 'tasks')
            try:
                def complete(task):
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        current = manager.get(task['taskId'])
                        if current['status'] not in {'queued', 'running'}:
                            self.assertEqual(current['status'], 'completed', current)
                            return manager.result(task['taskId'])
                        time.sleep(0.1)
                    self.fail('Real forecast timed out')

                initial = dict(schemaVersion='2.0', requestId='INIT-FORECAST', runType='stateInitialization',
                               context=dict(CONTEXT), period=dict(startTime=ISSUE, endTime=ISSUE, warmupDays=1),
                               parameters={}, inputs=dict(evaporation=dict(mode='monthly-climatology-assumption')), lineage={})
                state = complete(manager.submit(initial))
                parent = state['stateVersionId']
                before = {p.name: p.read_bytes() for p in (root / 'states').glob('*.json')}
                rain = manager.forecast_products.register(rain_body())
                request = forecast_request(parent, rain)
                task = manager.submit(request)
                wet = complete(task)
                self.assertEqual(manager.submit(request)['taskId'], task['taskId'])
                flow = wet['hydrograph']['forecast']
                self.assertEqual(len(flow), 6)
                self.assertEqual(wet['gridCount'], 110)
                self.assertEqual(wet['hydrograph']['obs'], [])
                self.assertAlmostEqual(wet['hydrograph']['volume'], sum(r['value'] for r in flow) * 3600)
                self.assertEqual(wet['parentStateVersionId'], parent)
                self.assertIsNone(wet['stateVersionId'])
                np.testing.assert_allclose(wet['hydrograph']['forcing']['precipitation'], rain['hourlyRainfallMm'])
                folder = manager.root / task['taskId']
                self.assertTrue((folder / 'forecast-end-state.json').is_file())
                self.assertFalse((folder / 'checkpoint.json').exists())
                manifest = read_json(folder / 'input-manifest.json')
                self.assertFalse(any(f['path'].endswith('.nc') for f in manifest['files']))
                with np.load(folder / 'grid-history.npz', allow_pickle=False) as arrays:
                    self.assertEqual(arrays['precipitation'].shape, (6, 110))
                    np.testing.assert_allclose(arrays['precipitation'], np.repeat(np.array([0, 1, 5, 10, 2, 0])[:, None], 110, axis=1))
                    self.assertLess(float(np.abs(arrays['water_balance_error']).max()), 1e-8)
                dry_rain = manager.forecast_products.register(rain_body([0] * 6))
                dry_request = forecast_request(parent, dry_rain)
                dry_request['requestId'] = 'DRY-FORECAST'
                dry_request['inputs']['parameterVersionId'] = wet['lineage']['parameterVersionId']
                dry_request['parameters'] = wet['parameters']
                dry = complete(manager.submit(dry_request))
                self.assertNotEqual(flow, dry['hydrograph']['forecast'])
                self.assertEqual(before, {p.name: p.read_bytes() for p in (root / 'states').glob('*.json')})
                self.assertEqual(manager.result(task['taskId']), wet)
                manager.archive(task['taskId'])
                backup = create_backup(root, Path(temp) / 'backup')
                backup_manifest = verify_backup(backup['path'])
                self.assertTrue(any(row['path'].startswith('forecast-rain/') for row in backup_manifest['files']))
                restore_backup(backup['path'], Path(temp) / 'restored')
                manager.close()
                manager = TaskManager(Path(temp) / 'restored' / 'tasks')
                self.assertEqual(manager.result(task['taskId']), wet)
                self.assertEqual(manager.forecast_products.resolve(rain['versionId']), rain)
                self.assertEqual(manager.submit(request)['taskId'], task['taskId'])
                print('FORECAST_REAL six hours / 110 grids / wet-dry / immutable state / restart / backup passed', flush=True)
            finally:
                manager.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
