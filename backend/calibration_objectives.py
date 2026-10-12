"""Platform names for the delivered configurable hydrological objectives."""
from __future__ import annotations

import math


METRICS = {"nse": "NSE", "kge": "KGE", "peak": "PEAK_ERROR", "time": "PEAK_TIME_ERROR"}
WATER_BALANCE = {"PBIAS", "VOLUME_ERROR"}


def objective_terms(criteria):
    """Preserve old volume defaults while naming the original PBIAS explicitly."""
    water = criteria.get("waterBalanceMetric", "VOLUME_ERROR")
    if water not in WATER_BALANCE:
        raise ValueError("水量评价请选择相对水量偏差")
    names = {**METRICS, "volume": water}
    active = [key for key, enabled in criteria.get("metrics", {}).items() if enabled]
    if not active or any(key not in names for key in active):
        raise ValueError("评价指标未登记")
    terms = []
    for key in active:
        weight = criteria.get("weights", {}).get(key, 0)
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
            raise ValueError("启用指标的权重必须为正且合计为 1")
        term = {"metric": names[key], "weight": weight}
        if key not in {"nse", "kge"}:
            term.update(scale=24.0 if key == "time" else 100.0, target=0.0)
        terms.append(term)
    if abs(sum(row["weight"] for row in terms) - 1) > 0.01:
        raise ValueError("启用指标的权重必须为正且合计为 1")
    return terms


def platform_objectives(source):
    reverse = {value: key for key, value in METRICS.items()}
    reverse.update(PBIAS="volume", VOLUME_ERROR="volume")
    terms = source.get("terms")
    if source.get("method") != "WEIGHTED_SUM" or not isinstance(terms, list) or not terms:
        raise ValueError("来源率定目标配置不完整")
    metrics, weights, water = {}, {}, "VOLUME_ERROR"
    for term in terms:
        metric = term.get("metric")
        if metric not in reverse or reverse[metric] in metrics:
            raise ValueError("来源率定目标含未登记或重复指标")
        key = reverse[metric]
        if key not in {"nse", "kge"} and (term.get("target", 0.0) != 0 or term.get("scale", 24 if key == "time" else 100) != (24 if key == "time" else 100)):
            raise ValueError("来源率定目标的尺度或目标值未登记")
        metrics[key], weights[key] = True, term["weight"]
        if key == "volume":
            water = metric
    result = {"metrics": metrics, "weights": weights, "waterBalanceMetric": water}
    objective_terms(result)
    return result
