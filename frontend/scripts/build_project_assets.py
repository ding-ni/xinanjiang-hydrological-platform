"""将两个实际案例的 Shapefile/Arc Grid 元数据转换为浏览器轻量空间目录。"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image
from rasterio.features import geometry_mask
from rasterio.mask import mask
from rasterio.transform import array_bounds, from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PUBLIC_PROJECTS = Path(__file__).resolve().parents[1] / "public" / "data" / "projects"


CASES = {
    "nananxi-xufan": {
        "name": "南安溪-徐畈",
        "folder": "南安溪-徐畈案例",
        "basin": Path("01_空间资料/流域边界/南安溪流域边界.shp"),
        "rivers": Path("01_空间资料/河网/河流.shp"),
        "stations": Path("01_空间资料/水文雨量站/南安溪水文雨量站点.shp"),
        "sections": None,
        "dem": Path("01_空间资料/DEM/nan_an_dem"),
    },
    "llpq": {
        "name": "里龙普曲",
        "folder": "里龙普曲案例",
        "basin": Path("llpq范围/llpq范围.shp"),
        "rivers": Path("llpq河流/llpq河流.shp"),
        "stations": None,
        "sections": Path("llpq入雅口/llpq入雅口.shp"),
        "dem": Path("llpqdem/llpqdem"),
    },
}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def read_geojson(path: Path) -> dict:
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise RuntimeError(f"空间文件缺少坐标系：{path}")
    frame = frame.to_crs("EPSG:4326")
    return json.loads(frame.to_json(drop_id=True))


def empty_collection() -> dict:
    return {"type": "FeatureCollection", "features": []}


def first_property(properties: dict, *keys: str, default=None):
    for key in keys:
        value = properties.get(key)
        if value not in (None, ""):
            return value
    return default


def station_catalog(features: list[dict], case_id: str) -> list[dict]:
    rows = []
    for index, feature in enumerate(features):
        properties = feature.get("properties") or {}
        coordinates = feature.get("geometry", {}).get("coordinates") or [None, None]
        station_type = str(first_property(properties, "站别", "type", default="站点"))
        if "雨量" in station_type:
            type_id = "rain"
        elif "水文" in station_type or "流量" in station_type:
            type_id = "section"
        elif "水位" in station_type:
            type_id = "water"
        else:
            type_id = "other"
        station_code = str(first_property(properties, "测站编", "code", "O_Name", default=f"{case_id}-{index + 1:03d}"))
        registered_area = first_property(properties, "集水面")
        rows.append({
            "id": station_code,
            "code": station_code,
            "name": str(first_property(properties, "测站名", "name", "O_Name", default=f"站点 {index + 1}")),
            "type": type_id,
            "typeLabel": station_type,
            "lng": coordinates[0],
            "lat": coordinates[1],
            "river": first_property(properties, "河流", "流入何"),
            "registeredCatchmentAreaKm2": float(registered_area) if registered_area not in (None, "") else None,
            "source": "已核实空间资料",
            "sourceId": "verified-spatial-source",
            "sourceProperties": properties,
        })
    return rows


def portable_source_reference(path: Path, project_root: Path = PROJECT_ROOT) -> str:
    """Return a machine-independent provenance reference for generated JSON."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return (Path("external-inputs") / path.name).as_posix()


def relative_resource_url(target: Path, base_dir: Path) -> str:
    """Return a POSIX relative URL from one generated JSON file to a resource."""
    relative = os.path.relpath(target, base_dir).replace(os.sep, "/")
    return relative if relative.startswith(".") else f"./{relative}"


def dem_metadata(path: Path) -> dict:
    with rasterio.open(path) as dataset:
        band = dataset.read(1, masked=True)
        valid = band.compressed()
        return {
            "source": path.name,
            "crs": str(dataset.crs),
            "width": dataset.width,
            "height": dataset.height,
            "bounds": list(dataset.bounds),
            "elevationMin": round(float(valid.min()), 2) if valid.size else None,
            "elevationMax": round(float(valid.max()), 2) if valid.size else None,
            "status": "source-metadata-only",
        }


def terrain_rgba(data: np.ndarray, inside_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    valid = np.isfinite(data)
    values = data[valid]
    if not values.size:
        raise RuntimeError("裁剪后的 DEM 没有有效高程")
    low, high = np.percentile(values, [2, 98])
    if high <= low:
        high = low + 1
    normalized = np.clip((data - low) / (high - low), 0, 1)

    stops = np.array([0.0, 0.22, 0.48, 0.72, 1.0])
    colors = np.array([
        [76, 125, 105],
        [132, 158, 113],
        [177, 166, 119],
        [151, 132, 111],
        [225, 226, 220],
    ], dtype=float)
    rgb = np.zeros((*data.shape, 3), dtype=float)
    for channel in range(3):
        rgb[..., channel] = np.interp(normalized, stops, colors[:, channel])

    filled = np.where(valid, data, np.nanmedian(values))
    grad_y, grad_x = np.gradient(filled)
    slope = np.pi / 2.0 - np.arctan(np.hypot(grad_x, grad_y))
    aspect = np.arctan2(-grad_x, grad_y)
    azimuth = np.deg2rad(315)
    altitude = np.deg2rad(45)
    shade = np.sin(altitude) * np.sin(slope) + np.cos(altitude) * np.cos(slope) * np.cos(azimuth - aspect)
    shade = np.clip((shade + 1) / 2, 0.28, 1.0)
    rgb *= (0.72 + shade[..., None] * 0.38)

    alpha = np.where(valid & inside_mask, 242, 0).astype(np.uint8)
    rgba = np.dstack([np.nan_to_num(np.clip(rgb, 0, 255), nan=0).astype(np.uint8), alpha])
    basin_values = data[valid & inside_mask]
    return rgba, {
        "elevationMin": round(float(values.min()), 2),
        "elevationMax": round(float(values.max()), 2),
        "basinElevationMin": round(float(basin_values.min()), 2) if basin_values.size else None,
        "basinElevationMax": round(float(basin_values.max()), 2) if basin_values.size else None,
        "displayMin": round(float(low), 2),
        "displayMax": round(float(high), 2),
    }


def reproject_array(source: np.ndarray, source_transform, source_crs, destination_resolution: float | None = None):
    height, width = source.shape
    left, bottom, right, top = array_bounds(height, width, source_transform)
    if destination_resolution is None:
        transform, out_width, out_height = calculate_default_transform(
            source_crs, "EPSG:4326", width, height, left, bottom, right, top
        )
    else:
        from rasterio.warp import transform_bounds
        west, south, east, north = transform_bounds(source_crs, "EPSG:4326", left, bottom, right, top, densify_pts=21)
        west = math.floor(west / destination_resolution) * destination_resolution
        south = math.floor(south / destination_resolution) * destination_resolution
        east = math.ceil(east / destination_resolution) * destination_resolution
        north = math.ceil(north / destination_resolution) * destination_resolution
        out_width = max(1, int(round((east - west) / destination_resolution)))
        out_height = max(1, int(round((north - south) / destination_resolution)))
        transform = from_origin(west, north, destination_resolution, destination_resolution)

    destination = np.full((out_height, out_width), np.nan, dtype=np.float32)
    reproject(
        source=source,
        destination=destination,
        src_transform=source_transform,
        src_crs=source_crs,
        src_nodata=np.nan,
        dst_transform=transform,
        dst_crs="EPSG:4326",
        dst_nodata=np.nan,
        resampling=Resampling.average if destination_resolution else Resampling.nearest,
    )
    return destination, transform


def write_dem_geotiff(path: Path, data: np.ndarray, transform) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999.0,
        compress="deflate",
    ) as target:
        target.write(np.where(np.isfinite(data), data, -9999.0).astype(np.float32), 1)


def write_dem_grid(path: Path, data: np.ndarray, transform, inside_mask: np.ndarray) -> int:
    features = []
    for row, col in np.argwhere(np.isfinite(data) & inside_mask):
        west = transform.c + col * transform.a
        east = west + transform.a
        north = transform.f + row * transform.e
        south = north + transform.e
        features.append({
            "type": "Feature",
            "properties": {"row": int(row), "col": int(col), "elevation": round(float(data[row, col]), 2)},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [west, north], [east, north], [east, south], [west, south], [west, north]
                ]],
            },
        })
    write_json(path, {"type": "FeatureCollection", "features": features})
    return len(features)


def build_terrain(case_id: str, basin_frame: gpd.GeoDataFrame, target: Path, china_dem_1km: Path) -> dict:
    if not china_dem_1km.exists():
        raise FileNotFoundError(f"全国 1 km DEM 不存在：{china_dem_1km}")
    with rasterio.open(china_dem_1km) as source:
        basin_albers = basin_frame.to_crs(source.crs)
        buffer_distance = 10000 if case_id == "nananxi-xufan" else 16000
        buffered = basin_albers.geometry.unary_union.buffer(buffer_distance)
        clipped, clipped_transform = mask(source, [buffered.__geo_interface__], crop=True, filled=False)
        clipped_data = clipped[0].astype(np.float32).filled(np.nan)

        one_km, one_km_transform = reproject_array(clipped_data, clipped_transform, source.crs)
        coarse, coarse_transform = reproject_array(clipped_data, clipped_transform, source.crs, destination_resolution=0.1)

    basin_geometry = [geom.__geo_interface__ for geom in basin_frame.geometry]
    outputs = {}
    for resolution, data, transform in (
        ("1km", one_km, one_km_transform),
        ("0p1deg", coarse, coarse_transform),
    ):
        inside = geometry_mask(basin_geometry, out_shape=data.shape, transform=transform, invert=True, all_touched=True)
        rgba, stats = terrain_rgba(data, inside)
        output = target / f"terrain-{resolution}.png"
        image = Image.fromarray(rgba)
        if rgba.shape[1] < 100:
            image = image.resize((rgba.shape[1] * 10, rgba.shape[0] * 10), Image.Resampling.NEAREST)
        image.save(output, optimize=True)
        raster_output = target / f"terrain-{resolution}.tif"
        grid_output = target / f"terrain-grid-{resolution}.geojson"
        clipped_to_basin = np.where(inside, data, np.nan)
        write_dem_geotiff(raster_output, clipped_to_basin, transform)
        grid_cell_count = write_dem_grid(grid_output, data, transform, inside)
        left, bottom, right, top = array_bounds(data.shape[0], data.shape[1], transform)
        outputs[resolution] = {
            "url": relative_resource_url(output, target),
            "rasterUrl": relative_resource_url(raster_output, target),
            "gridUrl": relative_resource_url(grid_output, target),
            "coordinates": [[left, top], [right, top], [right, bottom], [left, bottom]],
            "width": data.shape[1],
            "height": data.shape[0],
            "cellCount": grid_cell_count,
            "sourceResolution": "1000 m Albers",
            "displayResolution": "约 1 km" if resolution == "1km" else "0.1°（由裁剪后 1 km DEM 平均聚合）",
            **stats,
        }
    return outputs


def build_case(case_id: str, definition: dict, case_root: Path, china_dem_1km: Path) -> None:
    root = case_root / definition["folder"]
    target = PUBLIC_PROJECTS / case_id / "spatial"
    basin = read_geojson(root / definition["basin"])
    rivers = read_geojson(root / definition["rivers"])
    stations = read_geojson(root / definition["stations"]) if definition["stations"] else empty_collection()
    sections = read_geojson(root / definition["sections"]) if definition["sections"] else empty_collection()

    basin_frame = gpd.read_file(root / definition["basin"]).to_crs("EPSG:4326")
    basin_area_km2 = round(float(basin_frame.to_crs("EPSG:6933").geometry.area.sum() / 1_000_000), 2)

    stations_rows = station_catalog(stations["features"], case_id)
    sections_rows = station_catalog(sections["features"], case_id)
    if case_id == "nananxi-xufan":
        sections_rows = [row for row in stations_rows if row["id"] == "70115800"]
    for row in sections_rows:
        row["type"] = "section"
        row["typeLabel"] = "出口控制断面"
        row["projectBasinAreaKm2"] = basin_area_km2
        if case_id == "llpq":
            row["name"] = "里龙普曲入雅江口"

    write_json(target / "basin.geojson", basin)
    write_json(target / "rivers.geojson", rivers)
    write_json(target / "stations.geojson", stations)
    write_json(target / "sections.geojson", sections)
    write_json(target / "stations.json", {"stations": stations_rows})
    write_json(target / "sections.json", {"sections": sections_rows})

    terrain = build_terrain(case_id, basin_frame, target, china_dem_1km)
    bounds = [round(float(value), 6) for value in basin_frame.total_bounds]
    metadata = {
        "id": case_id,
        "name": definition["name"],
        "bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        "center": [round((bounds[0] + bounds[2]) / 2, 6), round((bounds[1] + bounds[3]) / 2, 6)],
        "basinAreaKm2": basin_area_km2,
        "featureCounts": {
            "basin": len(basin["features"]),
            "rivers": len(rivers["features"]),
            "stations": len(stations["features"]),
            "sections": len(sections_rows),
        },
        "dem": dem_metadata(root / definition["dem"]),
        "terrainSource": portable_source_reference(china_dem_1km),
        "terrain": terrain,
        "generatedFrom": portable_source_reference(root),
    }
    write_json(target / "metadata.json", metadata)

    for resolution in ("1km", "0p1deg"):
        resolution_root = PUBLIC_PROJECTS / case_id / resolution
        resolution_terrain = {
            **terrain[resolution],
            "url": relative_resource_url(target / f"terrain-{resolution}.png", resolution_root),
            "rasterUrl": relative_resource_url(target / f"terrain-{resolution}.tif", resolution_root),
            "gridUrl": relative_resource_url(target / f"terrain-grid-{resolution}.geojson", resolution_root),
        }
        write_json(resolution_root / "spatial.json", {
            "status": "available",
            "metadata": relative_resource_url(target / "metadata.json", resolution_root),
            "basin": relative_resource_url(target / "basin.geojson", resolution_root),
            "rivers": relative_resource_url(target / "rivers.geojson", resolution_root),
            "stations": relative_resource_url(target / "stations.geojson", resolution_root),
            "sections": relative_resource_url(target / "sections.geojson", resolution_root),
            "stationCatalog": relative_resource_url(target / "stations.json", resolution_root),
            "sectionCatalog": relative_resource_url(target / "sections.json", resolution_root),
            "terrain": resolution_terrain,
            "resolution": resolution,
            "note": "边界、河网、站点和全国 1 km DEM 裁剪预览来自已核实资料；0.1°地形由裁剪后的 1 km DEM 平均聚合。",
        })

    print(f"{case_id}: {metadata['featureCounts']} bounds={metadata['bounds']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build portable browser spatial assets for the configured XAJ cases.")
    parser.add_argument(
        "--cases-root",
        type=Path,
        default=Path(os.environ.get("XAJ_CASES_ROOT", PROJECT_ROOT / "实际案例")),
        help="Directory containing 南安溪-徐畈案例 and 里龙普曲案例 (default: project-relative 实际案例).",
    )
    parser.add_argument(
        "--china-dem-1km",
        type=Path,
        default=Path(os.environ["XAJ_CHINA_DEM_1KM"]) if os.environ.get("XAJ_CHINA_DEM_1KM") else None,
        help="Optional national 1 km DEM; may also be set with XAJ_CHINA_DEM_1KM.",
    )
    args = parser.parse_args()
    cases_root = args.cases_root.expanduser().resolve()
    china_dem_1km = args.china_dem_1km.expanduser().resolve() if args.china_dem_1km else None

    if not cases_root.is_dir():
        raise FileNotFoundError(f"案例资料目录不存在：{cases_root}。请使用 --cases-root 或 XAJ_CASES_ROOT 指定。")
    missing_cases = [definition["folder"] for definition in CASES.values() if not (cases_root / definition["folder"]).is_dir()]
    if missing_cases:
        raise FileNotFoundError(f"案例资料目录缺少：{', '.join(missing_cases)}；当前目录：{cases_root}")
    if china_dem_1km is None:
        raise FileNotFoundError("未指定全国 1 km DEM。请使用 --china-dem-1km 或设置 XAJ_CHINA_DEM_1KM。")
    if not china_dem_1km.is_file():
        raise FileNotFoundError(f"全国 1 km DEM 不存在：{china_dem_1km}")

    for case_id, definition in CASES.items():
        build_case(case_id, definition, cases_root, china_dem_1km)


if __name__ == "__main__":
    main()
