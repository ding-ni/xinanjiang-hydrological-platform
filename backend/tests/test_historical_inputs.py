"""Archive queries must not inherit a delivered package's temporal limits."""
import copy
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_json, read_json
from hydro_database import HydroDatabase, hour
from historical_inputs import HistoricalInputs
from input_products import InputProducts, HISTORICAL_SPATIAL_ID
from model_schemes import ModelSchemes, input_definition, preparation_binding_key
from scheme_inputs import normalize_preparation, build_prepared
from test_input_service import InputFixture
from test_hydro_database import observations


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = InputFixture(self.root / 'model')
        public = observations(self.root / 'public')
        self.database = HydroDatabase(self.root / 'runtime')
        self.database.import_existing(public, public / 'events.json')
        self.schemes = ModelSchemes(self.database)
        self.products = InputProducts(model_root=self.fixture.root, runtime_root=self.database.runtime)
        self.service = HistoricalInputs(self.database, self.fixture.root)

    def test_raw_zeros_missing_levels_and_grid_failure_remain_distinct(self):
        with patch.object(self.service, '_initialize_spatial', side_effect=ValueError('空间配置未完成')):
            result = self.service.process(event_id='xf-test')
        self.assertEqual(result['sourceType'], 'database-observed')
        self.assertEqual(result['observationPreview']['rain']['70115800'], [0, None, None, 4.5])
        self.assertEqual(result['observationPreview']['river']['flows'], [0, 1.2, None, None])
        self.assertEqual(result['observationPreview']['river']['levels'], [3.2, None, None, None])
        self.assertEqual(result['quality']['gridStatus'], 'failed')
        self.assertEqual(result['product']['cells'], [])

    def test_cross_year_queries_keep_missing_hours_and_single_station_interpolation(self):
        result = self.service.process(start='2016-12-31T23:00', end='2017-01-01T03:00')
        self.assertEqual(result['summary']['windowHours'], 5)
        self.assertIsNone(result['observationPreview']['river']['flows'][0])
        self.assertEqual(result['observationPreview']['rain']['70115800'], [None, 0, None, None, 4.5])
        self.assertEqual(result['summary']['reportingStationCount'], [0, 2, 0, 0, 1])
        self.assertEqual(result['quality']['gridStatus'], 'incomplete')
        self.assertAlmostEqual(result['summary']['arealRainfallMm'][-1], 4.5)

    def test_explicit_processing_parent_links_only_verified_ancestors(self):
        from delivered_schemes import migrate_delivered_schemes, scheme_assets
        for identifier in ('INPUT-parent', 'INPUT-child', 'INPUT-unrelated'):
            write_json(self.products.registry_root / (identifier + '.json'), {})
            processing = {'operation': 'schemeInput', 'parentBundleVersionId': 'INPUT-parent',
                'windows': [{'id': 'A', 'start': '2017-01-01T00:00', 'end': '2017-01-01T03:00'}]} if identifier == 'INPUT-child' else {'operation': 'mdbImport'}
            write_json(self.products.tasks_root / identifier / 'request.json', processing)
        def resolve(identifier):
            return {'manifest': {'name': '同名记录', 'sourceTaskId': identifier}, 'paths': {}}
        with patch.object(self.products, 'resolve', side_effect=resolve):
            migrate_delivered_schemes(self.schemes, self.products)
            before = scheme_assets(self.schemes)
            migrate_delivered_schemes(self.schemes, self.products)
        self.assertEqual(before, scheme_assets(self.schemes))
        assets = {row['asset_id']: row for row in before}
        self.assertEqual(assets['INPUT-parent']['scheme_id'], assets['INPUT-child']['scheme_id'])
        self.assertEqual(assets['INPUT-parent']['role'], 'processing-ancestor')
        self.assertIsNone(assets['INPUT-unrelated']['scheme_id'])

    def test_concurrent_queries_share_preparation_and_database_changes_invalidate_cache(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: self.service.process(event_id='xf-test'), range(2)))
        self.assertEqual(sorted([a['cacheHit'], b['cacheHit']]), [False, True])
        self.assertEqual(a['product'], b['product'])
        a['observationPreview']['rain']['70115800'][0] = 900
        self.assertEqual(self.service.process(event_id='xf-test')['observationPreview']['rain']['70115800'][0], 0)
        with self.database.connect() as db:
            db.execute('UPDATE observations SET value=8 WHERE station_id=? AND variable=? AND time_hour=?',
                       ('70115800', 'rain', hour('2017-01-01T00:00')))
        c = self.service.process(event_id='xf-test')
        self.assertFalse(c['cacheHit'])
        self.assertNotEqual(b['observationVersion'], c['observationVersion'])

    def test_new_inputs_need_only_spatial_configuration_not_old_annual_files(self):
        # Redirect old process paths instead of deleting any fixture files.
        self.service.precipitation_dir = self.root / 'absent-rain'
        self.service.paths['flow'] = self.root / 'absent-flow.csv'
        result = self.service.process(event_id='xf-test')
        self.assertEqual(result['summary']['windowHours'], 4)
        self.assertTrue(result['product']['cells'])
        with patch.object(self.products, '_baseline_manifest', side_effect=AssertionError('old bundle used')):
            parent = self.products.resolve(HISTORICAL_SPATIAL_ID)
            self.assertNotIn('flow', parent['paths'])
            payload = {'operation': 'schemeInput', 'requestId': 'PREP-test', 'name': '历史库新选取',
                'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'parentBundleVersionId': HISTORICAL_SPATIAL_ID,
                'period': {'startTime': '2017-01-01T00:00', 'endTime': '2017-01-01T01:00', 'warmupDays': 0},
                'rainfallMethod': 'stationIdw', 'stationSourceId': 'LOCAL_OBSERVATIONS', 'operationPurpose': 'simulation',
                'evaporation': {'mode': 'monthly-climatology-assumption'}}
            normalized = normalize_preparation(payload, self.products)
            folder = self.products.tasks_root / 'DATA-history'
            write_json(folder / 'request.json', normalized)
            output = build_prepared(folder, self.products)
        manifest = read_json(self.products.products_root / output['productVersionId'] / 'manifest.json')
        self.products._descriptor(manifest, output['manifestSHA256'])
        self.assertTrue(all('precipitation_qc_' not in row['path'] and 'xufan_outlet_hourly_qc' not in row['path'] for row in manifest['files']))
        self.assertEqual(manifest['sourceOrigin']['sourceId'], 'LOCAL_OBSERVATIONS')

    def test_delivery_migration_is_idempotent_and_does_not_select_full_catalogue(self):
        from delivered_schemes import migrate_delivered_schemes, scheme_assets
        identifier = 'INPUT-fixture'
        write_json(self.products.registry_root / (identifier + '.json'), {})
        folder = self.products.tasks_root / 'DATA-delivery'
        write_json(folder / 'request.json', {'operation': 'mdbImport', 'recordQueryScope': {'eventWindowsScope': 'FULL_FL_CODE_TABLE'}})
        bundle = {'manifest': {'name': '原始加工', 'operation': 'mdbImport', 'sourceTaskId': folder.name},
                  'paths': self.service.paths}
        with patch.object(self.products, 'resolve', return_value=bundle):
            migrate_delivered_schemes(self.schemes, self.products)
            migrate_delivered_schemes(self.schemes, self.products)
        self.assertEqual(self.schemes.list()['schemes'], [])
        self.assertEqual(len(scheme_assets(self.schemes)), 1)
        self.assertEqual(scheme_assets(self.schemes)[0]['role'], 'unlinked')
        write_json(folder / 'request.json', {'operation': 'schemeInput', 'warmupHours': 168, 'minimumStations': 2, 'windows': [
            {'id': 'xf-local', 'start': '2017-01-01T00:00', 'end': '2017-01-01T03:00'}]})
        with patch.object(self.products, 'resolve', return_value=bundle):
            migrate_delivered_schemes(self.schemes, self.products)
            migrate_delivered_schemes(self.schemes, self.products)
        rows = self.schemes.list()['schemes']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['configurationPending'])
        self.assertEqual(rows[0]['deliverySelectionCount'], 1)
        self.assertEqual(rows[0]['summary']['selectedCount'], 0)
        self.assertNotIn('params', self.schemes.get(rows[0]['id'])['editor'])
        self.assertEqual(self.schemes.get(rows[0]['id'])['editor']['warmupDays'], 7)
        self.assertEqual(self.schemes.get(rows[0]['id'])['editor']['deliveryProcessing']['minimumStations'], 2)

    def test_frozen_preparation_validation_does_not_read_updated_observations(self):
        payload = {'operation': 'schemeInput', 'requestId': 'PREP-frozen', 'projectId': 'nananxi-xufan',
            'resolutionId': '1km', 'parentBundleVersionId': HISTORICAL_SPATIAL_ID,
            'period': {'startTime': '2017-01-01T00:00', 'endTime': '2017-01-01T01:00', 'warmupDays': 0},
            'rainfallMethod': 'stationIdw', 'stationSourceId': 'LOCAL_OBSERVATIONS',
            'evaporation': {'mode': 'monthly-climatology-assumption'}}
        saved = normalize_preparation(payload, self.products)
        with self.database.connect() as db:
            db.execute("UPDATE observations SET value=8 WHERE variable='rain'")
        self.assertNotEqual(saved['preparationKey'], normalize_preparation(payload, self.products)['preparationKey'])
        with patch('scheme_inputs.local_observations_digest', side_effect=AssertionError('Frozen validation read current archive')):
            self.assertEqual(saved['preparationKey'], normalize_preparation(payload, self.products, frozen_definition=saved)['preparationKey'])
            changed = copy.deepcopy(payload)
            changed['minimumStations'] = 2
            self.assertNotEqual(saved['preparationKey'], normalize_preparation(changed, self.products, frozen_definition=saved)['preparationKey'])
        request = {'science': {'versionId': 'science-A'}, 'inputs': {'evaporation': {}}, 'parameters': {'WM': 100}}
        record = {'definitionSHA256': 'frozen-input-selection'}
        changed = copy.deepcopy(request)
        changed['parameters']['WM'] = 150
        self.assertEqual(preparation_binding_key(record, request), preparation_binding_key(record, changed))
        changed['science']['versionId'] = 'science-B'
        self.assertNotEqual(preparation_binding_key(record, request), preparation_binding_key(record, changed))

    def test_database_http_uses_public_catalog_and_returns_raw_when_grid_fails(self):
        from server import TaskManager, ThreadingHTTPServer, handler
        with patch('server.ThreadPoolExecutor'):
            manager = TaskManager(self.database.runtime / 'tasks', products=self.products)
        manager.historical_inputs = self.service
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(manager, self.root, products=self.products))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def get(path):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/model/database/' + path) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())
        try:
            self.assertEqual(get('catalog')[1]['events'][0]['id'], 'xf-test')
            with patch.object(self.service, '_initialize_spatial', side_effect=ValueError('no grid')):
                status, process = get('process?eventId=xf-test')
            self.assertEqual(status, 200)
            self.assertEqual(process['quality']['gridStatus'], 'failed')
            self.assertEqual(process['observationPreview']['river']['flows'][1], 1.2)
            for query in ('start=2017-01-01T00:00', 'start=x&end=y', 'eventId=xf-test&start=x',
                          'eventId=xf-test&eventId=xf-test', 'projectId=other', 'resolutionId=other'):
                self.assertEqual(get('process?' + query)[0], 400)
            self.assertEqual(get('process?eventId=unknown')[0], 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            manager.close()


if __name__ == '__main__':
    unittest.main()
