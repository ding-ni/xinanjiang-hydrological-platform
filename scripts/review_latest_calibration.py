"""Read-only diagnostics of the latest completed calibration and its gauges.

No TaskManager, preparation, simulation or data correction is invoked. SQLite
is opened with mode=ro; only the requested diagnostic JSON is generated.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys

import numpy as np
from pyproj import Transformer
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'backend/vendor/model'
sys.path.insert(0, str(MODEL / 'code'))
from xaj_routing.control_area import contributing_grid_areas
from xaj_routing.io import read_topology_json


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
        timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def epoch_hour(value):
    return int((datetime.fromisoformat(value) - datetime(1970, 1, 1)).total_seconds() // 3600)


def station_values(db, stations, start, end, project):
    first, last = epoch_hour(start), epoch_hour(end)
    values = np.full((last - first + 1, len(stations)), np.nan)
    for index, station in enumerate(stations):
        rows = db.execute('''SELECT time_hour, value FROM observations
            WHERE project_id=? AND station_id=? AND variable='rain'
            AND time_hour BETWEEN ? AND ? ORDER BY time_hour''',
            (project, station, first, last)).fetchall()
        seen = set()
        for hour, value in rows:
            if hour in seen:
                raise ValueError(f'Multiple observation sources: {station} {hour}')
            seen.add(hour)
            values[hour-first, index] = np.nan if value is None or value < 0 else value
    return values


def idw_weights(distances, power):
    exact = distances <= 1e-9
    weights = 1 / np.maximum(distances, 1e-9)**power
    weights[exact.any(axis=1)] = exact[exact.any(axis=1)]
    return weights / weights.sum(axis=1, keepdims=True)


def interpolate(rain, distances, area_weights, power=2):
    output = np.full(len(rain), np.nan)
    contribution = np.zeros(rain.shape[1])
    patterns, inverse = np.unique(np.isfinite(rain), axis=0, return_inverse=True)
    for pattern_index, available in enumerate(patterns):
        indices = np.flatnonzero(inverse == pattern_index)
        if not available.any():
            continue
        weights = area_weights @ idw_weights(distances[:, available], power)
        output[indices] = rain[indices][:, available] @ weights
        contribution[available] += weights * len(indices)
    return output, contribution / len(rain)


def longest_true(values):
    longest = current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT/'runtime-20261012')
    parser.add_argument('--operation-id', help='Pin a completed calibration instead of choosing the latest')
    parser.add_argument('--output', type=Path, default=ROOT/'开发产物/收尾复核20261018/latest-review.json')
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    output_path = args.output.resolve()
    if output_path == runtime or runtime in output_path.parents:
        raise ValueError('Diagnostic output must be outside the source runtime')
    candidates = [read(path) for path in (runtime/'operations').glob('*/status.json')]
    completed = [row for row in candidates if row.get('status') == 'completed'
                 and row.get('operation') == 'calibration'
                 and (not args.operation_id or row['taskId'] == args.operation_id)]
    if not completed:
        raise ValueError('No matching completed calibration in the requested runtime')
    latest = max(completed, key=lambda row: row['createdAt'])
    identifier = latest['taskId']
    result_path = runtime/'operations'/identifier/'result.json'
    result = read(result_path)
    result_digest = digest(result_path)
    request = result['frozenRequest']
    if request['period']['mode'] != 'event' or not result.get('eventStates'):
        raise ValueError('This diagnostic requires an event calibration with saved grid histories')
    project = request['context']['projectId']
    power = request['inputs'].get('idwPower', 2)
    if request['inputs'].get('stationSourceId') != 'LOCAL_OBSERVATIONS':
        raise ValueError('This diagnostic supports the local historical database only')
    network = request['inputs']['stationNetwork']['stations']
    stations = [row['id'] for row in network]
    first_state = result['eventStates'][0]
    metadata = read(runtime/'tasks'/first_state['taskId']/'grid-history.json')
    manifest = read(runtime/'inputs/products'/request['inputs']['bundleVersionId']/'manifest.json')
    def asset(name):
        row = manifest['paths'][name]
        return (MODEL if row['root'] == 'model' else runtime) / row['path']
    import csv
    with asset('grids').open(encoding='utf-8-sig') as stream:
        grids = list(csv.DictReader(stream))
    grid_ids = [row['unit_id'] for row in grids]
    assert grid_ids == metadata['gridIds']
    areas = contributing_grid_areas(asset('mapping'), read_topology_json(asset('topology')), 'R0020_UP', grid_ids)
    area = float(areas.sum())
    transform = Transformer.from_crs('EPSG:4326', 'EPSG:32650', always_xy=True)
    sx, sy = transform.transform([row['lon'] for row in network], [row['lat'] for row in network])
    directory = manifest['precipitationDirectory']
    with xr.open_dataset(next((runtime/directory['path']).glob('*.nc'))) as template:
        tx, ty = transform.transform(template.lon.values[[int(row['array_col']) for row in grids]],
                                     template.lat.values[[int(row['array_row']) for row in grids]])
    distances = np.hypot(np.subtract.outer(tx, sx), np.subtract.outer(ty, sy))
    db_path = runtime/'database/hydrology.sqlite3'
    events = []
    with sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        names = dict(db.execute('SELECT station_id,name FROM stations WHERE project_id=?', (project,)))
        annual = []
        zero_only_station_years = set()
        years_covered = {year for state in result['eventStates']
                         for year in range(int(state['start'][:4]), int(state['end'][:4])+1)}
        for year in sorted(years_covered):
            rain = station_values(db, stations, f'{year}-01-01T00:00',
                                  f'{year}-12-31T23:00', project)
            for index, station in enumerate(stations):
                measured = rain[:, index]
                valid = np.isfinite(measured)
                complete_zero = bool(valid.all() and np.all(measured == 0))
                if complete_zero:
                    zero_only_station_years.add((year, station))
                annual.append({'year': year, 'id': station, 'name': names.get(station, station),
                               'validHours': int(valid.sum()), 'expectedHours': len(measured),
                               'wetHours': int((measured > 0).sum()),
                               'totalMm': float(np.nansum(measured)) if valid.any() else None,
                               'completeYearAllZero': complete_zero})
        for state in result['eventStates']:
            event_id = state['eventId']
            rain = station_values(db, stations, state['start'], state['end'], project)
            basin_rain, weights = interpolate(rain, distances, areas/area, power)
            if not np.isfinite(basin_rain).all():
                raise ValueError(f'No rainfall source for one or more hours: {event_id}')
            graph = result['hydrographs'][event_id]
            obs = np.array([np.nan if row['value'] is None else row['value'] for row in graph['obs']])
            sim = np.array([row['value'] for row in graph['forecast']])
            if [row['time'] for row in graph['obs']] != [row['time'] for row in graph['forecast']]:
                raise ValueError(f'Observation and simulation times differ: {event_id}')
            paired = np.isfinite(obs) & np.isfinite(sim)
            full_volume = bool(paired.all())
            stored_rain = np.asarray(graph['forcing']['precipitation'])
            assert len(obs) == len(rain) == len(stored_rain)
            metric = next(row for row in result['eventMetrics'] if row['eventId'] == event_id)
            event = {'eventId':event_id, 'start':state['start'], 'end':state['end'],
                     'group':metric['group'], 'NSE':metric['NSE'], 'KGE':metric['KGE'],
                     'volumeBiasPercent':metric['volume_error_percent'],
                     'peakBiasPercent':metric['peak_error_percent'],
                     'peakTimingHours':metric['peak_time_error_hours'],
                     'pairedHours':int(paired.sum()), 'windowHours':len(obs),
                     'volumeScope':metric['volume_scope'],
                     'rainMm':float(basin_rain.sum()),
                     'observedRunoffMm':float(obs.sum()*3.6/area) if full_volume else None,
                     'simulatedRunoffMm':float(sim.sum()*3.6/area),
                     'pairedObservedRunoffMm':float(obs[paired].sum()*3.6/area),
                     'pairedSimulatedRunoffMm':float(sim[paired].sum()*3.6/area),
                     'startingFlowCms':{'observed':float(obs[0]),'simulated':float(sim[0])},
                     'maximumRainReproductionErrorMmH':float(np.max(np.abs(basin_rain-stored_rain))),
                     'stationChecks':[]}
            event['QoverP'] = event['observedRunoffMm']/event['rainMm'] if full_volume else None
            event['simQoverP'] = event['simulatedRunoffMm']/event['rainMm']
            paired_obs, paired_sim = obs[paired], sim[paired]
            nse = 1-float(np.sum((paired_sim-paired_obs)**2)/np.sum((paired_obs-paired_obs.mean())**2))
            bias = float((paired_sim.sum()/paired_obs.sum()-1)*100)
            event['metricReproductionErrors'] = {'NSE':abs(nse-metric['NSE']),
                                                 'volumeBiasPercent':abs(bias-metric['volume_error_percent'])}
            station_totals = np.where(np.isfinite(rain).any(axis=0), np.nansum(rain,axis=0), np.nan)
            network_median = np.nanmedian(station_totals)
            for index, station in enumerate(stations):
                measured = rain[:, index]
                others = np.arange(len(stations)) != index
                neighbour, _ = interpolate(rain[:, others], distances[:, others], areas/area, power)
                finite = np.isfinite(measured)
                zero_with_wet_peers = finite & (measured == 0) & (neighbour >= 1)
                record = {'id':station,'name':names.get(station,station), 'hours':len(measured),
                          'validHours':int(finite.sum()),'wetHours':int((measured>0).sum()),
                          'totalMm':float(np.nansum(measured)) if finite.any() else None,
                          'meanAreaIDWWeight':float(weights[index]),
                          'zeroWhileOtherStationsBasinRainAbove1mmH':int(zero_with_wet_peers.sum()),
                          'longestZeroSequenceHours':longest_true(finite & (measured == 0))}
                if finite.all() and measured.sum() < .15*network_median:
                    excluded = rain.copy(); excluded[:,index] = np.nan
                    sensitivity, _ = interpolate(excluded, distances, areas/area, power)
                    record['flag'] = 'COMPLETE_BUT_LOW_RELATIVE_TO_NETWORK_REQUIRES_SOURCE_QC'
                    record['excludeOnlySensitivityRainMm'] = float(sensitivity.sum())
                event['stationChecks'].append(record)
            # A diagnostic only: two prespecified suspect gauges, unchanged params/data.
            excluded = rain.copy()
            excluded[:,[i for i,key in enumerate(stations) if key in {'7011AJ2C','7011AJ99'}]] = np.nan
            sensitivity, _ = interpolate(excluded, distances, areas/area, power)
            event['excludeTwoSuspectGaugeSensitivityRainMm'] = float(sensitivity.sum())
            excluded = rain.copy()
            excluded_stations = []
            years = np.array([int(row['time'][:4]) for row in graph['obs']])
            for index, station in enumerate(stations):
                for year in set(years):
                    if (year, station) in zero_only_station_years:
                        excluded[years == year, index] = np.nan
                        excluded_stations.append(f'{station}:{year}')
            sensitivity, _ = interpolate(excluded, distances, areas/area, power)
            event['excludeFullYearZeroSensitivity'] = {'stations':excluded_stations,
                                                      'rainMm':float(sensitivity.sum())}
            folder = runtime/'tasks'/state['taskId']
            event_metadata = read(folder/'grid-history.json')
            task_result = read(folder/'result.json')
            assert digest(folder/'grid-history.npz') == task_result['gridHistory']['arraySHA256']
            assert digest(folder/'grid-history.json') == task_result['gridHistory']['catalogSHA256']
            with np.load(folder/'grid-history.npz',allow_pickle=False) as history:
                assert history['precipitation'].shape == (len(obs),len(areas))
                event['generatedRunoffMm']={key:float((history[key] @ (areas/area)).sum()) for key in ['RS','RI','RG']}
                event['actualETmm']=float((history['et_actual'] @ (areas/area)).sum())
                saturation=(history['WU']+history['WL']+history['WD'])/event_metadata['parameters']['WM']
                event['soilSaturationFirstLast']=[float(saturation[index] @ (areas/area)) for index in [0,-1]]
                event['maximumInternalBalanceErrorMm']=float(np.abs(history['water_balance_error']).max())
            events.append(event)
    rng=random.Random(20261018)
    selected=[rng.choice([row['eventId'] for row in events if row['group']==group])
              for group in ['CALIBRATION','VALIDATION','INDEPENDENT_TEST']]
    selected += [min(events,key=lambda row:row['volumeBiasPercent'])['eventId'],
                 max(events,key=lambda row:row['volumeBiasPercent'])['eventId'],
                 max(events,key=lambda row:row['NSE'])['eventId']]
    manual=[]
    for status in candidates:
        if status.get('operation') != 'manualTrial' or status.get('status') != 'completed':continue
        child=read(runtime/'operations'/status['taskId']/'result.json')
        child_request = child['frozenRequest']
        child_scheme = ((child_request.get('scheme') or {}).get('id')
                        or child_request.get('lineage',{}).get('schemeId'))
        if child_scheme != (request.get('scheme') or {}).get('id'):continue
        changes={key:{'automatic':result['bestParameters'][key],'manual':value}
                 for key,value in child['bestParameters'].items() if value != result['bestParameters'].get(key)}
        manual.append({'id':status['taskId'],'createdBeijing':local_time(status['createdAt']),
                       'parameterSource':child_request.get('lineage',{}).get('trialContext',{}).get('parameterSource'),
                       'changes':changes,'events':[{key:row.get(key) for key in ['eventId','group','NSE','volume_error_percent','peak_error_percent']}
                                                   for row in child['eventMetrics']]})
    manual.sort(key=lambda row: row['createdBeijing'])
    optimizer=result['optimizer']
    errors=Counter(row.get('error','unspecified') for row in optimizer['history'] if row.get('loss') is None)
    output={'operationId':identifier,'createdBeijing':local_time(latest['createdAt']),
            'completedBeijing':local_time(latest['completedAt']), 'resultSHA256':result_digest,
            'controlAreaKm2':area,'idwPower':power,'samplingSeed':20261018,'sampleEventIds':list(dict.fromkeys(selected)),
            'optimizer':{key:optimizer.get(key) for key in ['evaluations','validEvaluations','invalidEvaluations','terminationReason']},
            'invalidReasons':dict(errors),'events':events,'annualStationChecks':annual,'manualTrials':manual,
            'notes':['Exclusion numbers are rainfall sensitivity only, not corrected truth or recalibrated outputs.',
                     'Full observed runoff depth and Q/P are null when any observed hour is missing; paired volumes are separate.',
                     'Gauge data are read from the current database snapshot and checked against frozen basin rainfall, not assumed unchanged.',
                     'Q/P uses all flow, including baseflow and antecedent storage; it is not a separated storm runoff coefficient.',
                     'A labelled independent event used to guide tuning is development evidence for future parameter selection.']}
    assert digest(result_path)==result_digest
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({key:value for key,value in output.items() if key not in {'events','annualStationChecks','manualTrials'}},ensure_ascii=True))
    print(f'Diagnostic written to {output_path}')


if __name__=='__main__':
    main()
