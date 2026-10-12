"""Platform JSON service layer; no HTTP framework is required by the model."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import math

import pandas as pd

from ..calibration import CalibrationEngine
from ..core.types import GlacierParams, GlacierState, GridInfo, LandParams, LandState, NUMERICAL, RunContext
from ..core.parameters import (
    ParameterOverrideError,
    apply_parameter_overrides,
    resolve_land_parameter_overrides,
)
from ..data import EventDataProvider, EventWindow
from ..models.glacier import simulate_glacier
from ..models.xaj import simulate_land
from ..objectives import CompositeObjective


COMMON_OUTPUT_FIELDS = [
    "time", "run_id", "scenario_id", "param_set_id", "unit_id", "zone_type",
    "precipitation", "temperature", "rainfall", "snowfall", "snowmelt", "icemelt",
    "snow_sublimation", "ice_sublimation", "et_actual", "swe", "RS", "RI", "RG",
    "glacier_runoff", "total_runoff", "water_balance_error", "quality_flag",
]
LAND_DIAGNOSTIC_FIELDS = [
    "runoff_mechanism", "conversion_enabled", "conversion_activation",
    "effective_infiltration_capacity", "infiltration_excess_RS", "saturation_excess_RS",
    "impervious_runoff", "pervious_runoff", "net_rainfall", "xaj_input",
    "EU", "EL", "ED", "WU", "WL", "WD", "W", "S", "FR", "free_water_storage",
]
GLACIER_DIAGNOSTIC_FIELDS = [
    "bare_ice_fraction", "refreeze", "snow_drainage", "glacier_ablation",
    "snow_liquid", "glacier_storage",
]


class RequestValidationError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _forcing_frame(records):
    if not isinstance(records, list) or not records:
        raise RequestValidationError(["forcing must be a non-empty array"])
    data = pd.DataFrame(records)
    missing = {"time", "precipitation", "temperature"} - set(data.columns)
    if missing:
        raise RequestValidationError([f"missing forcing fields: {sorted(missing)}"])
    data["time"] = pd.to_datetime(data["time"], errors="raise")
    return data.set_index("time").sort_index()


def _set_parameter_path(overrides, path, value):
    current = overrides
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _deep_merge(base, updates):
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _json_value(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def _records(frame, fields_to_export):
    data = frame.reset_index()[fields_to_export].to_dict("records")
    return [{key: _json_value(value) for key, value in row.items()} for row in data]


def _build_request(payload):
    context_data = dict(payload.get("context", {}))
    context_data.setdefault("run_id", "RUN-LOCAL")
    context_data.setdefault("scenario_id", "BASE")
    context_data.setdefault("param_set_id", "DEFAULT")
    context_data.setdefault("time_step_hours", 1.0)
    context_data.setdefault("simulation_mode", payload.get("simulation_mode", "SINGLE").upper())
    context = RunContext(**context_data)
    grid_data = payload.get("grid")
    if not isinstance(grid_data, dict):
        raise RequestValidationError(["missing grid object"])
    grid = GridInfo(**{**grid_data, "zone_type": str(grid_data.get("zone_type", "")).upper()})
    if grid.zone_type not in {"GLACIER", "NON_GLACIER"}:
        raise RequestValidationError(["grid.zone_type must be GLACIER or NON_GLACIER"])
    if context.time_step_hours != NUMERICAL.supported_time_step_hours:
        raise RequestValidationError(["the current version supports only time_step_hours=1"])
    forcing = _forcing_frame(payload.get("forcing"))
    if context.simulation_mode == "EVENT":
        event_data = payload.get("event") or {}
        event = EventWindow(
            event_id=str(event_data.get("event_id", "EVENT-001")),
            start=pd.Timestamp(event_data["start"]),
            end=pd.Timestamp(event_data["end"]),
            warmup_hours=int(event_data.get("warmup_hours", 0)),
        )
        prepared = EventDataProvider().prepare(forcing, event)
        forcing = prepared.forcing
    return context, grid, forcing


# [API-SIM-01] Backward-compatible simulation request
def run_platform_request(payload):
    context, grid, forcing = _build_request(payload)
    initial = payload.get("initial_state", {})
    parameter_values = _deep_merge(
        payload.get("parameter_set", {}).get("parameters", {}),
        payload.get("params", {}),
    )
    if grid.zone_type == "GLACIER":
        try:
            params = apply_parameter_overrides(GlacierParams(), parameter_values)
        except ParameterOverrideError as exc:
            raise RequestValidationError([str(exc)]) from exc
        state = GlacierState(**initial) if initial else GlacierState()
        result = simulate_glacier(forcing, params, state, grid, context)
        diagnostic_fields = GLACIER_DIAGNOSTIC_FIELDS
        final_state = asdict(state)
        component_map = [("R_GLACIER", "glacier_runoff")]
    else:
        try:
            params = apply_parameter_overrides(
                LandParams(),
                resolve_land_parameter_overrides(parameter_values),
            )
        except ParameterOverrideError as exc:
            raise RequestValidationError([str(exc)]) from exc
        state = LandState(**initial) if initial else LandState()
        result = simulate_land(forcing, params, state, grid, context)
        diagnostic_fields = LAND_DIAGNOSTIC_FIELDS
        final_state = asdict(state)
        component_map = [("RS", "RS"), ("RI", "RI"), ("RG", "RG")]
    if context.simulation_mode == "EVENT":
        result = result.loc[pd.Timestamp(payload["event"]["start"]):]

    components = []
    for time, row in result.iterrows():
        for component_type, column in component_map:
            components.append({
                "run_id": context.run_id, "unit_id": grid.unit_id, "time": time.isoformat(),
                "component_type": component_type, "value_mm": float(row[column]),
                "area_km2": grid.area_km2, "model_type": grid.zone_type,
                "param_set_id": context.param_set_id, "quality_flag": row["quality_flag"],
            })
    summary = {
        "precipitation_mm": float(result.precipitation.sum()),
        "total_runoff_mm": float(result.total_runoff.sum()),
        "RS_mm": float(result.RS.sum()), "RI_mm": float(result.RI.sum()),
        "RG_mm": float(result.RG.sum()), "R_glacier_mm": float(result.glacier_runoff.sum()),
        "et_actual_mm": float(result.et_actual.sum()),
        "max_abs_balance_error_mm": float(result.water_balance_error.abs().max()),
    }
    return {
        "status": "SUCCEEDED",
        "metadata": {**asdict(context), **asdict(grid), "record_count": len(result)},
        "summary": summary,
        "hourly": _records(result, COMMON_OUTPUT_FIELDS + diagnostic_fields),
        "runoff_component": components,
        "final_state": final_state,
    }


# [API-CAL-01] Synchronous calibration service; backend may wrap this as an async task
def run_calibration_request(payload):
    if "calibration_config" not in payload or "objective_config" not in payload or "parameter_space" not in payload:
        raise RequestValidationError(["calibration_config, objective_config and parameter_space are required"])
    observation_records = payload.get("observation")
    if not isinstance(observation_records, list) or not observation_records:
        raise RequestValidationError(["observation must be a non-empty array"])
    observation = pd.DataFrame(observation_records)
    observation["time"] = pd.to_datetime(observation["time"], errors="raise")
    value_field = payload.get("observation_value_field", "value")
    observed = observation.set_index("time")[value_field].astype(float)
    objective = CompositeObjective.from_config(payload["objective_config"])
    simulation_field = payload.get("simulation_field", "total_runoff")

    def evaluate(candidate):
        request = deepcopy(payload["simulation_request"])
        overrides = deepcopy(request.get("params", {}))
        for name, value in candidate.items():
            _set_parameter_path(overrides, name, value)
        request["params"] = overrides
        response = run_platform_request(request)
        hourly = pd.DataFrame(response["hourly"])
        hourly["time"] = pd.to_datetime(hourly["time"])
        simulated = hourly.set_index("time")[simulation_field].astype(float)
        diagnostics = {"water_balance_error": hourly["water_balance_error"].astype(float)}
        return objective.evaluate(observed, simulated, diagnostics)["loss"]

    result = CalibrationEngine().run(payload["parameter_space"], evaluate, payload["calibration_config"])
    return {"status": "SUCCEEDED", **asdict(result)}
