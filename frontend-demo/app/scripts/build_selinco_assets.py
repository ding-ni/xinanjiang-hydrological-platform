from __future__ import annotations

import json
import heapq
import os
import shutil
import tempfile
import argparse
from collections import Counter, defaultdict
from pathlib import Path

import fiona
import numpy as np
import rasterio
import xarray as xr
from PIL import Image
from pyproj import Geod
from rasterio.windows import from_bounds
from rasterio.features import geometry_mask
from shapely import contains_xy
from shapely.geometry import LineString, box, mapping, shape
from shapely.ops import linemerge, nearest_points, unary_union
from pysheds.grid import Grid


APP_ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOT = APP_ROOT / "src" / "data" / "generated" / "selinco"
PUBLIC_ROOT = APP_ROOT / "public" / "data" / "selinco"
PACKAGE_ROOT: Path | None = None
DEM_ROOT: Path | None = None
STREAM_ROOT: Path | None = None

SUBBASIN_NAMES = {
    "扎加藏布边界": "扎加藏布入湖流域",
    "扎根藏布边界": "扎根藏布入湖流域",
    "波曲藏布边界": "波曲藏布入湖流域",
    "阿里藏布边界": "阿里藏布入湖流域",
}


def relative_resource_url(target: Path, base_dir: Path) -> str:
    """Return a portable POSIX URL relative to the JSON file's directory."""
    relative = os.path.relpath(target, base_dir).replace(os.sep, "/")
    return relative if relative.startswith(".") else f"./{relative}"


def read_shapes(path: Path):
    with fiona.open(str(path)) as source:
        return [shape(feature["geometry"]) for feature in source]


def feature_collection(features):
    return {"type": "FeatureCollection", "features": features}


def provided_river_network():
    """Build Strahler ordering on the supplied outlet-oriented 50-cell network."""
    features = []
    counts = {}
    records = []
    source_path = STREAM_ROOT / "merged" / "selinco_4basin_streams_thr50cells.shp"
    with fiona.open(str(source_path)) as source:
        for feature in source:
            properties = dict(feature["properties"])
            geometry = shape(feature["geometry"])
            records.append({"properties": properties, "geometry": geometry})
            basin_id = properties.get("basin_id")
            counts[basin_id] = counts.get(basin_id, 0) + 1

    order_counts = {}
    max_orders = {}
    for basin_id in ["zajia", "zagen", "boqu", "ali"]:
        basin_records = [record for record in records if record["properties"].get("basin_id") == basin_id]
        ends = defaultdict(list)
        for index, record in enumerate(basin_records):
            coordinates = list(record["geometry"].coords)
            end = tuple(round(value, 6) for value in coordinates[-1])
            ends[end].append(index)

        orders = {}
        unresolved = set(range(len(basin_records)))
        while unresolved:
            changed = False
            for index in list(unresolved):
                coordinates = list(basin_records[index]["geometry"].coords)
                start = tuple(round(value, 6) for value in coordinates[0])
                upstream = ends.get(start, [])
                if not all(item in orders for item in upstream):
                    continue
                incoming = [orders[item] for item in upstream]
                if not incoming:
                    order = 1
                else:
                    maximum = max(incoming)
                    order = maximum + 1 if incoming.count(maximum) >= 2 else maximum
                orders[index] = order
                unresolved.remove(index)
                changed = True
            if not changed:
                for index in unresolved:
                    orders[index] = 1
                unresolved.clear()

        distribution = Counter(orders.values())
        order_counts[basin_id] = {str(order): count for order, count in sorted(distribution.items())}
        max_orders[basin_id] = max(orders.values(), default=0)
        for index, record in enumerate(basin_records, start=1):
            properties = record["properties"]
            order = orders[index - 1]
            features.append({
                "type": "Feature",
                "properties": {
                    "id": f"{basin_id}-strahler-{index:03d}",
                    "order": order,
                    "riverGrade": f"{order}级河道",
                    "basinId": basin_id,
                    "subbasin": properties.get("basin_cn"),
                    "minimumDrainageAreaKm2": 53,
                    "lengthKm": round(float(properties.get("len_km") or 0), 2),
                    "kind": "river",
                    "method": "supplied filled DEM / D8 / flow accumulation / Strahler",
                },
                "geometry": mapping(record["geometry"].simplify(0.0006, preserve_topology=True)),
            })

    source_output = PUBLIC_ROOT / "hydrology" / "provided"
    for basin_id in ["zajia", "zagen", "boqu", "ali"]:
        source_output.mkdir(parents=True, exist_ok=True)
        for suffix in ["dem_filled.tif", "d8_pointer.tif", "flow_accum_cells.tif"]:
            source = STREAM_ROOT / basin_id / f"{basin_id}_{suffix}"
            if source.exists():
                shutil.copyfile(source, source_output / source.name)

    analysis = {
        "method": ["DEM填洼", "D8流向", "汇流累积", "最小成河汇水面积筛选", "Strahler河级计算", "四入湖流域合并"],
        "source": "provided stream network package",
        "resolution": "0.01°（约1 km）",
        "thresholdCells": [50, 150],
        "minimumDrainageAreaKm2": 53,
        "referenceMainChannelAreaKm2": 158,
        "streamOrderMethod": "Strahler",
        "orderCounts": order_counts,
        "maxOrderByBasin": max_orders,
        "maxOrder": max(max_orders.values(), default=0),
        "lineCounts": counts,
        "displayHierarchy": {"1": "一级河道", "2": "二级河道", "3": "三级河道", "4": "四级河道"},
        "rasterOutputs": [
            f"provided/{basin_id}_{suffix}"
            for basin_id in ["zajia", "zagen", "boqu", "ali"]
            for suffix in ["dem_filled.tif", "d8_pointer.tif", "flow_accum_cells.tif"]
        ],
    }
    write_json(PUBLIC_ROOT / "hydrology" / "analysis.json", analysis)
    return feature_collection(features), analysis


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def polygon_feature(geometry, properties, tolerance=0.002):
    simplified = geometry.simplify(tolerance, preserve_topology=True)
    return {"type": "Feature", "properties": properties, "geometry": mapping(simplified)}


def grid_lines(domain, bounds, step):
    min_x, min_y, max_x, max_y = bounds
    x0 = np.floor(min_x / step) * step
    y0 = np.floor(min_y / step) * step
    lines = []
    for x in np.arange(x0, max_x + step, step):
        part = LineString([(float(x), min_y), (float(x), max_y)]).intersection(domain)
        if not part.is_empty:
            lines.append(part)
    for y in np.arange(y0, max_y + step, step):
        part = LineString([(min_x, float(y)), (max_x, float(y))]).intersection(domain)
        if not part.is_empty:
            lines.append(part)
    return unary_union(lines)


def cell_count(domain, bounds, step):
    min_x, min_y, max_x, max_y = bounds
    xs = np.arange(np.floor(min_x / step) * step + step / 2, max_x, step)
    ys = np.arange(np.floor(min_y / step) * step + step / 2, max_y, step)
    xx, yy = np.meshgrid(xs, ys)
    return int(contains_xy(domain, xx, yy).sum())


def distributed_cells(domain, bounds, step, scale):
    """Build the actual calculation cells used by the interactive state/runoff maps."""
    min_x, min_y, max_x, max_y = bounds
    x0 = np.floor(min_x / step) * step
    y0 = np.floor(min_y / step) * step
    xs = np.arange(x0 + step / 2, max_x, step)
    ys = np.arange(y0 + step / 2, max_y, step)
    xx, yy = np.meshgrid(xs, ys)
    mask = contains_xy(domain, xx, yy)
    cells = []
    for row, col in np.argwhere(mask):
        lng = float(xs[col])
        lat = float(ys[row])
        west = lng - step / 2
        east = lng + step / 2
        south = lat - step / 2
        north = lat + step / 2
        wet_core = np.exp(-(((lng - 90.75) / 1.05) ** 2 + ((lat - 32.45) / 0.78) ** 2))
        southwest = np.exp(-(((lng - 88.35) / 0.72) ** 2 + ((lat - 31.05) / 0.82) ** 2))
        texture = 0.5 + 0.5 * np.sin(lng * 7.3 + lat * 5.1)
        soil = float(np.clip(0.35 + 0.30 * wet_core + 0.14 * southwest + 0.05 * texture, 0.22, 0.92))
        runoff = float(np.clip(0.25 + 3.4 * wet_core + 1.8 * southwest + 0.35 * texture, 0, 7.5))
        cells.append({
            "id": f"SEL-{scale.upper()}-{row:03d}-{col:03d}",
            "row": int(row),
            "col": int(col),
            "lng": round(lng, 6),
            "lat": round(lat, 6),
            "bounds": [
                [round(west, 6), round(south, 6)],
                [round(east, 6), round(south, 6)],
                [round(east, 6), round(north, 6)],
                [round(west, 6), round(north, 6)],
                [round(west, 6), round(south, 6)],
            ],
            "runoff": round(runoff, 2),
            "soil": round(soil, 2),
            "freeWater": round(soil * 48, 1),
            "surface": int(round(runoff * 42)),
        })
    return cells


def glacier_area_km2(glaciers):
    geod = Geod(ellps="WGS84")
    total = 0.0
    for glacier in glaciers:
        area, _ = geod.geometry_area_perimeter(glacier)
        total += abs(area)
    return total / 1_000_000


def terrain_rgba(dem_path: Path, bounds, output: Path, stats_domain=None):
    with rasterio.open(str(dem_path)) as source:
        window = from_bounds(*bounds, transform=source.transform).round_offsets().round_lengths()
        window = window.intersection(rasterio.windows.Window(0, 0, source.width, source.height))
        dem = source.read(1, window=window, masked=True).astype("float32")
        transform = source.window_transform(window)

    valid = ~np.ma.getmaskarray(dem) & np.isfinite(dem.filled(np.nan))
    values = dem.filled(np.nan)
    values[~valid] = np.nan
    lo, hi = np.nanpercentile(values, [2, 98])
    normalized = np.where(valid, np.clip((values - lo) / max(hi - lo, 1), 0, 1), 0)

    safe = np.where(valid, values, np.nanmedian(values[valid]))
    dy, dx = np.gradient(safe)
    slope = np.pi / 2 - np.arctan(np.hypot(dx, dy) / 22)
    aspect = np.arctan2(-dx, dy)
    azimuth = np.deg2rad(315)
    altitude = np.deg2rad(42)
    shade = np.sin(altitude) * np.sin(slope) + np.cos(altitude) * np.cos(slope) * np.cos(azimuth - aspect)
    shade = np.clip((shade + 1) / 2, 0.2, 1)

    stops = np.array(
        [
            [204, 221, 224],
            [178, 204, 202],
            [187, 199, 177],
            [203, 205, 191],
            [232, 234, 231],
        ],
        dtype="float32",
    )
    scaled = normalized * (len(stops) - 1)
    low = np.floor(scaled).astype(int)
    high = np.clip(low + 1, 0, len(stops) - 1)
    fraction = (scaled - low)[..., None]
    rgb = stops[low] * (1 - fraction) + stops[high] * fraction
    rgb = np.clip(rgb * (0.66 + shade[..., None] * 0.46), 0, 255).astype("uint8")
    alpha = np.where(valid, 244, 0).astype("uint8")
    rgba = np.dstack([rgb, alpha])

    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(rgba)
    if rgba.shape[1] < 100:
        image = image.resize((rgba.shape[1] * 10, rgba.shape[0] * 10), Image.Resampling.NEAREST)
    image.save(output, optimize=True)
    stats_valid = valid
    if stats_domain is not None:
        stats_valid = stats_valid & geometry_mask([mapping(stats_domain)], dem.shape, transform, invert=True)
    stats_values = values[stats_valid]
    return {
        "width": int(rgba.shape[1]),
        "height": int(rgba.shape[0]),
        "elevationMin": int(np.nanmin(stats_values)),
        "elevationMax": int(np.nanmax(stats_values)),
    }


def write_raster(path, values, transform, crs, nodata, dtype):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype=dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
    ) as target:
        target.write(values.astype(dtype), 1)


def river_network(dem_path, subbasins, subbasin_names, outlets, analysis_bounds):
    """Build a connected, outlet-controlled D8 network with Strahler ordering."""
    hydro_root = PUBLIC_ROOT / "hydrology"
    raw_path = hydro_root / "dem-1km-raw.tif"
    with rasterio.open(str(dem_path)) as source:
        window = from_bounds(*analysis_bounds, transform=source.transform).round_offsets().round_lengths()
        window = window.intersection(rasterio.windows.Window(0, 0, source.width, source.height))
        raw = source.read(1, window=window, masked=True).astype("float32")
        transform = source.window_transform(window)
        crs = source.crs
    raw_values = raw.filled(-9999.0)
    write_raster(raw_path, raw_values, transform, crs, -9999.0, "float32")

    valid = raw_values != -9999.0
    rows, cols = raw_values.shape
    xs = transform.c + (np.arange(cols) + 0.5) * transform.a
    ys = transform.f + (np.arange(rows) + 0.5) * transform.e
    xx, yy = np.meshgrid(xs, ys)
    directions = {1: (-1, 0), 2: (-1, 1), 4: (0, 1), 8: (1, 1), 16: (1, 0), 32: (1, -1), 64: (0, -1), 128: (-1, -1)}
    direction_codes = {delta: code for code, delta in directions.items()}
    filled_values = np.full((rows, cols), -9999.0, dtype="float32")
    flowdir = np.zeros((rows, cols), dtype="int16")
    accumulation = np.full((rows, cols), -9999.0, dtype="float32")
    stream_order = np.zeros((rows, cols), dtype="uint8")
    features = []
    outlet_results = []

    for basin_index, (basin, name, outlet) in enumerate(zip(subbasins, subbasin_names, outlets), start=1):
        basin_mask = contains_xy(basin, xx, yy) & valid
        outlet_x, outlet_y = outlet["coordinate"]
        distance = np.where(basin_mask, (xx - outlet_x) ** 2 + (yy - outlet_y) ** 2, np.inf)
        outlet_row, outlet_col = np.unravel_index(np.argmin(distance), distance.shape)
        conditioned = np.full((rows, cols), np.inf, dtype="float64")
        visited = np.zeros((rows, cols), dtype=bool)
        conditioned[outlet_row, outlet_col] = float(raw_values[outlet_row, outlet_col])
        queue = [(conditioned[outlet_row, outlet_col], int(outlet_row), int(outlet_col))]
        downstream = {}
        upstream = [[] for _ in range(rows * cols)]
        while queue:
            elevation, row, col = heapq.heappop(queue)
            if visited[row, col]:
                continue
            visited[row, col] = True
            for dr, dc in directions.values():
                nr, nc = row + dr, col + dc
                if not (0 <= nr < rows and 0 <= nc < cols and basin_mask[nr, nc]) or visited[nr, nc]:
                    continue
                next_elevation = max(float(raw_values[nr, nc]), elevation + 0.01)
                if next_elevation < conditioned[nr, nc]:
                    conditioned[nr, nc] = next_elevation
                    downstream[(nr, nc)] = (row, col)
                    heapq.heappush(queue, (next_elevation, nr, nc))

        for cell, target in downstream.items():
            delta = (target[0] - cell[0], target[1] - cell[1])
            flowdir[cell] = direction_codes[delta]
            upstream[target[0] * cols + target[1]].append(cell)
        filled_values[basin_mask] = conditioned[basin_mask].astype("float32")
        basin_accumulation = np.zeros((rows, cols), dtype="float32")
        basin_accumulation[basin_mask] = 1
        for row, col in sorted([tuple(map(int, cell)) for cell in np.argwhere(basin_mask)], key=lambda cell: conditioned[cell], reverse=True):
            target = downstream.get((row, col))
            if target:
                basin_accumulation[target] += basin_accumulation[row, col]
        accumulation[basin_mask] = basin_accumulation[basin_mask]

        # About 25 km2 at this DEM resolution; small gullies are excluded from the operational map.
        stream_mask = basin_mask & (basin_accumulation >= 25)
        stream_cells = [tuple(map(int, cell)) for cell in np.argwhere(stream_mask)]
        cell_orders = {}
        for cell in sorted(stream_cells, key=lambda item: basin_accumulation[item]):
            incoming = [cell_orders[u] for u in upstream[cell[0] * cols + cell[1]] if u in cell_orders]
            if not incoming:
                order = 1
            else:
                maximum = max(incoming)
                order = maximum + 1 if incoming.count(maximum) >= 2 else maximum
            cell_orders[cell] = order
            stream_order[cell] = order

        grouped_segments = {}
        for cell, order in cell_orders.items():
            target = downstream.get(cell)
            if target not in cell_orders:
                continue
            x1, y1 = rasterio.transform.xy(transform, cell[0], cell[1], offset="center")
            x2, y2 = rasterio.transform.xy(transform, target[0], target[1], offset="center")
            grouped_segments.setdefault(order, []).append(LineString([(x1, y1), (x2, y2)]))

        for order, segments in grouped_segments.items():
            merged = linemerge(unary_union(segments))
            for part_index, line in enumerate(getattr(merged, "geoms", [merged]), start=1):
                if line.length < 0.018:
                    continue
                features.append({
                    "type": "Feature",
                    "properties": {
                        "id": f"RIV-{basin_index}-{order}-{part_index:03d}",
                        "order": int(order),
                        "subbasin": name,
                        "kind": "river",
                        "method": "filled DEM / D8 / flow accumulation / outlet connectivity / Strahler",
                    },
                    "geometry": mapping(line.simplify(0.0015, preserve_topology=True)),
                })
        outlet_results.append({
            **outlet,
            "snappedCoordinate": [round(float(xx[outlet_row, outlet_col]), 6), round(float(yy[outlet_row, outlet_col]), 6)],
            "contributingCells": int(basin_mask.sum()),
            "streamCells": int(stream_mask.sum()),
            "maxOrder": int(max(cell_orders.values(), default=0)),
        })

    write_raster(hydro_root / "dem-1km-filled.tif", filled_values, transform, crs, -9999.0, "float32")
    write_raster(hydro_root / "flow-direction-d8-1km.tif", flowdir, transform, crs, 0, "int16")
    write_raster(hydro_root / "flow-accumulation-1km.tif", accumulation, transform, crs, -9999.0, "float32")
    write_raster(hydro_root / "stream-order-strahler-1km.tif", stream_order, transform, crs, 0, "uint8")
    analysis = {
        "method": ["DEM填洼", "D8流向", "汇流累积", "入湖口连通性筛选", "Strahler分级", "河网矢量化"],
        "sourceDem": dem_path.name,
        "resolution": "0.008333°（约1 km）",
        "streamThresholdCells": 25,
        "outlets": outlet_results,
        "rasterOutputs": [
            "dem-1km-raw.tif",
            "dem-1km-filled.tif",
            "flow-direction-d8-1km.tif",
            "flow-accumulation-1km.tif",
            "stream-order-strahler-1km.tif",
        ],
    }
    write_json(hydro_root / "analysis.json", analysis)
    return feature_collection(features), analysis


def generate_precip_nc(domain, bounds):
    forcing_root = PUBLIC_ROOT / "forcing"
    forcing_root.mkdir(parents=True, exist_ok=True)
    time = np.arange(np.datetime64("2026-06-01T00", "ns"), np.datetime64("2026-06-06T01", "ns"), np.timedelta64(1, "h"))
    products = {}
    previews = {}
    for key, step, filename in [
        ("1km", 0.01, "selinco_precip_hourly_1km.nc"),
        ("0p1deg", 0.1, "selinco_precip_hourly_0p1deg.nc"),
    ]:
        lon = np.arange(np.floor(bounds[0] / step) * step + step / 2, bounds[2], step, dtype="float32")
        lat = np.arange(np.floor(bounds[1] / step) * step + step / 2, bounds[3], step, dtype="float32")
        xx, yy = np.meshgrid(lon, lat)
        mask = contains_xy(domain, xx, yy)
        spatial = (
            0.7 * np.exp(-(((xx - 90.7) / 0.9) ** 2 + ((yy - 32.5) / 0.65) ** 2))
            + 0.45 * np.exp(-(((xx - 88.7) / 0.7) ** 2 + ((yy - 31.2) / 0.8) ** 2))
        )
        hours = np.arange(len(time), dtype="float32")
        pulse = 5.8 * np.exp(-((hours - 39) / 9) ** 2) + 3.2 * np.exp(-((hours - 74) / 13) ** 2)
        precip = (pulse[:, None, None] * (0.35 + spatial[None, :, :])).astype("float32")
        precip[:, ~mask] = np.nan
        dataset = xr.Dataset(
            data_vars={
                "precipitation": (("time", "lat", "lon"), precip, {"long_name": "hourly precipitation", "units": "mm h-1"}),
                "basin_mask": (("lat", "lon"), mask.astype("uint8"), {"long_name": "Selin Co inflow basin mask", "flag_values": [0, 1]}),
            },
            coords={"time": time, "lat": lat, "lon": lon},
            attrs={
                "title": "Selin Co hourly precipitation demonstration forcing",
                "model": "Distributed Xin'anjiang model demonstration",
                "crs": "EPSG:4326",
                "issue_time": "2026-06-02 14:00",
                "history": "Synthetic but structurally valid forcing generated for frontend workflow demonstration",
            },
        )
        output = forcing_root / filename
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            dataset.to_netcdf(temporary_path, engine="netcdf4", encoding={"precipitation": {"zlib": True, "complevel": 4, "_FillValue": -9999.0}})
            shutil.copyfile(temporary_path, output)
        finally:
            temporary_path.unlink(missing_ok=True)
        products[key] = {
            "file": relative_resource_url(output, forcing_root),
            "name": filename,
            "resolution": "1 km" if key == "1km" else "0.1°",
            "timeStart": np.datetime_as_string(time[0], unit="m").replace("T", " "),
            "timeEnd": np.datetime_as_string(time[-1], unit="m").replace("T", " "),
            "timeStep": "1 h",
            "variable": "precipitation",
            "units": "mm h-1",
            "shape": [int(len(time)), int(len(lat)), int(len(lon))],
            "timeCount": int(len(time)),
            "durationHours": int(len(time) - 1),
            "sizeBytes": output.stat().st_size,
        }
        stride = 5 if key == "1km" else 1
        row_count = (len(lat) // stride) * stride
        col_count = (len(lon) // stride) * stride
        trimmed = precip[:, :row_count, :col_count]
        grouped = trimmed.reshape(len(time), row_count // stride, stride, col_count // stride, stride)
        valid = np.isfinite(grouped)
        counts = valid.sum(axis=(2, 4))
        sums = np.nansum(grouped, axis=(2, 4))
        preview_values = np.divide(sums, counts, out=np.full_like(sums, np.nan), where=counts > 0)
        preview_lat = lat[:row_count].reshape(-1, stride).mean(axis=1)
        preview_lon = lon[:col_count].reshape(-1, stride).mean(axis=1)
        preview_step = step * stride
        cells = []
        for row, col in np.argwhere(np.any(np.isfinite(preview_values), axis=0)):
            center_x = float(preview_lon[col])
            center_y = float(preview_lat[row])
            half = preview_step / 2
            values = np.nan_to_num(preview_values[:, row, col], nan=0.0)
            cells.append({
                "id": f"P-{key.upper()}-{row:03d}-{col:03d}",
                "bounds": [
                    [round(center_x - half, 6), round(center_y - half, 6)],
                    [round(center_x + half, 6), round(center_y - half, 6)],
                    [round(center_x + half, 6), round(center_y + half, 6)],
                    [round(center_x - half, 6), round(center_y + half, 6)],
                    [round(center_x - half, 6), round(center_y - half, 6)],
                ],
                "values": [round(float(value), 2) for value in values],
            })
        previews[key] = {
            "source": filename,
            "resolution": products[key]["resolution"],
            "displayStep": round(preview_step, 3),
            "times": [np.datetime_as_string(value, unit="m").replace("T", " ") for value in time],
            "cells": cells,
        }
    write_json(forcing_root / "metadata.json", products)
    write_json(forcing_root / "precip-hourly-preview.json", previews)
    return products


def main():
    global PACKAGE_ROOT, DEM_ROOT, STREAM_ROOT
    parser = argparse.ArgumentParser(description="Build portable browser assets for the Selinco basin package.")
    parser.add_argument(
        "--package-root",
        type=Path,
        default=Path(os.environ["SELINCO_PACKAGE_ROOT"]) if os.environ.get("SELINCO_PACKAGE_ROOT") else None,
        help="色林错源资料目录；也可用 SELINCO_PACKAGE_ROOT 指定。",
    )
    parser.add_argument(
        "--dem-root",
        type=Path,
        default=Path(os.environ["TIBET_DEM_ROOT"]) if os.environ.get("TIBET_DEM_ROOT") else None,
        help="包含 1km 与 0p1deg DEM GeoTIFF 的目录；也可用 TIBET_DEM_ROOT 指定。",
    )
    parser.add_argument(
        "--stream-root",
        type=Path,
        default=Path(os.environ["SELINCO_STREAM_ROOT"]) if os.environ.get("SELINCO_STREAM_ROOT") else None,
        help="包含 merged/selinco_4basin_streams_thr50cells.shp 的目录；也可用 SELINCO_STREAM_ROOT 指定。",
    )
    args = parser.parse_args()
    if args.package_root is None or args.dem_root is None or args.stream_root is None:
        raise FileNotFoundError(
            "色林错资产构建需要显式指定 --package-root、--dem-root、--stream-root，"
            "或设置 SELINCO_PACKAGE_ROOT、TIBET_DEM_ROOT、SELINCO_STREAM_ROOT。"
        )
    PACKAGE_ROOT = args.package_root.expanduser().resolve()
    DEM_ROOT = args.dem_root.expanduser().resolve()
    STREAM_ROOT = args.stream_root.expanduser().resolve()
    required = [
        PACKAGE_ROOT / "full_basin_shp" / "basin1.shp",
        PACKAGE_ROOT / "lake_shp",
        PACKAGE_ROOT / "subbasin_shp",
        PACKAGE_ROOT / "glacier" / "glacier_in_basin.shp",
        DEM_ROOT,
        STREAM_ROOT / "merged" / "selinco_4basin_streams_thr50cells.shp",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("色林错资产输入不完整，请检查 --package-root、--dem-root、--stream-root：\n" + "\n".join(missing))
    dem_1km_candidates = sorted(DEM_ROOT.glob("*1km*.tif"))
    dem_01deg_candidates = sorted(DEM_ROOT.glob("*0p1deg*.tif"))
    if not dem_1km_candidates or not dem_01deg_candidates:
        raise FileNotFoundError(
            "色林错资产需要 1km 与 0p1deg 两套 DEM；当前目录缺少："
            + (" 1km" if not dem_1km_candidates else "")
            + (" 0p1deg" if not dem_01deg_candidates else "")
        )
    full_basin = unary_union(read_shapes(PACKAGE_ROOT / "full_basin_shp" / "basin1.shp"))
    lake_path = next((PACKAGE_ROOT / "lake_shp").glob("*.shp"))
    lake = unary_union(read_shapes(lake_path))

    subbasin_features = []
    subbasin_geometries = []
    subbasin_names = []
    for basin_index, path in enumerate(sorted((PACKAGE_ROOT / "subbasin_shp").glob("*.shp")), start=1):
        geometry = unary_union(read_shapes(path))
        subbasin_geometries.append(geometry)
        subbasin_names.append(SUBBASIN_NAMES.get(path.stem, path.stem))
        subbasin_features.append(
            polygon_feature(
                geometry,
                {"id": path.stem, "name": SUBBASIN_NAMES.get(path.stem, path.stem), "kind": "inflow", "basinIndex": basin_index},
            )
        )

    inflow_domain = unary_union(subbasin_geometries)
    model_domain = unary_union([inflow_domain, lake])
    outlets = []
    for name, geometry in zip(subbasin_names, subbasin_geometries):
        _, lake_point = nearest_points(geometry, lake)
        outlets.append({"name": name.replace("入湖流域", "入湖口"), "coordinate": [round(lake_point.x, 6), round(lake_point.y, 6)]})
    glaciers = read_shapes(PACKAGE_ROOT / "glacier" / "glacier_in_basin.shp")
    glacier_features = [
        polygon_feature(glacier, {"id": f"GLC-{index + 1:03d}", "kind": "glacier"}, tolerance=0.001)
        for index, glacier in enumerate(glaciers)
    ]

    bounds = tuple(round(value, 6) for value in full_basin.bounds)
    center = [round((bounds[0] + bounds[2]) / 2, 6), round((bounds[1] + bounds[3]) / 2, 6)]
    display_bounds = (
        max(73.5, bounds[0] - 1.85),
        max(25.99, bounds[1] - 0.4),
        min(104.42, bounds[2] + 1.85),
        min(39.99, bounds[3] + 0.4),
    )
    write_json(GENERATED_ROOT / "basin.json", polygon_feature(model_domain, {"name": "色林错入湖流域群", "kind": "inflow-basin-group"}, tolerance=0.0015))
    write_json(GENERATED_ROOT / "subbasins.json", feature_collection(subbasin_features))
    write_json(GENERATED_ROOT / "lake.json", polygon_feature(lake, {"name": "色林错", "kind": "lake"}, tolerance=0.0008))
    write_json(GENERATED_ROOT / "glaciers.json", feature_collection(glacier_features))
    outside_mask = box(*display_bounds).difference(model_domain)
    write_json(GENERATED_ROOT / "outside-mask.json", polygon_feature(outside_mask, {"kind": "outside-mask"}, tolerance=0.0015))

    grid_1km = grid_lines(inflow_domain, bounds, 0.01)
    grid_01deg = grid_lines(inflow_domain, bounds, 0.1)
    write_json(PUBLIC_ROOT / "grid-1km.geojson", polygon_feature(grid_1km, {"scale": "1km", "step": 0.01}, tolerance=0.0003))
    write_json(PUBLIC_ROOT / "grid-0p1deg.geojson", polygon_feature(grid_01deg, {"scale": "0p1deg", "step": 0.1}, tolerance=0.001))
    cells_1km = distributed_cells(inflow_domain, bounds, 0.01, "1km")
    cells_01deg = distributed_cells(inflow_domain, bounds, 0.1, "0p1deg")
    write_json(PUBLIC_ROOT / "cells-1km.json", cells_1km)
    write_json(PUBLIC_ROOT / "cells-0p1deg.json", cells_01deg)

    dem_1km = next(path for path in DEM_ROOT.glob("*.tif") if "1km" in path.name)
    dem_01deg = next(path for path in DEM_ROOT.glob("*.tif") if "0p1deg" in path.name)
    rivers, hydrology = provided_river_network()
    write_json(PUBLIC_ROOT / "rivers.geojson", rivers)
    write_json(GENERATED_ROOT / "rivers.json", rivers)
    terrain_1km = terrain_rgba(dem_1km, display_bounds, PUBLIC_ROOT / "dem-1km.png", model_domain)
    terrain_01deg = terrain_rgba(dem_01deg, display_bounds, PUBLIC_ROOT / "dem-0p1deg.png", model_domain)
    forcing = generate_precip_nc(inflow_domain, bounds)

    # The public forcing catalog resolves ./filename against its own directory.
    # Bundled JS metadata instead needs paths relative to the frontend app base.
    metadata_forcing = {
        scale: {
            **product,
            "file": (PUBLIC_ROOT / "forcing" / product["name"]).relative_to(APP_ROOT / "public").as_posix(),
        }
        for scale, product in forcing.items()
    }

    metadata = {
        "id": "selinco",
        "name": "色林错入湖流域群",
        "bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        "displayBounds": [[display_bounds[0], display_bounds[1]], [display_bounds[2], display_bounds[3]]],
        "imageCoordinates": [
            [display_bounds[0], display_bounds[3]],
            [display_bounds[2], display_bounds[3]],
            [display_bounds[2], display_bounds[1]],
            [display_bounds[0], display_bounds[1]],
        ],
        "center": center,
        "zoom": 6.55,
        "subbasinCount": len(subbasin_features),
        "outlets": outlets,
        "glacierCount": len(glacier_features),
        "glacierAreaKm2": round(glacier_area_km2(glaciers), 1),
        "hydrology": hydrology,
        "forcing": metadata_forcing,
        "scales": {
            "1km": {"label": "1 km", "step": 0.01, "cellCount": len(cells_1km), "terrain": terrain_1km},
            "0p1deg": {"label": "0.1°", "step": 0.1, "cellCount": len(cells_01deg), "terrain": terrain_01deg},
        },
    }
    write_json(GENERATED_ROOT / "metadata.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
