"""Rebuild flood-event metadata from QC precipitation and observed flow."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import xarray as xr

from .precipitation_quality import read_station_count


def load_window_overrides(path: str | Path | None) -> dict[str, pd.Timestamp]:
    """Load explicitly reviewed event ends from a QC audit table."""
    if path is None:
        return {}
    frame = pd.read_csv(path)
    required = {"event_id", "end_used_for_diagnostic"}
    if not required.issubset(frame.columns):
        raise ValueError(f"event window QC requires columns: {sorted(required)}")
    result: dict[str, pd.Timestamp] = {}
    for row in frame.itertuples(index=False):
        value = getattr(row, "end_used_for_diagnostic")
        if pd.isna(value):
            continue
        result[str(getattr(row, "event_id"))] = pd.Timestamp(value)
    return result


def _read_year_window(
    directory: Path,
    pattern: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    variables: list[str],
) -> tuple[pd.DatetimeIndex, dict[str, np.ndarray]]:
    time_parts: list[pd.DatetimeIndex] = []
    value_parts: dict[str, list[np.ndarray]] = {name: [] for name in variables}
    for year in range(start.year, end.year + 1):
        path = directory / pattern.format(year=year)
        if not path.is_file():
            raise FileNotFoundError(path)
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
            mask = (times >= start) & (times <= end)
            if not np.any(mask):
                continue
            time_parts.append(times[mask])
            for name in variables:
                value_parts[name].append(np.asarray(dataset[name].values[mask]))
    if not time_parts:
        raise ValueError(f"no data found for event window {start} to {end}")
    times = time_parts[0].append(time_parts[1:])
    expected = pd.date_range(start, end, freq="h")
    if not times.equals(expected):
        raise ValueError(f"event input is not a complete hourly axis: {start} to {end}")
    return times, {name: np.concatenate(parts, axis=0) for name, parts in value_parts.items()}


def _station_statistics(
    station_directory: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    times, values = _read_year_window(
        station_directory,
        "nananxi_station_precip_hourly_{year}.nc",
        start,
        end,
        ["precipitation"],
    )
    precipitation = np.asarray(values["precipitation"], dtype=float)
    finite = np.isfinite(precipitation)
    reporting = np.any(finite, axis=0)
    reporting_count = int(np.sum(reporting))
    finite_records = int(np.sum(finite[:, reporting])) if reporting_count else 0
    expected_records = len(times) * reporting_count
    totals = np.nansum(precipitation[:, reporting], axis=0) if reporting_count else np.asarray([])
    return {
        "reportingRainStations": reporting_count,
        "rainRecords": finite_records,
        "rainCompleteness": (
            float(finite_records / expected_records) if expected_records else 0.0
        ),
        "stationAverageTotalRainMm": float(np.mean(totals)) if totals.size else 0.0,
        "maximumStationTotalRainMm": float(np.max(totals)) if totals.size else 0.0,
        "maximumHourlyStationRainMm": (
            float(np.nanmax(precipitation[:, reporting])) if reporting_count else 0.0
        ),
    }


def _grid_statistics(
    grid_directory: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    positions: list[tuple[int, int]],
    areas_km2: np.ndarray,
) -> dict[str, Any]:
    time_parts: list[pd.DatetimeIndex] = []
    precipitation_parts: list[np.ndarray] = []
    station_count_parts: list[np.ndarray] = []
    station_count_fields: set[str] = set()
    for year in range(start.year, end.year + 1):
        path = grid_directory / f"nananxi_precip_hourly_1km_{year}.nc"
        if not path.is_file():
            raise FileNotFoundError(path)
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
            mask = (times >= start) & (times <= end)
            if not np.any(mask):
                continue
            source = np.asarray(dataset["precipitation"].values, dtype=np.float32)
            precipitation_parts.append(
                np.stack([source[mask, row, column] for row, column in positions], axis=1)
            )
            station_count, field = read_station_count(dataset, mask, required=True)
            station_count_parts.append(station_count)
            station_count_fields.add(str(field))
            time_parts.append(times[mask])
    if not time_parts:
        raise ValueError(f"no gridded precipitation for event window {start} to {end}")
    times = time_parts[0].append(time_parts[1:])
    expected = pd.date_range(start, end, freq="h")
    if not times.equals(expected):
        raise ValueError(f"gridded precipitation is not hourly complete: {start} to {end}")
    precipitation = np.concatenate(precipitation_parts, axis=0)
    counts = np.concatenate(station_count_parts)
    valid = np.isfinite(precipitation)
    valid_area = valid @ areas_km2
    weighted_sum = np.nansum(precipitation * areas_km2[None, :], axis=1)
    basin_hourly = np.divide(
        weighted_sum,
        valid_area,
        out=np.full(len(times), np.nan),
        where=valid_area > 0.0,
    )
    return {
        "gridBasinAverageTotalRainMm": float(np.nansum(basin_hourly)),
        "gridRainCompleteness": float(np.isfinite(basin_hourly).mean()),
        "minimumReportingStationCount": int(np.min(counts)),
        "maximumReportingStationCount": int(np.max(counts)),
        "stationCountFields": sorted(station_count_fields),
    }


def _flow_statistics(
    observations: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    event = observations.reindex(pd.date_range(start, end, freq="h"))
    flow = pd.to_numeric(event["flow"], errors="coerce")
    valid = flow.dropna()
    peak_time = valid.idxmax() if not valid.empty else None
    level = (
        pd.to_numeric(event["level"], errors="coerce")
        if "level" in event
        else pd.Series(index=event.index, dtype=float)
    )
    peak_level = level.get(peak_time, np.nan) if peak_time is not None else np.nan
    return {
        "riverRecords": int(valid.size),
        "riverCompleteness": float(valid.size / len(event)) if len(event) else 0.0,
        "observedPeakFlowM3s": float(valid.max()) if not valid.empty else None,
        "observedPeakLevelM": float(peak_level) if np.isfinite(peak_level) else None,
        "observedPeakTime": peak_time.isoformat() if peak_time is not None else None,
    }


def rebuild_event_catalog(
    catalog: Mapping[str, Any],
    *,
    station_directory: str | Path,
    grid_directory: str | Path,
    grid_cells: list[dict[str, Any]],
    grid_areas_km2: np.ndarray | None = None,
    observations: pd.DataFrame,
    end_overrides: Mapping[str, pd.Timestamp] | None = None,
    forcing_version: str = "precipitation_qc_20260819",
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Return a new immutable catalog and a row-by-row rebuild audit."""
    station_directory = Path(station_directory)
    grid_directory = Path(grid_directory)
    positions = [(int(cell["array_row"]), int(cell["array_col"])) for cell in grid_cells]
    areas = (
        np.asarray(grid_areas_km2, dtype=float)
        if grid_areas_km2 is not None
        else np.asarray([float(cell["area_km2"]) for cell in grid_cells], dtype=float)
    )
    if areas.shape != (len(grid_cells),):
        raise ValueError("grid_areas_km2 must have one value per grid cell")
    if not np.isfinite(areas).all() or np.any(areas < 0.0) or float(np.sum(areas)) <= 0.0:
        raise ValueError("grid_areas_km2 must be finite, non-negative and have positive total")
    overrides = dict(end_overrides or {})
    packages: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for source_item in catalog.get("packages", []):
        item = json.loads(json.dumps(source_item))
        event_id = str(item["eventId"])
        start = pd.Timestamp(item["start"])
        original_end = pd.Timestamp(item["end"])
        end = pd.Timestamp(overrides.get(event_id, original_end))
        if end < start:
            raise ValueError(f"event end precedes start: {event_id}")
        station = _station_statistics(station_directory, start, end)
        grid = _grid_statistics(grid_directory, start, end, positions, areas)
        flow = _flow_statistics(observations, start, end)
        observed_inputs = dict(item.get("observedInputs", {}))
        observed_inputs.update(station)
        observed_inputs.update(flow)
        observed_inputs["rainDatasetId"] = "XF-OBS-RAIN-HOURLY-QC"
        item["start"] = start.isoformat(timespec="minutes")
        item["end"] = end.isoformat(timespec="minutes")
        item["durationHours"] = int((end - start).total_seconds() / 3600.0) + 1
        item["status"] = "ready" if flow["riverCompleteness"] >= 0.999 else "review"
        item["observedInputs"] = observed_inputs
        item["spatialRainInputs"] = grid
        item["qcRevision"] = {
            "forcingVersion": forcing_version,
            "originalEnd": original_end.isoformat(timespec="minutes"),
            "windowChanged": end != original_end,
            "windowSource": (
                "DELIVERY_QC_DIAGNOSTIC_WINDOW" if end != original_end else "SOURCE_CATALOG"
            ),
        }
        packages.append(item)
        audit_rows.append(
            {
                "event_id": event_id,
                "start": item["start"],
                "original_end": original_end.isoformat(timespec="minutes"),
                "rebuilt_end": item["end"],
                "window_changed": end != original_end,
                "duration_hours": item["durationHours"],
                **station,
                **grid,
                **flow,
            }
        )
    rebuilt = dict(catalog)
    rebuilt["schemaVersion"] = "1.1"
    rebuilt["generatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rebuilt["count"] = len(packages)
    rebuilt["packages"] = packages
    rebuilt["qcRevision"] = {
        "forcingVersion": forcing_version,
        "sourceCatalogGeneratedAt": catalog.get("generatedAt"),
        "windowOverrideCount": sum(row["window_changed"] for row in audit_rows),
        "rainfallStatistics": "QC_ACCEPTED_STATIONS_AND_AREA_WEIGHTED_1KM_GRID",
        "flowStatistics": "RECOMPUTED_FROM_HOURLY_OBSERVATIONS",
    }
    return rebuilt, pd.DataFrame(audit_rows)


__all__ = ["load_window_overrides", "rebuild_event_catalog"]
