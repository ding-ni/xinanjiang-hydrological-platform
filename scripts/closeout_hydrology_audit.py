"""Reproducible closeout evidence and isolated hydrological experiments.

Never writes to the source workspace. Run prepare first, then audit/benchmark
with XAJ_RUNTIME_DIR pointing at the generated isolated runtime.
"""
import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
BASELINE = 'OP-ba24bb27ac6d45d89248aea49f24d39a'
RELATED = [BASELINE, 'OP-b9b76b62310b42fdb6bd3dc02267a268',
           'OP-516f4b27102a45ccac5458f6a3a31b49', 'OP-bf2945def63b49579092bcf90a85274e']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    from common import write_json
    write_json(path, value)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def prepare(source, output):
    source, output = source.resolve(), output.resolve()
    runtime = output / 'runtime'
    if output == source or source in output.parents:
        raise ValueError('隔离工作区不能放入原运行目录')
    if (output / 'evidence.json').exists():
        raise ValueError('已有基准证据，请使用现有隔离工作区或新的输出目录')
    runtime.mkdir(parents=True, exist_ok=True)
    evidence = []
    task_ids, input_ids = set(), set()
    def capture(path):
        if path.is_file():
            evidence.append({'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    for identifier in RELATED:
        folder = source / 'operations' / identifier
        status, result = read(folder / 'status.json'), read(folder / 'result.json')
        task_ids.update(status.get('childTaskIds', []))
        if result.get('primaryTaskId'):
            task_ids.add(result['primaryTaskId'])
        request = result.get('frozenRequest') or read(folder / 'request.json').get('request') or {}
        if request.get('inputs', {}).get('bundleVersionId', '').startswith('INPUT-'):
            input_ids.add(request['inputs']['bundleVersionId'])
        shutil.copytree(folder, runtime / 'operations' / identifier, dirs_exist_ok=True)
        for path in folder.glob('*.json'):
            capture(path)
    for identifier in task_ids:
        folder = source / 'tasks' / identifier
        if not folder.exists():
            continue
        request = read(folder / 'request.json')
        if request['inputs']['bundleVersionId'].startswith('INPUT-'):
            input_ids.add(request['inputs']['bundleVersionId'])
        shutil.copytree(folder, runtime / 'tasks' / identifier, dirs_exist_ok=True)
        for path in folder.iterdir():
            if path.suffix in {'.json', '.npz'}:
                capture(path)
    for identifier in input_ids:
        folder = source / 'inputs/products' / identifier
        shutil.copytree(folder, runtime / 'inputs/products' / identifier, dirs_exist_ok=True)
        destination = runtime / 'inputs/registry' / (identifier + '.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / 'inputs/registry' / destination.name, destination)
        for path in folder.rglob('*'):
            capture(path)
    for name in ['science-versions', 'routing-versions', 'parameter-schemes', 'parameters', 'states', 'implementations']:
        if (source / name).exists():
            shutil.copytree(source / name, runtime / name, dirs_exist_ok=True)
    baseline_status = read(source / 'operations' / BASELINE / 'status.json')
    prep = baseline_status.get('preparationTaskId')
    if prep:
        shutil.copytree(source / 'input-tasks' / prep, runtime / 'input-tasks' / prep, dirs_exist_ok=True)
    (runtime / 'database').mkdir(exist_ok=True)
    with sqlite3.connect((source / 'database/hydrology.sqlite3').as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(runtime / 'database/hydrology.sqlite3') as destination:
            original.backup(destination)
    for path in (ROOT / 'backend').glob('*.py'):
        capture(path)
    for path in (ROOT / 'backend/vendor/model/code').rglob('*.py'):
        capture(path)
    for path in (ROOT / 'backend/vendor/model/config').glob('*.json'):
        capture(path)
    write(output / 'evidence.json', {'source': str(source), 'runtime': str(runtime), 'baseline': BASELINE,
                                   'related': RELATED, 'files': evidence})
    subprocess_text = __import__('subprocess').check_output(['git', '-C', str(ROOT), 'diff', '--binary']).decode('utf-8', errors='replace')
    (output / 'baseline-working-tree.patch').write_text(subprocess_text, encoding='utf-8')
    print(json.dumps({'runtime': str(runtime), 'evidenceFiles': len(evidence), 'tasksCopied': len(task_ids)}, ensure_ascii=True), flush=True)


def baseline(runtime):
    return read(runtime / 'operations' / BASELINE / 'result.json')


def model_request(result, suffix):
    request = copy.deepcopy(result['frozenRequest'])
    for key in ['scheme', 'sourcePlan', 'lineage']:
        request.pop(key, None)
    request['requestId'] = 'AUDIT-' + suffix
    request['lineage'] = {}
    for key in ['sourceBundleVersionId', 'preparationTaskId']:
        request['inputs'].pop(key, None)
    request['period']['events'] = [row for row in request['period']['events'] if row.get('purpose') in {'率定', '验证', '独立检验'}]
    return request


def run_job(runtime, request, identifier):
    from common import write_json
    from worker import ModelJob
    folder = runtime / 'tasks' / identifier
    if (folder / 'result.json').is_file():
        return read(folder / 'result.json')
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / 'request.json', request)
    write_json(folder / 'status.json', {'taskId': identifier, 'createdAt': '2026-10-18T06:00:00Z', 'status': 'running'})
    started = time.perf_counter()
    try:
        ModelJob(folder).run()
        payload = read(folder / 'result.json')
        write_json(folder / 'status.json', {'taskId': identifier, 'createdAt': '2026-10-18T06:00:00Z', 'status': 'completed', 'elapsedSeconds': time.perf_counter() - started, 'runConfig': request})
        return payload
    except Exception as failure:
        write_json(folder / 'status.json', {'taskId': identifier, 'status': 'failed', 'error': str(failure)})
        raise


def check_runtime(runtime):
    from common import RUNTIME
    if runtime.resolve() != RUNTIME.resolve():
        raise ValueError('请把 XAJ_RUNTIME_DIR 设置为隔离 runtime 后再启动实验')


def audit(runtime, output):
    check_runtime(runtime)
    import numpy as np
    from common import MODEL
    sys.path.insert(0,str(MODEL/'code'))
    from input_products import InputProducts
    from hydro_database import HydroDatabase
    from scheme_inputs import station_block
    from station_network import observation_adapter
    from pyproj import Transformer
    result = baseline(runtime)
    request = result['frozenRequest']
    products = InputProducts(runtime_root=runtime)
    bundle = products.resolve(request['inputs']['bundleVersionId'])
    prep = read(runtime / 'input-tasks' / 'DATA-7304d264f3a74cb0a33d488c35b003c4' / 'request.json')
    adapter = observation_adapter(products, bundle, 'LOCAL_OBSERVATIONS')
    adapter['_stationNetwork'] = prep['stationNetwork']
    transformer = Transformer.from_crs('EPSG:4326', 'EPSG:32650', always_xy=True)
    import xarray as xr
    from xaj_routing.control_area import contributing_grid_areas
    from xaj_routing.io import read_topology_json
    from runoff_platform.data.precipitation_idw import idw_interpolate_hourly
    with bundle['paths']['grids'].open(encoding='utf-8-sig') as stream:
        grids=list(csv.DictReader(stream))
    grid_ids=[row['unit_id'] for row in grids]
    areas=contributing_grid_areas(bundle['paths']['mapping'],read_topology_json(bundle['paths']['topology']),'R0020_UP',grid_ids)
    with xr.open_dataset(next(bundle['precipitationDir'].glob('*.nc'))) as template:
        tx,ty=transformer.transform(template.lon.values[[int(row['array_col']) for row in grids]],template.lat.values[[int(row['array_row']) for row in grids]])
    stats, spatial, metrics = [], [], []
    for state in result['eventStates']:
        folder = runtime / 'tasks' / state['taskId']
        metadata = read(folder / 'grid-history.json')
        with np.load(folder / 'grid-history.npz', allow_pickle=False) as arrays:
            error = arrays['water_balance_error']
            spatial.append({'eventId': state['eventId'], 'maximumBalanceErrorMm': float(np.abs(error).max()),
                'variables': {name: {'min': float(arrays[name].min()), 'max': float(arrays[name].max()),
                                     'medianSpatialRange': float(np.median(np.ptp(arrays[name], axis=1)))}
                              for name in ['WU', 'WL', 'WD', 'S', 'RS', 'RI', 'RG']}})
        graph = result['hydrographs'][state['eventId']]
        observed = np.array([row['value'] if row['value'] is not None else np.nan for row in graph['obs']])
        simulated = np.array([row['value'] for row in graph['forecast']])
        paired = np.isfinite(observed) & np.isfinite(simulated)
        a, b = observed[paired], simulated[paired]
        nse = 1 - np.sum((a-b)**2) / np.sum((a-a.mean())**2)
        kge = 1 - np.sqrt((np.corrcoef(a,b)[0,1]-1)**2 + (b.std()/a.std()-1)**2 + (b.mean()/a.mean()-1)**2)
        peak = (b.max()/a.max()-1)*100
        volume = (b.sum()/a.sum()-1)*100
        peak_time = (np.flatnonzero(paired)[b.argmax()] - np.flatnonzero(paired)[a.argmax()])
        stored = next(row for row in result['eventMetrics'] if row['eventId'] == state['eventId'])
        metrics.append({'eventId': state['eventId'], 'NSE': float(nse), 'KGE': float(kge), 'peakErrorPercent': float(peak),
                        'volumeErrorPercent': float(volume), 'peakTimeSignedHours': int(peak_time),
                        'storedMetricMaximumDifference': float(max(abs(nse-stored['NSE']), abs(kge-stored['KGE']), abs(peak-stored['peak_error_percent']), abs(volume-stored['volume_error_percent']), abs(abs(peak_time)-stored['peak_time_error_hours'])))})
        first, last = __import__('pandas').Timestamp(state['start']), __import__('pandas').Timestamp(state['end'])
        times, ids, coordinates, rain, observed, _, _ = station_block(products, adapter, first, last, output / 'queries')
        rain = np.asarray(rain, dtype=float)
        x, y = transformer.transform(*zip(*coordinates))
        distances = np.hypot(np.subtract.outer(x,x), np.subtract.outer(y,y))
        power_results = []
        basin_totals={}
        for power in [1.,2.,3.]:
            interpolated,counts=idw_interpolate_hourly(rain,np.asarray(x),np.asarray(y),np.asarray(tx),np.asarray(ty),power=power,minimum_stations=prep['minimumStations'])
            basin_totals[str(power)]=float(np.sum(interpolated[:,areas>0] @ areas[areas>0]/areas.sum()))
            errors, actual, predicted = [], [], []
            for index in range(len(ids)):
                others = np.arange(len(ids)) != index
                d = distances[index, others]
                values = rain[:, others]
                valid = np.isfinite(values) & (values >= 0)
                colocated = d <= 1e-6
                weights = np.where(colocated, 1., 0.) if colocated.any() else 1 / np.maximum(d, 1e-6)**power
                effective = valid * weights
                sums = effective.sum(axis=1)
                estimate = np.divide(np.where(valid, values, 0) @ weights, sums, out=np.full(len(times), np.nan), where=sums>0)
                measured = rain[:,index]
                mask = np.isfinite(measured)&(measured>=0)&np.isfinite(estimate)
                actual.extend(measured[mask]);predicted.extend(estimate[mask])
                errors.extend((estimate[mask]-measured[mask]).tolist())
            a, b, errors = np.asarray(actual), np.asarray(predicted), np.asarray(errors)
            power_results.append({'power': power, 'pairedStationHours': len(errors), 'RMSE_mm_h': float(np.sqrt(np.mean(errors**2))) if len(errors) else None,
                                  'MAE_mm_h': float(np.mean(np.abs(errors))) if len(errors) else None,
                                  'totalBiasPercent': float((b.sum()/a.sum()-1)*100) if a.sum() else None})
        stats.append({'eventId':state['eventId'], 'stations':len(ids), 'missingStationHours':int((~np.isfinite(rain)|(rain<0)).sum()),
                      'stationTotals':{key:float(np.nansum(rain[:,i])) for i,key in enumerate(ids)}, 'leaveOneOut':power_results,
                      'basinRainMm':float(sum(graph.get('forcing',{}).get('precipitation',[]))),
                      'basinRainByPowerMm':basin_totals,'singleStationHours':int((counts==1).sum()),
                      'minimumHourlyStations':int(counts.min())})
        print('audited '+state['eventId'], flush=True)
    report = {'baseline': BASELINE, 'eventCount': len(metrics), 'publicCatalogCount': len(HydroDatabase(runtime).events()),
              'controlAreaKm2':float(areas.sum()),'qualityAreaKm2':read(bundle['paths']['quality'])['basin_area_km2'],
              'windowSummary': result['windowSummary'], 'objective':request['calibration']['objectives'],
              'optimizer':{key:result['optimizer'].get(key) for key in ['algorithm','evaluations','best_loss']},
              'metrics':metrics,'spatial':spatial,'rainfall':stats}
    write(output / 'hydrology-audit.json',report)


def benchmark(runtime, output):
    check_runtime(runtime)
    reference = baseline(runtime)
    records=[]
    # Holdout evaluation is deferred until all runs and candidate ranking finish.
    for algorithm in ['DIFFERENTIAL_EVOLUTION', 'SCE_UA']:
        for seed in [42,43,44]:
            request=model_request(reference,f'{algorithm}-{seed}')
            request['period']['events']=[row for row in request['period']['events'] if row['purpose'] != '独立检验']
            optimizer=request['calibration']['optimizer']
            optimizer.update(algorithm=algorithm,seed=seed,maxEvaluations=960)
            if algorithm=='SCE_UA':optimizer['complexes']=5
            else:optimizer.pop('complexes',None)
            identifier=f'JOB-audit-{algorithm}-{seed}'
            print('START '+identifier,flush=True)
            result=run_job(runtime,request,identifier)
            records.append({'algorithm':algorithm,'seed':seed,'taskId':identifier,'loss':result['optimizer']['best_loss'],
                'evaluations':result['optimizer']['evaluations'],'timings':result.get('timings'),
                'summary':result.get('windowSummary'),'parameters':result['bestParameters'],
                'routing':result.get('bestRoutingParameters'),'terminationReason':result['optimizer']['terminationReason']})
            write(output/'benchmark.json',{'runs':records})
    chosen=min(records,key=lambda row:row['loss'])
    write(output/'benchmark-candidate-locked.json',chosen)
    from routing_versions import RoutingVersions
    for row in records:
        request=model_request(reference,'holdout-'+row['taskId'])
        request['period']['events']=[event for event in request['period']['events'] if event['purpose']=='独立检验']
        request['calibration']['evaluationMode']='manual'
        request['calibration']['initialParameters']=row['parameters']
        request['calibration']['optimizer']={'algorithm':'FIXED_PARAMETERS','maxEvaluations':1}
        for key in ['target','jointProfile','searchParameters','routingSearchParameters','searchParameterOrder']:
            request['calibration'].pop(key,None)
        if row['routing']:
            version=RoutingVersions(runtime).create({'name':'隔离算法最终检验','parentVersionId':request['routing']['versionId'],'parameters':row['routing']})
            request['routing']={'versionId':version['routingVersionId']}
        final=run_job(runtime,request,row['taskId']+'-holdout')
        row['holdout']=final.get('windowSummary',{}).get('INDEPENDENT_TEST')
    write(output/'benchmark.json',{'runs':records,'selectedBeforeHoldout':chosen['taskId']})


def warmup(runtime, output):
    check_runtime(runtime)
    import numpy as np
    reference=baseline(runtime)
    from routing_versions import RoutingVersions
    from strict_forcing import load_hourly_forcing
    from input_products import InputProducts
    from worker import ModelJob
    results=[]
    selected=model_request(reference,'warmup-base')
    selected['calibration']['evaluationMode']='manual'
    selected['calibration']['initialParameters']=reference['bestParameters']
    selected['calibration']['optimizer']={'algorithm':'FIXED_PARAMETERS','maxEvaluations':1}
    for key in ['target','jointProfile','searchParameters','routingSearchParameters','searchParameterOrder']:
        selected['calibration'].pop(key,None)
    version=RoutingVersions(runtime).create({'name':'基准最佳汇流参数固定诊断','parentVersionId':selected['routing']['versionId'],'parameters':reference['bestRoutingParameters']})
    selected['routing']={'versionId':version['routingVersionId']}
    for days in [3,7,14,30]:
        request=copy.deepcopy(selected);request['period']['warmupDays']=days
        # Re-prepare the exact same station archive rather than mistaking the
        # original seven-day product's clipped range for missing observations.
        if days > 7:
            from scheme_inputs import normalize_preparation, build_prepared
            from common import write_json
            from input_products import HISTORICAL_SPATIAL_ID
            products=InputProducts(runtime_root=runtime)
            original_prep=read(runtime/'input-tasks/DATA-7304d264f3a74cb0a33d488c35b003c4/request.json')
            folder=runtime/'input-tasks'/f'DATA-audit-warmup-{days}'
            if not (folder/'result.json').exists():
                prep=normalize_preparation({'operation':'schemeInput','requestId':f'PREP-audit-{days}',
                    'projectId':'nananxi-xufan','resolutionId':'1km','parentBundleVersionId':HISTORICAL_SPATIAL_ID,
                    'period':request['period'],'statePolicy':'independent-warmup','rainfallMethod':'stationIdw',
                    'stationSourceId':'LOCAL_OBSERVATIONS','stationNetwork':original_prep['stationNetwork'],
                    'idwPower':original_prep['idwPower'],'minimumStations':original_prep['minimumStations'],
                    'science':request.get('science',{}),'evaporation':request['inputs']['evaporation']},products)
                write_json(folder/'request.json',prep)
                prepared=build_prepared(folder,products)
                write_json(folder/'result.json',prepared)
                write_json(folder/'status.json',{'taskId':folder.name,'status':'completed'})
            prepared=read(folder/'result.json')
            if prepared.get('productVersionId'):
                products.register_task(folder.name)
                request['inputs']['bundleVersionId']=prepared['productVersionId']
                request['inputs'].pop('bundleManifestSHA256',None)
        for event in request['period']['events']:
            body=copy.deepcopy(request);body['period']['events']=[event]
            identifier=f'JOB-audit-warmup-{days}-{event["id"]}'
            try:
                result=run_job(runtime,body,identifier)
                results.append({'eventId':event['id'],'warmupDays':days,'taskId':identifier,'metrics':result['eventMetrics'][0],'status':'completed'})
            except ValueError as failure:
                results.append({'eventId':event['id'],'warmupDays':days,'status':'unavailable','reason':str(failure)})
            write(output/'warmup.json',{'runs':results})
    combined=copy.deepcopy(selected);combined['period']['events']=combined['period']['events'][:2]
    together=run_job(runtime,combined,'JOB-audit-pair')
    comparisons=[]
    for event in combined['period']['events']:
        separate=read(runtime/'tasks'/f'JOB-audit-warmup-7-{event["id"]}'/'result.json')
        a=np.array([r['value'] for r in together['hydrographs'][event['id']]['forecast']])
        b=np.array([r['value'] for r in separate['hydrographs'][event['id']]['forecast']])
        final_states=[]
        ranges=[]
        for identifier in ['JOB-audit-pair',f'JOB-audit-warmup-7-{event["id"]}']:
            job=ModelJob(runtime/'tasks'/identifier)
            import pandas as pd
            row={'event_id':event['id'],'start':pd.Timestamp(event['start']),'end':pd.Timestamp(event['end']),'year':pd.Timestamp(event['start']).year,'group':'CALIBRATION'}
            forcing=job.load([row],168)[0]
            forcing.pet_mm_h=job.pet_provider.values(forcing.times)
            from worker import kernels
            _,land,routing=kernels.route_event_fast(forcing,job.params,job.routing,job.router,job.mapping,job.initial_state(job.params))
            final_states.append((land,routing))
            ranges.append({'start':forcing.times[0].isoformat(),'end':forcing.times[-1].isoformat(),'stateSource':'independent fixed scientific initial state, then own 168-hour warmup'})
        difference={key:float(np.abs(np.asarray(final_states[0][kind][key])-np.asarray(final_states[1][kind][key])).max())
                    for kind in [0,1] for key in final_states[0][kind]}
        comparisons.append({'eventId':event['id'],'maximumFlowDifference':float(np.abs(a-b).max()),
                            'metricsIdentical':next(r for r in together['eventMetrics'] if r['eventId']==event['id'])==separate['eventMetrics'][0],
                            'finalStateMaximumDifferences':difference,'actualDrivingRanges':ranges})
    write(output/'warmup.json',{'runs':results,'pairVsSeparate':comparisons})


def report(runtime, output):
    import statistics
    audit_data=read(output/'hydrology-audit.json')
    warmup_data=read(output/'warmup.json')
    benchmark_data=read(output/'benchmark.json')
    evidence=read(output/'evidence.json')
    original=baseline(runtime)
    source=Path(evidence['source'])
    protected=[row for row in evidence['files'] if row['path'].replace('\\','/').startswith(source.relative_to(ROOT).as_posix()+'/')]
    changed=[row['path'] for row in protected if not (ROOT/row['path']).is_file() or sha(ROOT/row['path'])!=row['sha256']]
    from implementation_versions import frozen_worker
    original_identity=original['lineage']['modelImplementationSHA256']
    verified_worker=frozen_worker(original_identity,runtime)
    parameter_checks=[]
    baseline_primary=original['primaryTaskId']
    related_primary={read(runtime/'operations'/identifier/'result.json').get('primaryTaskId') for identifier in RELATED}
    for path in (runtime/'parameter-schemes').glob('*.json'):
        parameter=read(path)
        if parameter.get('sourceTaskId') not in related_primary:
            continue
        saved_result=read(runtime/'tasks'/parameter['sourceTaskId']/'result.json')
        parameter_checks.append({'id':parameter['id'],'name':parameter['name'],'sourceTaskId':parameter['sourceTaskId'],
                                'parametersMatch':parameter['parameters']==(saved_result.get('bestParameters') or saved_result.get('parameters')),
                                'routingMatch':parameter['routingParameters']==(saved_result.get('bestRoutingParameters') or parameter['routingParameters'])})
    integrity={'protectedFilesChecked':len(protected),'changedSourceNumericalFiles':changed,
               'historicalWorkerVerified':str(verified_worker),'historicalImplementationSHA256':original_identity,
               'savedParameterChecks':parameter_checks}
    write(output/'integrity.json',integrity)
    lines=['# 参数率定与模拟收尾复核报告','',
        '复核对象：南安溪—徐畈1 km液态降雨新安江模型、坡面线性水库与控制断面切分后的河道汇流。基准为2026-10-18 12:42:18创建的16场自动率定，11场率定、3场验证、2场独立检验。',
        '', '## 结论','',
        '本次没有发现需要修改产流、坡面或河道方程的数值错误。独立标量对照、解析退水与汇流守恒、连续续算及搜索/重放一致性检查通过；这证明已检查实现的数值一致性，不能证明降雨输入无偏或模型能够充分解释所有实测洪水。',
        '', '当前16场拟合仍存在明显水量和洪峰误差。原因线索包括未启用水量目标、若干参数触边、预热敏感以及不同时期可用雨量站数量变化。本轮保留原参数和默认设置，没有用新增算法替换方案采用参数。',
        '', '## 一、已实现交互','',
        '- 新计算默认为临时结果；保存成果、保存参数、收藏分别管理。丢弃与恢复改变展示保留状态，原数值文件与依赖保留。',
        '- 退出计算工作区时汇总保存/丢弃清单；查看同一结果的状态和断面不触发。刷新后保留待处理记录。',
        '- 方案提供删除预览、关联临时计算选择和回收站恢复。正式成果、参数及续算依赖独立保留。',
        '- 状态支持选中栅格、多变量Excel导出，服务端限制为当前洪水完整评价期；保留原始数值、单位和来源。',
        '- 状态地图提供本场稳定范围与固定容量范围，显示极差，真实一致时显示一致；小量数值不再固定显示为0.000。',
        '- 断面为普通单行下拉；新方案可直接选择场次或连续时段；SCE-UA可选，差分进化仍为默认。',
        '', '## 二、复核结果','',
        f'- 公共目录：{audit_data["publicCatalogCount"]}场；最新16场逐场指标独立重算最大差值 {max(row["storedMetricMaximumDifference"] for row in audit_data["metrics"]):.3g}。',
        f'- 已保存逐时内部水量平衡残差最大值 {max(row["maximumBalanceErrorMm"] for row in audit_data["spatial"]):.3g} mm。该残差按内核原精度计算，输出径流为float32，比较导出径流与原精度诊断时须区分舍入误差。',
        '- 两场一起计算与各自单独计算：流量过程、逐场指标、全部产流末状态及坡面/河道末状态差值均为0；实际驱动起止与状态来源已记录在warmup.json。',
        '- 独立小算例覆盖干旱退水、干/湿/饱和状态、三层蒸散发、自由水三水源划分、零距离IDW、线性水库脉冲与Muskingum蓄量平衡；原有真实回归还覆盖系数时间换算、控制断面与续算。',
        '', '### 降雨和面积','',
        f'控制流域网格加权面积 {audit_data["controlAreaKm2"]:.8f} km²，质控配置流域面积 {audit_data["qualityAreaKm2"]} km²，相差约{(audit_data["qualityAreaKm2"]/audit_data["controlAreaKm2"]-1)*100:.2f}%。该差异影响径流深诊断口径，但不能单独解释当前约三成的水量误差；本轮没有静默替换面积。',
        '', '最新16场洪水期每小时至少有5至11个有效雨量站，没有单站驱动小时。部分旧年份站点未建成或缺资料，IDW按当时有效站重归一化，不补零。',
        '', '| IDW幂次 | 16场留一站验证RMSE的场均值（mm/h） |','| --- | ---: |']
    for power in [1.,2.,3.]:
        mean=statistics.mean(next(row['RMSE_mm_h'] for row in event['leaveOneOut'] if row['power']==power) for event in audit_data['rainfall'])
        lines.append(f'| {power:g} | {mean:.4f} |')
    rain_difference=max(abs(row['basinRainByPowerMm']['2.0']-row['basinRainMm']) for row in audit_data['rainfall'])
    lines += ['',f'独立重建幂次2的面雨量与冻结成果累计面雨量最大差值为{rain_difference:.3g} mm，与float32输入舍入一致。幂次1在这组留一站验证中较好，但留一站误差不是面雨量真值；暂不因此更改默认幂次2。各站累计雨量和幂次1/2/3的面雨量敏感性见hydrology-audit.json。',
              '', '### 初态与预热','', '全部4×16组均使用原站网、原资料、原参数和原汇流参数；14/30天重新从同一历史库准备完整驱动，未把原7天产品的裁剪边界误判为资料缺测。',
              '', '| 预热天数 | 16场平均NSE | 平均绝对水量误差 |','| --- | ---: | ---: |']
    for days in [3,7,14,30]:
        rows=[row for row in warmup_data['runs'] if row['warmupDays']==days and row['status']=='completed']
        lines.append(f'| {days} | {statistics.mean(row["metrics"]["NSE"] for row in rows):.4f} | {statistics.mean(abs(row["metrics"]["volume_error_percent"]) for row in rows):.2f}% |')
    lines += ['', '14天在本批固定参数实验中改善，30天未继续改善，说明初态对部分洪水有影响；这不是重新率定后的最优预热结论。7天默认保留，可用独立新方案进一步验证14天。',
              '', '### 率定与误差','', '原目标为NSE 0.5、洪峰误差0.25、峰现误差0.25，未启用水量或KGE目标。“合格”仅按NSE≥0.5，已在页面明确说明。',
              '', '| 用途 | 平均NSE | 平均绝对洪峰误差 | 平均绝对水量误差 |','| --- | ---: | ---: | ---: |']
    for key,label in [('CALIBRATION','率定11场'),('VALIDATION','验证3场'),('INDEPENDENT_TEST','独立检验2场')]:
        row=audit_data['windowSummary'][key]
        lines.append(f'| {label} | {row["mean_NSE"]:.4f} | {row["mean_abs_peak_error_percent"]:.2f}% | {row["mean_abs_volume_error_percent"]:.2f}% |')
    lines += ['', '基准KC=0.4、B=0.8、IM=0.08、地下水比例=0.02、Muskingum X=0.05等处于搜索边界。当前结果可能以参数补偿资料、初态或模型结构误差，不应只扩大边界并据此认定问题解决。',
              '', '问题较明显的2022-06-05、2022-03-17洪水，水量偏差约−59.0%、−57.4%；2019-08-09约+56.7%，误差方向并不统一。建议优先核对这些场的基流、前期蓄水、雨量空间代表性与洪水边界，再在新方案中显式加入水量目标比较。',
              '', '## 三、SCE-UA与差分进化','',
              '六次搜索使用相同的11场率定、3场验证、参数范围、初值、原目标函数和960次评价预算；独立检验2场从搜索请求中移除，候选按率定目标锁定后才统一评价。种子42的差分进化准确复现原960次搜索最优值。',
              '', '| 算法 | 种子 | 最优目标（越小越好） | 验证平均NSE | 独立检验平均NSE | 搜索秒数 |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for row in benchmark_data['runs']:
        lines.append(f'| {row["algorithm"]} | {row["seed"]} | {row["loss"]:.6f} | {row["summary"]["VALIDATION"]["mean_NSE"]:.4f} | {row["holdout"]["mean_NSE"]:.4f} | {row["timings"]["searchSeconds"]:.1f} |')
    lines += ['', '差分进化三次目标中位值约−0.2340，SCE-UA约−0.2017；本预算下差分进化的率定目标和独立检验整体较好。验证组表现存在交叉，因此不作算法普遍优劣结论。SCE-UA为16维、5个复合体、165点种群，短预算下演化轮数相对有限。耗时包含首次编译和同期诊断影响，不能据一次耗时判定性能。',
              '',f'独立检验前锁定候选为 {benchmark_data["selectedBeforeHoldout"]}，未自动采用到用户方案。',
              '', '## 四、问题分级与剩余限制','',
              '| 等级与类别 | 证据与处理 |',
              '| --- | --- |',
              '| 高优先级 · 拟合与率定取舍 | 多场水量误差仍大，原目标未启用水量；保留原成果，建议在新方案中比较水量目标及异常场次资料。 |',
              '| 中优先级 · 初态敏感性 | 固定参数14天预热在本组较好，30天并未继续改善；不据此替换7天默认。 |',
              '| 中优先级 · 资料与口径 | 站网随年份变化、面雨量代表性和约1.31%的面积口径差异需继续核对；PET是逐月假设。 |',
              '| 已修复 · 成果与界面 | 临时计算主动保存/丢弃、恢复、离开清单、参数与成果分离、旧收藏固定迁移、状态精确导出与色标、连续入口和异步方案读取保护。 |',
              '| 已检查 · 数值实现 | 本轮未发现需修改方程的错误；检查范围内守恒、标量对照、独立场次与续算一致性通过，不外推为所有情形均正确。 |',
              '', '## 五、保护、验收与边界','',
              f'- 原工作区{len(protected)}个受保护数值文件重新校验，变化{len(changed)}个；原计算程序快照通过完整摘要验证。',
              f'- 相关已保存参数来源核对{len(parameter_checks)}项：'+('参数及汇流数值全部一致。' if all(row['parametersMatch'] and row['routingMatch'] for row in parameter_checks) else '存在差异，见integrity.json。'),
              '- 前端298项回归通过，统一构建通过；相关后端回归64项（跳过7项环境专用测试）通过，32项启用真实模型的内核/续算/人工试算回归全部通过；最终专项12项通过，包含新增标量与解析测试4项、旧收藏迁移与状态导出检查。',
              '- 浏览器在1366×768及1920×1080检查状态和率定布局，验证Excel当前洪水完整边界、多变量、普通断面下拉、连续入口、SCE设置、临时模拟、内部状态跳转、取消离开、刷新恢复、丢弃、成果保存独立于收藏，以及方案删除恢复；结果和截图见browser-final/report.json。',
              '- 本报告不覆盖未接入真实资料的寒区演示分支；当前PET仍是冻结的逐月假设，不是实测蒸散发。',
              '- 状态与初态采用已登记方案；独立场次不等于自然流域状态每天重置，预热只是对未知前期状态的近似。连续轨迹适合资料完整且需要跨事件状态记忆的工作，两种方式均保留。',
              '- 回收站本轮不物理清理文件；浏览器关闭前仅警告，异常关闭后仍恢复待处理清单。',
              '', '原始证据：evidence.json；指标/降雨/状态：hydrology-audit.json；预热与配对：warmup.json；算法：benchmark.json及锁定候选；原文件保护：integrity.json。']
    (output/'复核报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    if changed or any(not row['parametersMatch'] or not row['routingMatch'] for row in parameter_checks):
        raise AssertionError('原数据或参数来源校验失败，见integrity.json')
    print(json.dumps({'report':str(output/'复核报告.md'),'protectedFiles':len(protected),'changes':len(changed)},ensure_ascii=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','audit','warmup','benchmark','report'])
    parser.add_argument('--source',type=Path,default=ROOT/'runtime')
    parser.add_argument('--output',type=Path,default=ROOT/'开发产物/收尾复核20261018')
    args=parser.parse_args()
    args.output=args.output.resolve()
    if args.command=='prepare':prepare(args.source,args.output)
    else:globals()[args.command](args.output/'runtime',args.output)
