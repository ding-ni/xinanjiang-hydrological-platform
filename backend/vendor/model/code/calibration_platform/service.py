"""CAL-SERVICE: independent task entry point used by API, CLI and frontend."""

from __future__ import annotations

import json
from pathlib import Path

from .algorithms import search
from .contracts import CALIBRATION_MODES, CalibrationTask, ObjectiveSpec, ParameterSpec
from .routing import RoutingOnlyEvaluator


def get_calibration_catalog() -> dict:
    return {
        "modes": list(CALIBRATION_MODES),
        "recommended_sequence": ["ROUTING_ONLY", "JOINT", "INDEPENDENT_VALIDATION"],
        "algorithms": ["GRID_SEARCH", "RANDOM_SEARCH", "DIFFERENTIAL_EVOLUTION"],
        "metrics": ["NSE", "KGE", "PBIAS", "RMSE", "PEAK_ERROR", "PEAK_TIME_ERROR"],
        "routing_parameter_aliases": [
            "slope_k.RS", "slope_k.RI", "slope_k.RG",
            "muskingum.k_scale", "muskingum.x", "initial.control_flow_cms",
        ],
        "mode_status": {
            "ROUTING_ONLY": "READY",
            "RUNOFF_ONLY": "READY_FOR_SINGLE_GRID_OR_RUNOFF_OBSERVATIONS",
            "JOINT": "RESERVED_UNTIL_FORCING_AND_MULTI_EVENT_CASES_ARE_CONFIRMED",
        },
    }


def _boundary_warnings(best_parameters: dict, specs: list[ParameterSpec]) -> list[str]:
    warnings = []
    by_name = {spec.name: spec for spec in specs}
    for name, value in best_parameters.items():
        spec = by_name[name]
        tolerance = max((spec.maximum - spec.minimum) * 1.0e-4, 1.0e-10)
        if abs(value - spec.minimum) <= tolerance:
            warnings.append(f"{name} reached lower bound {spec.minimum}")
        if abs(value - spec.maximum) <= tolerance:
            warnings.append(f"{name} reached upper bound {spec.maximum}")
    return warnings


def _resolve_paths(config: dict, root: Path) -> dict:
    result = dict(config)
    for key in ("topology", "routing_parameters", "runoff_input", "control_points", "observed_control_flow"):
        if key in result:
            result[key] = str((root / result[key]).resolve()) if not Path(result[key]).is_absolute() else result[key]
    return result


def run_calibration_task(payload: dict) -> dict:
    mode = str(payload.get("mode", "ROUTING_ONLY")).upper()
    task_id = str(payload.get("task_id", "CAL-LOCAL"))
    specs = [ParameterSpec(**item) for item in payload.get("parameter_specs", [])]
    objectives = [ObjectiveSpec(**item) for item in payload.get("objective_specs", [])]
    task = CalibrationTask(task_id, mode, str(payload.get("algorithm", "RANDOM_SEARCH")), specs, objectives, payload.get("algorithm_config", {}), payload.get("base_config", {}))
    if mode == "RUNOFF_ONLY":
        from runoff_platform.api.service import run_calibration_request
        response = run_calibration_request(payload["runoff_calibration_request"])
        return {"task_id": task.task_id, "mode": mode, "status": response.get("status", "SUCCEEDED"), "delegated_result": response}
    if mode == "JOINT":
        raise ValueError("JOINT requires a joint_case evaluator; run ROUTING_ONLY first until forcing and routing calibration cases are configured")
    config = _resolve_paths(payload["routing_case"], Path(payload.get("project_root", ".")).resolve())
    objective_specs = [spec.__dict__ for spec in objectives]
    config["objective_specs"] = objective_specs
    evaluator = RoutingOnlyEvaluator(config)
    result = search([spec.__dict__ for spec in specs], evaluator.evaluate, {"algorithm": task.algorithm, **task.algorithm_config})
    project_root = Path(payload.get("project_root", ".")).resolve()
    output_dir_value = Path(payload.get("output_dir", "calibration_output"))
    output_dir = output_dir_value if output_dir_value.is_absolute() else project_root / output_dir_value
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluator.write_best_parameters(result["best_parameters"], output_dir / "best_routing_parameters.json")
    evaluator.write_best_outputs(result["best_parameters"], output_dir, task.task_id)
    from integration.plotting import write_hydrograph
    write_hydrograph(
        config["observed_control_flow"],
        output_dir / "best_control_flow.csv",
        output_dir / "best_outlet_flow.csv",
        output_dir / "best_hydrograph.png",
        title="Nan'an River 2017 Routing Calibration Result",
    )
    baseline = evaluator.evaluate({})
    result["baseline_metrics"] = baseline
    warnings = _boundary_warnings(result["best_parameters"], specs)
    output_files = {
        "best_parameters": "best_routing_parameters.json",
        "control_flow": "best_control_flow.csv",
        "outlet_flow": "best_outlet_flow.csv",
        "hydrograph": "best_hydrograph.png",
        "summary": "calibration_summary.json",
        "result": "calibration_result.json",
    }
    response = {
        "task_id": task.task_id,
        "mode": mode,
        "status": "SUCCEEDED_WITH_WARNINGS" if warnings else "SUCCEEDED",
        **result,
        "warnings": warnings,
        "output_files": output_files,
    }
    (output_dir / "calibration_result.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    summary = {
        "task_id": task.task_id,
        "status": response["status"],
        "mode": mode,
        "evaluations": result["evaluations"],
        "best_parameters": result["best_parameters"],
        "baseline_metrics": {name: value["raw"] for name, value in baseline["terms"].items()},
        "best_metrics": {name: value["raw"] for name, value in result["best_metrics"]["terms"].items()},
        "warnings": warnings,
    }
    (output_dir / "calibration_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return response
