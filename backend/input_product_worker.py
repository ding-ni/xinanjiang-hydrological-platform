"""Isolated input processing, using shipped IDW/QC without changing sources."""
from __future__ import annotations

import argparse
import copy
import csv
import sys
import uuid
from pathlib import Path

from common import MODEL, RUNTIME, clean, now, read_json, write_json
from input_products import (InputProducts, DEFAULT_BUNDLE_VERSION_ID, TIME_CONVENTION,
                            DATA_TASK_ID, file_sha256, manifest_digest, processing_window)


def process_task(task_folder, products=None):
    """Build a product only; successful task completion does not register it."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    from pyproj import Transformer

    products = InputProducts() if products is None else products
    task_folder = Path(task_folder).resolve()
    task_id = task_folder.name
    if not DATA_TASK_ID.fullmatch(task_id) or task_folder.parent != products.tasks_root.resolve():
        raise ValueError("输入处理任务目录须为登记的 runtime/input-tasks/DATA-* 目录")
    request, status = read_json(task_folder / "request.json"), read_json(task_folder / "status.json")
    if status.get("taskId") != task_id or status.get("status") not in {"queued", "running"}:
        raise ValueError("只有排队或运行中的输入处理任务可执行")
    # The worker re-normalizes all browser-controlled fields. Server-owned
    # frozen hashes remain binding and must never be silently refreshed.
    allowed = {"requestId", "operation", "projectId", "resolutionId", "name", "start", "end",
               "parentBundleVersionId", "sourceId", "idwPower", "minimumStations", "zeroOnlyPolicy"}
    normalized = products.normalize_processing_request({key: value for key, value in request.items() if key in allowed})
    if normalized != request:
        raise ValueError("处理请求、源资料或父版本已偏离冻结清单")
    parent = products.resolve(request["parentBundleVersionId"])
    first, last, hours = processing_window(request["start"], request["end"])
    times = pd.date_range(first, last, freq="h")
    code_root = str(MODEL / "code")
    if code_root not in sys.path:
        sys.path.insert(0, code_root)
    from runoff_platform.data.precipitation_idw import sanitize_station_year, idw_interpolate_hourly
    from runoff_platform.data.precipitation_quality import read_station_count
    from runoff_platform.data.event_quality import evaluate_event_quality
    from xaj_routing.control_area import contributing_grid_areas
    from xaj_routing.io import read_topology_json

    def progress(value, label):
        current = read_json(task_folder / "status.json")
        if current.get("status") not in {"queued", "running"}:
            raise RuntimeError("输入处理任务已取消或不再运行")
        write_json(task_folder / "progress.json", {"progress": value, "stageLabel": label, "updatedAt": now()})
        print(label, flush=True)

    progress(5, "核验冻结输入清单、源资料及网格")
    with parent["paths"]["grids"].open("r", encoding="utf-8-sig", newline="") as stream:
        grids = list(csv.DictReader(stream))
    grid_ids = [row["unit_id"] for row in grids]
    if not grids or len(set(grid_ids)) != len(grid_ids):
        raise ValueError("输入网格为空或编号重复")
    rows = np.array([int(row["array_row"]) for row in grids], dtype=int)
    columns = np.array([int(row["array_col"]) for row in grids], dtype=int)
    areas = contributing_grid_areas(parent["paths"]["mapping"], read_topology_json(parent["paths"]["topology"]),
                                    "R0020_UP", grid_ids)
    control_indices = np.flatnonzero(areas > 0)
    control_areas = areas[control_indices]
    quality_config = read_json(parent["paths"]["quality"])
    flow_frame = pd.read_csv(parent["paths"]["flow"])
    flow_times = pd.DatetimeIndex(pd.to_datetime(flow_frame["time"], errors="raise"))
    if flow_times.has_duplicates or flow_times.tz is not None:
        raise ValueError("固定出口流量时轴重复或包含未登记时区")
    flows = pd.to_numeric(flow_frame["flow_m3_s"], errors="coerce").to_numpy(dtype=float)
    observed = pd.Series(flows, index=flow_times).where(np.isfinite(flows)).sort_index()
    product_id = "INPUT-" + uuid.uuid4().hex
    product_folder = products.products_root / product_id
    product_folder.mkdir(parents=True, exist_ok=False)
    precipitation_directory = product_folder / "precipitation"
    precipitation_directory.mkdir()
    forcing_version = "user-input-" + product_id.removeprefix("INPUT-")
    source, station_values, station_counts, station_summaries = None, None, None, []
    negative_source_count = 0
    if request["operation"] == "stationIdw":
        source = products.source(request["sourceId"])
        source_data = read_json(source["path"])
        source_times = pd.DatetimeIndex(pd.to_datetime(source_data["times"]))
        index = source_times.get_indexer(times)
        if np.any(index < 0):
            raise ValueError("源站点资料未完整覆盖处理窗口")
        raw_values = np.array([row["values"] for row in source_data["stations"]], dtype=float).T[index]
        if np.any(np.abs(raw_values[np.isfinite(raw_values)]) > np.finfo(np.float32).max):
            raise ValueError("源站点值超出逐时 NetCDF float32 可表达范围；不能生成无穷大输入")
        negative_source_count = int(np.count_nonzero(raw_values < 0))
        station_values, summaries = sanitize_station_year(
            raw_values, [row["id"] for row in source_data["stations"]],
            zero_only_as_missing=request["zeroOnlyPolicy"] == "ANNUAL_RECLASSIFY")
        station_summaries = [clean(summary.__dict__) for summary in summaries]
    grid_values = np.full((hours, len(grids)), np.nan, dtype=np.float32)
    counts = np.full(hours, -1, dtype=np.int16)
    source_count_fields, generated_files, negative_grid_count = set(), [], 0
    coordinate_signature = None
    years = list(range(first.year, last.year + 1))
    for position, year in enumerate(years):
        progress(10 + int(45 * position / len(years)), f"生成 {year} 年独立逐时网格输入")
        template_path = parent["precipitationDir"] / f"nananxi_precip_hourly_1km_{year}.nc"
        with xr.open_dataset(template_path, engine="h5netcdf") as template:
            if template["precipitation"].dims != ("time", "lat", "lon") or template["precipitation"].attrs.get("units") != "mm":
                raise ValueError("模板须为 time/lat/lon 逐小时降雨量 mm")
            latitudes = np.asarray(template["lat"].values, dtype=float)
            longitudes = np.asarray(template["lon"].values, dtype=float)
            signature = (latitudes.tolist(), longitudes.tolist())
            if coordinate_signature is not None and signature != coordinate_signature:
                raise ValueError("跨年输入网格坐标不一致")
            coordinate_signature = signature
            if np.any(rows < 0) or np.any(columns < 0) or rows.max() >= len(latitudes) or columns.max() >= len(longitudes):
                raise ValueError("注册网格数组行列越界")
            year_mask = times.year == year
            selected_times = times[year_mask]
            subset = template.sel(time=slice(selected_times[0], selected_times[-1]))
            actual = pd.DatetimeIndex(pd.to_datetime(subset["time"].values))
            if not actual.equals(selected_times):
                raise ValueError("父产品未覆盖完整、唯一的逐时处理窗口")
            if source is None:
                values = np.asarray(subset["precipitation"].values, dtype=np.float32)
                extracted = np.stack([values[:, row, col] for row, col in zip(rows, columns)], axis=1)
                negative_grid_count += int(np.count_nonzero(extracted < 0))
                extracted = np.maximum(extracted, 0.0)
                year_counts, count_field = read_station_count(subset)
                if count_field:
                    source_count_fields.add(count_field)
                if year_counts.shape != (len(selected_times),):
                    raise ValueError("父产品站数字段与时轴不一致")
            else:
                transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
                sx, sy = transformer.transform([row["lon"] for row in source_data["stations"]],
                                              [row["lat"] for row in source_data["stations"]])
                tx, ty = transformer.transform(longitudes[columns], latitudes[rows])
                if not all(np.isfinite(value).all() for value in (np.asarray(sx), np.asarray(sy), np.asarray(tx), np.asarray(ty))):
                    raise ValueError("站点或网格坐标无法在 UTM 50N 有效投影")
                extracted, year_counts = idw_interpolate_hourly(station_values[year_mask], np.asarray(sx), np.asarray(sy),
                                                              np.asarray(tx), np.asarray(ty), power=request["idwPower"],
                                                              minimum_stations=request["minimumStations"])
                source_count_fields.add("source_station_count")
            corrected = np.zeros((len(selected_times), len(latitudes), len(longitudes)), dtype=np.float32)
            corrected[:, rows, columns] = extracted.astype(np.float32)
            grid_values[year_mask] = extracted
            counts[year_mask] = year_counts
            precipitation_attributes = {key: value for key, value in template["precipitation"].attrs.items()
                                        if key in {"standard_name", "units", "cell_methods"}}
            precipitation_attributes.update(long_name="diagnostic hourly input precipitation amount",
                                            ancillary_variables="source_station_count precipitation_quality_code")
        dataset = xr.Dataset(
            {"precipitation": (("time", "lat", "lon"), corrected),
             "source_station_count": (("time",), np.asarray(year_counts, dtype=np.int16)),
             "precipitation_quality_code": (("time",), np.where(year_counts <= 0, 0, np.where(year_counts == 1, 1, 2)).astype(np.int8))},
            coords={"time": selected_times, "lat": latitudes, "lon": longitudes},
            attrs={"Conventions": "CF-1.10", "project_id": "nananxi-xufan", "bundle_version_id": product_id,
                   "forcing_version": forcing_version, "time_convention": TIME_CONVENTION,
                   "source_class": "USER_DECLARED_NOT_INDEPENDENTLY_VERIFIED" if source else "SUBSET_OF_REGISTERED_QC_PARENT",
                   "history": "isolated diagnostic product using unchanged shipped IDW/QC",
                   "comment": "Basin-outside cells are 0; missing source stays NaN inside; no production clearance"})
        dataset["precipitation"].attrs = precipitation_attributes
        dataset["source_station_count"].attrs = {"units": "1", "long_name": "finite source station count; -1 means unavailable"}
        dataset["precipitation_quality_code"].attrs = {"units": "1", "meanings": "0=none_or_unknown 1=single 2=multiple"}
        output = precipitation_directory / template_path.name
        dataset.to_netcdf(output, engine="h5netcdf", encoding={
            "precipitation": {"zlib": True, "complevel": 4, "dtype": "float32"},
            "source_station_count": {"zlib": True, "complevel": 4, "dtype": "int16"},
            "precipitation_quality_code": {"zlib": True, "complevel": 4, "dtype": "int8"}})
        dataset.close()
        generated_files.append(output)
    progress(65, "重算当前控制区雨量与登记场次质量，不继承旧雨量统计")
    valid_hours = np.isfinite(grid_values[:, control_indices]).all(axis=1)
    areal_rainfall = np.full(hours, np.nan)
    areal_rainfall[valid_hours] = np.average(grid_values[valid_hours][:, control_indices], axis=1, weights=control_areas)
    parent_catalog = read_json(parent["paths"]["events"])
    rebuilt_events, event_quality = [], []
    for item in parent_catalog.get("packages", []):
        start, end = pd.Timestamp(item["start"]), pd.Timestamp(item["end"])
        if start < times[0] or end > times[-1]:
            continue
        event_mask = (times >= start) & (times <= end)
        audit_start = start - pd.Timedelta(hours=168)
        audit_mask = (times >= audit_start) & (times <= end)
        quality = evaluate_event_quality(event_id=item["eventId"], start=start, end=end, times=times[audit_mask],
                                         grid_precipitation_mm=grid_values[audit_mask][:, control_indices],
                                         grid_areas_km2=control_areas, observed_flow_cms=observed,
                                         source_station_count=counts[audit_mask], config=quality_config)
        complete_warmup = bool(audit_start >= times[0] and valid_hours[audit_mask].all())
        if not complete_warmup:
            quality["exclusion_reasons"].append("WARMUP_INPUT_INCOMPLETE")
        if not valid_hours[event_mask].all() and "PRECIPITATION_INCOMPLETE" not in quality["exclusion_reasons"]:
            quality["exclusion_reasons"].append("PRECIPITATION_INCOMPLETE")
        quality.update(accepted=not quality["exclusion_reasons"], model_input_ready=complete_warmup and bool(valid_hours[event_mask].all()),
                       forcingVersion=forcing_version, controlAreaKm2=float(control_areas.sum()),
                       observationAreaKm2=float(quality_config["basin_area_km2"]))
        event_flows = observed.reindex(times[event_mask])
        flow_complete = float(event_flows.notna().mean())
        if flow_complete < 0.999:
            quality["exclusion_reasons"].append("FLOW_INCOMPLETE")
            quality["accepted"] = False
        valid_flow = event_flows.dropna()
        peak_time = valid_flow.idxmax() if not valid_flow.empty else None
        record = copy.deepcopy(item)
        record["observedInputs"] = {
            "rainDatasetId": forcing_version, "rainCompleteness": float(valid_hours[event_mask].mean()),
            "rainStatisticsSource": "CURRENT_CONTROL_GRID_NOT_RECONSTRUCTED_STATIONS",
            "reportingRainStations": None, "rainRecords": None,
            "riverRecords": int(valid_flow.size), "riverCompleteness": flow_complete,
            "observedPeakFlowM3s": float(valid_flow.max()) if valid_flow.size else None,
            "observedPeakTime": peak_time.isoformat() if peak_time is not None else None,
            "observedPeakLevelM": None,
        }
        event_counts = counts[event_mask]
        finite_counts = event_counts[event_counts >= 0]
        record["spatialRainInputs"] = {
            "gridBasinAverageTotalRainMm": float(np.sum(areal_rainfall[event_mask])) if valid_hours[event_mask].all() else None,
            "gridRainCompleteness": float(valid_hours[event_mask].mean()),
            "modelContributingAreaKm2": float(control_areas.sum()),
            "minimumReportingStationCount": int(finite_counts.min()) if finite_counts.size else None,
            "maximumReportingStationCount": int(finite_counts.max()) if finite_counts.size else None,
            "stationCountFields": sorted(source_count_fields),
        }
        record.update(status="ready" if quality["accepted"] else "review", quality=quality,
                      qcRevision={"forcingVersion": forcing_version, "parentBundleVersionId": parent["bundleVersionId"],
                                  "windowSource": "UNCHANGED_REGISTERED_WINDOW", "windowChanged": False})
        rebuilt_events.append(record)
        event_quality.append(quality)
    catalog = {"schemaVersion": "1.1", "generatedAt": now(), "count": len(rebuilt_events), "packages": rebuilt_events,
               "bundleVersionId": product_id,
               "controlSection": {"controlReachId": "R0020_UP", "observationAreaKm2": float(quality_config["basin_area_km2"]),
                                  "modelContributingAreaKm2": float(control_areas.sum())},
               "qcRevision": {"forcingVersion": forcing_version, "parentBundleVersionId": parent["bundleVersionId"],
                              "rainfallStatistics": "CURRENT_CONTROL_GRID_STRICT_COMPLETE_WEIGHTED_V2",
                              "flowStatistics": "FIXED_QC_FLOW_REFERENCE", "windowOverrideCount": 0}}
    events_path, quality_path = product_folder / "event-catalog.json", product_folder / "qc-report.json"
    write_json(events_path, catalog)
    summary = {"hours": hours, "gridCount": len(grids), "contributingGridCount": int(control_indices.size),
               "controlAreaKm2": float(control_areas.sum()), "observationAreaKm2": float(quality_config["basin_area_km2"]),
               "rainfallCompleteness": float(valid_hours.mean()),
               "flowCompleteness": float(observed.reindex(times).notna().mean()),
               "sourceStationCountAvailable": bool((counts >= 0).all()),
               "minimumReportingStationCount": int(counts[counts >= 0].min()) if np.any(counts >= 0) else None,
               "eventCount": len(rebuilt_events), "acceptedEventCount": sum(row["accepted"] for row in event_quality),
               "negativeSourceValuesReclassifiedMissing": negative_source_count,
               "negativeGridValuesClippedAsModel": negative_grid_count,
               "originalEventCount": len(parent_catalog.get("packages", [])),
               "eventsExcludedOutsideCoverage": len(parent_catalog.get("packages", [])) - len(rebuilt_events)}
    report = {"schemaVersion": "1.0", "bundleVersionId": product_id, "sourceTaskId": task_id,
              "productionEligible": False, "summary": summary, "stationSummaries": station_summaries,
              "eventQuality": event_quality, "zeroOnlyPolicy": request.get("zeroOnlyPolicy", "PARENT_QC_POLICY"),
              "missingValuePolicy": "NO_ZERO_FILL_NO_RENORMALIZED_CONTROL_AREA", "timeConvention": TIME_CONVENTION,
              "kernelReferences": ["runoff_platform.data.precipitation_idw.sanitize_station_year",
                                   "runoff_platform.data.precipitation_idw.idw_interpolate_hourly",
                                   "runoff_platform.data.event_quality.evaluate_event_quality"]}
    write_json(quality_path, report)
    progress(85, "冻结文件摘要与不可变输入产品清单")
    paths = {role: products.reference(path) for role, path in parent["paths"].items()}
    paths["events"] = products.reference(events_path)
    file_paths = list(dict.fromkeys([path for role, path in parent["paths"].items() if role != "events"] +
                                   generated_files + [events_path, quality_path]))
    if source:
        file_paths.extend([source["path"], source["manifestPath"]])
    source_origin = source["manifest"].get("sourceOrigin") if source else None
    database_source = isinstance(source_origin, dict) and source_origin.get("type") == "hydrology-database"
    source_label = ("水文资料库实测雨量经检查和距离反比加权插值生成；保留原始年资料、站点及时段记录"
                    if database_source else "登记站点雨量经检查和距离反比加权插值生成；来源说明随产品保存"
                    if source else "已登记网格降雨的独立时段；原产品保持不变")
    manifest = {"schemaVersion": "1.0", "bundleVersionId": product_id, "projectId": "nananxi-xufan", "resolutionId": "1km",
                "name": request["name"], "operation": request["operation"], "createdAt": now(),
                "sourceTaskId": task_id, "requestSHA256": manifest_digest(request),
                "parentBundleVersionId": parent["bundleVersionId"], "parentManifestSHA256": parent["manifestSHA256"],
                "sourceType": "model-qc", "sourceLabel": source_label, "timeConvention": TIME_CONVENTION,
                "forcingVersion": forcing_version, "observationVersion": parent["observationVersion"],
                "modelInputVersion": product_id, "productionEligible": False,
                "coverage": {"start": times[0].isoformat(), "end": times[-1].isoformat(), "hours": hours,
                             "years": sorted(set(times.year.tolist()))},
                "summary": summary, "qualityNotes": [source_label, "缺测不补零；不把子窗口全零当作年度失效。",
                                                      "模型评价沿用已登记徐畈出口整编流量；资料库实测水位和流量另按站点查询。",
                                                      "场次只保留完全位于覆盖内的原登记窗口，168h暖机不足会标记审核。",
                                                      "全部产品仅供本机诊断；生产资格未验收。"],
                "paths": paths, "precipitationDirectory": products.reference(precipitation_directory),
                "files": [products.file_record(path) for path in file_paths]}
    if source:
        manifest.update(sourceId=source["sourceId"], sourceSHA256=source["sourceSHA256"],
                        sourceManifestSHA256=source["manifestSHA256"])
        if source_origin is not None:
            manifest["sourceOrigin"] = copy.deepcopy(source_origin)
    manifest_path = product_folder / "manifest.json"
    write_json(manifest_path, manifest)
    digest = file_sha256(manifest_path)
    products._descriptor(manifest, digest, manifest_path, verify=True)
    public = products._public(manifest, digest)
    public["registered"] = False
    result = {"taskId": task_id, "resultVersionId": "DATA-RESULT-" + task_id,
              "productVersionId": product_id, "bundleVersionId": product_id, "manifestSHA256": digest,
              "resultSource": "input-processing", "productionEligible": False,
              "lineage": {"parentBundleVersionId": parent["bundleVersionId"], "parentManifestSHA256": parent["manifestSHA256"],
                          "sourceId": request.get("sourceId"), "sourceSHA256": request.get("sourceSHA256")},
              "product": public, "summary": summary}
    progress(100, "输入产品处理完成；等待显式登记（没有自动切换）")
    write_json(task_folder / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    process_task(Path(args.task))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
