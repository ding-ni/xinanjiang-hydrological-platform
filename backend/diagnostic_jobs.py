"""Adapters for delivered point routing and diagnostic experiment engines."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from common import MODEL, RUNTIME, read_json, write_json, safe_task
from contracts import finite_number, freeze_input_selection, freeze_routing_selection, normalize_request
from grid_routing import build_grid_routing
from routing_versions import RoutingVersions
from catalog import case_catalog
from science_context import IDENTITY_KEYS, ScienceVersions, freeze_science_selection

sys.path.insert(0, str(MODEL / "code"))


def point_assets():
    return {name: sha(MODEL / "data_interface/spatial" / filename) for name, filename in (
        ("demSHA256", "nan_an_dem_utm50_30m_clipped.asc"),
        ("controlSHA256", "xufan_70115800_control_point_snapped_v1.json"))}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def workflow_evidence():
    paths = [MODEL / 'scripts/run_continuous_water_balance.py',
             MODEL / 'config/production_calibration_workflows.json',
             MODEL / 'config/control_section_area_xufan.json',
             MODEL / 'config/pet_forcing_assumed_monthly.json',
             Path(__file__), Path(__file__).with_name('workflow_jobs.py'),
             Path(__file__).with_name('worker.py'), Path(__file__).with_name('routing_adapter.py'),
             Path(__file__).with_name('calibration_search.py'), Path(__file__).with_name('calibration_space.py')]
    return {path.name: sha(path) for path in paths}


def point_source(manager, task_id):
    result = manager.result(task_id)
    folder = safe_task(manager.root, task_id)
    request = read_json(folder / "request.json")
    if request.get("runType") not in {"simulation", "stateInitialization", "stateContinuation", "forecast"}:
        raise ValueError("请选择已完成的模拟或预报成果")
    if not (folder / "routing-evidence.json").is_file():
        raise ValueError("该旧任务未保存完整产流过程，请新建连续模拟后再查询点位")
    metadata = read_json(folder / "routing-evidence.json")
    evidence = result.get("routingEvidence", {})
    if (metadata.get("taskId") != task_id or metadata.get("lineage") != request.get("lineage")
            or evidence.get("arraySHA256") != sha(folder / "routing-runoff.npz")
            or metadata.get("arraySHA256") != evidence.get("arraySHA256")
            or evidence.get("catalogSHA256") != sha(folder / "routing-evidence.json")):
        raise ValueError("来源任务产流、汇流证据与成功结果不一致")
    return folder, request, result, metadata


def require_point_rerun(source, evidence, runtime_root):
    if source.get('period', {}).get('initialStateVersionId') or evidence.get('initialRoutingState') is not None:
        raise ValueError('该成果采用续算状态；河道中间断面需独立复算，控制断面可直接查询')
    if source.get('science'):
        freeze_science_selection(copy.deepcopy(source), runtime_root)


def locate_river_point(engine, point):
    from xaj_routing.raster_formats import cell_center_xy
    try:
        nearest, reach, distance = engine._nearest_stream(engine._point_cell(point['lon'], point['lat']))
    except (ValueError, IndexError) as exc:
        raise ValueError('所选位置不在可查询河网范围，请在流域河道内重新选点') from exc
    if distance > 300:
        raise ValueError(f'距河道{distance:.0f}米，超过300米，请在河道附近重新选点')
    longitude, latitude = engine.inverse_transformer.transform(*cell_center_xy(engine.grid, *nearest))
    control = next((node for node in engine.topology_result.control_nodes
                    if node.get('cell') == list(nearest) and node.get('upstream_reach_id') == 'R0020_UP'), None)
    location = {'clicked': [point['lon'], point['lat']], 'queried': [longitude, latitude],
                'distanceM': distance, 'reachId': reach}
    return longitude, latitude, location, control


def normalize_diagnostic(request, manager):
    if not isinstance(request, dict):
        raise ValueError("诊断请求必须为对象")
    request = copy.deepcopy(request)
    if request.get('runType') == 'productionWorkflow':
        if set(request) - {'runType', 'requestId', 'baseRequest', 'wlmUpperBounds', 'seeds', 'yearGroups', 'warmupYear', 'context', 'inputs', 'lineage', 'routing', 'workflowConfigSHA256', 'workflowEvidence', 'annualScience', 'annualScienceLineage'}:
            raise ValueError('工作流请求包含未登记字段')
        boundary = normalize_diagnostic({'runType': 'diagnosticExperiment', 'baseRequest': request.get('baseRequest'), 'mode': 'wlmBoundary', 'values': request.get('wlmUpperBounds')}, manager)
        stability = normalize_diagnostic({'runType': 'diagnosticExperiment', 'baseRequest': request.get('baseRequest'), 'mode': 'multiSeed', 'values': request.get('seeds')}, manager)
        groups = request.get('yearGroups')
        names = {'CALIBRATION', 'VALIDATION', 'INDEPENDENT_TEST'}
        if not isinstance(groups, dict) or set(groups) != names or any(not isinstance(rows, list) or not rows for rows in groups.values()):
            raise ValueError('连续诊断需要非空的率定、验证、独立检验年份组')
        years = [year for rows in groups.values() for year in rows]
        warmup = request.get('warmupYear')
        if any(isinstance(year, bool) or not isinstance(year, int) or not 1961 <= year <= 2024 for year in years) or len(years) != len(set(years)):
            raise ValueError('年份须在 1961–2024 且不能重复或跨组')
        if isinstance(warmup, bool) or not isinstance(warmup, int) or not 1960 <= warmup < min(years) or max(years) - warmup > 10:
            raise ValueError('预热年须早于所有评价年，连续跨度最多十年')
        frozen = boundary['baseRequest']
        bundle = manager.products.resolve(frozen['inputs']['bundleVersionId'])
        for year in range(warmup, max(years) + 1):
            if not (bundle['precipitationDir'] / f'nananxi_precip_hourly_1km_{year}.nc').is_file():
                raise ValueError(f'输入版本缺少连续年份 {year}')
        config_sha = sha(MODEL / 'config/production_calibration_workflows.json')
        if request.get('workflowConfigSHA256', config_sha) != config_sha:
            raise ValueError('工作流判据摘要已改变')
        evidence = workflow_evidence()
        if request.get('workflowEvidence', evidence) != evidence:
            raise ValueError('工作流实现或配置摘要已改变')
        lineage = copy.deepcopy(frozen['lineage'])
        if request.get('lineage', {}).get('sourceTaskId'):
            lineage['sourceTaskId'] = request['lineage']['sourceTaskId']
        scientific = ScienceVersions(manager.runtime_root).resolve(frozen['science']['versionId'])
        annual = ScienceVersions(manager.runtime_root).register(
            name='工作流年度连续计算的逐月给定 PET', state_config=scientific['paths']['state_config'],
            initial_land_state=scientific['paths']['initial_land_state'],
            pet_config=MODEL / 'config/pet_forcing_assumed_monthly.json',
            state_policy={'strategy': 'independent-warmup', 'warmupHours': 0})
        annual_request = copy.deepcopy(frozen)
        annual_request['science'] = {'versionId': annual['scienceVersionId']}
        annual_request['inputs']['evaporation'] = {'mode': 'science-version'}
        annual_request.pop('calibration', None)
        for key in (*IDENTITY_KEYS, 'scienceVersionId', 'petMode'):
            annual_request['lineage'].pop(key, None)
        annual_request, _descriptor = freeze_science_selection(annual_request, manager.runtime_root)
        if request.get('annualScience', annual_request['science']) != annual_request['science'] or request.get('annualScienceLineage', annual_request['lineage']) != annual_request['lineage']:
            raise ValueError('年度连续计算科学条件发生改变，请重新登记工作流')
        return {'runType': 'productionWorkflow', 'requestId': request.get('requestId'), 'baseRequest': frozen,
                'annualScience': annual_request['science'], 'annualScienceLineage': annual_request['lineage'],
                'wlmUpperBounds': boundary['values'], 'seeds': stability['values'], 'yearGroups': groups,
                'warmupYear': warmup, 'workflowConfigSHA256': config_sha, 'workflowEvidence': evidence,
                **{key: frozen[key] for key in ('context', 'inputs', 'routing')}, 'lineage': lineage}
    if request.get("runType") == "pointQuery":
        if 'riverOnly' in request and not isinstance(request['riverOnly'], bool):
            raise ValueError('河道查询标识无效')
        folder, source, result, evidence = point_source(manager, request.get("sourceTaskId"))
        point = request.get("point")
        if not isinstance(point, dict) or set(point) != {"lon", "lat", "channelWidthM"}:
            raise ValueError("点查询必须提供经度、纬度和分类河宽")
        for key, low, high in (("lon", -180, 180), ("lat", -90, 90), ("channelWidthM", 1, 1000)):
            point[key] = finite_number(point[key], key)
            if not low <= point[key] <= high:
                raise ValueError("点查询数值范围无效")
        frozen = {key: copy.deepcopy(source[key]) for key in ("inputs", "lineage", "routing", "context")}
        if source.get('science'):
            frozen['science'] = copy.deepcopy(source['science'])
        if request.get('lineage', {}).get('sourceTaskId'):
            frozen['lineage']['sourceTaskId'] = request['lineage']['sourceTaskId']
        frozen, bundle = freeze_input_selection(frozen, manager.products)
        frozen = freeze_routing_selection(frozen, bundle, manager.routing_versions)
        if request.get('riverOnly'):
            parameters = manager.routing_versions.resolve(frozen['routing']['versionId'])
            engine = make_point_engine(bundle, folder, parameters['parameters'])
            _longitude, _latitude, _location, control = locate_river_point(engine, point)
            if not control:
                require_point_rerun(source, evidence, manager.root.parent)
        else:
            require_point_rerun(source, evidence, manager.root.parent)
        request.update(frozen)
        request["sourceResultSHA256"] = sha(folder / "result.json")
        request["sourceRoutingEvidence"] = copy.deepcopy(result["routingEvidence"])
        request["spatialEvidence"] = point_assets()
        return request
    if request.get("runType") != "diagnosticExperiment":
        raise ValueError("诊断任务类型未接入")
    base = request.get("baseRequest")
    if not isinstance(base, dict) or base.get("runType") != "calibration":
        raise ValueError("参数检验必须选择已登记的率定任务")
    if base.get('calibration', {}).get('evaluationMode') == 'manual':
        raise ValueError('多种子与边界搜索请选用自动率定方案；人工试调在率定页对照')
    if base.get('sourcePlan') is not None or base.get('calibration', {}).get('optimizer', {}).get('initializationPolicy') == 'SOURCE_INITIAL_CANDIDATES':
        raise ValueError('原率定方案冻结随机种子与范围；参数检验请先采用候选，再登记普通自动率定方案')
    if base.get('calibration', {}).get('target') == 'routing':
        raise ValueError('本项产流稳定性检验请选择产流率定方案')
    base, bundle = freeze_input_selection(base, manager.products)
    base = freeze_routing_selection(base, bundle, manager.routing_versions)
    base = normalize_request(base, case_catalog(bundle))
    base, _science = freeze_science_selection(base, manager.runtime_root)
    periods = base["period"].get("windows", []) if base["period"].get("mode") == "continuous" else base["period"]["events"]
    if not any(row.get("purpose") == "独立检验" and row.get("status") != "inactive" for row in periods):
        raise ValueError("诊断实验必须保留独立检验组；不能用验证组冒充")
    if base["calibration"]["optimizer"]["maxEvaluations"] > 500:
        raise ValueError("单次实验预算最多 500 次，避免无界计算")
    mode, values = request.get("mode"), request.get("values")
    if base['calibration'].get('target') == 'joint' and mode == 'wlmBoundary':
        raise ValueError('联合方案的边界检验请显式修改搜索范围后重新登记，不沿用旧产流 WLM 工作流')
    if mode == 'wlmBoundary' and base['calibration'].get('searchParameters') is not None:
        raise ValueError('WLM边界检验请选用预设参数范围的率定方案')
    if not isinstance(values, list) or len(values) > 5:
        raise ValueError("实验值必须为不重复的有界数组")
    if mode == "multiSeed":
        if len(values) < 3 or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 4294967295 for v in values):
            raise ValueError("多种子实验需要 3–5 个不同的非负整数种子")
    elif mode == "wlmBoundary":
        if len(values) < 2 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not 60 <= v <= 200 for v in values):
            raise ValueError("WLM 实验需要 2–5 个不同的 60–200 mm 上界")
    else:
        raise ValueError("实验方式未接入")
    if len(set(values)) != len(values):
        raise ValueError("实验值必须互不重复")
    lineage = copy.deepcopy(base['lineage'])
    if request.get('lineage', {}).get('sourceTaskId'):
        lineage['sourceTaskId'] = request['lineage']['sourceTaskId']
    return {"runType": "diagnosticExperiment", "requestId": request.get("requestId"), "mode": mode,
            "values": values, "baseRequest": base, **{key: base[key] for key in ("context", "inputs", "routing")},
            "lineage": lineage}


def make_point_engine(bundle, output, parameters):
    sys.path.insert(0, str(MODEL / "code"))
    from routing_adapter import RegisteredPointFlowQuery
    paths = {"dem": MODEL / "data_interface/spatial/nan_an_dem_utm50_30m_clipped.asc",
             "control": MODEL / "data_interface/spatial/xufan_70115800_control_point_snapped_v1.json",
             "topology": bundle["paths"]["topology"], "output": output,
             "runoff_wide": output / "runoff-wide.csv", "runoff_grid_geojson": bundle["paths"]["geometry"],
             "grid_static": bundle["paths"]["grids"]}
    engine = RegisteredPointFlowQuery(MODEL, paths, {**parameters, "stream_threshold_km2": 1.0})
    expected = read_json(bundle["paths"]["topology"])["reaches"]
    if set(engine.reach_by_id) != {row["reach_id"] for row in expected}:
        raise ValueError("D8 重建河段与冻结拓扑不一致，拒绝使用默认替代河段")
    return engine


def spatial_payload(bundle):
    from pyproj import Transformer
    from xaj_routing.raster_formats import read_esri_ascii_grid, cell_center_xy
    grid = read_esri_ascii_grid(MODEL / "data_interface/spatial/nan_an_dem_utm50_30m_clipped.asc")
    transformer = Transformer.from_crs("EPSG:32650", "EPSG:4326", always_xy=True)
    topology = read_json(bundle["paths"]["topology"])
    reaches = []
    for reach in topology["reaches"]:
        row = copy.deepcopy(reach)
        row["startLonLat"] = list(transformer.transform(*cell_center_xy(grid, *row["start_cell"])))
        row["endLonLat"] = list(transformer.transform(*cell_center_xy(grid, *row["end_cell"])))
        reaches.append(row)
    control = read_json(MODEL / "data_interface/spatial/xufan_70115800_control_point_snapped_v1.json")
    control["originalSnappedReachId"] = control["snapped"]["reach_id"]
    if "R0020_UP" not in {row["reach_id"] for row in reaches}:
        raise ValueError("控制切分拓扑缺少徐畈上游控制河段")
    control["snapped"]["reach_id"] = "R0020_UP"
    control["topologyBinding"] = "CONTROL_SPLIT_V2_UPSTREAM_OUTFLOW"
    return {"bundleVersionId": bundle["bundleVersionId"], "manifestSHA256": bundle["manifestSHA256"],
            "topology": {**topology, "reaches": reaches}, "mappingSHA256": sha(bundle["paths"]["mapping"]),
            "geometry": read_json(bundle["paths"]["geometry"]),
            "gridRouting": build_grid_routing(bundle),
            "rivers": read_json(MODEL / "data_interface/spatial/南安溪河网.geojson"),
            "basin": read_json(MODEL / "data_interface/spatial/南安溪流域边界.geojson"),
            "control": control,
            "productionEligible": False}


def run_point(folder, request):
    import numpy as np
    from input_products import InputProducts
    # A read-only view avoids starting another executor/recovering statuses.
    class Source:
        root = RUNTIME / "tasks"
        def result(self, task_id):
            source = safe_task(self.root, task_id)
            if read_json(source / "status.json")["status"] not in {"completed", "archived"}:
                raise ValueError("来源模拟任务未成功完成")
            return read_json(source / "result.json")
    source, original, result, metadata = point_source(Source(), request["sourceTaskId"])
    if sha(source / "result.json") != request["sourceResultSHA256"] or result["routingEvidence"] != request["sourceRoutingEvidence"]:
        raise ValueError("排队后的来源任务发生改变")
    if point_assets() != request["spatialEvidence"]:
        raise ValueError("排队后的 DEM / 控制点发生改变")
    bundle = InputProducts().resolve(request["inputs"]["bundleVersionId"])
    if bundle["manifestSHA256"] != request["inputs"]["bundleManifestSHA256"]:
        raise ValueError("来源输入摘要改变")
    parameters = RoutingVersions().resolve(request["routing"]["versionId"])
    if parameters["parametersSHA256"] != request["routing"]["parametersSHA256"] or parameters["parameters"] != metadata["routingParameters"]:
        raise ValueError("来源汇流参数不一致")
    freeze_routing_selection(copy.deepcopy(request), bundle)
    write_json(folder / "progress.json", {"progress": 10, "stageLabel": "读取冻结产流；构建真实 30 m D8 拓扑"})
    with np.load(source / "routing-runoff.npz", allow_pickle=False) as archive:
        times = archive["times"].tolist()
        components = {name: archive[name].copy() for name in ("RS", "RI", "RG")}
    if any(values.shape != (len(times), len(metadata["gridIds"])) or not np.isfinite(values).all() for values in components.values()):
        raise ValueError("完整产流数组无效")
    engine = make_point_engine(bundle, folder, parameters["parameters"])
    point = request['point']
    longitude, latitude = point['lon'], point['lat']
    location = None
    if request.get('riverOnly'):
        longitude, latitude, location, control = locate_river_point(engine, point)
        if control:
            # The simulated outlet process is already the frozen control-node
            # outflow. Reading it avoids re-routing or rebuilding its state.
            rows = result['hydrograph']['forecast']
            answer = {'schema_version': 'point_flow_query.v1', 'run_id': request['sourceTaskId'],
                      'point': {'lon': longitude, 'lat': latitude, 'cell': control['cell']},
                      'classification': {'point_type': 'RIVER'}, 'location': location,
                      'series': {'time': [row['time'] for row in rows], 'flow_cms': [row['value'] for row in rows], 'unit': 'm3/s'},
                      'method': 'PERSISTED_CONTROL_HYDROGRAPH',
                      'topology': {'topology_action': 'EXISTING_CONTROL_NODE', 'control_node_id': control['node_id'],
                                   'upstream_reach_id': control['upstream_reach_id'], 'requires_rerun': False},
                      'quality': {'status': 'FORMAL_SPLIT_RESULT', 'warning': '读取来源模拟已保存的控制断面流量。'}}
            return {'pointQuery': answer, 'sourceTaskId': request['sourceTaskId'], 'warnings': [answer['quality']['warning']]}
    require_point_rerun(original, metadata, RUNTIME)
    with (folder / "runoff-wide.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time", "unit_id", "RS_mm", "RI_mm", "RG_mm"])
        for step, time in enumerate(times):
            for index, grid_id in enumerate(metadata["gridIds"]):
                writer.writerow([time, grid_id, *[float(components[name][step, index]) for name in ("RS", "RI", "RG")]])
    # The native slope query also needs the actual complete reach/component run.
    from integration.runoff_adapter import aggregate_runoff_to_reach_segments
    from xaj_routing.engine import RoutingEngine
    from xaj_routing.io import read_topology_json, read_runoff_csv, write_reach_flows_csv, write_component_flows_csv
    from xaj_routing.models import RoutingParameters
    aggregate_runoff_to_reach_segments(folder / "runoff-wide.csv", bundle["paths"]["mapping"], bundle["paths"]["topology"], folder / "runoff-by-unit.csv")
    routed = RoutingEngine(read_topology_json(bundle["paths"]["topology"]), RoutingParameters.from_mapping(parameters["parameters"])).run(read_runoff_csv(folder / "runoff-by-unit.csv"))
    write_reach_flows_csv(folder / "reach_flow.csv", routed.reach_flows, request["sourceTaskId"])
    write_component_flows_csv(folder / "component_flow.csv", routed.component_flows, request["sourceTaskId"])
    write_json(folder / "progress.json", {"progress": 65, "stageLabel": "定位点位并执行原后端切分汇流 / 坡面诊断"})
    answer = engine.query(lon=longitude, lat=latitude, channel_width_m=point["channelWidthM"],
                          run_id=request["sourceTaskId"], rerun_output_dir=folder / "split-rerun")
    pairs = [(time, flow) for time, flow in zip(answer["series"]["time"], answer["series"]["flow_cms"])
             if metadata["startTime"] <= time.replace(" ", "T") <= metadata["endTime"]]
    if not pairs or any(not math.isfinite(flow) or flow < 0 for _, flow in pairs):
        raise ValueError("点位过程线缺失或出现无效流量")
    answer["series"].update(time=[time.replace(" ", "T") for time, _ in pairs], flow_cms=[flow for _, flow in pairs])
    if location:
        answer['location'] = location
    return {"pointQuery": answer, "sourceTaskId": request["sourceTaskId"], "warnings": [answer["quality"]["warning"], "诊断计算，不能用于生产预报。"]}


def formal_report(result, request):
    best = result["optimizer"]["best_parameters"]
    numbers = [*best.values(), result["optimizer"]["best_loss"],
               result["validationMetrics"].get("mean_NSE"), result["independentTest"].get("mean_NSE")]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in numbers):
        raise ValueError("诊断报告存在缺测或非有限评价，不能参与稳定性 / 边界选择")
    periods = request["period"].get("windows", []) if request["period"].get("mode") == "continuous" else request["period"]["events"]
    return {"split": [{"id": row["id"], "purpose": row["purpose"], "start": row["start"], "end": row["end"]} for row in periods],
            "common_parameter_set": best,
            "parameter_space": {"bounds": {row["name"]: [row["minimum"], row["maximum"]] for row in result["searchSpace"]}},
            "groups": {"VALIDATION": result["validationMetrics"], "INDEPENDENT_TEST": result["independentTest"]},
            "objective": {"loss": result["optimizer"]["best_loss"]}, "algorithm_config": request["calibration"]["optimizer"]}


def diagnostic_pet_note(result):
    return {
        'CONSTANT_ZERO_EXPLICIT_REQUEST': '蒸散发显式设为零。',
        'MONTHLY_CLIMATOLOGY_ASSUMPTION': '蒸散发采用逐月给定值。',
        'CONSTANT_ASSUMPTION': '蒸散发采用给定常数。',
        'HOURLY_CSV': '蒸散发采用已登记的逐小时资料。',
    }.get(result.get('petMode'), '蒸散发采用所选方案冻结的条件。')


def run_experiment(folder, request, publish_candidate=True):
    from worker import ModelJob
    from calibration_platform.production_validation import evaluate_multi_seed_stability, evaluate_wlm_boundary_experiment
    task_id = folder.name
    reports, outputs = {}, {}
    for index, value in enumerate(request["values"]):
        child = folder / f"JOB-experiment-{index + 1}"
        base = copy.deepcopy(request["baseRequest"])
        base["requestId"] = f"{task_id}-{index + 1}"
        if request["mode"] == "multiSeed":
            base["calibration"]["optimizer"]["seed"] = value
        else:
            base["calibration"]["wlmUpperBound"] = value
        write_json(child / "request.json", base)
        write_json(child / "status.json", {"taskId": child.name})
        write_json(folder / "progress.json", {"progress": 5 + 85 * index / len(request["values"]), "stageLabel": f"诊断实验 {index + 1}/{len(request['values'])} · {value}"})
        ModelJob(child, publish_parameters=False).run()
        result = read_json(child / "result.json")
        label = str(value)
        reports[label] = formal_report(result, base)
        outputs[label] = {"value": value, "taskId": child.name, "bestParameters": result["bestParameters"],
                          "optimizer": {key: result["optimizer"][key] for key in ("best_loss", "evaluations")},
                          "validationMetrics": result["validationMetrics"], "independentTest": result["independentTest"]}
    evaluation = evaluate_multi_seed_stability(reports) if request["mode"] == "multiSeed" else evaluate_wlm_boundary_experiment(reports)
    chosen = evaluation.get("representative_run", evaluation.get("selected_run"))
    candidate = outputs[chosen]["bestParameters"]
    version = f"PARAM-{task_id}"
    candidate_record = {"parameterVersionId": version, "taskId": task_id,
               "context": request["context"], "bestParameters": candidate, "lineage": request["lineage"],
               "resultSource": "model-backend", "productionEligible": False, "diagnosticEvaluation": evaluation,
               "calibrationMetrics": read_json(folder / outputs[chosen]["taskId"] / "result.json")["calibrationMetrics"],
               "validationMetrics": outputs[chosen]["validationMetrics"], "independentTest": outputs[chosen]["independentTest"]}
    chosen_result = read_json(folder / outputs[chosen]['taskId'] / 'result.json')
    candidate_record.update(science=chosen_result['science'], scienceSnapshot=chosen_result.get('scienceSnapshot'))
    candidate_record.update({key: chosen_result.get(key) for key in ('statePolicy', 'petMode', 'continuousOrigin', 'actualWarmupHours')})
    if chosen_result.get('bestRoutingParameters'):
        candidate_record['bestRoutingParameters'] = chosen_result['bestRoutingParameters']
        candidate_record['calibrationTarget'] = chosen_result['calibrationTarget']
        if publish_candidate:
            candidate_record['routingCandidate'] = RoutingVersions(RUNTIME).create({
                'name': f'诊断代表联合方案 · {task_id[-8:]}',
                'parentVersionId': request['routing']['versionId'], 'parameters': chosen_result['bestRoutingParameters']})
    if publish_candidate:
        write_json(RUNTIME / "parameters" / f"{version}.json", candidate_record)
    write_json(folder / "formal-diagnostic-reports.json", reports)
    return {"experiment": {"mode": request["mode"], "evaluation": evaluation, "runs": list(outputs.values())},
            "bestParameters": candidate, "parameterVersionId": version,
            **{key: candidate_record[key] for key in ('science', 'scienceSnapshot', 'routingCandidate', 'bestRoutingParameters', 'calibrationTarget', 'statePolicy', 'petMode', 'continuousOrigin', 'actualWarmupHours') if key in candidate_record},
            **{key: candidate_record[key] for key in ('calibrationMetrics', 'validationMetrics', 'independentTest')},
            "warnings": ["已执行参数稳定性与边界检验。", diagnostic_pet_note(chosen_result)]}


def run_diagnostic(folder):
    request = read_json(folder / "request.json")
    if request['runType'] == 'productionWorkflow':
        from workflow_jobs import run_workflow
        result = run_workflow(folder, request)
    else:
        result = run_point(folder, request) if request["runType"] == "pointQuery" else run_experiment(folder, request)
    result.update({"schemaVersion": "2.1", "taskId": folder.name, "context": request["context"],
                   "contextKey": request["context"]["contextKey"], "resultVersionId": f"RESULT-{folder.name}",
                   "resultSource": "model-backend", "productionEligible": False, "lineage": request["lineage"]})
    write_json(folder / "result.json", result)
