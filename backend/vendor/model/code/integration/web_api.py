"""HTTP-facing read model for the integrated runoff-routing workbench.

The browser receives business identifiers and aggregated products only.  It
does not receive server paths or parse NetCDF/CSV files itself.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from dataclasses import fields
from copy import deepcopy
import pandas as pd
import numpy as np
import xarray as xr
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from xaj_routing.point_query import PointFlowQuery
from xaj_routing.raster_formats import cell_center_xy
from runoff_platform.api.schemas import get_parameter_catalog
from runoff_platform.core.types import GridInfo, LandParams, LandState, RunContext, RunoffConversionParams
from runoff_platform.models.xaj import simulate_land


COMPONENT_LABELS = {
    "RS": "地面流 RS",
    "RI": "壤中流 RI",
    "RG": "地下水 RG",
    "GLACIER_FAST": "冰川快速流",
    "GLACIER_SLOW": "冰川慢速流",
    "GLACIER_BASE": "冰川基流",
}


ROUTING_PARAMETER_SCHEMA = [
    {"name": "default_slope_k_hours.RS", "label": "地面流坡面汇流滞时", "unit": "hour", "minimum": 0.1, "maximum": 720.0},
    {"name": "default_slope_k_hours.RI", "label": "壤中流坡面汇流滞时", "unit": "hour", "minimum": 0.1, "maximum": 720.0},
    {"name": "default_slope_k_hours.RG", "label": "地下水坡面汇流滞时", "unit": "hour", "minimum": 0.1, "maximum": 720.0},
    {"name": "default_slope_k_hours.GLACIER_FAST", "label": "冰川快速流坡面滞时", "unit": "hour", "minimum": 0.1, "maximum": 720.0},
    {"name": "default_slope_k_hours.GLACIER_SLOW", "label": "冰川慢速流坡面滞时", "unit": "hour", "minimum": 0.1, "maximum": 720.0},
    {"name": "default_slope_k_hours.GLACIER_BASE", "label": "冰川基流坡面滞时", "unit": "hour", "minimum": 0.1, "maximum": 720.0},
    {"name": "muskingum_x", "label": "Muskingum 权重 X", "unit": "1", "minimum": 0.0, "maximum": 0.5},
    {"name": "default_channel_velocity_kmh", "label": "河道默认流速", "unit": "km/h", "minimum": 0.1, "maximum": 20.0},
]

CURRENT_XAJ_PARAMETERS = {
    "KC", "WM", "WUM", "WLM", "C", "B", "SM", "EX", "KI", "KG", "IM"
}
MANUAL_RUN_ID = re.compile(r"manual-[0-9a-f]{12}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_value(value: Any) -> Any:
    """Convert NumPy/Pandas scalar values to JSON-safe primitives."""
    if value is None:
        return None
    try:
        if not np.isfinite(value):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if hasattr(value, "item") else value


class IntegratedResultsService:
    """Expose current local integrated results through stable business APIs."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        split_config = self.project_root / "config" / "integrated_2017_control_split_v2.json"
        self.default_config = split_config if split_config.is_file() else self.project_root / "config" / "integrated_2017.json"
        self.event_catalog_path = self.project_root / "data_interface" / "event_catalog" / "nananxi_flood_event_catalog_qc_20260819.json"
        self.observed_flow_path = self.project_root / "data_interface" / "observations" / "xufan_outlet_hourly_qc.csv"
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="integrated-run")
        self._tasks: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._event_series_cache: dict[str, dict[str, Any]] = {}
        self._grid_state_cache: dict[tuple[str, str | None, str | None, str | None], dict[str, Any]] = {}
        self._flood_rain_cache: dict[str, dict[str, Any]] = {}
        self._point_query: PointFlowQuery | None = None
        self._data_source_dir = self.project_root / 'data_sources'
        self._data_source_manifest = self._data_source_dir / 'active_source.json'
        self._lock = threading.RLock()

    def _config(self) -> dict[str, Any]:
        return _read_json(self.default_config)

    def data_source_status(self) -> dict[str, Any]:
        manifest = _read_json(self._data_source_manifest) if self._data_source_manifest.is_file() else {}
        source_path = self._resolve(manifest["stored_path"]) if manifest.get("stored_path") else None
        available = bool(source_path and source_path.is_file())
        integration_status = manifest.get("integration_status", "PENDING_MAPPING") if available else "NOT_CONFIGURED"
        return {
            "status": "READY" if available else "NOT_CONFIGURED",
            "model_ready": available and integration_status == "READY",
            "integration_status": integration_status,
            "source_type": manifest.get("source_type"),
            "original_name": manifest.get("original_name"),
            "stored_path": manifest.get("stored_path"),
            "size_bytes": source_path.stat().st_size if available else 0,
            "modified_at": manifest.get("modified_at"),
            "sha256": manifest.get("sha256"),
            "message": "数据源已登记，后续运行使用该数据源配置。" if available else "尚未配置数据源，请先选择 MDB、ACCDB 或 CSV 文件。",
            "supported_extensions": [".mdb", ".accdb", ".csv", ".xlsx"],
        }

    def register_data_source(self, filename: str, body_stream, content_length: int) -> dict[str, Any]:
        safe_name = Path(filename or "uploaded_data").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".mdb", ".accdb", ".csv", ".xlsx"}:
            raise ValueError("仅支持 .mdb、.accdb、.csv 或 .xlsx 数据文件")
        if content_length <= 0 or content_length > 512 * 1024 * 1024:
            raise ValueError("数据文件大小必须在 1 B 到 512 MB 之间")
        self._data_source_dir.mkdir(parents=True, exist_ok=True)
        stored = self._data_source_dir / safe_name
        temporary = stored.with_suffix(stored.suffix + ".uploading")
        digest = hashlib.sha256()
        remaining = content_length
        with temporary.open("wb") as handle:
            while remaining:
                chunk = body_stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("数据文件上传不完整")
                handle.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        temporary.replace(stored)
        manifest = {
            "schema_version": "data_source.v1",
            "source_type": suffix.lstrip("."),
            "original_name": safe_name,
            "stored_path": stored.relative_to(self.project_root).as_posix(),
            "size_bytes": stored.stat().st_size,
            "sha256": digest.hexdigest(),
            "modified_at": _now(),
            "status": "READY",
            "integration_status": "PENDING_MAPPING",
        }
        self._write_json_atomic(self._data_source_manifest, manifest)
        return self.data_source_status()
    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    def _paths(self) -> dict[str, Path]:
        config = self._config()
        return {
            "config": self.default_config,
            "output": self._resolve(config["output_dir"]),
            "dem": self._resolve(config["dem_ascii"]),
            "topology": self._resolve(config["topology"]),
            "control": self._resolve(config["control_points"]),
            "runoff_wide": self._resolve(config["runoff_wide"]),
            "runoff_grid_geojson": self._resolve(config["runoff_grid_geojson"]),
            "grid_static": self._resolve(config["grid_static"]),
            "initial_routing_state": self._resolve(config["initial_routing_state"]) if config.get("initial_routing_state") else None,
            "observed": self._resolve(config["observed_control_flow"]),
            "xaj_parameters": self._resolve(
                config.get("xaj_parameter_set", "data_interface/runoff_2017/xaj_parameter_set.json")
            ),
            "routing_parameters": self._resolve(config["routing_parameters"]),
        }

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        """Write a parameter file atomically so a running request never sees half JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def parameters(self, zone_type: str = "NON_GLACIER") -> dict[str, Any]:
        """Return editable runoff and routing parameters with units and limits."""
        zone = str(zone_type or "NON_GLACIER").upper()
        catalog = get_parameter_catalog(zone)
        paths = self._paths()
        xaj = _read_json(paths["xaj_parameters"]) if paths["xaj_parameters"].is_file() else {}
        routing = _read_json(paths["routing_parameters"]) if paths["routing_parameters"].is_file() else {}
        land_values = dict(xaj.get("land_parameters", {}))
        runoff_parameters: list[dict[str, Any]] = []
        for item in catalog["parameters"]:
            value = land_values.get(item["name"], catalog["defaults"].get(item["name"]))
            runoff_parameters.append({
                **item,
                "value": _json_value(value),
                "group": "runoff",
                "active_in_current_chain": item["name"] in CURRENT_XAJ_PARAMETERS,
            })
        routing_defaults = routing.get("default_slope_k_hours", {})
        routing_values = {
            **{f"default_slope_k_hours.{key}": value for key, value in routing_defaults.items()},
            "muskingum_x": routing.get("metadata", {}).get("muskingum_x", 0.2),
            "default_channel_velocity_kmh": routing.get("metadata", {}).get("default_channel_velocity_kmh", 3.0),
        }
        routing_parameters = [
            {
                **item,
                "value": _json_value(routing_values.get(item["name"])),
                "group": "routing",
                "active_in_current_chain": item["name"] not in {
                    "default_channel_velocity_kmh",
                    "default_slope_k_hours.GLACIER_FAST",
                    "default_slope_k_hours.GLACIER_SLOW",
                    "default_slope_k_hours.GLACIER_BASE",
                },
            }
            for item in ROUTING_PARAMETER_SCHEMA
        ]
        return {
            "schema_version": "parameter_set.v1",
            "zone_type": zone,
            "parameter_set_id": xaj.get("param_set_id", "UNREGISTERED"),
            "status": xaj.get("status", "ACTIVE"),
            "last_modified": datetime.fromtimestamp(
                max(paths["xaj_parameters"].stat().st_mtime if paths["xaj_parameters"].is_file() else 0,
                    paths["routing_parameters"].stat().st_mtime if paths["routing_parameters"].is_file() else 0),
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z"),
            "parameters": runoff_parameters + routing_parameters,
            "values": {
                "runoff": {item["name"]: item["value"] for item in runoff_parameters},
                "routing": {item["name"]: item["value"] for item in routing_parameters},
            },
            "constraints": catalog.get("constraints", []) + [
                "routing default slope k must be positive",
                "0 <= muskingum_x <= 0.5",
            ],
            "apply_note": "保存后对新的状态诊断/重算生效；已生成的历史结果不会自动重算。",
        }

    def update_parameters(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist manual parameter edits from the platform UI."""
        zone = str(payload.get("zone_type", "NON_GLACIER")).upper()
        if zone != "NON_GLACIER":
            raise ValueError("当前南安溪交付实例仅允许编辑 NON_GLACIER 参数")
        values = payload.get("values")
        if not isinstance(values, dict):
            raise ValueError("values 必须是对象，包含 runoff 和 routing")
        catalog = get_parameter_catalog(zone)
        current = self.parameters(zone)
        metadata = {item["name"]: item for item in current["parameters"]}
        normalized: dict[str, dict[str, Any]] = {"runoff": {}, "routing": {}}
        for group in normalized:
            submitted = values.get(group, {})
            if not isinstance(submitted, dict):
                raise ValueError(f"values.{group} 必须是对象")
            allowed = {name for name, item in metadata.items() if item["group"] == group}
            unknown = sorted(set(submitted) - allowed)
            if unknown:
                raise ValueError(f"不支持的{group}参数: {', '.join(unknown)}")
            for name, raw in submitted.items():
                item = metadata[name]
                if isinstance(raw, bool):
                    value: Any = raw
                else:
                    try:
                        value = float(raw)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"参数 {name} 必须是数字") from exc
                    if not np.isfinite(value):
                        raise ValueError(f"参数 {name} 必须是有限数字")
                    if value < float(item["minimum"]) or value > float(item["maximum"]):
                        raise ValueError(
                            f"参数 {name} 超出范围 [{item['minimum']}, {item['maximum']}]"
                        )
                normalized[group][name] = value
        runoff = normalized["runoff"]
        if runoff.get("WM", current["values"]["runoff"].get("WM")) < (
            runoff.get("WUM", current["values"]["runoff"].get("WUM"))
            + runoff.get("WLM", current["values"]["runoff"].get("WLM"))
        ):
            raise ValueError("约束不满足：WM 必须大于等于 WUM + WLM")
        if runoff.get("KI", current["values"]["runoff"].get("KI", 0)) + runoff.get(
            "KG", current["values"]["runoff"].get("KG", 0)
        ) > 1.0:
            raise ValueError("约束不满足：KI + KG 必须小于等于 1")

        paths = self._paths()
        xaj = _read_json(paths["xaj_parameters"])
        xaj.setdefault("land_parameters", {}).update(runoff)
        xaj["status"] = "MANUAL_EDITED"
        xaj["manual_edit_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        xaj["manual_edit_source"] = "platform_parameter_editor"
        routing = _read_json(paths["routing_parameters"])
        for name, value in normalized["routing"].items():
            if name.startswith("default_slope_k_hours."):
                routing.setdefault("default_slope_k_hours", {})[name.split(".", 1)[1]] = value
            elif name == "muskingum_x":
                routing.setdefault("metadata", {})["muskingum_x"] = value
                for reach in routing.get("reach_muskingum", {}).values():
                    reach["x"] = value
            elif name == "default_channel_velocity_kmh":
                routing.setdefault("metadata", {})["default_channel_velocity_kmh"] = value
        self._write_json_atomic(paths["xaj_parameters"], xaj)
        self._write_json_atomic(paths["routing_parameters"], routing)
        with self._lock:
            self._grid_state_cache.clear()
            self._point_query = None
        return self.parameters(zone)

    def _report(self) -> dict[str, Any]:
        path = self._paths()["output"] / "integration_validation_report.json"
        return _read_json(path) if path.is_file() else {"status": "NOT_RUN"}

    def _run_id(self) -> str:
        return str(self._config().get("run_id", "integrated_run"))

    def _point_query_service(self) -> PointFlowQuery:
        if self._point_query is None:
            config = dict(self._config())
            parameter_path = self._resolve(config.get("routing_parameters", ""))
            if parameter_path.is_file():
                config.update(_read_json(parameter_path))
            topology_payload = _read_json(self._paths()["topology"])
            config["spatial_version"] = topology_payload.get(
                "spatial_version", config.get("spatial_version", "nan_an_dem_utm50_30m_clipped_v1")
            )
            config["default_channel_width_m"] = float(config.get("default_channel_width_m", 20.0))
            config["default_channel_velocity_kmh"] = float(
                config.get("metadata", {}).get("default_channel_velocity_kmh", 3.0)
            )
            self._point_query = PointFlowQuery(self.project_root, self._paths(), config)
        return self._point_query

    def point_flow_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            lon = float(payload.get("lon"))
            lat = float(payload.get("lat"))
        except (TypeError, ValueError) as exc:
            raise ValueError("lon 和 lat 必须为数字") from exc
        query_id = f"point-{uuid.uuid4().hex[:12]}"
        rerun_output_dir = self.project_root / "data_products" / "point_flow_queries" / query_id / "rerun"
        result = self._point_query_service().query(
            lon=lon,
            lat=lat,
            channel_width_m=payload.get("channel_width_m"),
            run_id=str(payload.get("run_id") or self._run_id()),
            rerun_output_dir=rerun_output_dir,
        )
        result["point_query_id"] = query_id
        if result.get("method") == "TEMPORARY_CONTROL_SPLIT_RERUN":
            # Expose a business product id, never the server's local output path.
            details = result.setdefault("method_details", {})
            details["rerun_product_id"] = query_id
            details["rerun_status"] = "COMPLETED"
        result["request"] = {
            "lon": lon,
            "lat": lat,
            "channel_width_m": payload.get("channel_width_m"),
            "run_id": result["run_id"],
        }
        result["dependencies"] = {
            "spatial_version": _read_json(self._paths()["topology"]).get("spatial_version"),
            "parameter_set_id": Path(self._config().get("routing_parameters", "routing_parameters")).stem,
            "product_schema_version": "point_flow_query.v1",
        }
        output_dir = self.project_root / "data_products" / "point_flow_queries"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{query_id}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result["outputs"] = ["point_flow_query.json"]
        return result

    def point_query_context(self) -> dict[str, Any]:
        query = self._point_query_service()
        # Match the DEM preview window used by the frontend. Using the full
        # raster extent here shifts the stream overlay because dem_preview()
        # intentionally crops to the topology extent.
        reaches = query.topology_result.reaches
        endpoint_cells = [cell for reach in reaches for cell in (reach.start_cell, reach.end_cell)]
        row_min = max(0, min(cell[0] for cell in endpoint_cells))
        row_max = min(query.grid.nrows - 1, max(cell[0] for cell in endpoint_cells))
        col_min = max(0, min(cell[1] for cell in endpoint_cells))
        col_max = min(query.grid.ncols - 1, max(cell[1] for cell in endpoint_cells))
        corners = [
            (query.grid.xllcorner + col_min * query.grid.cellsize,
             query.grid.yllcorner + (query.grid.nrows - row_max - 1) * query.grid.cellsize),
            (query.grid.xllcorner + (col_max + 1) * query.grid.cellsize,
             query.grid.yllcorner + (query.grid.nrows - row_min) * query.grid.cellsize),
        ]
        bbox = [query.inverse_transformer.transform(x, y) for x, y in corners]
        lines = []
        for reach in query.topology_result.reaches:
            points = []
            for row, col in reach.cells:
                x, y = cell_center_xy(query.grid, row, col)
                lon, lat = query.inverse_transformer.transform(x, y)
                points.append([float(lon), float(lat)])
            lines.append({"reach_id": reach.reach_id, "points": points})
        control_nodes = []
        for node in query.topology_result.control_nodes:
            row, col = node["cell"]
            x, y = cell_center_xy(query.grid, row, col)
            lon, lat = query.inverse_transformer.transform(x, y)
            control_nodes.append(
                {
                    **node,
                    "lon": float(lon),
                    "lat": float(lat),
                }
            )
        return {
            "schema_version": "point_flow_context.v1",
            "crs": "EPSG:4326",
            "bbox_wgs84": {
                "min_lon": min(item[0] for item in bbox), "min_lat": min(item[1] for item in bbox),
                "max_lon": max(item[0] for item in bbox), "max_lat": max(item[1] for item in bbox),
            },
            "default_channel_width_m": query.default_width_m,
            "stream_threshold_km2": self._config().get("stream_threshold_km2", 1.0),
            "dem_resolution_m": query.grid.cellsize,
            "stream_lines": lines,
            "control_nodes": control_nodes,
        }

    def _event_catalog(self) -> dict[str, Any]:
        return _read_json(self.event_catalog_path)

    @staticmethod
    def _event_payload(
        package: dict[str, Any],
        simulation_event_id: str,
        simulation_run_id: str,
    ) -> dict[str, Any]:
        observed = package.get("observedInputs", {})
        return {
            "event_id": package.get("eventId"),
            "flood_code": package.get("floodCode"),
            "start": package.get("start"),
            "end": package.get("end"),
            "duration_hours": package.get("durationHours"),
            "catalog_status": package.get("status", "unknown"),
            "observed_available": bool(observed.get("riverRecords")),
            "rain_completeness": observed.get("rainCompleteness"),
            "flow_completeness": observed.get("riverCompleteness"),
            "observed_peak_m3_s": observed.get("observedPeakFlowM3s"),
            "observed_peak_time": observed.get("observedPeakTime"),
            "reporting_rain_stations": observed.get("reportingRainStations"),
            "simulation_available": package.get("eventId") == simulation_event_id,
            "simulation_run_id": simulation_run_id if package.get("eventId") == simulation_event_id else None,
        }

    def event_catalog(self) -> dict[str, Any]:
        catalog = self._event_catalog()
        simulation_event_id = "xf-31170619"
        result_ready = (self._paths()["output"] / "control_flow.csv").is_file()
        items = [
            self._event_payload(package, simulation_event_id, self._run_id())
            for package in catalog.get("packages", [])
        ]
        if not result_ready:
            for item in items:
                item["simulation_available"] = False
                item["simulation_run_id"] = None
        input_ready = (self.project_root / "data_interface" / "runoff_2017" /
                       "forcing_event_by_grid.csv").is_file()
        for item in items:
            item["run_input_available"] = input_ready and item["event_id"] == simulation_event_id
        return {
            "schema_version": "event_catalog.v1",
            "catalog_version": catalog.get("qcRevision", {}).get("forcingVersion"),
            "source_type": "QC_EVENT_CATALOG",
            "total": len(items),
            "simulation_ready_count": sum(1 for item in items if item["simulation_available"]),
            "current_event_id": simulation_event_id,
            "items": items,
        }

    def event_detail(self, event_id: str) -> dict[str, Any]:
        simulation_event_id = "xf-31170619"
        package = next(
            (item for item in self._event_catalog().get("packages", []) if item.get("eventId") == event_id),
            None,
        )
        if package is None:
            raise KeyError(f"event not found: {event_id}")
        payload = self._event_payload(package, simulation_event_id, self._run_id())
        if not (self._paths()["output"] / "control_flow.csv").is_file():
            payload["simulation_available"] = False
            payload["simulation_run_id"] = None
        payload["run_input_available"] = (
            event_id == simulation_event_id and
            (self.project_root / "data_interface" / "runoff_2017" /
             "forcing_event_by_grid.csv").is_file())
        payload["qc_revision"] = package.get("qcRevision", {})
        payload["spatial_rain_inputs"] = package.get("spatialRainInputs", {})
        payload["message"] = (
            "该场次已有产汇流模型结果。"
            if payload["simulation_available"]
            else "该场次已有质量控制后的实测资料，但尚未生成本平台的产流与河段汇流结果。"
        )
        return payload

    def event_series(self, event_id: str, section_id: str = "xufan_70115800") -> dict[str, Any]:
        detail = self.event_detail(event_id)
        if detail["simulation_available"]:
            result = self.series(detail["simulation_run_id"], section_id)
            result["event_id"] = event_id
            result["event_status"] = "SIMULATION_READY"
            return result
        if event_id in self._event_series_cache:
            return dict(self._event_series_cache[event_id])
        start = str(detail["start"]).replace("T", " ")
        end = str(detail["end"]).replace("T", " ")
        times: list[str] = []
        observed: list[float] = []
        if self.observed_flow_path.is_file():
            with self.observed_flow_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    timestamp = str(row.get("time", ""))
                    if start <= timestamp <= end:
                        times.append(timestamp)
                        observed.append(_number(row.get("flow_m3_s"), float("nan")))
        result = {
            "event_id": event_id,
            "section_id": section_id,
            "unit": "m3/s",
            "time": times,
            "observed": observed,
            "simulated": [None] * len(times),
            "metrics": {},
            "event_status": "OBSERVED_ONLY",
            "message": detail["message"] if times else "该场次实测过程线数据不可用",
        }
        self._event_series_cache[event_id] = result
        return dict(result)

    def dem_preview(self, max_side: int = 120) -> dict[str, Any]:
        """Return a small real-DEM preview aligned to the topology viewBox."""
        dem_path = self._paths()["dem"]
        lines = dem_path.read_text(encoding="utf-8-sig").splitlines()
        header: dict[str, float] = {}
        data_start = 0
        for index, line in enumerate(lines[:6]):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    header[parts[0].lower()] = float(parts[1])
                except ValueError:
                    pass
            data_start = index + 1
        ncols = int(header["ncols"])
        nrows = int(header["nrows"])
        nodata = header.get("nodata_value", -9999.0)
        values = [float(token) for line in lines[data_start:] for token in line.split()]
        if len(values) < ncols * nrows:
            raise ValueError(f"DEM values are incomplete: expected {ncols * nrows}, got {len(values)}")
        grid = [values[row * ncols:(row + 1) * ncols] for row in range(nrows)]

        topology = _read_json(self._paths()["topology"])
        cells = [
            point
            for reach in topology.get("reaches", [])
            for point in (reach.get("start_cell", []), reach.get("end_cell", []))
            if len(point) == 2
        ]
        row_min = max(0, min((int(point[0]) for point in cells), default=0))
        row_max = min(nrows - 1, max((int(point[0]) for point in cells), default=nrows - 1))
        col_min = max(0, min((int(point[1]) for point in cells), default=0))
        col_max = min(ncols - 1, max((int(point[1]) for point in cells), default=ncols - 1))
        row_count = row_max - row_min + 1
        col_count = col_max - col_min + 1
        row_step = max(1, (row_count + max_side - 1) // max_side)
        col_step = max(1, (col_count + max_side - 1) // max_side)

        preview: list[list[float | None]] = []
        valid: list[float] = []
        for row in range(row_min, row_max + 1, row_step):
            output_row: list[float | None] = []
            for col in range(col_min, col_max + 1, col_step):
                block = [
                    grid[r][c]
                    for r in range(row, min(row + row_step, row_max + 1))
                    for c in range(col, min(col + col_step, col_max + 1))
                    if grid[r][c] != nodata
                ]
                value = round(sum(block) / len(block), 2) if block else None
                output_row.append(value)
                if value is not None:
                    valid.append(value)
            preview.append(output_row)
        return {
            "dem_id": "nan_an_dem_utm50_30m_clipped_v1",
            "label": "30 m DEM 底图",
            "requested_label": "dem3m",
            "resolution_m": header.get("cellsize"),
            "crs": "EPSG:32650",
            "nodata": nodata,
            "min_elevation_m": round(min(valid), 2) if valid else None,
            "max_elevation_m": round(max(valid), 2) if valid else None,
            "grid": preview,
            "row_start": row_min,
            "col_start": col_min,
            "source_rows": nrows,
            "source_cols": ncols,
            "notice": "工程内当前可用的真实 DEM 分辨率为 30 m；未发现可标记为 3 m 的 DEM 文件。",
        }

    def list_runs(self) -> dict[str, Any]:
        report = self._report()
        output = self._paths()["output"]
        status = "COMPLETED" if (output / "control_flow.csv").is_file() else "NOT_RUN"
        items = [
                {
                    "run_id": self._run_id(),
                    "run_type": "hindcast",
                    "basin_code": "nan_an",
                    "target_section": "xufan_70115800",
                    "status": status,
                    "quality_status": report.get("status", "NOT_RUN"),
                    "submitted_at": report.get("created_at"),
                    "finished_at": report.get("finished_at"),
                    "time_count": report.get("routing_structure", {}).get("outlet_flow_rows", 0),
                }
            ]
        manual_root = self.project_root / "validation" / "manual_runs"
        for folder in sorted(manual_root.glob("manual-*"), reverse=True) if manual_root.is_dir() else []:
            if folder.is_dir() and MANUAL_RUN_ID.fullmatch(folder.name):
                saved = _read_json(folder / "task.json") if (folder / "task.json").is_file() else {}
                with self._lock:
                    task = self._tasks.get(folder.name, saved)
                status = task.get("status", "COMPLETED" if (folder / "control_flow.csv").is_file() else "FAILED")
                items.append({"run_id": folder.name, "run_type": "manual_parameters",
                              "basin_code": "nan_an", "target_section": "xufan_70115800",
                              "status": status, "quality_status": status,
                              "submitted_at": task.get("submitted_at"),
                              "finished_at": task.get("finished_at"),
                              "time_count": 0})
        return {
            "items": items,
            "page": 1,
            "page_size": 20,
            "total": len(items),
        }

    def summary(self) -> dict[str, Any]:
        report = self._report()
        output = self._paths()["output"]
        observed = _read_csv(self._paths()["observed"]) if self._paths()["observed"].is_file() else []
        latest_time = observed[-1].get("time") if observed else None
        control_payload = _read_json(self._paths()["control"])
        if isinstance(control_payload, list):
            section_count = len(control_payload)
        elif isinstance(control_payload, dict) and "control_points" in control_payload:
            section_count = len(control_payload["control_points"])
        elif isinstance(control_payload, dict) and "controls" in control_payload:
            section_count = len(control_payload["controls"])
        else:
            section_count = 1
        return {
            "basin_code": "nan_an",
            "basin_name": "南安溪—徐畈试点流域",
            "as_of": latest_time,
            "run_id": self._run_id(),
            "run_status": "COMPLETED" if (output / "control_flow.csv").is_file() else "NOT_RUN",
            "quality_status": report.get("status", "NOT_RUN"),
            "station_count": 1,
            "section_count": section_count,
            "reach_count": len(_read_json(self._paths()["topology"]).get("units", [])),
            "source_grid_count": len(_read_csv(self._paths()["grid_static"])),
            "mapping_error": report.get("mapping", {}).get("max_source_fraction_error"),
            "volume_error": report.get("adapter", {}).get("max_component_volume_error_mm_km2"),
            "nse": report.get("control_section_diagnostic", {}).get("nse"),
            "warnings": report.get("limitations", []),
            "data_source": self.data_source_status(),
        }

    def topology(self) -> dict[str, Any]:
        topology = _read_json(self._paths()["topology"])
        control = _read_json(self._paths()["control"])
        reaches = topology.get("reaches", [])
        rows = [point[0] for reach in reaches for point in (reach.get("start_cell", []), reach.get("end_cell", []))]
        cols = [point[1] for reach in reaches for point in (reach.get("start_cell", []), reach.get("end_cell", []))]
        row_min, row_max = min(rows or [0]), max(rows or [1])
        col_min, col_max = min(cols or [0]), max(cols or [1])

        def project(cell: list[int] | tuple[int, int]) -> list[float]:
            row, col = cell
            x = 8.0 + 84.0 * (col - col_min) / max(col_max - col_min, 1)
            y = 10.0 + 80.0 * (row_max - row) / max(row_max - row_min, 1)
            return [round(x, 3), round(y, 3)]

        reach_payload = []
        upstream_by_reach = {str(reach.get("reach_id")): [] for reach in reaches}
        for reach in reaches:
            downstream_id = reach.get("downstream_reach_id")
            if downstream_id:
                upstream_by_reach.setdefault(str(downstream_id), []).append(str(reach["reach_id"]))
        control_reach_id = str(control.get("snapped", {}).get("reach_id", ""))
        control_nodes = list(topology.get("control_nodes") or [])
        control_reaches = {
            str(node.get("upstream_reach_id"))
            for node in control_nodes
            if node.get("upstream_reach_id")
        }
        for reach in reaches:
            reach_id = str(reach["reach_id"])
            reach_payload.append(
                {
                    "reach_id": reach_id,
                    "downstream_reach_id": reach.get("downstream_reach_id"),
                    "upstream_reach_ids": sorted(upstream_by_reach.get(reach_id, [])),
                    "is_terminal": not bool(reach.get("downstream_reach_id")),
                    "is_control": reach_id == control_reach_id or reach_id in control_reaches,
                    "length_km": reach.get("length_km", 0),
                    "start": project(reach.get("start_cell", [row_min, col_min])),
                    "end": project(reach.get("end_cell", [row_max, col_max])),
                }
            )
        units = [
            {
                "unit_id": unit["unit_id"],
                "reach_id": unit.get("reach_id"),
                "area_km2": unit.get("area_km2", 0),
                "zone_type": unit.get("zone_type", "non_glacier"),
                "slope_mean": unit.get("slope_mean"),
                "downstream_unit_id": unit.get("downstream_unit_id"),
            }
            for unit in topology.get("units", [])
        ]
        upstream_by_unit = {str(unit["unit_id"]): [] for unit in topology.get("units", [])}
        for unit in topology.get("units", []):
            downstream_id = unit.get("downstream_unit_id")
            if downstream_id:
                upstream_by_unit.setdefault(str(downstream_id), []).append(str(unit["unit_id"]))
        for unit in units:
            unit["upstream_unit_ids"] = sorted(upstream_by_unit.get(str(unit["unit_id"]), []))
        snapped = control.get("snapped", {})
        if control_nodes:
            sections = []
            for node in control_nodes:
                sections.append(
                    {
                        "section_id": node.get("control_id"),
                        "node_id": node.get("node_id"),
                        "name": node.get("name"),
                        "reach_id": node.get("original_reach_id"),
                        "upstream_reach_id": node.get("upstream_reach_id"),
                        "downstream_reach_id": node.get("downstream_reach_id"),
                        "x": project(node.get("cell", [row_max, col_max])),
                        "cell": node.get("cell"),
                        "split_status": node.get("status", "CONTROL_NODE_SPLIT"),
                    }
                )
        else:
            sections = [
                {
                    "section_id": control.get("control_id"),
                    "name": control.get("name"),
                    "reach_id": snapped.get("reach_id"),
                    "x": project(snapped.get("cell", [row_max, col_max])),
                    "distance_m": snapped.get("distance_m"),
                    "control_area_km2": snapped.get("accumulation_area_km2"),
                }
            ]
        return {
            "basin_code": topology.get("basin_id", "nan_an"),
            "spatial_version": topology.get("spatial_version"),
            "resolution_m": topology.get("cell_size_m"),
            "stream_threshold_km2": self._config().get("stream_threshold_km2"),
            "reaches": reach_payload,
            "units": units,
            "control_nodes": control_nodes,
            "sections": sections,
        }

    def run_detail(self, run_id: str) -> dict[str, Any]:
        if run_id != self._run_id():
            with self._lock:
                if run_id in self._tasks:
                    return dict(self._tasks[run_id])
            output = self.project_root / "validation" / "manual_runs" / run_id
            if MANUAL_RUN_ID.fullmatch(run_id) and (output / "task.json").is_file():
                return _read_json(output / "task.json")
            if MANUAL_RUN_ID.fullmatch(run_id) and (output / "control_flow.csv").is_file():
                return {"run_id": run_id, "status": "COMPLETED", "run_type": "manual_parameters",
                        "target_section": "xufan_70115800", "result_available": True}
            raise KeyError(f"run not found: {run_id}")
        config = self._config()
        report = self._report()
        return {
            "run_id": run_id,
            "status": "COMPLETED" if (self._paths()["output"] / "control_flow.csv").is_file() else "NOT_RUN",
            "run_type": "hindcast",
            "basin_code": "nan_an",
            "target_section": "xufan_70115800",
            "time_step_hours": config.get("time_step_hours", 1.0),
            "dependencies": {
                "spatial_version": _read_json(self._paths()["topology"]).get("spatial_version"),
                "parameter_set_id": Path(config.get("routing_parameters", "routing_parameters")).stem,
                "state_in_id": "EMPTY_INITIAL_ROUTING_STATE",
                "data_product_version": "runoff_2017_components_wide_v1",
                "product_schema_version": "handoff_schema_v2.0",
            },
            "routing_method": report.get("routing_method"),
            "quality_status": report.get("status", "NOT_RUN"),
            "outputs": ["component_flow", "reach_flow", "control_flow", "outlet_flow", "final_routing_state"],
        }

    def series(self, run_id: str, section_id: str = "xufan_70115800") -> dict[str, Any]:
        paths = self._paths()
        if run_id != self._run_id() and not (
            MANUAL_RUN_ID.fullmatch(run_id) and
            (self.project_root / "validation" / "manual_runs" / run_id).is_dir()
        ):
            raise KeyError(f"run not found: {run_id}")
        output = (paths["output"] if run_id == self._run_id() else
                  self.project_root / "validation" / "manual_runs" / run_id)
        if not (output / "control_flow.csv").is_file():
            raise ValueError("routing result has not been generated for this run")
        simulated = _read_csv(output / "control_flow.csv")
        observed = {row["time"]: _number(row.get("observed_flow_m3_s")) for row in _read_csv(paths["observed"])}
        times = [row["time"] for row in simulated]
        simulated_values = [_number(row.get("outflow_cms")) for row in simulated]
        observed_values = [observed.get(time) for time in times]
        return {
            "run_id": run_id,
            "section_id": section_id,
            "unit": "m3/s",
            "time": times,
            "observed": observed_values,
            "simulated": simulated_values,
            "metrics": _read_json(output / "integration_validation_report.json").get(
                "control_section_diagnostic", {}) if (output / "integration_validation_report.json").is_file() else {},
        }

    def diagnostics(self, run_id: str) -> dict[str, Any]:
        if run_id != self._run_id() and not (
            MANUAL_RUN_ID.fullmatch(run_id) and
            (self.project_root / "validation" / "manual_runs" / run_id).is_dir()
        ):
            raise KeyError(f"run not found: {run_id}")
        output = (self._paths()["output"] if run_id == self._run_id() else
                  self.project_root / "validation" / "manual_runs" / run_id)
        if not (output / "component_flow.csv").is_file():
            raise ValueError("routing diagnostics have not been generated for this run")
        report = _read_json(output / "integration_validation_report.json")
        component_rows = _read_csv(output / "component_flow.csv")
        totals: dict[str, float] = {}
        for row in component_rows:
            name = row.get("component_type", "UNKNOWN")
            totals[name] = totals.get(name, 0.0) + _number(row.get("runoff_mm"))
        final_state = _read_json(output / "final_routing_state.json")
        return {
            "run_id": run_id,
            "routing": report.get("routing_method", {}),
            "components": [
                {"key": key, "label": COMPONENT_LABELS.get(key, key), "total_mm": round(value, 3)}
                for key, value in sorted(totals.items())
            ],
            "quality": {
                "mapping": report.get("mapping", {}),
                "adapter": report.get("adapter", {}),
                "structure": report.get("routing_structure", {}),
                "control_section": report.get("control_section_diagnostic", {}),
            },
            "state": {
                "slope_units": len(final_state.get("slope_outflow_cms", {})),
                "reach_inflow_items": len(final_state.get("reach_inflow_cms", {})),
                "reach_outflow_items": len(final_state.get("reach_outflow_cms", {})),
                "state_version": "STATE-EMPTY-INITIAL-V1",
            },
            "limitations": report.get("limitations", []),
        }

    def grid_state(self, run_id: str, unit_id: str | None = None,
                   timestamp: str | None = None, start: str | None = None,
                   end: str | None = None) -> dict[str, Any]:
        """Return per-grid hourly forcing, XAJ states and runoff diagnostics.

        The product is generated from the same forcing and parameter set used
        by the integrated run. It is intentionally separate from outlet flow
        products so the frontend can inspect a cell without parsing files.
        """
        manual_output = self.project_root / "validation" / "manual_runs" / run_id
        if run_id != self._run_id() and not (MANUAL_RUN_ID.fullmatch(run_id) and
                                            (manual_output / "xaj_parameter_snapshot.json").is_file()):
            raise KeyError(f"run not found: {run_id}")
        cache_key = (run_id, unit_id, start, end)
        with self._lock:
            cached = self._grid_state_cache.get((run_id, None, None, None))
            if cached is None and timestamp is None:
                cached = self._grid_state_cache.get(cache_key)
        if cached is not None:
            rows = [row for row in cached["records"]
                    if (not unit_id or row["unit_id"] == unit_id)
                    and (not timestamp or pd.Timestamp(row["time"]) == pd.Timestamp(timestamp))
                    and (not start or pd.Timestamp(row["time"]) >= pd.Timestamp(start))
                    and (not end or pd.Timestamp(row["time"]) <= pd.Timestamp(end))]
            return {**cached, "unit_id": unit_id, "record_count": len(rows), "records": rows}
        paths = self._paths()
        forcing_dir = self.project_root / "data_interface" / "runoff_2017"
        forcing_rows = _read_csv(forcing_dir / "forcing_event_by_grid.csv")
        static_rows = {r["unit_id"]: r for r in _read_csv(paths["grid_static"])}
        init_path = forcing_dir / "initial_state_before_event.csv"
        initial_rows = {r["unit_id"]: r for r in _read_csv(init_path)} if init_path.is_file() else {}
        parameter_path = (manual_output / "xaj_parameter_snapshot.json" if run_id != self._run_id()
                          else paths["xaj_parameters"])
        if not parameter_path.is_file():
            parameter_path = forcing_dir / "xaj_parameter_set.json"
        raw = _read_json(parameter_path) if parameter_path.is_file() else {}
        values = raw.get("land_parameters", raw)
        allowed = {f.name for f in fields(LandParams) if f.name not in {"phase", "snow", "pet", "conversion"}}
        params = LandParams()
        for name in allowed:
            if name in values:
                setattr(params, name, float(values[name]))
        conversion = values.get("conversion", {})
        if isinstance(conversion, dict):
            params.conversion = RunoffConversionParams(**{
                item.name: conversion[item.name] for item in fields(RunoffConversionParams)
                if item.name in conversion})
        selected_units = [unit_id] if unit_id else sorted({r["unit_id"] for r in forcing_rows})
        rows_by_unit: dict[str, list[dict[str, str]]] = {u: [] for u in selected_units}
        for row in forcing_rows:
            if row["unit_id"] in rows_by_unit:
                rows_by_unit[row["unit_id"]].append(row)
        records: list[dict[str, Any]] = []
        for uid, rows in rows_by_unit.items():
            if not rows:
                continue
            frame = __import__("pandas").DataFrame([
                {"time": r["time"], "precipitation": _number(r.get("precipitation_mm")),
                 "temperature": _number(r.get("temperature_c_assumed")),
                 "pet_mm_h": _number(r.get("pet_mm_h_assumed"))}
                for r in rows
            ])
            frame["time"] = __import__("pandas").to_datetime(frame["time"])
            frame = frame.set_index("time").sort_index()
            srow = static_rows.get(uid, {})
            grid = GridInfo(unit_id=uid, zone_type="NON_GLACIER",
                            area_km2=_number(srow.get("area_km2"), 1.0),
                            elevation_m=_number(srow.get("elevation_m")),
                            slope_degree=_number(srow.get("slope_degree"), 10.0))
            irow = initial_rows.get(uid, {})
            state = LandState(**{k: _number(irow.get(k), getattr(LandState(), k))
                                 for k in ("swe", "snow_liquid", "WU", "WL", "WD", "S", "FR")})
            result = simulate_land(frame, params, state, grid,
                                   RunContext(run_id=run_id, scenario_id="STATE_EXPLORER",
                                              param_set_id=str(raw.get("param_set_id", "XAJ"))))
            for t, value in result.iterrows():
                rec = {"time": t.isoformat(), "unit_id": uid, "zone_type": "NON_GLACIER",
                       "area_km2": grid.area_km2, "precipitation_mm": float(value.precipitation),
                       "rainfall_mm": float(value.rainfall), "snowfall_mm": float(value.snowfall),
                       "infiltration_mm": max(float(value.net_rainfall) - float(value.pervious_runoff), 0.0),
                       "et_actual_mm": float(value.et_actual), "EU_mm": float(value.EU),
                       "EL_mm": float(value.EL), "ED_mm": float(value.ED),
                       "WU_mm": float(value.WU), "WL_mm": float(value.WL), "WD_mm": float(value.WD),
                       "ULM_mm": float(value.WU), "WLM_storage_mm": float(value.WL),
                       "deep_storage_mm": float(value.WD), "SWE_mm": float(value.swe),
                       "RS_mm": float(value.RS), "RI_mm": float(value.RI), "RG_mm": float(value.RG),
                       "total_runoff_mm": float(value.total_runoff),
                       "runoff_mechanism": str(value.runoff_mechanism),
                       "water_balance_error_mm": float(value.water_balance_error),
                       "quality_flag": str(value.quality_flag)}
                rec_time = pd.Timestamp(rec["time"])
                records.append(rec)
        records.sort(key=lambda r: (r["time"], r["unit_id"]))
        payload = {"schema_version": "grid_state.v1", "run_id": run_id,
                "unit_id": unit_id, "record_count": len(records),
                "time_step_hours": 1, "units": {"water_flux": "mm/h", "storage": "mm"},
                "records": records}
        with self._lock:
            self._grid_state_cache[(run_id, unit_id, None, None)] = payload
        if timestamp or start or end:
            rows = [row for row in records
                    if (not timestamp or pd.Timestamp(row["time"]) == pd.Timestamp(timestamp))
                    and (not start or pd.Timestamp(row["time"]) >= pd.Timestamp(start))
                    and (not end or pd.Timestamp(row["time"]) <= pd.Timestamp(end))]
            return {**payload, "record_count": len(rows), "records": rows}
        return payload

    def grid_units(self, run_id: str) -> dict[str, Any]:
        manual = self.project_root / "validation" / "manual_runs" / run_id
        if run_id != self._run_id() and not (MANUAL_RUN_ID.fullmatch(run_id) and manual.is_dir()):
            raise KeyError(f"run not found: {run_id}")
        rows = _read_csv(self._paths()["grid_static"])
        return {"schema_version": "grid_units.v1", "run_id": run_id,
                "units": [{"unit_id": row.get("unit_id"),
                            "area_km2": _number(row.get("area_km2")),
                            "elevation_m": _number(row.get("elevation_m")),
                            "slope_degree": _number(row.get("slope_degree"))}
                           for row in rows]}

    def grid_geometry(self) -> dict[str, Any]:
        """Join the 1 km polygons to business unit IDs without exposing file paths."""
        static = _read_csv(self._paths()["grid_static"])
        by_cell = {(int(row["source_row"]), int(row["source_col"])): row
                   for row in static}
        geojson = _read_json(self._paths()["runoff_grid_geojson"])
        features = []
        for feature in geojson.get("features", []):
            props = feature.get("properties", {})
            row = by_cell.get((int(props["row"]), int(props["col"])))
            if row:
                features.append({"type": "Feature", "geometry": feature["geometry"],
                                 "properties": {"unit_id": row["unit_id"],
                                                "area_km2": _number(row["area_km2"])}})
        return {"type": "FeatureCollection", "schema_version": "grid_geometry.v1",
                "crs": "EPSG:4326", "features": features}

    def flood_grid_rainfall(self, flood_number: str, timestamp: str) -> dict[str, Any]:
        """Read one QC raster slice; elevated values are review candidates, not errors."""
        summary = self.flood_areal_rainfall(flood_number, timestamp=timestamp)
        if not summary["records"]:
            raise ValueError("requested hour is outside this flood event")
        hour = pd.Timestamp(summary["records"][0]["time"])
        path = (self.project_root / "data_interface" / "precipitation_qc_20260819"
                / "1km_hourly" / f"nananxi_precip_hourly_1km_{hour.year}.nc")
        if not path.is_file():
            raise ValueError(f"QC rainfall raster is missing for {hour.year}")
        static = _read_csv(self._paths()["grid_static"])
        with xr.open_dataset(path, engine="h5netcdf") as dataset:
            times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
            position = times.get_indexer([hour])
            if position[0] < 0:
                raise ValueError("requested hour is not present in the QC rainfall raster")
            raster = np.asarray(dataset["precipitation"].isel(time=int(position[0])).values,
                                dtype=float)
        values = np.asarray([raster[int(row["array_row"]), int(row["array_col"])]
                             for row in static], dtype=float)
        wet = values[np.isfinite(values) & (values > 0)]
        wet_median = float(np.median(wet)) if len(wet) else 0.0
        # This is only a visual QC prompt; a spatially concentrated storm can be real.
        review_limit = max(20.0, 3.0 * wet_median)
        cells = []
        for row, value in zip(static, values):
            missing = not np.isfinite(value)
            cells.append({"unit_id": row["unit_id"],
                          "rainfall_mm": None if missing else float(value),
                          "quality_flag": "MISSING" if missing else
                          "REVIEW_HIGH" if value >= review_limit else "OK"})
        return {"schema_version": "flood_grid_rainfall.v1",
                "flood_code": summary["flood_code"], "time": hour.isoformat(),
                "unit": "mm/h", "review_threshold_mm_h": review_limit,
                "review_rule": "rain >= max(20 mm/h, 3 x wet-grid median); review only",
                "cells": cells}

    def flood_areal_rainfall(self, flood_number: str, timestamp: str | None = None,
                             start: str | None = None, end: str | None = None) -> dict[str, Any]:
        """Return area-weighted hourly precipitation for one flood number.

        The calculation uses the QC 1 km grid and the same grid-cell areas as
        the runoff product. It is a read-only diagnostic product and does not
        alter the registered runoff or routing result.
        """
        number = str(flood_number).strip()
        catalog = self._event_catalog()
        package = next((item for item in catalog.get("packages", [])
                        if str(item.get("floodCode", "")).strip() == number
                        or str(item.get("eventId", "")).strip() == number), None)
        if package is None:
            raise KeyError(f"flood not found: {number}")
        event_id = str(package.get("eventId"))
        cache_key = event_id
        if cache_key not in self._flood_rain_cache:
            paths = self._paths()
            static_rows = _read_csv(paths["grid_static"])
            positions = [(int(row["array_row"]), int(row["array_col"])) for row in static_rows]
            areas = np.asarray([_number(row.get("area_km2")) for row in static_rows], dtype=float)
            event_start, event_end = pd.Timestamp(package["start"]), pd.Timestamp(package["end"])
            rain_dir = self.project_root / "data_interface" / "precipitation_qc_20260819" / "1km_hourly"
            chunks: list[pd.DataFrame] = []
            for year in range(event_start.year, event_end.year + 1):
                nc_path = rain_dir / f"nananxi_precip_hourly_1km_{year}.nc"
                if not nc_path.is_file():
                    continue
                with xr.open_dataset(nc_path, engine="h5netcdf") as dataset:
                    times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
                    mask = (times >= event_start) & (times <= event_end)
                    if not np.any(mask):
                        continue
                    source = np.asarray(dataset["precipitation"].values[mask], dtype=float)
                    values = np.stack([source[:, row, col] for row, col in positions], axis=1)
                    valid = np.isfinite(values)
                    valid_area = valid @ areas
                    weighted = np.nansum(values * areas[None, :], axis=1)
                    basin = np.divide(weighted, valid_area,
                                      out=np.full(len(times[mask]), np.nan), where=valid_area > 0)
                    finite_values = np.where(valid, values, np.nan)
                    chunks.append(pd.DataFrame({
                        "time": times[mask].astype(str),
                        "areal_rainfall_mm": basin,
                        "valid_grid_count": valid.sum(axis=1),
                        "total_grid_count": len(positions),
                        "min_grid_rainfall_mm": np.nanmin(finite_values, axis=1),
                        "max_grid_rainfall_mm": np.nanmax(finite_values, axis=1),
                    }))
            if not chunks:
                raise ValueError(f"no gridded precipitation found for flood {number}")
            frame = pd.concat(chunks, ignore_index=True).sort_values("time")
            frame["quality_flag"] = np.where(
                frame["valid_grid_count"] == frame["total_grid_count"], "OK",
                np.where(frame["valid_grid_count"] > 0, "PARTIAL_GRID", "MISSING_GRID"),
            )
            self._flood_rain_cache[cache_key] = {
                "event_id": event_id,
                "flood_code": str(package.get("floodCode", number)),
                "start": package.get("start"), "end": package.get("end"),
                "duration_hours": package.get("durationHours"),
                "records": frame.to_dict("records"),
            }
        payload = deepcopy(self._flood_rain_cache[cache_key])
        rows = payload["records"]
        if timestamp:
            target = pd.Timestamp(timestamp)
            rows = [row for row in rows if pd.Timestamp(row["time"]) == target]
        else:
            if start:
                lower = pd.Timestamp(start)
                rows = [row for row in rows if pd.Timestamp(row["time"]) >= lower]
            if end:
                upper = pd.Timestamp(end)
                rows = [row for row in rows if pd.Timestamp(row["time"]) <= upper]
        for row in rows:
            row["time"] = pd.Timestamp(row["time"]).isoformat()
            row["valid_grid_count"] = int(row["valid_grid_count"])
            row["total_grid_count"] = int(row["total_grid_count"])
            for key in ("areal_rainfall_mm", "min_grid_rainfall_mm", "max_grid_rainfall_mm"):
                row[key] = _json_value(row[key])
        payload.update({"schema_version": "flood_areal_rainfall.v1", "record_count": len(rows),
                        "time_step_hours": 1, "unit": "mm/h", "records": rows})
        return payload

    def monitor(self) -> dict[str, Any]:
        paths = self._paths()
        observed = _read_csv(paths["observed"]) if paths["observed"].is_file() else []
        latest = observed[-1] if observed else {}
        return {
            "as_of": latest.get("time"),
            "stations": [
                {
                    "station_id": "xufan_70115800",
                    "name": "徐畈（二）出口控制断面",
                    "type": "section",
                    "value": _number(latest.get("observed_flow_m3_s")),
                    "unit": "m3/s",
                    "quality_flag": "OBSERVED",
                    "alert_level": "normal",
                }
            ],
        }

    def submit_manual_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Freeze the edited parameters before recalculating runoff and routing."""
        event_id = str(payload.get("event_id") or "xf-31170619")
        if event_id != "xf-31170619":
            raise ValueError("Only the 2017 event has gridded runoff forcing in this package")
        paths = self._paths()
        run_id = f"manual-{uuid.uuid4().hex[:12]}"
        with self._lock:
            if any(item.get("status") in {"QUEUED", "RUNNING"} for item in self._tasks.values()):
                raise RuntimeError("An integrated run is already active")
            xaj = deepcopy(_read_json(paths["xaj_parameters"]))
            routing = deepcopy(_read_json(paths["routing_parameters"]))
            task = {"run_id": run_id, "status": "QUEUED", "stage": "QUEUED",
                    "progress_percent": 0.0, "message": "Parameters frozen; waiting to run",
                    "event_id": event_id, "submitted_at": _now(),
                    "run_type": "manual_parameters"}
            self._tasks[run_id] = task
            self._futures[run_id] = self._executor.submit(
                self._execute_manual, run_id, xaj, routing)
        return dict(task)

    def _execute_manual(self, run_id: str, xaj: dict[str, Any],
                        routing: dict[str, Any]) -> None:
        output = self.project_root / "validation" / "manual_runs" / run_id
        try:
            output.mkdir(parents=True, exist_ok=False)
            self._write_json_atomic(output / "xaj_parameter_snapshot.json", xaj)
            self._write_json_atomic(output / "routing_parameter_snapshot.json", routing)
            self._update_task(run_id, status="RUNNING", stage="RUNOFF", progress_percent=10.0,
                              message="Recalculating 110 grid runoff series")
            self._write_manual_runoff(run_id, xaj, output / "runoff_components_wide.csv")
            config = deepcopy(self._config())
            relative = lambda path: path.relative_to(self.project_root).as_posix()
            config.update({"run_id": run_id, "runoff_wide": relative(output / "runoff_components_wide.csv"),
                           "routing_parameters": relative(output / "routing_parameter_snapshot.json"),
                           "xaj_parameter_set": relative(output / "xaj_parameter_snapshot.json"),
                           "output_dir": relative(output)})
            config_path = self.project_root / "config" / f"{run_id}.json"
            self._write_json_atomic(config_path, config)
            self._update_task(run_id, stage="ROUTING", progress_percent=65.0,
                              message="Routing the newly calculated runoff")
            from .pipeline import run_integrated_case
            run_integrated_case(config_path)
            self._update_task(run_id, status="COMPLETED", stage="COMPLETED",
                              progress_percent=100.0, message="Runoff and routing completed",
                              finished_at=_now(), result_available=True)
        except Exception as exc:
            self._update_task(run_id, status="FAILED", stage="FAILED",
                              progress_percent=100.0, message=f"Manual run failed: {exc}",
                              finished_at=_now(), result_available=False)

    def _write_manual_runoff(self, run_id: str, xaj: dict[str, Any], output: Path) -> None:
        """Use the same hourly XAJ kernel as grid-state replay with frozen inputs."""
        paths = self._paths()
        forcing_path = self.project_root / "data_interface" / "runoff_2017" / "forcing_event_by_grid.csv"
        initial_path = forcing_path.parent / "initial_state_before_event.csv"
        forcing = pd.read_csv(forcing_path)
        static = {row["unit_id"]: row for row in _read_csv(paths["grid_static"])}
        initial = {row["unit_id"]: row for row in _read_csv(initial_path)}
        values = xaj.get("land_parameters", {})
        params = LandParams()
        for item in fields(LandParams):
            if item.name in values and item.name not in {"phase", "snow", "pet", "conversion"}:
                setattr(params, item.name, float(values[item.name]))
        conversion = values.get("conversion", {})
        if isinstance(conversion, dict):
            params.conversion = RunoffConversionParams(**{
                field.name: conversion[field.name] for field in fields(RunoffConversionParams)
                if field.name in conversion})
        columns = ["time", "unit_id", "zone_type", "area_km2", "elevation_m",
                   "slope_degree", "RS_mm", "RI_mm", "RG_mm", "total_runoff_mm",
                   "water_balance_error", "quality_flag", "param_set_id"]
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for unit_id, group in forcing.groupby("unit_id", sort=True):
                srow = static[unit_id]
                irow = initial.get(unit_id, {})
                grid = GridInfo(unit_id=unit_id, zone_type="NON_GLACIER",
                                area_km2=_number(srow["area_km2"]),
                                elevation_m=_number(srow.get("elevation_m")),
                                slope_degree=_number(srow.get("slope_degree"), 10.0))
                state = LandState(**{key: _number(irow.get(key), getattr(LandState(), key))
                                     for key in ("swe", "snow_liquid", "WU", "WL", "WD", "S", "FR")})
                meteo = group.rename(columns={"precipitation_mm": "precipitation",
                                               "temperature_c_assumed": "temperature",
                                               "pet_mm_h_assumed": "pet_mm_h"})
                meteo["time"] = pd.to_datetime(meteo["time"])
                meteo = meteo.set_index("time").sort_index()
                result = simulate_land(meteo, params, state, grid,
                                       RunContext(run_id=run_id, scenario_id="MANUAL",
                                                  param_set_id=xaj.get("param_set_id", run_id)))
                for time, value in result.iterrows():
                    writer.writerow({"time": time.isoformat(), "unit_id": unit_id,
                                     "zone_type": "NON_GLACIER", "area_km2": grid.area_km2,
                                     "elevation_m": grid.elevation_m,
                                     "slope_degree": grid.slope_degree,
                                     "RS_mm": value.RS, "RI_mm": value.RI, "RG_mm": value.RG,
                                     "total_runoff_mm": value.total_runoff,
                                     "water_balance_error": value.water_balance_error,
                                     "quality_flag": value.quality_flag,
                                     "param_set_id": xaj.get("param_set_id", run_id)})

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "xf-31170619")
        if event_id != "xf-31170619":
            raise ValueError(
                f"场次 {event_id} 尚未生成产流与河段汇流输入，不能调用 2017 整合方案"
            )
        task_id = str(payload.get("run_id") or f"local-run-{uuid.uuid4().hex[:10]}")
        with self._lock:
            if any(item.get("status") in {"QUEUED", "RUNNING"} for item in self._tasks.values()):
                raise RuntimeError("已有产汇流任务正在运行")
            self._tasks[task_id] = {
                "run_id": task_id,
                "status": "QUEUED",
                "stage": "QUEUED",
                "progress_percent": 0.0,
                "message": "已创建本地整合运行任务",
                "event_id": event_id,
                "submitted_at": _now(),
            }
            self._futures[task_id] = self._executor.submit(self._execute, task_id)
        return dict(self._tasks[task_id])

    def _execute(self, task_id: str) -> None:
        try:
            self._update_task(task_id, status="RUNNING", stage="PREPARING", progress_percent=10.0)
            from .pipeline import run_integrated_case

            self._update_task(task_id, stage="RUNNING", progress_percent=35.0, message="正在执行产流到河段汇流链")
            run_integrated_case(self.default_config)
            self._update_task(task_id, status="COMPLETED", stage="COMPLETED", progress_percent=100.0, message="产汇流运行完成")
        except Exception as exc:
            self._update_task(task_id, status="FAILED", stage="FAILED", progress_percent=100.0, message=f"运行失败：{exc}")

    def _update_task(self, task_id: str, **updates: Any) -> None:
        with self._lock:
            self._tasks.setdefault(task_id, {}).update(updates)
            snapshot = dict(self._tasks[task_id])
        if MANUAL_RUN_ID.fullmatch(task_id):
            folder = self.project_root / "validation" / "manual_runs" / task_id
            if folder.is_dir():
                self._write_json_atomic(folder / "task.json", snapshot)

    def task(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if run_id in self._tasks:
                return dict(self._tasks[run_id])
        return self.run_detail(run_id)

    def events(self, run_id: str) -> list[dict[str, Any]]:
        task = self.task(run_id)
        return [
            {
                "event_id": f"{run_id}-status",
                "event_type": "RUN_STATUS",
                "occurred_at": _now(),
                "run_id": run_id,
                "payload_version": "1.0",
                "payload": task,
            }
        ]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)


__all__ = ["IntegratedResultsService"]
