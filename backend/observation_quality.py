"""Observation coverage and explicitly masked volumes, without imputation.

Existing bundles that omit ``observation_coverage`` retain their old acceptance
policy while receiving the same diagnostics. New MDB bundles opt in explicitly.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta


DEFAULT_OBSERVATION_POLICY = {
    "minimum_observed_coverage": 0.8,
    "maximum_missing_gap_hours": 48,
    "minimum_phase_coverage": 0.8,
    "peak_window_hours": 6,
}


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _times(values):
    result = [datetime.fromisoformat(str(value)) if not isinstance(value, datetime) else value for value in values]
    if not result or any(value.tzinfo is not None or value.minute or value.second or value.microsecond for value in result):
        raise ValueError("观测评价时轴须为无时区的连续整小时")
    if any(last - first != timedelta(hours=1) for first, last in zip(result, result[1:])):
        raise ValueError("观测评价时轴须连续、递增且唯一")
    return result


def resolve_observation_policy(config=None, enforce=None):
    config = config or {}
    declared = config.get("observation_coverage")
    if declared is None and any(key in config for key in DEFAULT_OBSERVATION_POLICY):
        declared = config
    if declared is not None and not isinstance(declared, dict):
        raise ValueError("observation_coverage 须为配置对象")
    policy = {**DEFAULT_OBSERVATION_POLICY, **(declared or {})}
    enabled = bool(declared is not None) if enforce is None else enforce
    if not isinstance(enabled, bool):
        raise ValueError("观测覆盖门槛 enforce 须为布尔值")
    if "enforce" in policy:
        if not isinstance(policy["enforce"], bool):
            raise ValueError("观测覆盖门槛 enforce 须为布尔值")
        enabled = policy["enforce"] if enforce is None else enforce
    for name in ("minimum_observed_coverage", "minimum_phase_coverage"):
        value = policy[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(name + " 须为0到1的有限数值")
    for name in ("maximum_missing_gap_hours", "peak_window_hours"):
        value = policy[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(name + " 须为非负整数")
    policy["enforce"] = enabled
    policy["compatibility"] = "EXPLICIT_POLICY" if declared is not None else "HISTORICAL_REPORT_ONLY"
    return policy


def evaluate_observation_coverage(times, observed, config=None, enforce=None):
    times = _times(times)
    values = [_number(value) for value in observed]
    if len(values) != len(times):
        raise ValueError("观测流量与评价时轴长度不一致")
    policy = resolve_observation_policy(config, enforce)
    valid = [value is not None for value in values]
    missing_runs, start = [], None
    for index, good in enumerate(valid):
        if not good and start is None:
            start = index
        if start is not None and (good or index == len(valid) - 1):
            stop = index if good else index + 1
            missing_runs.append({"start": times[start].isoformat(), "end": times[stop - 1].isoformat(), "hours": stop - start})
            start = None
    coverage = sum(valid) / len(valid)
    longest = max((run["hours"] for run in missing_runs), default=0)
    observed_peak_index = max((index for index, good in enumerate(valid) if good),
                              key=lambda index: values[index], default=None)
    phases = {}
    if observed_peak_index is not None:
        radius = policy["peak_window_hours"]
        ranges = {
            "rising": (0, observed_peak_index + 1),
            "peak_window": (max(0, observed_peak_index - radius), min(len(values), observed_peak_index + radius + 1)),
            "recession": (observed_peak_index, len(values)),
        }
        for name, (first, stop) in ranges.items():
            phases[name] = {"start": times[first].isoformat(), "end": times[stop - 1].isoformat(),
                            "hours": stop - first, "valid_hours": sum(valid[first:stop]),
                            "coverage_fraction": sum(valid[first:stop]) / (stop - first)}
    reasons, warnings = [], []
    if coverage < 1:
        warnings.append("FLOW_INCOMPLETE")
    if coverage < policy["minimum_observed_coverage"]:
        reasons.append("OBSERVED_COVERAGE_BELOW_MINIMUM")
    if longest > policy["maximum_missing_gap_hours"]:
        reasons.append("OBSERVED_MISSING_GAP_EXCEEDS_MAXIMUM")
    if observed_peak_index is None:
        reasons.append("NO_VALID_OBSERVED_FLOW")
    elif any(phase["coverage_fraction"] < policy["minimum_phase_coverage"] for phase in phases.values()):
        reasons.append("OBSERVED_PHASE_COVERAGE_BELOW_MINIMUM")
    exclusions = reasons if policy["enforce"] else []
    return {
        "window_hours": len(times), "valid_hours": sum(valid), "missing_hours": len(valid) - sum(valid),
        "coverage_fraction": coverage, "maximum_missing_gap_hours": longest, "missing_intervals": missing_runs,
        "phase_coverage": phases,
        "phase_definition": "Split at the maximum available observation; missing hours may hide the true peak.",
        "observed_peak_time": times[observed_peak_index].isoformat() if observed_peak_index is not None else None,
        "true_peak_confirmed": coverage == 1,
        "policy": policy, "accepted": not exclusions,
        "exclusion_reasons": exclusions, "warning_reasons": list(dict.fromkeys(warnings + reasons)),
        "volume_scope": "COMPLETE_EVENT" if coverage == 1 else "AVAILABLE_OBSERVED_HOURS_ONLY",
    }


def masked_volume_metrics(times, observed, simulated):
    times = _times(times)
    obs, sim = [_number(value) for value in observed], [_number(value) for value in simulated]
    if len(obs) != len(times) or len(sim) != len(times):
        raise ValueError("流量与评价时轴长度不一致")
    paired = [a is not None and b is not None for a, b in zip(obs, sim)]
    obs_sum = math.fsum(a for a, keep in zip(obs, paired) if keep) * 3600
    sim_sum = math.fsum(b for b, keep in zip(sim, paired) if keep) * 3600
    complete_obs, complete_pair = all(value is not None for value in obs), all(paired)
    return {
        "paired_hours": sum(paired), "window_hours": len(times), "paired_fraction": sum(paired) / len(times),
        "volume_scope": "COMPLETE_EVENT" if complete_pair else "COMMON_VALID_HOURS_ONLY",
        "masked_observed_volume_m3": obs_sum if any(paired) else None,
        "masked_simulated_volume_m3": sim_sum if any(paired) else None,
        "masked_volume_bias_percent": 100 * (sim_sum - obs_sum) / obs_sum if obs_sum > 0 else None,
        "complete_observed_volume_m3": math.fsum(obs) * 3600 if complete_obs else None,
        "full_volume_error_available": complete_pair,
        "paired_start": next((time.isoformat() for time, keep in zip(times, paired) if keep), None),
        "paired_end": next((time.isoformat() for time, keep in zip(reversed(times), reversed(paired)) if keep), None),
        "missing_value_policy": "NO_ZERO_FILL_NO_INTERPOLATION_NO_FULL_VOLUME_EXTRAPOLATION",
    }


__all__ = ["evaluate_observation_coverage", "masked_volume_metrics", "resolve_observation_policy", "DEFAULT_OBSERVATION_POLICY"]
