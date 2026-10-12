"""[OBJ-METRIC-01] Independent hydrological objective metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _aligned(observed, simulated):
    obs = pd.Series(observed, dtype=float)
    sim = pd.Series(simulated, dtype=float)
    if isinstance(observed, pd.Series) and isinstance(simulated, pd.Series):
        obs, sim = observed.align(simulated, join="inner")
    mask = np.isfinite(obs.to_numpy()) & np.isfinite(sim.to_numpy())
    return obs.to_numpy()[mask], sim.to_numpy()[mask]


def nse(observed, simulated, **_):
    obs, sim = _aligned(observed, simulated)
    denominator = np.sum((obs - np.mean(obs)) ** 2)
    return float(1.0 - np.sum((sim - obs) ** 2) / denominator) if denominator > 0 else float("nan")


def kge(observed, simulated, **_):
    obs, sim = _aligned(observed, simulated)
    if len(obs) < 2 or np.mean(obs) == 0 or np.std(obs) == 0:
        return float("nan")
    correlation = np.corrcoef(obs, sim)[0, 1] if np.std(sim) > 0 else 0.0
    variability = np.std(sim) / np.std(obs)
    bias = np.mean(sim) / np.mean(obs)
    return float(1.0 - np.sqrt((correlation - 1) ** 2 + (variability - 1) ** 2 + (bias - 1) ** 2))


def pbias(observed, simulated, **_):
    obs, sim = _aligned(observed, simulated)
    denominator = np.sum(obs)
    return float(100.0 * np.sum(sim - obs) / denominator) if denominator != 0 else float("nan")


def rmse(observed, simulated, **_):
    obs, sim = _aligned(observed, simulated)
    return float(np.sqrt(np.mean((sim - obs) ** 2))) if len(obs) else float("nan")


def peak_relative_error(observed, simulated, **_):
    obs, sim = _aligned(observed, simulated)
    peak = np.max(obs) if len(obs) else float("nan")
    return float(100.0 * (np.max(sim) - peak) / peak) if peak > 0 else float("nan")


def peak_time_error(observed, simulated, **_):
    obs, sim = _aligned(observed, simulated)
    return float(abs(int(np.argmax(sim)) - int(np.argmax(obs)))) if len(obs) else float("nan")


def runoff_depth_error(observed, simulated, **_):
    obs, sim = _aligned(observed, simulated)
    denominator = np.sum(obs)
    return float(100.0 * (np.sum(sim) - denominator) / denominator) if denominator != 0 else float("nan")


def water_balance_error(_observed, _simulated, diagnostics=None, **_):
    if diagnostics is None or "water_balance_error" not in diagnostics:
        raise ValueError("WATER_BALANCE_ERROR requires diagnostics.water_balance_error")
    values = pd.to_numeric(pd.Series(diagnostics["water_balance_error"]), errors="coerce")
    return float(values.abs().sum())
