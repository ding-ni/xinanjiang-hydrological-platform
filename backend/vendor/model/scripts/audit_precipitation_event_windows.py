"""Audit basin precipitation, flood windows and annual water-supply consistency."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _relative_difference(left: pd.Series, right: pd.Series) -> pd.Series:
    denominator = right.abs().replace(0.0, np.nan)
    result = (left - right) / denominator
    return result.where(denominator.notna(), np.where(left.abs() <= 1.0e-10, 0.0, np.nan))


def run(args: argparse.Namespace) -> dict:
    config = _json(Path(args.config).resolve())
    thresholds = config["thresholds"]
    catalog = _json(Path(args.event_catalog).resolve())
    catalog_rows = []
    for event in catalog["packages"]:
        observed = event.get("observedInputs", {})
        spatial = event.get("spatialRainInputs", {})
        revision = event.get("qcRevision", {})
        catalog_rows.append(
            {
                "event_id": event["eventId"],
                "start": event["start"],
                "end": event["end"],
                "status": event.get("status"),
                "duration_hours": event.get("durationHours"),
                "window_changed": bool(revision.get("windowChanged", False)),
                "reporting_station_count": observed.get("reportingRainStations"),
                "station_average_total_rain_mm": observed.get("stationAverageTotalRainMm"),
                "grid_basin_average_total_rain_mm": spatial.get("gridBasinAverageTotalRainMm"),
                "grid_rain_completeness": spatial.get("gridRainCompleteness"),
                "river_completeness": observed.get("riverCompleteness"),
                "observed_peak_flow_m3_s": observed.get("observedPeakFlowM3s"),
                "observed_peak_time": observed.get("observedPeakTime"),
            }
        )
    frame = pd.DataFrame(catalog_rows)
    quality = pd.read_csv(Path(args.event_quality).resolve())
    rebuild = pd.read_csv(Path(args.rebuild_audit).resolve())
    rebuild = rebuild[["event_id", "original_end", "rebuilt_end", "window_changed"]].rename(
        columns={"window_changed": "window_changed_rebuild"}
    )
    frame = frame.merge(quality, on="event_id", how="left", suffixes=("", "_quality"))
    frame = frame.merge(rebuild, on="event_id", how="left")
    frame["grid_station_total_relative_difference"] = _relative_difference(
        pd.to_numeric(frame["grid_basin_average_total_rain_mm"], errors="coerce"),
        pd.to_numeric(frame["station_average_total_rain_mm"], errors="coerce"),
    )

    def flags(row: pd.Series) -> list[str]:
        result = []
        station_count = float(row.get("reporting_station_count") or 0.0)
        rain_difference = row.get("grid_station_total_relative_difference")
        if station_count < thresholds["minimum_reporting_station_count_for_spatial_check"]:
            result.append("SPATIAL_INTERPOLATION_NOT_VERIFIABLE_SINGLE_STATION")
        if pd.notna(rain_difference) and abs(float(rain_difference)) > thresholds["maximum_grid_station_total_relative_difference"]:
            result.append("GRID_STATION_TOTAL_DIFFERENCE")
        if bool(row.get("window_changed")) or bool(row.get("window_changed_rebuild")):
            result.append("EVENT_WINDOW_REBUILT")
        if float(row.get("duration_hours") or 0.0) > thresholds["maximum_event_duration_hours"]:
            result.append("EVENT_DURATION_EXCESSIVE")
        if float(row.get("peak_offset_hours") or np.inf) <= thresholds["maximum_peak_near_start_hours"]:
            result.append("PEAK_NEAR_EVENT_START")
        if float(row.get("end_flow_to_peak_ratio") or 0.0) > thresholds["maximum_end_flow_to_peak_ratio"]:
            result.append("EVENT_END_FLOW_REMAINS_HIGH")
        ratio = row.get("observed_runoff_to_precipitation_ratio")
        if pd.notna(ratio) and float(ratio) > thresholds["maximum_runoff_to_precipitation_ratio"]:
            result.append("RUNOFF_PRECIPITATION_RATIO_EXCESSIVE")
        if float(row.get("river_completeness") or 0.0) < thresholds["minimum_river_completeness"]:
            result.append("RIVER_INCOMPLETE")
        if float(row.get("grid_rain_completeness") or 0.0) < thresholds["minimum_grid_rain_completeness"]:
            result.append("GRID_RAIN_INCOMPLETE")
        if str(row.get("status", "")).lower() != "ready":
            result.append("CATALOG_STATUS_NOT_READY")
        return sorted(set(result))

    event_flags = frame.apply(flags, axis=1)
    frame["audit_flags"] = event_flags.map(";".join)
    frame["audit_flag_count"] = event_flags.map(len)
    frame["requires_review"] = frame["audit_flag_count"] > 0

    annual = pd.read_csv(Path(args.annual_metrics).resolve())
    annual["annual_water_supply_flag"] = np.where(
        annual["climate_balance_residual_using_observed_runoff_mm"].abs()
        > thresholds["maximum_abs_annual_climate_balance_residual_mm"],
        "OBSERVED_RUNOFF_EXCEEDS_CLIMATE_WATER_SUPPLY",
        "OK",
    )
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / "event_precipitation_window_audit.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output_dir / "annual_water_supply_audit.csv", index=False, encoding="utf-8-sig")
    reason_counts = Counter(reason for reasons in event_flags for reason in reasons)
    accepted = frame.loc[frame["accepted"].fillna(False).astype(bool)]
    report = {
        "module_id": config["module_id"],
        "status": "REVIEW_REQUIRED" if frame["requires_review"].any() or annual["annual_water_supply_flag"].ne("OK").any() else "PASSED",
        "event_count": int(len(frame)),
        "accepted_event_count": int(len(accepted)),
        "event_review_count": int(frame["requires_review"].sum()),
        "accepted_event_review_count": int(accepted["requires_review"].sum()),
        "flag_counts": dict(reason_counts),
        "grid_station_difference": {
            "median_percent": float(frame["grid_station_total_relative_difference"].median() * 100.0),
            "maximum_abs_percent": float(frame["grid_station_total_relative_difference"].abs().max() * 100.0),
        },
        "annual_water_supply": {
            "year_count": int(len(annual)),
            "flagged_year_count": int(annual["annual_water_supply_flag"].ne("OK").sum()),
            "mean_climate_balance_residual_mm": float(annual["climate_balance_residual_using_observed_runoff_mm"].mean()),
            "minimum_climate_balance_residual_mm": float(annual["climate_balance_residual_using_observed_runoff_mm"].min()),
        },
        "decision": "DO_NOT_PROMOTE_OR_RECALIBRATE_PRODUCTION_PARAMETERS_UNTIL_FLOW_RAINFALL_AREA_AND_RESERVOIR_DATA_DATUMS_ARE_RECONCILED",
        "output_files": {
            "event_audit": "event_precipitation_window_audit.csv",
            "annual_audit": "annual_water_supply_audit.csv",
        },
    }
    (output_dir / "precipitation_event_audit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config" / "precipitation_event_audit_config.json"))
    parser.add_argument("--event-catalog", default=str(ROOT / "data_interface" / "event_catalog" / "nananxi_flood_event_catalog_qc_20260819.json"))
    parser.add_argument("--event-quality", default=str(ROOT / "validation" / "production_calibration_monthly_pet_20260819" / "formal_runs" / "stability_WLM_100_seed_20260819" / "event_quality.csv"))
    parser.add_argument("--rebuild-audit", default=str(ROOT / "data_interface" / "event_catalog" / "event_catalog_rebuild_audit.csv"))
    parser.add_argument("--annual-metrics", default=str(ROOT / "validation" / "continuous_state_corrected_area_20260819" / "annual_water_balance_metrics.csv"))
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
