"""Derived spatial products for routing topology workflows."""

from __future__ import annotations

from math import inf
from typing import Any

import numpy as np

from .raster_formats import AsciiGrid, cell_center_xy
from .spatial import GridTopologyResult, ReachSegment


def outlet_candidates_from_result(
    result: GridTopologyResult,
    grid: AsciiGrid,
    grid_crs: str | None = None,
    lonlat_crs: str | None = "EPSG:4326",
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Create ranked outlet candidates from terminal reaches."""

    outlet_reaches = [
        reach for reach in result.reaches if reach.downstream_reach_id is None
    ]
    if not outlet_reaches:
        outlet_reaches = result.reaches

    candidates: list[dict[str, Any]] = []
    for reach in outlet_reaches:
        row, col = reach.end_cell
        x, y = cell_center_xy(grid, row, col)
        candidate = {
            "candidate_id": f"outlet_{reach.reach_id}",
            "basin_id": result.basin_id,
            "spatial_version": result.spatial_version,
            "reach_id": reach.reach_id,
            "cell": [int(row), int(col)],
            "x": x,
            "y": y,
            "grid_crs": grid_crs,
            "accumulation_area_km2": _float_or_none(result.accumulation_area_km2[row, col]),
            "elevation_m": _float_or_none(result.filled_dem[row, col]),
            "reach_length_km": float(reach.length_km),
            "source": "terminal_reach",
        }
        candidate.update(_optional_lonlat(x, y, grid_crs, lonlat_crs))
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            -(
                item["accumulation_area_km2"]
                if item["accumulation_area_km2"] is not None
                else -inf
            ),
            item["reach_id"],
        )
    )
    if max_count is not None:
        return candidates[:max_count]
    return candidates


def snap_point_to_stream(
    result: GridTopologyResult,
    grid: AsciiGrid,
    x: float,
    y: float,
    point_crs: str | None = None,
    grid_crs: str | None = None,
    max_distance_m: float | None = None,
    control_id: str = "control_point",
    name: str | None = None,
) -> dict[str, Any]:
    """Snap a point to the nearest stream cell and return its reach id."""

    grid_x, grid_y = transform_xy(x, y, point_crs, grid_crs)
    stream_cells = np.argwhere(result.stream_raster)
    if stream_cells.size == 0:
        raise ValueError("topology has no extracted stream cells")

    reach_by_cell = _stream_cell_to_reach(result.reaches)
    best: tuple[float, int, int] | None = None
    for row, col in stream_cells:
        cell_x, cell_y = cell_center_xy(grid, int(row), int(col))
        distance = float(np.hypot(cell_x - grid_x, cell_y - grid_y))
        if best is None or distance < best[0]:
            best = (distance, int(row), int(col))
    assert best is not None

    distance, row, col = best
    if max_distance_m is not None and distance > max_distance_m:
        raise ValueError(
            f"nearest stream cell is {distance:.2f} m away, "
            f"exceeding max_distance_m={max_distance_m}"
        )

    cell_x, cell_y = cell_center_xy(grid, row, col)
    reach_id = reach_by_cell.get((row, col))
    payload = {
        "control_id": control_id,
        "name": name or control_id,
        "basin_id": result.basin_id,
        "spatial_version": result.spatial_version,
        "input": {
            "x": float(x),
            "y": float(y),
            "crs": point_crs,
        },
        "snapped": {
            "reach_id": reach_id,
            "cell": [row, col],
            "x": cell_x,
            "y": cell_y,
            "grid_crs": grid_crs,
            "distance_m": distance,
            "accumulation_area_km2": _float_or_none(result.accumulation_area_km2[row, col]),
            "elevation_m": _float_or_none(result.filled_dem[row, col]),
        },
    }
    payload["snapped"].update(_optional_lonlat(cell_x, cell_y, grid_crs, "EPSG:4326"))
    return payload


def placeholder_control_from_candidate(
    candidate: dict[str, Any],
    control_id: str,
    name: str,
) -> dict[str, Any]:
    """Create a placeholder control-point record from the best outlet candidate."""

    return {
        "control_id": control_id,
        "name": name,
        "status": "placeholder_from_auto_outlet",
        "note": (
            "Replace this record with surveyed station longitude/latitude, then "
            "run snap-point-from-ascii to bind the station to the extracted stream."
        ),
        "basin_id": candidate.get("basin_id"),
        "spatial_version": candidate.get("spatial_version"),
        "snapped": {
            "reach_id": candidate.get("reach_id"),
            "cell": candidate.get("cell"),
            "x": candidate.get("x"),
            "y": candidate.get("y"),
            "grid_crs": candidate.get("grid_crs"),
            "lon": candidate.get("lon"),
            "lat": candidate.get("lat"),
            "lonlat_crs": candidate.get("lonlat_crs"),
            "distance_m": 0.0,
            "accumulation_area_km2": candidate.get("accumulation_area_km2"),
            "elevation_m": candidate.get("elevation_m"),
        },
    }


def transform_xy(
    x: float,
    y: float,
    src_crs: str | None,
    dst_crs: str | None,
) -> tuple[float, float]:
    """Transform one coordinate pair when CRS metadata requires it."""

    if not src_crs or not dst_crs or src_crs.upper() == dst_crs.upper():
        return float(x), float(y)
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError(
            "pyproj is required when point CRS differs from grid CRS. "
            "Run this command with a Python environment that has pyproj installed."
        ) from exc
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    out_x, out_y = transformer.transform(x, y)
    return float(out_x), float(out_y)


def _optional_lonlat(
    x: float,
    y: float,
    src_crs: str | None,
    lonlat_crs: str | None,
) -> dict[str, Any]:
    if not src_crs or not lonlat_crs:
        return {}
    try:
        lon, lat = transform_xy(x, y, src_crs, lonlat_crs)
    except RuntimeError:
        return {}
    return {
        "lon": lon,
        "lat": lat,
        "lonlat_crs": lonlat_crs,
    }


def _stream_cell_to_reach(reaches: list[ReachSegment]) -> dict[tuple[int, int], str]:
    mapping: dict[tuple[int, int], str] = {}
    ordered = sorted(reaches, key=lambda item: item.reach_id)
    for reach in ordered:
        if reach.downstream_reach_id is None:
            mapping.setdefault(reach.end_cell, reach.reach_id)
    for reach in ordered:
        for cell in reach.cells:
            mapping.setdefault(cell, reach.reach_id)
    return mapping


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number
