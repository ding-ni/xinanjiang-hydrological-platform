"""Formal random-split multi-event calibration for the Nan'an-Xufan platform.

All events use one common parameter vector.  Complete events are randomly and
reproducibly assigned to calibration, validation and independent-test groups.
The search can use a reproducible subset of the calibration group for staged
calibration; the frozen best vector is always evaluated on every retained
event.  Grid runoff uses the same XAJ equations as
``runoff_platform.models.xaj`` and routing uses the platform routing model.

This is a non-glacier application.  Supplied precipitation is treated as
liquid and temperature is not used.  PET is an explicit command-line
assumption until observed hourly PET forcing is available.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from runoff_platform.config_loader import load_parameter_set  # noqa: E402
from runoff_platform.core.types import (  # noqa: E402
    daily_source_separation_to_hourly,
    hourly_source_separation_to_daily,
)
from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids  # noqa: E402
from xaj_routing.engine import RoutingEngine  # noqa: E402
from xaj_routing.io import (  # noqa: E402
    read_control_points_json,
    read_parameters_json,
    read_topology_json,
)
from xaj_routing.control_area import contributing_grid_areas  # noqa: E402
from xaj_routing.models import RoutingState, RunoffRecord  # noqa: E402
from calibration_platform.algorithms import search as search_algorithm  # noqa: E402
from calibration_platform.routing_kernel import route_arrays  # noqa: E402
from calibration_platform.qualification import (  # noqa: E402
    load_qualification_config,
    qualification_definition,
    qualify_event,
)
from runoff_platform.data.event_quality import evaluate_event_quality  # noqa: E402
from runoff_platform.data.pet_forcing import PetProvider  # noqa: E402
from runoff_platform.data.precipitation_quality import read_station_count  # noqa: E402
from state_initialization.core import (  # noqa: E402
    build_uniform_land_state,
)
from state_initialization.routing import build_observed_flow_anchored_state  # noqa: E402


CAL_YEARS = set(range(1964, 2000))
VAL_YEARS = set(range(2001, 2015))
TEST_YEARS = set(range(2015, 2025))
DEFAULT_TEMP_C = 15.0


@dataclass
class EventData:
    event_id: str
    year: int
    start: pd.Timestamp
    end: pd.Timestamp
    times: pd.DatetimeIndex
    precip: np.ndarray  # [time, grid]
    observed: pd.Series
    group: str
    forcing_start: pd.Timestamp | None = None
    source_station_count: np.ndarray | None = None
    pet_mm_h: np.ndarray | None = None


@dataclass
class PreparedRunoff:
    """Frozen XAJ output reused by every routing-only candidate."""

    event: EventData
    unit_rs: np.ndarray
    unit_ri: np.ndarray
    unit_rg: np.ndarray
    final_land_state: dict[str, np.ndarray]


def find_package_root(data_root: Path) -> Path:
    for candidate in data_root.rglob("*"):
        if candidate.is_dir() and any(candidate.glob("*.nc")):
            return candidate.parent if candidate.name else candidate
    # The normal package has a directory containing the hourly netCDF files.
    candidates = list(data_root.rglob("*.nc"))
    if not candidates:
        raise FileNotFoundError(f"no precipitation netCDF found below {data_root}")
    return candidates[0].parent.parent


def find_index(data_root: Path) -> Path:
    for path in data_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("packages"), list):
            return path
    raise FileNotFoundError("event index JSON with a packages list was not found")


def find_observed_flow(data_root: Path) -> pd.Series:
    files = list(data_root.rglob("*.xlsx"))
    if not files:
        raise FileNotFoundError("no observed-flow workbook found")
    for path in files:
        try:
            sheets = pd.ExcelFile(path).sheet_names
            sheet = next((s for s in sheets if "流量" in s or "水位" in s), None)
            if sheet is None:
                continue
            frame = pd.read_excel(path, sheet_name=sheet, usecols=[0, 2])
            frame.columns = ["time", "flow"]
            frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
            frame["flow"] = pd.to_numeric(frame["flow"], errors="coerce")
            frame = frame.dropna().drop_duplicates("time").set_index("time").sort_index()
            if len(frame) > 100:
                return frame["flow"]
        except Exception:
            continue
    raise FileNotFoundError("could not identify an observed-flow worksheet")


def find_precip_dir(data_root: Path) -> Path:
    files = list(data_root.rglob("nananxi_precip_hourly_1km_*.nc"))
    if not files:
        raise FileNotFoundError("hourly precipitation netCDF files not found")
    return files[0].parent


def open_precipitation_dataset(path: Path):
    """Open platform NetCDF with the first backend installed in the runtime."""
    available = set(xr.backends.list_engines())
    for engine in ("h5netcdf", "netcdf4", "scipy"):
        if engine in available:
            return xr.open_dataset(path, engine=engine)
    return xr.open_dataset(path)


def load_grids(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def eligible_events(
    data_root: Path,
    warmup_hours: int,
    event_catalog: Path | None = None,
) -> tuple[list[dict], dict]:
    catalog_path = event_catalog.resolve() if event_catalog is not None else find_index(data_root)
    payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    events = []
    exclusions: dict[str, int] = {}
    for item in payload["packages"]:
        start = pd.Timestamp(item["start"])
        end = pd.Timestamp(item["end"])
        observed = item.get("observedInputs", {})
        reason = None
        if start.year not in CAL_YEARS | VAL_YEARS | TEST_YEARS or end.year != start.year:
            reason = "year_not_supported_or_cross_year"
        elif observed.get("rainCompleteness", 0.0) < 0.999:
            reason = "rain_incomplete"
        elif observed.get("riverCompleteness", 0.0) < 0.999:
            reason = "river_incomplete"
        elif end <= start:
            reason = "invalid_window"
        if reason:
            exclusions[reason] = exclusions.get(reason, 0) + 1
            continue
        events.append({"event_id": item["eventId"], "start": start, "end": end, "year": start.year, "group": "UNASSIGNED"})
    events.sort(key=lambda x: x["start"])
    return events, {
        "catalog_path": str(catalog_path),
        "catalog_count": len(payload["packages"]),
        "eligible_count": len(events),
        "exclusions": exclusions,
    }


def random_split_events(events: list[dict], seed: int, calibration_ratio: float, validation_ratio: float) -> tuple[list[dict], dict]:
    """Randomly assign complete events to one of three disjoint groups."""
    if not events:
        return [], {"seed": seed, "ratios": {}}
    if calibration_ratio <= 0 or validation_ratio <= 0 or calibration_ratio + validation_ratio >= 1:
        raise ValueError("calibration and validation ratios must be positive and sum to less than one")
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(events))
    n_cal = int(round(len(events) * calibration_ratio))
    n_val = int(round(len(events) * validation_ratio))
    if len(events) < 3:
        n_cal, n_val = 1, 0
    else:
        n_cal = min(max(n_cal, 1), len(events) - 2)
        n_val = min(max(n_val, 1), len(events) - n_cal - 1)
    labels = ["INDEPENDENT_TEST"] * len(events)
    for pos in order[:n_cal]:
        labels[int(pos)] = "CALIBRATION"
    for pos in order[n_cal : n_cal + n_val]:
        labels[int(pos)] = "VALIDATION"
    assigned = []
    for index, event in enumerate(events):
        assigned.append({**event, "group": labels[index]})
    return assigned, {"method": "RANDOM_EVENT_SPLIT", "seed": seed, "ratios": {"calibration": calibration_ratio, "validation": validation_ratio, "independent_test": 1.0 - calibration_ratio - validation_ratio}, "counts": {name: labels.count(name) for name in ("CALIBRATION", "VALIDATION", "INDEPENDENT_TEST")}}


def load_event_data(data_root: Path, events: list[dict], grids: list[dict], observed: pd.Series, warmup_hours: int, continuous: bool = True, precip_dir: Path | None = None, event_rainfall_scale: dict[str, float] | None = None) -> list[EventData]:
    # Keep an explicitly supplied ASCII junction path intact on Windows.
    # Path.resolve() expands it back to the Chinese source path, which the
    # installed NetCDF C backend cannot open reliably in this runtime.
    precip_dir = precip_dir.absolute() if precip_dir is not None else find_precip_dir(data_root)
    if not precip_dir.is_dir():
        raise FileNotFoundError(f"precipitation directory not found: {precip_dir}")
    array_positions = [(int(g["array_row"]), int(g["array_col"])) for g in grids]
    result: list[EventData] = []
    previous_end: pd.Timestamp | None = None
    cached_year: int | None = None
    cached_times: pd.DatetimeIndex | None = None
    cached_values: np.ndarray | None = None
    cached_station_count: np.ndarray | None = None

    def load_year(year: int) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
        nonlocal cached_year, cached_times, cached_values, cached_station_count
        if cached_year == year and cached_times is not None and cached_values is not None and cached_station_count is not None:
            return cached_times, cached_values, cached_station_count
        path = precip_dir / f"nananxi_precip_hourly_1km_{year}.nc"
        with open_precipitation_dataset(path) as dataset:
            cached_times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
            values = np.asarray(dataset["precipitation"].values, dtype=np.float32)
            cached_values = np.stack([values[:, row, col] for row, col in array_positions], axis=1)
            cached_station_count, _ = read_station_count(dataset)
        cached_year = year
        return cached_times, cached_values, cached_station_count

    for event in sorted(events, key=lambda item: item["start"]):
        warmup_start = event["start"] - pd.Timedelta(hours=warmup_hours)
        segment_start = warmup_start if not continuous or previous_end is None else previous_end + pd.Timedelta(hours=1)
        if segment_start > event["start"]:
            raise ValueError(f"overlapping events are not supported in continuous mode: {event['event_id']}")
        time_parts: list[pd.DatetimeIndex] = []
        value_parts: list[np.ndarray] = []
        station_count_parts: list[np.ndarray] = []
        for year in range(segment_start.year, event["end"].year + 1):
            all_times, grid_values, station_counts = load_year(year)
            mask = (all_times >= segment_start) & (all_times <= event["end"])
            if np.any(mask):
                time_parts.append(all_times[mask])
                value_parts.append(grid_values[mask])
                station_count_parts.append(station_counts[mask])
        times = time_parts[0].append(time_parts[1:]) if time_parts else pd.DatetimeIndex([])
        forcing = np.concatenate(value_parts, axis=0) if value_parts else np.empty((0, len(grids)), dtype=np.float32)
        source_station_count = np.concatenate(station_count_parts) if station_count_parts else np.empty(0, dtype=np.int16)
        if len(times) == 0 or times[0] != segment_start or times[-1] != event["end"]:
            raise ValueError(f"precipitation does not cover {event['event_id']} continuous window")
        expected = pd.date_range(segment_start, event["end"], freq="h")
        if len(times) != len(expected) or not times.equals(expected):
            raise ValueError(f"hourly precipitation has a gap before {event['event_id']}")
        if event_rainfall_scale and event["event_id"] in event_rainfall_scale:
            event_mask = (times >= event["start"]) & (times <= event["end"])
            forcing[event_mask, :] *= float(event_rainfall_scale[event["event_id"]])
        obs = observed.loc[event["start"] : event["end"]]
        if obs.empty:
            continue
        result.append(EventData(event["event_id"], event["year"], event["start"], event["end"], times, np.maximum(forcing, 0.0), obs, event["group"], segment_start, source_station_count))
        previous_end = event["end"]
    result.sort(key=lambda x: x.start)
    return result


def vectorized_xaj(precip: np.ndarray, params, areas: np.ndarray, temperature_c: float, pet_mm_h: float | np.ndarray, state: dict[str, np.ndarray] | None = None, return_state: bool = False):
    """Compatibility wrapper around the shared XAJ-WARMUP-CORE module."""
    if state is None:
        raise ValueError("vectorized XAJ requires an explicit platform initial state")
    rs, ri, rg, final_state = simulate_xaj_liquid_grids(
        precip,
        params,
        pet_mm_h,
        state,
    )
    return (rs, ri, rg, final_state) if return_state else (rs, ri, rg)


def build_mapping(path: Path, topology_path: Path, grids: list[dict]) -> tuple[list[str], np.ndarray]:
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    units = [str(item["unit_id"]) for item in topology["units"]]
    area_by_unit = {str(item["unit_id"]): float(item["area_km2"]) for item in topology["units"]}
    grid_index = {str(row["unit_id"]): i for i, row in enumerate(grids)}
    matrix = np.zeros((len(grids), len(units)), dtype=float)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            gid = row["grid_id"]
            uid = row["unit_id"]
            if gid in grid_index and uid in area_by_unit:
                matrix[grid_index[gid], units.index(uid)] += float(row["adjusted_overlap_area_km2"]) / area_by_unit[uid]
    return units, matrix


def route_event(event: EventData, params, routing_payload: dict, base_routing, units: list[str], mapping: np.ndarray, topology_path: Path, controls_path: Path, land_state=None, routing_state=None, pet_mm_h: float | np.ndarray | None = None):
    if pet_mm_h is None:
        pet_mm_h = event.pet_mm_h
    if pet_mm_h is None:
        raise ValueError("event PET forcing has not been prepared")
    rs, ri, rg, final_land_state = vectorized_xaj(event.precip, params, np.ones(event.precip.shape[1]), DEFAULT_TEMP_C, pet_mm_h, state=land_state, return_state=True)
    # Area-weighted grid depths to routing-unit depths. The mapping matrix has
    # overlap area divided by routing-unit area in each cell.
    unit_rs = rs @ mapping; unit_ri = ri @ mapping; unit_rg = rg @ mapping
    records: list[RunoffRecord] = []
    for t_index, timestamp in enumerate(event.times):
        dt = timestamp.to_pydatetime()
        for u_index, unit_id in enumerate(units):
            records.append(RunoffRecord(unit_id=unit_id, time=dt, RS=float(unit_rs[t_index, u_index]), RI=float(unit_ri[t_index, u_index]), RG=float(unit_rg[t_index, u_index])))
    engine = RoutingEngine(topology=read_topology_json(topology_path), parameters=base_routing, initial_state=routing_state, control_points=read_control_points_json(controls_path))
    result = engine.run(records)
    rows = [{"time": row.time, "flow": row.outflow_cms} for row in result.control_flows]
    if not rows:
        return pd.Series(dtype=float), final_land_state, result.final_state
    frame = pd.DataFrame(rows)
    return frame.groupby("time")["flow"].mean().sort_index(), final_land_state, result.final_state


class FastRoutingRunner:
    """Array implementation of the same slope-reservoir and Muskingum steps."""

    def __init__(self, topology_path: Path, units: list[str], controls_path: Path):
        topology = read_topology_json(topology_path)
        self.topology = topology
        self.unit_ids = units
        self.unit_area = np.asarray([topology.units[unit_id].area_km2 for unit_id in units], dtype=float)
        self.reach_ids = topology.reaches_upstream_first()
        self.reach_index = {reach_id: index for index, reach_id in enumerate(self.reach_ids)}
        self.unit_reach = np.asarray([self.reach_index.get(topology.units[unit_id].reach_id, -1) for unit_id in units], dtype=int)
        self.downstream = np.asarray([self.reach_index.get(topology.reaches[reach_id].downstream_reach_id, -1) for reach_id in self.reach_ids], dtype=int)
        controls = read_control_points_json(controls_path)
        if len(controls) != 1:
            raise ValueError("formal calibration requires exactly one control point")
        self.control_point = controls[0]
        node = topology.control_node(self.control_point.control_id)
        if node and node.get("upstream_reach_id"):
            from dataclasses import replace
            self.control_point = replace(self.control_point, reach_id=str(node["upstream_reach_id"]))
        self.control_reach = self.reach_index[self.control_point.reach_id]

    # [STATE-INITIALIZATION-03H] Standard checkpoint to fast routing arrays.
    def standard_state_to_fast(self, state: RoutingState) -> dict[str, np.ndarray]:
        slope_state = np.zeros((len(self.unit_ids), 3), dtype=float)
        for unit_index, unit_id in enumerate(self.unit_ids):
            values = state.slope_outflow_cms.get(unit_id, {})
            for component_index, component in enumerate(("RS", "RI", "RG")):
                slope_state[unit_index, component_index] = float(values.get(component, 0.0))
        return {
            "slope_state": slope_state,
            "prev_in": np.asarray(
                [state.reach_inflow_cms.get(reach_id, 0.0) for reach_id in self.reach_ids],
                dtype=float,
            ),
            "prev_out": np.asarray(
                [state.reach_outflow_cms.get(reach_id, 0.0) for reach_id in self.reach_ids],
                dtype=float,
            ),
        }

    def observed_flow_anchor(self, observed_flow_cms: float) -> dict[str, np.ndarray]:
        state = build_observed_flow_anchored_state(
            self.topology,
            self.control_point,
            observed_flow_cms,
        )
        return self.standard_state_to_fast(state)

    @staticmethod
    def _coefficients(k_hours: float, x: float) -> tuple[float, float, float]:
        k_seconds = float(k_hours) * 3600.0
        dt = 3600.0
        denominator = k_seconds - k_seconds * x + 0.5 * dt
        return ((-k_seconds * x + 0.5 * dt) / denominator, (k_seconds * x + 0.5 * dt) / denominator, (k_seconds - k_seconds * x - 0.5 * dt) / denominator)

    def run(self, times: pd.DatetimeIndex, unit_rs: np.ndarray, unit_ri: np.ndarray, unit_rg: np.ndarray, routing, state=None):
        if float(routing.time_step_hours) != 1.0:
            raise ValueError("FastRoutingRunner supports only a 1-hour routing step")
        times = pd.DatetimeIndex(times)
        if times.hasnans or (len(times) > 1 and np.any(times[1:] - times[:-1] != pd.Timedelta(hours=1))):
            raise ValueError("routing timestamps must be consecutive 1-hour steps")
        n_units = len(self.unit_ids); n_reaches = len(self.reach_ids)
        arrays = tuple(np.asarray(value, dtype=float) for value in (unit_rs, unit_ri, unit_rg))
        expected = (len(times), n_units)
        if any(value.shape != expected for value in arrays):
            raise ValueError(f"unit runoff arrays must all have shape {expected}")
        if any(not np.isfinite(value).all() or np.any(value < 0.0) for value in arrays):
            raise ValueError("unit runoff arrays must be finite and non-negative")
        if state is None:
            slope_state = np.zeros((n_units, 3), dtype=float); prev_in = np.zeros(n_reaches, dtype=float); prev_out = np.zeros(n_reaches, dtype=float)
        else:
            slope_state = state["slope_state"].copy(); prev_in = state["prev_in"].copy(); prev_out = state["prev_out"].copy()
            for value, shape in ((slope_state, (n_units, 3)), (prev_in, (n_reaches,)), (prev_out, (n_reaches,))):
                if value.shape != shape or not np.isfinite(value).all():
                    raise ValueError("routing checkpoint arrays have invalid shape or non-finite values")
        overrides = routing.unit_component_slope_k_hours
        if set(overrides) - set(self.unit_ids):
            raise ValueError("unit-specific slope parameters reference unknown routing units")
        slope_k = np.asarray([[routing.slope_k_seconds(unit, component) / 3600.0
                               for component in ("RS", "RI", "RG")]
                              for unit in self.unit_ids], dtype=float)
        if not np.isfinite(slope_k).all() or np.any(slope_k <= 0.0):
            raise ValueError("all slope-reservoir K values must be finite and positive")
        slope_decay = np.exp(-1.0 / slope_k)
        coefficients = np.asarray([self._coefficients(routing.reach_muskingum[reach_id].k_hours, routing.reach_muskingum[reach_id].x) for reach_id in self.reach_ids], dtype=float)
        flow, slope_state, prev_in, prev_out = route_arrays(
            np.stack(arrays, axis=2), self.unit_area, self.unit_reach,
            self.downstream, slope_decay, coefficients, self.control_reach,
            slope_state, prev_in, prev_out)
        final_state = {"slope_state": slope_state, "prev_in": prev_in, "prev_out": prev_out}
        return pd.Series(flow, index=times), final_state


def route_event_fast(
    event: EventData,
    params,
    routing,
    router: FastRoutingRunner,
    mapping: np.ndarray,
    land_state,
    routing_state=None,
    pet_mm_h: float | np.ndarray | None = None,
    routing_starts_at_event: bool = False,
):
    if pet_mm_h is None:
        pet_mm_h = event.pet_mm_h
    if pet_mm_h is None:
        raise ValueError("event PET forcing has not been prepared")
    rs, ri, rg, final_land_state = vectorized_xaj(event.precip, params, np.ones(event.precip.shape[1]), DEFAULT_TEMP_C, pet_mm_h, state=land_state, return_state=True)
    unit_rs = rs @ mapping; unit_ri = ri @ mapping; unit_rg = rg @ mapping
    start_index = int(event.times.searchsorted(event.start)) if routing_starts_at_event else 0
    if routing_starts_at_event and (
        start_index >= len(event.times) or event.times[start_index] != event.start
    ):
        raise ValueError(f"event start is not present in forcing: {event.event_id}")
    simulated, final_routing_state = router.run(
        event.times[start_index:],
        unit_rs[start_index:],
        unit_ri[start_index:],
        unit_rg[start_index:],
        routing,
        routing_state,
    )
    return simulated, final_land_state, final_routing_state


# [ROUTING-RUNOFF-CACHE-13A] Compute XAJ and grid-to-routing aggregation once.
def prepare_runoff_event(
    event: EventData,
    params,
    mapping: np.ndarray,
    land_state,
    pet_mm_h: float | np.ndarray | None = None,
) -> PreparedRunoff:
    if pet_mm_h is None:
        pet_mm_h = event.pet_mm_h
    if pet_mm_h is None:
        raise ValueError("event PET forcing has not been prepared")
    rs, ri, rg, final_land_state = vectorized_xaj(
        event.precip,
        params,
        np.ones(event.precip.shape[1]),
        DEFAULT_TEMP_C,
        pet_mm_h,
        state=land_state,
        return_state=True,
    )
    return PreparedRunoff(
        event=event,
        unit_rs=np.asarray(rs @ mapping, dtype=np.float32),
        unit_ri=np.asarray(ri @ mapping, dtype=np.float32),
        unit_rg=np.asarray(rg @ mapping, dtype=np.float32),
        final_land_state=final_land_state,
    )


# [ROUTING-RUNOFF-CACHE-13B] Routing candidates consume cached RS/RI/RG only.
def route_prepared_runoff(
    prepared: PreparedRunoff,
    routing,
    router: FastRoutingRunner,
    routing_state=None,
    routing_starts_at_event: bool = False,
):
    event = prepared.event
    start_index = int(event.times.searchsorted(event.start)) if routing_starts_at_event else 0
    if routing_starts_at_event and (
        start_index >= len(event.times) or event.times[start_index] != event.start
    ):
        raise ValueError(f"event start is not present in forcing: {event.event_id}")
    simulated, final_routing_state = router.run(
        event.times[start_index:],
        prepared.unit_rs[start_index:],
        prepared.unit_ri[start_index:],
        prepared.unit_rg[start_index:],
        routing,
        routing_state,
    )
    return simulated, prepared.final_land_state, final_routing_state


def metrics(observed: pd.Series, simulated: pd.Series, qualification_config=None) -> dict[str, float | int | bool | None]:
    paired = pd.concat([observed.rename("observed"), simulated.rename("simulated")], axis=1).dropna()
    if paired.empty:
        return {"paired_hours": 0, "qualified": False}
    obs = paired.observed.to_numpy(float); sim = paired.simulated.to_numpy(float)
    mean_obs = float(np.mean(obs)); den = float(np.sum((obs - mean_obs) ** 2))
    nse = None if den == 0 else 1.0 - float(np.sum((obs - sim) ** 2)) / den
    std_o = float(np.std(obs)); std_s = float(np.std(sim)); corr = float(np.corrcoef(obs, sim)[0, 1]) if std_o > 0 and std_s > 0 else 0.0
    alpha = std_s / std_o if std_o > 0 else float("nan"); beta = float(np.mean(sim) / mean_obs) if mean_obs else float("nan")
    kge = None if not np.isfinite(alpha + beta) else 1.0 - math.sqrt((corr - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2)
    op = float(np.max(obs)); sp = float(np.max(sim)); ot = paired.index[int(np.argmax(obs))]; st = paired.index[int(np.argmax(sim))]
    peak_error = 100.0 * (sp - op) / op if op else float("nan"); volume_error = 100.0 * (float(np.sum(sim)) - float(np.sum(obs))) / float(np.sum(obs)) if float(np.sum(obs)) else float("nan")
    time_error = abs((st - ot).total_seconds()) / 3600.0
    result = {"paired_hours": int(len(paired)), "NSE": nse, "KGE": kge, "PBIAS_percent": volume_error, "observed_peak_m3_s": op, "simulated_peak_m3_s": sp, "peak_error_percent": peak_error, "peak_time_error_hours": time_error, "volume_error_percent": volume_error}
    result["qualified"] = qualify_event(result, qualification_config)
    return result


def routing_from_candidate(base_payload: dict, candidate: dict) -> Any:
    payload = json.loads(json.dumps(base_payload))
    for name in ("RS", "RI", "RG"):
        key = f"routing.{name}_k_hours"
        if key in candidate:
            payload["default_slope_k_hours"][name] = float(candidate[key])
    scale = float(candidate.get("routing.muskingum_k_scale", 1.0))
    x_override = candidate.get("routing.muskingum_x")
    for value in payload.get("reach_muskingum", {}).values():
        value["k_hours"] = float(value["k_hours"]) * scale
        if x_override is not None:
            value["x"] = float(x_override)
    return read_parameters_json_from_mapping(payload)


def read_parameters_json_from_mapping(payload):
    from xaj_routing.models import RoutingParameters
    return RoutingParameters.from_mapping(payload)


def candidate_params(base, candidate):
    for name in ("KC", "WM", "WUM", "WLM", "B", "C", "SM", "EX", "IM"):
        setattr(base, name, float(candidate.get(f"runoff.{name}", getattr(base, name))))
    direct_source = "runoff.KI" in candidate or "runoff.KG" in candidate
    derived_source = (
        "runoff.K_total_day" in candidate
        or "runoff.groundwater_fraction" in candidate
    )
    if direct_source and derived_source:
        raise ValueError("candidate cannot mix direct KI/KG with derived source parameters")
    if derived_source:
        default_total, default_groundwater = hourly_source_separation_to_daily(
            base.KI,
            base.KG,
        )
        total_day = float(candidate.get("runoff.K_total_day", default_total))
        groundwater_fraction = float(
            candidate.get("runoff.groundwater_fraction", default_groundwater)
        )
        base.KI, base.KG = daily_source_separation_to_hourly(
            total_day,
            groundwater_fraction,
        )
    else:
        base.KI = float(candidate.get("runoff.KI", base.KI))
        base.KG = float(candidate.get("runoff.KG", base.KG))
    base.source_coefficient_time_step_hours = 1.0
    # Keep the physically required XAJ constraints explicit.
    if base.WM <= base.WUM + base.WLM or base.KI + base.KG >= 0.98:
        raise ValueError("invalid candidate: WM must exceed WUM+WLM and KI+KG must be below 1")
    return base


def resolved_runoff_parameters(params):
    """Actual hourly model parameters exported for simulation and handoff."""
    return {
        f"runoff.{name}": float(getattr(params, name))
        for name in ("KC", "WM", "WUM", "WLM", "B", "C", "SM", "EX", "KI", "KG", "IM")
    }


def summary_for(results: pd.DataFrame) -> dict:
    if results.empty:
        return {"event_count": 0, "qualified_count": 0, "qualified_rate_percent": 0.0}
    return {"event_count": int(len(results)), "qualified_count": int(results.qualified.sum()), "qualified_rate_percent": float(results.qualified.mean() * 100), "mean_NSE": float(results.NSE.mean()), "mean_KGE": float(results.KGE.mean()), "mean_abs_peak_error_percent": float(results.peak_error_percent.abs().mean()), "mean_abs_volume_error_percent": float(results.volume_error_percent.abs().mean()), "median_peak_time_error_hours": float(results.peak_time_error_hours.median())}


def objective_loss(frame: pd.DataFrame, specs: list[dict[str, Any]]) -> float:
    """Convert configurable event-average metrics into a minimization loss."""
    if frame.empty or not specs:
        raise ValueError("objective requires non-empty metrics and terms")
    utilities = []
    for spec in specs:
        name = str(spec["metric"]).upper()
        if name == "NSE":
            raw = float(frame["NSE"].fillna(-1.0).mean()); utility = raw
        elif name == "KGE":
            raw = float(frame["KGE"].fillna(-1.0).mean()); utility = raw
        elif name in {"PBIAS", "RUNOFF_DEPTH_ERROR", "VOLUME_ERROR"}:
            raw = float(frame["volume_error_percent"].abs().mean()); target = float(spec.get("target", 0.0)); scale = float(spec.get("scale", 100.0)); utility = -abs(raw - target) / scale
        elif name in {"PEAK_ERROR", "PEAK_RELATIVE_ERROR"}:
            raw = float(frame["peak_error_percent"].abs().mean()); target = float(spec.get("target", 0.0)); scale = float(spec.get("scale", 100.0)); utility = -abs(raw - target) / scale
        elif name in {"PEAK_TIME_ERROR", "PEAK_TIME_ERROR_HOURS"}:
            raw = float(frame["peak_time_error_hours"].mean()); target = float(spec.get("target", 0.0)); scale = float(spec.get("scale", 24.0)); utility = -abs(raw - target) / scale
        else:
            raise ValueError(f"unsupported objective metric: {name}")
        utilities.append(float(spec.get("weight", 1.0)) * utility)
    weight_sum = sum(float(spec.get("weight", 1.0)) for spec in specs)
    if weight_sum <= 0:
        raise ValueError("objective weights must sum to a positive value")
    return -sum(utilities) / weight_sum


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--event-catalog", help="explicit QC-rebuilt event catalog; defaults to discovery below data-root")
    parser.add_argument("--output-dir", default=str(ROOT / "validation" / "formal_multi_event_20260818"))
    parser.add_argument("--warmup-hours", type=int, default=None, help="optional override of state_config.fixed_warmup_hours")
    parser.add_argument("--max-evaluations", type=int, default=200, help="maximum candidate parameter sets evaluated")
    parser.add_argument("--algorithm", choices=("RANDOM_SEARCH", "GRID_SEARCH", "DIFFERENTIAL_EVOLUTION"), default="DIFFERENTIAL_EVOLUTION")
    parser.add_argument("--population-size", type=int, default=24)
    parser.add_argument("--generations", type=int, default=10)
    parser.add_argument("--differential-weight", type=float, default=0.8)
    parser.add_argument("--crossover-probability", type=float, default=0.7)
    parser.add_argument("--objective-config", default=str(ROOT / "config" / "objective_config_event.json"))
    parser.add_argument("--parameter-space", default=str(ROOT / "config" / "parameter_space_runoff_multievent_hourly.json"), help="independent calibration bounds; default optimizes hourly runoff parameters and freezes routing")
    parser.add_argument("--base-parameter-set", default=str(ROOT / "config" / "parameter_set_land.json"), help="frozen baseline runoff parameter set; routing-only searches do not modify it")
    parser.add_argument("--data-review-config", default=str(ROOT / "config" / "control_section_data_review_xufan.json"), help="control-section data review used to qualify the calibration result")
    parser.add_argument("--qualification-config", default=str(ROOT / "config" / "qualification_config_event.json"), help="single event qualification-rate definition; strict combined rate is disabled")
    parser.add_argument("--pet-config", default=str(ROOT / "config" / "pet_forcing_assumed_monthly.json"), help="PET-FORCING-02 JSON used by every event")
    parser.add_argument("--pet-mm-h", type=float, default=None, help="deprecated constant diagnostic override")
    parser.add_argument("--precip-dir", default=None, help="explicit corrected 1 km precipitation directory; defaults to discovery below data-root")
    parser.add_argument("--event-rainfall-scale", default=None, help="diagnostic JSON mapping event_id to total-rainfall multiplier; scales only event hours")
    parser.add_argument("--event-quality-config", default=str(ROOT / "config" / "event_quality_config.json"), help="physical event selection rules applied before random split")
    parser.add_argument("--limit-events", type=int, default=0, help="debug limit; zero keeps all eligible events")
    parser.add_argument("--calibration-event-limit", type=int, default=0, help="optional staged-search sample from the random calibration group; zero uses all calibration events")
    parser.add_argument("--state-config", default=str(ROOT / "config" / "state_initialization_config.json"), help="STATE-INITIALIZATION-03 policy and defaults")
    parser.add_argument("--initial-land-state", default=str(ROOT / "config" / "initial_state_land.json"), help="external state template used at the start of warm-up")
    parser.add_argument("--state-strategy", choices=("FIXED_WARMUP", "OBSERVED_FLOW_ANCHORED", "CONTINUOUS"), default=None)
    parser.add_argument("--state-mode", choices=("CONTINUOUS", "EVENT_WARMUP"), default=None, help="deprecated compatibility alias")
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument("--calibration-ratio", type=float, default=0.667)
    parser.add_argument("--validation-ratio", type=float, default=0.201)
    parser.add_argument(
        "--all-events-calibration",
        action="store_true",
        help="use every accepted event as a calibration event; no random validation split",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    out = Path(args.output_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    data_root = Path(args.data_root).resolve()
    state_config_path = Path(args.state_config).resolve()
    state_config = json.loads(state_config_path.read_text(encoding="utf-8-sig"))
    state_strategy = str(args.state_strategy or state_config["default_strategy"]).upper()
    if args.state_mode:
        legacy_strategy = "CONTINUOUS" if args.state_mode == "CONTINUOUS" else "FIXED_WARMUP"
        if args.state_strategy and state_strategy != legacy_strategy:
            raise ValueError("--state-mode conflicts with --state-strategy")
        state_strategy = legacy_strategy
    warmup_hours = int(args.warmup_hours if args.warmup_hours is not None else state_config["fixed_warmup_hours"])
    if warmup_hours <= 0:
        raise ValueError("state warmup_hours must be positive")
    if args.pet_mm_h is None:
        pet_provider = PetProvider.from_file(args.pet_config)
    else:
        pet_provider = PetProvider(
            {
                "mode": "CONSTANT_ASSUMPTION",
                "value_mm_h": float(args.pet_mm_h),
                "production_eligible": False,
                "provenance": "Deprecated command-line diagnostic override.",
            },
            ROOT,
        )
    initial_land_state_path = Path(args.initial_land_state).resolve()
    initial_land_template = json.loads(initial_land_state_path.read_text(encoding="utf-8-sig"))
    grid_path = ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"
    grids = load_grids(grid_path)
    event_catalog_path = Path(args.event_catalog).resolve() if args.event_catalog else None
    events_meta, metadata_selection = eligible_events(
        data_root,
        warmup_hours,
        event_catalog_path,
    )
    if args.limit_events:
        events_meta = events_meta[: args.limit_events]
        metadata_selection["debug_event_limit"] = int(args.limit_events)
    observed = find_observed_flow(data_root)
    selected_precip_dir = Path(args.precip_dir).absolute() if args.precip_dir else None
    event_rainfall_scale = None
    if args.event_rainfall_scale:
        event_rainfall_scale = {str(k): float(v) for k, v in json.loads(Path(args.event_rainfall_scale).read_text(encoding="utf-8-sig")).items()}
    quality_events = load_event_data(data_root, events_meta, grids, observed, warmup_hours, continuous=False, precip_dir=selected_precip_dir, event_rainfall_scale=event_rainfall_scale)
    topology_path = ROOT / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"
    mapping_path = ROOT / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"
    controls_path = ROOT / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"
    event_quality_path = Path(args.event_quality_config).resolve()
    event_quality_config = json.loads(event_quality_path.read_text(encoding="utf-8-sig"))
    control_points = read_control_points_json(controls_path)
    if len(control_points) != 1:
        raise ValueError("formal calibration requires exactly one control point")
    control_grid_areas = contributing_grid_areas(
        mapping_path,
        read_topology_json(topology_path),
        control_points[0].reach_id,
        [str(grid["unit_id"]) for grid in grids],
    )
    quality_grid_indexes = np.flatnonzero(control_grid_areas > 0.0)
    if quality_grid_indexes.size == 0:
        raise RuntimeError("control section has no upstream grids for event quality")
    event_quality_rows = [
        evaluate_event_quality(
            event_id=event.event_id,
            start=event.start,
            end=event.end,
            times=event.times,
            grid_precipitation_mm=event.precip[:, quality_grid_indexes],
            grid_areas_km2=control_grid_areas[quality_grid_indexes],
            observed_flow_cms=event.observed,
            source_station_count=event.source_station_count,
            config=event_quality_config,
        )
        for event in quality_events
    ]
    accepted_ids = {row["event_id"] for row in event_quality_rows if row["accepted"]}
    exclusion_counts = Counter(reason for row in event_quality_rows for reason in row["exclusion_reasons"])
    warning_counts = Counter(reason for row in event_quality_rows for reason in row["warning_reasons"])
    quality_export = pd.DataFrame(
        [
            {
                **row,
                "exclusion_reasons": ";".join(row["exclusion_reasons"]),
                "warning_reasons": ";".join(row["warning_reasons"]),
            }
            for row in event_quality_rows
        ]
    )
    quality_export.to_csv(out / "event_quality.csv", index=False, encoding="utf-8-sig")
    if not accepted_ids:
        raise RuntimeError(
            "event quality rejected every event; use corrected precipitation with "
            "source_station_count/reporting_station_count and inspect event_quality.csv"
        )
    events_meta = [event for event in events_meta if event["event_id"] in accepted_ids]
    if args.all_events_calibration:
        events_meta = [{**event, "group": "CALIBRATION"} for event in events_meta]
        split = {
            "method": "ALL_ACCEPTED_EVENTS_CALIBRATION",
            "seed": args.split_seed,
            "ratios": {"calibration": 1.0, "validation": 0.0, "independent_test": 0.0},
            "counts": {"CALIBRATION": len(events_meta), "VALIDATION": 0, "INDEPENDENT_TEST": 0},
        }
    else:
        events_meta, split = random_split_events(events_meta, args.split_seed, args.calibration_ratio, args.validation_ratio)
    selected_ids = {event["event_id"] for event in events_meta}
    group_by_id = {event["event_id"]: event["group"] for event in events_meta}
    if state_strategy in {"FIXED_WARMUP", "OBSERVED_FLOW_ANCHORED"}:
        events = [event for event in quality_events if event.event_id in selected_ids]
        for event in events:
            event.group = group_by_id[event.event_id]
    else:
        events = load_event_data(data_root, events_meta, grids, observed, warmup_hours, continuous=True, precip_dir=selected_precip_dir, event_rainfall_scale=event_rainfall_scale)
    # [PET-FORCING-02A] Prepare one immutable hourly PET vector per event.
    for event in events:
        event.pet_mm_h = pet_provider.values(event.times)
    selection = {
        **metadata_selection,
        "event_quality": {
            "config_path": str(event_quality_path),
            "evaluated_count": len(event_quality_rows),
            "accepted_count": len(accepted_ids),
            "excluded_count": len(event_quality_rows) - len(accepted_ids),
            "exclusion_reason_counts": dict(exclusion_counts),
            "warning_reason_counts": dict(warning_counts),
            "audit_file": str(out / "event_quality.csv"),
        },
    }
    routing_path = ROOT / "validation" / "calibration_routing_2017" / "best_routing_parameters.json"
    routing_payload = json.loads(routing_path.read_text(encoding="utf-8")); units, mapping = build_mapping(mapping_path, topology_path, grids)
    routing_mapping_error = float(np.max(np.abs(mapping.sum(axis=0) - 1.0)))
    maximum_mapping_error = float(state_config.get("maximum_routing_mapping_relative_error", 0.05))
    if routing_mapping_error > maximum_mapping_error:
        raise ValueError(
            "grid-to-routing mapping error exceeds state configuration limit: "
            f"{routing_mapping_error:.6f} > {maximum_mapping_error:.6f}"
        )
    state_quality_flags = []
    if routing_mapping_error > float(state_config.get("routing_mapping_warning_tolerance", 1.0e-6)):
        state_quality_flags.append("ROUTING_MAPPING_AREA_MISMATCH")
    fast_router = FastRoutingRunner(topology_path, units, controls_path)
    # [CAL-BASE-PARAMETERS-06J] The baseline is an explicit interface input.
    # Routing-only calibration therefore freezes the selected runoff parameter set.
    base_params_path = Path(args.base_parameter_set).resolve()
    base_parameter_bytes = base_params_path.read_bytes()
    base_payload = json.loads(base_parameter_bytes.decode("utf-8-sig"))
    base_parameter_sha256 = hashlib.sha256(base_parameter_bytes).hexdigest()
    objective_payload = json.loads(Path(args.objective_config).read_text(encoding="utf-8-sig"))
    objective_specs = objective_payload.get("terms", objective_payload.get("objective_specs", []))
    if not objective_specs:
        raise ValueError("objective config must contain a non-empty terms list")
    qualification_config = load_qualification_config(Path(args.qualification_config).resolve())
    base_params = load_parameter_set(base_params_path)
    base_routing = read_parameters_json(routing_path)
    parameter_space_path = Path(args.parameter_space).resolve()
    parameter_space_payload = json.loads(parameter_space_path.read_text(encoding="utf-8-sig"))
    specs = parameter_space_payload.get("parameters", [])
    if not specs:
        raise ValueError("parameter-space config must contain a non-empty parameters list")
    required_spec_keys = {"name", "minimum", "maximum"}
    if any(not required_spec_keys.issubset(spec) for spec in specs):
        raise ValueError("every parameter-space item requires name, minimum and maximum")
    bounds = {spec["name"]: (float(spec["minimum"]), float(spec["maximum"])) for spec in specs}
    names = list(bounds)
    routing_parameter_names = [name for name in names if name.startswith("routing.")]
    runoff_parameter_names = [name for name in names if name.startswith("runoff.")]
    if routing_parameter_names and runoff_parameter_names:
        calibration_scope = "RUNOFF_ROUTING_JOINT"
    elif routing_parameter_names:
        calibration_scope = "ROUTING_ONLY_RUNOFF_FROZEN"
    else:
        calibration_scope = "RUNOFF_ONLY_ROUTING_FROZEN"
    configured_time_step = float(parameter_space_payload.get("time_step_hours", 1.0))
    if configured_time_step != 1.0:
        raise ValueError("formal multi-event calibration supports only a 1-hour parameter space")
    calibration_events = [e for e in events if e.group == "CALIBRATION"]
    if not calibration_events:
        raise RuntimeError("no calibration events available")
    calibration_event_count = len(calibration_events)
    if args.calibration_event_limit and args.calibration_event_limit < len(calibration_events):
        sample_rng = np.random.default_rng(args.split_seed + 1009)
        selected_indices = np.sort(sample_rng.choice(len(calibration_events), size=args.calibration_event_limit, replace=False))
        calibration_events = [calibration_events[int(index)] for index in selected_indices]
    calibration_event_ids = {event.event_id for event in calibration_events}
    event_map = {e.event_id: e for e in events}
    cache: dict[tuple[float, ...], tuple[float, pd.DataFrame]] = {}

    # [ROUTING-RUNOFF-CACHE-13C] A routing-only search has one immutable XAJ
    # parameter set. Prepare every event once, preserving the selected land-state
    # strategy, then reuse these unit runoff arrays for all routing candidates.
    prepared_runoff: dict[str, PreparedRunoff] = {}
    if calibration_scope == "ROUTING_ONLY_RUNOFF_FROZEN":
        fixed_runoff_params = candidate_params(load_parameter_set(base_params_path), {})
        carried_land_state = build_uniform_land_state(
            initial_land_template,
            len(grids),
            fixed_runoff_params,
        )
        for number, event in enumerate(events, start=1):
            initial_land_state = (
                carried_land_state
                if state_strategy == "CONTINUOUS"
                else build_uniform_land_state(
                    initial_land_template,
                    len(grids),
                    fixed_runoff_params,
                )
            )
            prepared = prepare_runoff_event(
                event,
                fixed_runoff_params,
                mapping,
                initial_land_state,
                event.pet_mm_h,
            )
            prepared_runoff[event.event_id] = prepared
            if state_strategy == "CONTINUOUS":
                carried_land_state = prepared.final_land_state
            if number % 10 == 0 or number == len(events):
                print(f"runoff cache {number}/{len(events)}", flush=True)

    # [STATE-INITIALIZATION-03I] One strategy adapter shared by search and final evaluation.
    def simulate_with_state_strategy(event, params, routing, carried_land, carried_routing):
        if state_strategy == "CONTINUOUS":
            initial_land = carried_land
            initial_routing = carried_routing
            routing_starts_at_event = False
        else:
            initial_land = build_uniform_land_state(initial_land_template, len(grids), params)
            if state_strategy == "OBSERVED_FLOW_ANCHORED":
                if event.start not in event.observed.index:
                    raise ValueError(
                        f"observed flow is missing at event start: {event.event_id}"
                    )
                start_flow = float(event.observed.loc[event.start])
                initial_routing = fast_router.observed_flow_anchor(start_flow)
                routing_starts_at_event = True
            else:
                initial_routing = None
                routing_starts_at_event = False
        if prepared_runoff:
            return route_prepared_runoff(
                prepared_runoff[event.event_id],
                routing,
                fast_router,
                initial_routing,
                routing_starts_at_event=routing_starts_at_event,
            )
        return route_event_fast(
            event,
            params,
            routing,
            fast_router,
            mapping,
            initial_land,
            initial_routing,
            event.pet_mm_h,
            routing_starts_at_event=routing_starts_at_event,
        )

    def evaluate(candidate: dict, include_rows: bool = False):
        key = tuple(round(float(candidate[n]), 7) for n in names)
        if key in cache and not include_rows:
            return cache[key]
        params = candidate_params(load_parameter_set(base_params_path), candidate)
        routing = routing_from_candidate(routing_payload, candidate)
        rows = []
        land_state = build_uniform_land_state(initial_land_template, len(grids), params)
        routing_state = None
        evaluation_sequence = events if state_strategy == "CONTINUOUS" else calibration_events
        for number, event in enumerate(evaluation_sequence, start=1):
            sim, next_land_state, next_routing_state = simulate_with_state_strategy(
                event, params, routing, land_state, routing_state
            )
            if state_strategy == "CONTINUOUS":
                land_state, routing_state = next_land_state, next_routing_state
            if event.event_id in calibration_event_ids:
                m = metrics(event.observed, sim.loc[event.start:event.end], qualification_config); rows.append({"event_id": event.event_id, "year": event.year, "group": event.group, **m})
        frame = pd.DataFrame(rows)
        loss = objective_loss(frame, objective_specs)
        result = (float(loss), frame)
        if not include_rows: cache[key] = result
        return result

    def algorithm_objective(candidate):
        loss, frame = evaluate(candidate)
        print(f"evaluation={len(cache)} loss={loss:.5f} cal_nse={frame.NSE.mean():.4f}", flush=True)
        return {"loss": loss, "calibration_summary": summary_for(frame)}

    if args.algorithm == "DIFFERENTIAL_EVOLUTION" and args.max_evaluations < 4:
        raise ValueError("DIFFERENTIAL_EVOLUTION requires max_evaluations >= 4")
    algorithm_result = search_algorithm(
        specs,
        algorithm_objective,
        {"algorithm": args.algorithm, "max_evaluations": args.max_evaluations, "seed": args.seed, "population_size": min(max(4, args.population_size), args.max_evaluations), "generations": args.generations, "differential_weight": args.differential_weight, "crossover_probability": args.crossover_probability},
    )
    best_candidate = algorithm_result["best_parameters"]
    best_loss = algorithm_result["best_loss"]
    history = algorithm_result["history"]
    if not best_candidate:
        errors = [record.get("error") for record in history if record.get("error")]
        detail = errors[0] if errors else "no candidate produced a finite objective"
        raise RuntimeError(f"all candidate evaluations failed; first error: {detail}")

    frozen_params = candidate_params(load_parameter_set(base_params_path), best_candidate); frozen_routing = routing_from_candidate(routing_payload, best_candidate)
    resolved_frozen_params = resolved_runoff_parameters(frozen_params)
    all_rows = []
    land_state = build_uniform_land_state(initial_land_template, len(grids), frozen_params)
    routing_state = None
    for i, event in enumerate(events, start=1):
        sim, next_land_state, next_routing_state = simulate_with_state_strategy(
            event, frozen_params, frozen_routing, land_state, routing_state
        )
        if state_strategy == "CONTINUOUS":
            land_state, routing_state = next_land_state, next_routing_state
        all_rows.append({"event_id": event.event_id, "year": event.year, "start": event.start.isoformat(), "end": event.end.isoformat(), "group": event.group, **metrics(event.observed, sim.loc[event.start:event.end], qualification_config)})
        if i % 10 == 0 or i == len(events): print(f"final evaluation {i}/{len(events)}", flush=True)
    metrics_frame = pd.DataFrame(all_rows); metrics_frame.to_csv(out / "event_metrics.csv", index=False, encoding="utf-8-sig")
    groups = {group: summary_for(frame) for group, frame in metrics_frame.groupby("group")}
    group_frame = pd.DataFrame([{"group": group, **values} for group, values in groups.items()])
    group_frame.to_csv(out / "group_metrics.csv", index=False, encoding="utf-8-sig")
    data_review_path = Path(args.data_review_config).resolve()
    data_review = json.loads(data_review_path.read_text(encoding="utf-8-sig"))
    production_clearance = bool(data_review.get("production_clearance", False))
    report = {
        "status": "FORMAL_MULTI_EVENT_CALIBRATION" if production_clearance else "DIAGNOSTIC_ONLY_DATA_REVIEW_REQUIRED",
        "split": split,
        "calibration_scope": calibration_scope,
        "base_parameter_set": {
            "path": str(base_params_path),
            "sha256": base_parameter_sha256,
            "frozen_for_routing_only": calibration_scope == "ROUTING_ONLY_RUNOFF_FROZEN",
        },
        "routing_runoff_cache": {
            "enabled": bool(prepared_runoff),
            "module_id": "ROUTING-RUNOFF-CACHE-13",
            "event_count": len(prepared_runoff),
            "cached_fields": ["RS", "RI", "RG"],
            "aggregation_level": "routing_unit",
            "runoff_computations_per_event": 1 if prepared_runoff else None,
            "note": "Only routing-only calibration reuses frozen XAJ runoff; runoff and joint calibration remain uncached.",
        },
        "data_review": {
            "config_path": str(data_review_path),
            "status": data_review.get("status", "UNKNOWN"),
            "production_clearance": production_clearance,
            "unresolved_items": data_review.get("unresolved_items", []),
        },
        "frozen_routing_parameter_path": str(routing_path) if not routing_parameter_names else None,
        "selection": selection,
        "calibration_search": {
            "event_count_available": calibration_event_count,
            "event_count_used": len(calibration_events),
            "sampling_seed": args.split_seed + 1009 if args.calibration_event_limit else None,
        },
        "state_initialization": {
            "module_id": "STATE-INITIALIZATION-03",
            "strategy": state_strategy,
            "config_path": str(state_config_path),
            "initial_land_state_path": str(initial_land_state_path),
            "fixed_warmup_hours": warmup_hours,
            "carry_land_state": state_strategy == "CONTINUOUS",
            "carry_routing_state": state_strategy == "CONTINUOUS",
            "observed_flow_anchor": state_strategy == "OBSERVED_FLOW_ANCHORED",
            "between_event_forcing": "observed hourly precipitation" if state_strategy == "CONTINUOUS" else "not simulated",
            "quality_flags": state_quality_flags,
            "routing_mapping_max_relative_error": routing_mapping_error,
        },
        "common_parameter_set": best_candidate,
        "resolved_runoff_parameter_set": resolved_frozen_params,
        "algorithm": args.algorithm,
        "algorithm_config": {
            "max_evaluations": args.max_evaluations,
            "population_size": args.population_size,
            "generations": args.generations,
            "differential_weight": args.differential_weight,
            "crossover_probability": args.crossover_probability,
            "seed": args.seed,
        },
        "objective": {
            "config_path": str(Path(args.objective_config).resolve()),
            "terms": objective_specs,
            "loss": best_loss,
        },
        "forcing_assumptions": {
            "temperature_mode": "NOT_USED_NON_GLACIER",
            "precipitation_dir": str(selected_precip_dir or find_precip_dir(data_root)),
            "pet": pet_provider.report(),
            "warmup_hours": warmup_hours,
            "warning": "non-glacier mode treats supplied precipitation as liquid; no temperature phase partition is used",
        },
        "qualification_rule": qualification_definition(qualification_config),
        "groups": groups,
        "all_events": summary_for(metrics_frame),
        "calibration_history": history,
        "parameter_space": {
            "config_path": str(parameter_space_path),
            "time_step_hours": configured_time_step,
            "bounds": bounds,
        },
    }
    history_rows = []
    for record in history:
        row = {"evaluation": record.get("evaluation"), "loss": record.get("loss"), "error": record.get("error")}
        row.update(record.get("parameters", {}))
        history_rows.append(row)
    pd.DataFrame(history_rows).to_csv(out / "algorithm_history.csv", index=False, encoding="utf-8-sig")
    source = (
        f"{calibration_scope} search using {len(calibration_events)} of "
        f"{calibration_event_count} calibration events; frozen-parameter evaluation "
        f"on all {len(events)} events"
    )
    (out / "common_parameter_set.json").write_text(json.dumps({"param_set_id": "NANANXI_MULTI_EVENT_COMMON_20260818", "calibration_scope": calibration_scope, "routing_parameter_names": routing_parameter_names, "search_parameters": best_candidate, "parameters": resolved_frozen_params, "source": source}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    resolved_plain = {
        name.removeprefix("runoff."): value
        for name, value in resolved_frozen_params.items()
    }
    resolved_plain["source_coefficient_time_step_hours"] = 1.0
    (out / "resolved_runoff_parameter_set.json").write_text(
        json.dumps(
            {
                "param_set_id": "NANANXI_MULTI_EVENT_RESOLVED_20260818",
                "model": "XAJ_THREE_SOURCE",
                "time_step_hours": 1,
                "status": "CALIBRATION_CANDIDATE_NOT_FINAL_PRODUCTION",
                "parameters": resolved_plain,
                "search_parameters": best_candidate,
                "source": source,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        order = ["CALIBRATION", "VALIDATION", "INDEPENDENT_TEST"]
        plot_frame = group_frame.set_index("group").reindex(order)
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
        plot_frame["qualified_rate_percent"].plot.bar(ax=axes[0], color=["#d95f02", "#7570b3", "#1b9e77"], rot=0)
        axes[0].set_ylabel("Qualified rate (%)"); axes[0].set_title("Multi-event qualification")
        plot_frame["mean_NSE"].plot.bar(ax=axes[1], color=["#d95f02", "#7570b3", "#1b9e77"], rot=0)
        axes[1].axhline(0.5, color="black", linestyle="--", linewidth=1); axes[1].set_ylabel("Mean NSE"); axes[1].set_title("Mean NSE by period")
        fig.savefig(out / "formal_multi_event_summary.png", dpi=180); plt.close(fig)
    except Exception as exc:
        report["plot_warning"] = repr(exc)
    (out / "formal_multi_event_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": report["status"], "groups": groups, "common_parameter_set": best_candidate}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
