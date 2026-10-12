"""[OBJ-REG-01] Metric registry; adding a metric does not change calibration code."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from . import metrics


@dataclass(frozen=True)
class MetricDefinition:
    function: Callable
    direction: str
    default_scale: float


METRICS: Dict[str, MetricDefinition] = {
    "NSE": MetricDefinition(metrics.nse, "MAXIMIZE", 1.0),
    "KGE": MetricDefinition(metrics.kge, "MAXIMIZE", 1.0),
    "PBIAS": MetricDefinition(metrics.pbias, "MINIMIZE_ABS", 100.0),
    "RMSE": MetricDefinition(metrics.rmse, "MINIMIZE_ABS", 1.0),
    "PEAK_ERROR": MetricDefinition(metrics.peak_relative_error, "MINIMIZE_ABS", 100.0),
    "PEAK_TIME_ERROR": MetricDefinition(metrics.peak_time_error, "MINIMIZE_ABS", 24.0),
    "RUNOFF_DEPTH_ERROR": MetricDefinition(metrics.runoff_depth_error, "MINIMIZE_ABS", 100.0),
    "WATER_BALANCE_ERROR": MetricDefinition(metrics.water_balance_error, "MINIMIZE_ABS", 1.0),
}


def get_metric(name: str) -> MetricDefinition:
    key = name.upper()
    if key not in METRICS:
        raise KeyError(f"unknown objective metric: {name}")
    return METRICS[key]
