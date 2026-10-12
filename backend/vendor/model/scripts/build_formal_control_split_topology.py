# -*- coding: utf-8 -*-
"""Build the persisted routing topology with explicit control-section nodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from xaj_routing.control_split import split_topology_at_control_points
from xaj_routing.raster_formats import read_esri_ascii_grid
from xaj_routing.spatial import build_grid_topology, valid_mask


def _read_controls(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return payload
    if "control_points" in payload:
        return list(payload["control_points"])
    if "controls" in payload:
        return list(payload["controls"])
    return [payload]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ascii", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threshold-km2", type=float, default=1.0)
    parser.add_argument("--basin-id", default="nan_an")
    parser.add_argument("--spatial-version", default="nan_an_dem_utm50_30m_clipped_control_split_v2")
    args = parser.parse_args()

    grid = read_esri_ascii_grid(args.ascii)
    base = build_grid_topology(
        dem=grid.elevation,
        cell_size_m=grid.cellsize,
        stream_threshold_km2=args.threshold_km2,
        basin_id=args.basin_id,
        spatial_version=args.spatial_version.replace("_control_split_v2", "_base_v1"),
        nodata=grid.nodata,
        zone_type="non_glacier",
    )
    mask = valid_mask(grid.elevation, grid.nodata)
    controls = _read_controls(args.control)
    split = split_topology_at_control_points(base, mask, controls)
    payload = split.to_mapping(include_grids=False)
    payload.update(
        {
            "spatial_version": args.spatial_version,
            "source_ascii": str(args.ascii).replace("\\", "/"),
            "stream_threshold_km2": args.threshold_km2,
            "control_points_source": str(args.control).replace("\\", "/"),
            "topology_status": "FORMAL_CONTROL_SPLIT",
            "base_spatial_version": base.spatial_version,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "status": "FORMAL_CONTROL_SPLIT",
        "basin_id": split.basin_id,
        "base_reach_count": len(base.reaches),
        "split_reach_count": len(split.reaches),
        "unit_count": len(split.units),
        "control_node_count": len(split.control_nodes),
        "control_nodes": split.control_nodes,
        "cell_size_m": split.cell_size_m,
        "stream_threshold_km2": args.threshold_km2,
    }
    summary_path = args.output.with_name(args.output.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
