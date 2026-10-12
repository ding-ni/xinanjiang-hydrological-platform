"""Production calibration diagnostics and explicit acceptance gates.

This module evaluates completed runs. It never changes model equations or
silently expands parameter bounds; orchestration remains configuration driven.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np


EXTERNAL_GROUPS = ("VALIDATION", "INDEPENDENT_TEST")


class ProductionValidationError(ValueError):
    """Raised when workflow reports are incomplete or not comparable."""


def _as_report(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    path = Path(value)
    if path.is_dir():
        path = path / "formal_multi_event_report.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _external_mean_nse(report: Mapping[str, Any]) -> float:
    values = [
        float(report["groups"][group]["mean_NSE"])
        for group in EXTERNAL_GROUPS
    ]
    return float(np.mean(values))


def _finite_float(value: Any, fallback: float) -> float:
    """Convert an optional metric without allowing NaN to pass a gate."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _same_split(reports: Mapping[str, Mapping[str, Any]]) -> bool:
    signatures = [
        json.dumps(report.get("split", {}), sort_keys=True, ensure_ascii=False)
        for report in reports.values()
    ]
    return len(set(signatures)) == 1


# [CAL-BOUNDARY-07] Compare configured WLM search spaces with one fixed split/seed.
def evaluate_wlm_boundary_experiment(
    runs: Mapping[str, Mapping[str, Any] | str | Path],
    parameter: str = "runoff.WLM",
    tolerance_fraction: float = 1.0e-6,
) -> dict[str, Any]:
    if len(runs) < 2:
        raise ProductionValidationError("WLM boundary experiment requires at least two runs")
    reports = {name: _as_report(value) for name, value in runs.items()}
    if not _same_split(reports):
        raise ProductionValidationError("WLM boundary runs must use an identical event split")
    rows = []
    for name, report in reports.items():
        bounds = report.get("parameter_space", {}).get("bounds", {})
        parameters = report.get("common_parameter_set", {})
        if parameter not in bounds or parameter not in parameters:
            raise ProductionValidationError(f"run {name} does not contain {parameter}")
        minimum, maximum = (float(value) for value in bounds[parameter])
        value = float(parameters[parameter])
        tolerance = max(1.0e-9, (maximum - minimum) * tolerance_fraction)
        rows.append(
            {
                "run": name,
                "minimum": minimum,
                "maximum": maximum,
                "value": value,
                "at_lower_bound": abs(value - minimum) <= tolerance,
                "at_upper_bound": abs(value - maximum) <= tolerance,
                "external_mean_NSE": _external_mean_nse(report),
                "validation_NSE": float(report["groups"]["VALIDATION"]["mean_NSE"]),
                "independent_test_NSE": float(
                    report["groups"]["INDEPENDENT_TEST"]["mean_NSE"]
                ),
                "objective_loss": float(report.get("objective", {}).get("loss", math.nan)),
            }
        )
    resolved_rows = [row for row in rows if not row["at_lower_bound"] and not row["at_upper_bound"]]
    selection_pool = resolved_rows or rows
    selected = max(selection_pool, key=lambda row: row["external_mean_NSE"])
    resolved = bool(resolved_rows)
    return {
        "module_id": "CAL-WLM-BOUNDARY-07",
        "status": "PASSED" if resolved else "REVIEW_REQUIRED",
        "parameter": parameter,
        "event_split_identical": True,
        "boundary_resolved": resolved,
        "selected_run": selected["run"],
        "selected_upper_bound": selected["maximum"],
        "selected_value": selected["value"],
        "runs": sorted(rows, key=lambda row: row["maximum"]),
        "warnings": [] if resolved else [
            f"{parameter} still touches a configured boundary in every experiment"
        ],
    }


# [CAL-STABILITY-08] Quantify optimizer sensitivity after bounds are fixed.
def evaluate_multi_seed_stability(
    runs: Mapping[str, Mapping[str, Any] | str | Path],
    criteria: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    if len(runs) < 3:
        raise ProductionValidationError("multi-seed stability requires at least three runs")
    criteria = {
        "maximum_parameter_cv": 0.35,
        "maximum_normalized_parameter_range": 0.50,
        "maximum_external_nse_std": 0.10,
        "maximum_objective_loss_std": 0.10,
        **dict(criteria or {}),
    }
    reports = {name: _as_report(value) for name, value in runs.items()}
    if not _same_split(reports):
        raise ProductionValidationError("multi-seed runs must use an identical event split")
    first = next(iter(reports.values()))
    parameter_names = list(first.get("common_parameter_set", {}))
    bounds = first.get("parameter_space", {}).get("bounds", {})
    if not parameter_names or any(name not in bounds for name in parameter_names):
        raise ProductionValidationError("multi-seed reports have no comparable parameter bounds")
    for name, report in reports.items():
        if list(report.get("common_parameter_set", {})) != parameter_names:
            raise ProductionValidationError(f"parameter set differs in seed run {name}")
        if report.get("parameter_space", {}).get("bounds", {}) != bounds:
            raise ProductionValidationError(f"parameter bounds differ in seed run {name}")

    labels = list(reports)
    matrix = np.asarray(
        [[float(reports[label]["common_parameter_set"][name]) for name in parameter_names] for label in labels],
        dtype=float,
    )
    parameter_rows = []
    normalized = np.empty_like(matrix)
    for index, name in enumerate(parameter_names):
        values = matrix[:, index]
        minimum, maximum = (float(value) for value in bounds[name])
        span = max(maximum - minimum, 1.0e-12)
        mean = float(np.mean(values))
        std = float(np.std(values))
        cv = std / max(abs(mean), span * 1.0e-6)
        normalized_range = float((np.max(values) - np.min(values)) / span)
        normalized[:, index] = (values - minimum) / span
        parameter_rows.append(
            {
                "parameter": name,
                "mean": mean,
                "standard_deviation": std,
                "coefficient_of_variation": cv,
                "minimum_value": float(np.min(values)),
                "maximum_value": float(np.max(values)),
                "normalized_range": normalized_range,
                "cv_passed": cv <= criteria["maximum_parameter_cv"],
                "range_passed": normalized_range
                <= criteria["maximum_normalized_parameter_range"],
            }
        )

    external_nse = np.asarray([_external_mean_nse(reports[label]) for label in labels])
    losses = np.asarray(
        [float(reports[label].get("objective", {}).get("loss", math.nan)) for label in labels]
    )
    median_vector = np.median(normalized, axis=0)
    distances = np.sqrt(np.sum((normalized - median_vector) ** 2, axis=1))
    representative_index = int(np.argmin(distances))
    seed_rows = [
        {
            "run": label,
            "search_seed": reports[label].get("algorithm_config", {}).get("seed"),
            "external_mean_NSE": float(external_nse[index]),
            "objective_loss": float(losses[index]),
            "distance_to_parameter_median": float(distances[index]),
        }
        for index, label in enumerate(labels)
    ]
    checks = {
        "parameter_cv": all(row["cv_passed"] for row in parameter_rows),
        "parameter_range": all(row["range_passed"] for row in parameter_rows),
        "external_nse_std": float(np.std(external_nse))
        <= criteria["maximum_external_nse_std"],
        "objective_loss_std": bool(np.isfinite(losses).all())
        and float(np.std(losses)) <= criteria["maximum_objective_loss_std"],
    }
    stable = all(checks.values())
    return {
        "module_id": "CAL-MULTI-SEED-08",
        "status": "PASSED" if stable else "REVIEW_REQUIRED",
        "stable": stable,
        "run_count": len(labels),
        "event_split_identical": True,
        "criteria": criteria,
        "checks": checks,
        "external_nse_standard_deviation": float(np.std(external_nse)),
        "objective_loss_standard_deviation": (
            float(np.std(losses)) if np.isfinite(losses).all() else None
        ),
        "representative_run": labels[representative_index],
        "parameters": parameter_rows,
        "runs": seed_rows,
    }


def _check(
    check_id: str,
    label: str,
    passed: bool,
    actual: Any,
    threshold: Any,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "label": label,
        "required": required,
        "passed": bool(passed),
        "actual": actual,
        "threshold": threshold,
    }


# [CAL-PRODUCTION-GATE-10] No result is called production without every required check.
def evaluate_production_gate(
    boundary_report: Mapping[str, Any],
    stability_report: Mapping[str, Any],
    continuous_report: Mapping[str, Any],
    event_report: Mapping[str, Any],
    criteria: Mapping[str, Any],
) -> dict[str, Any]:
    continuous_groups = continuous_report.get("groups", {})
    event_groups = event_report.get("groups", {})
    validation_cont = continuous_groups.get("VALIDATION", {})
    test_cont = continuous_groups.get("INDEPENDENT_TEST", {})
    validation_event = event_groups.get("VALIDATION", {})
    test_event = event_groups.get("INDEPENDENT_TEST", {})
    pet = continuous_report.get("pet", {})
    minimum_coverage = float(criteria.get("minimum_observed_coverage", 0.99))
    maximum_internal_error = float(criteria.get("maximum_internal_balance_error_mm", 0.01))
    minimum_continuous_nse = float(criteria.get("minimum_continuous_nse", 0.50))
    maximum_continuous_bias = float(criteria.get("maximum_abs_continuous_volume_bias_percent", 15.0))
    minimum_event_nse = float(criteria.get("minimum_event_nse", 0.50))
    maximum_event_bias = float(criteria.get("maximum_abs_event_volume_bias_percent", 20.0))
    minimum_qualified_rate = float(criteria.get("minimum_event_qualified_rate_percent", 50.0))

    observed_coverage = _finite_float(
        continuous_report.get("minimum_observed_coverage"), -math.inf
    )
    internal_error = _finite_float(
        continuous_report.get("maximum_abs_internal_balance_error_mm"), math.inf
    )
    validation_cont_nse = _finite_float(validation_cont.get("mean_NSE"), -math.inf)
    test_cont_nse = _finite_float(test_cont.get("mean_NSE"), -math.inf)
    validation_cont_bias = _finite_float(
        validation_cont.get("mean_volume_error_percent"), math.inf
    )
    test_cont_bias = _finite_float(
        test_cont.get("mean_volume_error_percent"), math.inf
    )
    validation_event_nse = _finite_float(validation_event.get("mean_NSE"), -math.inf)
    test_event_nse = _finite_float(test_event.get("mean_NSE"), -math.inf)
    validation_event_bias = _finite_float(
        validation_event.get("mean_abs_volume_error_percent"), math.inf
    )
    test_event_bias = _finite_float(
        test_event.get("mean_abs_volume_error_percent"), math.inf
    )
    validation_qualified = _finite_float(
        validation_event.get("qualified_rate_percent"), -math.inf
    )
    test_qualified = _finite_float(
        test_event.get("qualified_rate_percent"), -math.inf
    )

    checks = [
        _check("WLM_BOUNDARY", "WLM边界已消除", boundary_report.get("boundary_resolved") is True, boundary_report.get("selected_value"), "selected value strictly inside bounds"),
        _check("MULTI_SEED", "多随机种子稳定", stability_report.get("stable") is True, stability_report.get("status"), "PASSED"),
        _check("PET_PROVENANCE", "PET来源可用于生产率定", pet.get("production_eligible") is True, pet.get("mode"), "observed or independently derived PET"),
        _check("OBSERVED_COVERAGE", "连续实测流量完整率", observed_coverage >= minimum_coverage, continuous_report.get("minimum_observed_coverage"), f">={minimum_coverage}"),
        _check("INTERNAL_BALANCE", "模型内部水量闭合", internal_error <= maximum_internal_error, continuous_report.get("maximum_abs_internal_balance_error_mm"), f"<={maximum_internal_error} mm"),
        _check("CONTINUOUS_VALIDATION_NSE", "连续验证期NSE", validation_cont_nse >= minimum_continuous_nse, validation_cont.get("mean_NSE"), f">={minimum_continuous_nse}"),
        _check("CONTINUOUS_TEST_NSE", "连续独立检验期NSE", test_cont_nse >= minimum_continuous_nse, test_cont.get("mean_NSE"), f">={minimum_continuous_nse}"),
        _check("CONTINUOUS_VOLUME_BIAS", "连续期水量偏差", max(abs(validation_cont_bias), abs(test_cont_bias)) <= maximum_continuous_bias, {"validation": validation_cont.get("mean_volume_error_percent"), "independent_test": test_cont.get("mean_volume_error_percent")}, f"absolute <= {maximum_continuous_bias}%"),
        _check("EVENT_VALIDATION_NSE", "洪水验证期NSE", validation_event_nse >= minimum_event_nse, validation_event.get("mean_NSE"), f">={minimum_event_nse}"),
        _check("EVENT_TEST_NSE", "洪水独立检验期NSE", test_event_nse >= minimum_event_nse, test_event.get("mean_NSE"), f">={minimum_event_nse}"),
        _check("EVENT_VOLUME_BIAS", "洪水场次水量偏差", max(validation_event_bias, test_event_bias) <= maximum_event_bias, {"validation": validation_event.get("mean_abs_volume_error_percent"), "independent_test": test_event.get("mean_abs_volume_error_percent")}, f"<={maximum_event_bias}%"),
        _check("EVENT_QUALIFIED_RATE", "洪水场次合格率", min(validation_qualified, test_qualified) >= minimum_qualified_rate, {"validation": validation_event.get("qualified_rate_percent"), "independent_test": test_event.get("qualified_rate_percent")}, f">={minimum_qualified_rate}%"),
    ]
    failed = [item for item in checks if item["required"] and not item["passed"]]
    return {
        "module_id": "CAL-PRODUCTION-GATE-10",
        "status": "PRODUCTION_APPROVED" if not failed else "REVIEW_REQUIRED",
        "production_approved": not failed,
        "criteria": dict(criteria),
        "checks": checks,
        "failed_check_ids": [item["check_id"] for item in failed],
        "note": "Thresholds are platform configuration and must be confirmed against the applicable operational standard.",
    }


__all__ = [
    "ProductionValidationError",
    "evaluate_multi_seed_stability",
    "evaluate_production_gate",
    "evaluate_wlm_boundary_experiment",
]
