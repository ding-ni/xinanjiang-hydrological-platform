# -*- coding: utf-8 -*-
"""Split a DEM-derived reach at a snapped control-section cell.

This produces a diagnostic topology. The production topology is left intact
until the split is reviewed and reach routing parameters are assigned.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, box, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from xaj_routing.raster_formats import read_esri_ascii_grid
from xaj_routing.spatial import (
    assign_cells_to_reaches,
    build_grid_topology,
    downstream_cell,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def split_reach(result, reach_id: str, control_cell: tuple[int, int]):
    target = next((reach for reach in result.reaches if reach.reach_id == reach_id), None)
    if target is None:
        raise ValueError(f"reach not found: {reach_id}")
    if control_cell not in target.cells:
        raise ValueError(f"control cell {control_cell} is not on {reach_id}: {target.cells[:3]} ...")
    index = target.cells.index(control_cell)
    if index == 0 or index == len(target.cells) - 1:
        raise ValueError("control cell must be inside the reach, not at its endpoint")

    from xaj_routing.spatial import ReachSegment

    up_cells = tuple(target.cells[: index + 1])
    down_cells = tuple(target.cells[index + 1 :])
    up = ReachSegment("R0020_UP", target.start_cell, control_cell, up_cells, "R0020_DOWN", max(len(up_cells) - 1, 1) * result.cell_size_m / 1000.0)
    down = ReachSegment("R0020_DOWN", down_cells[0], target.end_cell, down_cells, target.downstream_reach_id, max(len(down_cells) - 1, 1) * result.cell_size_m / 1000.0)
    reaches = []
    for reach in result.reaches:
        if reach.reach_id == reach_id:
            reaches.extend((up, down))
        else:
            reaches.append(reach)
    return reaches, up, down, index


def build_units(result, reaches, mask, zone_type: str):
    from xaj_routing.spatial import build_unit_info

    return build_unit_info(
        result.basin_id,
        result.spatial_version + "_control_split_v1",
        result.filled_dem,
        result.slope_deg,
        result.aspect_deg,
        result.flow_dir,
        result.stream_raster,
        reaches,
        mask,
        result.cell_size_m,
        zone_type,
    )


def write_mapping(output: Path, grid_static: Path, grid_geojson: Path, units: list[dict], assignment: dict[tuple[int, int], str | None], grid: object, cell_size_m: float) -> dict:
    grids = list(csv.DictReader(grid_static.open(encoding="utf-8-sig", newline="")))
    id_by_source = {(int(row["source_row"]), int(row["source_col"])): row["unit_id"] for row in grids}
    geo = json.loads(grid_geojson.read_text(encoding="utf-8"))
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    geometries = [transform(transformer.transform, shape(feature["geometry"])) for feature in geo["features"]]
    source_ids = [id_by_source[(int(feature["properties"]["row"]), int(feature["properties"]["col"]))] for feature in geo["features"]]
    tree = STRtree(geometries)
    raw: dict[tuple[str, str], float] = {}
    assigned_centers: list[Point] = []
    assigned_reaches: list[str] = []
    half = cell_size_m / 2.0
    for (row, col), reach_id in assignment.items():
        if reach_id is None:
            continue
        x = grid.xllcorner + (col + 0.5) * cell_size_m
        y = grid.yllcorner + (grid.nrows - row - 0.5) * cell_size_m
        center = Point(x, y)
        assigned_centers.append(center)
        assigned_reaches.append(reach_id)
        cell_polygon = box(x - half, y - half, x + half, y + half)
        for index in tree.query(cell_polygon):
            index = int(index)
            overlap = cell_polygon.intersection(geometries[index]).area
            if overlap > 0.0:
                key = (source_ids[index], reach_id)
                raw[key] = raw.get(key, 0.0) + overlap
    raw_by_grid: dict[str, float] = {}
    for (grid_id, _), area in raw.items():
        raw_by_grid[grid_id] = raw_by_grid.get(grid_id, 0.0) + area
    source_by_id = dict(zip(source_ids, geometries))
    missing_ids = sorted(set(source_ids) - set(raw_by_grid))
    if missing_ids:
        assigned_tree = STRtree(assigned_centers)
        for grid_id in missing_ids:
            index = int(assigned_tree.nearest(source_by_id[grid_id]))
            raw[(grid_id, assigned_reaches[index])] = 0.0
            raw_by_grid[grid_id] = 0.0
    rows = []
    missing = 0
    for item in grids:
        grid_id = item["unit_id"]
        pairs = [(reach_id, area) for (gid, reach_id), area in raw.items() if gid == grid_id]
        denominator = raw_by_grid.get(grid_id, 0.0)
        if not pairs:
            missing += 1
        for reach_id, overlap in pairs:
            fraction = overlap / denominator if denominator > 0.0 else 1.0
            rows.append({
                "grid_id": grid_id,
                "unit_id": f"U_{reach_id}_NON_GLACIER",
                "reach_id": reach_id,
                "raw_overlap_area_km2": overlap / 1_000_000.0,
                "adjusted_overlap_area_km2": float(item["area_km2"]) * fraction,
                "source_fraction": fraction,
                "mapping_method": "D8_FLOW_PATH_AREA_OVERLAP_CONTROL_SPLIT",
            })
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {"grid_count": len(grids), "mapping_row_count": len(rows), "unassigned_grid_count": missing, "assigned_grid_count": len(grids) - missing}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ascii", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--grid-static", required=True, type=Path)
    parser.add_argument("--grid-geojson", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threshold-km2", type=float, default=1.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    grid = read_esri_ascii_grid(args.ascii)
    result = build_grid_topology(
        dem=grid.elevation,
        cell_size_m=grid.cellsize,
        stream_threshold_km2=args.threshold_km2,
        basin_id="nan_an",
        spatial_version="nan_an_dem_utm50_30m_clipped_v1",
        nodata=grid.nodata,
        zone_type="non_glacier",
    )
    control = json.loads(args.control.read_text(encoding="utf-8-sig"))
    snapped = control["snapped"]
    control_cell = tuple(int(value) for value in snapped["cell"])
    control_reach = str(snapped["reach_id"])
    reaches, up, down, split_index = split_reach(result, control_reach, control_cell)
    mask = np.isfinite(grid.elevation) & (grid.elevation != grid.nodata)
    units = build_units(result, reaches, mask, "non_glacier")
    assignment = assign_cells_to_reaches(result.flow_dir, result.stream_raster, reaches, mask)
    mapping_summary = write_mapping(args.output_dir / "grid_to_split_unit.csv", args.grid_static, args.grid_geojson, units, assignment, grid, grid.cellsize)

    old_unit_area = next(unit["area_km2"] for unit in result.units if unit["reach_id"] == control_reach)
    up_unit = next(unit for unit in units if unit["reach_id"] == "R0020_UP")
    down_unit = next(unit for unit in units if unit["reach_id"] == "R0020_DOWN")
    reach_payload = [reach.to_mapping() for reach in reaches]
    unit_payload = units
    topo = {
        "spatial_version": "nan_an_dem_utm50_30m_clipped_control_split_v1",
        "basin_id": "nan_an",
        "cell_size_m": grid.cellsize,
        "source_ascii": str(args.ascii).replace("\\", "/"),
        "stream_threshold_km2": args.threshold_km2,
        "control_section": {
            "control_id": control.get("control_id"),
            "control_reach_original": control_reach,
            "control_cell": list(control_cell),
            "split_index_in_original_reach": split_index,
            "upstream_reach_id": "R0020_UP",
            "downstream_reach_id": "R0020_DOWN",
            "snap_distance_m": snapped.get("distance_m"),
        },
        "reaches": reach_payload,
        "units": unit_payload,
    }
    write_json(args.output_dir / "nan_an_topology_control_split_v1.json", topo)

    assignment_grid = np.full(result.flow_dir.shape, -1, dtype=np.int16)
    reach_names = {reach.reach_id: index + 1 for index, reach in enumerate(reaches)}
    for cell, reach_id in assignment.items():
        if reach_id is not None:
            assignment_grid[cell] = reach_names[reach_id]
    np.savetxt(args.output_dir / "d8_split_unit_assignment.asc", assignment_grid, fmt="%d")
    summary = {
        "algorithm": "same packaged 30 m DEM -> fill depression -> D8 -> flow accumulation -> 1 km2 stream threshold",
        "control_cell": list(control_cell),
        "control_reach_original": control_reach,
        "original_reach_cell_count": len(next(reach for reach in result.reaches if reach.reach_id == control_reach).cells),
        "upstream_reach_cell_count": len(up.cells),
        "downstream_reach_cell_count": len(down.cells),
        "original_unit_area_km2": old_unit_area,
        "upstream_unit_area_km2": up_unit["area_km2"],
        "downstream_unit_area_km2": down_unit["area_km2"],
        "split_unit_area_sum_km2": up_unit["area_km2"] + down_unit["area_km2"],
        "area_difference_km2": up_unit["area_km2"] + down_unit["area_km2"] - old_unit_area,
        "upstream_fraction_of_original_unit": up_unit["area_km2"] / old_unit_area,
        "downstream_fraction_of_original_unit": down_unit["area_km2"] / old_unit_area,
        "reach_count_before": len(result.reaches),
        "reach_count_after": len(reaches),
        "unit_count_after": len(units),
        "mapping": mapping_summary,
        "control_accumulation_area_km2": snapped.get("accumulation_area_km2"),
        "warning": "diagnostic topology; routing parameter records for R0020_UP/R0020_DOWN are not yet assigned",
    }
    write_json(args.output_dir / "control_split_summary.json", summary)
    md = [
        "# 徐畈控制断面 R0020 切分结果", "",
        f"- 控制断面栅格：`{control_cell}`。",
        f"- 原 R0020 河网像元数：`{len(next(reach for reach in result.reaches if reach.reach_id == control_reach).cells)}`。",
        f"- R0020_UP 像元数：`{len(up.cells)}`，集水单元面积：`{up_unit['area_km2']:.4f} km2`。",
        f"- R0020_DOWN 像元数：`{len(down.cells)}`，集水单元面积：`{down_unit['area_km2']:.4f} km2`。",
        f"- 切分前后 R0020 单元面积差：`{summary['area_difference_km2']:.6f} km2`。",
        f"- R0020_UP 占原 R0020 单元：`{summary['upstream_fraction_of_original_unit']:.3%}`；R0020_DOWN：`{summary['downstream_fraction_of_original_unit']:.3%}`。",
        f"- 1 km 产流网格已按 D8 流向分配，未分配网格数：`{mapping_summary['unassigned_grid_count']}`。",
        "",
        "## 数据流",
        "",
        "```text",
        "30 m DEM -> 填洼 -> D8 -> 累积量 -> 1 km2 阈值河网",
        "       -> 原 R0020 河段像元序列 -> 控制断面像元切分",
        "       -> R0020_UP / R0020_DOWN 集水区",
        "       -> 1 km 网格 D8 流向归属 -> grid_to_split_unit.csv",
        "```",
        "",
        "## 注意",
        "",
        "该结果是诊断拓扑，未覆盖正式拓扑。正式汇流前必须为 R0020_UP、R0020_DOWN 配置独立 Muskingum 参数，并在控制断面处输出 R0020_UP 的出流。",
    ]
    (args.output_dir / "control_split_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
