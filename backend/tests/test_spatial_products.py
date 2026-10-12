"""Real original DEM/D8 products and successful-task-only download contracts."""
import copy
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from common import MODEL, read_json, write_json
from input_products import InputProducts
from server import TaskManager, ThreadingHTTPServer, handler
from spatial_products import (FILES, SOURCE_FILES, normalize_spatial, generate, artifact_bytes,
                              make_point_engine, unit_raster)
from xaj_routing.raster_formats import read_esri_ascii_grid, cell_center_xy


class SpatialProductsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        with patch('server.ThreadPoolExecutor'):
            cls.manager = TaskManager(cls.root / 'tasks')
        cls.products = cls.manager.products
        cls.task = cls.manager.submit({'runType': 'spatialExport', 'requestId': 'spatial-test'})
        cls.folder = cls.root / 'tasks' / cls.task['taskId']
        cls.request = read_json(cls.folder / 'request.json')
        cls.result = generate(cls.folder, cls.request, cls.products)
        cls.manager.update(cls.task['taskId'], status='completed', resultVersionId=cls.result['resultVersionId'])
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(cls.manager, cls.root))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}/api/model/tasks/{cls.task["taskId"]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        cls.manager.close()
        cls.temp.cleanup()

    def test_dem_is_byte_identical_and_all_rasters_share_geometry(self):
        output = self.folder / 'spatial'
        self.assertEqual((output / 'dem.asc').read_bytes(), (MODEL / SOURCE_FILES['dem.asc']).read_bytes())
        dem = read_esri_ascii_grid(output / 'dem.asc')
        self.assertEqual((dem.nrows, dem.ncols, dem.cellsize), (474, 421, 30))
        for name in ('routing-units', 'flow-direction', 'accumulation-area', 'stream-mask'):
            grid = read_esri_ascii_grid(output / f'{name}.asc')
            for field in ('nrows', 'ncols', 'cellsize', 'xllcorner', 'yllcorner', 'nodata'):
                self.assertEqual(getattr(grid, field), getattr(dem, field))
            np.testing.assert_array_equal(grid.elevation == -9999, dem.elevation == -9999)
        from pyproj import CRS
        self.assertEqual(CRS.from_wkt((output / 'dem.prj').read_text()).to_epsg(), 32650)

    def test_units_and_river_paths_match_original_algorithm_exactly(self):
        bundle = self.products.resolve()
        engine = make_point_engine(bundle, self.root, {})
        labels, rows = unit_raster(engine, read_json(bundle['paths']['topology']))
        output = self.folder / 'spatial'
        np.testing.assert_array_equal(labels, read_esri_ascii_grid(output / 'routing-units.asc').elevation)
        for name, native in [('flow-direction', engine.topology_result.flow_dir),
                             ('accumulation-area', engine.topology_result.accumulation_area_km2),
                             ('stream-mask', engine.topology_result.stream_raster.astype(int))]:
            np.testing.assert_array_equal(read_esri_ascii_grid(output / f'{name}.asc').elevation,
                                          np.where(engine.mask, native, -9999))
        self.assertEqual(len(rows), 32)
        self.assertEqual(int((labels > 0).sum()), 79500)
        self.assertEqual(int((labels == 0).sum()), 130)
        features = read_json(output / 'reaches.geojson')['features']
        self.assertEqual(len(features), 32)
        for feature in features:
            reach = engine.reach_by_id[feature['properties']['reach_id']]
            expected = [list(engine.inverse_transformer.transform(*cell_center_xy(engine.grid, *cell))) for cell in reach.cells]
            self.assertEqual(feature['geometry']['coordinates'], expected)
        bad = read_json(bundle['paths']['topology'])
        bad['units'][0]['cell_count'] += 1
        with self.assertRaisesRegex(ValueError, '面积'):
            unit_raster(engine, bad)
        bad = read_json(bundle['paths']['topology'])
        bad['reaches'][0]['end_cell'][0] += 1
        with self.assertRaisesRegex(ValueError, '拓扑'):
            unit_raster(engine, bad)

    def test_package_content_relative_names_and_hashes(self):
        data = artifact_bytes(self.folder, self.result)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), FILES | {'manifest.json'})
            manifest = json.loads(archive.read('manifest.json'))
            for row in manifest['files']:
                self.assertNotIn('..', row['name'])
                self.assertEqual(hashlib.sha256(archive.read(row['name'])).hexdigest(), row['sha256'])
                self.assertEqual(len(archive.read(row['name'])), row['size'])
            self.assertEqual(manifest['summary']['unassignedCells'], 130)
            self.assertEqual(manifest['sourceEvidence'], self.request['spatialEvidence'])

    def test_previews_are_real_png_and_only_explicit_names_allowed(self):
        for name in ('terrain-network.png', 'routing-units.png'):
            data = artifact_bytes(self.folder, self.result, name)
            image = Image.open(io.BytesIO(data))
            self.assertEqual(image.format, 'PNG')
            self.assertGreater(image.width, 1500)
            self.assertGreater(len(image.convert('RGB').getcolors(image.width * image.height)), 1000)
        for name in ('../request.json', 'manifest.json', 'dem.asc', '/terrain-network.png'):
            with self.assertRaises(ValueError):
                artifact_bytes(self.folder, self.result, name)

    def test_tampered_file_and_manifest_are_rejected(self):
        path = self.folder / 'spatial' / 'terrain-network.png'
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'tampered')
            with self.assertRaisesRegex(ValueError, '校验'):
                artifact_bytes(self.folder, self.result, path.name)
            with self.assertRaisesRegex(ValueError, '校验'):
                artifact_bytes(self.folder, self.result)
        finally:
            path.write_bytes(original)
        bad = copy.deepcopy(self.result)
        bad['spatial']['manifestSHA256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, '校验'):
            artifact_bytes(self.folder, bad)

    def test_request_freezes_sources_and_clone_is_traceable(self):
        frozen = normalize_spatial(self.request, self.products)
        self.assertEqual(frozen, self.request)
        for changes in ({'path': 'C:/secret'}, {'inputs': {'path': 'C:/secret'}},
                        {'context': {}}, {'spatialEvidence': {}}, {'inputs': {'bundleManifestSHA256': '0' * 64}}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, KeyError)):
                normalize_spatial({**self.request, **changes}, self.products)
        clone = self.manager.clone(self.task['taskId'])
        self.assertEqual(clone['runConfig']['spatialEvidence'], self.request['spatialEvidence'])
        self.assertEqual(clone['lineage']['sourceTaskId'], self.task['taskId'])
        self.assertEqual(self.manager.submit({'runType': 'spatialExport', 'requestId': 'spatial-test'})['taskId'], self.task['taskId'])

    def test_http_success_archived_and_unfinished_task_gates(self):
        for status in ('completed', 'archived'):
            self.manager.update(self.task['taskId'], status=status)
            for route, mime in (('/spatial-package', 'application/zip'), ('/spatial-preview?name=terrain-network.png', 'image/png')):
                with urllib.request.urlopen(self.url + route) as response:
                    self.assertEqual(response.headers['Content-Type'], mime)
                    self.assertEqual(len(response.read()), int(response.headers['Content-Length']))
        try:
            for status in ('cancelled', 'failed', 'running', 'queued'):
                self.manager.update(self.task['taskId'], status=status)
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(self.url + '/spatial-package')
                self.assertEqual(failure.exception.code, 409)
        finally:
            self.manager.update(self.task['taskId'], status='completed')
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(self.url + '/spatial-preview?name=../request.json')
        self.assertEqual(failure.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
