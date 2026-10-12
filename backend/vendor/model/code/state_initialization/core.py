"""[STATE-INITIALIZATION-03] State contracts, validation and serialization."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from runoff_platform.core.types import LandParams
from runoff_platform.models.xaj_vectorized import LAND_STATE_FIELDS
from xaj_routing.models import RoutingState


STATE_MODULE_ID = "STATE-INITIALIZATION-03"
STATE_SCHEMA_VERSION = "1.0"
SUPPORTED_STRATEGIES = {
    "FIXED_WARMUP",
    "OBSERVED_FLOW_ANCHORED",
    "CHECKPOINT",
    "CONTINUOUS",
}


class StateInitializationError(ValueError):
    """Raised when a state request or checkpoint violates the contract."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# [STATE-INITIALIZATION-03A] External initial-state template to grid arrays.
def build_uniform_land_state(
    template: Mapping[str, Any],
    grid_count: int,
    params: LandParams,
) -> dict[str, np.ndarray]:
    values = template.get("state", template)
    representation = str(template.get("representation", "ABSOLUTE_MM")).upper()
    if representation == "CAPACITY_FRACTION":
        required = {
            "swe",
            "snow_liquid",
            "WU_fraction",
            "WL_fraction",
            "WD_fraction",
            "S_fraction",
            "FR",
        }
        missing = sorted(required - values.keys())
        if missing:
            raise StateInitializationError(
                f"capacity-fraction initial state is missing fields: {missing}"
            )
        fractions = {
            name: float(values[name])
            for name in ("WU_fraction", "WL_fraction", "WD_fraction", "S_fraction")
        }
        if any(value < 0.0 or value > 1.0 for value in fractions.values()):
            raise StateInitializationError("initial storage fractions must be within [0, 1]")
        absolute = {
            "swe": float(values["swe"]),
            "snow_liquid": float(values["snow_liquid"]),
            "WU": fractions["WU_fraction"] * params.WUM,
            "WL": fractions["WL_fraction"] * params.WLM,
            "WD": fractions["WD_fraction"] * params.WDM,
            "S": fractions["S_fraction"] * params.SM,
            "FR": float(values["FR"]),
        }
    elif representation == "ABSOLUTE_MM":
        missing = [field for field in LAND_STATE_FIELDS if field not in values]
        if missing:
            raise StateInitializationError(f"initial land state is missing fields: {missing}")
        absolute = {field: float(values[field]) for field in LAND_STATE_FIELDS}
    else:
        raise StateInitializationError(
            "initial state representation must be ABSOLUTE_MM or CAPACITY_FRACTION"
        )
    arrays = {
        field: np.full(grid_count, absolute[field], dtype=float)
        for field in LAND_STATE_FIELDS
    }
    validate_land_state_arrays(arrays, params, grid_count)
    return arrays


def validate_land_state_arrays(
    state: Mapping[str, np.ndarray],
    params: LandParams,
    grid_count: int,
) -> None:
    limits = {
        "swe": (0.0, np.inf),
        "snow_liquid": (0.0, np.inf),
        "WU": (0.0, params.WUM),
        "WL": (0.0, params.WLM),
        "WD": (0.0, params.WDM),
        "S": (0.0, params.SM),
        "FR": (0.0, 1.0),
    }
    errors = []
    for field in LAND_STATE_FIELDS:
        if field not in state:
            errors.append(f"missing {field}")
            continue
        values = np.asarray(state[field], dtype=float)
        if values.shape != (grid_count,):
            errors.append(f"{field} shape is {values.shape}, expected ({grid_count},)")
            continue
        low, high = limits[field]
        if not np.isfinite(values).all() or np.any(values < low) or np.any(values > high):
            errors.append(f"{field} outside [{low}, {high}]")
    if errors:
        raise StateInitializationError("; ".join(errors))


# [STATE-INITIALIZATION-03B] Stable per-grid checkpoint representation.
def arrays_to_land_records(
    grid_ids: Sequence[str],
    state: Mapping[str, np.ndarray],
    zone_types: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    zone_types = zone_types or ["NON_GLACIER"] * len(grid_ids)
    if len(zone_types) != len(grid_ids):
        raise StateInitializationError("zone_types and grid_ids length mismatch")
    records = []
    for index, grid_id in enumerate(grid_ids):
        records.append(
            {
                "grid_id": str(grid_id),
                "zone_type": str(zone_types[index]).upper(),
                **{
                    field: float(np.asarray(state[field])[index])
                    for field in LAND_STATE_FIELDS
                },
            }
        )
    return records


def land_records_to_arrays(
    records: Sequence[Mapping[str, Any]],
    grid_ids: Sequence[str],
    params: LandParams,
) -> dict[str, np.ndarray]:
    by_grid = {str(record["grid_id"]): record for record in records}
    missing = [grid_id for grid_id in grid_ids if str(grid_id) not in by_grid]
    if missing:
        raise StateInitializationError(f"checkpoint is missing grid states: {missing[:5]}")
    arrays = {
        field: np.asarray(
            [float(by_grid[str(grid_id)][field]) for grid_id in grid_ids],
            dtype=float,
        )
        for field in LAND_STATE_FIELDS
    }
    validate_land_state_arrays(arrays, params, len(grid_ids))
    return arrays


# [STATE-INITIALIZATION-03C] Versioned checkpoint reader/writer.
def load_checkpoint(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if payload.get("module_id") != STATE_MODULE_ID:
        raise StateInitializationError("checkpoint module_id is not STATE-INITIALIZATION-03")
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise StateInitializationError(
            f"unsupported checkpoint schema_version: {payload.get('schema_version')}"
        )
    if not payload.get("state_id") or not payload.get("effective_time"):
        raise StateInitializationError("checkpoint requires state_id and effective_time")
    records = payload.get("land_states")
    if not isinstance(records, list) or not records:
        raise StateInitializationError("checkpoint land_states must be a non-empty list")
    required = {"grid_id", "zone_type", *LAND_STATE_FIELDS}
    grid_ids = []
    for record in records:
        missing = sorted(required - record.keys())
        if missing:
            raise StateInitializationError(
                f"checkpoint land state is missing fields: {missing}"
            )
        grid_ids.append(str(record["grid_id"]))
        for field in LAND_STATE_FIELDS:
            value = float(record[field])
            if not math.isfinite(value) or value < 0.0:
                raise StateInitializationError(
                    f"checkpoint {record['grid_id']} {field} must be finite and non-negative"
                )
        if float(record["FR"]) > 1.0:
            raise StateInitializationError(
                f"checkpoint {record['grid_id']} FR must be within [0, 1]"
            )
    if len(grid_ids) != len(set(grid_ids)):
        raise StateInitializationError("checkpoint contains duplicate grid_id values")
    routing_state = RoutingState.from_mapping(payload.get("routing_state"))
    routing_values = [
        value
        for components in routing_state.slope_outflow_cms.values()
        for value in components.values()
    ] + list(routing_state.reach_inflow_cms.values()) + list(
        routing_state.reach_outflow_cms.values()
    )
    if any(not math.isfinite(value) or value < 0.0 for value in routing_values):
        raise StateInitializationError(
            "checkpoint routing state values must be finite and non-negative"
        )
    return payload


def write_checkpoint(
    path: str | Path,
    payload: Mapping[str, Any],
    overwrite: bool = False,
) -> Path:
    output_path = Path(path)
    if output_path.exists() and not overwrite:
        raise StateInitializationError(
            f"state checkpoint already exists; provide a new state_id: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path
