"""Build the registered 1 km runoff-grid D8 and grid-to-reach relations."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path


NEIGHBOURS = (
    (-1, -1, math.sqrt(2)), (-1, 0, 1.0), (-1, 1, math.sqrt(2)),
    (0, -1, 1.0), (0, 1, 1.0),
    (1, -1, math.sqrt(2)), (1, 0, 1.0), (1, 1, math.sqrt(2)),
)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"1 km 网格汇流资料中的{label}无效") from exc
    if not math.isfinite(number):
        raise ValueError(f"1 km 网格汇流资料中的{label}无效")
    return number


def build_grid_routing(bundle):
    """Derive one steepest-downhill D8 edge per grid and retain registered weights."""
    paths = bundle["paths"]
    static_rows = _read_csv(paths["grids"])
    geometry = _read_json(paths["geometry"])
    mapping_rows = _read_csv(paths["mapping"])
    topology = _read_json(paths["topology"])

    static = {}
    by_cell = {}
    for source in static_rows:
        grid_id = source["unit_id"]
        if not grid_id or grid_id in static:
            raise ValueError("1 km 网格编号为空或重复")
        row, col = int(source["source_row"]), int(source["source_col"])
        if (row, col) in by_cell:
            raise ValueError("1 km 网格行列位置重复")
        static[grid_id] = {
            "id": grid_id, "row": row, "col": col,
            "elevationM": _finite(source["elevation_m"], "网格高程"),
            "areaKm2": _finite(source["area_km2"], "网格面积"),
        }
        if static[grid_id]['areaKm2'] <= 0:
            raise ValueError('1 km 网格面积须大于零')
        by_cell[(row, col)] = grid_id
    if not static:
        raise ValueError("未登记 1 km 产流网格")

    positions = {}
    for feature in geometry.get("features", []):
        props = feature.get("properties", {})
        position = (int(props["row"]), int(props["col"]))
        if position in positions:
            raise ValueError("1 km 网格空间位置重复")
        coordinates = feature.get("geometry", {}).get("coordinates", [])
        if feature.get("geometry", {}).get("type") != "Polygon" or not coordinates:
            raise ValueError("1 km 网格必须是多边形")
        ring = coordinates[0]
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError("1 km 网格边界不闭合")
        polygon = [[_finite(point[0], "网格经度"), _finite(point[1], "网格纬度")] for point in ring]
        unique_points = polygon[:-1]
        positions[position] = {
            "polygon": polygon,
            "center": [sum(point[0] for point in unique_points) / len(unique_points),
                       sum(point[1] for point in unique_points) / len(unique_points)],
        }
    if set(positions) != set(by_cell):
        raise ValueError("1 km 网格几何与产流格点清单不一致")

    reach_rows = topology.get("reaches", [])
    unit_rows = topology.get("units", [])
    reach_ids = {row["reach_id"] for row in reach_rows}
    unit_ids = {row["unit_id"] for row in unit_rows}
    if not reach_ids or not unit_ids:
        raise ValueError("未登记河段或汇流单元拓扑")
    if len(reach_ids) != len(reach_rows) or len(unit_ids) != len(unit_rows):
        raise ValueError('河段或汇流单元编号重复')
    unit_reaches = {row['unit_id']: row['reach_id'] for row in unit_rows}
    if any(reach not in reach_ids for reach in unit_reaches.values()):
        raise ValueError('汇流单元引用了未登记河段')

    links_by_grid = {grid_id: [] for grid_id in static}
    totals = {grid_id: 0.0 for grid_id in static}
    for source in mapping_rows:
        grid_id, reach_id, unit_id = source["grid_id"], source["reach_id"], source["unit_id"]
        if grid_id not in static or reach_id not in reach_ids or unit_id not in unit_ids:
            raise ValueError("网格—河段映射引用了未登记的网格、河段或汇流单元")
        if unit_reaches[unit_id] != reach_id:
            raise ValueError('网格挂接河段与汇流单元所属河段不一致')
        fraction = _finite(source["source_fraction"], "面积权重")
        area = _finite(source["adjusted_overlap_area_km2"], "挂接面积")
        if not 0 <= fraction <= 1.000001 or area < 0:
            raise ValueError("网格—河段映射权重或面积超出范围")
        links_by_grid[grid_id].append({
            "reachId": reach_id, "unitId": unit_id, "areaKm2": area,
            "fraction": fraction, "method": source["mapping_method"],
        })
        totals[grid_id] += fraction
    if any(not links_by_grid[grid_id] or not math.isclose(totals[grid_id], 1.0, rel_tol=0, abs_tol=1e-6)
           for grid_id in static):
        raise ValueError("网格—河段面积权重未闭合")

    grid_rows = []
    for grid_id, row in sorted(static.items()):
        best = None
        for dr, dc, distance in NEIGHBOURS:
            downstream_id = by_cell.get((row["row"] + dr, row["col"] + dc))
            if downstream_id is None:
                continue
            slope = (row["elevationM"] - static[downstream_id]["elevationM"]) / distance
            if slope > 0 and (best is None or slope > best[0]):
                best = (slope, downstream_id)
        position = positions[(row["row"], row["col"])]
        grid_rows.append({
            **row, "center": position["center"], "polygon": position["polygon"],
            "downstreamGridId": best[1] if best else None,
            "mapping": {"links": links_by_grid[grid_id], "totalFraction": totals[grid_id]},
        })

    reaches = [{
        "id": row["reach_id"], "downstream": row.get("downstream_reach_id"),
        "lengthKm": row.get("length_km", 0), "isControl": row["reach_id"] == "R0020_UP",
        "isTerminal": row.get("downstream_reach_id") is None,
    } for row in reach_rows]
    return {
        "schemaVersion": "1.0",
        "method": {"gridFlow": "D8_MAX_NEIGHBOR_SLOPE", "elevationField": "grid_static.csv:elevation_m",
                   "gridToReach": "REGISTERED_AREA_WEIGHT"},
        "source": {
            "bundleVersionId": bundle["bundleVersionId"],
            "bundleManifestSHA256": bundle["manifestSHA256"],
            "files": {name: _sha256(paths[key]) for name, key in (
                ("gridStatic", "grids"), ("gridGeometry", "geometry"),
                ("gridToReachWeights", "mapping"), ("reachTopology", "topology"))},
        },
        "summary": {
            "gridCount": len(grid_rows),
            "d8EdgeCount": sum(row["downstreamGridId"] is not None for row in grid_rows),
            "localTerminalCount": sum(row["downstreamGridId"] is None for row in grid_rows),
            "mappingRowCount": sum(len(row["mapping"]["links"]) for row in grid_rows),
            "reachCount": len(reaches),
            "fallbackGridCount": sum(any(link["method"] == "NEAREST_FALLBACK" for link in row["mapping"]["links"])
                                      for row in grid_rows),
        },
        "grids": grid_rows,
        "reaches": reaches,
    }
