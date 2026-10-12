"""CLI for static terrain topology construction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .io import write_json
from .raster_formats import (
    clip_ascii_grid_by_polygons,
    convert_arcgrid_with_rasterio,
    read_esri_ascii_grid,
    write_esri_ascii_grid,
    write_geotiff_from_ascii_grid,
)
from .spatial import build_grid_topology
from .spatial_products import (
    outlet_candidates_from_result,
    placeholder_control_from_candidate,
    snap_point_to_stream,
)
from .vector_formats import read_shapefile_polygons, transform_polygon_shapes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xaj-spatial")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser_ = subparsers.add_parser(
        "build-grid-topology",
        help="build routing topology from a DEM JSON grid",
    )
    build_parser_.add_argument("--dem-json", required=True, help="input DEM JSON path")
    build_parser_.add_argument("--out", required=True, help="output topology JSON path")
    build_parser_.add_argument(
        "--no-grids",
        action="store_true",
        help="only export units and reaches, not raster matrices",
    )

    ascii_parser = subparsers.add_parser(
        "build-from-ascii",
        help="build routing topology from an ESRI ASCII DEM",
    )
    ascii_parser.add_argument("--ascii", required=True, help="input ESRI ASCII grid path")
    ascii_parser.add_argument("--out", required=True, help="output topology JSON path")
    ascii_parser.add_argument("--cell-size-m", required=True, type=float)
    ascii_parser.add_argument("--stream-threshold-km2", required=True, type=float)
    ascii_parser.add_argument("--basin-id", default="BASIN")
    ascii_parser.add_argument("--spatial-version", default="spatial_v1")
    ascii_parser.add_argument("--zone-type", default="non_glacier")
    ascii_parser.add_argument("--grid-crs", default=None)
    ascii_parser.add_argument("--outlet-candidates-out", default=None)
    ascii_parser.add_argument("--placeholder-control-out", default=None)
    ascii_parser.add_argument("--placeholder-control-id", default="xufan_control")
    ascii_parser.add_argument("--placeholder-control-name", default="Xufan control section")
    ascii_parser.add_argument("--no-grids", action="store_true")

    convert_parser = subparsers.add_parser(
        "convert-arcgrid",
        help="convert Arc/Info Grid to GeoTIFF and optional ESRI ASCII grid",
    )
    convert_parser.add_argument("--src", required=True, help="source Arc/Info Grid folder")
    convert_parser.add_argument("--tif", required=True, help="output GeoTIFF path")
    convert_parser.add_argument("--ascii", default=None, help="optional output ASCII grid path")
    convert_parser.add_argument("--dst-crs", default=None, help="optional output CRS")
    convert_parser.add_argument("--resolution", default=None, type=float)
    convert_parser.add_argument("--summary-out", default=None, help="optional summary JSON path")

    clip_parser = subparsers.add_parser(
        "clip-ascii-by-shp",
        help="clip an ESRI ASCII DEM by a polygon shapefile boundary",
    )
    clip_parser.add_argument("--ascii", required=True, help="input ESRI ASCII grid path")
    clip_parser.add_argument("--boundary-shp", required=True, help="input basin boundary shapefile")
    clip_parser.add_argument("--out-ascii", required=True, help="output clipped ASCII grid path")
    clip_parser.add_argument("--out-tif", default=None, help="optional output clipped GeoTIFF path")
    clip_parser.add_argument("--boundary-crs", default="EPSG:4490")
    clip_parser.add_argument("--grid-crs", default="EPSG:32650")
    clip_parser.add_argument("--nodata", default=-9999.0, type=float)
    clip_parser.add_argument("--summary-out", default=None)
    clip_parser.add_argument("--no-crop", action="store_true")

    snap_parser = subparsers.add_parser(
        "snap-point-from-ascii",
        help="build topology from ASCII and snap a point to the extracted stream raster",
    )
    snap_parser.add_argument("--ascii", required=True, help="input ESRI ASCII grid path")
    snap_parser.add_argument("--out", required=True, help="output snapped control JSON path")
    snap_parser.add_argument("--cell-size-m", required=True, type=float)
    snap_parser.add_argument("--stream-threshold-km2", required=True, type=float)
    snap_parser.add_argument("--x", required=True, type=float)
    snap_parser.add_argument("--y", required=True, type=float)
    snap_parser.add_argument("--point-crs", default=None)
    snap_parser.add_argument("--grid-crs", default=None)
    snap_parser.add_argument("--basin-id", default="BASIN")
    snap_parser.add_argument("--spatial-version", default="spatial_v1")
    snap_parser.add_argument("--zone-type", default="non_glacier")
    snap_parser.add_argument("--control-id", default="control_point")
    snap_parser.add_argument("--control-name", default=None)
    snap_parser.add_argument("--max-distance-m", default=None, type=float)
    return parser


def run_build_grid_topology(args: argparse.Namespace) -> int:
    with Path(args.dem_json).open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    result = build_grid_topology(
        dem=np.asarray(data["elevation"], dtype=float),
        cell_size_m=float(data["cell_size_m"]),
        stream_threshold_km2=float(data["stream_threshold_km2"]),
        basin_id=str(data.get("basin_id", "BASIN")),
        spatial_version=str(data.get("spatial_version", "spatial_v1")),
        nodata=data.get("nodata"),
        fill_epsilon=float(data.get("fill_epsilon", 1.0e-6)),
        zone_type=str(data.get("zone_type", "non_glacier")),
    )
    write_json(args.out, result.to_mapping(include_grids=not args.no_grids))
    print(
        "topology complete: "
        f"{len(result.reaches)} reaches, "
        f"{len(result.units)} units, "
        f"spatial_version={result.spatial_version}"
    )
    return 0


def run_build_from_ascii(args: argparse.Namespace) -> int:
    grid = read_esri_ascii_grid(args.ascii)
    result = build_grid_topology(
        dem=grid.elevation,
        cell_size_m=args.cell_size_m,
        stream_threshold_km2=args.stream_threshold_km2,
        basin_id=args.basin_id,
        spatial_version=args.spatial_version,
        nodata=grid.nodata,
        zone_type=args.zone_type,
    )
    payload = result.to_mapping(include_grids=not args.no_grids)
    payload["source_ascii"] = args.ascii.replace("\\", "/")
    payload["ascii_extent"] = grid.extent
    payload["ascii_cellsize"] = grid.cellsize
    write_json(args.out, payload)
    candidates = None
    if args.outlet_candidates_out or args.placeholder_control_out:
        candidates = outlet_candidates_from_result(
            result,
            grid,
            grid_crs=args.grid_crs,
        )
    if args.outlet_candidates_out:
        write_json(
            args.outlet_candidates_out,
            {
                "basin_id": args.basin_id,
                "spatial_version": args.spatial_version,
                "grid_crs": args.grid_crs,
                "source_ascii": args.ascii.replace("\\", "/"),
                "candidates": candidates,
            },
        )
    if args.placeholder_control_out:
        if not candidates:
            raise ValueError("no outlet candidates were produced")
        write_json(
            args.placeholder_control_out,
            placeholder_control_from_candidate(
                candidates[0],
                control_id=args.placeholder_control_id,
                name=args.placeholder_control_name,
            ),
        )
    print(
        "topology complete: "
        f"{len(result.reaches)} reaches, "
        f"{len(result.units)} units, "
        f"spatial_version={result.spatial_version}"
    )
    return 0


def run_convert_arcgrid(args: argparse.Namespace) -> int:
    summary = convert_arcgrid_with_rasterio(
        source=args.src,
        geotiff_out=args.tif,
        ascii_out=args.ascii,
        dst_crs=args.dst_crs,
        resolution=args.resolution,
    )
    if args.summary_out:
        write_json(args.summary_out, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_clip_ascii_by_shp(args: argparse.Namespace) -> int:
    grid = read_esri_ascii_grid(args.ascii)
    shapes = read_shapefile_polygons(args.boundary_shp)
    transformed = transform_polygon_shapes(shapes, args.boundary_crs, args.grid_crs)
    clipped, summary = clip_ascii_grid_by_polygons(
        grid,
        transformed,
        output_nodata=args.nodata,
        crop=not args.no_crop,
    )
    write_esri_ascii_grid(args.out_ascii, clipped)
    summary.update(
        {
            "source_ascii": args.ascii.replace("\\", "/"),
            "boundary_shp": args.boundary_shp.replace("\\", "/"),
            "boundary_crs": args.boundary_crs,
            "grid_crs": args.grid_crs,
            "out_ascii": args.out_ascii.replace("\\", "/"),
            "shape_count": len(shapes),
        }
    )
    if args.out_tif:
        summary["geotiff"] = write_geotiff_from_ascii_grid(
            args.out_tif,
            clipped,
            crs=args.grid_crs,
        )
    if args.summary_out:
        write_json(args.summary_out, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_snap_point_from_ascii(args: argparse.Namespace) -> int:
    grid = read_esri_ascii_grid(args.ascii)
    result = build_grid_topology(
        dem=grid.elevation,
        cell_size_m=args.cell_size_m,
        stream_threshold_km2=args.stream_threshold_km2,
        basin_id=args.basin_id,
        spatial_version=args.spatial_version,
        nodata=grid.nodata,
        zone_type=args.zone_type,
    )
    payload = snap_point_to_stream(
        result=result,
        grid=grid,
        x=args.x,
        y=args.y,
        point_crs=args.point_crs,
        grid_crs=args.grid_crs,
        max_distance_m=args.max_distance_m,
        control_id=args.control_id,
        name=args.control_name,
    )
    payload["source_ascii"] = args.ascii.replace("\\", "/")
    payload["stream_threshold_km2"] = args.stream_threshold_km2
    write_json(args.out, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build-grid-topology":
        return run_build_grid_topology(args)
    if args.command == "build-from-ascii":
        return run_build_from_ascii(args)
    if args.command == "convert-arcgrid":
        return run_convert_arcgrid(args)
    if args.command == "clip-ascii-by-shp":
        return run_clip_ascii_by_shp(args)
    if args.command == "snap-point-from-ascii":
        return run_snap_point_from_ascii(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
