"""Frozen four-stage diagnostic workflow using the delivered numerical kernels."""
from __future__ import annotations
import copy
from common import MODEL, RUNTIME, read_json, write_json
from diagnostic_jobs import run_experiment, sha, workflow_evidence


def annual_frame(rows):
    """Keep all-missing metrics numeric for the delivered group evaluator."""
    import pandas as pd
    frame = pd.DataFrame(rows)
    for name in ('NSE', 'KGE', 'volume_error_percent', 'climate_balance_residual_using_observed_runoff_mm'):
        frame[name] = pd.to_numeric(frame[name] if name in frame else pd.Series(index=frame.index, dtype=float), errors='coerce')
    return frame


def continuous_balance(folder, request, parameters):
    import numpy as np
    import pandas as pd
    from worker import ModelJob, kernels
    from run_continuous_water_balance import _load_precipitation_year, _state_storage_depth, _group_summary
    from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids
    from runoff_platform.data.pet_forcing import PetProvider
    from xaj_routing.control_area import contributing_grid_areas, contributing_topology_area
    from xaj_routing.io import read_topology_json

    first, last = request['warmupYear'], max(year for rows in request['yearGroups'].values() for year in rows)
    base = copy.deepcopy(request['baseRequest'])
    base.update(runType='simulation', parameters=parameters)
    base.pop('calibration', None)
    base['inputs']['evaporation'] = {'mode': 'monthly-climatology-assumption'}
    base['period'] = {'startTime': f'{first}-01-01T00:00', 'endTime': f'{last}-12-31T23:00', 'warmupDays': 0}
    write_json(folder / 'request.json', base)
    write_json(folder / 'status.json', {'taskId': folder.name})
    job = ModelJob(folder, publish_parameters=False)
    topology = read_topology_json(job.topology)
    areas = contributing_grid_areas(job.mapping_file, topology, 'R0020_UP', job.grid_ids)
    model_area = float(np.sum(areas))
    area_config = read_json(MODEL / 'config/control_section_area_xufan.json')
    observed_area = float(area_config['observation_area_km2'])
    if not np.isfinite(model_area) or model_area <= 0 or not np.isfinite(observed_area) or observed_area <= 0:
        raise ValueError('连续诊断控制区面积必须为有限正数')
    weights = areas / model_area
    relative_difference = abs(model_area - observed_area) / observed_area
    if observed_area <= 0 or relative_difference > area_config['maximum_model_to_observation_relative_difference']:
        raise ValueError('连续诊断控制区面积与注册观测断面不一致')
    mapping_error = float(np.max(np.abs(job.mapping.sum(axis=0) - 1)))
    if mapping_error > 0.05:
        raise ValueError('连续诊断网格映射不闭合')
    positions = [(int(grid['array_row']), int(grid['array_col'])) for grid in job.grids]
    provider = PetProvider.from_file(MODEL / 'config/pet_forcing_assumed_monthly.json')
    land, routing = job.initial_state(job.params), None
    by_year = {year: group for group, years in request['yearGroups'].items() for year in years}
    rows, hourly = [], []
    for year in range(first, last + 1):
        write_json(folder.parent / 'progress.json', {'progress': 60 + 35 * (year - first) / (last - first + 1), 'stageLabel': f'连续水量平衡 {year} 年；继承土地与河网记忆'})
        times, precipitation = _load_precipitation_year(job.precip_dir, year, positions)
        start_storage = _state_storage_depth(land, weights)
        rs, ri, rg, land, diagnostics = simulate_xaj_liquid_grids(precipitation, job.params, provider.values(times), land, return_diagnostics=True)
        arrays = [rs, ri, rg, *land.values(), diagnostics['et_actual'], diagnostics['water_balance_error']]
        if any(not np.isfinite(values).all() for values in arrays):
            raise ValueError('连续产流或土地状态出现非有限数值')
        end_storage = _state_storage_depth(land, weights)
        flow, routing = job.router.run(times, rs @ job.mapping, ri @ job.mapping, rg @ job.mapping, job.routing, routing)
        if not np.isfinite(flow.to_numpy(float)).all() or (flow < 0).any():
            raise ValueError('连续汇流出现无效输出')
        if year not in by_year:
            continue
        observed = job.observed.reindex(times)
        hourly.append(pd.DataFrame({'time': times, 'year': year, 'group': by_year[year], 'simulated_flow_m3_s': flow.to_numpy(), 'observed_flow_m3_s': observed.to_numpy()}))
        coverage = float(observed.notna().mean())
        p = float(np.sum(precipitation @ weights))
        et = float(np.sum(diagnostics['et_actual'] @ weights))
        storage_change = end_storage - start_storage
        internal = diagnostics['water_balance_error'] @ weights
        # A partial observed series cannot represent an annual runoff volume.
        observed_depth = float(observed.sum() * 3.6 / observed_area) if observed.notna().all() else None
        residual = ((p - et - storage_change) * model_area / observed_area - observed_depth) if observed_depth is not None else None
        rows.append({'year': year, 'group': by_year[year], 'hour_count': len(times), 'observed_coverage': coverage,
                     'precipitation_mm': p, 'actual_et_mm': et, 'generated_runoff_mm': float(np.sum((rs + ri + rg) @ weights)),
                     'land_storage_change_mm': storage_change, 'internal_balance_error_mm': float(np.sum(internal)),
                     'maximum_abs_hourly_internal_balance_error_mm': float(np.max(np.abs(internal))),
                     'simulated_control_runoff_depth_mm': float(flow.sum() * 3.6 / observed_area),
                     'observed_control_runoff_depth_mm': observed_depth,
                     'climate_balance_residual_using_observed_runoff_mm': residual,
                     **kernels.metrics(observed.dropna(), flow)})
    annual = annual_frame(rows)
    annual.to_csv(folder / 'annual_water_balance_metrics.csv', index=False, encoding='utf-8-sig')
    pd.concat(hourly, ignore_index=True).to_csv(folder / 'continuous_hourly_flow.csv', index=False, encoding='utf-8-sig')
    report = {'module_id': 'CAL-CONTINUOUS-BALANCE-09', 'status': 'SUCCEEDED', 'annual': rows,
              'simulation_years': [first, last], 'year_groups': request['yearGroups'], 'pet': provider.report(),
              'state_carry_over': {'land': True, 'routing': True},
              'groups': {group: _group_summary(annual[annual.group == group]) for group in request['yearGroups']},
              'minimum_observed_coverage': float(annual.observed_coverage.min()),
              'maximum_abs_internal_balance_error_mm': float(annual.internal_balance_error_mm.abs().max()),
              'maximum_abs_hourly_internal_balance_error_mm': float(annual.maximum_abs_hourly_internal_balance_error_mm.max()),
              'area': {'observation_area_km2': observed_area, 'model_contributing_area_km2': model_area,
                       'topology_contributing_area_km2': contributing_topology_area(topology, 'R0020_UP'),
                       'model_to_observation_relative_difference': relative_difference, 'control_reach_id': 'R0020_UP',
                       'original_area_config_reach_id': area_config['control_reach_id'], 'policy': area_config['policy']},
              'routing_mapping_max_relative_error': mapping_error, 'inputManifestSHA256': job.input_digest,
              'warnings': ['逐月 PET 为诊断假设，不能作为生产资格。', '缺测观测不补零；不完整年份不报告完整年实测径流深。']}
    write_json(folder / 'continuous_water_balance_report.json', report)
    return read_json(folder / 'continuous_water_balance_report.json')


def run_workflow(folder, request):
    from calibration_platform.production_validation import evaluate_production_gate
    config_path = MODEL / 'config/production_calibration_workflows.json'
    if sha(config_path) != request['workflowConfigSHA256']:
        raise ValueError('排队后的工作流判据发生改变')
    if workflow_evidence() != request['workflowEvidence']:
        raise ValueError('排队后的工作流实现或配置发生改变')
    config = read_json(config_path)['workflows']['NANAN_PRODUCTION_V1']
    base = copy.deepcopy(request['baseRequest'])
    stage = {**request, 'runType': 'diagnosticExperiment', 'mode': 'wlmBoundary', 'values': request['wlmUpperBounds'], 'baseRequest': base}
    boundary_folder = folder / 'JOB-boundary'
    write_json(folder / 'progress.json', {'progress': 3, 'stageLabel': '第一阶段：WLM 上界比较'})
    boundary = run_experiment(boundary_folder, stage, publish_candidate=False)
    base['calibration']['wlmUpperBound'] = boundary['experiment']['evaluation']['selected_upper_bound']
    base['calibration']['initialParameters'] = boundary['bestParameters']
    stage.update(mode='multiSeed', values=request['seeds'], baseRequest=base)
    stability_folder = folder / 'JOB-stability'
    write_json(folder / 'progress.json', {'progress': 30, 'stageLabel': '第二阶段：固定边界后多种子稳定性'})
    stability = run_experiment(stability_folder, stage, publish_candidate=False)
    continuous = continuous_balance(folder / 'continuous-balance', request, stability['bestParameters'])
    event_report = {'groups': {'CALIBRATION': stability['calibrationMetrics'], 'VALIDATION': stability['validationMetrics'], 'INDEPENDENT_TEST': stability['independentTest']}}
    gate = evaluate_production_gate(boundary['experiment']['evaluation'], stability['experiment']['evaluation'], continuous, event_report, config['production_criteria'])
    if gate['production_approved']:
        raise ValueError('诊断 PET 工作流不应获得生产批准，拒绝保存此结论')
    version = f'PARAM-{folder.name}'
    result = {'workflow': {'boundary': boundary['experiment'], 'stability': stability['experiment'], 'continuous': continuous, 'gate': gate},
              'bestParameters': stability['bestParameters'], 'parameterVersionId': version,
              **{key: stability[key] for key in ('calibrationMetrics', 'validationMetrics', 'independentTest')},
              'workflowEvidence': request['workflowEvidence'],
              'warnings': ['四阶段计算与原后端验收判据已执行；诊断 PET 不获得生产资格。', '本流程使用统一的固定预热策略，不等同于原 OBSERVED_FLOW_ANCHORED 策略。', '原后端边界与代表种子选择参考验证及独立检验指标，因此此处独立检验属于选择后诊断，不是未参与选择的最终盲测。', '年度分组与洪水场次分组分别设置，可能存在时段交叉；不得据此宣称独立生产验收。']}
    write_json(folder / 'production-gate-report.json', gate)
    write_json(RUNTIME / 'parameters' / f'{version}.json', {'parameterVersionId': version, 'taskId': folder.name,
               'context': request['context'], 'lineage': request['lineage'], 'bestParameters': result['bestParameters'],
               'resultSource': 'model-backend', 'productionEligible': False,
               **{key: result[key] for key in ('calibrationMetrics', 'validationMetrics', 'independentTest')}, 'workflowGate': gate})
    return result
