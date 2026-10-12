"""[STATE-INITIALIZATION-03] Callable state-initialization task service."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from runoff_platform.config_loader import load_parameter_set
from runoff_platform.data.pet_forcing import PetProvider
from runoff_platform.data.precipitation_quality import read_station_count
from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids
from xaj_routing.engine import RoutingEngine
from xaj_routing.io import (
    read_control_points_json,
    read_parameters_json,
    read_topology_json,
)
from xaj_routing.models import RoutingState, RunoffRecord

from .core import (
    STATE_MODULE_ID,
    STATE_SCHEMA_VERSION,
    SUPPORTED_STRATEGIES,
    StateInitializationError,
    arrays_to_land_records,
    build_uniform_land_state,
    load_checkpoint,
    sha256_file,
    write_checkpoint,
)
from .routing import build_observed_flow_anchored_state


def _resolve(value: str, project_root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _required(payload: dict[str, Any], fields: set[str]) -> None:
    missing = sorted(field for field in fields if payload.get(field) in (None, ""))
    if missing:
        raise StateInitializationError(f"missing state task fields: {missing}")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_grids(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise StateInitializationError("grid_static contains no grids")
    required = {"array_row", "array_col", "area_km2"}
    if not required.issubset(rows[0]):
        raise StateInitializationError(f"grid_static requires columns: {sorted(required)}")
    return rows


# [STATE-INITIALIZATION-03E] Load an exact fixed hourly warm-up window.
def _load_precipitation_window(
    precipitation_dir: Path,
    grids: list[dict[str, str]],
    event_start: pd.Timestamp,
    warmup_hours: int,
    require_source_station_count: bool,
) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    if warmup_hours <= 0:
        raise StateInitializationError("warmup_hours must be positive")
    start = event_start - pd.Timedelta(hours=warmup_hours)
    end = event_start - pd.Timedelta(hours=1)
    positions = [(int(row["array_row"]), int(row["array_col"])) for row in grids]
    times_parts: list[pd.DatetimeIndex] = []
    precipitation_parts: list[np.ndarray] = []
    station_count_parts: list[np.ndarray] = []
    for year in range(start.year, end.year + 1):
        path = precipitation_dir / f"nananxi_precip_hourly_1km_{year}.nc"
        if not path.exists():
            raise StateInitializationError(f"precipitation file not found: {path}")
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            if "precipitation" not in dataset:
                raise StateInitializationError(f"precipitation variable missing: {path}")
            all_times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
            mask = (all_times >= start) & (all_times <= end)
            if not np.any(mask):
                continue
            source = np.asarray(dataset["precipitation"].values, dtype=np.float32)
            precipitation_parts.append(
                np.stack([source[mask, row, column] for row, column in positions], axis=1)
            )
            times_parts.append(all_times[mask])
            try:
                station_count, _ = read_station_count(
                    dataset,
                    mask,
                    required=require_source_station_count,
                )
            except ValueError as exc:
                raise StateInitializationError(f"{exc}: {path}") from exc
            station_count_parts.append(station_count)

    if not times_parts:
        raise StateInitializationError("warm-up precipitation window is empty")
    times = times_parts[0].append(times_parts[1:])
    precipitation = np.concatenate(precipitation_parts, axis=0)
    station_count = np.concatenate(station_count_parts)
    expected = pd.date_range(start, end, freq="h")
    if not times.equals(expected):
        raise StateInitializationError(
            f"warm-up precipitation must be complete hourly data: expected {len(expected)}, got {len(times)}"
        )
    if not np.isfinite(precipitation).all() or np.any(precipitation < 0.0):
        raise StateInitializationError("warm-up precipitation contains missing or negative values")
    if require_source_station_count and np.any(station_count < 1):
        raise StateInitializationError("warm-up contains hours without a valid source station")
    return times, precipitation, station_count


def _build_grid_to_unit_mapping(
    path: Path,
    grids: list[dict[str, str]],
    topology,
) -> tuple[list[str], np.ndarray, dict[str, float]]:
    unit_ids = list(topology.units)
    unit_index = {unit_id: index for index, unit_id in enumerate(unit_ids)}
    grid_ids = [str(row.get("grid_id") or row.get("unit_id")) for row in grids]
    grid_index = {grid_id: index for index, grid_id in enumerate(grid_ids)}
    mapping = np.zeros((len(grids), len(unit_ids)), dtype=float)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            grid_id = str(row["grid_id"])
            unit_id = str(row["unit_id"])
            if grid_id not in grid_index or unit_id not in unit_index:
                continue
            unit_area = topology.units[unit_id].area_km2
            if unit_area <= 0.0:
                continue
            mapping[grid_index[grid_id], unit_index[unit_id]] += (
                float(row["adjusted_overlap_area_km2"]) / unit_area
            )
    missing_units = [
        unit_ids[index]
        for index in range(len(unit_ids))
        if not np.any(mapping[:, index] > 0.0)
    ]
    if missing_units:
        raise StateInitializationError(f"grid mapping is missing routing units: {missing_units}")
    unit_weight_sums = mapping.sum(axis=0)
    diagnostics = {
        "minimum_unit_weight_sum": float(unit_weight_sums.min()),
        "maximum_unit_weight_sum": float(unit_weight_sums.max()),
        "maximum_unit_weight_relative_error": float(
            np.max(np.abs(unit_weight_sums - 1.0))
        ),
    }
    return unit_ids, mapping, diagnostics


# [STATE-INITIALIZATION-03F] Route warm-up RS/RI/RG into a standard routing state.
def _route_warmup(
    times: pd.DatetimeIndex,
    rs: np.ndarray,
    ri: np.ndarray,
    rg: np.ndarray,
    mapping: np.ndarray,
    unit_ids: list[str],
    topology,
    routing_parameters,
    control_points,
):
    unit_rs = rs @ mapping
    unit_ri = ri @ mapping
    unit_rg = rg @ mapping
    records = [
        RunoffRecord(
            unit_id=unit_id,
            time=timestamp.to_pydatetime(),
            RS=float(unit_rs[time_index, unit_index]),
            RI=float(unit_ri[time_index, unit_index]),
            RG=float(unit_rg[time_index, unit_index]),
        )
        for time_index, timestamp in enumerate(times)
        for unit_index, unit_id in enumerate(unit_ids)
    ]
    result = RoutingEngine(
        topology=topology,
        parameters=routing_parameters,
        initial_state=RoutingState(),
        control_points=control_points,
    ).run(records)
    final_control_flow = (
        float(result.control_flows[-1].outflow_cms) if result.control_flows else None
    )
    return result.final_state, final_control_flow


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "STATE"


def _copy_checkpoint_strategy(
    payload: dict[str, Any],
    project_root: Path,
    strategy: str,
) -> dict[str, Any]:
    _required(payload, {"source_checkpoint", "output_dir"})
    source_path = _resolve(payload["source_checkpoint"], project_root)
    source = load_checkpoint(source_path)
    state_id = str(payload.get("state_id") or f"{source['state_id']}_{strategy}")
    output_dir = _resolve(payload["output_dir"], project_root)
    result = {
        **source,
        "state_id": state_id,
        "strategy": strategy,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            **source.get("provenance", {}),
            "parent_state_id": source["state_id"],
            "source_checkpoint": str(source_path),
            "source_checkpoint_sha256": sha256_file(source_path),
        },
    }
    output_path = write_checkpoint(
        output_dir / f"{_safe_id(state_id)}.json",
        result,
        overwrite=bool(payload.get("overwrite", False)),
    )
    return {
        "module_id": STATE_MODULE_ID,
        "task_id": str(payload.get("task_id", "STATE-LOCAL")),
        "status": "SUCCEEDED",
        "summary": {
            "state_id": state_id,
            "strategy": strategy,
            "effective_time": result["effective_time"],
            "grid_count": len(result["land_states"]),
            "quality_flags": result.get("diagnostics", {}).get("quality_flags", []),
        },
        "output_files": {"state_checkpoint": str(output_path)},
    }


# [STATE-INITIALIZATION-03G] Unified JSON-compatible task entry.
def run_state_initialization_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = Path(payload.get("project_root", ".")).resolve()
    strategy = str(payload.get("strategy", "")).upper()
    if strategy not in SUPPORTED_STRATEGIES:
        raise StateInitializationError(
            f"strategy must be one of {sorted(SUPPORTED_STRATEGIES)}"
        )
    if strategy in {"CHECKPOINT", "CONTINUOUS"}:
        return _copy_checkpoint_strategy(payload, project_root, strategy)

    required = {
        "event_id",
        "event_start",
        "state_config",
        "initial_land_state",
        "land_parameter_set",
        "grid_static",
        "precipitation_directory",
        "grid_to_unit_weight",
        "topology",
        "routing_parameters",
        "control_points",
        "output_dir",
    }
    _required(payload, required)
    if strategy == "OBSERVED_FLOW_ANCHORED":
        _required(payload, {"observed_flow_cms"})

    paths = {
        name: _resolve(payload[name], project_root)
        for name in required
        if name not in {"event_id", "event_start", "output_dir"}
    }
    output_dir = _resolve(payload["output_dir"], project_root)
    config = _load_json(paths["state_config"])
    warmup_hours = int(payload.get("warmup_hours", config["fixed_warmup_hours"]))
    pet_config_value = payload.get("pet_config", config.get("pet_config"))
    pet_provider = None
    if pet_config_value:
        if payload.get("pet_config"):
            pet_config_path = _resolve(str(pet_config_value), project_root)
        else:
            pet_config_path = _resolve(str(pet_config_value), paths["state_config"].parent)
        pet_provider = PetProvider.from_file(pet_config_path)
        paths["pet_config"] = pet_config_path
        pet_mm_h = None
    else:
        # Legacy scalar support remains for old task payloads.
        pet_mm_h = float(payload.get("pet_mm_h", config["pet_mm_h"]))
    require_station_count = bool(config.get("require_source_station_count", True))
    event_start = pd.Timestamp(payload["event_start"])
    if event_start.minute or event_start.second or event_start.microsecond:
        raise StateInitializationError("event_start must be aligned to a whole hour")

    grids = _load_grids(paths["grid_static"])
    grid_ids = [str(row.get("grid_id") or row.get("unit_id")) for row in grids]
    zone_types = [str(row.get("zone_type") or "NON_GLACIER") for row in grids]
    unsupported_zones = sorted(
        {zone.upper() for zone in zone_types if zone.upper() != "NON_GLACIER"}
    )
    if unsupported_zones:
        raise StateInitializationError(
            "this state task uses liquid-rain XAJ warm-up and only accepts "
            f"NON_GLACIER grids; received {unsupported_zones}"
        )
    params = load_parameter_set(paths["land_parameter_set"])
    initial_template = _load_json(paths["initial_land_state"])
    initial_land_state = build_uniform_land_state(initial_template, len(grids), params)
    times, precipitation, station_count = _load_precipitation_window(
        paths["precipitation_directory"],
        grids,
        event_start,
        warmup_hours,
        require_station_count,
    )
    pet_forcing = pet_provider.values(times) if pet_provider is not None else pet_mm_h

    rs, ri, rg, final_land_state = simulate_xaj_liquid_grids(
        precipitation,
        params,
        pet_forcing,
        initial_land_state,
    )
    topology = read_topology_json(paths["topology"])
    routing_parameters = read_parameters_json(paths["routing_parameters"])
    controls = read_control_points_json(paths["control_points"])
    if len(controls) != 1:
        raise StateInitializationError("state initialization requires exactly one control point")
    unit_ids, mapping, mapping_diagnostics = _build_grid_to_unit_mapping(
        paths["grid_to_unit_weight"], grids, topology
    )
    maximum_mapping_error = float(
        config.get("maximum_routing_mapping_relative_error", 0.05)
    )
    if mapping_diagnostics["maximum_unit_weight_relative_error"] > maximum_mapping_error:
        raise StateInitializationError(
            "grid-to-routing mapping error exceeds state configuration limit: "
            f"{mapping_diagnostics['maximum_unit_weight_relative_error']:.6f} > "
            f"{maximum_mapping_error:.6f}"
        )

    warmup_routing_flow = None
    observed_anchor = None
    if strategy == "FIXED_WARMUP":
        routing_state, warmup_routing_flow = _route_warmup(
            times,
            rs,
            ri,
            rg,
            mapping,
            unit_ids,
            topology,
            routing_parameters,
            controls,
        )
    else:
        observed_anchor = float(payload["observed_flow_cms"])
        routing_state = build_observed_flow_anchored_state(
            topology,
            controls[0],
            observed_anchor,
        )

    event_id = str(payload["event_id"])
    state_id = str(
        payload.get("state_id")
        or f"{event_id}_{strategy}_{event_start.strftime('%Y%m%dT%H%M%S')}"
    )
    quality_flags: list[str] = []
    if not require_station_count:
        quality_flags.append("SOURCE_STATION_COUNT_NOT_REQUIRED")
    mapping_warning_tolerance = float(
        config.get("routing_mapping_warning_tolerance", 1.0e-6)
    )
    if (
        mapping_diagnostics["maximum_unit_weight_relative_error"]
        > mapping_warning_tolerance
    ):
        quality_flags.append("ROUTING_MAPPING_AREA_MISMATCH")
    checkpoint = {
        "schema_version": STATE_SCHEMA_VERSION,
        "module_id": STATE_MODULE_ID,
        "state_id": state_id,
        "strategy": strategy,
        "event_id": event_id,
        "effective_time": event_start.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "land_states": arrays_to_land_records(grid_ids, final_land_state, zone_types),
        "routing_state": routing_state.to_mapping(),
        "observed_flow_anchor": (
            None
            if observed_anchor is None
            else {
                "control_id": controls[0].control_id,
                "control_reach_id": controls[0].reach_id,
                "flow_cms": observed_anchor,
            }
        ),
        "diagnostics": {
            "quality_flags": quality_flags,
            "grid_count": len(grids),
            "warmup_hours_expected": warmup_hours,
            "warmup_hours_loaded": len(times),
            "warmup_start": times[0].isoformat(),
            "warmup_end": times[-1].isoformat(),
            "warmup_basin_mean_precipitation_mm": float(
                np.average(
                    precipitation.sum(axis=0),
                    weights=np.asarray([float(row["area_km2"]) for row in grids]),
                )
            ),
            "minimum_source_station_count": int(station_count.min()),
            "fixed_warmup_final_control_flow_cms": warmup_routing_flow,
            "routing_mapping": mapping_diagnostics,
            "mean_land_state": {
                name: float(np.mean(values))
                for name, values in final_land_state.items()
            },
        },
        "provenance": {
            "source_files_overwritten": False,
            "state_config": str(paths["state_config"]),
            "initial_land_state": str(paths["initial_land_state"]),
            "land_parameter_set": str(paths["land_parameter_set"]),
            "precipitation_directory": str(paths["precipitation_directory"]),
            "grid_static": str(paths["grid_static"]),
            "grid_to_unit_weight": str(paths["grid_to_unit_weight"]),
            "topology": str(paths["topology"]),
            "routing_parameters": str(paths["routing_parameters"]),
            "control_points": str(paths["control_points"]),
            "input_sha256": {
                name: sha256_file(path)
                for name, path in paths.items()
                if path.is_file()
            },
            "assumptions": {
                "precipitation_phase": "LIQUID_NON_GLACIER",
                "temperature_used": False,
                "pet": (
                    pet_provider.report()
                    if pet_provider is not None
                    else {
                        "mode": "CONSTANT_ASSUMPTION",
                        "value_mm_h": pet_mm_h,
                        "production_eligible": False,
                    }
                ),
                "routing_anchor_component": (
                    "RG" if strategy == "OBSERVED_FLOW_ANCHORED" else None
                ),
            },
        },
    }
    output_path = write_checkpoint(
        output_dir / f"{_safe_id(state_id)}.json",
        checkpoint,
        overwrite=bool(payload.get("overwrite", False)),
    )
    return {
        "module_id": STATE_MODULE_ID,
        "task_id": str(payload.get("task_id", "STATE-LOCAL")),
        "status": "SUCCEEDED",
        "summary": {
            "state_id": state_id,
            "strategy": strategy,
            "effective_time": checkpoint["effective_time"],
            "grid_count": len(grids),
            "warmup_hours": warmup_hours,
            "observed_flow_anchor_cms": observed_anchor,
            "quality_flags": quality_flags,
        },
        "output_files": {"state_checkpoint": str(output_path)},
    }
