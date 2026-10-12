"""Unified callable service for precipitation and flood-event data quality."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from runoff_platform.data.event_quality import evaluate_event_quality
from runoff_platform.data.precipitation_idw import rebuild_grid_year
from runoff_platform.data.precipitation_quality import read_station_count
from state_initialization import STATE_MODULE_ID, run_state_initialization_task


PRECIPITATION_MODULE_ID = "DQ-PRECIP-IDW-01"
EVENT_QUALITY_MODULE_ID = "DQ-EVENT-QC-02"


class DataQualityRequestError(ValueError):
    pass


def get_data_quality_catalog() -> dict[str, Any]:
    """Return frontend-readable module metadata and dependency order."""
    return {
        "catalog_version": "1.0",
        "modules": [
            {
                "module_id": PRECIPITATION_MODULE_ID,
                "name": "Precipitation station QC and 1 km IDW rebuild",
                "name_zh": "站点降雨质控与1公里IDW重建",
                "status": "READY",
                "depends_on": [],
                "run_function": "data_quality_platform.run_data_quality_task",
                "request_schema": "data_interface/precipitation_preprocessing_schema.json",
                "outputs": ["gridded NetCDF", "precipitation_qc_summary.csv", "precipitation_qc_report.json"],
            },
            {
                "module_id": EVENT_QUALITY_MODULE_ID,
                "name": "Flood-event physical quality audit",
                "name_zh": "洪水场次物理质量审计",
                "status": "READY",
                "depends_on": [PRECIPITATION_MODULE_ID],
                "run_function": "data_quality_platform.run_data_quality_task",
                "request_schema": "data_interface/event_quality_schema.json",
                "outputs": ["event_quality.csv", "accepted_events.csv", "excluded_events.csv", "event_quality_report.json"],
            },
            {
                "module_id": STATE_MODULE_ID,
                "name": "Runoff, groundwater and routing state initialization",
                "name_zh": "产流、地下水与汇流初始状态",
                "status": "READY",
                "depends_on": [EVENT_QUALITY_MODULE_ID],
                "run_function": "data_quality_platform.run_data_quality_task",
                "request_schema": "data_interface/state_initialization_schema.json",
                "outputs": ["versioned land/routing state checkpoint"],
            },
            {
                "module_id": "RUNOFF-XAJ-04",
                "name": "Grid XAJ runoff simulation",
                "name_zh": "网格新安江产流计算",
                "status": "READY",
                "depends_on": [STATE_MODULE_ID],
                "run_function": "runoff_platform.api.run_platform_request",
                "request_schema": "data_interface/examples/example_request_land.json",
                "outputs": ["RS", "RI", "RG", "final_state", "water_balance_error"],
            },
            {
                "module_id": "ROUTING-05",
                "name": "Slope and channel routing",
                "name_zh": "坡面与河网汇流",
                "status": "READY",
                "depends_on": ["RUNOFF-XAJ-04"],
                "run_function": "xaj_routing.engine.RoutingEngine.run",
                "request_schema": "data_interface/handoff_schema.json",
                "outputs": ["control flow", "outlet flow", "routing final state"],
            },
            {
                "module_id": "MULTI-EVENT-CALIBRATION-06",
                "name": "Random-split multi-event calibration",
                "name_zh": "随机划分多场洪水率定",
                "status": "READY_ASYNC",
                "depends_on": [PRECIPITATION_MODULE_ID, EVENT_QUALITY_MODULE_ID, STATE_MODULE_ID, "RUNOFF-XAJ-04", "ROUTING-05"],
                "run_function": "calibration_platform.FormalCalibrationTaskManager.submit",
                "request_schema": "data_interface/formal_calibration_task_schema.json",
                "outputs": ["common parameters", "event metrics", "group metrics", "calibration history"],
            },
            {
                "module_id": "CAL-WLM-BOUNDARY-07",
                "name": "WLM search-boundary experiment",
                "name_zh": "WLM参数边界检验",
                "status": "READY",
                "depends_on": ["MULTI-EVENT-CALIBRATION-06"],
                "run_function": "calibration_platform.evaluate_wlm_boundary_experiment",
                "request_schema": "data_interface/production_calibration_workflow_schema.json",
                "outputs": ["boundary resolution", "selected search space", "comparison metrics"],
            },
            {
                "module_id": "CAL-MULTI-SEED-08",
                "name": "Multi-seed optimizer stability",
                "name_zh": "多随机种子稳定性检验",
                "status": "READY",
                "depends_on": ["CAL-WLM-BOUNDARY-07"],
                "run_function": "calibration_platform.evaluate_multi_seed_stability",
                "request_schema": "data_interface/production_calibration_workflow_schema.json",
                "outputs": ["parameter stability", "metric stability", "representative run"],
            },
            {
                "module_id": "CAL-CONTINUOUS-BALANCE-09",
                "name": "Continuous annual water-balance validation",
                "name_zh": "全年连续水量平衡检验",
                "status": "READY",
                "depends_on": ["CAL-MULTI-SEED-08", "RUNOFF-XAJ-04", "ROUTING-05"],
                "run_function": "scripts.run_continuous_water_balance.run",
                "request_schema": "config/continuous_water_balance_config.json",
                "outputs": ["annual water balance", "continuous metrics", "state carry-over audit"],
            },
            {
                "module_id": "PRODUCTION-CALIBRATION-10",
                "name": "Production parameter acceptance workflow",
                "name_zh": "生产参数完整验收工作流",
                "status": "READY_ASYNC",
                "depends_on": ["CAL-WLM-BOUNDARY-07", "CAL-MULTI-SEED-08", "CAL-CONTINUOUS-BALANCE-09"],
                "run_function": "calibration_platform.ProductionCalibrationTaskManager.submit",
                "request_schema": "data_interface/production_calibration_workflow_schema.json",
                "outputs": ["production gate report", "approved or review parameter set", "workflow archive"],
            },
        ],
        "recommended_pipeline": [
            PRECIPITATION_MODULE_ID,
            EVENT_QUALITY_MODULE_ID,
            STATE_MODULE_ID,
            "RUNOFF-XAJ-04",
            "ROUTING-05",
            "MULTI-EVENT-CALIBRATION-06",
            "CAL-WLM-BOUNDARY-07",
            "CAL-MULTI-SEED-08",
            "CAL-CONTINUOUS-BALANCE-09",
            "PRODUCTION-CALIBRATION-10",
        ],
        "planned_modules": [],
    }


def _resolve(value: str, project_root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _load_grid_cells(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


# [DQ-PRECIP-IDW-01] Callable precipitation preprocessing task.
def _run_precipitation_task(payload: dict[str, Any], project_root: Path) -> dict[str, Any]:
    required = {"station_dir", "template_grid_dir", "output_dir", "grid_static", "start_year", "end_year"}
    missing = sorted(required - payload.keys())
    if missing:
        raise DataQualityRequestError(f"missing precipitation task fields: {missing}")
    station_dir = _resolve(payload["station_dir"], project_root)
    template_dir = _resolve(payload["template_grid_dir"], project_root)
    output_dir = _resolve(payload["output_dir"], project_root)
    grid_cells = _load_grid_cells(_resolve(payload["grid_static"], project_root))
    output_dir.mkdir(parents=True, exist_ok=True)
    policy = str(payload.get("zero_only_station_year_policy", "RECLASSIFY_AS_MISSING")).upper()
    reports = []
    for year in range(int(payload["start_year"]), int(payload["end_year"]) + 1):
        station_path = station_dir / f"nananxi_station_precip_hourly_{year}.nc"
        template_path = template_dir / f"nananxi_precip_hourly_1km_{year}.nc"
        if not station_path.exists() or not template_path.exists():
            continue
        reports.append(
            rebuild_grid_year(
                station_path,
                template_path,
                output_dir / template_path.name,
                grid_cells,
                power=float(payload.get("idw_power", 2.0)),
                minimum_stations=int(payload.get("minimum_stations", 1)),
                zero_only_as_missing=policy == "RECLASSIFY_AS_MISSING",
                overwrite=bool(payload.get("overwrite", False)),
            )
        )
    summary_rows = [{key: value for key, value in report.items() if key != "station_summaries"} for report in reports]
    summary_path = output_dir / "precipitation_qc_summary.csv"
    report_path = output_dir / "precipitation_qc_report.json"
    pd.DataFrame(summary_rows).to_csv(summary_path, index=False, encoding="utf-8-sig")
    report_payload = {
        "module_id": PRECIPITATION_MODULE_ID,
        "status": "SUCCEEDED",
        "year_count": len(reports),
        "policy": {
            "zero_only_station_year": policy,
            "idw_power": float(payload.get("idw_power", 2.0)),
            "minimum_stations": int(payload.get("minimum_stations", 1)),
            "source_files_overwritten": False,
        },
        "years": reports,
    }
    report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "module_id": PRECIPITATION_MODULE_ID,
        "task_id": str(payload.get("task_id", "PRECIP-LOCAL")),
        "status": "SUCCEEDED",
        "summary": {"year_count": len(reports)},
        "output_files": {
            "precipitation_directory": str(output_dir),
            "qc_summary": str(summary_path),
            "qc_report": str(report_path),
        },
    }


def _read_observed_flow(payload: dict[str, Any], project_root: Path) -> pd.Series:
    path = _resolve(payload["observed_flow"], project_root)
    time_column = payload.get("observed_time_column")
    flow_column = payload.get("observed_flow_column")
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    elif path.suffix.lower() in {".xlsx", ".xls"}:
        sheet_name = payload.get("observed_sheet")
        if sheet_name is None:
            workbook = pd.ExcelFile(path)
            sheet_name = next((name for name in workbook.sheet_names if "流量" in name), workbook.sheet_names[-1])
        frame = pd.read_excel(path, sheet_name=sheet_name)
    else:
        raise DataQualityRequestError("observed_flow must be CSV or Excel")
    if time_column is None:
        time_column = frame.columns[0]
    if flow_column is None:
        flow_column = next((name for name in frame.columns if "流量" in str(name)), frame.columns[2])
    series = pd.DataFrame(
        {
            "time": pd.to_datetime(frame[time_column], errors="coerce"),
            "flow": pd.to_numeric(frame[flow_column], errors="coerce"),
        }
    ).dropna().drop_duplicates("time").set_index("time").sort_index()["flow"]
    if series.empty:
        raise DataQualityRequestError("observed flow contains no valid rows")
    return series


def _metadata_exclusions(item: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    start = pd.Timestamp(item["start"])
    end = pd.Timestamp(item["end"])
    reasons = []
    allowed_ranges = payload.get("allowed_year_ranges", [])
    if len(allowed_ranges) == 2 and all(isinstance(value, (int, float, str)) for value in allowed_ranges):
        allowed_ranges = [allowed_ranges]
    if allowed_ranges and not any(int(first) <= start.year <= int(last) for first, last in allowed_ranges):
        reasons.append("YEAR_OUTSIDE_ALLOWED_RANGES")
    if end.year != start.year:
        reasons.append("CROSS_YEAR_EVENT")
    observed = item.get("observedInputs", {})
    if float(observed.get("rainCompleteness", 0.0)) < float(payload.get("minimum_catalog_rain_completeness", 0.999)):
        reasons.append("CATALOG_RAIN_INCOMPLETE")
    if float(observed.get("riverCompleteness", 0.0)) < float(payload.get("minimum_catalog_flow_completeness", 0.999)):
        reasons.append("CATALOG_FLOW_INCOMPLETE")
    return reasons


# [DQ-EVENT-QC-02] Callable event audit task executed before random splitting.
def _run_event_quality_task(payload: dict[str, Any], project_root: Path) -> dict[str, Any]:
    required = {"event_catalog", "precipitation_directory", "observed_flow", "grid_static", "quality_config", "output_dir"}
    missing = sorted(required - payload.keys())
    if missing:
        raise DataQualityRequestError(f"missing event quality task fields: {missing}")
    catalog = json.loads(_resolve(payload["event_catalog"], project_root).read_text(encoding="utf-8-sig"))
    precipitation_dir = _resolve(payload["precipitation_directory"], project_root)
    grid_cells = _load_grid_cells(_resolve(payload["grid_static"], project_root))
    quality_config = json.loads(_resolve(payload["quality_config"], project_root).read_text(encoding="utf-8-sig"))
    observed = _read_observed_flow(payload, project_root)
    output_dir = _resolve(payload["output_dir"], project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    warmup_hours = int(payload.get("warmup_hours", 168))
    positions = [(int(cell["array_row"]), int(cell["array_col"])) for cell in grid_cells]
    areas = np.asarray([float(cell["area_km2"]) for cell in grid_cells], dtype=float)
    cache: dict[int, tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]] = {}

    def load_year(year: int) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
        if year in cache:
            return cache[year]
        path = precipitation_dir / f"nananxi_precip_hourly_1km_{year}.nc"
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
            source = np.asarray(dataset["precipitation"].values, dtype=np.float32)
            values = np.stack([source[:, row, column] for row, column in positions], axis=1)
            station_count, _ = read_station_count(dataset)
        cache[year] = (times, values, station_count)
        return cache[year]

    rows = []
    for item in catalog.get("packages", []):
        event_id = str(item.get("eventId"))
        start = pd.Timestamp(item["start"])
        end = pd.Timestamp(item["end"])
        metadata_reasons = _metadata_exclusions(item, payload)
        if metadata_reasons:
            rows.append({
                "event_id": event_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "accepted": False,
                "exclusion_reasons": metadata_reasons,
                "warning_reasons": [],
                "quality_stage": "METADATA",
            })
            continue
        segment_start = start - pd.Timedelta(hours=warmup_hours)
        time_parts = []
        precipitation_parts = []
        station_count_parts = []
        missing_file = False
        for year in range(segment_start.year, end.year + 1):
            try:
                year_times, year_values, year_station_count = load_year(year)
            except FileNotFoundError:
                missing_file = True
                break
            mask = (year_times >= segment_start) & (year_times <= end)
            time_parts.append(year_times[mask])
            precipitation_parts.append(year_values[mask])
            station_count_parts.append(year_station_count[mask])
        if missing_file or not time_parts:
            rows.append({
                "event_id": event_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "accepted": False,
                "exclusion_reasons": ["PRECIPITATION_FILE_MISSING"],
                "warning_reasons": [],
                "quality_stage": "PHYSICAL",
            })
            continue
        times = time_parts[0].append(time_parts[1:])
        precipitation = np.concatenate(precipitation_parts)
        station_count = np.concatenate(station_count_parts)
        result = evaluate_event_quality(
            event_id=event_id,
            start=start,
            end=end,
            times=times,
            grid_precipitation_mm=precipitation,
            grid_areas_km2=areas,
            observed_flow_cms=observed,
            source_station_count=station_count,
            config=quality_config,
        )
        result["quality_stage"] = "PHYSICAL"
        rows.append(result)

    exclusion_counts = Counter(reason for row in rows for reason in row["exclusion_reasons"])
    warning_counts = Counter(reason for row in rows for reason in row["warning_reasons"])
    export_rows = [{**row, "exclusion_reasons": ";".join(row["exclusion_reasons"]), "warning_reasons": ";".join(row["warning_reasons"])} for row in rows]
    frame = pd.DataFrame(export_rows)
    all_path = output_dir / "event_quality.csv"
    accepted_path = output_dir / "accepted_events.csv"
    excluded_path = output_dir / "excluded_events.csv"
    report_path = output_dir / "event_quality_report.json"
    frame.to_csv(all_path, index=False, encoding="utf-8-sig")
    frame[frame["accepted"] == True].to_csv(accepted_path, index=False, encoding="utf-8-sig")  # noqa: E712
    frame[frame["accepted"] == False].to_csv(excluded_path, index=False, encoding="utf-8-sig")  # noqa: E712
    accepted_count = int(frame["accepted"].sum())
    report = {
        "module_id": EVENT_QUALITY_MODULE_ID,
        "status": "SUCCEEDED",
        "evaluated_count": int(len(frame)),
        "accepted_count": accepted_count,
        "excluded_count": int(len(frame) - accepted_count),
        "exclusion_reason_counts": dict(exclusion_counts),
        "warning_reason_counts": dict(warning_counts),
        "output_files": {
            "all": str(all_path),
            "accepted": str(accepted_path),
            "excluded": str(excluded_path),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "module_id": EVENT_QUALITY_MODULE_ID,
        "task_id": str(payload.get("task_id", "EVENT-QC-LOCAL")),
        "status": "SUCCEEDED",
        "summary": {key: report[key] for key in ("evaluated_count", "accepted_count", "excluded_count", "exclusion_reason_counts", "warning_reason_counts")},
        "output_files": {**report["output_files"], "report": str(report_path)},
    }


def run_data_quality_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one JSON-compatible request by stable module_id."""
    module_id = str(payload.get("module_id", "")).upper()
    project_root = Path(payload.get("project_root", ".")).resolve()
    if module_id == PRECIPITATION_MODULE_ID:
        return _run_precipitation_task(payload, project_root)
    if module_id == EVENT_QUALITY_MODULE_ID:
        return _run_event_quality_task(payload, project_root)
    if module_id == STATE_MODULE_ID:
        return run_state_initialization_task(payload)
    raise DataQualityRequestError(f"unsupported module_id: {module_id}")
