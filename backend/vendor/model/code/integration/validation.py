"""INTEGRATION-QC: structural and hydrologic diagnostics for integrated output."""

from __future__ import annotations

import csv
import math
from pathlib import Path


def _nse(observed: list[float], simulated: list[float]) -> float | None:
    mean_observed = sum(observed) / len(observed)
    denominator = sum((value - mean_observed) ** 2 for value in observed)
    if denominator == 0.0:
        return None
    return 1.0 - sum((obs - sim) ** 2 for obs, sim in zip(observed, simulated)) / denominator


def _rmse(observed: list[float], simulated: list[float]) -> float:
    return math.sqrt(sum((obs - sim) ** 2 for obs, sim in zip(observed, simulated)) / len(observed))


def compare_control_flow(observed_path: str | Path, simulated_path: str | Path) -> dict:
    observed = {
        row["time"]: float(row["observed_flow_m3_s"])
        for row in csv.DictReader(Path(observed_path).open("r", encoding="utf-8-sig"))
    }
    simulated = {
        row["time"]: float(row["outflow_cms"])
        for row in csv.DictReader(Path(simulated_path).open("r", encoding="utf-8-sig"))
    }
    common_times = sorted(set(observed) & set(simulated))
    obs_values = [observed[time] for time in common_times]
    sim_values = [simulated[time] for time in common_times]
    observed_sum = sum(obs_values)
    return {
        "paired_hour_count": len(common_times),
        "nse": _nse(obs_values, sim_values),
        "rmse_m3_s": _rmse(obs_values, sim_values),
        "pbias_percent": 100.0 * (sum(sim_values) - observed_sum) / observed_sum,
        "observed_peak_m3_s": max(obs_values),
        "simulated_peak_m3_s": max(sim_values),
        "peak_relative_error_percent": 100.0 * (max(sim_values) - max(obs_values)) / max(obs_values),
        "note": "Diagnostic only: routing parameters and initial routing state are not jointly calibrated.",
    }
