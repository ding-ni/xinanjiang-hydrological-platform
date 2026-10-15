"""Historical observations and derived display rain, independent of input packages."""
from collections import OrderedDict
import copy
import threading

from common import clean, read_json
from hydro_database import timestamp
from input_products import manifest_digest, file_sha256
from input_service import InputService

DATABASE_ID = 'HISTORICAL_DATABASE'


class HistoricalInputs(InputService):
    def __init__(self, database, model_root=None):
        super().__init__(model_root=model_root)
        self.database = database
        self.bundle_version = DATABASE_ID
        self._cache = OrderedDict()
        self._cache_lock = threading.RLock()

    def catalog(self, project_id='nananxi-xufan', resolution_id='1km'):
        self._case(project_id, resolution_id)
        summary = self.database.summary()
        coverage = {row['variable']: row for row in summary['coverage']}
        rain, flow = coverage.get('rain', {}), coverage.get('flow', {})
        common = {'start': max(rain.get('start') or '', flow.get('start') or ''),
                  'end': min(rain.get('end') or '', flow.get('end') or '')}
        sites = self.database.stations()
        stations = [{**row, 'spatialCode': row['id'], 'legacyCode': row['id'],
                     'start': timestamp(row['startHour']), 'end': timestamp(row['endHour'])}
                    for row in sites if row['variable'] == 'rain']
        events = [{**row, 'id': row['eventId'], 'sourceFloodNo': row.get('floodCode'),
                   'purpose': '未使用', 'status': 'active', 'source': DATABASE_ID,
                   'bundleVersionId': DATABASE_ID, 'sectionId': '70115800',
                   'observedPeak': row.get('observedInputs', {}).get('observedPeakFlowM3s')}
                  for row in self.database.events()]
        latest = None
        if common['start'] and common['end'] and common['start'] <= common['end']:
            from datetime import datetime, timedelta
            end = datetime.fromisoformat(common['end'])
            start = max(datetime.fromisoformat(common['start']), end - timedelta(hours=71))
            latest = {'start': start.isoformat(), 'end': end.isoformat()}
        return {'schemaVersion': '2.0', 'caseKey': 'nananxi-xufan:1km', 'sourceType': 'database-observed',
                'bundleVersionId': DATABASE_ID, 'rain': {**rain, 'stations': stations}, 'river': flow,
                'events': events, 'coverage': {'precipitation': rain, 'observedFlow': flow, 'common': common,
                'coverageMeaning': 'BOUNDING_DATES_NOT_A_COMPLETENESS_GUARANTEE'}, 'latestPeriod': latest,
                'hasStationRainfall': True, 'hasWaterLevel': True,
                'qualityNotes': ['历史库原始实测记录；网格降雨由站点资料派生，未自动认定为已通过QC。']}

    def process(self, start=None, end=None, event_id=None, project_id='nananxi-xufan', resolution_id='1km'):
        self._case(project_id, resolution_id)
        if event_id:
            if start or end:
                raise ValueError('场次与自定义时段不能同时指定')
            event = next((row for row in self.database.events() if row['eventId'] == event_id), None)
            if event is None:
                raise KeyError('公共洪水目录没有该场次')
            start, end = event['start'], event['end']
        elif not start and not end:
            latest = self.catalog()['latestPeriod']
            if not latest:
                raise ValueError('历史库没有共同覆盖时段，请指定查询时间')
            start, end = latest['start'], latest['end']
        if not start or not end:
            raise ValueError('请同时指定起止时间')
        first, last = self._time(start), self._time(end)
        if not 1 <= (last - first).total_seconds() / 3600 + 1 <= 8784:
            raise ValueError('过程查询限1至8784小时，请分段查询')
        with self._cache_lock:
            return self._process(first, last, event_id)

    def _process(self, first, last, event_id):
        import numpy as np
        import pandas as pd
        from scheme_inputs import local_observations_digest
        from station_network import local_station_network
        sites = self.database.stations()
        settings = self._settings()
        try:
            network = local_station_network(self.database)
            grid_error = None
        except ValueError as error:
            network, grid_error = None, str(error)
        digest = local_observations_digest(type('Workspace', (), {'runtime_root': self.database.runtime})(), first, last)
        # Spatial and interpolation changes invalidate only the derived process.
        spatial_files = {role: self._file(self.paths[role]) if self.paths[role].is_file() else None
                         for role in ('grids', 'geometry', 'mapping', 'topology')}
        from common import MODEL
        implementation = manifest_digest([file_sha256(__file__), file_sha256(MODEL / 'code/runoff_platform/data/precipitation_idw.py')])
        key = manifest_digest([first.isoformat(), last.isoformat(), digest, network, settings, spatial_files, implementation])
        if key in self._cache:
            result = copy.deepcopy(self._cache[key])
            result['cacheHit'] = True
            result['eventId'] = event_id
            return result
        times = pd.date_range(first, last, freq='h')
        raw = {}
        for site in sites:
            with self.database.export_window(site['id'], site['variable'], first.isoformat(), last.isoformat()) as (_, rows):
                raw[site['id'], site['variable']] = [row['value'] for row in rows]
        try:
            if getattr(self, '_spatial_fingerprint', None) != spatial_files:
                self._spatial_initialized = False
            self._initialize_spatial()
            self._spatial_fingerprint = spatial_files
            grids, cells, indexes, areas, control_area = self.grids, self.cells, self.control_indexes, self.areas, self.control_area
        except (OSError, ValueError, KeyError, ImportError) as error:
            grid_error = str(error)
            grids, cells, indexes, areas, control_area = [], [], np.array([], dtype=int), np.array([]), 0
        rain = {site['id']: raw[site['id'], 'rain'] for site in sites if site['variable'] == 'rain'}
        flows = raw.get(('70115800', 'flow'), [None] * len(times))
        levels = raw.get(('70115800', 'level'), [None] * len(times))
        values = np.full((len(times), len(grids)), np.nan)
        counts = np.full(len(times), 0, dtype=int)
        if network and grids:
            try:
                from pyproj import Transformer
                from runoff_platform.data.precipitation_idw import idw_interpolate_hourly
                ids = [row['id'] for row in network['stations']]
                station_values = np.array([rain.get(code, [None] * len(times)) for code in ids], dtype=float).T
                station_values[(~np.isfinite(station_values)) | (station_values < 0)] = np.nan
                transformer = Transformer.from_crs('EPSG:4326', 'EPSG:32650', always_xy=True)
                sx, sy = transformer.transform(*zip(*[(row['lon'], row['lat']) for row in network['stations']]))
                # The spatial grid centres are independent of any rainfall file.
                lat, lon = grid_axes(self.grids, self.cells)
                tx, ty = transformer.transform([lon[int(row['array_col'])] for row in self.grids],
                                               [lat[int(row['array_row'])] for row in self.grids])
                values, counts = idw_interpolate_hourly(station_values, np.asarray(sx), np.asarray(sy),
                    np.asarray(tx), np.asarray(ty), power=settings['idwPower'], minimum_stations=settings['minimumStations'])
            except (ValueError, KeyError, ImportError, OSError, RuntimeError) as error:
                grid_error = str(error)
        complete = np.isfinite(values[:, indexes]).all(axis=1) if len(indexes) else np.zeros(len(times), dtype=bool)
        areal = np.full(len(times), np.nan)
        if complete.any():
            areal[complete] = np.average(values[complete][:, indexes], axis=1, weights=areas[indexes])
        version = 'DATABASE-' + key
        result = clean({'schemaVersion': '2.0', 'caseKey': 'nananxi-xufan:1km', 'sourceType': 'database-observed',
            'bundleVersionId': DATABASE_ID, 'forcingVersion': version, 'observationVersion': 'DATABASE-' + digest,
            'eventId': event_id, 'cacheHit': False,
            'product': {'sourceType': 'database-derived-grid', 'resolution': '1km', 'times': list(times),
                        'start': times[0], 'end': times[-1], 'stepHours': 1,
                        'cells': [{**cell, 'values': values[:, index]} for index, cell in enumerate(cells)]},
            'observationPreview': {'sourceType': 'database-observed', 'times': list(times), 'rain': rain,
                                  'river': {'spatialCode': '70115800', 'flows': flows, 'levels': levels}},
            'summary': {'arealRainfallMm': areal, 'reportingStationCount': counts, 'windowHours': len(times),
                        'validGridFraction': np.isfinite(values).mean(axis=1) if grids else np.zeros(len(times)), 'controlAreaKm2': control_area,
                        'gridCount': len(grids), 'rainfallCompleteness': float(complete.mean())},
            'quality': {'status': 'raw-observations', 'gridStatus': 'failed' if grid_error else 'ready' if complete.all() else 'incomplete',
                        'gridError': grid_error, 'warningReasons': [] if complete.all() else ['DERIVED_RAIN_INCOMPLETE']},
            'provenance': {'sourceType': 'database-observed', 'databaseId': DATABASE_ID, 'observationSHA256': digest,
                           'stationNetwork': network, 'processing': settings, 'spatialFiles': spatial_files, 'implementationSHA256': implementation,
                           'notes': ['历史实测与派生网格分别保存；查看资料不代表通过率定质量检查。']}})
        if digest != local_observations_digest(type('Workspace', (), {'runtime_root': self.database.runtime})(), first, last):
            raise RuntimeError('查询期间实测资料已更新，请重新查询')
        self._cache[key] = copy.deepcopy(result)
        while len(self._cache) > 4:
            self._cache.popitem(last=False)
        return result

    def _settings(self):
        with self.database.connect() as db:
            row = db.execute("SELECT payload FROM preparation_defaults WHERE id=1").fetchone()
        import json
        defaults = json.loads(row[0]) if row else {}
        return {key: defaults.get(key, value) for key, value in [('idwPower', 2), ('minimumStations', 1)]}


def grid_axes(grids, cells):
    """Recover static cell centres; no annual forcing file is required."""
    import numpy as np
    lat = np.full(max(int(row['array_row']) for row in grids) + 1, np.nan)
    lon = np.full(max(int(row['array_col']) for row in grids) + 1, np.nan)
    for grid, cell in zip(grids, cells):
        ring = np.asarray(cell['bounds'][:-1], dtype=float)
        lat[int(grid['array_row'])] = (ring[:, 1].min() + ring[:, 1].max()) / 2
        lon[int(grid['array_col'])] = (ring[:, 0].min() + ring[:, 0].max()) / 2
    # Fill unused rows/columns of the regular grid by linear coordinates.
    for axis in (lat, lon):
        known = np.flatnonzero(np.isfinite(axis))
        if len(known) > 1:
            step = (axis[known[-1]] - axis[known[0]]) / (known[-1] - known[0])
            axis[:] = axis[known[0]] + (np.arange(len(axis)) - known[0]) * step
    return lat, lon
