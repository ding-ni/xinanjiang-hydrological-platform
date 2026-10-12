"""Static terrain topology construction for routing.

The functions in this module work on in-memory grids.  File adapters for
GeoTIFF, Arc/Info Grid, PostGIS, or NetCDF should call this layer after they
have loaded arrays and metadata.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from math import sqrt
from typing import Any

import numpy as np


D8_DIRECTIONS: tuple[tuple[int, int, int, float], ...] = (
    (0, 1, 1, 1.0),
    (1, 1, 2, sqrt(2.0)),
    (1, 0, 4, 1.0),
    (1, -1, 8, sqrt(2.0)),
    (0, -1, 16, 1.0),
    (-1, -1, 32, sqrt(2.0)),
    (-1, 0, 64, 1.0),
    (-1, 1, 128, sqrt(2.0)),
)
CODE_TO_OFFSET = {code: (dr, dc) for dr, dc, code, _ in D8_DIRECTIONS}


def valid_mask(dem: np.ndarray, nodata: float | None = None) -> np.ndarray:
    arr = np.asarray(dem, dtype=float)
    mask = np.isfinite(arr)
    if nodata is not None:
        mask &= arr != nodata
    return mask


def _iter_neighbors(row: int, col: int, shape: tuple[int, int]):
    rows, cols = shape
    for dr, dc, _, _ in D8_DIRECTIONS:
        nr = row + dr
        nc = col + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            yield nr, nc


def _is_boundary(row: int, col: int, mask: np.ndarray) -> bool:
    rows, cols = mask.shape
    if row in (0, rows - 1) or col in (0, cols - 1):
        return True
    return any(not mask[nr, nc] for nr, nc in _iter_neighbors(row, col, mask.shape))


def fill_depressions_priority_flood(
    dem: np.ndarray,
    nodata: float | None = None,
    epsilon: float = 1.0e-6,
) -> np.ndarray:
    """Fill closed depressions with a priority-flood algorithm."""
    arr = np.asarray(dem, dtype=float)
    mask = valid_mask(arr, nodata)
    filled = arr.copy()
    filled[~mask] = np.nan

    visited = ~mask.copy()
    heap: list[tuple[float, int, int]] = []

    rows, cols = arr.shape
    for row in range(rows):
        for col in range(cols):
            if mask[row, col] and _is_boundary(row, col, mask):
                visited[row, col] = True
                heapq.heappush(heap, (filled[row, col], row, col))

    while heap:
        elevation, row, col = heapq.heappop(heap)
        for nr, nc in _iter_neighbors(row, col, arr.shape):
            if visited[nr, nc] or not mask[nr, nc]:
                continue
            visited[nr, nc] = True
            new_elevation = filled[nr, nc]
            if new_elevation <= elevation:
                new_elevation = elevation + max(epsilon, 0.0)
            filled[nr, nc] = new_elevation
            heapq.heappush(heap, (new_elevation, nr, nc))

    return filled


def compute_slope_aspect(
    dem: np.ndarray,
    cell_size_m: float,
    nodata: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute slope and downslope aspect in degrees."""
    if cell_size_m <= 0:
        raise ValueError("cell_size_m must be positive")
    arr = np.asarray(dem, dtype=float)
    mask = valid_mask(arr, nodata)
    dz_drow, dz_dcol = np.gradient(arr, cell_size_m, cell_size_m)
    slope = np.degrees(np.arctan(np.hypot(dz_dcol, dz_drow)))
    aspect = (np.degrees(np.arctan2(-dz_dcol, dz_drow)) + 360.0) % 360.0
    aspect[slope == 0.0] = np.nan
    slope[~mask] = np.nan
    aspect[~mask] = np.nan
    return slope, aspect


def compute_d8_flow_dir(
    dem: np.ndarray,
    cell_size_m: float,
    nodata: float | None = None,
) -> np.ndarray:
    """Compute ESRI-style D8 flow direction codes."""
    if cell_size_m <= 0:
        raise ValueError("cell_size_m must be positive")
    arr = np.asarray(dem, dtype=float)
    mask = valid_mask(arr, nodata)
    flow_dir = np.zeros(arr.shape, dtype=np.int16)

    rows, cols = arr.shape
    for row in range(rows):
        for col in range(cols):
            if not mask[row, col]:
                continue
            best_slope = 0.0
            best_code = 0
            center = arr[row, col]
            for dr, dc, code, distance_factor in D8_DIRECTIONS:
                nr = row + dr
                nc = col + dc
                if not (0 <= nr < rows and 0 <= nc < cols) or not mask[nr, nc]:
                    continue
                drop = center - arr[nr, nc]
                if drop <= 0.0:
                    continue
                local_slope = drop / (cell_size_m * distance_factor)
                if local_slope > best_slope:
                    best_slope = local_slope
                    best_code = code
            flow_dir[row, col] = best_code

    return flow_dir


def downstream_cell(
    flow_dir: np.ndarray,
    row: int,
    col: int,
    mask: np.ndarray | None = None,
) -> tuple[int, int] | None:
    code = int(flow_dir[row, col])
    if code == 0:
        return None
    if code not in CODE_TO_OFFSET:
        raise ValueError(f"invalid D8 code {code} at ({row}, {col})")
    dr, dc = CODE_TO_OFFSET[code]
    nr = row + dr
    nc = col + dc
    rows, cols = flow_dir.shape
    if not (0 <= nr < rows and 0 <= nc < cols):
        return None
    if mask is not None and not mask[nr, nc]:
        return None
    return nr, nc


def compute_flow_accumulation(
    flow_dir: np.ndarray,
    mask: np.ndarray | None = None,
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """Compute upstream accumulation by topological propagation."""
    flow = np.asarray(flow_dir)
    if mask is None:
        mask = np.ones(flow.shape, dtype=bool)
    else:
        mask = np.asarray(mask, dtype=bool)
    if weights is None:
        acc = np.where(mask, 1.0, 0.0)
    else:
        acc = np.where(mask, np.asarray(weights, dtype=float), 0.0)

    indegree = np.zeros(flow.shape, dtype=np.int32)
    downstream: dict[tuple[int, int], tuple[int, int]] = {}

    rows, cols = flow.shape
    for row in range(rows):
        for col in range(cols):
            if not mask[row, col]:
                continue
            ds = downstream_cell(flow, row, col, mask)
            if ds is None:
                continue
            downstream[(row, col)] = ds
            indegree[ds] += 1

    queue = [(row, col) for row in range(rows) for col in range(cols) if mask[row, col] and indegree[row, col] == 0]
    processed = 0
    head = 0
    while head < len(queue):
        row, col = queue[head]
        head += 1
        processed += 1
        ds = downstream.get((row, col))
        if ds is None:
            continue
        dr, dc = ds
        acc[dr, dc] += acc[row, col]
        indegree[dr, dc] -= 1
        if indegree[dr, dc] == 0:
            queue.append(ds)

    if processed != int(mask.sum()):
        raise ValueError("flow direction grid contains a cycle")

    acc[~mask] = np.nan
    return acc


def extract_stream_raster(
    accumulation_area_km2: np.ndarray,
    threshold_area_km2: float,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    if threshold_area_km2 <= 0:
        raise ValueError("threshold_area_km2 must be positive")
    if mask is None:
        mask = np.isfinite(accumulation_area_km2)
    return (accumulation_area_km2 >= threshold_area_km2) & mask


@dataclass(frozen=True)
class ReachSegment:
    reach_id: str
    start_cell: tuple[int, int]
    end_cell: tuple[int, int]
    cells: tuple[tuple[int, int], ...]
    downstream_reach_id: str | None
    length_km: float

    def to_mapping(self) -> dict[str, Any]:
        return {
            "reach_id": self.reach_id,
            "downstream_reach_id": self.downstream_reach_id,
            "length_km": self.length_km,
            "start_cell": list(self.start_cell),
            "end_cell": list(self.end_cell),
            "cell_count": len(self.cells),
        }


def delineate_reaches(
    stream_raster: np.ndarray,
    flow_dir: np.ndarray,
    cell_size_m: float,
) -> list[ReachSegment]:
    """Split stream raster into reaches at heads, confluences, and outlets."""
    stream = np.asarray(stream_raster, dtype=bool)
    mask = stream
    stream_downstream: dict[tuple[int, int], tuple[int, int] | None] = {}
    upstream_count = np.zeros(stream.shape, dtype=np.int16)

    rows, cols = stream.shape
    for row in range(rows):
        for col in range(cols):
            if not stream[row, col]:
                continue
            ds = downstream_cell(flow_dir, row, col, mask)
            if ds is not None and stream[ds]:
                stream_downstream[(row, col)] = ds
                upstream_count[ds] += 1
            else:
                stream_downstream[(row, col)] = None

    starts = sorted(
        (row, col)
        for row in range(rows)
        for col in range(cols)
        if stream[row, col] and upstream_count[row, col] != 1
    )

    pending: list[dict[str, Any]] = []
    for start in starts:
        ds = stream_downstream[start]
        if ds is None and upstream_count[start] != 0:
            continue

        cells = [start]
        current = start
        while True:
            ds = stream_downstream[current]
            if ds is None:
                break
            cells.append(ds)
            current = ds
            if upstream_count[current] != 1:
                break

        pending.append(
            {
                "start_cell": start,
                "end_cell": current,
                "cells": tuple(cells),
            }
        )

    start_to_reach: dict[tuple[int, int], str] = {}
    for index, item in enumerate(pending, start=1):
        start_to_reach[item["start_cell"]] = f"R{index:04d}"

    reaches: list[ReachSegment] = []
    for index, item in enumerate(pending, start=1):
        reach_id = f"R{index:04d}"
        end_cell = item["end_cell"]
        downstream_reach_id = None
        if stream_downstream[end_cell] is not None:
            downstream_reach_id = start_to_reach.get(end_cell)
        length_km = max(len(item["cells"]) - 1, 1) * cell_size_m / 1000.0
        reaches.append(
            ReachSegment(
                reach_id=reach_id,
                start_cell=item["start_cell"],
                end_cell=end_cell,
                cells=item["cells"],
                downstream_reach_id=downstream_reach_id,
                length_km=length_km,
            )
        )

    return reaches


def _stream_cell_to_reach(reaches: list[ReachSegment]) -> dict[tuple[int, int], str]:
    mapping: dict[tuple[int, int], str] = {}
    for reach in reaches:
        for cell in reach.cells:
            mapping[cell] = reach.reach_id
    for reach in reaches:
        mapping[reach.start_cell] = reach.reach_id
    return mapping


def assign_cells_to_reaches(
    flow_dir: np.ndarray,
    stream_raster: np.ndarray,
    reaches: list[ReachSegment],
    mask: np.ndarray,
) -> dict[tuple[int, int], str | None]:
    stream_to_reach = _stream_cell_to_reach(reaches)
    memo: dict[tuple[int, int], str | None] = {}
    rows, cols = flow_dir.shape

    def resolve(cell: tuple[int, int]) -> str | None:
        if cell in memo:
            return memo[cell]
        path: list[tuple[int, int]] = []
        current = cell
        while True:
            if current in memo:
                reach_id = memo[current]
                break
            if stream_raster[current] and current in stream_to_reach:
                reach_id = stream_to_reach[current]
                break
            path.append(current)
            ds = downstream_cell(flow_dir, current[0], current[1], mask)
            if ds is None:
                reach_id = None
                break
            current = ds
        for item in path:
            memo[item] = reach_id
        return reach_id

    assignments: dict[tuple[int, int], str | None] = {}
    for row in range(rows):
        for col in range(cols):
            if mask[row, col]:
                assignments[(row, col)] = resolve((row, col))
    return assignments


@dataclass(frozen=True)
class GridTopologyResult:
    spatial_version: str
    basin_id: str
    cell_size_m: float
    filled_dem: np.ndarray
    slope_deg: np.ndarray
    aspect_deg: np.ndarray
    flow_dir: np.ndarray
    accumulation_cells: np.ndarray
    accumulation_area_km2: np.ndarray
    stream_raster: np.ndarray
    reaches: list[ReachSegment]
    units: list[dict[str, Any]]
    control_nodes: list[dict[str, Any]] = field(default_factory=list)

    def to_mapping(self, include_grids: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "spatial_version": self.spatial_version,
            "basin_id": self.basin_id,
            "cell_size_m": self.cell_size_m,
            "reaches": [reach.to_mapping() for reach in self.reaches],
            "units": self.units,
            "control_nodes": self.control_nodes,
        }
        if include_grids:
            data.update(
                {
                    "filled_dem": _array_to_json(self.filled_dem),
                    "slope_deg": _array_to_json(self.slope_deg),
                    "aspect_deg": _array_to_json(self.aspect_deg),
                    "flow_dir": self.flow_dir.astype(int).tolist(),
                    "accumulation_cells": _array_to_json(self.accumulation_cells),
                    "accumulation_area_km2": _array_to_json(self.accumulation_area_km2),
                    "stream_raster": self.stream_raster.astype(int).tolist(),
                }
            )
        return data


def _array_to_json(array: np.ndarray) -> list[list[float | None]]:
    output: list[list[float | None]] = []
    for row in np.asarray(array, dtype=float):
        output.append([None if not np.isfinite(value) else float(value) for value in row])
    return output


def build_unit_info(
    basin_id: str,
    spatial_version: str,
    dem: np.ndarray,
    slope_deg: np.ndarray,
    aspect_deg: np.ndarray,
    flow_dir: np.ndarray,
    stream_raster: np.ndarray,
    reaches: list[ReachSegment],
    mask: np.ndarray,
    cell_size_m: float,
    zone_type: str = "non_glacier",
) -> list[dict[str, Any]]:
    assignments = assign_cells_to_reaches(flow_dir, stream_raster, reaches, mask)
    cells_by_reach: dict[str, list[tuple[int, int]]] = {reach.reach_id: [] for reach in reaches}
    for cell, reach_id in assignments.items():
        if reach_id is not None:
            cells_by_reach[reach_id].append(cell)

    reach_by_id = {reach.reach_id: reach for reach in reaches}
    cell_area_km2 = cell_size_m * cell_size_m / 1_000_000.0
    units: list[dict[str, Any]] = []
    for reach in reaches:
        cells = cells_by_reach[reach.reach_id]
        if not cells:
            continue
        rows = np.array([cell[0] for cell in cells])
        cols = np.array([cell[1] for cell in cells])
        downstream_unit_id = None
        if reach.downstream_reach_id:
            downstream_unit_id = f"U_{reach.downstream_reach_id}_{zone_type.upper()}"
        units.append(
            {
                "unit_id": f"U_{reach.reach_id}_{zone_type.upper()}",
                "basin_id": basin_id,
                "subbasin_id": reach.reach_id,
                "reach_id": reach.reach_id,
                "zone_type": zone_type,
                "area_km2": float(len(cells) * cell_area_km2),
                "elevation_mean": _nanmean_at(dem, rows, cols),
                "slope_mean": _nanmean_at(slope_deg, rows, cols),
                "aspect_mean": _nanmean_at(aspect_deg, rows, cols),
                "glacier_fraction": 0.0,
                "moraine_fraction": 0.0,
                "outlet_cell_id": list(reach_by_id[reach.reach_id].end_cell),
                "downstream_unit_id": downstream_unit_id,
                "spatial_version": spatial_version,
                "cell_count": len(cells),
            }
        )
    return units


def _nanmean_at(array: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> float | None:
    values = np.asarray(array, dtype=float)[rows, cols]
    if np.all(~np.isfinite(values)):
        return None
    return float(np.nanmean(values))


def build_grid_topology(
    dem: np.ndarray,
    cell_size_m: float,
    stream_threshold_km2: float,
    basin_id: str = "BASIN",
    spatial_version: str = "spatial_v1",
    nodata: float | None = None,
    fill_epsilon: float = 1.0e-6,
    zone_type: str = "non_glacier",
) -> GridTopologyResult:
    if cell_size_m <= 0:
        raise ValueError("cell_size_m must be positive")
    arr = np.asarray(dem, dtype=float)
    if arr.ndim != 2:
        raise ValueError("dem must be a 2D grid")

    mask = valid_mask(arr, nodata)
    filled_dem = fill_depressions_priority_flood(arr, nodata=nodata, epsilon=fill_epsilon)
    slope_deg, aspect_deg = compute_slope_aspect(filled_dem, cell_size_m, nodata=None)
    flow_dir = compute_d8_flow_dir(filled_dem, cell_size_m, nodata=None)
    flow_dir[~mask] = 0
    accumulation_cells = compute_flow_accumulation(flow_dir, mask=mask)
    accumulation_area_km2 = accumulation_cells * (cell_size_m * cell_size_m / 1_000_000.0)
    stream_raster = extract_stream_raster(
        accumulation_area_km2,
        threshold_area_km2=stream_threshold_km2,
        mask=mask,
    )
    reaches = delineate_reaches(stream_raster, flow_dir, cell_size_m)
    units = build_unit_info(
        basin_id=basin_id,
        spatial_version=spatial_version,
        dem=filled_dem,
        slope_deg=slope_deg,
        aspect_deg=aspect_deg,
        flow_dir=flow_dir,
        stream_raster=stream_raster,
        reaches=reaches,
        mask=mask,
        cell_size_m=cell_size_m,
        zone_type=zone_type,
    )

    return GridTopologyResult(
        spatial_version=spatial_version,
        basin_id=basin_id,
        cell_size_m=cell_size_m,
        filled_dem=filled_dem,
        slope_deg=slope_deg,
        aspect_deg=aspect_deg,
        flow_dir=flow_dir,
        accumulation_cells=accumulation_cells,
        accumulation_area_km2=accumulation_area_km2,
        stream_raster=stream_raster,
        reaches=reaches,
        units=units,
    )
