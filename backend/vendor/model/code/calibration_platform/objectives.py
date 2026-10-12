"""CAL-OBJECTIVE: metric calculation independent of the routing evaluator."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np


def _aligned(observed: dict[datetime, float], simulated: dict[datetime, float]):
    times = sorted(set(observed) & set(simulated))
    obs = np.asarray([observed[t] for t in times], dtype=float)
    sim = np.asarray([simulated[t] for t in times], dtype=float)
    mask = np.isfinite(obs) & np.isfinite(sim)
    return obs[mask], sim[mask], len(times)


def metric(metric_name: str, observed: dict[datetime, float], simulated: dict[datetime, float]) -> float:
    obs, sim, _ = _aligned(observed, simulated)
    name = metric_name.upper()
    if not len(obs):
        return float("nan")
    if name == "NSE":
        denominator = np.sum((obs - np.mean(obs)) ** 2)
        return float(1.0 - np.sum((sim - obs) ** 2) / denominator) if denominator else float("nan")
    if name == "KGE":
        if len(obs) < 2 or np.mean(obs) == 0 or np.std(obs) == 0:
            return float("nan")
        correlation = np.corrcoef(obs, sim)[0, 1] if np.std(sim) else 0.0
        variability = np.std(sim) / np.std(obs)
        bias = np.mean(sim) / np.mean(obs)
        return float(1.0 - np.sqrt((correlation - 1) ** 2 + (variability - 1) ** 2 + (bias - 1) ** 2))
    if name in {"PBIAS", "RUNOFF_DEPTH_ERROR"}:
        return float(100.0 * np.sum(sim - obs) / np.sum(obs)) if np.sum(obs) else float("nan")
    if name == "RMSE":
        return float(np.sqrt(np.mean((sim - obs) ** 2)))
    if name in {"PEAK_ERROR", "PEAK_RELATIVE_ERROR"}:
        return float(100.0 * (np.max(sim) - np.max(obs)) / np.max(obs)) if np.max(obs) else float("nan")
    if name in {"PEAK_TIME_ERROR", "PEAK_TIME_ERROR_HOURS"}:
        return float(abs(int(np.argmax(sim)) - int(np.argmax(obs))))
    raise ValueError(f"unsupported calibration metric: {metric_name}")


def evaluate_objective(
    observed: dict[datetime, float],
    simulated: dict[datetime, float],
    objective_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    if not objective_specs:
        raise ValueError("objective_specs must not be empty")
    weight_sum = sum(float(spec.get("weight", 1.0)) for spec in objective_specs)
    if weight_sum <= 0:
        raise ValueError("objective weights must sum to a positive value")
    terms = {}
    score = 0.0
    for spec in objective_specs:
        name = str(spec["metric"]).upper()
        raw = metric(name, observed, simulated)
        if not math.isfinite(raw):
            utility = -1.0e6
        elif name in {"NSE", "KGE"}:
            utility = raw
        else:
            scale = float(spec.get("scale") or (24.0 if "TIME" in name else 100.0))
            target = float(spec.get("target", 0.0))
            if scale <= 0:
                raise ValueError(f"objective scale must be positive: {name}")
            utility = -abs(raw - target) / scale
        weight = float(spec.get("weight", 1.0))
        score += weight * utility
        terms[name] = {"raw": raw, "utility": utility, "weight": weight}
    score /= weight_sum
    return {"score": score, "loss": -score, "terms": terms}
