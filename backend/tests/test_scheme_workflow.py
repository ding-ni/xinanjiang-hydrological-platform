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
    def test_input_revision_and_calculation_draft_are_distinct_and_freeze_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ModelSchemes(HydroDatabase(directory))
            request = simulation()
            request['inputs']['bundleVersionId'] = 'source-A'
            body = {'name': '试验方案', 'definition': input_definition(request), 'calculationDraft': request}
            first = repository.save(body)
            request['parameters']['B'] = .4
            second = repository.save({**body, 'id': first['id'], 'revision': 1, 'calculationDraft': request})
            self.assertEqual(second['revision'], 1)
            request['period']['endTime'] = '2017-06-22T00:00'
            third = repository.save({**body, 'id': first['id'], 'revision': 1, 'definition': input_definition(request), 'calculationDraft': request})
            self.assertEqual(third['revision'], 2)
            self.assertNotEqual(repository.get(first['id'], 1)['definition'], third['definition'])
            with self.assertRaises(RuntimeError):
                repository.save({**body, 'id': first['id'], 'revision': 1, 'definition': input_definition(request)})
            request['scheme'] = {'id': first['id'], 'revision': 2}
            self.assertEqual(repository.freeze(request)['lineage']['schemeRevision'], 2)
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

    def test_cross_year_reuses_exact_grids_and_reports_warmup_gaps(self):
        original = self.fixture.hashes()
        result = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 2 / 24})
        self.assertEqual(result['issues'], [])
        self.assertTrue(result['reused'])
        self.assertEqual(self.fixture.hashes(), original)
        missing = self.prepare({'startTime': '2018-01-01T00:00', 'endTime': '2018-01-01T03:00', 'warmupDays': 1})
        self.assertEqual(missing['issues'][0]['missingIntervals'][0]['hours'], 22)

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
