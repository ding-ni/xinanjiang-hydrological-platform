"""Build versioned Nan'anxi model-input data, not an EXE or application installer.

ZIP creation is optional and requires the explicit --create-zip flag.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import numpy as np
import openpyxl
import rasterio
import xlsxwriter
import xarray as xr
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform


FILL_VALUE = np.float32(-9999.0)
TIME_UNITS = "hours since 1970-01-01 00:00:00"
PACKAGE_VERSION = "2026.08.03.1"

CHINESE_PATHS = {
    "station": Path("降雨资料") / "站点逐小时降雨",
    "1km": Path("降雨资料") / "1公里逐小时降雨场",
    "0p1deg": Path("降雨资料") / "0.1度逐小时降雨场",
    "outlet": Path("出口水文资料"),
    "spatial": Path("空间资料"),
    "events": Path("洪水场次"),
    "parameters": Path("参数资料") / "旧模型参数源文件（保持原名）",
    "metadata": Path("资料说明"),
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_public_catalog_resource(resource_url: str, catalog_path: Path, public_root: Path) -> Path:
    """Resolve a catalog reference locally without escaping the configured public tree.

    Current references are relative to the declaring catalog. Historical /data/...
    references remain supported, but network URLs and machine-specific paths do not.
    """
    if not isinstance(resource_url, str) or not resource_url or resource_url != resource_url.strip():
        raise ValueError("Catalog resource must be a non-empty, whitespace-free URL reference")
    if any(ord(character) < 32 or ord(character) == 127 for character in resource_url):
        raise ValueError("Control characters are not allowed in catalog resource references")
    parsed = urlsplit(resource_url)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Catalog resources must be local paths without schemes, hosts, queries or fragments")
    resource_path = unquote(parsed.path, encoding="utf-8", errors="strict")
    if not resource_path or any(character in resource_path for character in "\\:%?#"):
        raise ValueError("Catalog resources cannot contain drive paths, backslashes or ambiguous URL characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in resource_path):
        raise ValueError("Encoded control characters are not allowed in catalog resource references")

    resolved_public_root = public_root.resolve()
    resolved_catalog_path = catalog_path.resolve()
    try:
        resolved_catalog_path.relative_to(resolved_public_root)
    except ValueError as error:
        raise ValueError("The declaring catalog must stay inside the configured public directory") from error
    if resource_path.startswith("/data/"):
        target = resolved_public_root / resource_path.lstrip("/")
    elif resource_path.startswith("/"):
        raise ValueError("Only historical /data/... references may use a leading slash")
    else:
        target = resolved_catalog_path.parent / resource_path
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(resolved_public_root)
    except ValueError as error:
        raise ValueError("Catalog resource escapes the configured public directory") from error
    if resolved_target == resolved_public_root:
        raise ValueError("Catalog resource must reference a file, not the public directory")
    return resolved_target


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def year_hours(year: int) -> np.ndarray:
    start = np.datetime64(f"{year:04d}-01-01T00:00", "h")
    end = np.datetime64(f"{year + 1:04d}-01-01T00:00", "h")
    return np.arange(start, end, np.timedelta64(1, "h")).astype("datetime64[ns]")


def load_grid(spatial_root: Path, scale: str):
    tif_path = spatial_root / f"terrain-{scale}.tif"
    grid = read_json(spatial_root / f"terrain-grid-{scale}.geojson")
    features = {
        (int(feature["properties"]["row"]), int(feature["properties"]["col"])): feature
        for feature in grid["features"]
    }
    valid = set(features)
    rows = [row for row, _ in valid]
    cols = [col for _, col in valid]
    row0, row1, col0, col1 = min(rows), max(rows), min(cols), max(cols)
    with rasterio.open(tif_path) as source:
        elevation = source.read(1)[row0 : row1 + 1, col0 : col1 + 1].astype("float32")
        transform = source.transform
        lon = transform.c + (np.arange(col0, col1 + 1) + 0.5) * transform.a
        lat = transform.f + (np.arange(row0, row1 + 1) + 0.5) * transform.e
        crs_wkt = source.crs.to_wkt()
    mask = np.zeros(elevation.shape, dtype="uint8")
    basin_fraction = np.zeros(elevation.shape, dtype="float32")
    cell_area = np.zeros(elevation.shape, dtype="float64")
    effective_area = np.zeros(elevation.shape, dtype="float64")
    to_projected = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    basin_feature = read_json(spatial_root / "basin.geojson")["features"][0]
    basin_projected = shapely_transform(to_projected.transform, shape(basin_feature["geometry"]))
    projected_cells = {}
    for row, col in valid:
        local = (row - row0, col - col0)
        mask[local] = 1
        cell_projected = shapely_transform(to_projected.transform, shape(features[(row, col)]["geometry"]))
        full_area = float(cell_projected.area)
        inside_area = float(cell_projected.intersection(basin_projected).area)
        projected_cells[local] = cell_projected
        cell_area[local] = full_area
        effective_area[local] = inside_area
        basin_fraction[local] = inside_area / full_area if full_area else 0.0
    elevation[mask == 0] = np.nan
    return {
        "scale": scale,
        "row0": row0,
        "col0": col0,
        "lat": lat.astype("float64"),
        "lon": lon.astype("float64"),
        "elevation": elevation,
        "mask": mask,
        "basin_fraction": basin_fraction,
        "cell_area": cell_area,
        "effective_area": effective_area,
        "basin_projected": basin_projected,
        "projected_cells": projected_cells,
        "effective_area_km2": float(effective_area.sum() / 1_000_000),
        "crs_wkt": crs_wkt,
        "valid": sorted(valid),
    }


def station_metadata(observation_catalog, spatial_stations):
    by_spatial = {str(item["code"]): item for item in spatial_stations["stations"]}
    stations = []
    for item in observation_catalog["rain"]["stations"]:
        if not item.get("records") or not item.get("spatialCode"):
            continue
        spatial = by_spatial.get(str(item["spatialCode"]))
        if not spatial:
            raise ValueError(f"Missing coordinates for rain station {item['legacyCode']}")
        stations.append(
            {
                "legacyCode": str(item["legacyCode"]),
                "stationId": str(item["spatialCode"]),
                "name": item["name"],
                "longitude": float(spatial["lng"]),
                "latitude": float(spatial["lat"]),
            }
        )
    return stations


def idw_weights(stations, grid):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    station_x, station_y = transformer.transform(
        [item["longitude"] for item in stations], [item["latitude"] for item in stations]
    )
    valid_local = []
    cell_lon = []
    cell_lat = []
    for row, col in grid["valid"]:
        local_row = row - grid["row0"]
        local_col = col - grid["col0"]
        valid_local.append((local_row, local_col))
        cell_lon.append(grid["lon"][local_col])
        cell_lat.append(grid["lat"][local_row])
    cell_x, cell_y = transformer.transform(cell_lon, cell_lat)
    distances = np.hypot(
        np.asarray(station_x, dtype="float64")[:, None] - np.asarray(cell_x, dtype="float64")[None, :],
        np.asarray(station_y, dtype="float64")[:, None] - np.asarray(cell_y, dtype="float64")[None, :],
    )
    distances = np.maximum(distances, 1.0)
    return distances ** -2, valid_local


def interpolate_idw(station_values: np.ndarray, raw_weights: np.ndarray):
    valid = np.isfinite(station_values) & (station_values >= 0)
    station_count = valid.sum(axis=1).astype("uint8")
    codes = np.zeros(valid.shape[0], dtype="uint16")
    for index in range(valid.shape[1]):
        codes |= valid[:, index].astype("uint16") << index
    output = np.full((valid.shape[0], raw_weights.shape[1]), np.nan, dtype="float32")
    for code in np.unique(codes):
        if code == 0:
            continue
        time_index = np.flatnonzero(codes == code)
        station_index = np.flatnonzero([(code >> index) & 1 for index in range(valid.shape[1])])
        weights = raw_weights[station_index]
        weights = weights / weights.sum(axis=0, keepdims=True)
        output[time_index] = station_values[np.ix_(time_index, station_index)] @ weights
    quality = np.select(
        [station_count == 0, station_count == 1, station_count == 2, station_count >= 3],
        [0, 1, 2, 3],
    ).astype("uint8")
    return output, station_count, quality


def grid_dataset(time, precipitation, grid, attrs):
    dataset = xr.Dataset(
        data_vars={
            "precipitation": (("time", "lat", "lon"), precipitation),
        },
        coords={"time": time, "lat": grid["lat"], "lon": grid["lon"]},
        attrs=attrs,
    )
    dataset["precipitation"].attrs.update(
        standard_name="precipitation_amount",
        long_name="hourly observed precipitation amount",
        units="mm",
        cell_methods="time: sum",
        coordinates="lat lon",
    )
    dataset["lat"].attrs.update(standard_name="latitude", units="degrees_north", axis="Y")
    dataset["lon"].attrs.update(standard_name="longitude", units="degrees_east", axis="X")
    dataset["time"].attrs.update(standard_name="time", axis="T")
    return dataset


def nc_encoding(dataset, spatial_chunks=None):
    encoding = {
        "time": {"dtype": "int64", "units": TIME_UNITS, "calendar": "proleptic_gregorian"},
    }
    if "precipitation" in dataset:
        chunks = (168, *spatial_chunks) if spatial_chunks else None
        encoding["precipitation"] = {
            "dtype": "float32",
            "_FillValue": FILL_VALUE,
            "zlib": True,
            "complevel": 4,
            "shuffle": True,
            **({"chunksizes": chunks} if chunks else {}),
        }
    return encoding


def write_station_nc(path, year, time, values, stations, source_file):
    dataset = xr.Dataset(
        data_vars={
            "precipitation": (("time", "station"), values),
            "station_id": (("station",), np.asarray([item["stationId"] for item in stations], dtype=str)),
            "legacy_code": (("station",), np.asarray([item["legacyCode"] for item in stations], dtype=str)),
            "station_name": (("station",), np.asarray([item["name"] for item in stations], dtype=str)),
            "longitude": (("station",), np.asarray([item["longitude"] for item in stations], dtype="float64")),
            "latitude": (("station",), np.asarray([item["latitude"] for item in stations], dtype="float64")),
        },
        coords={"time": time, "station": np.arange(len(stations), dtype="int16")},
        attrs={
            "Conventions": "CF-1.10",
            "title": f"Nan'anxi hourly observed station precipitation, {year}",
            "source": "xufan.mdb ReFactorDT",
            "source_file": source_file,
            "history": f"created {iso_now()} by build_nananxi_delivery_package.py",
        },
    )
    dataset["precipitation"].attrs.update(standard_name="precipitation_amount", units="mm", cell_methods="time: sum")
    dataset["longitude"].attrs.update(standard_name="longitude", units="degrees_east")
    dataset["latitude"].attrs.update(standard_name="latitude", units="degrees_north")
    encoding = {
        "time": {"dtype": "int64", "units": TIME_UNITS, "calendar": "proleptic_gregorian"},
        "precipitation": {"dtype": "float32", "_FillValue": FILL_VALUE, "zlib": True, "complevel": 4, "shuffle": True, "chunksizes": (168, len(stations))},
    }
    dataset.to_netcdf(path, engine="netcdf4", format="NETCDF4", encoding=encoding)


def aggregate_to_coarse(fine_values, fine_grid, coarse_grid, fine_local):
    coarse = np.full((fine_values.shape[0], *coarse_grid["mask"].shape), np.nan, dtype="float32")
    counts = np.zeros(coarse_grid["mask"].shape, dtype="uint16")
    for coarse_row, coarse_col in coarse_grid["valid"]:
        local_row = coarse_row - coarse_grid["row0"]
        local_col = coarse_col - coarse_grid["col0"]
        west = coarse_grid["lon"][local_col] - 0.05
        east = coarse_grid["lon"][local_col] + 0.05
        south = coarse_grid["lat"][local_row] - 0.05
        north = coarse_grid["lat"][local_row] + 0.05
        selected = []
        areas = []
        coarse_cell = coarse_grid["projected_cells"][(local_row, local_col)]
        for index, (fine_row, fine_col) in enumerate(fine_local):
            overlap = fine_grid["projected_cells"][(fine_row, fine_col)].intersection(coarse_cell).intersection(
                fine_grid["basin_projected"]
            )
            if overlap.area > 0:
                selected.append(index)
                areas.append(float(overlap.area))
        if selected:
            selected_values = fine_values[:, selected]
            weights = np.asarray(areas, dtype="float64")
            valid = np.isfinite(selected_values)
            valid_area = valid @ weights
            totals = np.nansum(selected_values * weights[None, :], axis=1)
            aggregated = np.full(fine_values.shape[0], np.nan, dtype="float32")
            np.divide(totals, valid_area, out=aggregated, where=valid_area > 0)
            coarse[:, local_row, local_col] = aggregated
            counts[local_row, local_col] = len(selected)
    return coarse, counts


def build_precipitation(frontend_root: Path, standard_root: Path, catalog, catalog_path: Path):
    project_root = frontend_root / "app" / "public" / "data" / "projects" / "nananxi-xufan"
    observation_root = project_root / "observations"
    spatial_root = project_root / "spatial"
    stations = station_metadata(catalog, read_json(spatial_root / "stations.json"))
    station_index = {item["legacyCode"]: index for index, item in enumerate(stations)}
    fine_grid = load_grid(spatial_root, "1km")
    coarse_grid = load_grid(spatial_root, "0p1deg")
    weights, fine_local = idw_weights(stations, fine_grid)

    station_out = standard_root / "forcing" / "observed_precipitation" / "station"
    fine_out = standard_root / "forcing" / "observed_precipitation" / "1km"
    coarse_out = standard_root / "forcing" / "observed_precipitation" / "0p1deg"
    for directory in (station_out, fine_out, coarse_out):
        directory.mkdir(parents=True, exist_ok=True)

    summaries = []
    for year_item in catalog["rain"]["years"]:
        year = int(year_item["year"])
        source_path = resolve_public_catalog_resource(year_item["url"], catalog_path, frontend_root / "app" / "public")
        source = read_json(source_path)
        full_time = year_hours(year)
        station_values = np.full((len(full_time), len(stations)), np.nan, dtype="float32")
        observed_hours = []
        for hour, source_station_index, value in source["rows"]:
            legacy_code = str(source["stationCodes"][source_station_index])
            target_station_index = station_index.get(legacy_code)
            if target_station_index is None or value is None or hour < 0 or hour >= len(full_time):
                continue
            observed_hours.append(int(hour))
            station_values[int(hour), target_station_index] = np.float32(value)

        if observed_hours:
            first_hour, last_hour = min(observed_hours), max(observed_hours)
            time = full_time[first_hour : last_hour + 1]
            station_values = station_values[first_hour : last_hour + 1]
        else:
            time = full_time[:0]
            station_values = station_values[:0]

        fine_valid, station_count, quality = interpolate_idw(station_values, weights)
        # 矩形栅格的流域外格点用 0 填充；流域内无任何实测站点的时刻仍保留 NaN。
        fine = np.zeros((len(time), *fine_grid["mask"].shape), dtype="float32")
        for cell_index, (row, col) in enumerate(fine_local):
            fine[:, row, col] = fine_valid[:, cell_index]
        coarse, fine_cell_counts = aggregate_to_coarse(fine_valid, fine_grid, coarse_grid, fine_local)

        common_attrs = {
            "Conventions": "CF-1.10, ACDD-1.3",
            "project_id": "nananxi-xufan",
            "basin_name": "Nan'anxi-Xufan",
            "time_resolution": "1 hour",
            "source_dataset": "XF-OBS-RAIN-HOURLY",
            "source_file": str(source_path),
            "interpolation": "inverse_distance_weighting",
            "idw_power": 2.0,
            "distance_crs": "EPSG:32650",
            "history": f"created {iso_now()} by build_nananxi_delivery_package.py",
            "comment": "Values are observed precipitation fields, not runoff or routing results. Basin-outside cells are 0; no-reporting hours inside the basin remain NaN.",
        }
        fine_dataset = grid_dataset(
            time,
            fine,
            fine_grid,
            {**common_attrs, "title": f"Nan'anxi 1 km hourly observed precipitation field, {year}", "spatial_resolution": "approximately 1 km"},
        )
        coarse_dataset = grid_dataset(
            time,
            coarse,
            coarse_grid,
            {
                **common_attrs,
                "title": f"Nan'anxi 0.1 degree hourly observed precipitation field, {year}",
                "spatial_resolution": "0.1 degree",
                "aggregation": "area-equivalent mean of valid 1 km cell centres",
            },
        )
        station_file = station_out / f"nananxi_station_precip_hourly_{year}.nc"
        fine_file = fine_out / f"nananxi_precip_hourly_1km_{year}.nc"
        coarse_file = coarse_out / f"nananxi_precip_hourly_0p1deg_{year}.nc"
        write_station_nc(station_file, year, time, station_values, stations, str(source_path))
        fine_dataset.to_netcdf(fine_file, engine="netcdf4", format="NETCDF4", encoding=nc_encoding(fine_dataset, fine_grid["mask"].shape))
        coarse_dataset.to_netcdf(coarse_file, engine="netcdf4", format="NETCDF4", encoding=nc_encoding(coarse_dataset, coarse_grid["mask"].shape))
        summaries.append(
            {
                "year": year,
                "hours": len(time),
                "hoursWithRainStations": int(np.count_nonzero(station_count)),
                "minimumReportingStations": int(station_count[station_count > 0].min()) if np.any(station_count > 0) else 0,
                "maximumReportingStations": int(station_count.max()),
                "stationFile": station_file.relative_to(standard_root).as_posix(),
                "grid1kmFile": fine_file.relative_to(standard_root).as_posix(),
                "grid0p1degFile": coarse_file.relative_to(standard_root).as_posix(),
            }
        )
        print(f"precipitation {year}: {summaries[-1]['hoursWithRainStations']} observed hours", flush=True)
    return summaries, fine_grid, coarse_grid, stations


def write_interpolation_validation(standard_root: Path, rain_summaries, stations):
    powers = (1.0, 1.5, 2.0, 2.5, 3.0)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    x, y = transformer.transform(
        [item["longitude"] for item in stations], [item["latitude"] for item in stations]
    )
    distances = np.hypot(
        np.asarray(x)[:, None] - np.asarray(x)[None, :],
        np.asarray(y)[:, None] - np.asarray(y)[None, :],
    )
    np.fill_diagonal(distances, np.inf)

    def empty_stats():
        return np.zeros(8, dtype="float64")

    def add_errors(target, errors, wet_errors):
        target[0] += errors.size
        target[1] += np.abs(errors).sum()
        target[2] += np.square(errors).sum()
        target[3] += errors.sum()
        target[4] += wet_errors.size
        target[5] += np.abs(wet_errors).sum()
        target[6] += np.square(wet_errors).sum()
        target[7] += wet_errors.sum()

    def finish(values):
        n, absolute, squared, bias_sum, wet_n, wet_absolute, wet_squared, wet_bias_sum = values
        return {
            "count": int(n),
            "mae": absolute / n if n else np.nan,
            "rmse": np.sqrt(squared / n) if n else np.nan,
            "bias": bias_sum / n if n else np.nan,
            "wetCount": int(wet_n),
            "wetMae": wet_absolute / wet_n if wet_n else np.nan,
            "wetRmse": np.sqrt(wet_squared / wet_n) if wet_n else np.nan,
            "wetBias": wet_bias_sum / wet_n if wet_n else np.nan,
        }

    def excel_value(value):
        return None if isinstance(value, (float, np.floating)) and not np.isfinite(value) else value

    totals = {power: empty_stats() for power in powers}
    station_totals = [empty_stats() for _ in stations]
    yearly = []
    for summary in rain_summaries:
        with xr.open_dataset(standard_root / summary["stationFile"], engine="netcdf4") as dataset:
            values = dataset["precipitation"].values.astype("float64")
        valid = np.isfinite(values)
        year_stats = empty_stats()
        for power in powers:
            weights = distances ** -power
            for station_index in range(values.shape[1]):
                other_valid = valid.copy()
                other_valid[:, station_index] = False
                denominator = other_valid @ weights[station_index]
                prediction = np.divide(
                    np.nan_to_num(values) @ weights[station_index],
                    denominator,
                    out=np.full(values.shape[0], np.nan),
                    where=denominator > 0,
                )
                usable = valid[:, station_index] & np.isfinite(prediction)
                errors = prediction[usable] - values[usable, station_index]
                wet = usable & ((values[:, station_index] > 0) | (prediction > 0))
                wet_errors = prediction[wet] - values[wet, station_index]
                add_errors(totals[power], errors, wet_errors)
                if power == 2.0:
                    add_errors(year_stats, errors, wet_errors)
                    add_errors(station_totals[station_index], errors, wet_errors)
        yearly.append({"year": summary["year"], **finish(year_stats)})

    comparison = [{"power": power, **finish(totals[power])} for power in powers]
    station_results = [
        {"stationId": station["stationId"], "stationName": station["name"], **finish(station_totals[index])}
        for index, station in enumerate(stations)
    ]
    output = standard_root / "metadata" / "interpolation_validation.xlsx"
    workbook = xlsxwriter.Workbook(output)
    header = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
    number = workbook.add_format({"num_format": "0.0000"})

    method_sheet = workbook.add_worksheet("方法比较")
    method_headers = ["幂指数", "检验记录数", "平均绝对误差", "均方根误差", "平均偏差", "降雨时段记录数", "降雨时段平均绝对误差", "降雨时段均方根误差", "降雨时段平均偏差"]
    method_sheet.write_row(0, 0, method_headers, header)
    for row, item in enumerate(comparison, 1):
        method_sheet.write_row(row, 0, [excel_value(value) for value in [item["power"], item["count"], item["mae"], item["rmse"], item["bias"], item["wetCount"], item["wetMae"], item["wetRmse"], item["wetBias"]]], number)
    method_sheet.write(len(comparison) + 2, 0, "结论", header)
    method_sheet.write(len(comparison) + 2, 1, "幂指数2至3的均方根误差差异很小，保留水文插值常用的幂指数2。")
    method_sheet.set_column("A:I", 20)

    year_sheet = workbook.add_worksheet("逐年检验")
    year_headers = ["年份", "检验记录数", "平均绝对误差", "均方根误差", "平均偏差", "降雨时段记录数", "降雨时段平均绝对误差", "降雨时段均方根误差", "降雨时段平均偏差"]
    year_sheet.write_row(0, 0, year_headers, header)
    for row, item in enumerate(yearly, 1):
        year_sheet.write_row(row, 0, [excel_value(value) for value in [item["year"], item["count"], item["mae"], item["rmse"], item["bias"], item["wetCount"], item["wetMae"], item["wetRmse"], item["wetBias"]]], number)
    year_sheet.set_column("A:I", 20)

    station_sheet = workbook.add_worksheet("逐站检验")
    station_headers = ["站号", "站名", "检验记录数", "平均绝对误差", "均方根误差", "平均偏差", "降雨时段记录数", "降雨时段平均绝对误差", "降雨时段均方根误差", "降雨时段平均偏差"]
    station_sheet.write_row(0, 0, station_headers, header)
    for row, item in enumerate(station_results, 1):
        station_sheet.write_row(row, 0, [excel_value(value) for value in [item["stationId"], item["stationName"], item["count"], item["mae"], item["rmse"], item["bias"], item["wetCount"], item["wetMae"], item["wetRmse"], item["wetBias"]]], number)
    station_sheet.set_column("A:J", 20)
    workbook.close()
    return {"method": "leave-one-station-out", "selectedPower": 2.0, "comparison": comparison}


def build_outlet_hydrology(frontend_root: Path, standard_root: Path, catalog, catalog_path: Path):
    output_root = standard_root / "hydrology" / "outlet_observations"
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / "xufan_outlet_hourly.xlsx"
    summaries = []
    workbook = xlsxwriter.Workbook(output, {"constant_memory": True})
    workbook.set_properties({"title": "徐畈出口逐小时实测水位流量", "author": "南安溪资料整理"})
    info = workbook.add_worksheet("资料说明")
    info.write_column("A1", ["资料名称", "测站", "测站编码", "资料来源", "时间范围", "记录数", "缺测说明"])
    info.write_column(
        "B1",
        [
            "徐畈出口逐小时实测水位流量",
            "徐畈（二）",
            "70115800",
            "xufan.mdb 中的 st_river_r 表",
            f"{catalog['river']['start']} 至 {catalog['river']['end']}",
            catalog["river"]["totalRecords"],
            "空白单元格表示源资料缺测，不得按0处理",
        ],
    )
    info.set_column("A:A", 14)
    info.set_column("B:B", 48)

    sheet = workbook.add_worksheet("逐小时水位流量")
    header_format = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
    time_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    number_format = workbook.add_format({"num_format": "0.000"})
    headers = ["时间", "水位（米）", "流量（立方米每秒）", "资料状态"]
    sheet.write_row(0, 0, headers, header_format)
    row_number = 1
    for year_item in catalog["river"]["years"]:
        year = int(year_item["year"])
        source_path = resolve_public_catalog_resource(year_item["url"], catalog_path, frontend_root / "app" / "public")
        source = read_json(source_path)
        observed_flow = 0
        for hour, level_value, flow_value in source["rows"]:
            observation_time = datetime(year, 1, 1) + timedelta(hours=int(hour))
            sheet.write_datetime(row_number, 0, observation_time, time_format)
            if level_value is not None:
                sheet.write_number(row_number, 1, float(level_value), number_format)
            if flow_value is not None:
                sheet.write_number(row_number, 2, float(flow_value), number_format)
                observed_flow += 1
            if level_value is not None and flow_value is not None:
                status = "完整"
            elif level_value is None and flow_value is None:
                status = "水位、流量缺测"
            elif level_value is None:
                status = "水位缺测"
            else:
                status = "流量缺测"
            sheet.write_string(row_number, 3, status)
            row_number += 1
        summaries.append(
            {
                "year": year,
                "records": len(source["rows"]),
                "observedFlowHours": observed_flow,
                "file": output.relative_to(standard_root).as_posix(),
            }
        )
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, row_number - 1, len(headers) - 1)
    sheet.set_column("A:A", 18)
    sheet.set_column("B:C", 20)
    sheet.set_column("D:D", 18)
    workbook.close()
    return summaries


def copy_supporting_files(frontend_root: Path, case_root: Path, standard_root: Path):
    project_root = frontend_root / "app" / "public" / "data" / "projects" / "nananxi-xufan"
    spatial_out = standard_root / "spatial"
    events_out = standard_root / "events"
    parameters_out = standard_root / "parameters" / "legacy_source"
    metadata_out = standard_root / "metadata"
    for directory in (spatial_out, events_out, parameters_out, metadata_out):
        directory.mkdir(parents=True, exist_ok=True)
    for name in [
        "basin.geojson", "rivers.geojson", "stations.json", "stations.geojson", "sections.json",
        "terrain-1km.tif", "terrain-0p1deg.tif", "terrain-grid-1km.geojson", "terrain-grid-0p1deg.geojson", "metadata.json",
    ]:
        shutil.copy2(project_root / "spatial" / name, spatial_out / name)
    for name in ["flood-events.json", "event-data-packages.json"]:
        shutil.copy2(project_root / "observations" / name, events_out / name)
    shutil.copy2(project_root / "input-preparation.json", metadata_out / "input-preparation.json")
    data_source = case_root / "02_徐畈水文与模型资料" / "data"
    shutil.copytree(data_source, parameters_out / "data", dirs_exist_ok=True)
    shutil.copy2(frontend_root / "app" / "scripts" / "build_nananxi_delivery_package.py", metadata_out / "build_nananxi_delivery_package.py")


def localize_delivery_tree(source_root: Path, target_root: Path, rain_summaries, river_summaries, *, zip_filename: str | None = None):
    target_root.mkdir(parents=True)
    for summary in rain_summaries:
        year = summary["year"]
        mappings = {
            "stationFile": (CHINESE_PATHS["station"] / f"nananxi_station_precip_hourly_{year}.nc"),
            "grid1kmFile": (CHINESE_PATHS["1km"] / f"nananxi_precip_hourly_1km_{year}.nc"),
            "grid0p1degFile": (CHINESE_PATHS["0p1deg"] / f"nananxi_precip_hourly_0p1deg_{year}.nc"),
        }
        for key, relative in mappings.items():
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / summary[key], target)
            summary[key] = relative.as_posix()

    outlet_relative = CHINESE_PATHS["outlet"] / "徐畈出口逐小时水位流量.xlsx"
    outlet_target = target_root / outlet_relative
    outlet_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_root / river_summaries[0]["file"], outlet_target)
    for summary in river_summaries:
        summary["file"] = outlet_relative.as_posix()

    spatial_names = {
        "basin.geojson": "南安溪流域边界.geojson",
        "rivers.geojson": "南安溪河网.geojson",
        "stations.json": "南安溪水文雨量站.json",
        "stations.geojson": "南安溪水文雨量站.geojson",
        "sections.json": "徐畈出口断面.json",
        "terrain-1km.tif": "南安溪1公里DEM.tif",
        "terrain-0p1deg.tif": "南安溪0.1度DEM.tif",
        "terrain-grid-1km.geojson": "南安溪1公里有效格点.geojson",
        "terrain-grid-0p1deg.geojson": "南安溪0.1度有效格点.geojson",
        "metadata.json": "空间资料说明.json",
    }
    for source_name, target_name in spatial_names.items():
        target = target_root / CHINESE_PATHS["spatial"] / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / "spatial" / source_name, target)

    event_names = {
        "flood-events.json": "南安溪实测洪水场次.json",
        "event-data-packages.json": "南安溪洪水场次资料索引.json",
    }
    for source_name, target_name in event_names.items():
        target = target_root / CHINESE_PATHS["events"] / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / "events" / source_name, target)

    shutil.copytree(source_root / "parameters" / "legacy_source" / "data", target_root / CHINESE_PATHS["parameters"])
    metadata_root = target_root / CHINESE_PATHS["metadata"]
    metadata_root.mkdir(parents=True, exist_ok=True)
    preparation = read_json(source_root / "metadata" / "input-preparation.json")
    preparation["deliveryPackage"] = {
        "status": "ready",
        "folder": "南安溪-徐畈数据资料整合",
        "zip": zip_filename,
        "netcdfFileCount": 201,
        "note": "本目录只保留模型输入资料和简明说明，不生成数据库、哈希清单或插值检验表。",
    }
    (metadata_root / "输入准备说明.json").write_text(
        json.dumps(preparation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.copy2(source_root / "metadata" / "build_nananxi_delivery_package.py", metadata_root / "数据资料整合生成脚本.py")


def write_catalog(standard_root: Path, rain_summaries, river_summaries, fine_grid, coarse_grid, stations):
    metadata_root = standard_root / CHINESE_PATHS["metadata"]
    catalog = {
        "schemaVersion": "1.0",
        "createdAt": iso_now(),
        "项目": "南安溪-徐畈",
        "模型": "分布式新安江模型",
        "降雨资料": {
            "时间分辨率": "1小时",
            "年份数": len(rain_summaries),
            "站点数": len(stations),
            "1公里有效格点数": int(fine_grid["mask"].sum()),
            "0.1度有效格点数": int(coarse_grid["mask"].sum()),
            "空间化方法": "逐时采用当时有实测记录的站点，以EPSG:32650米制距离进行IDW插值，幂指数2",
            "缺测处理": "流域内某时次没有任何站点实测时保留NaN；矩形栅格的流域外格点填0；实测无雨保留0",
            "NC变量": "降雨场文件仅含 precipitation、time、lat、lon",
        },
        "出口水文资料": {
            "测站": "徐畈（二）",
            "站号": "70115800",
            "格式": "Excel",
            "文件": river_summaries[0]["file"],
            "说明": "水位和流量放在同一个工作表中，空白表示源资料缺测",
        },
        "尚缺资料": ["连续蒸发或潜在蒸散发资料", "预报降雨", "正式初始状态"],
    }
    (metadata_root / "数据说明.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = """# 南安溪-徐畈数据资料整合

本资料包用于南安溪—徐畈分布式新安江模型的数据准备。目录只保留可直接使用或需要继续确认的资料，不包含数据库、哈希清单、插值检验表和产汇流程序。

## 主要内容

- `降雨资料/站点逐小时降雨`：站点逐小时实测降雨，按年保存为 NC；
- `降雨资料/1公里逐小时降雨场`：模型主尺度逐小时降雨场；
- `降雨资料/0.1度逐小时降雨场`：由 1 公里有效格点按面积聚合，用于尺度对照；
- `出口水文资料/徐畈出口逐小时水位流量.xlsx`：水位和流量合并保存；
- `空间资料`：流域边界、河网、站点、出口、DEM和有效格点；
- `洪水场次`：已登记洪水场次资料；
- `参数资料`：旧模型参数源文件；
- `资料说明`：简明数据说明和生成脚本。

## 降雨处理

1 公里降雨采用反距离权重法，幂指数为 2，距离在米制投影下计算。每个时次只使用该时次确有实测记录的站点，这样不会把未建站、停测或缺报站点错误地带入插值。早期只有一个站的年份只能表示单站降雨向流域扩展，空间代表性有限。

降雨为 0 表示该站或格点在该小时无雨，应保留。流域内所有站均无记录时保留 NaN，不能伪造为 0。为便于普通软件查看，矩形栅格中流域外格点写为 0；有效计算范围以空间资料中的流域边界和有效格点为准。每个年度NC只保留实际资料首小时到末小时，不再补全年空白时段。

降雨场 NC 仅含 `precipitation`、`time`、`lat`、`lon`。空间边界、DEM和有效格点单独存放，不重复写入每一个逐年 NC。

## 尚缺资料

原资料中的日蒸发表为空，因此目前尚缺连续蒸发或潜在蒸散发资料。本包也不包含预报降雨、正式初始状态和产汇流程序。
"""
    (standard_root.parent / "使用说明.md").write_text(readme, encoding="utf-8")
    return catalog


def build_sqlite_and_manifest(package_root: Path, standard_root: Path, catalog):
    metadata_root = standard_root / CHINESE_PATHS["metadata"]
    database = metadata_root / "数据资料索引.sqlite"
    if database.exists():
        database.unlink()
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE package_versions(version TEXT PRIMARY KEY, created_at TEXT NOT NULL, project_id TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY, format TEXT NOT NULL, path TEXT NOT NULL, status TEXT NOT NULL, description TEXT NOT NULL);
            CREATE TABLE missing_inputs(input_id TEXT PRIMARY KEY, status TEXT NOT NULL, note TEXT NOT NULL);
            CREATE TABLE files(path TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, role TEXT NOT NULL);
            """
        )
        connection.execute("INSERT INTO package_versions VALUES (?,?,?,?)", (PACKAGE_VERSION, catalog["createdAt"], "nananxi-xufan", "complete-with-declared-gaps"))
        datasets = [
            ("XF-OBS-RAIN-STATION-NC", "NetCDF-4", CHINESE_PATHS["station"].as_posix(), "ready", "逐小时站点实测降雨"),
            ("XF-OBS-RAIN-1KM-NC", "NetCDF-4/CF-1.10", CHINESE_PATHS["1km"].as_posix(), "ready", "逐小时1公里实测降雨场"),
            ("XF-OBS-RAIN-0P1DEG-NC", "NetCDF-4/CF-1.10", CHINESE_PATHS["0p1deg"].as_posix(), "ready", "逐小时0.1度实测降雨场"),
            ("XF-OBS-OUTLET-XLSX", "Excel", (CHINESE_PATHS["outlet"] / "徐畈出口逐小时水位流量.xlsx").as_posix(), "ready", "徐畈出口逐小时实测水位和流量"),
            ("XF-SPATIAL", "GeoTIFF/GeoJSON", CHINESE_PATHS["spatial"].as_posix(), "ready", "数字高程、流域、格点、河网、站点和出口"),
            ("XF-FLOOD-EVENTS", "JSON", CHINESE_PATHS["events"].as_posix(), "ready", "已登记洪水场次及逐场资料摘要"),
            ("XF-LEGACY-PARAMETERS", "legacy text", CHINESE_PATHS["parameters"].as_posix(), "source-found", "保持原名的旧模型参数源文件"),
        ]
        connection.executemany("INSERT INTO datasets VALUES (?,?,?,?,?)", datasets)
        missing = [
            ("evaporation", "missing", "xufan.mdb day_evap is empty"),
            ("forecast_precipitation", "missing", "No future hourly precipitation product supplied"),
            ("initial_state", "missing", "Must be generated by model continuation"),
            ("model_service", "not-included", "Runoff and routing are backend responsibilities"),
        ]
        connection.executemany("INSERT INTO missing_inputs VALUES (?,?,?)", missing)
        connection.commit()

        indexed = []
        for file in sorted(package_root.rglob("*")):
            if not file.is_file() or file in {database, metadata_root / "文件校验清单.csv"}:
                continue
            relative = file.relative_to(package_root).as_posix()
            role = "original-case" if relative.startswith("01_原始资料备份（保持原名）/") else "standardized-input"
            indexed.append((relative, file.stat().st_size, sha256(file), role))
        connection.executemany("INSERT INTO files VALUES (?,?,?,?)", indexed)
        connection.commit()
    finally:
        connection.close()

    manifest_rows = []
    for file in sorted(package_root.rglob("*")):
        if file.is_file() and file.name != "文件校验清单.csv":
            manifest_rows.append((file.relative_to(package_root).as_posix(), file.stat().st_size, sha256(file)))
    manifest = metadata_root / "文件校验清单.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["path", "size_bytes", "sha256"])
        writer.writerows(manifest_rows)
    return manifest_rows


def validate_outputs(standard_root: Path, rain_summaries, river_summaries, fine_grid, coarse_grid):
    if len(rain_summaries) != 67 or len(river_summaries) != 61:
        raise ValueError("Unexpected year-file count")
    sample_years = [1959, 1964, 2024, 2025]
    for year in sample_years:
        path = standard_root / "forcing" / "observed_precipitation" / "1km" / f"nananxi_precip_hourly_1km_{year}.nc"
        with xr.open_dataset(path, engine="netcdf4") as dataset:
            if dataset.sizes["lat"] != fine_grid["mask"].shape[0] or dataset.sizes["lon"] != fine_grid["mask"].shape[1]:
                raise ValueError(f"1 km dimensions failed for {year}")
            if set(dataset.data_vars) != {"precipitation"}:
                raise ValueError(f"1 km variables are not simplified for {year}")
            if float(dataset["precipitation"].max(skipna=True)) < 0:
                raise ValueError(f"1 km precipitation failed for {year}")
        path = standard_root / "forcing" / "observed_precipitation" / "0p1deg" / f"nananxi_precip_hourly_0p1deg_{year}.nc"
        with xr.open_dataset(path, engine="netcdf4") as dataset:
            if set(dataset.data_vars) != {"precipitation"}:
                raise ValueError(f"0.1 degree variables are not simplified for {year}")
    outlet = standard_root / "hydrology" / "outlet_observations" / "xufan_outlet_hourly.xlsx"
    workbook = openpyxl.load_workbook(outlet, read_only=True, data_only=True)
    try:
        sheet = workbook["逐小时水位流量"]
        if sheet.max_row != 515_423:
            raise ValueError(f"Outlet observation row count failed: {sheet.max_row}")
    finally:
        workbook.close()


def make_zip(package_root: Path):
    zip_path = package_root.with_suffix(".zip")
    portable_root = "南安溪-徐畈数据资料整合"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for file in sorted(package_root.rglob("*")):
            if file.is_file():
                archive.write(file, (Path(portable_root) / file.relative_to(package_root)).as_posix())
    return zip_path


def main():
    parser = argparse.ArgumentParser(description="Build model-input data; this is not an EXE or application installer.")
    parser.add_argument("--frontend-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    zip_options = parser.add_mutually_exclusive_group()
    zip_options.add_argument("--create-zip", action="store_true", help="Explicitly create an optional data ZIP after conversion (not an installer).")
    zip_options.add_argument("--skip-zip", action="store_true", help="Compatibility option: do not create a ZIP; this is already the default.")
    args = parser.parse_args()

    frontend_root = args.frontend_root.resolve()
    case_root = args.case_root.resolve()
    package_root = args.output_root.resolve()
    if package_root.exists():
        raise FileExistsError(f"Output already exists: {package_root}")
    if not (case_root / "02_徐畈水文与模型资料" / "xufan.mdb").is_file():
        raise FileNotFoundError("xufan.mdb not found under case root")

    original_root = package_root / "01_原始资料备份（保持原名）"
    standard_root = package_root / "02_模型输入资料"
    package_root.mkdir(parents=True)
    shutil.copytree(case_root, original_root)

    project_root = frontend_root / "app" / "public" / "data" / "projects" / "nananxi-xufan"
    observation_catalog_path = project_root / "observations" / "catalog.json"
    observation_catalog = read_json(observation_catalog_path)
    with tempfile.TemporaryDirectory(prefix="xaj_nananxi_") as temporary:
        staging_root = Path(temporary)
        staging_standard = staging_root / "standardized"
        localized_standard = staging_root / "localized"
        staging_standard.mkdir(parents=True)
        copy_supporting_files(frontend_root, case_root, staging_standard)
        rain_summaries, fine_grid, coarse_grid, stations = build_precipitation(frontend_root, staging_standard, observation_catalog, observation_catalog_path)
        river_summaries = build_outlet_hydrology(frontend_root, staging_standard, observation_catalog, observation_catalog_path)
        validate_outputs(staging_standard, rain_summaries, river_summaries, fine_grid, coarse_grid)
        localize_delivery_tree(
            staging_standard,
            localized_standard,
            rain_summaries,
            river_summaries,
            zip_filename=package_root.with_suffix(".zip").name if args.create_zip else None,
        )
        catalog = write_catalog(localized_standard, rain_summaries, river_summaries, fine_grid, coarse_grid, stations)
        shutil.copytree(localized_standard, standard_root)
        shutil.copy2(staging_root / "使用说明.md", package_root / "使用说明.md")
    manifest_rows = [file for file in package_root.rglob("*") if file.is_file()]
    zip_path = make_zip(package_root) if args.create_zip else None
    result = {
        "packageRoot": str(package_root),
        "zipPath": str(zip_path) if zip_path else None,
        "packageFiles": len(manifest_rows),
        "packageBytes": sum(file.stat().st_size for file in package_root.rglob("*") if file.is_file()),
        "rainYears": len(rain_summaries),
        "riverYears": len(river_summaries),
        "validCells1km": int(fine_grid["mask"].sum()),
        "validCells0p1deg": int(coarse_grid["mask"].sum()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
