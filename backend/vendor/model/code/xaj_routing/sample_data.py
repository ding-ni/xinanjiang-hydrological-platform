"""Sample input generators for local routing integration tests."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .io import read_json, write_json
from .models import RUNOFF_FIELDS


def generate_synthetic_runoff_csv(
    topology_path: str | Path,
    out_path: str | Path,
    start_time: str,
    periods: int = 12,
    time_step_hours: float = 1.0,
    peak_rs_mm: float = 5.0,
) -> dict[str, Any]:
    """Generate a routing-ready runoff CSV aligned with topology units."""

    if periods <= 0:
        raise ValueError("periods must be positive")
    if time_step_hours <= 0:
        raise ValueError("time_step_hours must be positive")
    if peak_rs_mm < 0:
        raise ValueError("peak_rs_mm must be non-negative")

    topology = read_json(topology_path)
    units = sorted(topology.get("units", []), key=lambda item: item["unit_id"])
    if not units:
        raise ValueError("topology has no units")

    start = datetime.fromisoformat(start_time)
    pattern = _storm_pattern(periods)
    output_path = Path(out_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["unit_id", "time", *RUNOFF_FIELDS])
        writer.writeheader()
        for step, factor in enumerate(pattern):
            current_time = start + timedelta(hours=step * time_step_hours)
            for unit in units:
                zone_type = str(unit.get("zone_type", "non_glacier")).lower()
                row = _runoff_row(
                    unit_id=str(unit["unit_id"]),
                    time=current_time,
                    zone_type=zone_type,
                    factor=factor,
                    peak_rs_mm=peak_rs_mm,
                )
                writer.writerow(row)

    return {
        "topology": str(topology_path).replace("\\", "/"),
        "out": str(output_path).replace("\\", "/"),
        "unit_count": len(units),
        "periods": periods,
        "row_count": len(units) * periods,
        "start_time": start.isoformat(sep=" "),
        "time_step_hours": time_step_hours,
        "peak_rs_mm": peak_rs_mm,
        "fields": ["unit_id", "time", *RUNOFF_FIELDS],
    }


def generate_routing_parameters_json(
    topology_path: str | Path,
    out_path: str | Path,
    time_step_hours: float = 1.0,
    default_channel_velocity_kmh: float = 3.0,
    muskingum_x: float = 0.2,
) -> dict[str, Any]:
    """Generate first-pass routing parameters from reach lengths."""

    if time_step_hours <= 0:
        raise ValueError("time_step_hours must be positive")
    if default_channel_velocity_kmh <= 0:
        raise ValueError("default_channel_velocity_kmh must be positive")
    if not 0.0 <= muskingum_x <= 0.5:
        raise ValueError("muskingum_x must be between 0 and 0.5")

    topology = read_json(topology_path)
    reaches = sorted(topology.get("reaches", []), key=lambda item: item["reach_id"])
    if not reaches:
        raise ValueError("topology has no reaches")

    reach_muskingum: dict[str, dict[str, float]] = {}
    for reach in reaches:
        length_km = float(reach.get("length_km", 0.0) or 0.0)
        travel_time_hours = length_km / default_channel_velocity_kmh
        k_hours = max(time_step_hours, travel_time_hours)
        reach_muskingum[str(reach["reach_id"])] = {
            "k_hours": round(k_hours, 6),
            "x": muskingum_x,
        }

    payload = {
        "time_step_hours": time_step_hours,
        "default_slope_k_hours": {
            "RS": 2.0,
            "RI": 8.0,
            "RG": 24.0,
            "GLACIER_FAST": 2.0,
            "GLACIER_SLOW": 12.0,
            "GLACIER_BASE": 48.0,
        },
        "unit_component_slope_k_hours": {},
        "reach_muskingum": reach_muskingum,
        "metadata": {
            "source": "synthetic_first_pass_parameters",
            "topology": str(topology_path).replace("\\", "/"),
            "default_channel_velocity_kmh": default_channel_velocity_kmh,
            "muskingum_x": muskingum_x,
        },
    }
    write_json(out_path, payload)
    return {
        "topology": str(topology_path).replace("\\", "/"),
        "out": str(out_path).replace("\\", "/"),
        "reach_count": len(reaches),
        "time_step_hours": time_step_hours,
        "default_channel_velocity_kmh": default_channel_velocity_kmh,
        "muskingum_x": muskingum_x,
    }


def _storm_pattern(periods: int) -> list[float]:
    if periods == 1:
        return [1.0]
    peak_index = max(1, periods // 3)
    tail = max(1, periods - peak_index - 1)
    values: list[float] = []
    for step in range(periods):
        if step <= peak_index:
            factor = step / peak_index
        else:
            factor = max(0.0, 1.0 - (step - peak_index) / tail)
        values.append(round(factor, 6))
    return values


def _runoff_row(
    unit_id: str,
    time: datetime,
    zone_type: str,
    factor: float,
    peak_rs_mm: float,
) -> dict[str, Any]:
    if "glacier" in zone_type:
        return {
            "unit_id": unit_id,
            "time": time.isoformat(sep=" "),
            "RS": 0.0,
            "RI": 0.0,
            "RG": 0.0,
            "glacier_fast": round(peak_rs_mm * 0.75 * factor, 6),
            "glacier_slow": round(peak_rs_mm * 0.25 * factor, 6),
            "glacier_base": round(peak_rs_mm * 0.05, 6),
        }
    return {
        "unit_id": unit_id,
        "time": time.isoformat(sep=" "),
        "RS": round(peak_rs_mm * factor, 6),
        "RI": round(peak_rs_mm * 0.35 * factor, 6),
        "RG": round(peak_rs_mm * 0.12 * factor + 0.02, 6),
        "glacier_fast": 0.0,
        "glacier_slow": 0.0,
        "glacier_base": 0.0,
    }
