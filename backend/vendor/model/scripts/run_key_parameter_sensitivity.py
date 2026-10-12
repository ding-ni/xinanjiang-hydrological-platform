"""Screen WLM, B, WM and SM with continuous inter-event state inheritance.

This module changes one runoff parameter at a time.  It is a diagnostic
boundary/sensitivity experiment, not a replacement for joint calibration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "scripts"))

from runoff_platform.config_loader import load_parameter_set  # noqa: E402
from calibration_platform.qualification import (  # noqa: E402
    load_qualification_config,
    qualification_definition,
)
from runoff_platform.data.event_quality import evaluate_event_quality  # noqa: E402
from runoff_platform.data.pet_forcing import PetProvider  # noqa: E402
from state_initialization.core import build_uniform_land_state  # noqa: E402
from xaj_routing.control_area import contributing_grid_areas  # noqa: E402
from xaj_routing.io import (  # noqa: E402
    read_control_points_json,
    read_parameters_json,
    read_topology_json,
)
from run_formal_multievent_calibration import (  # noqa: E402
    FastRoutingRunner,
    build_mapping,
    candidate_params,
    eligible_events,
    find_observed_flow,
    load_event_data,
    load_grids,
    metrics,
    objective_loss,
    random_split_events,
    resolved_runoff_parameters,
    route_event_fast,
    summary_for,
)


MODULE_ID = "XAJ-KEY-SENSITIVITY-12"


def _resolve_reference(value: str, container_path: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (container_path.parent / path).resolve()


def _file_identity(path: Path) -> dict[str, str]:
    content = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}


def _scenario_summary(frame: pd.DataFrame, objective_specs: list[dict[str, Any]]) -> dict[str, Any]:
    all_summary = summary_for(frame)
    calibration = frame.loc[frame["group"] == "CALIBRATION"]
    result: dict[str, Any] = {
        **all_summary,
        "calibration_loss": objective_loss(calibration, objective_specs),
        "mean_peak_error_percent": float(frame["peak_error_percent"].mean()),
        "mean_volume_error_percent": float(frame["volume_error_percent"].mean()),
    }
    for group, group_frame in frame.groupby("group"):
        prefix = group.lower()
        group_summary = summary_for(group_frame)
        result.update({f"{prefix}_{name}": value for name, value in group_summary.items()})
    return result


def _write_plots(summary: pd.DataFrame, baseline: dict[str, float], output: Path) -> list[str]:
    sensitivity = summary.loc[summary["parameter"].isin(["WLM", "B", "WM", "SM"])].copy()
    if sensitivity.empty:
        return []
    parameters = [name for name in ("WLM", "B", "WM", "SM") if name in set(sensitivity["parameter"])]

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for axis, name in zip(axes.flat, parameters):
        values = sensitivity.loc[sensitivity["parameter"] == name].sort_values("parameter_value")
        axis.plot(values["parameter_value"], values["mean_NSE"], marker="o", label="Mean NSE")
        axis.plot(values["parameter_value"], values["mean_KGE"], marker="s", label="Mean KGE")
        axis.axvline(baseline[name], color="black", linestyle="--", linewidth=1, label="Baseline")
        axis.set(title=name, xlabel=name, ylabel="Efficiency")
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8)
    efficiency_name = "key_parameter_efficiency_sensitivity.png"
    fig.savefig(output / efficiency_name, dpi=180)
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for axis, name in zip(axes.flat, parameters):
        values = sensitivity.loc[sensitivity["parameter"] == name].sort_values("parameter_value")
        axis.plot(values["parameter_value"], values["mean_abs_peak_error_percent"], marker="o", label="Abs peak error")
        axis.plot(values["parameter_value"], values["mean_abs_volume_error_percent"], marker="s", label="Abs volume error")
        axis.axvline(baseline[name], color="black", linestyle="--", linewidth=1, label="Baseline")
        axis.set(title=name, xlabel=name, ylabel="Mean absolute error (%)")
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8)
    error_name = "key_parameter_error_sensitivity.png"
    fig.savefig(output / error_name, dpi=180)
    plt.close(fig)
    return [efficiency_name, error_name]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--config", default=str(ROOT / "config" / "key_parameter_sensitivity.json"))
    parser.add_argument("--output-dir", default=str(ROOT / "validation" / "key_parameter_sensitivity_20260819"))
    parser.add_argument("--limit-events", type=int, default=0, help="smoke-test limit; zero evaluates every accepted event")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if str(config.get("state_strategy", "")).upper() != "CONTINUOUS":
        raise ValueError("key-parameter sensitivity requires state_strategy=CONTINUOUS")
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data_root = Path(args.data_root).resolve()
    warmup_hours = int(config["warmup_hours"])

    baseline_path = _resolve_reference(config["baseline_parameter_set"], config_path)
    event_catalog_path = _resolve_reference(config["event_catalog"], config_path)
    precip_dir = _resolve_reference(config["precipitation_directory"], config_path)
    quality_config_path = _resolve_reference(config["event_quality_config"], config_path)
    pet_config_path = _resolve_reference(config["pet_config"], config_path)
    initial_state_path = _resolve_reference(config["initial_land_state"], config_path)
    objective_config_path = _resolve_reference(config["objective_config"], config_path)
    qualification_config_path = _resolve_reference(config["qualification_config"], config_path)

    # [SENSITIVITY-12A] Use the same static grid/routing interfaces as formal calibration.
    grid_path = ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"
    topology_path = ROOT / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"
    mapping_path = ROOT / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"
    controls_path = ROOT / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"
    routing_path = ROOT / "validation" / "calibration_routing_2017" / "best_routing_parameters.json"
    grids = load_grids(grid_path)
    units, mapping = build_mapping(mapping_path, topology_path, grids)
    router = FastRoutingRunner(topology_path, units, controls_path)
    routing = read_parameters_json(routing_path)

    # [SENSITIVITY-12B] Reapply event completeness and physical-quality gates.
    event_meta, catalog_report = eligible_events(data_root, warmup_hours, event_catalog_path)
    observed = find_observed_flow(data_root)
    quality_events = load_event_data(
        data_root,
        event_meta,
        grids,
        observed,
        warmup_hours,
        continuous=False,
        precip_dir=precip_dir,
    )
    quality_config = json.loads(quality_config_path.read_text(encoding="utf-8-sig"))
    control_points = read_control_points_json(controls_path)
    if len(control_points) != 1:
        raise ValueError("sensitivity analysis requires exactly one control point")
    control_areas = contributing_grid_areas(
        mapping_path,
        read_topology_json(topology_path),
        control_points[0].reach_id,
        [str(grid["unit_id"]) for grid in grids],
    )
    upstream = np.flatnonzero(control_areas > 0.0)
    quality_rows = [
        evaluate_event_quality(
            event_id=event.event_id,
            start=event.start,
            end=event.end,
            times=event.times,
            grid_precipitation_mm=event.precip[:, upstream],
            grid_areas_km2=control_areas[upstream],
            observed_flow_cms=event.observed,
            source_station_count=event.source_station_count,
            config=quality_config,
        )
        for event in quality_events
    ]
    accepted_ids = {row["event_id"] for row in quality_rows if row["accepted"]}
    if not accepted_ids:
        raise RuntimeError("event-quality screening rejected every event")
    quality_export = pd.DataFrame(
        [
            {
                **row,
                "exclusion_reasons": ";".join(row["exclusion_reasons"]),
                "warning_reasons": ";".join(row["warning_reasons"]),
            }
            for row in quality_rows
        ]
    )
    quality_export.to_csv(output / "event_quality.csv", index=False, encoding="utf-8-sig")

    event_meta = [event for event in event_meta if event["event_id"] in accepted_ids]
    event_meta, split = random_split_events(
        event_meta,
        int(config["split_seed"]),
        float(config["calibration_ratio"]),
        float(config["validation_ratio"]),
    )
    if args.limit_events:
        event_meta = event_meta[: args.limit_events]
    # [SENSITIVITY-12C] The first event has fixed warm-up; every later segment starts
    # one hour after the previous event and carries both land and routing state.
    events = load_event_data(
        data_root,
        event_meta,
        grids,
        observed,
        warmup_hours,
        continuous=True,
        precip_dir=precip_dir,
    )
    pet_provider = PetProvider.from_file(pet_config_path)
    for event in events:
        event.pet_mm_h = pet_provider.values(event.times)

    baseline_params = load_parameter_set(baseline_path)
    baseline_values = {
        name: float(getattr(baseline_params, name)) for name in ("WLM", "B", "WM", "SM")
    }
    initial_template = json.loads(initial_state_path.read_text(encoding="utf-8-sig"))
    objective_payload = json.loads(objective_config_path.read_text(encoding="utf-8-sig"))
    objective_specs = objective_payload.get("terms", objective_payload.get("objective_specs", []))
    qualification_config = load_qualification_config(qualification_config_path)

    scenarios: list[tuple[str, str, float, dict[str, float]]] = [
        ("BASELINE", "BASELINE", float("nan"), {})
    ]
    for parameter, values in config["parameters"].items():
        for value in values:
            scenarios.append(
                (f"{parameter}_{float(value):g}", parameter, float(value), {f"runoff.{parameter}": float(value)})
            )

    # [SENSITIVITY-12D] Cache exact duplicates such as a tested value equal to baseline.
    scenario_frames: dict[tuple[float, ...], pd.DataFrame] = {}
    metric_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    parameter_order = ("KC", "WM", "WUM", "WLM", "B", "C", "SM", "EX", "KI", "KG", "IM")
    for scenario_number, (scenario_id, parameter, value, change) in enumerate(scenarios, start=1):
        try:
            params = candidate_params(load_parameter_set(baseline_path), change)
        except ValueError as exc:
            skipped.append(
                {"scenario_id": scenario_id, "parameter": parameter, "parameter_value": value, "reason": str(exc)}
            )
            continue
        fingerprint = tuple(round(float(getattr(params, name)), 10) for name in parameter_order)
        duplicate = fingerprint in scenario_frames
        if duplicate:
            frame = scenario_frames[fingerprint].copy()
        else:
            land_state = build_uniform_land_state(initial_template, len(grids), params)
            routing_state = None
            rows = []
            for event in events:
                simulation, land_state, routing_state = route_event_fast(
                    event,
                    params,
                    routing,
                    router,
                    mapping,
                    land_state,
                    routing_state,
                    event.pet_mm_h,
                )
                rows.append(
                    {
                        "event_id": event.event_id,
                        "year": event.year,
                        "group": event.group,
                        **metrics(event.observed, simulation.loc[event.start : event.end], qualification_config),
                    }
                )
            frame = pd.DataFrame(rows)
            scenario_frames[fingerprint] = frame.copy()
        summary = _scenario_summary(frame, objective_specs)
        summary_rows.append(
            {
                "scenario_id": scenario_id,
                "parameter": parameter,
                "parameter_value": value,
                "is_baseline_equivalent": duplicate or parameter == "BASELINE",
                **summary,
            }
        )
        for row in frame.to_dict(orient="records"):
            metric_rows.append(
                {
                    "scenario_id": scenario_id,
                    "parameter": parameter,
                    "parameter_value": value,
                    **row,
                }
            )
        print(
            f"sensitivity={scenario_number}/{len(scenarios)} scenario={scenario_id} "
            f"loss={summary['calibration_loss']:.5f} nse={summary['mean_NSE']:.4f}",
            flush=True,
        )

    metrics_frame = pd.DataFrame(metric_rows)
    summary_frame = pd.DataFrame(summary_rows)
    metrics_frame.to_csv(output / "key_parameter_sensitivity_metrics.csv", index=False, encoding="utf-8-sig")
    summary_frame.to_csv(output / "key_parameter_sensitivity_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(skipped).to_csv(output / "skipped_scenarios.csv", index=False, encoding="utf-8-sig")

    boundary_findings = []
    for parameter in ("WLM", "B", "WM", "SM"):
        values = summary_frame.loc[summary_frame["parameter"] == parameter].sort_values("parameter_value")
        if values.empty:
            continue
        best = values.loc[values["calibration_loss"].idxmin()]
        tested_min = float(values["parameter_value"].min())
        tested_max = float(values["parameter_value"].max())
        best_value = float(best["parameter_value"])
        signal = "INTERIOR_TESTED_VALUE"
        if best_value == tested_min:
            signal = "OPTIMUM_AT_TESTED_LOWER_BOUND"
        elif best_value == tested_max:
            signal = "OPTIMUM_AT_TESTED_UPPER_BOUND"
        boundary_findings.append(
            {
                "parameter": parameter,
                "tested_minimum": tested_min,
                "tested_maximum": tested_max,
                "baseline": baseline_values[parameter],
                "best_one_at_a_time_value": best_value,
                "best_calibration_loss": float(best["calibration_loss"]),
                "boundary_signal": signal,
            }
        )

    plot_files = _write_plots(summary_frame, baseline_values, output)
    report = {
        "module_id": MODULE_ID,
        "status": "DIAGNOSTIC_ONLY_DATA_REVIEW_REQUIRED",
        "method": "ONE_PARAMETER_AT_A_TIME",
        "state_initialization": {
            "strategy": "CONTINUOUS",
            "fixed_first_warmup_hours": warmup_hours,
            "carry_land_state": True,
            "carry_routing_state": True,
            "between_event_forcing": "observed hourly precipitation and configured PET",
        },
        "baseline_parameter_set": _file_identity(baseline_path),
        "resolved_baseline_parameters": resolved_runoff_parameters(baseline_params),
        "qualification_rule": qualification_definition(qualification_config),
        "event_selection": {
            **catalog_report,
            "quality_accepted_count": len(accepted_ids),
            "evaluated_count": len(events),
            "split": split,
            "quality_exclusion_counts": dict(
                Counter(reason for row in quality_rows for reason in row["exclusion_reasons"])
            ),
        },
        "boundary_findings": boundary_findings,
        "skipped_scenarios": skipped,
        "limitations": [
            "One-at-a-time sensitivity does not identify parameter interactions.",
            "The control-section data review has not granted production clearance.",
            "PET is configured forcing and should be replaced when observed PET becomes available.",
        ],
        "outputs": {
            "event_metrics": "key_parameter_sensitivity_metrics.csv",
            "scenario_summary": "key_parameter_sensitivity_summary.csv",
            "skipped_scenarios": "skipped_scenarios.csv",
            "event_quality": "event_quality.csv",
            "plots": plot_files,
        },
    }
    (output / "key_parameter_sensitivity_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": report["status"], "boundary_findings": boundary_findings}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
