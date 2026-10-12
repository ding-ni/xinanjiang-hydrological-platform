"""The delivered calibration plan embedded in existing immutable candidates."""
from __future__ import annotations

import copy
import csv
from pathlib import Path

from calibration_groups import group_windows
from calibration_objectives import platform_objectives
from common import RUNTIME, read_json, safe_task
from science_context import digest_file, digest_json

SOURCE_INITIALIZATION = "SOURCE_INITIAL_CANDIDATES"
SOURCE_QC = "SOURCE_ORIGINAL_20261011"
TARGET_POLICY = "LEGACY_CLOSED_RING_VERTEX_MEAN"
OPERATIONS = {"search", "fixed-replay"}
GROUP_PURPOSE = {"CALIBRATION": "率定", "VALIDATION": "验证", "INDEPENDENT_TEST": "独立检验"}
PARAMETER_KEYS = ("KC", "WM", "WUM", "WLM", "B", "C", "SM", "EX", "KI", "KG", "IM")


def _csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _seal(value):
    value = copy.deepcopy(value)
    digest = digest_json(value)
    return {**value, "planId": "SOURCE-PLAN-" + digest, "planSHA256": digest}


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get("schemaVersion") != "1.0":
        raise ValueError("来源率定方案缺少完整配置")
    payload = {key: value for key, value in plan.items() if key not in {"planId", "planSHA256"}}
    digest = digest_json(payload)
    if plan.get("planSHA256") != digest or plan.get("planId") != "SOURCE-PLAN-" + digest:
        raise ValueError("来源率定方案摘要不一致")
    if group_windows(plan["sourceEvents"]) != plan["overlapGroups"]:
        raise ValueError("来源洪水用途与物理洪水组不一致")
    return plan


def effective_initial_candidates(parameters, routing, specs, extra):
    """Match the delivered CURRENT_ACTIVE_PARAMETERS seed before extra seeds."""
    from calibration_space import coordinates
    values = {'runoff.' + key: value for key, value in coordinates(parameters).items()}
    values['routing.muskingum_k_scale'] = 1.0
    for component in ('RS', 'RI', 'RG'):
        values['routing.' + component + '_k_hours'] = routing['default_slope_k_hours'][component]
    xs = {float(row['x']) for row in routing.get('reach_muskingum', {}).values()}
    if len(xs) == 1:
        values['routing.muskingum_x'] = xs.pop()
    seed = {row['name']: values[row['name']] for row in specs if row['name'] in values}
    if not isinstance(extra, list) or any(not isinstance(row, dict) for row in extra):
        raise ValueError('来源初始候选文件须为参数方案数组')
    return [{'source': 'CURRENT_ACTIVE_PARAMETERS', 'parameters': seed}, *copy.deepcopy(extra)]


def freeze_source_plan(folder, science, original_routing_version):
    """Read every original search dependency; no default is substituted."""
    folder = Path(folder)
    names = ("request.json", "resolved_request.json", "parameter_space_snapshot.json",
             "objective_config_snapshot.json", "event_quality_config_snapshot.json",
             "base_parameter_snapshot.json", "routing_parameter_snapshot.json",
             "initial_candidates.json", "qualification_config_snapshot.json", "data_review_config_snapshot.json")
    if any(not (folder / name).is_file() for name in names):
        raise ValueError("来源率定任务缺少原方案设置文件")
    source = {name: read_json(folder / name) for name in names}
    audit_paths = ('output/event_metrics.csv', 'output/event_quality.csv', 'output/overlap_group_metrics.csv')
    if any(not (folder / name).is_file() for name in audit_paths):
        raise ValueError('来源率定缺少逐场与洪水组评价记录')
    request = source["resolved_request.json"]
    space = source["parameter_space_snapshot.json"]
    profile = {"NANAN_JOINT_REFERENCE_MDB": "reference-16", "NANAN_JOINT_ALL_MDB": "all-physical-16"}.get(request.get("profile_id"))
    if profile is None or request.get("calibration_scope") != "RUNOFF_ROUTING_JOINT":
        raise ValueError("来源产汇流率定预设未登记")
    specs = space.get("parameters")
    if not isinstance(specs, list) or len(specs) != 16 or len({row.get("name") for row in specs}) != 16:
        raise ValueError("来源率定应包含 16 个独立计算变量")
    bounds = {scope: {} for scope in ("runoff", "routing")}
    for row in specs:
        scope, key = row["name"].split(".", 1)
        bounds[scope][key] = {key: row[key] for key in ("minimum", "maximum")}
    rows = _csv(folder / "output/event_metrics.csv")
    events = [{"id": row["event_id"], "start": row["start"], "end": row["end"],
               "purpose": GROUP_PURPOSE[row["group"]], "status": "active"} for row in rows]
    quality_rows = _csv(folder / "output/event_quality.csv")
    windows = [{"id": row["event_id"], "start": row["start"], "end": row["end"]} for row in quality_rows]
    accepted = {row["event_id"] for row in quality_rows if row["accepted"].strip().lower() == "true"}
    if accepted != {row["id"] for row in events} or not windows or len({row["id"] for row in windows}) != len(windows):
        raise ValueError("来源 QC 场次名单与率定报告不一致")
    initial = source["base_parameter_snapshot.json"]["parameters"]
    optimizer = {"algorithm": request["algorithm"], "strategy": "joint", "maxEvaluations": request["max_evaluations"],
                 "populationSize": request["population_size"], "generations": request["generations"],
                 "differentialWeight": request["differential_weight"], "crossoverProbability": request["crossover_probability"],
                 "seed": request["search_seed"], "initializationPolicy": SOURCE_INITIALIZATION,
                 "initialCandidates": source["initial_candidates.json"]}
    optimizer['effectiveInitialCandidates'] = effective_initial_candidates(
        initial, source['routing_parameter_snapshot.json'], specs, optimizer['initialCandidates'])
    state = read_json(folder / "state_config_snapshot.json")
    from datetime import datetime, timedelta
    first = min(datetime.fromisoformat(row["start"]) for row in windows)
    origin = (datetime(first.year, 1, 1) - timedelta(hours=state["fixed_warmup_hours"])).isoformat()
    quality_path = folder / "event_quality_config_snapshot.json"
    plan = {"schemaVersion": "1.0", "sourceTaskId": request["task_id"], "sourceProfileId": request["profile_id"],
            "name": "同事原率定方案", "sourceMDBSHA256": request["source_sha256"],
            "sourceFiles": {name: digest_file(folder / name) for name in names + audit_paths}, "sourceConfiguration": source,
            "requiredSpatialTargetPolicy": TARGET_POLICY, "requiredPrecipitationDtype": "float64",
            "sourceQcPolicy": SOURCE_QC, "qcConfigurationSHA256": digest_file(quality_path),
            "sourceAcceptedEventIds": [row["id"] for row in events],
            "sourceExcludedEventIds": [row["id"] for row in windows if row["id"] not in accepted],
            "sourceEvents": events, "sourceEventWindows": windows, "overlapGroups": group_windows(events),
            "sourceSplitSeed": request["split_seed"], "sourceSearchSeed": request["search_seed"],
            "sourceSplitRatios": {"calibration": request["calibration_ratio"], "validation": request["validation_ratio"]},
            "sourceSearchSpace": specs, "originalRoutingVersionId": original_routing_version,
            'effectiveInitialCandidates': optimizer['effectiveInitialCandidates'],
            "originalRoutingParameters": source["routing_parameter_snapshot.json"],
            "science": {key: science[key] for key in ("versionId", "configurationSHA256", "petConfigurationSHA256")},
            "actualWarmupHours": state["fixed_warmup_hours"], "continuousOrigin": origin,
            "calibration": {"target": "joint", "jointProfile": profile, "evaluationMode": "automatic",
                            "initialParameters": {key: initial[key] for key in PARAMETER_KEYS},
                            "searchParameters": bounds["runoff"], "routingSearchParameters": bounds["routing"],
                            "searchParameterOrder": [row["name"] for row in specs], "optimizer": optimizer,
                            "objectives": platform_objectives(source["objective_config_snapshot.json"]),
                            "statePolicy": "continuous-origin", "continuousOrigin": origin}}
    return validate_plan(_seal(plan))


def source_input(plan, products):
    matches = []
    for public in products.list_versions()["versions"]:
        if public.get("sourceQcPolicy") != SOURCE_QC:
            continue
        bundle = products.resolve(public["bundleVersionId"])
        manifest = bundle["manifest"]
        source_hash = manifest.get("databaseSHA256") or manifest.get("sourceOrigin", {}).get("databaseSHA256")
        if manifest.get("sourceId") != "SRC-MDB-" + plan["sourceMDBSHA256"] and source_hash != plan["sourceMDBSHA256"]:
            continue
        if (manifest.get("targetCoordinatePolicy") != TARGET_POLICY or manifest.get("forcingValueDtype") != "float64"
                or manifest.get("eventQualityConfigurationSHA256") != plan["qcConfigurationSHA256"]):
            continue
        if digest_file(bundle["paths"]["quality"]) != plan["qcConfigurationSHA256"]:
            raise ValueError("来源 QC 文件摘要与原方案不符")
        matches.append(bundle)
    if len(matches) != 1:
        raise ValueError("请先登记同 MDB、原插值点和原 QC 的来源计算资料")
    return matches[0]


def _record(parameter_id, runtime_root):
    record = read_json(safe_task(Path(runtime_root) / "parameters", parameter_id).with_suffix(".json"))
    if record.get("parameterVersionId") != parameter_id or record.get("candidateKind") not in {"frozen-calibration", "manual-edit"}:
        raise ValueError("请选择已经登记的来源候选")
    return record, validate_plan(record.get("originalCalibrationPlan"))


def _build(record, plan, bundle, operation):
    from catalog import case_catalog
    registered = {row["id"]: row for row in case_catalog(bundle)["cases"]["nananxi-xufan:1km"]["events"]}
    original = {row["id"]: row for row in plan["sourceEvents"]}
    windows = plan["sourceEvents"] if operation == "search" else plan["sourceEventWindows"]
    events = []
    for row in windows:
        event = copy.deepcopy(registered.get(row["id"]))
        if not event or event["start"] != row["start"] or event["end"] != row["end"]:
            raise ValueError("来源场次窗口与已登记资料不符")
        if row["id"] in original:
            event["purpose"] = original[row["id"]]["purpose"]
        else:
            overlaps = [group for group in plan["overlapGroups"] if row["start"] <= group["end"] and group["start"] <= row["end"]]
            purposes = {group["purpose"] for group in overlaps}
            if len(purposes) > 1:
                raise ValueError("来源补算窗口跨越不同用途的洪水组")
            event.update(purpose=next(iter(purposes), "独立检验"), sourceSupplementaryReplay=True)
        events.append(event)
    calibration = copy.deepcopy(plan["calibration"])
    routing, science = plan["originalRoutingVersionId"], plan["science"]
    if operation == "fixed-replay":
        calibration.update(target="runoff", evaluationMode="manual", initialParameters=copy.deepcopy(record["bestParameters"]),
                           optimizer={"algorithm": "FIXED_PARAMETERS", "strategy": "joint", "maxEvaluations": 1, "populationSize": 4, "seed": 0})
        for key in ("searchParameters", "routingSearchParameters", "searchParameterOrder"):
            calibration.pop(key, None)
        routing, science = record["routingCandidate"]["routingVersionId"], record["science"]
    request = {"runType": "calibration", "context": copy.deepcopy(record["context"]),
               "inputs": {"bundleVersionId": bundle["bundleVersionId"], "bundleManifestSHA256": bundle["manifestSHA256"],
                          "evaporation": {"mode": "science-version"}},
               "routing": {"versionId": routing}, "science": copy.deepcopy(science),
               "period": {"mode": "event", "warmupDays": plan["actualWarmupHours"] / 24, "events": events,
                          "eventSet": {"selectedEventIds": [row["id"] for row in events]}}, "calibration": calibration,
               "sourcePlan": {"parameterVersionId": record["parameterVersionId"], "planId": plan["planId"],
                              "planSHA256": plan["planSHA256"], "operation": operation}}
    request["period"]["overlapGroups"] = group_windows(events)
    return request


def build_original_request(parameter_id, runtime_root=None, operation="search", products=None):
    if operation not in OPERATIONS:
        raise ValueError("来源方案请选择恢复率定设置或固定参数重放")
    from input_products import InputProducts
    root = Path(runtime_root or RUNTIME)
    record, plan = _record(parameter_id, root)
    bundle = source_input(plan, products or InputProducts(runtime_root=root))
    return _build(record, plan, bundle, operation)


def original_plan_payload(parameter_id, runtime_root=None, products=None, operation="search"):
    from input_products import InputProducts
    from contracts import normalize_request
    from catalog import case_catalog
    root = Path(runtime_root or RUNTIME)
    record, plan = _record(parameter_id, root)
    registry = products or InputProducts(runtime_root=root)
    bundle = source_input(plan, registry)
    request = _build(record, plan, bundle, operation)
    request = normalize_request(request, case_catalog(bundle), runtime_root=root)
    return {"plan": {**plan, "requiredInputBundleVersionId": bundle["bundleVersionId"],
                     "bundleManifestSHA256": bundle["manifestSHA256"], "operation": operation}, "request": request}


def validate_original_request(request, runtime_root=None):
    """Every service and worker verifies the registered plan, never a client flag."""
    if not isinstance(request, dict):
        raise ValueError("请求必须是 JSON 对象")
    calibration = request.get("calibration") or {}
    optimizer = calibration.get("optimizer") or {}
    marker = request.get("sourcePlan")
    if marker is None:
        if optimizer.get("initializationPolicy") == SOURCE_INITIALIZATION:
            raise ValueError("原种群启动方式只能用于已经核验的来源率定方案")
        return copy.deepcopy(request)
    if not isinstance(marker, dict) or set(marker) != {"parameterVersionId", "planId", "planSHA256", "operation"}:
        raise ValueError("来源方案编号或摘要不完整")
    operation = marker["operation"]
    root = Path(runtime_root or RUNTIME)
    record, plan = _record(marker["parameterVersionId"], root)
    if marker["planId"] != plan["planId"] or marker["planSHA256"] != plan["planSHA256"] or operation not in OPERATIONS:
        raise ValueError("来源方案编号或摘要与登记记录不符")
    expected = build_original_request(marker["parameterVersionId"], root, operation)
    if request.get("runType") != "calibration" or request.get("context") != expected["context"]:
        raise ValueError("来源方案的任务类型或流域上下文不符")
    for key, identity in (("inputs", "bundleVersionId"), ("routing", "versionId"), ("science", "versionId")):
        if request.get(key, {}).get(identity) != expected[key][identity]:
            raise ValueError("来源方案的输入、汇流或科学版本被改变")
    from routing_versions import RoutingVersions
    selected_routing = RoutingVersions(root).resolve(request['routing']['versionId'])
    routing_expected = plan['originalRoutingParameters'] if operation == 'search' else record['bestRoutingParameters']
    if selected_routing['parameters'] != routing_expected:
        raise ValueError('来源方案的冻结河道 K 基准或候选汇流参数被改变')
    if request.get("inputs", {}).get("bundleManifestSHA256", expected["inputs"]["bundleManifestSHA256"]) != expected["inputs"]["bundleManifestSHA256"]:
        raise ValueError("来源方案的输入摘要被改变")
    if request.get("inputs", {}).get("evaporation") != expected["inputs"]["evaporation"]:
        raise ValueError("来源方案须使用冻结的蒸散发配置")
    period = request.get("period", {})
    if period.get("mode") != "event" or period.get("warmupDays") != expected["period"]["warmupDays"]:
        raise ValueError("来源方案的评价方式或预热期被改变")
    projection = lambda rows: sorted((row["id"], row["start"], row["end"], row["purpose"]) for row in rows
                                     if row.get("purpose") != "未使用" and row.get("status") != "inactive")
    if projection(period.get("events", [])) != projection(expected["period"]["events"]):
        raise ValueError("来源方案的场次窗口或用途被改变")
    if set(period.get("eventSet", {}).get("selectedEventIds", [])) != set(expected["period"]["eventSet"]["selectedEventIds"]):
        raise ValueError("来源方案的冻结场次名单被改变")
    for key, value in expected["calibration"].items():
        if key != "optimizer" and calibration.get(key) != value:
            raise ValueError("来源率定设置被改变：" + key)
    if operation == "search":
        expected_optimizer = expected["calibration"]["optimizer"]
        for key, value in expected_optimizer.items():
            if key not in {"maxEvaluations", "populationSize", "generations"} and optimizer.get(key) != value:
                raise ValueError("来源搜索设置被改变：" + key)
        # A smaller acceptance budget is explicit in the saved request and result.
        for key in ("maxEvaluations", "populationSize", "generations"):
            value = optimizer.get(key)
            if type(value) is not int or not 1 <= value <= expected_optimizer[key]:
                raise ValueError("来源搜索预算须为原预算或更小的试运行预算")
    elif optimizer != expected["calibration"]["optimizer"]:
        raise ValueError("来源重放必须使用固定参数，不进行新的寻优")
    result = copy.deepcopy(request)
    result.setdefault("lineage", {}).update(originalCalibrationPlanId=plan["planId"], originalCalibrationPlanSHA256=plan["planSHA256"])
    return result


def source_replay_exclusions(request, runtime_root=None):
    if request.get("sourcePlan", {}).get("operation") != "fixed-replay":
        return set()
    validate_original_request(request, runtime_root)
    _candidate, plan = _record(request["sourcePlan"]["parameterVersionId"], runtime_root or RUNTIME)
    return set(plan["sourceExcludedEventIds"])
