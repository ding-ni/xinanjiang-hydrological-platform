"""Scheme revisions, automatic preparation and source/state continuity boundaries."""
import copy
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_json, read_json, MODEL
from hydro_database import HydroDatabase
from input_products import InputProducts
from model_schemes import ModelSchemes, input_definition
from scheme_inputs import normalize_preparation, build_prepared, local_observations_digest
from test_input_service import InputFixture
from test_unified_backend import simulation, calibration
from test_continuous_calibration import continuous_request


class SchemeTests(unittest.TestCase):
    def test_saved_unselected_bounds_are_versioned_but_viewed_event_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=ModelSchemes(HydroDatabase(directory))
            request=simulation();request['inputs']['bundleVersionId']='source-A'
            body={'name':'范围记忆','definition':input_definition(request),'calculationDraft':request,'editor':{'eventId':'A','searchRangeOverrides':{'SM':{'minimum':8,'maximum':60}}}}
            first=repo.save(body)
            body['editor']['eventId']='B'
            second=repo.save({**body,'id':first['id'],'revision':1})
            self.assertEqual(second['revision'],1)
            body['editor']['searchRangeOverrides']['SM']['maximum']=80
            third=repo.save({**body,'id':first['id'],'revision':1})
            self.assertEqual(third['revision'],2)
            self.assertEqual(repo.get(first['id'],1)['savedEditor']['searchRangeOverrides']['SM']['maximum'],60)
            self.assertEqual(third['savedEditor']['searchRangeOverrides']['SM']['maximum'],80)
    def test_drafts_are_conflict_checked_and_never_overlay_saved_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = ModelSchemes(HydroDatabase(directory))
            request = simulation()
            request['inputs']['bundleVersionId'] = 'source-A'
            first = repo.save({'name': '原名', 'definition': input_definition(request), 'calculationDraft': request,
                               'editor': {'params': {'B': .2}}})
            body = {'editor': {'params': {'B': .6}}, 'revision': 1, 'draftRevision': first['draftRevision']}
            draft = repo.save_editor(first['id'], body)
            with self.assertRaisesRegex(RuntimeError, '另一页面'):
                repo.save_editor(first['id'], body)
            self.assertEqual(draft['savedEditor']['params']['B'], .2)
            renamed = repo.rename(first['id'], {'name': '新名'})
            self.assertEqual(renamed['revision'], 1)
            self.assertEqual(renamed['calculationDraft'], first['calculationDraft'])
            discarded = repo.discard_editor(first['id'], {'revision': 1, 'draftRevision': draft['draftRevision']})
            self.assertFalse(discarded['hasDraft'])
            self.assertEqual(discarded['savedEditor'], first['savedEditor'])

    def test_mdb_timeout_splits_without_duplicate_hours_and_rejects_other_errors(self):
        from scheme_inputs import query_mdb_window
        import re
        from datetime import datetime
        calls = []
        def query(snapshot, folder, sql, timeout):
            self.assertEqual(timeout, 60)
            dates = [datetime.strptime(value, '%m/%d/%Y %H:%M:%S') for value in re.findall(r'#([^#]+)#', sql)]
            calls.append(dates)
            if dates[0] != dates[1]:
                raise RuntimeError('MDB_DRIVER_TIMEOUT')
            return [dates[0].isoformat()]
        values = query_mdb_window(None, None, 'SELECT * WHERE TM{window}', '2020-01-01T00:00', '2020-01-01T03:00', query=query)
        self.assertEqual(len(values), 4)
        self.assertEqual(len(set(values)), 4)
        with self.assertRaisesRegex(RuntimeError, 'bad source'):
            query_mdb_window(None, None, '{window}', '2020-01-01T00:00', '2020-01-01T03:00', query=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('bad source')))
        with self.assertRaisesRegex(ValueError, '2020-01-01T00:00'):
            query_mdb_window(None, None, '{window}', '2020-01-01T00:00', '2020-01-01T00:00', query=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('MDB_DRIVER_TIMEOUT')))

    def test_input_revision_and_calculation_draft_are_distinct_and_freeze_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ModelSchemes(HydroDatabase(directory))
            request = simulation()
            request['inputs']['bundleVersionId'] = 'source-A'
            body = {'name': '试验方案', 'definition': input_definition(request), 'calculationDraft': request}
            first = repository.save(body)
            request['parameters']['B'] = .4
            second = repository.save({**body, 'id': first['id'], 'revision': 1, 'calculationDraft': request})
            self.assertEqual(second['revision'], 2)
            self.assertEqual(repository.get(first['id'], 1)['calculationDraft']['parameters']['B'], first['calculationDraft']['parameters']['B'])
            request['period']['endTime'] = '2017-06-22T00:00'
            third = repository.save({**body, 'id': first['id'], 'revision': 2, 'definition': input_definition(request), 'calculationDraft': request})
            self.assertEqual(third['revision'], 3)
            self.assertNotEqual(repository.get(first['id'], 1)['definition'], third['definition'])
            with self.assertRaises(RuntimeError):
                repository.save({**body, 'id': first['id'], 'revision': 1, 'definition': input_definition(request)})
            request['scheme'] = {'id': first['id'], 'revision': 3}
            self.assertEqual(repository.freeze(request, strict=True)['lineage']['schemeRevision'], 3)
            request['parameters']['B'] = .5
            with self.assertRaisesRegex(ValueError, '计算参数'):
                repository.freeze(request, strict=True)
            request['period']['endTime'] = '2017-06-23T00:00'
            with self.assertRaisesRegex(ValueError, '修订不一致'):
                repository.freeze(request)

    def test_source_scope_never_silently_merges_conflicting_records(self):
        from test_hydro_database import observations, conflicting_rain
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = observations(root / 'public')
            database = HydroDatabase(root / 'runtime')
            database.import_existing(public, public / 'events.json')
            before = local_observations_digest(InputProducts(runtime_root=root / 'runtime'), '2017-01-01T00:00', '2017-01-01T03:00')
            conflicting_rain(database, '2017-01-01T00:00')
            with self.assertRaises(RuntimeError):
                database.window('70115800', 'rain', '2017-01-01T00:00', '2017-01-01T03:00')
            alternative = database.window('70115800', 'rain', '2017-01-01T00:00', '2017-01-01T03:00', sourceId='ALT-rain')
            self.assertEqual(alternative['series'][0]['value'], 12)
            self.assertEqual(alternative['missing'], 3)
            after = local_observations_digest(InputProducts(runtime_root=root / 'runtime'), '2017-01-01T00:00', '2017-01-01T03:00')
            self.assertNotEqual(before, after)

    def test_local_digest_preserves_original_order_sources_and_station_metadata(self):
        import hashlib
        import json
        from hydro_database import hour
        from test_hydro_database import observations, conflicting_rain

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = observations(root / 'public')
            database = HydroDatabase(root / 'runtime')
            database.import_existing(public, public / 'events.json')
            products = InputProducts(runtime_root=root / 'runtime')
            first, last = '2017-01-01T00:00', '2017-01-01T03:00'

            def legacy_digest():
                digest = hashlib.sha256()
                with database.connect() as db:
                    for row in db.execute('''SELECT station_id,variable,time_hour,value,source_id
                            FROM observations WHERE time_hour BETWEEN ? AND ?
                            ORDER BY station_id,variable,time_hour,source_id''', (hour(first), hour(last))):
                        digest.update(json.dumps(list(row), separators=(',', ':')).encode())
                    for row in db.execute('SELECT * FROM stations ORDER BY station_id'):
                        digest.update(json.dumps(list(row), separators=(',', ':')).encode())
                return digest.hexdigest()

            original = local_observations_digest(products, first, last)
            self.assertEqual(original, legacy_digest())
            conflicting_rain(database, first)
            with database.connect() as db:
                db.execute('INSERT INTO source_files VALUES (?,?,?,?,?,?,?)',
                           ('ALT-river', 'nananxi-xufan', 'river', 'observations/river/alternate.json', '1' * 64, 2, 'test'))
                db.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?)', [
                    ('ALT-river', 'nananxi-xufan', '70115800', 'flow', hour(first), 7.5),
                    ('ALT-river', 'nananxi-xufan', '70115800', 'level', hour(first), None),
                ])
            conflicted = local_observations_digest(products, first, last)
            self.assertEqual(conflicted, legacy_digest())
            self.assertNotEqual(conflicted, original)

            # Unrelated hours do not change the selected-period identity.
            with database.connect() as db:
                db.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?)', [
                    ('ALT-rain', 'nananxi-xufan', '70115800', 'rain', hour(first) - 1, 50.),
                    ('ALT-river', 'nananxi-xufan', '70115800', 'flow', hour(last) + 1, 99.),
                ])
            self.assertEqual(local_observations_digest(products, first, last), conflicted)
            self.assertEqual(local_observations_digest(products, first, last), legacy_digest())

            # Station coordinates still belong to the same source identity.
            with database.connect() as db:
                db.execute('UPDATE stations SET lon=lon+0.01 WHERE project_id=? AND station_id=?',
                           ('nananxi-xufan', '70149600'))
            relocated = local_observations_digest(products, first, last)
            self.assertEqual(relocated, legacy_digest())
            self.assertNotEqual(relocated, conflicted)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = InputFixture(self.root / 'model')
        self.products = InputProducts(self.fixture.root, self.root / 'runtime')
        from station_network import station_network
        station_network(self.products, self.products.resolve(), {'stations': [{'id': 'rain-A', 'lon': 119., 'lat': 30.}], 'flowStation': 'flow-A'})

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, period, **settings):
        request = normalize_preparation({'requestId': 'PREP-TEST', 'operation': 'schemeInput', 'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'period': period, **settings}, self.products)
        folder = self.products.tasks_root / 'DATA-test'
        write_json(folder / 'request.json', request)
        return build_prepared(folder, self.products)

    def test_event_default_prepares_fourteen_days_and_frozen_preparation_keeps_original(self):
        from scheme_inputs import required_drive_intervals
        from science_context import ScienceVersions
        payload = {'requestId': 'PREP-DEFAULT', 'operation': 'schemeInput',
                   'projectId': 'nananxi-xufan', 'resolutionId': '1km',
                   'period': {'mode': 'event', 'events': [{'id': 'xf-31180101',
                       'start': '2018-01-01T00:00', 'end': '2018-01-01T03:00', 'purpose': '率定'}]}}
        current = normalize_preparation(payload, self.products)
        self.assertEqual(current['warmupHours'], 336)
        self.assertEqual(required_drive_intervals(current)[0]['start'], '2017-12-18T00:00:00')
        explicit = copy.deepcopy(payload)
        explicit['period']['warmupDays'] = 7
        previous = normalize_preparation(explicit, self.products)
        restored = normalize_preparation(payload, self.products, frozen_definition=previous)
        self.assertEqual(restored['warmupHours'], 168)
        self.assertEqual(restored['preparationKey'], previous['preparationKey'])
        registry = ScienceVersions(self.products.runtime_root)
        for preparation in (current, restored):
            science = registry.resolve(preparation['science']['versionId'])
            self.assertEqual(science['statePolicy']['warmupHours'], preparation['warmupHours'])
        legacy = normalize_preparation(payload, self.products, frozen_definition={})
        self.assertEqual(legacy['warmupHours'], 168)

    def test_cross_year_reuses_exact_grids_and_reports_warmup_gaps(self):
        original = self.fixture.hashes()
        result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 2 / 24})
        self.assertEqual(result['issues'], [])
        self.assertTrue(result['reused'])
        self.assertEqual(self.fixture.hashes(), original)
        missing = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 1})
        self.assertEqual(missing['issues'][0]['missingIntervals'][0]['hours'], 22)

    def test_event_preparation_rejects_old_origin_and_reports_exact_unshortened_warmup_gap(self):
        period = {'mode': 'event', 'warmupDays': 1, 'events': [
            {'id': 'USER-FLOOD', 'start': '2018-01-01T00:00', 'end': '2018-01-01T03:00', 'purpose': '率定'}]}
        with self.assertRaisesRegex(ValueError, '各场预热'):
            self.prepare(period, statePolicy='continuous-origin', continuousOrigin='2017-01-01T00:00')
        result = self.prepare(period, statePolicy='independent-warmup')
        gap = result['issues'][0]['missingIntervals'][0]
        self.assertEqual(gap['start'], '2017-12-31T00:00:00')
        self.assertEqual(gap['end'], '2017-12-31T21:00:00')
        self.assertEqual(gap['hours'], 22)
        self.assertEqual(result['start'], '2017-12-31T00:00:00')

    def test_concurrent_identical_preparations_use_one_queue_record(self):
        from concurrent.futures import ThreadPoolExecutor
        from server import InputTaskManager
        from scheme_inputs import reuse_preparation
        with patch('server.ThreadPoolExecutor'):
            manager = InputTaskManager(self.products.tasks_root, products=self.products)
            try:
                request = {'operation': 'schemeInput', 'projectId': 'nananxi-xufan', 'resolutionId': '1km',
                           'period': {'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 0}}
                with ThreadPoolExecutor(max_workers=2) as clients:
                    results = list(clients.map(lambda index: reuse_preparation(manager, {**request, 'requestId': f'PREP-CONCURRENT-{index}'}), range(2)))
                self.assertEqual(results[0]['taskId'], results[1]['taskId'])
                self.assertEqual(len(manager.list()), 1)
            finally:
                manager.close()

    def test_custom_window_is_saved_and_source_quality_is_enforced(self):
        period = {'mode': 'event', 'warmupDays': 2 / 24, 'events': [{'id': 'USER-FLOOD', 'start': '2018-01-01T00:00', 'end': '2018-01-01T03:00', 'purpose': '率定'}]}
        result = self.prepare(period)
        self.assertEqual(result['issues'], [])
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        catalog = read_json(self.products._path(manifest['paths']['events']))
        self.assertEqual(catalog['packages'][0]['eventId'], 'USER-FLOOD')
        period['events'][0]['end'] = '2018-01-01T05:00'
        missing = self.prepare(period)
        self.assertTrue(missing['issues'])
        self.assertEqual(missing['issues'][0]['missingIntervals'][0]['hours'], 2)

    def test_high_runoff_ratio_is_accepted_in_preparation_and_preview(self):
        from input_service import InputService
        self.fixture.flows = [100000.] * 6
        self.fixture.write_flow()
        self.products = InputProducts(self.fixture.root, self.root / 'high-ratio-runtime')
        period = {'mode': 'event', 'warmupDays': 2 / 24, 'events': [
            {'id': 'USER-FLOOD', 'start': '2018-01-01T00:00', 'end': '2018-01-01T03:00', 'purpose': '率定'}]}
        result = self.prepare(period)
        self.assertEqual(result['issues'], [])
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        record = read_json(self.products._path(manifest['paths']['events']))['packages'][0]
        self.assertTrue(record['quality']['accepted'])
        self.assertGreater(record['quality']['observed_runoff_to_precipitation_ratio'], 100)
        preview = InputService(self.fixture.root).event('xf-31180101', warmup_hours=2)
        self.assertTrue(preview['quality']['model_input_ready'])
        self.assertGreater(preview['quality']['observed_runoff_to_precipitation_ratio'], 100)
        period['events'][0]['end'] = '2018-01-01T05:00'
        self.assertTrue(self.prepare(period)['issues'][0]['missingIntervals'])

    def test_new_quality_policy_bypasses_old_preparation_cache_and_scheme_binding(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from input_products import manifest_digest
        from model_schemes import preparation_binding_key
        from scheme_inputs import reuse_preparation
        payload = {'requestId': 'PREP-UNCAPPED', 'operation': 'schemeInput',
                   'projectId': 'nananxi-xufan', 'resolutionId': '1km',
                   'period': {'mode': 'event', 'warmupDays': 0, 'events': [
                       {'id': 'xf-31180101', 'start': '2018-01-01T00:00',
                        'end': '2018-01-01T03:00', 'purpose': '率定'}]}}
        current = normalize_preparation(payload, self.products)
        # Frozen requests from before this policy have no policy field.
        previous = normalize_preparation(payload, self.products, frozen_definition={})
        self.assertNotEqual(current['preparationKey'], previous['preparationKey'])
        self.assertEqual(previous, normalize_preparation(payload, self.products, frozen_definition=previous))
        manager = SimpleNamespace(lock=threading.Lock(), products=self.products,
            list=lambda: [{'status': 'completed', 'runConfig': previous, 'taskId': 'DATA-old'}],
            submit=Mock(return_value={'taskId': 'DATA-new'}))
        self.assertEqual(reuse_preparation(manager, payload)['taskId'], 'DATA-new')
        manager.submit.assert_called_once_with(payload)
        record = {'definitionSHA256': 'fixed-definition'}
        request = {'runType': 'calibration', 'period': payload['period']}
        old_key = manifest_digest({'definition': record['definitionSHA256'], 'science': {}, 'evaporation': {}})
        self.assertNotEqual(preparation_binding_key(record, request), old_key)
        self.assertEqual(preparation_binding_key(record, {**request, 'sourcePlan': {'planId': 'historical'}}), old_key)

    def test_multiyear_blocks_keep_missing_time_as_nan(self):
        from strict_forcing import load_hourly_forcing
        import numpy as np
        import csv
        bundle = self.products.resolve()
        with bundle['paths']['grids'].open(encoding='utf-8-sig') as stream:
            grids = list(csv.DictReader(stream))
        times, values, counts, _ = load_hourly_forcing(bundle, grids, '2016-12-31T23:00', '2019-01-01T00:00', require_complete=False)
        self.assertEqual(len(times), 17522)
        self.assertTrue(np.isnan(values[0]).all())
        self.assertTrue(np.isnan(values[-1]).all())
        with self.assertRaisesRegex(ValueError, '缺测'):
            load_hourly_forcing(bundle, grids, times[0], times[-1])

    def test_station_processing_uses_full_annual_block_and_records_both_sources(self):
        import numpy as np
        import pandas as pd
        from strict_forcing import load_hourly_forcing
        import csv
        times = pd.date_range('2017-12-31T22:00', '2018-01-01T03:00', freq='h')
        def observations(products, parent, first, last, folder):
            block = pd.date_range(first, last, freq='h')
            return block, ['station'], [(119., 30.)], np.full((len(block), 1), 2.), list(range(len(block))), 'LOCAL_OBSERVATIONS', 'test-flow'
        with patch('scheme_inputs.station_block', observations):
            result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 2 / 24}, rainfallMethod='stationIdw')
        self.assertEqual(result['issues'], [])
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        self.assertEqual(manifest['sourceOrigin']['flowSource'], 'test-flow')
        self.assertEqual(manifest['sourceOrigin']['sourceId'], 'LOCAL_OBSERVATIONS')
        self.assertEqual(manifest['coverage']['years'], [2017, 2018])
        bundle = self.products._descriptor(manifest, result['manifestSHA256'], verify=True)
        with bundle['paths']['grids'].open(encoding='utf-8-sig') as stream:
            grids = list(csv.DictReader(stream))
        actual, rain, _, _ = load_hourly_forcing(bundle, grids, times[0], times[-1])
        self.assertTrue(actual.equals(times))
        np.testing.assert_array_equal(rain, np.full((6, 3), 2.))

    def test_independent_floods_prepare_only_selected_warmup_intervals(self):
        import numpy as np
        import pandas as pd
        import xarray as xr
        from strict_forcing import load_hourly_forcing
        calls = []
        def observations(products, parent, first, last, folder):
            calls.append((first, last))
            block = pd.date_range(first, last, freq='h')
            return block, ['rain-A'], [(119., 30.)], np.full((len(block), 1), 2.), [1.] * len(block), 'LOCAL_OBSERVATIONS', 'LOCAL_OBSERVATIONS'
        period = {'mode': 'event', 'warmupDays': 2/24, 'events': [
            {'id': 'EARLY', 'start': '1964-07-01T00:00', 'end': '1964-07-01T03:00', 'purpose': '率定'},
            {'id': 'LATE', 'start': '2024-07-01T00:00', 'end': '2024-07-01T03:00', 'purpose': '验证'},
            {'id': 'OVERLAP', 'start': '2024-07-01T03:00', 'end': '2024-07-01T05:00', 'purpose': '验证'},
        ]}
        with patch('scheme_inputs.station_block', observations):
            result = self.prepare(period, rainfallMethod='stationIdw', operationPurpose='simulation')
        self.assertEqual(result['issues'], [])
        self.assertEqual(len(calls), 2)
        self.assertEqual([(first.isoformat(), last.isoformat()) for first, last in calls], [
            ('1964-06-30T22:00:00', '1964-07-01T03:00:00'),
            ('2024-06-30T22:00:00', '2024-07-01T05:00:00')])
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        self.assertEqual(manifest['coverage']['hours'], 14)
        self.assertEqual(manifest['coverage']['years'], [1964, 2024])
        self.assertEqual(len(manifest['coverage']['intervals']), 2)
        directory = self.products._path(manifest['precipitationDirectory'])
        self.assertEqual(len(list(directory.glob('*.nc'))), 2)
        with xr.open_dataset(directory / 'nananxi_precip_hourly_1km_2024.nc', engine='h5netcdf') as dataset:
            self.assertEqual(len(dataset.time), 8)

    def test_disjoint_intervals_in_one_year_share_a_sparse_file(self):
        import numpy as np
        import pandas as pd
        import xarray as xr
        calls = []
        def observations(products, parent, first, last, folder):
            calls.append((first, last))
            block = pd.date_range(first, last, freq='h')
            return block, ['rain-A'], [(119., 30.)], np.full((len(block), 1), 2.), [1.] * len(block), 'LOCAL_OBSERVATIONS', 'LOCAL_OBSERVATIONS'
        period = {'mode': 'event', 'warmupDays': 0, 'events': [
            {'id': 'FIRST', 'start': '2024-01-01T00:00', 'end': '2024-01-01T03:00', 'purpose': '率定'},
            {'id': 'SECOND', 'start': '2024-12-01T00:00', 'end': '2024-12-01T03:00', 'purpose': '验证'}]}
        with patch('scheme_inputs.station_block', observations):
            result = self.prepare(period, rainfallMethod='stationIdw', operationPurpose='simulation')
        self.assertEqual(len(calls), 2)
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        directory = self.products._path(manifest['precipitationDirectory'])
        self.assertEqual(len(list(directory.glob('*.nc'))), 1)
        with xr.open_dataset(directory / 'nananxi_precip_hourly_1km_2024.nc', engine='h5netcdf') as dataset:
            self.assertEqual(len(dataset.time), 8)
            self.assertEqual(len(set(dataset.time.values)), 8)

    def test_continuous_origin_keeps_full_drive_between_evaluation_windows(self):
        from scheme_inputs import required_drive_intervals
        body = {'period': {'mode': 'event'}, 'statePolicy': 'continuous-origin',
                'start': '2023-12-31T00:00:00', 'end': '2025-01-01T00:00:00', 'warmupHours': 168,
                'windows': [{'start': '2024-01-01T00:00:00', 'end': '2024-01-02T00:00:00'},
                            {'start': '2025-01-01T00:00:00', 'end': '2025-01-01T00:00:00'}]}
        self.assertEqual(required_drive_intervals(body), [{'start': body['start'], 'end': body['end']}])

    def test_sparse_source_digest_ignores_unselected_gap_but_tracks_selected_rain(self):
        from test_hydro_database import observations, conflicting_rain
        from hydro_database import hour
        database = HydroDatabase(self.products.runtime_root)
        public = observations(self.root / 'public-sparse')
        database.import_existing(public, public / 'events.json')
        ranges = [{'start': '2017-01-01T00:00', 'end': '2017-01-01T00:00'},
                  {'start': '2017-01-01T03:00', 'end': '2017-01-01T03:00'}]
        first = local_observations_digest(self.products, ranges[0]['start'], ranges[-1]['end'], ranges)
        conflicting_rain(database, '2017-01-01T01:00')
        self.assertEqual(local_observations_digest(self.products, ranges[0]['start'], ranges[-1]['end'], ranges), first)
        with database.connect() as db:
            db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?)',
                ('ALT-rain', 'nananxi-xufan', '70115800', 'rain', hour(ranges[0]['start']), 12.))
        self.assertNotEqual(local_observations_digest(self.products, ranges[0]['start'], ranges[-1]['end'], ranges), first)

    def test_configured_sql_stations_prepare_rain_and_flow_without_mocking(self):
        import numpy as np
        from hydro_database import hour
        from strict_forcing import load_hourly_forcing
        import csv
        database = HydroDatabase(self.products.runtime_root)
        with database.connect() as db:
            db.execute("INSERT INTO projects VALUES ('nananxi-xufan','Test')")
            for site, kind in [('rain-A', '雨量站'), ('flow-A', '水文站')]:
                db.execute('INSERT INTO stations VALUES (?,?,?,?,?,?,?)', ('nananxi-xufan', site, site, kind, 119., 30., '{}'))
            for variable, site, values in [('rain', 'rain-A', [2.] * 6), ('flow', 'flow-A', self.fixture.flows)]:
                source = 'declared-' + variable
                db.execute('INSERT INTO source_files VALUES (?,?,?,?,?,?,?)', (source, 'nananxi-xufan', variable, variable, '0'*64, 6, 'test'))
                db.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?)', [(source, 'nananxi-xufan', site, variable, hour(time.isoformat()), value) for time, value in zip(self.fixture.times, values)])
        result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 2/24}, rainfallMethod='stationIdw')
        self.assertEqual(result['issues'], [])
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        bundle = self.products._descriptor(manifest, result['manifestSHA256'], verify=True)
        with bundle['paths']['grids'].open(encoding='utf-8-sig') as stream:
            grids = list(csv.DictReader(stream))
        _, rain, _, _ = load_hourly_forcing(bundle, grids, self.fixture.times[0], self.fixture.times[-1])
        np.testing.assert_array_equal(rain, np.full((6, 3), 2.))
        import pandas as pd
        np.testing.assert_array_equal(pd.read_csv(bundle['paths']['flow']).flow_m3_s, self.fixture.flows)

    def test_network_change_invalidates_preparation_identity(self):
        from station_network import station_network
        payload = {'requestId': 'PREP-NETWORK', 'operation': 'schemeInput', 'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'period': {'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 0}, 'rainfallMethod': 'stationIdw'}
        first = normalize_preparation(payload, self.products)
        station_network(self.products, self.products.resolve(), {'stations': [{'id': 'rain-A', 'lon': 119.1, 'lat': 30.}], 'flowStation': 'flow-A'})
        second = normalize_preparation(payload, self.products)
        self.assertNotEqual(first['preparationKey'], second['preparationKey'])

    def test_local_station_product_does_not_inherit_parent_mdb_source(self):
        import numpy as np
        import pandas as pd
        parent = copy.deepcopy(self.products.resolve())
        parent['manifest']['sourceId'] = 'OTHER-MDB'
        def observations(products, adapter, first, last, folder):
            self.assertNotIn('sourceId', adapter['manifest'])
            block = pd.date_range(first, last, freq='h')
            return block, ['rain-A'], [(119., 30.)], np.full((len(block), 1), 2.), [1.] * len(block), 'LOCAL_OBSERVATIONS', 'LOCAL_OBSERVATIONS'
        with patch.object(self.products, 'resolve', return_value=parent), patch('scheme_inputs.station_block', observations):
            result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 0}, rainfallMethod='stationIdw', stationSourceId='LOCAL_OBSERVATIONS')
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        self.assertNotIn('sourceId', manifest)
        self.assertEqual(manifest['sourceOrigin']['sourceId'], 'LOCAL_OBSERVATIONS')

    def test_simulation_without_flow_keeps_rain_and_does_not_require_evaluation(self):
        self.fixture.flows = [None] * 6
        self.fixture.write_flow()
        self.products = InputProducts(self.fixture.root, self.root / 'missing-flow-runtime')
        result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 2/24})
        self.assertEqual(result['issues'], [])
        self.assertTrue(result['reused'])

    def test_event_simulation_separates_driving_gaps_from_missing_observations(self):
        self.fixture.flows = [None] * 6
        self.fixture.write_flow()
        self.products = InputProducts(self.fixture.root, self.root / 'event-missing-flow-runtime')
        period = {'mode': 'event', 'warmupDays': 2/24,
                  'events': [{'id': 'USER-SIMULATION', 'start': '2018-01-01T00:00',
                              'end': '2018-01-01T03:00', 'purpose': '模拟'}]}
        result = self.prepare(period, operationPurpose='simulation')
        self.assertEqual(result['issues'], [])
        manifest = read_json(self.products.products_root / result['productVersionId'] / 'manifest.json')
        record = read_json(self.products._path(manifest['paths']['events']))['packages'][0]
        self.assertEqual(record['status'], 'ready')
        self.assertEqual(record['observationCoverage']['valid_hours'], 0)
        self.assertEqual(record['observedInputs']['riverCompleteness'], 0)
        period['events'][0]['end'] = '2018-01-01T05:00'
        missing = self.prepare(period, operationPurpose='simulation')
        self.assertEqual(missing['issues'][0]['missingIntervals'][0]['hours'], 2)
        self.assertNotIn('评价期实测流量不足', missing['issues'][0]['reasons'])

    def test_preparation_quality_is_not_reused_between_calibration_and_simulation(self):
        body = {'requestId': 'PREP-PURPOSE', 'operation': 'schemeInput', 'projectId': 'nananxi-xufan',
                'resolutionId': '1km', 'period': {'mode': 'event', 'warmupDays': 0,
                    'events': [{'id': 'xf-31180101', 'start': '2018-01-01T00:00',
                                'end': '2018-01-01T03:00', 'purpose': '率定'}]}}
        calibration_request = normalize_preparation({**body, 'operationPurpose': 'calibration'}, self.products)
        simulation_request = normalize_preparation({**body, 'operationPurpose': 'simulation'}, self.products)
        self.assertNotEqual(calibration_request['preparationKey'], simulation_request['preparationKey'])

    def test_fixed_simulation_does_not_apply_calibration_policy_to_saved_science(self):
        from science_context import ScienceVersions
        science = ScienceVersions(self.products.runtime_root).for_request({'runType': 'simulation', 'period': {'continuousOrigin': '2017-12-31T22:00'}})
        result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 2/24}, science={'versionId': science['scienceVersionId']}, evaporation={'mode': 'science-version'})
        self.assertEqual(result['issues'], [])

    def test_pet_gap_is_reported_with_rain_and_flow_gaps(self):
        from science_context import ScienceVersions
        science = ScienceVersions(self.products.runtime_root).for_request({'runType': 'simulation'})
        csv = self.root / 'hourly-pet.csv'
        import pandas as pd
        pd.DataFrame({'time': ['2018-01-01T00:00'], 'pet_mm_h': [0.1]}).to_csv(csv, index=False)
        limited = ScienceVersions(self.products.runtime_root).register(name='PET coverage fixture',
            state_config=science['paths']['state_config'], initial_land_state=science['paths']['initial_land_state'],
            pet_config={'mode': 'HOURLY_CSV', 'path': str(csv), 'production_eligible': False})
        result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T05:00', 'warmupDays': 1}, science={'versionId': limited['scienceVersionId']}, evaporation={'mode': 'science-version'})
        self.assertTrue(result['issues'][0]['missingIntervals'])
        self.assertEqual(result['issues'][0]['petMissingIntervals'][-1]['hours'], 5)

    def test_complete_multiyear_files_preserve_every_hour_across_blocks(self):
        import numpy as np
        import pandas as pd
        import xarray as xr
        from strict_forcing import load_hourly_forcing
        folder = self.root / 'multiyear-fixture'
        folder.mkdir()
        expected = pd.date_range('2023-12-31T22:00', '2025-01-01T02:00', freq='h')
        for year in (2023, 2024, 2025):
            block = expected[expected.year == year]
            value = expected.get_indexer(block).astype(float)
            data = xr.Dataset({'precipitation': (('time', 'lat', 'lon'), value[:, None, None]), 'source_station_count': ('time', np.ones(len(block)))}, coords={'time': block, 'lat': [30.], 'lon': [119.]})
            data.precipitation.attrs['units'] = 'mm'
            data.to_netcdf(folder / f'nananxi_precip_hourly_1km_{year}.nc', engine='h5netcdf')
            data.close()
        times, actual, _, audit = load_hourly_forcing({'precipitationDir': folder}, [{'array_row': 0, 'array_col': 0}], expected[0], expected[-1])
        self.assertTrue(times.equals(expected))
        self.assertEqual(audit['incomplete_forcing_hours'], 0)
        np.testing.assert_array_equal(actual[:, 0], np.arange(len(expected)))


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real isolated scheme acceptance')
class RealWorkflowTests(unittest.TestCase):
    def test_real_event_search_continuous_search_and_best_state_continuation(self):
        import numpy as np
        from server import TaskManager, InputTaskManager
        from scheme_inputs import reuse_preparation
        from task_diagnostics import TaskDiagnostics
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            manager = TaskManager(runtime / 'tasks')
            inputs = InputTaskManager(runtime / 'input-tasks', products=manager.products)
            def wait(queue, task):
                deadline = time.monotonic() + 360
                while time.monotonic() < deadline:
                    current = queue.get(task['taskId'])
                    if current['status'] not in {'queued', 'running', 'cancelling'}:
                        self.assertEqual(current['status'], 'completed', current.get('errorMessage'))
                        return current, queue.result(task['taskId'])
                    time.sleep(.2)
                self.fail('Isolated acceptance timed out')
            try:
                # Real event search and immutable fixed-parameter replay.
                event = calibration()
                event_task, event_result = wait(manager, manager.submit(event))
                self.assertEqual(event_result['optimizer']['evaluations'], 4)
                fixed = copy.deepcopy(event)
                fixed['requestId'] = 'TEST-FIXED'
                fixed['calibration'].update(evaluationMode='manual', simulationOnly=True, initialParameters=event_result['bestParameters'])
                fixed_task, fixed_result = wait(manager, manager.submit(fixed))
                self.assertEqual(fixed_task['runType'], 'eventSimulation')
                self.assertEqual(fixed_result['optimizer']['evaluations'], 1)
                for key in event_result['hydrographs']:
                    np.testing.assert_allclose([r['value'] for r in fixed_result['hydrographs'][key]['forecast']], [r['value'] for r in event_result['hydrographs'][key]['forecast']], rtol=1e-12)
                continuous = continuous_request(staged=False)
                continuous['requestId'] = 'REAL-CONTINUOUS'
                continuous['calibration'].update(statePolicy='continuous-origin', continuousOrigin='2017-06-18T00:00')
                continuous['inputs']['bundleVersionId'] = manager.products.resolve()['bundleVersionId']
                prep_body = {'operation': 'schemeInput', 'requestId': 'REAL-PREP', 'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'parentBundleVersionId': continuous['inputs']['bundleVersionId'], 'period': continuous['period'], 'statePolicy': 'continuous-origin', 'continuousOrigin': '2017-06-18T00:00'}
                prepared_task, prepared = wait(inputs, reuse_preparation(inputs, prep_body))
                self.assertEqual(prepared['issues'], [])
                scheme = manager.schemes.save({'name': '隔离连续验收', 'definition': input_definition(continuous), 'calculationDraft': continuous})
                continuous['scheme'] = {'id': scheme['id'], 'revision': 1}
                continuous['inputs'].update(sourceBundleVersionId=continuous['inputs']['bundleVersionId'], preparationTaskId=prepared_task['taskId'])
                task, result = wait(manager, manager.submit(continuous))
                self.assertEqual(result['optimizer']['evaluations'], 4)
                self.assertEqual(result['optimizer']['terminationReason'], 'BUDGET_EXHAUSTED')
                self.assertEqual(result['optimizationGroups'], ['CALIBRATION'])
                self.assertNotIn('stateCalculationError', result)
                child, history = wait(manager, manager.get(result['stateCalculationTaskId']))
                for period in result['periods']:
                    simulated = {r['time']: r['value'] for r in history['hydrograph']['forecast']}
                    np.testing.assert_allclose([simulated[r['time']] for r in result['hydrographs'][period['id']]['forecast']], [r['value'] for r in result['hydrographs'][period['id']]['forecast']], rtol=1e-10, atol=1e-10)
                self.assertTrue(TaskDiagnostics(manager).catalog(child['taskId'])['times'])
                from diagnostic_jobs import spatial_payload
                from xaj_routing.raster_formats import read_esri_ascii_grid, cell_center_xy
                from pyproj import Transformer
                spatial = spatial_payload(manager.products.resolve())
                cell = spatial['control']['snapped']['cell']
                grid = read_esri_ascii_grid(MODEL / 'data_interface/spatial/nan_an_dem_utm50_30m_clipped.asc')
                lon, lat = Transformer.from_crs('EPSG:32650', 'EPSG:4326', always_xy=True).transform(*cell_center_xy(grid, *cell))
                query = {'requestId': 'REAL-POINT', 'runType': 'pointQuery', 'sourceTaskId': child['taskId'], 'riverOnly': True, 'point': {'lon': lon, 'lat': lat, 'channelWidthM': 20}}
                point_task, point_result = wait(manager, manager.submit(query))
                self.assertEqual(point_result['pointQuery']['method'], 'PERSISTED_CONTROL_HYDROGRAPH')
                self.assertEqual(point_result['pointQuery']['series']['flow_cms'], [row['value'] for row in history['hydrograph']['forecast']])
                self.assertEqual(manager.submit({**query, 'requestId': 'REAL-POINT-REUSE'})['taskId'], point_task['taskId'])
                from diagnostic_jobs import make_point_engine
                engine = make_point_engine(manager.products.resolve(), runtime / 'point-location', manager.routing_versions.resolve()['parameters'])
                control_cells = {tuple(node['cell']) for node in engine.topology_result.control_nodes}
                reach = next(row for row in engine.topology_result.reaches if len(row.cells) > 10)
                middle = next(cell for cell in reach.cells[3:-3] if tuple(cell) not in control_cells)
                lon, lat = engine.inverse_transformer.transform(*cell_center_xy(engine.grid, *middle))
                query.update(requestId='REAL-MIDDLE-POINT', point={'lon': lon, 'lat': lat, 'channelWidthM': 20})
                _, middle_result = wait(manager, manager.submit(query))
                self.assertEqual(middle_result['pointQuery']['topology']['topology_action'], 'TEMPORARY_CONTROL_NODE_SPLIT')
                self.assertEqual(middle_result['pointQuery']['topology']['split_status'], 'RERUN_COMPLETED')
                self.assertTrue(np.isfinite(middle_result['pointQuery']['series']['flow_cms']).all())
                extension = copy.deepcopy(child['runConfig'])
                extension.update(requestId='REAL-CONTINUE', runType='stateContinuation', lineage={})
                extension['period'] = {'startTime': '2017-06-22T00:00', 'endTime': '2017-06-22T05:00', 'warmupDays': 1, 'initialStateVersionId': history['endStateVersionId']}
                _, continued = wait(manager, manager.submit(extension))
                whole = copy.deepcopy(child['runConfig'])
                whole.update(requestId='REAL-WHOLE', lineage={})
                whole['period']['endTime'] = '2017-06-22T05:00'
                _, full = wait(manager, manager.submit(whole))
                np.testing.assert_allclose([r['value'] for r in continued['hydrograph']['forecast']], [r['value'] for r in full['hydrograph']['forecast']][-6:], rtol=1e-10, atol=1e-10)
                # Cross-year numerical trajectory and checkpoint handoff.
                cross = copy.deepcopy(child['runConfig'])
                cross.update(requestId='REAL-CROSS-YEAR', lineage={})
                cross['period'].update(startTime='2017-12-31T00:00', endTime='2018-01-01T05:00', continuousOrigin='2017-12-30T00:00')
                _, cross_full = wait(manager, manager.submit(cross))
                first = copy.deepcopy(cross)
                first['requestId'] = 'REAL-CROSS-FIRST'
                first['period']['endTime'] = '2017-12-31T23:00'
                _, cross_first = wait(manager, manager.submit(first))
                tail = copy.deepcopy(cross)
                tail.update(requestId='REAL-CROSS-TAIL', runType='stateContinuation')
                tail['period'] = {'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T05:00', 'warmupDays': 1, 'initialStateVersionId': cross_first['endStateVersionId']}
                _, cross_tail = wait(manager, manager.submit(tail))
                np.testing.assert_allclose([r['value'] for r in cross_tail['hydrograph']['forecast']], [r['value'] for r in cross_full['hydrograph']['forecast']][-6:], rtol=1e-10, atol=1e-10)
            finally:
                inputs.close(); manager.close()


if __name__ == '__main__':
    unittest.main()
