"""INTEGRATION-MAP: build conservative 1 km runoff-grid to routing-unit weights."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, box, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from xaj_routing.raster_formats import cell_center_xy, read_esri_ascii_grid
from xaj_routing.control_split import split_topology_at_control_points
from xaj_routing.spatial import assign_cells_to_reaches, build_grid_topology, valid_mask


MAPPING_FIELDS = (
    "grid_id",
    "unit_id",
    "reach_id",
    "raw_overlap_area_km2",
    "adjusted_overlap_area_km2",
    "source_fraction",
    "mapping_method",
)


def _read_source_grids(geojson_path: Path, grid_static_path: Path):
    with grid_static_path.open("r", encoding="utf-8-sig", newline="") as handle:
        static_rows = list(csv.DictReader(handle))
    id_by_row_col = {
        (int(row["source_row"]), int(row["source_col"])): row["unit_id"]
        for row in static_rows
    }
    area_by_id = {row["unit_id"]: float(row["area_km2"]) for row in static_rows}
    payload = json.loads(geojson_path.read_text(encoding="utf-8"))
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    geometries = []
    grid_ids = []
    for feature in payload["features"]:
        properties = feature["properties"]
        grid_id = id_by_row_col[(int(properties["row"]), int(properties["col"]))]
        geometries.append(transform(transformer.transform, shape(feature["geometry"])))
        grid_ids.append(grid_id)
    return geometries, grid_ids, area_by_id


def build_grid_to_unit_weights(
    dem_ascii_path: str | Path,
    topology_path: str | Path,
    runoff_grid_geojson_path: str | Path,
    grid_static_path: str | Path,
    output_path: str | Path,
    stream_threshold_km2: float = 1.0,
    controls: list[dict] | None = None,
) -> dict[str, float | int]:
    """Build normalized overlap weights while preserving every source-grid volume."""

    dem_ascii_path = Path(dem_ascii_path)
    topology_path = Path(topology_path)
    output_path = Path(output_path)
    topology_payload = json.loads(topology_path.read_text(encoding="utf-8"))
    unit_by_reach = {row["reach_id"]: row["unit_id"] for row in topology_payload["units"]}

    grid = read_esri_ascii_grid(dem_ascii_path)
    base_rebuilt = build_grid_topology(
        grid.elevation,
        grid.cellsize,
        stream_threshold_km2,
        basin_id=topology_payload.get("basin_id", "nan_an"),
        spatial_version=topology_payload.get("spatial_version", "spatial_v1"),
        nodata=grid.nodata,
    )
    mask = valid_mask(grid.elevation, grid.nodata)
    rebuilt = (
        split_topology_at_control_points(base_rebuilt, mask, controls)
        if controls
        else base_rebuilt
    )
    rebuilt_reaches = [reach.to_mapping() for reach in rebuilt.reaches]
    if rebuilt_reaches != topology_payload["reaches"]:
        raise ValueError("DEM reconstruction does not match the supplied routing topology")

    assignments = assign_cells_to_reaches(
        rebuilt.flow_dir,
        rebuilt.stream_raster,
        rebuilt.reaches,
        mask,
    )
    source_geometries, source_ids, source_area_by_id = _read_source_grids(
        Path(runoff_grid_geojson_path), Path(grid_static_path)
    )
    source_geometry_by_id = dict(zip(source_ids, source_geometries))
    tree = STRtree(source_geometries)
    raw_overlap_m2: dict[tuple[str, str], float] = defaultdict(float)
    mapping_method_by_pair: dict[tuple[str, str], str] = {}
    assigned_centers = []
    assigned_unit_ids = []
    half = grid.cellsize / 2.0

    for (row, col), reach_id in assignments.items():
        if reach_id is None:
            continue
        center_x, center_y = cell_center_xy(grid, row, col)
        assigned_centers.append(Point(center_x, center_y))
        assigned_unit_ids.append(unit_by_reach[reach_id])
        cell_polygon = box(center_x - half, center_y - half, center_x + half, center_y + half)
        for source_index in tree.query(cell_polygon):
            source_geometry = source_geometries[int(source_index)]
            overlap_m2 = cell_polygon.intersection(source_geometry).area
            if overlap_m2 > 0.0:
                pair = (source_ids[int(source_index)], unit_by_reach[reach_id])
                raw_overlap_m2[pair] += overlap_m2
                mapping_method_by_pair[pair] = "AREA_OVERLAP"

    rows = []
    fractions_by_grid: dict[str, float] = defaultdict(float)
    raw_by_grid: dict[str, float] = defaultdict(float)
    for (grid_id, unit_id), overlap_m2 in raw_overlap_m2.items():
        raw_by_grid[grid_id] += overlap_m2

    missing_grids = sorted(set(source_area_by_id) - set(raw_by_grid))
    if missing_grids:
        assigned_tree = STRtree(assigned_centers)
        for grid_id in missing_grids:
            nearest_index = int(assigned_tree.nearest(source_geometry_by_id[grid_id]))
            pair = (grid_id, assigned_unit_ids[nearest_index])
            raw_overlap_m2[pair] = 0.0
            raw_by_grid[grid_id] = 0.0
            mapping_method_by_pair[pair] = "NEAREST_FALLBACK"

    for (grid_id, unit_id), overlap_m2 in sorted(raw_overlap_m2.items()):
        source_fraction = (
            overlap_m2 / raw_by_grid[grid_id] if raw_by_grid[grid_id] > 0.0 else 1.0
        )
        fractions_by_grid[grid_id] += source_fraction
        adjusted_area = source_area_by_id[grid_id] * source_fraction
        rows.append(
            {
                "grid_id": grid_id,
                "unit_id": unit_id,
                "reach_id": next(
                    reach_id for reach_id, mapped_unit_id in unit_by_reach.items()
                    if mapped_unit_id == unit_id
                ),
                "raw_overlap_area_km2": overlap_m2 / 1_000_000.0,
                "adjusted_overlap_area_km2": adjusted_area,
                "source_fraction": source_fraction,
                "mapping_method": mapping_method_by_pair[(grid_id, unit_id)],
            }
        )

    max_fraction_error = max(abs(value - 1.0) for value in fractions_by_grid.values())
    if max_fraction_error > 1.0e-10:
        raise ValueError(f"source fractions do not close: max error={max_fraction_error}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MAPPING_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "mapping_row_count": len(rows),
        "source_grid_count": len(source_area_by_id),
        "routing_unit_count": len({row["unit_id"] for row in rows}),
        "source_area_km2": sum(source_area_by_id.values()),
        "raw_overlap_area_km2": sum(raw_overlap_m2.values()) / 1_000_000.0,
        "adjusted_overlap_area_km2": sum(row["adjusted_overlap_area_km2"] for row in rows),
        "max_source_fraction_error": max_fraction_error,
        "nearest_fallback_grid_count": len(missing_grids),
        "routing_method": "REACH_SEGMENT",
        "routing_dem_resolution_m": grid.cellsize,
        "source_grid_resolution_m": 1000.0,
    }
