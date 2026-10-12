"""[API-SCHEMA-01] Parameter metadata used to build frontend forms."""
from __future__ import annotations

from dataclasses import asdict

from ..core.types import GlacierParams, LandParams, NUMERICAL, PHYSICAL


LAND_PARAMETER_SCHEMA = [
    {"name": "KC", "label": "蒸散发折算系数", "unit": "1", "minimum": 0.4, "maximum": 1.5, "calibratable": True},
    {"name": "WM", "label": "流域平均张力水容量", "unit": "mm", "minimum": 50, "maximum": 300, "calibratable": True},
    {"name": "WUM", "label": "上层张力水容量", "unit": "mm", "minimum": 5, "maximum": 80, "calibratable": True},
    {"name": "WLM", "label": "下层张力水容量", "unit": "mm", "minimum": 20, "maximum": 200, "calibratable": True},
    {"name": "C", "label": "深层蒸散发系数", "unit": "1", "minimum": 0.05, "maximum": 0.5, "calibratable": True},
    {"name": "B", "label": "张力水容量曲线指数", "unit": "1", "minimum": 0.05, "maximum": 0.6, "calibratable": True},
    {"name": "SM", "label": "自由水蓄水容量", "unit": "mm", "minimum": 5, "maximum": 100, "calibratable": True},
    {"name": "EX", "label": "自由水容量曲线指数", "unit": "1", "minimum": 0.5, "maximum": 2.5, "calibratable": True},
    {"name": "KI", "label": "壤中流出流系数", "unit": "1/hour", "minimum": 0.003, "maximum": 0.06, "calibratable": True, "time_step_dependent": True},
    {"name": "KG", "label": "地下径流出流系数", "unit": "1/hour", "minimum": 0.003, "maximum": 0.06, "calibratable": True, "time_step_dependent": True},
    {"name": "IM", "label": "不透水面积比例", "unit": "fraction", "minimum": 0, "maximum": 0.1, "calibratable": True},
    {"name": "CI", "label": "壤中流消退系数（汇流）", "unit": "1", "minimum": 0, "maximum": 1, "calibratable": False, "owner": "ROUTING"},
    {"name": "CG", "label": "地下径流消退系数（汇流）", "unit": "1", "minimum": 0, "maximum": 1, "calibratable": False, "owner": "ROUTING"},
    {"name": "KE", "label": "马斯京根河段数（汇流）", "unit": "1", "minimum": 1, "maximum": 20, "calibratable": False, "owner": "ROUTING"},
    {"name": "XE", "label": "马斯京根权重（汇流）", "unit": "1", "minimum": 0, "maximum": 0.5, "calibratable": False, "owner": "ROUTING"},
]

GLACIER_PARAMETER_SCHEMA = [
    {"name": "snow.snow_ddf", "label": "融雪度日因子", "unit": "mm/degC/day", "minimum": 1, "maximum": 10, "calibratable": True},
    {"name": "ice_ddf", "label": "融冰度日因子", "unit": "mm/degC/day", "minimum": 2, "maximum": 15, "calibratable": True},
    {"name": "snow.liquid_capacity_ratio", "label": "雪层液态持水率", "unit": "fraction", "minimum": 0.01, "maximum": 0.15, "calibratable": True},
    {"name": "reservoir_k", "label": "冰川临时水库小时出流系数", "unit": "1/hour", "minimum": 0.001, "maximum": 0.5, "calibratable": True, "time_step_dependent": True},
    {"name": "ice_sublimation_fraction_pet", "label": "裸冰升华PET比例", "unit": "fraction", "minimum": 0, "maximum": 1, "calibratable": True},
]

LAND_SOURCE_CALIBRATION_PARAMETERIZATIONS = {
    "DIRECT_HOURLY": ["KI", "KG"],
    "DAILY_TOTAL_AND_SPLIT": [
        {
            "name": "K_total_day",
            "label": "日总自由水出流系数",
            "unit": "1/day",
            "minimum": 0.0,
            "maximum": 0.99,
        },
        {
            "name": "groundwater_fraction",
            "label": "地下径流分配比例",
            "unit": "fraction",
            "minimum": 0.0,
            "maximum": 1.0,
        },
    ],
}


def get_parameter_schema(zone_type: str):
    zone = zone_type.upper()
    if zone == "NON_GLACIER":
        defaults, schema = asdict(LandParams()), LAND_PARAMETER_SCHEMA
    elif zone == "GLACIER":
        defaults, schema = asdict(GlacierParams()), GLACIER_PARAMETER_SCHEMA
    else:
        raise ValueError("zone_type must be GLACIER or NON_GLACIER")
    constraints = ["WM >= WUM + WLM", "KI + KG <= 1"] if zone == "NON_GLACIER" else []
    return {"zone_type": zone, "defaults": defaults, "parameters": schema, "constraints": constraints}


def get_parameter_catalog(zone_type: str):
    catalog = get_parameter_schema(zone_type)
    catalog.update({
        "parameter_defaults": catalog["defaults"],
        "runoff_model": "XAJ_SATURATION_EXCESS" if catalog["zone_type"] == "NON_GLACIER" else "GLACIER",
        "melt_method_options": ["degree_day", "energy_balance"],
        "simulation_mode_options": ["SINGLE", "CONTINUOUS", "EVENT"],
        "calibration_algorithm_options": ["GRID_SEARCH", "RANDOM_SEARCH", "DIFFERENTIAL_EVOLUTION"],
        "objective_metric_options": ["NSE", "KGE", "PBIAS", "RMSE", "PEAK_ERROR", "PEAK_TIME_ERROR", "RUNOFF_DEPTH_ERROR", "WATER_BALANCE_ERROR"],
        "units": {"water_flux": "mm/h", "water_storage": "mm", "temperature": "degC", "radiation": "W/m2", "slope": "degree", "area": "km2"},
        "physical_constants": asdict(PHYSICAL),
        "numerical_settings": asdict(NUMERICAL),
    })
    if catalog["zone_type"] == "NON_GLACIER":
        catalog["source_calibration_parameterizations"] = LAND_SOURCE_CALIBRATION_PARAMETERIZATIONS
    return catalog
