"""Server-owned case/input registry. Browser URLs never become filesystem paths."""
from __future__ import annotations

import importlib.util
from routing_calibration import definitions as routing_search_definitions
from common import DEFAULT_INPUT_BUNDLE, MODEL, read_json

PARAMETERS = {
    "KC": ("蒸散发折算系数", 0, 3, "1"),
    "WM": ("总张力水容量", 10, 1000, "mm"),
    "WUM": ("上层张力水容量", 1, 150, "mm"),
    "WLM": ("下层张力水容量", 1, 600, "mm"),
    "B": ("蓄水容量曲线指数", 0, 3, "1"),
    "C": ("深层蒸散发系数", 0, 1, "1"),
    "SM": ("自由水蓄水容量", 0.1, 200, "mm"),
    "EX": ("自由水容量曲线指数", 0, 5, "1"),
    "KI": ("小时壤中流出流系数", 0, 0.999, "1/hour"),
    "KG": ("小时地下水出流系数", 0, 0.999, "1/hour"),
    "IM": ("不透水面积比例", 0, 0.999, "1"),
}
ALGORITHMS = [
    {"id": "DIFFERENTIAL_EVOLUTION", "label": "差分进化法", "note": "合作者模型中的真实差分进化搜索"},
    {"id": "RANDOM_SEARCH", "label": "随机搜索", "note": "有界、可复现随机搜索"},
    {"id": "GRID_SEARCH", "label": "网格搜索", "note": "受最大评价次数约束的参数网格搜索"},
]

PARAMETER_NOTES = {
    'KC': '将蒸散发输入折算为流域蒸散发需求；本模型采用 KC 符号。',
    'WM': '上层、下层与深层张力水容量之和，须大于 WUM + WLM。',
    'WUM': '上层土壤可蓄存的张力水容量。',
    'WLM': '下层土壤可蓄存的张力水容量；深层容量由 WM − WUM − WLM 确定。',
    'B': '描述流域内张力水容量的空间分布。',
    'C': '控制深层土壤水参与蒸散发的程度。',
    'SM': '控制自由水蓄存及地表、壤中、地下径流划分。',
    'EX': '描述流域内自由水容量的空间分布。',
    'KI': '自由水向壤中流的逐小时出流系数。',
    'KG': '自由水向地下水的逐小时出流系数。',
    'IM': '流域中不透水面积占总面积的比例。',
}


def case_catalog(bundle=None):
    from calibration_space import definitions
    from joint_calibration import definitions as joint_definitions
    from joint_calibration import profiles as joint_profiles
    from science_context import model_implementation_identity
    missing = [name for name in ("numpy", "pandas", "scipy", "xarray", "h5netcdf", "h5py", "pyproj", "shapely", "matplotlib", "openpyxl") if importlib.util.find_spec(name) is None]
    required = ["code", "config/parameter_set_land.json", "config/routing_parameters_control_split_v2.json", "config/initial_state_land.json", "config/pet_forcing_assumed_monthly.json", "config/event_quality_config.json", "data_interface/runoff_2017/grid_static.csv", "data_interface/event_catalog/nananxi_flood_event_catalog_qc_20260819.json", "data_interface/spatial/nan_an_topology_control_split_v2.json", "data_interface/precipitation_qc_20260819/1km_hourly", "data_interface/observations/xufan_outlet_hourly_qc.csv", "data_interface/spatial/grid_to_unit_weight_control_split_v2.csv"]
    missing += [p for p in required if not (MODEL / p).exists()]
    ready = not missing
    base = read_json(MODEL / "config/parameter_set_land.json")["parameters"]
    event_catalog = read_json(bundle["paths"]["events"] if bundle else MODEL / "data_interface/event_catalog/nananxi_flood_event_catalog_qc_20260819.json")
    events = []
    for row in event_catalog["packages"]:
        observed = row["observedInputs"]
        events.append({
            "id": row["eventId"], "eventId": row["eventId"], "basinProjectId": "nananxi-xufan",
            "name": row["eventId"], "sourceFloodNo": row.get("floodCode"), "start": row["start"], "end": row["end"],
            "observedPeak": observed.get("observedPeakFlowM3s"), "peakTime": observed.get("observedPeakTime"),
            "volumeM3": observed.get("volumeM3", observed.get("riverVolumeM3")), "sectionId": "70115800",
            "sourceFiles": {"rain": bundle["forcingVersion"] if bundle else "precipitation_qc_20260819/1km_hourly",
                            "river": bundle["observationVersion"] if bundle else "observations/xufan_outlet_hourly_qc.csv"},
            "rainCompleteness": observed.get("rainCompleteness"), "flowCompleteness": observed.get("riverCompleteness"),
            "status": "active" if row["status"] == "ready" else "pending", "revision": 1,
            "source": bundle["modelInputVersion"] if bundle else "model-qc-20260819", "purpose": "未使用",
            "bundleVersionId": bundle["bundleVersionId"] if bundle else DEFAULT_INPUT_BUNDLE,
        })
    case = {
        "basinId": "nananxi-xufan", "resolutionId": "1km", "ready": ready,
        "mode": "model-backend", "operations": ["eventCalibration", "continuousCalibration", "continuousSimulation", "stateInitialization", "stateContinuation", "forecast"] if ready else [],
        "manualCalibration": ready,
        "searchParameterDefinitions": definitions(),
        "routingSearchParameterDefinitions": routing_search_definitions(),
        "jointSearchParameterDefinitions": joint_definitions(),
        "jointSearchProfiles": joint_profiles(),
        "calibrationTargets": ['runoff', 'routing', 'joint'],
        "calibrationEvaluationPolicy": 'EQUAL_PHYSICAL_GROUP_UNIQUE_HOURS',
        "scienceVersionsService": '/api/model/science-versions',
        "modelImplementationSHA256": model_implementation_identity()['modelImplementationSHA256'],
        "checkpointImport": ready,
        "unsupported": {},
        "forecastRainService": "/api/model/forecast-rain",
        "missingInputs": missing, "reason": "真实产汇流计算服务已接入" if ready else "模型输入或运行依赖缺失",
        "parameterVersionId": "NANANXI_XAJ_REFERENCE_V1", "parameters": {k: base[k] for k in PARAMETERS},
        "parameterDefinitions": [{"key": k, "name": k, "symbol": k, "label": v[0], "desc": PARAMETER_NOTES[k], "min": v[1], "max": v[2], "step": 0.0001 if k in ("KI", "KG") else 0.001 if k == 'IM' else 0.01, "unit": v[3], "default": base[k]} for k, v in PARAMETERS.items()],
        "algorithms": ALGORITHMS, "events": events, "defaultWarmupDays": 7, "defaultMaxEvaluations": 120,
        "forcingVersion": bundle["forcingVersion"] if bundle else "precipitation_qc_20260819",
        "observationVersion": bundle["observationVersion"] if bundle else "xufan_outlet_hourly_qc_20260819",
        "inputBundleVersionId": bundle["bundleVersionId"] if bundle else DEFAULT_INPUT_BUNDLE,
        "defaultInputBundleVersionId": DEFAULT_INPUT_BUNDLE,
        "inputProductsService": "/api/model/input-products",
        "inputServiceReady": ready, "inputService": "/api/model/inputs",
        "diagnostics": ["qcInputWindow", "controlArealRainfall", "eventQuality", "savedGridStateHistory", "routingVersions", "pointQuery", "diagnosticExperiment", "productionWorkflow"] if ready else [],
        "routingResolutionM": 30, "routingMethod": "LINEAR_RESERVOIR_MUSKINGUM_CONTROL_SPLIT_V2",
        "defaultSimulationPeriod": {"start": "2017-06-19T11:00", "end": "2017-06-25T08:00"},
        "productionEligible": False,
        "initializationStrategies": [{"id": "FIXED_WARMUP", "label": "降雨预热"},
                                     {"id": "OBSERVED_FLOW_ANCHORED", "label": "按实测流量初始化"}],
        "qualityNotes": ["地图与计算采用同一输入版本的网格雨量和出口流量。", "历史站点雨量、水位和流量由水文资料库管理。", "场次蒸散发为 0 mm/h；连续模拟与连续率定采用逐月给定值。", "当前模型为液态降雨产汇流模型。"],
        "modules": [
            {"id": "FORECAST", "label": "降雨情景预报", "status": "integrated", "reason": "逐小时给定降雨、起报状态衔接、真实产汇流计算及预报成果保存。"},
            {"id": "SPATIAL-EXPORT", "label": "地形河网与汇流分区成果", "status": "integrated", "reason": "原始 30 m DEM、D8 河网、控制断面切分、分区栅格、PNG 图件和 GIS 成果包。"},
            {"id": "CONTINUOUS-CALIBRATION", "label": "连续与分阶段率定", "status": "integrated", "reason": "独立预热的率定、验证和独立检验时段；联合搜索或蓄水产流—水源划分两阶段搜索。"},
            {"id": "QC-INPUTS", "label": "整编输入、网格雨量与控制区面雨量", "status": "integrated", "reason": "地图与模型采用同一输入版本和面积权重。"},
            {"id": "DQ-EVENT-QC-02", "label": "洪水场次物理质控", "status": "integrated", "reason": "可独立查询登记场次，也在率定执行中验证。"},
            {"id": "STATE-REPLAY", "label": "逐网格逐时状态回放", "status": "integrated", "reason": "连续模拟和状态任务保存逐小时过程及末状态检查点。"},
            {"id": "STATE-MANAGEMENT", "label": "起算状态管理", "status": "integrated", "reason": "降雨预热、实测流量锚定、命名收藏、检查点导入导出与续算。"},
            {"id": "RESULT-MANAGEMENT", "label": "计算成果整理", "status": "integrated", "reason": "分组、收藏、备注、三组评价期过程、同条件曲线比较及汇总导出。"},
            {"id": "DQ-PRECIP-IDW-01", "label": "资料检查与降雨空间插值", "status": "integrated", "reason": "支持整编时段提取、站点雨量距离反比加权插值及计算资料版本登记。"},
            {"id": "POINT-FLOW", "label": "任意点流量与临时控制断面", "status": "integrated", "reason": "复用模拟产流，河道点按拓扑切分汇流，坡面点计算估算流量。"},
            {"id": "ROUTING-PARAMETERS", "label": "坡面与河网汇流参数", "status": "integrated", "reason": "全局坡面、单元坡面及河段参数可编辑、登记、选用和导出。"},
            {"id": "SPATIAL-TOPOLOGY", "label": "拓扑与网格空间产品", "status": "integrated", "reason": "32 河段、110 网格、汇流单元和控制区面积权重。"},
            {"id": "MDB-MAPPING", "label": "MDB 独立资料登记", "status": "integrated", "reason": "只读副本查询、同站同小时冲突检查、严格缺测和单独输入版本；旧资料不自动切换。"},
            {"id": "JOINT-CALIBRATION", "label": "产流、坡面与河道联合率定", "status": "integrated", "reason": "14/16变量方案、当前基线实际评价、物理洪水组去重小时目标。"},
            {"id": "SCIENCE-CONTEXT", "label": "科学条件冻结", "status": "integrated", "reason": "PET、初态、状态策略和实现摘要；参数、汇流与科学条件联合选用。"},
            {"id": "PRODUCTION-CALIBRATION-10", "label": "参数稳定性与多年水量平衡", "status": "integrated", "reason": "边界比较、多随机种子搜索、连续多年水量平衡和评价判据。"},
        ],
    }
    return {"schemaVersion": "2.0", "service": "unified-xaj-platform", "cases": {"nananxi-xufan:1km": case}, "resultSource": "model-backend"}
