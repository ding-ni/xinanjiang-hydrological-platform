"""CAL-ROUTING: evaluator for ROUTING_ONLY tasks."""

from __future__ import annotations

import copy
import csv
import json
from datetime import datetime
from pathlib import Path

from xaj_routing.engine import RoutingEngine
from xaj_routing.io import (
    read_control_points_json,
    read_runoff_csv,
    read_topology_json,
    write_control_flows_csv,
    write_json,
    write_outlet_flows_csv,
)
from xaj_routing.models import RoutingParameters, RoutingState
from xaj_routing.algorithms import muskingum_coefficients
from state_initialization.routing import build_observed_flow_anchored_state

from .objectives import evaluate_objective


def _set_path(data: dict, path: str, value: float) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def apply_routing_candidate(base_parameters: dict, candidate: dict[str, float]) -> tuple[dict, float | None]:
    """Apply stable regional aliases without exposing 31 reach parameters."""
    mapping = copy.deepcopy(base_parameters)
    initial_control_flow = None
    for path, value in candidate.items():
        if path.startswith("slope_k."):
            component = path.split(".", 1)[1].upper()
            mapping.setdefault("default_slope_k_hours", {})[component] = value
        elif path == "muskingum.k_scale":
            for parameters in mapping.get("reach_muskingum", {}).values():
                parameters["k_hours"] = float(parameters["k_hours"]) * value
        elif path == "muskingum.x":
            for parameters in mapping.get("reach_muskingum", {}).values():
                parameters["x"] = value
        elif path == "initial.control_flow_cms":
            initial_control_flow = value
        else:
            _set_path(mapping, path, value)
    return mapping, initial_control_flow


def validate_muskingum_stability(parameters: RoutingParameters) -> None:
    for reach_id, item in parameters.reach_muskingum.items():
        coefficients = muskingum_coefficients(
            item.k_hours * 3600.0,
            item.x,
            parameters.time_step_seconds,
        )
        if min(coefficients.c0, coefficients.c1, coefficients.c2) < -1.0e-12:
            raise ValueError(f"unstable Muskingum coefficients for {reach_id}")


class RoutingOnlyEvaluator:
    def __init__(self, config: dict):
        self.config = config
        self.topology_path = Path(config["topology"])
        self.parameters_path = Path(config["routing_parameters"])
        self.runoff_path = Path(config["runoff_input"])
        self.control_points_path = Path(config["control_points"])
        self.observed_path = Path(config["observed_control_flow"])
        self.topology = read_topology_json(self.topology_path)
        self.records = read_runoff_csv(self.runoff_path)
        self.base_parameters = json.loads(self.parameters_path.read_text(encoding="utf-8-sig"))
        self.controls = read_control_points_json(self.control_points_path)
        if len(self.controls) != 1:
            raise ValueError("ROUTING_ONLY currently requires exactly one control point")
        self.observed = self._read_observed()

    def _read_observed(self):
        with self.observed_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return {datetime.fromisoformat(row["time"]): float(row["observed_flow_m3_s"]) for row in csv.DictReader(handle)}

    def evaluate(self, candidate: dict[str, float]) -> dict:
        result, simulated = self.simulate(candidate)
        metrics = evaluate_objective(self.observed, simulated, self.config["objective_specs"])
        metrics["simulation"] = {
            "control_flow_count": len(simulated),
            "negative_flow_count": sum(flow.outflow_cms < -1.0e-10 for flow in result.reach_flows),
        }
        return metrics

    def simulate(self, candidate: dict[str, float]):
        mapping, initial_control_flow = apply_routing_candidate(self.base_parameters, candidate)
        parameters = RoutingParameters.from_mapping(mapping)
        validate_muskingum_stability(parameters)
        state = self._build_initial_state(initial_control_flow)
        result = RoutingEngine(self.topology, parameters, initial_state=state, control_points=self.controls).run(self.records)
        negative_flow_count = sum(flow.outflow_cms < -1.0e-10 for flow in result.reach_flows)
        if negative_flow_count:
            raise ValueError(f"routing candidate generated {negative_flow_count} negative flows")
        simulated = {flow.time: flow.outflow_cms for flow in result.control_flows if flow.control_id == self.controls[0].control_id}
        return result, simulated

    def _build_initial_state(self, control_flow_cms: float | None) -> RoutingState:
        if control_flow_cms is None or control_flow_cms <= 0.0:
            return RoutingState()
        return build_observed_flow_anchored_state(
            self.topology,
            self.controls[0],
            control_flow_cms,
        )

    def write_best_parameters(self, candidate: dict[str, float], output_path: str | Path) -> None:
        mapping, _ = apply_routing_candidate(self.base_parameters, candidate)
        write_json(output_path, mapping)

    def write_best_outputs(self, candidate: dict[str, float], output_dir: str | Path, run_id: str) -> None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result, _ = self.simulate(candidate)
        write_control_flows_csv(output_dir / "best_control_flow.csv", result.control_flows, run_id)
        write_outlet_flows_csv(output_dir / "best_outlet_flow.csv", result.outlet_flows, run_id)
