"""Temporary rainfall-to-runoff proxy for routing integration checks."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from .io import read_topology_json, write_json
from .models import parse_time


def generate_basin_average_runoff_proxy(
    *,
    rainfall_csv: str | Path,
    topology_json: str | Path,
    out_csv: str | Path,
    manifest_out: str | Path | None = None,
    runoff_coefficient: float = 1.0,
    rs_fraction: float = 0.6,
    ri_fraction: float = 0.3,
    rg_fraction: float = 0.1,
) -> dict[str, Any]:
    """Convert real rainfall forcing to a standard runoff CSV for smoke tests.

    This is not a production Xinanjiang runoff calculation. It preserves the
    routing module interface while the actual runoff module is still being built.
    """

    if runoff_coefficient < 0:
        raise ValueError("runoff_coefficient must be non-negative")
    fractions = {
        "RS": rs_fraction,
        "RI": ri_fraction,
        "RG": rg_fraction,
    }
    total_fraction = sum(fractions.values())
    if total_fraction <= 0:
        raise ValueError("RS/RI/RG fractions must sum to a positive value")
    normalized = {key: value / total_fraction for key, value in fractions.items()}

    topology = read_topology_json(topology_json)
    units = list(topology.units.values())
    if not units:
        raise ValueError("topology contains no units")

    rainfall_by_time = _read_basin_average_rainfall(rainfall_csv)
    output_path = Path(out_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    row_count = 0
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "unit_id",
                "time",
                "RS",
                "RI",
                "RG",
                "glacier_fast",
                "glacier_slow",
                "glacier_base",
            ],
        )
        writer.writeheader()
        for time_value in sorted(rainfall_by_time):
            effective_mm = rainfall_by_time[time_value] * runoff_coefficient
            for unit in units:
                writer.writerow(
                    {
                        "unit_id": unit.unit_id,
                        "time": time_value.isoformat(sep=" "),
                        "RS": f"{effective_mm * normalized['RS']:.6f}",
                        "RI": f"{effective_mm * normalized['RI']:.6f}",
                        "RG": f"{effective_mm * normalized['RG']:.6f}",
                        "glacier_fast": "0.000000",
                        "glacier_slow": "0.000000",
                        "glacier_base": "0.000000",
                    }
                )
                row_count += 1

    times = sorted(rainfall_by_time)
    event_total_mm = sum(rainfall_by_time.values())
    manifest = {
        "method": "basin_average_rainfall_proxy",
        "warning": "This is a routing smoke-test input, not production Xinanjiang runoff.",
        "rainfall_csv": str(rainfall_csv),
        "topology_json": str(topology_json),
        "out_csv": str(out_csv),
        "unit_count": len(units),
        "time_count": len(times),
        "row_count": row_count,
        "runoff_coefficient": runoff_coefficient,
        "rs_fraction": normalized["RS"],
        "ri_fraction": normalized["RI"],
        "rg_fraction": normalized["RG"],
        "basin_average_event_precip_mm": event_total_mm,
        "basin_average_effective_runoff_mm": event_total_mm * runoff_coefficient,
        "max_hourly_basin_average_precip_mm": max(rainfall_by_time.values()) if times else 0.0,
        "start_time": times[0].isoformat(sep=" ") if times else None,
        "end_time": times[-1].isoformat(sep=" ") if times else None,
    }
    if manifest_out:
        write_json(manifest_out, manifest)
    return manifest


def _read_basin_average_rainfall(path: str | Path) -> dict[Any, float]:
    sums: dict[Any, float] = defaultdict(float)
    counts: dict[Any, int] = defaultdict(int)
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "time" not in (reader.fieldnames or []) or "precip_mm" not in (reader.fieldnames or []):
            raise ValueError("rainfall forcing CSV must contain time and precip_mm columns")
        for row_number, row in enumerate(reader, start=2):
            precip_text = str(row.get("precip_mm", "")).strip()
            if precip_text == "":
                continue
            try:
                time_value = parse_time(str(row["time"]))
                precip_mm = float(precip_text)
            except ValueError as exc:
                raise ValueError(f"rainfall row {row_number}: {exc}") from exc
            sums[time_value] += precip_mm
            counts[time_value] += 1

    return {
        time_value: sums[time_value] / counts[time_value]
        for time_value in sums
        if counts[time_value] > 0
    }
