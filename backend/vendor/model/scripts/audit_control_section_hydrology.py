"""Audit control-section flow records and annual rainfall-runoff consistency.

This is a data-quality module. It does not change observations, parameters or
model equations. Outputs are intended for platform pre-calibration review.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import xarray as xr
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from xaj_routing.control_area import contributing_grid_areas  # noqa: E402
from xaj_routing.io import read_control_points_json, read_topology_json  # noqa: E402


def _read_grid_cells(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _select_year_rows(
    path: Path,
    years: set[int],
    *,
    sheet_name: str | None,
    time_column: int,
    level_column: int,
    flow_column: int,
    station_column: int | None = None,
) -> pd.DataFrame:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[sheet_name] if sheet_name else workbook.active
    rows: list[dict[str, Any]] = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        raw_time = values[time_column]
        if raw_time is None:
            continue
        try:
            time = pd.Timestamp(raw_time)
        except (TypeError, ValueError):
            continue
        if time.year not in years:
            continue
        rows.append(
            {
                "time": time,
                "level": values[level_column],
                "flow": values[flow_column],
                "station_code": values[station_column] if station_column is not None else None,
            }
        )
    workbook.close()
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["level", "flow", "station_code"])
    frame["level"] = pd.to_numeric(frame["level"], errors="coerce")
    frame["flow"] = pd.to_numeric(frame["flow"], errors="coerce")
    return frame.set_index("time").sort_index()


def _longest_constant_run(values: pd.Series) -> tuple[int, pd.Timestamp | None, pd.Timestamp | None]:
    valid = values.dropna()
    if valid.empty:
        return 0, None, None
    groups = valid.ne(valid.shift()).cumsum()
    counts = groups.value_counts()
    group = counts.idxmax()
    selected = valid.index[groups == group]
    return int(len(selected)), selected[0], selected[-1]


def _longest_monotonic_run(
    values: pd.Series, *, increasing: bool
) -> tuple[int, pd.Timestamp | None, pd.Timestamp | None]:
    valid = values.dropna()
    if valid.empty:
        return 0, None, None
    difference = valid.diff()
    continues = difference.ge(-1.0e-12) if increasing else difference.le(1.0e-12)
    break_groups = (~continues).cumsum()
    counts = break_groups.value_counts()
    group = counts.idxmax()
    selected = valid.index[break_groups == group]
    return int(len(selected)), selected[0], selected[-1]


def _station_year_statistics(directory: Path, years: Iterable[int]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for year in sorted(years):
        path = directory / f"nananxi_station_precip_hourly_{year}.nc"
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            values = np.asarray(dataset["precipitation"].values, dtype=float)
            names = [str(value) for value in dataset["station_name"].values]
            station_ids = [str(value) for value in dataset["station_id"].values]
            times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
        for index, name in enumerate(names):
            station = values[:, index]
            finite = np.isfinite(station)
            monthly = pd.Series(station, index=times).groupby(times.month).sum(min_count=1)
            rows.append(
                {
                    "year": year,
                    "station_id": station_ids[index],
                    "station_name": name,
                    "finite_hours": int(finite.sum()),
                    "positive_hours": int(np.sum(station[finite] > 0.0)),
                    "annual_total_mm": float(np.nansum(station)) if finite.any() else None,
                    "zero_rain_month_count": int((monthly.fillna(0.0) == 0.0).sum()),
                }
            )
    return pd.DataFrame(rows)


def _load_control_grid_areas(args: argparse.Namespace, grids: list[dict[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    topology = read_topology_json(Path(args.topology).resolve())
    controls = read_control_points_json(Path(args.control_points).resolve())
    if len(controls) != 1:
        raise ValueError("hydrology audit requires exactly one control point")
    control = controls[0]
    config = json.loads(Path(args.control_area_config).read_text(encoding="utf-8-sig"))
    if control.control_id != str(config["control_id"]) or control.reach_id != str(
        config["control_reach_id"]
    ):
        raise ValueError("control point and area configuration do not match")
    areas = contributing_grid_areas(
        Path(args.grid_mapping).resolve(),
        topology,
        control.reach_id,
        [str(grid["unit_id"]) for grid in grids],
    )
    return areas, config


def _load_basin_precipitation(
    directory: Path,
    years: Iterable[int],
    grids: list[dict[str, str]],
    effective_areas: np.ndarray,
) -> pd.Series:
    positions = [(int(grid["array_row"]), int(grid["array_col"])) for grid in grids]
    selected = np.flatnonzero(effective_areas > 0.0)
    parts: list[pd.Series] = []
    for year in sorted(years):
        path = directory / f"nananxi_precip_hourly_1km_{year}.nc"
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
            source = np.asarray(dataset["precipitation"].values, dtype=float)
            values = np.stack(
                [source[:, positions[index][0], positions[index][1]] for index in selected],
                axis=1,
            )
        areas = effective_areas[selected]
        valid = np.isfinite(values)
        valid_area = valid @ areas
        weighted = np.nansum(values * areas[None, :], axis=1)
        basin = np.divide(
            weighted,
            valid_area,
            out=np.full(len(times), np.nan),
            where=valid_area > 0.0,
        )
        parts.append(pd.Series(basin, index=times, name="precipitation_mm"))
    return pd.concat(parts).sort_index()


def _compare_sources(raw: pd.DataFrame, standard: pd.DataFrame) -> dict[str, Any]:
    raw_duplicates = int(raw.index.duplicated(keep=False).sum())
    standard_duplicates = int(standard.index.duplicated(keep=False).sum())
    raw_unique = raw.loc[~raw.index.duplicated(keep="last")]
    standard_unique = standard.loc[~standard.index.duplicated(keep="last")]
    common = raw_unique.index.intersection(standard_unique.index)
    level_match = np.isclose(
        raw_unique.loc[common, "level"],
        standard_unique.loc[common, "level"],
        rtol=0.0,
        atol=5.0e-4,
        equal_nan=True,
    )
    flow_match = np.isclose(
        raw_unique.loc[common, "flow"],
        standard_unique.loc[common, "flow"],
        rtol=0.0,
        atol=5.0e-4,
        equal_nan=True,
    )
    return {
        "raw_records": int(len(raw)),
        "standard_records": int(len(standard)),
        "raw_duplicate_timestamp_records": raw_duplicates,
        "standard_duplicate_timestamp_records": standard_duplicates,
        "common_timestamp_count": int(len(common)),
        "raw_only_timestamp_count": int(len(raw_unique.index.difference(standard_unique.index))),
        "standard_only_timestamp_count": int(len(standard_unique.index.difference(raw_unique.index))),
        "level_mismatch_count": int((~level_match).sum()),
        "flow_mismatch_count": int((~flow_match).sum()),
        "raw_station_codes": sorted(
            str(value) for value in raw["station_code"].dropna().unique()
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    years = sorted(set(int(value) for value in args.years))
    year_set = set(years)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    raw = _select_year_rows(
        Path(args.raw_flow).resolve(),
        year_set,
        sheet_name=None,
        time_column=1,
        level_column=2,
        flow_column=3,
        station_column=0,
    )
    standard = _select_year_rows(
        Path(args.standard_flow).resolve(),
        year_set,
        sheet_name=args.standard_sheet,
        time_column=0,
        level_column=1,
        flow_column=2,
    )
    comparison = _compare_sources(raw, standard)
    standard = standard.loc[~standard.index.duplicated(keep="last")]

    grids = _read_grid_cells(Path(args.grid_static).resolve())
    control_grid_areas, area_config = _load_control_grid_areas(args, grids)
    precipitation = _load_basin_precipitation(
        Path(args.precip_dir).resolve(), years, grids, control_grid_areas
    )
    station_year = _station_year_statistics(Path(args.station_dir).resolve(), years)
    observation_area_km2 = float(area_config["observation_area_km2"])

    annual_rows: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, Any]] = []
    jump_rows: list[dict[str, Any]] = []
    for year in years:
        expected = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")
        flow = standard["flow"].reindex(expected)
        level = standard["level"].reindex(expected)
        rain = precipitation.reindex(expected)
        runoff_depth = float(flow.sum(min_count=1)) * 3.6 / observation_area_km2
        rain_depth = float(rain.sum(min_count=1))
        constant_hours, constant_start, constant_end = _longest_constant_run(flow)
        increasing_hours, increasing_start, increasing_end = _longest_monotonic_run(
            flow, increasing=True
        )
        decreasing_hours, decreasing_start, decreasing_end = _longest_monotonic_run(
            flow, increasing=False
        )
        valid_pair = pd.concat([level, flow], axis=1).dropna()
        level_flow_spearman = (
            float(valid_pair.corr(method="spearman").iloc[0, 1])
            if len(valid_pair) >= 2
            else None
        )
        annual_rows.append(
            {
                "year": year,
                "expected_hours": len(expected),
                "flow_record_count": int(flow.notna().sum()),
                "flow_completeness": float(flow.notna().mean()),
                "negative_flow_hours": int((flow < 0.0).sum()),
                "zero_flow_hours": int((flow == 0.0).sum()),
                "minimum_flow_m3s": float(flow.min()),
                "mean_flow_m3s": float(flow.mean()),
                "peak_flow_m3s": float(flow.max()),
                "peak_time": flow.idxmax().isoformat() if flow.notna().any() else None,
                "annual_precipitation_mm": rain_depth,
                "annual_runoff_depth_mm": runoff_depth,
                "runoff_precipitation_ratio": runoff_depth / rain_depth if rain_depth > 0.0 else None,
                "longest_identical_flow_run_hours": constant_hours,
                "longest_identical_flow_run_start": constant_start.isoformat() if constant_start else None,
                "longest_identical_flow_run_end": constant_end.isoformat() if constant_end else None,
                "longest_non_decreasing_flow_run_hours": increasing_hours,
                "longest_non_decreasing_flow_run_start": increasing_start.isoformat() if increasing_start else None,
                "longest_non_decreasing_flow_run_end": increasing_end.isoformat() if increasing_end else None,
                "longest_non_increasing_flow_run_hours": decreasing_hours,
                "longest_non_increasing_flow_run_start": decreasing_start.isoformat() if decreasing_start else None,
                "longest_non_increasing_flow_run_end": decreasing_end.isoformat() if decreasing_end else None,
                "zero_basin_rain_month_count": int(
                    sum(
                        float(rain[expected.month == month].sum(min_count=1)) == 0.0
                        for month in range(1, 13)
                    )
                ),
                "level_flow_spearman": level_flow_spearman,
            }
        )
        hourly_jump = flow.diff().abs().dropna().nlargest(20)
        for time, value in hourly_jump.items():
            jump_rows.append(
                {
                    "year": year,
                    "time": time.isoformat(),
                    "previous_flow_m3s": float(flow.loc[time - pd.Timedelta(hours=1)]),
                    "flow_m3s": float(flow.loc[time]),
                    "absolute_hourly_change_m3s": float(value),
                }
            )
        for month in range(1, 13):
            month_mask = expected.month == month
            month_flow = flow[month_mask]
            month_rain = rain[month_mask]
            month_runoff = float(month_flow.sum(min_count=1)) * 3.6 / observation_area_km2
            month_precipitation = float(month_rain.sum(min_count=1))
            monthly_rows.append(
                {
                    "year": year,
                    "month": month,
                    "precipitation_mm": month_precipitation,
                    "runoff_depth_mm": month_runoff,
                    "runoff_precipitation_ratio": (
                        month_runoff / month_precipitation if month_precipitation > 0.0 else None
                    ),
                    "mean_flow_m3s": float(month_flow.mean()),
                    "peak_flow_m3s": float(month_flow.max()),
                    "flow_completeness": float(month_flow.notna().mean()),
                }
            )

    annual = pd.DataFrame(annual_rows)
    monthly = pd.DataFrame(monthly_rows)
    jumps = pd.DataFrame(jump_rows)
    annual.to_csv(output / "annual_hydrology_audit.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(output / "monthly_hydrology_audit.csv", index=False, encoding="utf-8-sig")
    jumps.to_csv(output / "largest_hourly_flow_changes.csv", index=False, encoding="utf-8-sig")
    station_year.to_csv(
        output / "station_year_precipitation_audit.csv", index=False, encoding="utf-8-sig"
    )

    flags = []
    if comparison["flow_mismatch_count"] or comparison["raw_only_timestamp_count"] or comparison["standard_only_timestamp_count"]:
        flags.append("RAW_AND_PLATFORM_FLOW_DIFFER")
    if bool((annual["flow_completeness"] < 0.999).any()):
        flags.append("ANNUAL_FLOW_INCOMPLETE")
    if bool((annual["runoff_precipitation_ratio"] > 1.2).any()):
        flags.append("ANNUAL_RUNOFF_PRECIPITATION_RATIO_EXCEEDS_1P2")
    if bool(
        (
            annual[
                [
                    "longest_non_decreasing_flow_run_hours",
                    "longest_non_increasing_flow_run_hours",
                ]
            ].max(axis=1)
            > 720
        ).any()
    ):
        flags.append("FLOW_HAS_MONOTONIC_SEGMENT_LONGER_THAN_720_HOURS")
    if bool((annual["zero_basin_rain_month_count"] > 0).any()):
        flags.append("BASIN_PRECIPITATION_HAS_ALL_ZERO_MONTHS")
    accepted_station_year = station_year.loc[station_year["finite_hours"] > 0]
    annual_station_spread = accepted_station_year.groupby("year")["annual_total_mm"].agg(
        ["min", "max"]
    )
    if bool(((annual_station_spread["max"] / annual_station_spread["min"]) > 1.5).any()):
        flags.append("STATION_ANNUAL_PRECIPITATION_SPREAD_EXCEEDS_50_PERCENT")
    report = {
        "module_id": "DQ-CONTROL-HYDROLOGY-04",
        "status": "REVIEW_REQUIRED" if flags else "PASSED",
        "years": years,
        "control_area": {
            "config_id": area_config["config_id"],
            "observation_area_km2": observation_area_km2,
            "model_contributing_area_km2": float(control_grid_areas.sum()),
            "contributing_grid_count": int((control_grid_areas > 0.0).sum()),
        },
        "source_comparison": comparison,
        "annual": annual_rows,
        "flags": flags,
        "outputs": {
            "annual": str(output / "annual_hydrology_audit.csv"),
            "monthly": str(output / "monthly_hydrology_audit.csv"),
            "largest_hourly_changes": str(output / "largest_hourly_flow_changes.csv"),
            "station_year_precipitation": str(
                output / "station_year_precipitation_audit.csv"
            ),
        },
    }
    report_path = output / "control_section_hydrology_audit_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-flow", required=True)
    parser.add_argument("--standard-flow", required=True)
    parser.add_argument("--standard-sheet", default="逐小时水位流量")
    parser.add_argument("--precip-dir", required=True)
    parser.add_argument("--station-dir", required=True)
    parser.add_argument("--years", nargs="+", type=int, default=[2014, 2017])
    parser.add_argument(
        "--grid-static",
        default=str(ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"),
    )
    parser.add_argument(
        "--grid-mapping",
        default=str(ROOT / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"),
    )
    parser.add_argument(
        "--topology",
        default=str(ROOT / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"),
    )
    parser.add_argument(
        "--control-points",
        default=str(ROOT / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"),
    )
    parser.add_argument(
        "--control-area-config",
        default=str(ROOT / "config" / "control_section_area_xufan.json"),
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
