"""Validate the Vue job contract before queueing real model computation."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from common import MODEL, ROUTING_VERSION, clean
from catalog import PARAMETERS, ALGORITHMS

OPERATIONS = {"calibration": "eventCalibration", "simulation": "continuousSimulation", "stateInitialization": "stateInitialization", "stateContinuation": "stateContinuation", "forecast": "forecast"}


def finite_number(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}必须为有限数值")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label}必须为有限数值")
    if not math.isfinite(result):
        raise ValueError(f"{label}必须为有限数值")
    return result


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ValueError("必须提供完整的模型时刻")
    if result.tzinfo is not None or result.minute or result.second or result.microsecond:
        raise ValueError("模型时刻必须为本地整点，不接受隐式时区转换")
    return result


def continuous_windows(period):
    """Chronological, disjoint scoring windows; state policy is frozen separately."""
    windows = []
    for prefix, purpose in (("calibration", "率定"), ("validation", "验证"), ("independent", "独立检验")):
        start_value, end_value = period.get(prefix + "Start"), period.get(prefix + "End")
        if prefix == "independent" and not start_value and not end_value:
            continue
        start, end = timestamp(start_value), timestamp(end_value)
        if start >= end:
            raise ValueError(f"{purpose}期开始须早于结束")
        windows.append({"id": prefix.upper(), "purpose": purpose,
                        "start": start.isoformat(), "end": end.isoformat()})
    ordered = sorted(windows, key=lambda row: row["start"])
    if any(left["end"] >= right["start"] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("率定、验证与独立检验时段不能重叠（结束时刻计入本段）")
    if [row['id'] for row in ordered] != [row['id'] for row in windows]:
        raise ValueError('评价期须按率定、验证、独立检验的时间顺序排列')
    if period.get("initialStateVersionId"):
        raise ValueError("连续率定须为每组候选参数重新演算状态，不使用已有末状态")
    if period.get('manualProcessId'):
        windows = [row for row in windows if row['id'] == period['manualProcessId']]
        if not windows:
            raise ValueError('请选择有效的评价期')
    if "windows" in period and period["windows"] != windows:
        raise ValueError("冻结时段与率定设置不一致")
    return windows


def freeze_input_selection(request, products):
    """Resolve a registered immutable bundle before a job enters the queue.

    The bundle identity is distinct from the task's later inputManifestSHA256,
    which additionally covers selected annual files, code, and parent state.
    No global current/latest input exists; legacy requests use the fixed default.
    """
    if not isinstance(request, dict):
        raise ValueError("请求必须是 JSON 对象")
    for key in ("inputs", "lineage"):
        if key in request and not isinstance(request[key], dict):
            raise ValueError(f"{key} 必须为 JSON 对象")
    request = clean(request)
    inputs = request.setdefault("inputs", {})
    if set(inputs).intersection({"path", "root", "files", "precipitationDir", "modelRoot", "runtimeRoot", "url"}):
        raise ValueError("只能选择已登记的输入版本，不接受客户端资料路径")
    descriptor = products.resolve(inputs.get("bundleVersionId"))
    if descriptor.get('preparationOnly'):
        raise ValueError('请先按方案准备历史库输入；空间配置本身不能运行模型')
    version, digest = descriptor["bundleVersionId"], descriptor["manifestSHA256"]
    if "bundleManifestSHA256" in inputs and inputs["bundleManifestSHA256"] != digest:
        raise ValueError("请求输入版本摘要与后端登记不一致")
    inputs.update({"bundleVersionId": version, "bundleManifestSHA256": digest})
    lineage = request.setdefault("lineage", {})
    authoritative = {"inputBundleVersionId": version, "inputBundleManifestSHA256": digest,
                     "forcingVersion": descriptor["forcingVersion"],
                     "observationVersion": descriptor["observationVersion"]}
    for key, value in authoritative.items():
        if key in lineage and lineage[key] != value:
            raise ValueError(f"请求的 {key} 与已登记输入版本不一致")
    lineage.update(authoritative)
    return request, descriptor


def freeze_routing_selection(request, bundle, versions=None):
    from routing_versions import RoutingVersions
    selection = request.get("routing", {})
    if not isinstance(selection, dict) or set(selection) - {"versionId", "parametersSHA256"}:
        raise ValueError("只能选择已登记汇流版本，不接受路径或内联参数")
    registry = versions or RoutingVersions()
    version = registry.resolve(selection.get("versionId"))
    from joint_calibration import validate_routing
    if (request.get('calibration') or {}).get('optimizer', {}).get('initializationPolicy') == 'SOURCE_INITIAL_CANDIDATES':
        from original_calibration_plan import validate_original_request
        request = validate_original_request(request, registry.root.parent)
    else:
        validate_routing(version['parameters'])
    if selection.get("parametersSHA256", version["parametersSHA256"]) != version["parametersSHA256"]:
        raise ValueError("汇流参数摘要与登记版本不一致")
    paths = [bundle["paths"]["topology"], bundle["paths"]["mapping"],
             MODEL / "config/routing_parameters_control_split_v2.json"]
    digests = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    if version["routingVersionId"] != ROUTING_VERSION:
        digests[-1] = version["parametersSHA256"]
    digest = hashlib.sha256(json.dumps(digests).encode()).hexdigest()
    lineage = request.setdefault("lineage", {})
    values = {"routingVersionId": version["routingVersionId"], "routingManifestSHA256": digest}
    if any(key in lineage and lineage[key] != value for key, value in values.items()):
        raise ValueError("请求汇流版本或摘要与后端冻结配置不一致")
    lineage.update(values)
    request["routing"] = {"versionId": version["routingVersionId"], "parametersSHA256": version["parametersSHA256"]}
    if request.get('runType') == 'calibration' and isinstance(request.get('calibration'), dict):
        from routing_calibration import validate
        validate(request['calibration'], version['parameters'])
        from joint_calibration import validate as validate_joint
        validate_joint(request['calibration'], routing=version['parameters'])
    return request


def normalize_request(request, catalog, runtime_root=None):
    if not isinstance(request, dict):
        raise ValueError("请求必须是 JSON 对象")
    if request.get('sourcePlan') is not None or (request.get('calibration') or {}).get('optimizer', {}).get('initializationPolicy') == 'SOURCE_INITIAL_CANDIDATES':
        from original_calibration_plan import validate_original_request
        request = validate_original_request(request, runtime_root)
    for key in ("context", "period", "inputs", "lineage", "parameters"):
        if key in request and not isinstance(request[key], dict):
            raise ValueError(f"{key} 必须为 JSON 对象")
    if not isinstance(request.get("inputs", {}).get("evaporation", {}), dict):
        raise ValueError("inputs.evaporation 必须为 JSON 对象")
    if request.get("runType") == "calibration":
        if not isinstance(request.get("calibration"), dict):
            raise ValueError("率定任务必须提供 calibration 对象")
        for key in ("optimizer", "objectives", "initialParameters"):
            if not isinstance(request["calibration"].get(key), dict):
                raise ValueError(f"calibration.{key} 必须为 JSON 对象")
    context = request.get("context", {})
    case_key = f"{context.get('projectId')}:{context.get('resolutionId')}"
    case = catalog["cases"].get(case_key)
    if not case or not case["ready"]:
        raise ValueError("当前流域或尺度未接入真实模型；不能套用其他案例")
    run_type = request.get("runType")
    if "initialization" in request:
        initialization = request["initialization"]
        if run_type != "stateInitialization" or not isinstance(initialization, dict):
            raise ValueError("初始化方式仅用于状态初始化任务")
        if set(initialization) - {"strategy", "anchor"} or initialization.get("strategy") not in {"FIXED_WARMUP", "OBSERVED_FLOW_ANCHORED", "CONTINUOUS"}:
            raise ValueError("请选择降雨预热、实测流量初始化或从连续起点补算")
        if initialization.get("strategy") != "OBSERVED_FLOW_ANCHORED" and "anchor" in initialization:
            raise ValueError("仅实测流量初始化使用流量锚定条件")
        if initialization.get('strategy') == 'CONTINUOUS' and not request.get('science', {}).get('versionId'):
            raise ValueError('从连续起点补算须选择已登记的连续科学条件')
    operation = OPERATIONS.get(run_type)
    if run_type == "calibration" and request.get("period", {}).get("mode") == "continuous":
        operation = "continuousCalibration"
    if operation not in case["operations"]:
        raise ValueError(case["unsupported"].get(run_type, "当前任务类型尚未接入真实模型"))
    if context.get("contextKey") != "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km":
        raise ValueError("任务上下文与案例不一致")
    if context.get("modelConfigId") != "nananxi-xufan-1km":
        raise ValueError("模型配置与案例尺度不一致")
    request = clean(request)
    period = request.setdefault("period", {})
    from event_start import validate_event_start
    validate_event_start(request, runtime_root)
    if (run_type == 'calibration' and period.get('mode') == 'event'
            and not request.get('sourcePlan') and not request.get('lineage', {}).get('replayOfTaskId')):
        period.setdefault('warmupDays', 14)
    warmup = finite_number(period.get("warmupDays", 7), "预热期")
    minimum_warmup = 0 if period.get('initialStateVersionId') else 1
    if not math.isfinite(warmup) or warmup < minimum_warmup or warmup > 365:
        raise ValueError("预热期必须为 1 到 365 天")
    if run_type == "calibration":
        if period.get("mode") not in {"event", "continuous"}:
            raise ValueError("请选择洪水场次或连续时段率定")
        evaluation_mode = request['calibration'].get('evaluationMode', 'automatic')
        if 'simulationOnly' in request['calibration'] and (request['calibration']['simulationOnly'] is not True or evaluation_mode != 'manual' or period.get('mode') != 'event'):
            raise ValueError('场次模拟须使用指定参数与场次时段')
        if period.get('manualProcessId') and evaluation_mode != 'manual':
            raise ValueError('单评价期试算仅用于人工试调')
        if evaluation_mode not in {'automatic', 'manual'}:
            raise ValueError('请选择自动率定或人工试调')
        if evaluation_mode == 'manual':
            request['calibration']['optimizer'] = {'algorithm': 'FIXED_PARAMETERS', 'strategy': 'joint',
                                                   'maxEvaluations': 1, 'populationSize': 4, 'seed': 0}
        optimizer = request.get("calibration", {}).get("optimizer", {})
        algorithm = optimizer.get("algorithm")
        allowed_algorithms = {row['id'] for row in ALGORITHMS} | ({'FIXED_PARAMETERS'} if evaluation_mode == 'manual' else set())
        if algorithm not in allowed_algorithms:
            raise ValueError("后端不支持该率定算法，不能自动替换")
        count = optimizer.get("maxEvaluations")
        population = optimizer.get("populationSize")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
            raise ValueError("最大评价次数必须为 1 到 10000 的整数")
        if isinstance(population, bool) or not isinstance(population, int) or not 4 <= population <= 1000:
            raise ValueError("种群规模必须为 4 到 1000 的整数")
        if algorithm == "DIFFERENTIAL_EVOLUTION" and count < 4:
            raise ValueError("差分进化至少需要 4 次评价")
        for field, lo, hi in (('differentialWeight', 0, 2), ('crossoverProbability', 0, 1)):
            if field in optimizer:
                value = finite_number(optimizer[field], '差分进化设置')
                if not lo <= value <= hi or (field == 'differentialWeight' and value == 0):
                    raise ValueError('差分进化的差分权重或交叉概率超出范围')
        if 'generations' in optimizer and (type(optimizer['generations']) is not int or not 1 <= optimizer['generations'] <= 10000):
            raise ValueError('差分进化代数须为 1 到 10000 的整数')
        if optimizer.get('initializationPolicy') not in {None, 'REEVALUATE_CURRENT', 'SOURCE_INITIAL_CANDIDATES'}:
            raise ValueError('搜索起始方式未登记')
        seed = optimizer.get("seed", 42)
        if algorithm == 'SCE_UA':
            from calibration_search import sce_population_size
            sce_population_size(1, optimizer.get('complexes', 5))
            if optimizer.get('initializationPolicy') == 'SOURCE_INITIAL_CANDIDATES':
                raise ValueError('SCE-UA不适用于原方案原算法起始种群')
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
            raise ValueError("随机种子必须为 0 到 4294967295 的整数")
        strategy = optimizer.get("strategy", "joint")
        if strategy not in {"joint", "staged"}:
            raise ValueError("请选择联合搜索或分阶段搜索")
        minimum = 10 if algorithm == "DIFFERENTIAL_EVOLUTION" else 4
        if strategy == "staged" and count < minimum:
            raise ValueError(f"分阶段搜索至少需要 {minimum} 次评价（含各阶段起始方案）")
        if "wlmUpperBound" in request["calibration"]:
            upper = finite_number(request["calibration"]["wlmUpperBound"], "WLM 上界")
            if not 60 <= upper <= 200:
                raise ValueError("WLM 诊断上界必须在 60–200 mm 之间")
        if period["mode"] == "continuous":
            period["windows"] = continuous_windows(period)
            if request.get("inputs", {}).get("evaporation", {}).get("mode") not in {"monthly-climatology-assumption", "science-version"}:
                raise ValueError("连续率定须选用逐月给定蒸散发")
            # Event selections belong to event mode only, never to continuous lineage.
            for field in ("events", "eventSet", "eventAssignment"):
                period.pop(field, None)
            request.setdefault("lineage", {})["eventSetVersionId"] = None
        else:
            validate_event_period(period, request, case, runtime_root)
        objectives = request["calibration"].get("objectives", {})
        if evaluation_mode == 'manual':
            # A fixed simulation needs no search objective configuration. Keep
            # valid user metrics; incomplete search drafts cannot block it.
            from calibration_objectives import objective_terms
            try:
                objective_terms(objectives)
            except (ValueError, TypeError, AttributeError):
                objectives = {'metrics': {'nse': True}, 'weights': {'nse': 1}}
                request['calibration']['objectives'] = objectives
        for key in ("metrics", "weights"):
            if not isinstance(objectives.get(key), dict):
                raise ValueError(f"calibration.objectives.{key} 必须为 JSON 对象")
        if any(not isinstance(value, bool) for value in objectives["metrics"].values()):
            raise ValueError("评价指标开关必须为布尔值")
        active = [k for k, v in objectives.get("metrics", {}).items() if v]
        weights = objectives.get("weights", {})
        if not active or any(k not in {"nse", "kge", "peak", "time", "volume"} for k in active):
            raise ValueError("评价指标未登记")
        values = [finite_number(weights.get(k, 0), "权重") for k in active]
        if any(not math.isfinite(v) or v <= 0 for v in values) or abs(sum(values) - 1) > 0.01:
            raise ValueError("启用指标的权重必须为正且合计为 1")
        from calibration_objectives import objective_terms
        objective_terms(objectives)
    elif run_type == 'forecast':
        issue = request.get('issue')
        if not isinstance(issue, dict) or issue.get('mode') not in {'manual', 'automatic'}:
            raise ValueError('请提供预报起报条件')
        if issue.get('sectionId') != '70115800':
            raise ValueError('当前模型预报断面为徐畈（70115800）')
        issue_time = timestamp(issue.get('issueTime'))
        lead = issue.get('leadHours')
        if type(lead) is not int or not 1 <= lead <= 72:
            raise ValueError('预见期须为 1–72 小时的整数')
        start, end = issue_time + timedelta(hours=1), issue_time + timedelta(hours=lead)
        if timestamp(period.get('startTime')) != start or timestamp(period.get('endTime')) != end:
            raise ValueError('预报时段应为起报下一小时至预见期终点')
        if timestamp(issue.get('forecastEndTime')) != end:
            raise ValueError('预报终止时刻与预见期不一致')
        if not period.get('initialStateVersionId'):
            raise ValueError('预报须选择起报时刻的实测状态')
        if request['inputs'].get('evaporation', {}).get('mode') not in {'monthly-climatology-assumption', 'science-version'}:
            raise ValueError('预报沿用起报状态的逐月给定蒸散发方案')
        issue['issueTime'], issue['forecastEndTime'] = issue_time.isoformat(), end.isoformat()
        period['startTime'], period['endTime'] = start.isoformat(), end.isoformat()
    else:
        start = timestamp(period.get("startTime"))
        end = timestamp(period.get("endTime"))
        if end < start:
            raise ValueError("计算结束时刻不能早于起点")
        if request.get("inputs", {}).get("evaporation", {}).get("mode") not in {"monthly-climatology-assumption", "science-version"}:
            raise ValueError("连续计算必须显式确认逐月假设 PET；不能把它标注为实测蒸散发")
        if run_type == "stateContinuation" and not period.get("initialStateVersionId"):
            raise ValueError("状态续算必须指定父状态版本，不能自动改为重新初始化")
        if run_type == "stateInitialization" and period.get("initialStateVersionId"):
            raise ValueError("初始化不能携带父状态；请使用续算任务")
        if run_type == "stateInitialization" and start != end:
            raise ValueError("初始化应指定同一起算状态时刻")
        if period.get('continuousOrigin'):
            origin = timestamp(period['continuousOrigin'])
            if period.get('initialStateVersionId') or origin > start:
                raise ValueError('连续起点须早于计算起点，续算继承父状态不另设起点')
    params = request.get("calibration", {}).get("initialParameters", {}) if run_type == "calibration" else request.get("parameters", {})
    if set(params) - set(PARAMETERS):
        raise ValueError(f"模型参数未登记：{sorted(set(params) - set(PARAMETERS))}")
    for name, value in params.items():
        numeric = finite_number(value, f"参数 {name}")
        if not math.isfinite(numeric) or not PARAMETERS[name][1] <= numeric <= PARAMETERS[name][2]:
            raise ValueError(f"参数 {name} 超出边界")
    base = {**case["parameters"], **params}
    base = {k: finite_number(v, f"参数 {k}") for k, v in base.items()}
    if base["WM"] <= base["WUM"] + base["WLM"] or base["KI"] + base["KG"] >= 1:
        raise ValueError("参数违反 WM > WUM+WLM 或 KI+KG < 1 约束")
    if run_type == 'calibration':
        from routing_calibration import validate
        if request['calibration'].get('target', 'runoff') not in ('runoff', 'routing', 'joint'):
            raise ValueError('请选择产流、坡面汇流或产汇流联合率定')
        validate(request['calibration'])
        from calibration_space import validate_selection
        if request['calibration'].get('target') == 'joint':
            from joint_calibration import validate as validate_joint
            validate_joint(request['calibration'], runoff=base)
        else:
            validate_selection(request['calibration'], base)
        if optimizer['algorithm'] == 'SCE_UA':
            from calibration_search import sce_population_size
            from calibration_space import resolve_space
            from joint_calibration import resolve_space as joint_space
            from routing_calibration import resolve_space as routing_space
            settings = request['calibration']
            specs = joint_space(settings) if settings.get('target') == 'joint' else routing_space(settings) if settings.get('target') == 'routing' else resolve_space(settings)
            partitions = [specs]
            if optimizer.get('strategy') == 'staged':
                storage = {'WM', 'WUM', 'WLM', 'B', 'IM'}
                partitions = [[s for s in specs if s['name'].split('.')[-1] in storage], [s for s in specs if s['name'].split('.')[-1] not in storage]]
            budgets = [optimizer['maxEvaluations']] if len(partitions) == 1 else [optimizer['maxEvaluations']//2, optimizer['maxEvaluations']-optimizer['maxEvaluations']//2]
            for selected, budget in zip(partitions, budgets):
                required = sce_population_size(len(selected), optimizer.get('complexes', 5)) + 1
                if budget < required:
                    raise ValueError(f'SCE-UA当前{len(selected)}维搜索至少需要{required}次评价（每阶段含初值），当前{budget}次')
        state_policy = request['calibration'].get('statePolicy', 'independent-warmup')
        if state_policy not in {'independent-warmup', 'continuous-origin'}:
            raise ValueError('请选择按洪水组独立预热或从连续原点计算')
        if 'continuousOrigin' in request['calibration']:
            origin = timestamp(request['calibration']['continuousOrigin'])
            first = min(timestamp(row['start']) for row in period.get('windows', period.get('events', []))
                        if row.get('purpose') != '未使用' and row.get('status') != 'inactive')
            if state_policy != 'continuous-origin' or origin > first:
                raise ValueError('连续起点须早于首个评价时刻')
    if request.get('inputs', {}).get('evaporation', {}).get('mode') == 'science-version' and not request.get('science', {}).get('versionId'):
        raise ValueError('选择科学条件蒸散发时须指定已登记科学版本')
    return request


def validate_event_period(period, request, case, runtime_root=None):
        events = period.get("events", [])
        if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
            raise ValueError("period.events 必须为场次对象数组")
        event_set = period.get("eventSet", {})
        if not isinstance(event_set, dict) or not isinstance(event_set.get("selectedEventIds", []), list):
            raise ValueError("period.eventSet 必须包含 selectedEventIds 数组")
        if any(not isinstance(value, str) for value in event_set.get("selectedEventIds", [])):
            raise ValueError("冻结场次编号必须为字符串")
        rows = [row for row in events if row.get("purpose") != "未使用" and row.get("status") != "inactive"]
        if not rows or len(rows) > 500:
            raise ValueError("请选择 1 到 500 场洪水")
        known = {row["id"]: row for row in case["events"]}
        from original_calibration_plan import source_replay_exclusions
        replay_only = source_replay_exclusions(request, runtime_root) if request.get('sourcePlan', {}).get('operation') == 'fixed-replay' else set()
        seen = set()
        for row in rows:
            if not isinstance(row.get("id"), str) or row.get("id") in seen or row.get("id") not in known:
                raise ValueError("场次编号重复或未登记到后端资料目录")
            seen.add(row["id"])
            if known[row["id"]]["status"] != "active" and row['id'] not in replay_only:
                raise ValueError(f"场次 {row['id']} 资料未通过目录完整性检查")
            if row.get("purpose") not in {"率定", "验证", "独立检验"}:
                raise ValueError("场次用途无效")
            start, end = timestamp(row.get("start")), timestamp(row.get("end"))
            if start != timestamp(known[row["id"]]["start"]) or end != timestamp(known[row["id"]]["end"]):
                raise ValueError("场次窗口与已登记洪水资料不一致；请先登记修订版本")
            if start >= end:
                raise ValueError("场次开始必须早于结束")
        if request['calibration'].get('evaluationMode', 'automatic') != 'manual' and not any(row["purpose"] == "率定" for row in rows):
            raise ValueError("至少需要一场率定或人工试算洪水")
        if request['calibration'].get('evaluationMode', 'automatic') != 'manual' and not any(row["purpose"] == "验证" for row in rows):
            raise ValueError("自动率定至少需要一场验证洪水")
        selected = set(event_set.get("selectedEventIds", []))
        if selected != seen:
            raise ValueError("冻结场次清单与分组快照不一致")
        evaporation = request.get("inputs", {}).get("evaporation", {})
        if evaporation.get('mode') not in {'constant-zero', 'monthly-climatology-assumption', 'science-version'} or (evaporation.get("mode") == 'constant-zero' and evaporation.get("valueMmPerHour") != 0):
            raise ValueError("当前场次方案要求显式零 PET，不接受未登记的蒸散发方案")
        from calibration_groups import group_windows
        groups = group_windows(rows)
        if 'overlapGroups' in period and period['overlapGroups'] != groups:
            raise ValueError('冻结洪水组与登记窗口不一致')
        period['overlapGroups'] = groups
