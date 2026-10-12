"""Precipitation QC and hourly IDW interpolation.

The source station package can contain station-years that are finite but zero
for every hour.  Such records are not credible annual zero-rain observations
and must not dilute active stations during interpolation.  This module keeps
that policy explicit and configurable, and never overwrites source products.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True)
class StationYearSummary:
    station_id: str
    finite_hours: int
    nonzero_hours: int
    annual_total_mm: float
    status: str


def sanitize_station_year(
    values: np.ndarray,
    station_ids: Sequence[str],
    *,
    zero_only_as_missing: bool = True,
) -> tuple[np.ndarray, list[StationYearSummary]]:
    """Return station precipitation with invalid station-years set to NaN."""
    array = np.asarray(values, dtype=float)
    if array.ndim != 2:
        raise ValueError("station precipitation must have shape [time, station]")
    if array.shape[1] != len(station_ids):
        raise ValueError("station_ids length does not match station dimension")

    cleaned = array.copy()
    cleaned[cleaned < 0.0] = np.nan
    summaries: list[StationYearSummary] = []
    for index, station_id in enumerate(station_ids):
        column = cleaned[:, index]
        finite = np.isfinite(column)
        finite_hours = int(finite.sum())
        nonzero_hours = int(np.sum(finite & (column > 0.0)))
        annual_total = float(np.nansum(column))
        if finite_hours == 0:
            status = "NO_DATA"
        elif zero_only_as_missing and nonzero_hours == 0 and annual_total == 0.0:
            status = "ZERO_ONLY_RECLASSIFIED_MISSING"
            cleaned[:, index] = np.nan
        else:
            status = "ACTIVE"
        summaries.append(
            StationYearSummary(
                station_id=str(station_id),
                finite_hours=finite_hours,
                nonzero_hours=nonzero_hours,
                annual_total_mm=annual_total,
                status=status,
            )
        )
    return cleaned, summaries


def idw_interpolate_hourly(
    station_values: np.ndarray,
    station_x: np.ndarray,
    station_y: np.ndarray,
    target_x: np.ndarray,
    target_y: np.ndarray,
    *,
    power: float = 2.0,
    minimum_stations: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate hourly station values to targets and return station counts."""
    values = np.asarray(station_values, dtype=float)
    station_x = np.asarray(station_x, dtype=float)
    station_y = np.asarray(station_y, dtype=float)
    target_x = np.asarray(target_x, dtype=float)
    target_y = np.asarray(target_y, dtype=float)
    if values.ndim != 2:
        raise ValueError("station_values must have shape [time, station]")
    if values.shape[1] != len(station_x) or len(station_x) != len(station_y):
        raise ValueError("station coordinates do not match station_values")
    if len(target_x) != len(target_y):
        raise ValueError("target coordinate lengths do not match")
    if power <= 0.0:
        raise ValueError("IDW power must be positive")
    if minimum_stations < 1:
        raise ValueError("minimum_stations must be at least one")

    distances = np.hypot(
        target_x[:, None] - station_x[None, :],
        target_y[:, None] - station_y[None, :],
    )
    valid = np.isfinite(values)
    station_count = valid.sum(axis=1).astype(np.int16)
    output = np.full((values.shape[0], len(target_x)), np.nan, dtype=float)
    patterns, inverse = np.unique(valid, axis=0, return_inverse=True)
    for pattern_index, pattern in enumerate(patterns):
        row_indices = np.flatnonzero(inverse == pattern_index)
        if int(pattern.sum()) < minimum_stations:
            continue
        selected_distances = distances[:, pattern]
        selected_weights = np.zeros_like(selected_distances, dtype=float)
        exact_rows = np.any(selected_distances <= 1e-9, axis=1)
        selected_weights[exact_rows] = (selected_distances[exact_rows] <= 1e-9).astype(float)
        normal_rows = ~exact_rows
        selected_weights[normal_rows] = 1.0 / np.power(selected_distances[normal_rows], power)
        denominator = selected_weights.sum(axis=1)
        usable_targets = denominator > 0.0
        if not np.any(usable_targets):
            continue
        numerator = values[row_indices][:, pattern] @ selected_weights.T
        output[np.ix_(row_indices, usable_targets)] = (
            numerator[:, usable_targets] / denominator[usable_targets]
        )
    return output, station_count


def rebuild_grid_year(
    station_path: str | Path,
    template_grid_path: str | Path,
    output_path: str | Path,
    grid_cells: Sequence[dict[str, Any]],
    *,
    power: float = 2.0,
    minimum_stations: int = 1,
    zero_only_as_missing: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Rebuild one gridded year from station data using a grid file as template."""
    import xarray as xr
    from pyproj import Transformer

    station_path = Path(station_path)
    template_grid_path = Path(template_grid_path)
    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output_path}")

    with xr.open_dataset(station_path, engine="h5netcdf") as station_ds:
        station_values = np.asarray(station_ds["precipitation"].values, dtype=float)
        station_ids = [str(value) for value in station_ds["station_id"].values]
        station_lon = np.asarray(station_ds["longitude"].values, dtype=float)
        station_lat = np.asarray(station_ds["latitude"].values, dtype=float)
        station_times = np.asarray(station_ds["time"].values)
        cleaned, station_summaries = sanitize_station_year(
            station_values,
            station_ids,
            zero_only_as_missing=zero_only_as_missing,
        )

    with xr.open_dataset(template_grid_path, engine="h5netcdf") as template:
        latitudes = np.asarray(template["lat"].values, dtype=float)
        longitudes = np.asarray(template["lon"].values, dtype=float)
        template_times = np.asarray(template["time"].values)
        template_values = np.asarray(template["precipitation"].values, dtype=float)
        template_attrs = dict(template.attrs)
        precipitation_attrs = dict(template["precipitation"].attrs)
    if not np.array_equal(station_times, template_times):
        raise ValueError("station and template time axes differ")

    rows = np.asarray([int(cell["array_row"]) for cell in grid_cells], dtype=int)
    columns = np.asarray([int(cell["array_col"]) for cell in grid_cells], dtype=int)
    cell_areas = np.asarray([float(cell["area_km2"]) for cell in grid_cells], dtype=float)
    target_lon = longitudes[columns]
    target_lat = latitudes[rows]
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    station_x, station_y = transformer.transform(station_lon, station_lat)
    target_x, target_y = transformer.transform(target_lon, target_lat)
    interpolated, station_count = idw_interpolate_hourly(
        cleaned,
        np.asarray(station_x),
        np.asarray(station_y),
        np.asarray(target_x),
        np.asarray(target_y),
        power=power,
        minimum_stations=minimum_stations,
    )

    corrected = np.zeros_like(template_values, dtype=np.float32)
    corrected[:, rows, columns] = interpolated.astype(np.float32)
    original_basin = np.nansum(template_values[:, rows, columns] * cell_areas[None, :], axis=1) / cell_areas.sum()
    corrected_basin = np.nansum(interpolated * cell_areas[None, :], axis=1) / cell_areas.sum()
    no_source_hours = station_count < minimum_stations
    quality_code = np.where(no_source_hours, 0, np.where(station_count == 1, 1, 2)).astype(np.int8)

    dataset = xr.Dataset(
        data_vars={
            "precipitation": (("time", "lat", "lon"), corrected),
            "source_station_count": (("time",), station_count),
            "precipitation_quality_code": (("time",), quality_code),
        },
        coords={"time": template_times, "lat": latitudes, "lon": longitudes},
        attrs={
            **template_attrs,
            "history": "rebuilt by runoff_platform.data.precipitation_idw",
            "idw_power": float(power),
            "minimum_stations": int(minimum_stations),
            "zero_only_station_year_policy": "reclassify_as_missing" if zero_only_as_missing else "keep_as_observed_zero",
            "quality_code_meanings": "0=no source station, 1=single station, 2=multiple stations",
            "source_station_file": str(station_path),
        },
    )
    dataset["precipitation"].attrs = precipitation_attrs
    dataset["source_station_count"].attrs = {"long_name": "valid source station count used for hourly IDW", "units": "1"}
    dataset["precipitation_quality_code"].attrs = {"long_name": "hourly precipitation interpolation quality code", "units": "1"}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = {
        "precipitation": {"zlib": True, "complevel": 4, "dtype": "float32"},
        "source_station_count": {"zlib": True, "complevel": 4, "dtype": "int16"},
        "precipitation_quality_code": {"zlib": True, "complevel": 4, "dtype": "int8"},
    }
    dataset.to_netcdf(output_path, engine="h5netcdf", encoding=encoding)
    dataset.close()

    active_count = sum(summary.status == "ACTIVE" for summary in station_summaries)
    reclassified_count = sum(summary.status == "ZERO_ONLY_RECLASSIFIED_MISSING" for summary in station_summaries)
    return {
        "year": int(str(station_times[0])[:4]),
        "station_count": len(station_summaries),
        "active_station_count": active_count,
        "zero_only_reclassified_count": reclassified_count,
        "no_data_station_count": sum(summary.status == "NO_DATA" for summary in station_summaries),
        "hours_below_minimum_stations": int(no_source_hours.sum()),
        "original_basin_total_mm": float(np.nansum(original_basin)),
        "corrected_basin_total_mm": float(np.nansum(corrected_basin)),
        "station_summaries": [summary.__dict__ for summary in station_summaries],
        "output_path": str(output_path),
    }
