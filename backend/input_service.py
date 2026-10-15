"""Read-only, registered QC inputs shared by the unified UI and model worker.

No delivery API, historical simulated result, browser-supplied path, interpolation
or mutable data-source registration is used here. The grid order, clipping policy
and control-area weights match the current worker's control-split-v2 input chain.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from common import DEFAULT_INPUT_BUNDLE, MODEL, RUNTIME, clean, read_json
from observation_quality import evaluate_observation_coverage


PROJECT_ID = "nananxi-xufan"
RESOLUTION_ID = "1km"
CASE_KEY = f"{PROJECT_ID}:{RESOLUTION_ID}"
FORCING_VERSION = "precipitation_qc_20260819"
OBSERVATION_VERSION = "xufan_outlet_hourly_qc_20260819"
CONTROL_REACH_ID = "R0020_UP"
MAX_WINDOW_HOURS = 744
MAX_WARMUP_HOURS = 365 * 24
MAX_EVENT_WINDOW_HOURS = 366 * 24 + 1200
HOURLY_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:00(?::00)?$")
EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,100}$")


class InputService:
    """Lazy and thread-safe input reader; constructor does not load science DLLs."""

    def __init__(self, model_root=None, bundle=None):
        self.model_root = Path(MODEL if model_root is None else model_root).resolve()
        self.bundle = bundle or {}
        self.bundle_version = self.bundle.get("bundleVersionId", DEFAULT_INPUT_BUNDLE)
        self.bundle_digest = self.bundle.get("manifestSHA256")
        self.forcing_version = self.bundle.get("forcingVersion", FORCING_VERSION)
        self.observation_version = self.bundle.get("observationVersion", OBSERVATION_VERSION)
        self.model_input_version = self.bundle.get("modelInputVersion", "nananxi-qc20260819-control-split-v2")
        self._lock = threading.RLock()
        self._initialized = False
        self._spatial_initialized = False
        self._hashes = {}
        self._catalog_cache = None
        self.paths = {
            "grids": self.model_root / "data_interface/runoff_2017/grid_static.csv",
            "geometry": self.model_root / "data_interface/spatial/南安溪1公里有效格点.geojson",
            "topology": self.model_root / "data_interface/spatial/nan_an_topology_control_split_v2.json",
            "mapping": self.model_root / "data_interface/spatial/grid_to_unit_weight_control_split_v2.csv",
            "flow": self.model_root / "data_interface/observations/xufan_outlet_hourly_qc.csv",
            "events": self.model_root / "data_interface/event_catalog/nananxi_flood_event_catalog_qc_20260819.json",
            "quality": self.model_root / "config/event_quality_config.json",
        }
        self.precipitation_dir = self.model_root / "data_interface/precipitation_qc_20260819/1km_hourly"
        if bundle:
            if set(self.paths).difference(bundle.get("paths", {})):
                raise ValueError("登记输入产品缺少必需来源路径")
            self.paths = {key: Path(bundle["paths"][key]) for key in self.paths}
            self.precipitation_dir = Path(bundle["precipitationDir"])

    @staticmethod
    def _case(project_id, resolution_id):
        if project_id != PROJECT_ID or resolution_id != RESOLUTION_ID:
            raise ValueError("只读 QC 输入仅登记了 nananxi-xufan:1km；不得套用其他流域或尺度")

    @staticmethod
    def _time(value):
        if not isinstance(value, str) or not HOURLY_ISO.fullmatch(value):
            raise ValueError("时刻须为无时区的源资料整点 ISO 时间，例如 2017-06-19T11:00")
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("无效的输入资料时刻") from exc

    def _initialize_spatial(self):
        if self._spatial_initialized:
            return
        import numpy as np
        import pandas as pd
        import xarray as xr

        # Use only the shipped, unchanged shared helpers; never import the old
        # IntegratedResultsService or the task worker (which writes artifacts).
        code_root = str(MODEL / "code")
        if code_root not in sys.path:
            sys.path.insert(0, code_root)
        from runoff_platform.data.event_quality import evaluate_event_quality
        from runoff_platform.data.precipitation_quality import read_station_count
        from xaj_routing.control_area import contributing_grid_areas
        from xaj_routing.io import read_topology_json

        self.np, self.pd, self.xr = np, pd, xr
        self._evaluate_event_quality = evaluate_event_quality
        self._read_station_count = read_station_count
        with self.paths["grids"].open("r", encoding="utf-8-sig", newline="") as stream:
            self.grids = list(csv.DictReader(stream))
        self.grid_ids = [row["unit_id"] for row in self.grids]
        if not self.grids or len(set(self.grid_ids)) != len(self.grid_ids):
            raise ValueError("注册网格编号为空或重复")
        self.areas = contributing_grid_areas(
            self.paths["mapping"], read_topology_json(self.paths["topology"]),
            CONTROL_REACH_ID, self.grid_ids,
        )
        self.control_indexes = np.flatnonzero(self.areas > 0)
        self.control_area = float(self.areas.sum())
        self.project_area = sum(float(row["area_km2"]) for row in self.grids)
        geometry = read_json(self.paths["geometry"])
        by_position = {(int(feature["properties"]["row"]), int(feature["properties"]["col"])):
                       feature["geometry"]["coordinates"][0] for feature in geometry["features"]}
        self.cells = []
        for index, row in enumerate(self.grids):
            position = (int(row["source_row"]), int(row["source_col"]))
            if position not in by_position:
                raise ValueError(f"注册网格 {row['unit_id']} 缺少源行列几何")
            self.cells.append({"id": row["unit_id"], "row": position[0], "col": position[1],
                               "bounds": by_position[position], "areaKm2": float(row["area_km2"]),
                               "controlAreaKm2": float(self.areas[index]),
                               "controlWeight": float(self.areas[index] / self.control_area)})
        self._spatial_initialized = True

    def _initialize(self):
        if self._initialized:
            return
        self._initialize_spatial()
        np, pd = self.np, self.pd
        frame = pd.read_csv(self.paths["flow"])
        flow_times = pd.DatetimeIndex(pd.to_datetime(frame["time"], errors="raise"))
        if flow_times.has_duplicates or flow_times.tz is not None:
            raise ValueError("注册 QC 流量时刻重复或包含未登记时区")
        flows = pd.to_numeric(frame["flow_m3_s"], errors="coerce").to_numpy(dtype=float)
        self.observed = pd.Series(flows, index=flow_times).sort_index()
        self.observed = self.observed.where(np.isfinite(self.observed) & (self.observed >= 0))
        if self.observed.dropna().empty:
            raise ValueError("注册 QC 流量为空")
        self.event_catalog = read_json(self.paths["events"])
        self.events = {row["eventId"]: row for row in self.event_catalog["packages"]}
        self.quality_config = read_json(self.paths["quality"])
        self.observation_area = float(self.quality_config["basin_area_km2"])
        if not np.isfinite(self.observation_area) or self.observation_area <= 0:
            raise ValueError("注册观测控制面积无效")
        self.years = {}
        for path in self.precipitation_dir.glob("nananxi_precip_hourly_1km_*.nc"):
            match = re.fullmatch(r"nananxi_precip_hourly_1km_(\d{4})\.nc", path.name)
            if match:
                self.years[int(match.group(1))] = path
        if not self.years:
            raise FileNotFoundError("固定版本 QC 逐小时降雨未登记")
        self._initialized = True

    def _open(self, path):
        available = set(self.xr.backends.list_engines())
        for engine in ("h5netcdf", "netcdf4", "scipy"):
            if engine in available:
                return self.xr.open_dataset(path, engine=engine)
        return self.xr.open_dataset(path)

    def _file(self, path):
        stat = path.stat()
        fingerprint = (stat.st_size, stat.st_mtime_ns)
        cached = self._hashes.get(path)
        if cached is None or cached[0] != fingerprint:
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            try:
                relative = path.relative_to(self.model_root).as_posix()
            except ValueError:
                try:
                    relative = "runtime/" + path.relative_to(RUNTIME).as_posix()
                except ValueError:
                    relative = "registered-product/" + path.name
            cached = (fingerprint, {"path": relative,
                                    "sha256": hasher.hexdigest(), "bytes": stat.st_size})
            self._hashes[path] = cached
        return dict(cached[1])

    def _provenance(self, years):
        paths = list(self.paths.values()) + [self.years[year] for year in sorted(years)]
        files = [self._file(path) for path in paths]
        return {"sourceType": "model-qc", "inputSource": "fixed-registered-qc" if self.bundle_version == DEFAULT_INPUT_BUNDLE else "registered-input-product",
                "modelInputVersion": self.model_input_version,
                "bundleVersionId": self.bundle_version, "bundleManifestSHA256": self.bundle_digest,
                "forcingVersion": self.forcing_version, "observationVersion": self.observation_version,
                "controlReachId": CONTROL_REACH_ID, "weighting": "UPSTREAM_ADJUSTED_OVERLAP_AREA",
                "coordinateCRS": "EPSG:4326", "timeConvention": "SOURCE_LOCAL_NAIVE_HOURLY",
                "files": files,
                "inputManifestSHA256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                "notes": ["格点与当前模型任务使用同一登记版本输入；不是历史模拟结果。",
                          "无已登记 QC 逐站雨量及水位：不使用旧 MDB 数值替补。",
                          "控制区面积由当前 v2 切分河网权重计算；旧场次目录面积仅为历史元信息。",
                          *self.bundle.get("qualityNotes", [])]}

    def _read_arrays(self, start, end, limit):
        np, pd = self.np, self.pd
        hours = int((end - start).total_seconds() / 3600) + 1
        if end < start or not 1 <= hours <= limit:
            raise ValueError(f"只读输入窗口须为 1 至 {limit} 个整小时（首末时刻均包含）")
        times = pd.date_range(start, end, freq="h")
        years = set(range(start.year, end.year + 1))
        missing = sorted(years.difference(self.years))
        if missing:
            raise FileNotFoundError("所选年份无固定版本 QC 降雨：" + ",".join(map(str, missing)))
        if self.bundle.get("manifest", {}).get("forcingPolicy") == "STRICT_MISSING":
            from strict_forcing import load_hourly_forcing
            actual, values, counts, audit = load_hourly_forcing(
                self.bundle, self.grids, start, end, require_complete=False)
            observed = self.observed.reindex(actual)
            contributing = values[:, self.control_indexes]
            complete = np.isfinite(contributing).all(axis=1)
            rainfall = np.full(len(actual), np.nan)
            rainfall[complete] = np.average(contributing[complete], axis=1,
                                            weights=self.areas[self.control_indexes])
            return {"times": actual, "values": values, "counts": counts, "observed": observed,
                    "rainfall": rainfall, "years": years,
                    "present": np.array(audit["present_hour_mask"], dtype=bool),
                    "countFields": audit["station_count_fields"],
                    "negativeCount": audit["negative_values_reclassified_missing"], "strictForcingAudit": audit}
        values = np.full((len(times), len(self.grids)), np.nan, dtype=float)
        counts = np.full(len(times), -1, dtype=np.int16)
        present = np.zeros(len(times), dtype=bool)
        count_fields = set()
        negative_count = 0
        for year in sorted(years):
            with self._open(self.years[year]) as dataset:
                subset = dataset.sel(time=slice(start, end))
                actual_times = pd.DatetimeIndex(pd.to_datetime(subset["time"].values))
                if actual_times.has_duplicates or actual_times.tz is not None:
                    raise ValueError("QC 降雨时间轴重复或时区未登记")
                if not len(actual_times):
                    continue
                positions = times.get_indexer(actual_times)
                if np.any(positions < 0):
                    raise ValueError("QC 降雨时间轴含非整点或窗口外时刻")
                if subset["precipitation"].dims != ("time", "lat", "lon"):
                    raise ValueError("注册 QC 降雨维度须为 time,lat,lon")
                if str(subset["precipitation"].attrs.get("units", "")).strip() != "mm":
                    raise ValueError("注册 QC 逐时降雨量单位须为 mm")
                precipitation = np.asarray(subset["precipitation"].values, dtype=np.float32)
                try:
                    extracted = np.stack([precipitation[:, int(row["array_row"]), int(row["array_col"])]
                                          for row in self.grids], axis=1)
                except IndexError as exc:
                    raise ValueError("注册网格数组位置超出 QC 降雨范围") from exc
                negative_count += int(np.count_nonzero(extracted < 0))
                # Match the existing numerical loader explicitly. Missing cells
                # remain NaN; only finite negative precipitation is clipped.
                values[positions] = np.maximum(extracted, 0.0)
                station_counts, field = self._read_station_count(subset)
                if station_counts.ndim != 1 or len(station_counts) != len(actual_times):
                    raise ValueError("QC 源站数字段须与逐时轴一一对应")
                counts[positions] = station_counts
                present[positions] = True
                if field:
                    count_fields.add(field)
        observed = self.observed.reindex(times)
        contributing = values[:, self.control_indexes]
        contributing_areas = self.areas[self.control_indexes]
        complete = np.isfinite(contributing).all(axis=1)
        rainfall = np.full(len(times), np.nan)
        rainfall[complete] = np.average(contributing[complete], axis=1, weights=contributing_areas)
        return {"times": times, "values": values, "counts": counts, "observed": observed,
                "rainfall": rainfall, "years": years, "present": present,
                "countFields": sorted(count_fields), "negativeCount": negative_count}

    def _window_payload(self, arrays):
        np = self.np
        times = [time.isoformat() for time in arrays["times"]]
        start, end = times[0], times[-1]
        cells = [{**cell, "values": arrays["values"][:, index]} for index, cell in enumerate(self.cells)]
        counts = [int(value) if value >= 0 else None for value in arrays["counts"]]
        valid_fraction = np.isfinite(arrays["values"]).mean(axis=1)
        warning_reasons = []
        if not bool(arrays["present"].all()):
            warning_reasons.append("PRECIPITATION_TIME_GAP")
        if not bool(np.isfinite(arrays["values"]).all()):
            warning_reasons.append("PRECIPITATION_INCOMPLETE")
        if not bool(np.isfinite(arrays["observed"]).all()):
            warning_reasons.append("FLOW_INCOMPLETE")
        if any(value is None for value in counts):
            warning_reasons.append("SOURCE_STATION_COUNT_UNAVAILABLE")
        if any(value == 0 for value in counts):
            warning_reasons.append("SOURCE_STATIONS_BELOW_MINIMUM")
        if arrays["negativeCount"]:
            warning_reasons.append("NEGATIVE_PRECIPITATION_RECLASSIFIED_MISSING" if arrays.get("strictForcingAudit")
                                   else "NEGATIVE_PRECIPITATION_CLIPPED_AS_WORKER")
        quality = {"warningReasons": warning_reasons, "stationCountFields": arrays["countFields"],
                   "sourceStationCountAvailable": all(value is not None for value in counts),
                   "negativePrecipitationClippedCount": 0 if arrays.get("strictForcingAudit") else arrays["negativeCount"],
                   "negativePrecipitationReclassifiedMissingCount": arrays["negativeCount"] if arrays.get("strictForcingAudit") else 0,
                   "missingHours": int((~arrays["present"]).sum())}
        observation_quality = evaluate_observation_coverage(arrays["times"], arrays["observed"].to_numpy(),
                                                           self.quality_config)
        quality["observationCoverage"] = observation_quality
        quality["warningReasons"] = list(dict.fromkeys(warning_reasons + observation_quality["warning_reasons"]))
        if arrays.get("strictForcingAudit"):
            quality["strictForcingAudit"] = {key: value for key, value in arrays["strictForcingAudit"].items()
                                              if key not in {"present_hour_mask", "negative_counts_by_hour", "nonfinite_counts_by_hour"}}
        result = {"schemaVersion": "2.0", "caseKey": CASE_KEY, "sourceType": "model-qc",
                  "bundleVersionId": self.bundle_version, "bundleManifestSHA256": self.bundle_digest,
                  "forcingVersion": self.forcing_version, "observationVersion": self.observation_version,
                  "product": {"resolution": RESOLUTION_ID, "resolutionId": RESOLUTION_ID,
                              "start": start, "end": end, "stepHours": 1, "times": times, "cells": cells,
                              "sourceType": "model-qc", "forcingVersion": self.forcing_version,
                              "bundleVersionId": self.bundle_version},
                  "observationPreview": {"schemaVersion": "2.0", "sourceType": "model-qc",
                                         "forcingVersion": self.forcing_version, "observationVersion": self.observation_version,
                                         "start": start, "end": end, "stepHours": 1, "times": times, "rain": {},
                                         "river": {"spatialCode": "70115800", "flows": arrays["observed"].to_numpy(),
                                                   "levels": [None] * len(times)}, "quality": quality},
                  "summary": {"controlAreaKm2": self.control_area, "observationAreaKm2": self.observation_area,
                              "projectAreaKm2": self.project_area, "gridCount": len(self.grids),
                              "contributingGridCount": len(self.control_indexes), "windowHours": len(times),
                              "arealRainfallMm": arrays["rainfall"], "reportingStationCount": counts,
                              "validGridFraction": valid_fraction,
                              "rainfallCompleteness": float(np.isfinite(arrays["rainfall"]).mean()),
                              "flowCompleteness": float(np.isfinite(arrays["observed"]).mean())},
                  "quality": quality, "provenance": self._provenance(arrays["years"])}
        return clean(result)

    def _coverage(self):
        pd = self.pd
        first, last = min(self.years), max(self.years)
        with self._open(self.years[first]) as dataset:
            precipitation_start = pd.Timestamp(dataset["time"].values[0])
        with self._open(self.years[last]) as dataset:
            precipitation_end = pd.Timestamp(dataset["time"].values[-1])
        available_flow = self.observed.dropna()
        flow_start, flow_end = available_flow.index.min(), available_flow.index.max()
        common_start, common_end = max(precipitation_start, flow_start), min(precipitation_end, flow_end)
        if common_end < common_start:
            raise ValueError("固定 QC 降雨与观测流量没有共同覆盖")
        # Select the latest genuinely paired, QC-available consecutive run. A
        # file's end date alone does not prove a paired 72-hour observation.
        scan_start = max(common_start, common_end - pd.Timedelta(hours=MAX_WINDOW_HOURS - 1))
        arrays = self._read_arrays(scan_start, common_end, MAX_WINDOW_HOURS)
        valid = (self.np.isfinite(arrays["values"]).all(axis=1) &
                 self.np.isfinite(arrays["observed"]).to_numpy() & (arrays["counts"] > 0))
        runs, current_start = [], None
        for index, accepted in enumerate(valid):
            if accepted and current_start is None:
                current_start = index
            if current_start is not None and (not accepted or index == len(valid) - 1):
                stop = index if not accepted else index + 1
                runs.append((current_start, stop))
                current_start = None
        full_runs = [run for run in runs if run[1] - run[0] >= 72]
        candidates = full_runs if full_runs else runs
        latest = None
        if candidates:
            first_index, stop = candidates[-1]
            first_index = max(first_index, stop - 72)
            latest = {"start": arrays["times"][first_index].isoformat(),
                      "end": arrays["times"][stop - 1].isoformat(),
                      "hours": stop - first_index, "stepHours": 1}
        return {"precipitation": {"start": precipitation_start.isoformat(), "end": precipitation_end.isoformat(),
                                  "years": sorted(self.years)},
                "observedFlow": {"start": flow_start.isoformat(), "end": flow_end.isoformat()},
                "common": {"start": common_start.isoformat(), "end": common_end.isoformat()},
                "coverageMeaning": "BOUNDING_DATES_NOT_A_COMPLETENESS_GUARANTEE"}, latest

    def catalog(self, project_id=PROJECT_ID, resolution_id=RESOLUTION_ID):
        self._case(project_id, resolution_id)
        with self._lock:
            self._initialize()
            if self._catalog_cache is None:
                coverage, latest = self._coverage()
                events = [{"id": row["eventId"], "eventId": row["eventId"],
                           "sourceFloodNo": row.get("floodCode"), "start": row["start"], "end": row["end"],
                           "status": row.get("status"), "source": self.model_input_version,
                           "observedPeak": row.get("observedInputs", {}).get("observedPeakFlowM3s"),
                           "peakTime": row.get("observedInputs", {}).get("observedPeakTime"),
                           "volumeM3": row.get("observedInputs", {}).get("volumeM3", row.get("observedInputs", {}).get("riverVolumeM3")),
                           "sectionId": "70115800", "revision": 1, "purpose": "未使用",
                           "bundleVersionId": self.bundle_version,
                           "rainCompleteness": row.get("observedInputs", {}).get("rainCompleteness"),
                           "flowCompleteness": row.get("observedInputs", {}).get("riverCompleteness")}
                          for row in self.events.values()]
                latest_years = (set(range(self.pd.Timestamp(latest["start"]).year,
                                         self.pd.Timestamp(latest["end"]).year + 1)) if latest else set())
                self._catalog_cache = {"schemaVersion": "2.0", "caseKey": CASE_KEY, "sourceType": "model-qc",
                                       "bundleVersionId": self.bundle_version, "bundleManifestSHA256": self.bundle_digest,
                                       "forcingVersion": self.forcing_version, "observationVersion": self.observation_version,
                                       "rain": dict(coverage["precipitation"]), "river": dict(coverage["observedFlow"]),
                                       "coverage": coverage, "latestPeriod": latest, "events": events,
                                       "gridCount": len(self.grids), "contributingGridCount": len(self.control_indexes),
                                       "controlAreaKm2": self.control_area, "observationAreaKm2": self.observation_area,
                                       "hasStationRainfall": False, "hasWaterLevel": False,
                                       "provenance": self._provenance(latest_years)}
            # A caller must not be able to mutate cached catalog/grid metadata.
            return json.loads(json.dumps(clean(self._catalog_cache), allow_nan=False))

    def window(self, start=None, end=None, project_id=PROJECT_ID, resolution_id=RESOLUTION_ID):
        self._case(project_id, resolution_id)
        with self._lock:
            self._initialize()
            if start is None and end is None:
                latest = self.catalog()["latestPeriod"]
                if latest is None:
                    raise ValueError("最近共同覆盖范围没有可用 QC 逐时窗口，请显式选择时段")
                start, end = latest["start"], latest["end"]
            elif start is None or end is None:
                raise ValueError("start 和 end 必须同时提供或同时省略")
            start_time, end_time = self._time(start), self._time(end)
            arrays = self._read_arrays(start_time, end_time, MAX_WINDOW_HOURS)
            return self._window_payload(arrays)

    def event(self, event_id, warmup_hours=168, project_id=PROJECT_ID, resolution_id=RESOLUTION_ID):
        self._case(project_id, resolution_id)
        if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
            raise ValueError("无效场次编号")
        if isinstance(warmup_hours, bool) or not isinstance(warmup_hours, int) or not 0 <= warmup_hours <= MAX_WARMUP_HOURS:
            raise ValueError(f"暖机小时数须为 0 至 {MAX_WARMUP_HOURS} 的整数")
        with self._lock:
            self._initialize()
            if event_id not in self.events:
                raise KeyError("未登记的 QC 场次：" + event_id)
            row = self.events[event_id]
            start, end = self.pd.Timestamp(row["start"]), self.pd.Timestamp(row["end"])
            warmup_start = start - self.pd.Timedelta(hours=warmup_hours)
            arrays = self._read_arrays(warmup_start, end, MAX_EVENT_WINDOW_HOURS)
            # Warm-up participates in the scientific audit but is not a second
            # displayed event. Even an explicit 365-day request only serializes
            # the event's grid product, avoiding a million warm-up grid values.
            first = int(arrays["times"].searchsorted(start))
            displayed = {**arrays, **{key: arrays[key][first:] for key in
                                      ("times", "values", "counts", "observed", "rainfall", "present")}}
            if arrays.get("strictForcingAudit"):
                audit = arrays["strictForcingAudit"]
                displayed_audit = {**audit, "start": start.isoformat(), "end": end.isoformat(),
                                   "requested_hours": len(displayed["times"]),
                                   "present_hour_mask": audit["present_hour_mask"][first:],
                                   "negative_counts_by_hour": audit["negative_counts_by_hour"][first:],
                                   "nonfinite_counts_by_hour": audit["nonfinite_counts_by_hour"][first:]}
                ready = displayed["present"] & self.np.isfinite(displayed["values"]).all(axis=1) & (displayed["counts"] > 0)
                displayed_audit.update(missing_time_hours=int((~displayed["present"]).sum()),
                                       incomplete_forcing_hours=int((~ready).sum()),
                                       negative_values_reclassified_missing=sum(displayed_audit["negative_counts_by_hour"]),
                                       nonfinite_values=sum(displayed_audit["nonfinite_counts_by_hour"]))
                displayed.update(strictForcingAudit=displayed_audit,
                                 negativeCount=displayed_audit["negative_values_reclassified_missing"])
            result = self._window_payload(displayed)
            quality = self._evaluate_event_quality(
                event_id=event_id, start=start, end=end, times=arrays["times"],
                grid_precipitation_mm=arrays["values"][:, self.control_indexes],
                grid_areas_km2=self.areas[self.control_indexes],
                observed_flow_cms=self.observed.loc[start:end], source_station_count=arrays["counts"],
                config=self.quality_config,
            )
            # Report incomplete source coverage explicitly even though the old
            # audit formula can sum only the available paired flow records.
            event_times = self.pd.date_range(start, end, freq="h")
            flow_completeness = float(self.np.isfinite(self.observed.reindex(event_times)).mean())
            quality["flow_completeness"] = flow_completeness
            if flow_completeness < 1.0:
                quality["warning_reasons"].append("FLOW_INCOMPLETE")
            observation_quality = evaluate_observation_coverage(
                event_times, self.observed.reindex(event_times).to_numpy(), self.quality_config)
            quality["observation_coverage"] = observation_quality
            quality["maximum_observed_missing_gap_hours"] = observation_quality["maximum_missing_gap_hours"]
            quality["observed_phase_coverage"] = observation_quality["phase_coverage"]
            quality["observed_volume_scope"] = observation_quality["volume_scope"]
            quality["exclusion_reasons"] = list(dict.fromkeys(quality["exclusion_reasons"] + observation_quality["exclusion_reasons"]))
            quality["warning_reasons"] = list(dict.fromkeys(quality["warning_reasons"] + observation_quality["warning_reasons"]))
            quality["accepted"] = bool(quality["accepted"] and observation_quality["accepted"])
            quality["task_precipitation_complete"] = bool(self.np.isfinite(arrays["values"]).all())
            quality["task_time_axis_complete"] = bool(arrays["present"].all())
            quality["model_input_ready"] = (quality["task_precipitation_complete"] and
                                             quality["task_time_axis_complete"] and quality["accepted"])
            result.update({"event": {"id": event_id, "eventId": event_id, "sourceFloodNo": row.get("floodCode"),
                                     "start": start.isoformat(), "end": end.isoformat(), "status": row.get("status"),
                                     "observedInputs": row.get("observedInputs", {}),
                                     "spatialRainInputs": row.get("spatialRainInputs", {}),
                                     "qcRevision": row.get("qcRevision", {})},
                           "eventPeriod": {"start": start.isoformat(), "end": end.isoformat()},
                           "warmupPeriod": {"start": warmup_start.isoformat(),
                                            "end": (start - self.pd.Timedelta(hours=1)).isoformat() if warmup_hours else None,
                                            "hours": warmup_hours},
                           "qualityPeriod": {"start": warmup_start.isoformat(), "end": end.isoformat(),
                                             "hours": len(arrays["times"])},
                           "qualityPolicy": {"maximumWarmupHours": MAX_WARMUP_HOURS,
                                             "eventProductIncludesWarmup": False},
                           "inputQuality": result["quality"], "quality": quality})
            return clean(result)


__all__ = ["InputService", "CASE_KEY", "FORCING_VERSION", "OBSERVATION_VERSION"]
