"""Physical quality checks for event-based rainfall-runoff calibration."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


def evaluate_event_quality(
    *,
    event_id: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    times: pd.DatetimeIndex,
    grid_precipitation_mm: np.ndarray,
    grid_areas_km2: np.ndarray,
    observed_flow_cms: pd.Series,
    source_station_count: np.ndarray | None,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one event using configurable data and water-volume checks."""
    rules = config.get("rules", config)
    warnings_config = config.get("warnings", {})
    precipitation = np.asarray(grid_precipitation_mm, dtype=float)
    areas = np.asarray(grid_areas_km2, dtype=float)
    if precipitation.ndim != 2 or precipitation.shape[1] != len(areas):
        raise ValueError("grid precipitation must have shape [time, grid]")
    if len(times) != precipitation.shape[0]:
        raise ValueError("precipitation time axis length mismatch")
    if np.any(areas <= 0.0) or not np.isfinite(areas).all():
        raise ValueError("grid areas must be finite and positive")

    valid = np.isfinite(precipitation)
    valid_area = valid @ areas
    weighted_sum = np.nansum(precipitation * areas[None, :], axis=1)
    basin_precipitation = np.divide(
        weighted_sum,
        valid_area,
        out=np.full(len(times), np.nan, dtype=float),
        where=valid_area > 0.0,
    )
    event_mask = (times >= start) & (times <= end)
    precipitation_valid_fraction = float(valid[event_mask].mean()) if np.any(event_mask) else 0.0
    event_rain = float(np.nansum(basin_precipitation[event_mask]))
    warmup_event_rain = float(np.nansum(basin_precipitation))

    paired = observed_flow_cms.loc[start:end].dropna().astype(float)
    duration_hours = int((end - start).total_seconds() / 3600.0) + 1
    basin_area_km2 = float(config["basin_area_km2"])
    observed_depth = (
        float(paired.sum()) * 3600.0 / (basin_area_km2 * 1_000_000.0) * 1000.0
        if len(paired)
        else float("nan")
    )
    runoff_precipitation_ratio = (
        observed_depth / warmup_event_rain
        if warmup_event_rain > 0.0 and np.isfinite(observed_depth)
        else None
    )

    source_counts = None if source_station_count is None else np.asarray(source_station_count, dtype=float)
    source_count_available = source_counts is not None and len(source_counts) == len(times) and np.any(source_counts >= 0)
    minimum_source_stations = int(rules.get("minimum_source_stations", 1))
    if source_count_available:
        source_ok_fraction = float(np.mean(source_counts[event_mask] >= minimum_source_stations))
        single_station_fraction = float(np.mean(source_counts[event_mask] == 1))
        minimum_source_count = int(np.min(source_counts[event_mask]))
    else:
        source_ok_fraction = None
        single_station_fraction = None
        minimum_source_count = None

    exclusions: list[str] = []
    warnings: list[str] = []
    if duration_hours > int(rules.get("maximum_duration_hours", 720)):
        exclusions.append("DURATION_EXCEEDS_MAXIMUM")
    if len(paired) < int(rules.get("minimum_paired_flow_hours", 24)):
        exclusions.append("FLOW_RECORD_TOO_SHORT")
    if precipitation_valid_fraction < float(rules.get("minimum_precipitation_valid_fraction", 0.999)):
        exclusions.append("PRECIPITATION_INCOMPLETE")
    if source_count_available:
        if source_ok_fraction < float(rules.get("minimum_source_station_hour_fraction", 0.999)):
            exclusions.append("SOURCE_STATIONS_BELOW_MINIMUM")
    else:
        warnings.append("SOURCE_STATION_COUNT_UNAVAILABLE")
        if bool(rules.get("require_source_station_count", False)):
            exclusions.append("SOURCE_STATION_COUNT_UNAVAILABLE")
    if warmup_event_rain < float(rules.get("minimum_warmup_event_rain_mm", 1.0)):
        exclusions.append("WARMUP_EVENT_RAIN_BELOW_MINIMUM")
    maximum_ratio = float(rules.get("maximum_observed_runoff_to_precipitation_ratio", 1.2))
    if runoff_precipitation_ratio is not None and runoff_precipitation_ratio > maximum_ratio:
        exclusions.append("RUNOFF_PRECIPITATION_RATIO_EXCEEDS_MAXIMUM")

    if single_station_fraction is not None and single_station_fraction > float(warnings_config.get("single_station_hour_fraction", 0.5)):
        warnings.append("SINGLE_STATION_DOMINATED")
    if len(paired):
        peak_position = int(np.argmax(paired.to_numpy()))
        if peak_position <= int(warnings_config.get("peak_near_start_hours", 6)):
            warnings.append("PEAK_NEAR_EVENT_START")
        peak = float(paired.max())
        end_ratio = float(paired.iloc[-1] / peak) if peak > 0.0 else 0.0
        if end_ratio > float(warnings_config.get("end_flow_to_peak_ratio", 0.2)):
            warnings.append("EVENT_END_FLOW_REMAINS_HIGH")
    else:
        peak_position = None
        end_ratio = None

    return {
        "event_id": event_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_hours": duration_hours,
        "paired_flow_hours": int(len(paired)),
        "event_rain_mm": event_rain,
        "warmup_event_rain_mm": warmup_event_rain,
        "observed_runoff_depth_mm": observed_depth,
        "observed_runoff_to_precipitation_ratio": runoff_precipitation_ratio,
        "precipitation_valid_fraction": precipitation_valid_fraction,
        "source_station_count_available": bool(source_count_available),
        "minimum_source_station_count": minimum_source_count,
        "source_station_ok_fraction": source_ok_fraction,
        "single_station_hour_fraction": single_station_fraction,
        "peak_offset_hours": peak_position,
        "end_flow_to_peak_ratio": end_ratio,
        "accepted": not exclusions,
        "exclusion_reasons": exclusions,
        "warning_reasons": warnings,
    }
