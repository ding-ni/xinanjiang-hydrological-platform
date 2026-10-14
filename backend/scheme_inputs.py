"""Automatic, source-bound model input preparation for a scheme.

Registered gridded rain is reused byte-for-byte. Station processing is bounded
in annual blocks; observations never fall back to another source silently.
"""
from __future__ import annotations

import copy
import csv
import re
import sys
import uuid
from datetime import timedelta
from common import MODEL, now, read_json, write_json, clean
from input_products import hourly_time, manifest_digest


def local_observations_digest(products, start, end):
    import hashlib
    import json
    from hydro_database import HydroDatabase, hour
    digest = hashlib.sha256()
    with HydroDatabase(products.runtime_root).connect() as db:
        for row in db.execute('SELECT station_id,variable,time_hour,value,source_id FROM observations WHERE time_hour BETWEEN ? AND ? ORDER BY station_id,variable,time_hour,source_id', (hour(start), hour(end))):
            digest.update(json.dumps(list(row), separators=(',', ':')).encode())
        for row in db.execute('SELECT * FROM stations ORDER BY station_id'):
            digest.update(json.dumps(list(row), separators=(',', ':')).encode())
    return digest.hexdigest()


def normalize_preparation(payload, products):
    allowed = {'operation', 'requestId', 'name', 'projectId', 'resolutionId',
               'parentBundleVersionId', 'period', 'statePolicy', 'continuousOrigin',
               'rainfallMethod', 'idwPower', 'minimumStations', 'science', 'evaporation', 'stationSourceId'}
    if set(payload) - allowed:
        raise ValueError('自动资料准备字段无效')
    if payload.get('projectId') != 'nananxi-xufan' or payload.get('resolutionId') != '1km':
        raise ValueError('当前模型只接入南安溪徐畈1km产流网格')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,100}', str(payload.get('requestId'))):
        raise ValueError('资料准备请求编号无效')
    parent = products.resolve(payload.get('parentBundleVersionId'))
    period = copy.deepcopy(payload.get('period', {}))
    warmup = period.get('warmupDays', 7)
    if isinstance(warmup, bool) or not isinstance(warmup, (int, float)) or not 0 <= warmup <= 365:
        raise ValueError('预热天数须为0至365')
    if period.get('mode') == 'continuous':
        from contracts import continuous_windows
        windows = continuous_windows({k: v for k, v in period.items() if k != 'windows'})
    elif period.get('mode') == 'event':
        windows = [row for row in period.get('events', []) if row.get('purpose') in {'率定', '验证', '独立检验'} and row.get('status') != 'inactive']
        if not windows or len(windows) > 500:
            raise ValueError('请选择1至500场洪水')
        if len({row.get('id') for row in windows}) != len(windows):
            raise ValueError('洪水编号重复')
        for row in windows:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(row.get('id'))):
                raise ValueError('洪水编号须为字母、数字、下划线或短横线')
            if hourly_time(row['start']) >= hourly_time(row['end']):
                raise ValueError('洪水起止时段无效')
    else:
        if period.get('mode') not in {None, 'simulation'}:
            raise ValueError('计算方式无效')
        windows = [{'id': 'SIMULATION', 'start': period.get('startTime'), 'end': period.get('endTime'), 'purpose': '模拟'}]
    first = min(hourly_time(row['start']) for row in windows)
    end = max(hourly_time(row['end']) for row in windows)
    if end < first:
        raise ValueError('计算结束时刻不能早于起点')
    start = first - timedelta(hours=round(warmup * 24)) if not period.get('initialStateVersionId') else first
    policy = payload.get('statePolicy', 'independent-warmup')
    if policy not in {'independent-warmup', 'continuous-origin'}:
        raise ValueError('状态计算方式无效')
    if policy == 'continuous-origin' and not period.get('initialStateVersionId'):
        origin = payload.get('continuousOrigin') or start.isoformat()
        start = hourly_time(origin)
        if start > first:
            raise ValueError('连续起点不能晚于首个计算时刻')
    method = payload.get('rainfallMethod', 'reuse')
    if method not in {'reuse', 'stationIdw'}:
        raise ValueError('降雨处理只支持直接使用网格降雨或站点距离反比插值')
    power, minimum = payload.get('idwPower', 2), payload.get('minimumStations', 1)
    if isinstance(power, bool) or not isinstance(power, (int, float)) or not 1 <= power <= 4 or type(minimum) is not int or not 1 <= minimum <= 32:
        raise ValueError('插值幂次须为1至4，最少有效站须为1至32')
    definition = {'parentBundleVersionId': parent['bundleVersionId'], 'parentManifestSHA256': parent['manifestSHA256'],
                  'windows': windows, 'start': start.isoformat(), 'end': end.isoformat(),
                  'warmupHours': round(warmup * 24), 'statePolicy': policy, 'rainfallMethod': method,
                  'idwPower': float(power), 'minimumStations': minimum}
    if method == 'stationIdw':
        from station_network import station_network, observation_adapter
        selection = payload.get('stationSourceId')
        adapter = observation_adapter(products, parent, selection)
        if selection:
            if selection == 'LOCAL_OBSERVATIONS':
                adapter['manifest'].pop('sourceId', None)
            else:
                source = products.source(selection)
                adapter['manifest']['sourceId'] = selection
                definition['stationSourceManifestSHA256'] = source['manifestSHA256']
            definition['stationSourceId'] = selection
        definition['stationNetwork'] = station_network(products, adapter)
    from science_context import ScienceVersions
    science_request = {'runType': 'calibration' if period.get('mode') in {'event', 'continuous'} else 'simulation',
                       'period': period,
                       **({'calibration': {'statePolicy': policy}} if period.get('mode') in {'event', 'continuous'} else {}),
                       'science': payload.get('science', {}), 'inputs': {'evaporation': payload.get('evaporation', {})}}
    scientific = ScienceVersions(products.runtime_root).for_request(science_request)
    definition['science'] = {'versionId': scientific['scienceVersionId']}
    definition['petConfigurationSHA256'] = scientific['petConfigurationSHA256']
    if method == 'stationIdw' and (payload.get('stationSourceId') == 'LOCAL_OBSERVATIONS' or not payload.get('stationSourceId') and not parent['manifest'].get('sourceId')):
        definition['localObservationSHA256'] = local_observations_digest(products, start, end)
    return {**payload, **definition, 'name': str(payload.get('name') or '方案计算资料')[:200],
            'preparationKey': manifest_digest(definition), 'productionEligible': False}


def reuse_preparation(manager, request):
    with manager.lock:
        normalized = manager.products.normalize_processing_request(request)
        for task in manager.list():
            if task.get('status') in {'queued', 'running', 'completed', 'archived'} and task.get('runConfig', {}).get('preparationKey') == normalized['preparationKey']:
                # Resolve again rather than trusting a cached result's filenames.
                if task.get('registeredBundleVersionId'):
                    manager.products.resolve(task['registeredBundleVersionId'])
                return task
        return manager.submit(request)


def station_block(products, parent, start, end, folder):
    """Return rain and outlet flow from one declared observation source."""
    import numpy as np
    import pandas as pd
    manifest = parent['manifest']
    network = parent.get('_stationNetwork')
    if network is None:
        from station_network import station_network
        network = station_network(products, parent)
    ids = [row['id'] for row in network['stations']]
    coordinates = [(row['lon'], row['lat']) for row in network['stations']]
    outlet = network['flowStation']
    source_id = manifest.get('sourceId')
    if source_id and products.source(source_id)['manifest'].get('sourceType') == 'mdb':
        from mdb_import import query_mdb_copy, normalize_records
        source = products.source(source_id)
        snapshot = products._path(read_json(source['path'])['databaseFile'])
        lo, hi = start.strftime('%m/%d/%Y %H:%M:%S'), end.strftime('%m/%d/%Y %H:%M:%S')
        clause = f' BETWEEN #{lo}# AND #{hi}#'
        station_list = ','.join("'" + key + "'" for key in ids)
        rain = query_mdb_copy(snapshot, folder, 'SELECT HS_Code,YMDHM,DTRN FROM ReFactorDT WHERE HS_Code IN (' + station_list + ') AND YMDHM' + clause + ' ORDER BY YMDHM,HS_Code', timeout=120)
        flow = query_mdb_copy(snapshot, folder, "SELECT stcd,ymdhm,q FROM st_river_r WHERE stcd='" + outlet + "' AND ymdhm" + clause + ' ORDER BY ymdhm', timeout=120)
        rain, _ = normalize_records(rain, station_field='HS_Code', time_field='YMDHM', value_field='DTRN', allowed_stations=set(ids))
        flow, _ = normalize_records(flow, station_field='stcd', time_field='ymdhm', value_field='q', allowed_stations={outlet})
        times = pd.date_range(start, end, freq='h')
        values = np.array([[rain.get((key, time.to_pydatetime()), np.nan) for key in ids] for time in times], dtype=float)
        observed = [flow.get((outlet, time.to_pydatetime()), np.nan) for time in times]
        return times, ids, coordinates, values, observed, source_id, source_id
    if source_id and products.source(source_id)['manifest'].get('sourceType') != 'mdb':
        if outlet != 'PARENT_INPUT':
            raise ValueError('该雨量资料配用当前资料的实测流量，请重新保存计算站网')
        source = products.source(source_id)
        data = read_json(source['path'])
        times = pd.date_range(start, end, freq='h')
        indexed = pd.DatetimeIndex(data['times']).get_indexer(times)
        lookup = {row['id']: row for row in data['stations']}
        if any(key not in lookup for key in ids):
            raise ValueError('配置站网含该来源没有的雨量站')
        stations = [lookup[key] for key in ids]
        values = np.full((len(times), len(stations)), np.nan)
        found = indexed >= 0
        values[found] = np.array([row['values'] for row in stations], dtype=float).T[indexed[found]]
        observed = pd.read_csv(parent['paths']['flow']).assign(time=lambda df: pd.to_datetime(df.time)).set_index('time').flow_m3_s.reindex(times).to_numpy()
        return times, ids, coordinates, values, observed, source_id, parent['bundleVersionId']
    from hydro_database import HydroDatabase
    database = HydroDatabase(products.runtime_root)
    if not ids:
        raise ValueError('资料库没有具备坐标的雨量站，请先配置站网')
    times = pd.date_range(start, end, freq='h')
    def full_window(station, variable):
        with database.export_window(station, variable, start.isoformat(), end.isoformat()) as (summary, rows):
            return {**summary, 'series': list(rows)}
    rows = [full_window(key, 'rain') for key in ids]
    flow = full_window(outlet, 'flow')
    values = np.array([[record['value'] for record in row['series']] for row in rows], dtype=float).T
    return times, ids, coordinates, values, [row['value'] for row in flow['series']], 'LOCAL_OBSERVATIONS', 'LOCAL_OBSERVATIONS'


def build_prepared(folder, products):
    import numpy as np
    import pandas as pd
    import xarray as xr
    from pyproj import Transformer
    from strict_forcing import load_hourly_forcing, missing_intervals
    sys.path.insert(0, str(MODEL / 'code'))
    from runoff_platform.data.precipitation_idw import idw_interpolate_hourly
    from runoff_platform.data.event_quality import evaluate_event_quality
    from observation_quality import evaluate_observation_coverage
    from xaj_routing.control_area import contributing_grid_areas
    from xaj_routing.io import read_topology_json
    request = read_json(folder / 'request.json')
    original_fields = {'operation', 'requestId', 'name', 'projectId', 'resolutionId', 'parentBundleVersionId', 'period', 'statePolicy', 'continuousOrigin', 'rainfallMethod', 'idwPower', 'minimumStations', 'science', 'evaporation', 'stationSourceId'}
    if normalize_preparation({key: value for key, value in request.items() if key in original_fields}, products) != request:
        raise ValueError('资料准备请求已偏离冻结条件，请重新准备')
    parent = products.resolve(request['parentBundleVersionId'])
    parent['_stationNetwork'] = request.get('stationNetwork')
    if parent['manifestSHA256'] != request['parentManifestSHA256']:
        raise ValueError('资料来源已偏离准备时保存的摘要')
    if request.get('localObservationSHA256') and request['localObservationSHA256'] != local_observations_digest(products, request['start'], request['end']):
        raise ValueError('实测资料已更新，请重新准备方案资料')
    start, end = pd.Timestamp(request['start']), pd.Timestamp(request['end'])
    times = pd.date_range(start, end, freq='h')
    from science_context import ScienceVersions
    from runoff_platform.data.pet_forcing import PetProvider
    scientific = ScienceVersions(products.runtime_root).resolve(request['science']['versionId'])
    provider = PetProvider.from_file(scientific['paths']['pet_config'])
    pet_valid = provider.series.reindex(times).notna().to_numpy() if provider.mode == 'HOURLY_CSV' else np.isfinite(provider.values(times))
    with parent['paths']['grids'].open(encoding='utf-8-sig', newline='') as stream:
        grids = list(csv.DictReader(stream))
    areas = contributing_grid_areas(parent['paths']['mapping'], read_topology_json(parent['paths']['topology']), 'R0020_UP', [row['unit_id'] for row in grids])
    indexes = np.flatnonzero(areas > 0)
    quality = read_json(parent['paths']['quality'])
    flow = pd.read_csv(parent['paths']['flow']).assign(time=lambda df: pd.to_datetime(df.time)).set_index('time').flow_m3_s
    values = np.full((len(times), len(grids)), np.nan)
    counts = np.full(len(times), -1, dtype=np.int16)
    generated = []
    product_id = 'INPUT-' + uuid.uuid4().hex
    output = products.products_root / product_id
    method = request['rainfallMethod']
    if method == 'reuse':
        _, values, counts, _ = load_hourly_forcing(parent, grids, start, end, require_complete=False)
        rain_directory = parent['precipitationDir']
    else:
        output.mkdir(parents=True)
        rain_directory = output / 'precipitation'
        rain_directory.mkdir()
        template_path = next(parent['precipitationDir'].glob('*.nc'))
        with xr.open_dataset(template_path, engine='h5netcdf') as template:
            lat, lon = template.lat.values.copy(), template.lon.values.copy()
        row_ids = np.array([int(row['array_row']) for row in grids])
        col_ids = np.array([int(row['array_col']) for row in grids])
        transformer = Transformer.from_crs('EPSG:4326', 'EPSG:32650', always_xy=True)
        tx, ty = transformer.transform(lon[col_ids], lat[row_ids])
        chunks = []
        for year in range(start.year, end.year + 1):
            first, last = max(start, pd.Timestamp(year=year, month=1, day=1)), min(end, pd.Timestamp(year=year, month=12, day=31, hour=23))
            from station_network import observation_adapter
            adapter = observation_adapter(products, parent, request.get('stationSourceId'))
            block, ids, coordinates, rain, observed, source_id, flow_source = station_block(products, adapter, first, last, folder / 'queries')
            rain[(~np.isfinite(rain)) | (rain < 0)] = np.nan
            sx, sy = transformer.transform(*zip(*coordinates))
            interpolated, n = idw_interpolate_hourly(rain, np.asarray(sx), np.asarray(sy), np.asarray(tx), np.asarray(ty), power=request['idwPower'], minimum_stations=request['minimumStations'])
            loc = times.get_indexer(block)
            values[loc], counts[loc] = interpolated, n
            array = np.full((len(block), len(lat), len(lon)), np.nan, dtype=np.float32)
            array[:, row_ids, col_ids] = interpolated
            dataset = xr.Dataset({'precipitation': (('time', 'lat', 'lon'), array), 'source_station_count': (('time',), n)}, coords={'time': block, 'lat': lat, 'lon': lon})
            dataset.precipitation.attrs['units'] = 'mm'
            path = rain_directory / f'nananxi_precip_hourly_1km_{year}.nc'
            dataset.to_netcdf(path, engine='h5netcdf', encoding={'precipitation': {'zlib': True, 'dtype': 'float32'}})
            dataset.close()
            generated.append(path)
            chunks.extend(zip(block, observed))
            write_json(folder / 'progress.json', {'progress': 10 + round(50 * (year - start.year + 1) / (end.year - start.year + 1)), 'stageLabel': f'已自动处理{year}年站点降雨'})
        flow = pd.Series([value for _, value in chunks], index=pd.DatetimeIndex([time for time, _ in chunks]), name='flow_m3_s')
        flow_path = output / 'observed-flow.csv'
        flow.rename_axis('time').to_csv(flow_path)
        generated.append(flow_path)
    flow = pd.to_numeric(flow, errors='coerce').where(lambda series: np.isfinite(series) & (series >= 0))
    valid = np.isfinite(values).all(axis=1) & (counts >= request['minimumStations'])
    issues, packages = [], []
    original_catalog = read_json(parent['paths']['events'])
    original = {row['eventId']: row for row in original_catalog['packages']}
    event_mode = request['period'].get('mode') == 'event'
    windows = request['windows']
    for window in windows:
        first, last = pd.Timestamp(window['start']), pd.Timestamp(window['end'])
        audit_start = start if request['statePolicy'] == 'continuous-origin' else first - pd.Timedelta(hours=request['warmupHours'])
        if request['period'].get('initialStateVersionId'):
            audit_start = first
        mask = (times >= audit_start) & (times <= last)
        score_times = pd.date_range(first, last, freq='h')
        scored_flow = flow.reindex(score_times)
        missing = missing_intervals(times[mask], valid[mask])
        pet_missing = missing_intervals(times[mask], pet_valid[mask])
        reasons = []
        if missing:
            reasons.append('降雨或有效雨量站不足（含起算和预热）')
        if pet_missing:
            reasons.append('冻结蒸散发配置未覆盖连续驱动或预热时段')
        coverage = evaluate_observation_coverage(score_times, scored_flow.to_numpy(), quality)
        if window['purpose'] != '模拟' and (not scored_flow.notna().all() if not event_mode else not coverage['accepted']):
            reasons.append('评价期实测流量不足')
        event_quality = None
        if event_mode:
            event_quality = evaluate_event_quality(event_id=window['id'], start=first, end=last, times=times[mask], grid_precipitation_mm=values[mask][:, indexes], grid_areas_km2=areas[indexes], observed_flow_cms=flow, source_station_count=counts[mask], config=quality)
            if not event_quality['accepted']:
                reasons.extend(event_quality['exclusion_reasons'])
        if reasons:
            issues.append({'eventId': window['id'], 'purpose': window['purpose'], 'start': first.isoformat(), 'end': last.isoformat(), 'reasons': list(dict.fromkeys(reasons)), 'missingIntervals': missing, 'petMissingIntervals': pet_missing, 'flowMissingIntervals': missing_intervals(score_times, scored_flow.notna().to_numpy())})
        if event_mode:
            record = copy.deepcopy(original.get(window['id'], {}))
            record.update(eventId=window['id'], start=first.isoformat(), end=last.isoformat(), floodCode=record.get('floodCode', window['id']), status='review' if reasons else 'ready')
            record['observedInputs'] = {**record.get('observedInputs', {}), 'rainCompleteness': float(valid[(times >= first) & (times <= last)].mean()), 'riverCompleteness': float(scored_flow.notna().mean()), 'observedPeakFlowM3s': float(scored_flow.max()) if scored_flow.notna().any() else None, 'observedPeakTime': scored_flow.idxmax().isoformat() if scored_flow.notna().any() else None}
            record['quality'] = event_quality
            packages.append(record)
    # Identical registered windows and rain need no new scientific input identity.
    same_windows = event_mode and all(row['id'] in original and hourly_time(row['start']) == hourly_time(original[row['id']]['start']) and hourly_time(row['end']) == hourly_time(original[row['id']]['end']) for row in windows)
    if method == 'reuse' and (not event_mode or same_windows):
        return {'taskId': folder.name, 'preparedBundleVersionId': parent['bundleVersionId'], 'reused': True, 'issues': clean(issues), 'start': start.isoformat(), 'end': end.isoformat()}
    output.mkdir(parents=True, exist_ok=True)
    paths = copy.deepcopy(parent['manifest']['paths'])
    if method != 'reuse':
        paths['flow'] = products.reference(flow_path)
    if event_mode:
        event_path = output / 'event-catalog.json'
        write_json(event_path, {'schemaVersion': '1.1', 'count': len(packages), 'packages': packages})
        paths['events'] = products.reference(event_path)
        generated.append(event_path)
    files = [row for row in parent['files'] if not (row['root'], row['path']) in {(parent['manifest']['paths'][role]['root'], parent['manifest']['paths'][role]['path']) for role in ('events', 'flow') if paths[role] != parent['manifest']['paths'][role]}]
    files += [products.file_record(path) for path in generated]
    manifest = {**copy.deepcopy(parent['manifest']), 'bundleVersionId': product_id, 'modelInputVersion': product_id, 'name': request['name'], 'operation': 'schemeInput', 'createdAt': now(), 'sourceTaskId': folder.name, 'requestSHA256': manifest_digest(request), 'parentBundleVersionId': parent['bundleVersionId'], 'parentManifestSHA256': parent['manifestSHA256'], 'paths': paths, 'files': files, 'precipitationDirectory': products.reference(rain_directory), 'productionEligible': False,
                'forcingVersion': parent['forcingVersion'] if method == 'reuse' else 'scheme-rain-' + product_id,
                'observationVersion': parent['observationVersion'] if method == 'reuse' else 'scheme-flow-' + product_id,
                'forcingPolicy': parent['manifest'].get('forcingPolicy', 'STRICT_MISSING') if method == 'reuse' else 'STRICT_MISSING',
                'coverage': {'years': list(range(start.year, end.year + 1)), 'start': start.isoformat(), 'end': end.isoformat()}, 'recordQueryScope': {'start': start.isoformat(), 'end': end.isoformat()},
                'summary': {'eventCount': len(packages) if event_mode else original_catalog.get('count', len(original_catalog['packages'])), 'acceptedEventCount': sum(row['status'] == 'ready' for row in packages) if event_mode else None, 'gridCount': len(grids)}}
    manifest['processing'] = {'rainfallMethod': method, 'idwPower': request['idwPower'], 'minimumStations': request['minimumStations'], 'preparationKey': request['preparationKey']}
    if method == 'stationIdw':
        manifest['sourceLabel'] = '方案站点雨量自动插值'
        # The prepared rain must carry its own station source, not inherit the
        # parent grid's source when the user selected another observation archive.
        if source_id == 'LOCAL_OBSERVATIONS':
            manifest.pop('sourceId', None)
        else:
            manifest['sourceId'] = source_id
        manifest['sourceOrigin'] = {'sourceId': source_id, 'localObservationSHA256': request.get('localObservationSHA256'), 'method': 'HOURLY_VALID_STATION_IDW', 'flowSource': flow_source}
        if request.get('localObservationSHA256') != (local_observations_digest(products, start, end) if source_id == 'LOCAL_OBSERVATIONS' else None):
            raise ValueError('处理过程中实测资料已更新，请重新准备')
    path = output / 'manifest.json'
    write_json(path, clean(manifest))
    from input_products import file_sha256
    return {'taskId': folder.name, 'productVersionId': product_id, 'manifestSHA256': file_sha256(path), 'productionEligible': False, 'issues': clean(issues), 'start': start.isoformat(), 'end': end.isoformat(), 'reused': False}
