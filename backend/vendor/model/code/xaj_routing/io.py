"""File adapters for the routing prototype."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .models import (
    ControlFlow,
    ControlPoint,
    ComponentFlow,
    OutletFlow,
    ReachFlow,
    RoutingParameters,
    RoutingState,
    RunoffRecord,
)
from .topology import Topology


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read_runoff_csv(path: str | Path) -> list[RunoffRecord]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [RunoffRecord.from_mapping(row) for row in reader]


def read_topology_json(path: str | Path) -> Topology:
    return Topology.from_mapping(read_json(path))


def read_parameters_json(path: str | Path) -> RoutingParameters:
    return RoutingParameters.from_mapping(read_json(path))


def read_state_json(path: str | Path | None) -> RoutingState:
    if not path:
        return RoutingState()
    return RoutingState.from_mapping(read_json(path))


def read_control_points_json(path: str | Path | None) -> list[ControlPoint]:
    if not path:
        return []
    data = read_json(path)
    if isinstance(data, list):
        items = data
    elif "control_points" in data:
        items = data["control_points"]
    elif "controls" in data:
        items = data["controls"]
    else:
        items = [data]
    return [ControlPoint.from_mapping(item) for item in items]


def write_component_flows_csv(
    path: str | Path,
    rows: list[ComponentFlow],
    run_id: str = "",
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "time",
        "unit_id",
        "reach_id",
        "component_type",
        "runoff_mm",
        "inflow_cms",
        "slope_outflow_cms",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "run_id": run_id,
                    "time": row.time.isoformat(sep=" "),
                    "unit_id": row.unit_id,
                    "reach_id": row.reach_id or "",
                    "component_type": row.component_type,
                    "runoff_mm": f"{row.runoff_mm:.6f}",
                    "inflow_cms": f"{row.inflow_cms:.6f}",
                    "slope_outflow_cms": f"{row.slope_outflow_cms:.6f}",
                }
            )


def write_reach_flows_csv(
    path: str | Path,
    rows: list[ReachFlow],
    run_id: str = "",
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "time",
        "reach_id",
        "local_inflow_cms",
        "total_inflow_cms",
        "outflow_cms",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "run_id": run_id,
                    "time": row.time.isoformat(sep=" "),
                    "reach_id": row.reach_id,
                    "local_inflow_cms": f"{row.local_inflow_cms:.6f}",
                    "total_inflow_cms": f"{row.total_inflow_cms:.6f}",
                    "outflow_cms": f"{row.outflow_cms:.6f}",
                }
            )


def write_outlet_flows_csv(
    path: str | Path,
    rows: list[OutletFlow],
    run_id: str = "",
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["run_id", "time", "outlet_id", "outflow_cms"]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "run_id": run_id,
                    "time": row.time.isoformat(sep=" "),
                    "outlet_id": row.outlet_id,
                    "outflow_cms": f"{row.outflow_cms:.6f}",
                }
            )


def write_control_flows_csv(
    path: str | Path,
    rows: list[ControlFlow],
    run_id: str = "",
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "time",
        "control_id",
        "control_name",
        "reach_id",
        "outflow_cms",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "run_id": run_id,
                    "time": row.time.isoformat(sep=" "),
                    "control_id": row.control_id,
                    "control_name": row.control_name,
                    "reach_id": row.reach_id,
                    "outflow_cms": f"{row.outflow_cms:.6f}",
                }
            )
