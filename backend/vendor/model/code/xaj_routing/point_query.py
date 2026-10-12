"""Point-flow query support for arbitrary locations inside the basin.

The production routing run remains the source of the hydrograph. This module
maps a requested point to the existing 30 m D8 topology and derives a point
hydrograph without changing the persisted production topology.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer

from .algorithms import linear_reservoir_step, muskingum_step
from .control_split import split_topology_at_control_points
from .engine import RoutingEngine
from .io import read_runoff_csv, read_state_json, write_component_flows_csv, write_json, write_reach_flows_csv
from .models import RoutingParameters
from .raster_formats import AsciiGrid, cell_center_xy, read_esri_ascii_grid
from .spatial import assign_cells_to_reaches, build_grid_topology, downstream_cell, valid_mask
from .topology import Topology


class PointQueryError(ValueError):
    """Raised when a point-flow request cannot be resolved."""


class PointFlowQuery:
    """Resolve point locations and derive point flow from a persisted run."""

    def __init__(self, project_root: str | Path, paths: dict[str, Path], config: dict[str, Any]):
        self.project_root = Path(project_root).resolve()
        self.paths = paths
        self.config = config
        self.grid: AsciiGrid = read_esri_ascii_grid(paths["dem"])
        mask = valid_mask(self.grid.elevation, self.grid.nodata)
        self.mask = mask
        self.base_topology_result = build_grid_topology(
            self.grid.elevation,
            self.grid.cellsize,
            float(config.get("stream_threshold_km2", 1.0)),
            "nan_an",
            str(config.get("spatial_version", "point_query_30m")),
            self.grid.nodata,
        )
        self.control_points = self._load_control_payload()
        self.topology_result = split_topology_at_control_points(
            self.base_topology_result,
            mask,
            self.control_points,
        )
        self.assignments = assign_cells_to_reaches(
            self.topology_result.flow_dir,
            self.topology_result.stream_raster,
            self.topology_result.reaches,
            mask,
        )
        self.reach_by_id = {reach.reach_id: reach for reach in self.topology_result.reaches}
        self.stream_cells = [cell for reach in self.topology_result.reaches for cell in reach.cells]
        self.transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
        self.inverse_transformer = Transformer.from_crs("EPSG:32650", "EPSG:4326", always_xy=True)
        self.default_width_m = float(config.get("default_channel_width_m", 20.0))
        self.default_velocity_kmh = float(config.get("default_channel_velocity_kmh", 3.0))

    def _load_control_payload(self) -> list[dict[str, Any]]:
        path = self.paths.get("control")
        if not path or not Path(path).is_file():
            return []
        import json

        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            return list(payload)
        if "control_points" in payload:
            return list(payload["control_points"])
        if "controls" in payload:
            return list(payload["controls"])
        return [payload]

    def _point_cell(self, lon: float, lat: float) -> tuple[int, int]:
        x, y = self.transformer.transform(lon, lat)
        col = int(math.floor((x - self.grid.xllcorner) / self.grid.cellsize))
        row = int(math.floor((self.grid.yllcorner + self.grid.nrows * self.grid.cellsize - y) / self.grid.cellsize))
        if row < 0 or row >= self.grid.nrows or col < 0 or col >= self.grid.ncols or not self.mask[row, col]:
            raise PointQueryError("点位不在当前 DEM 有效流域范围内")
        return row, col

    def _nearest_stream(self, cell: tuple[int, int]) -> tuple[tuple[int, int], str, float]:
        row, col = cell
        nearest, reach_id, distance = min(
            ((candidate, self.assignments.get(candidate), math.hypot(candidate[0] - row, candidate[1] - col) * self.grid.cellsize)
             for candidate in self.stream_cells if self.assignments.get(candidate)),
            key=lambda item: item[2],
        )
        return nearest, str(reach_id), float(distance)

    def _path_to_stream(self, start: tuple[int, int]) -> tuple[list[tuple[int, int]], str | None]:
        path: list[tuple[int, int]] = []
        current = start
        visited: set[tuple[int, int]] = set()
        while current not in visited:
            visited.add(current)
            path.append(current)
            if self.topology_result.stream_raster[current] and current in self.reach_by_cell:
                return path, self.reach_by_cell[current]
            current = downstream_cell(self.topology_result.flow_dir, current[0], current[1], self.mask)
            if current is None:
                break
        return path, self.assignments.get(start)

    @property
    def reach_by_cell(self) -> dict[tuple[int, int], str]:
        if not hasattr(self, "_reach_by_cell"):
            self._reach_by_cell = {cell: reach.reach_id for reach in self.topology_result.reaches for cell in reach.cells}
        return self._reach_by_cell

    def _load_reach_flow(self, run_id: str) -> dict[str, list[dict[str, str]]]:
        path = self.paths["output"] / "reach_flow.csv"
        if not path.is_file():
            raise PointQueryError("当前运行没有 reach_flow.csv 结果产品")
        rows = []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if rows and run_id and any(row.get("run_id") not in (None, "", run_id) for row in rows):
            rows = [row for row in rows if row.get("run_id") in (None, "", run_id)]
        result: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            result.setdefault(str(row["reach_id"]), []).append(row)
        return result

    def _load_component_flow(self, run_id: str, reach_id: str) -> dict[str, list[dict[str, str]]]:
        path = self.paths["output"] / "component_flow.csv"
        if not path.is_file():
            return {}
        result: dict[str, list[dict[str, str]]] = {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("run_id") not in (None, "", run_id) or row.get("reach_id") != reach_id:
                    continue
                result.setdefault(row["time"], []).append(row)
        return result

    @staticmethod
    def _muskingum_series(inflow: list[float], k_hours: float, x: float, dt_hours: float) -> list[float]:
        previous_inflow = 0.0
        previous_outflow = 0.0
        output: list[float] = []
        for current in inflow:
            value = muskingum_step(current, previous_inflow, previous_outflow, k_hours * 3600.0, x, dt_hours * 3600.0)
            output.append(max(0.0, value))
            previous_inflow, previous_outflow = current, value
        return output

    def _parameter_payload_for_topology(self, topology_result) -> dict[str, Any]:
        """Fill parameters for temporary split reaches from their base reach."""
        payload = {
            "time_step_hours": float(self.config.get("time_step_hours", 1.0)),
            "default_slope_k_hours": dict(self.config.get("default_slope_k_hours", {})),
            "unit_component_slope_k_hours": dict(self.config.get("unit_component_slope_k_hours", {})),
            "reach_muskingum": {
                str(key): dict(value)
                for key, value in self.config.get("reach_muskingum", {}).items()
            },
        }
        base_lengths = {reach.reach_id: reach.length_km for reach in self.base_topology_result.reaches}
        for reach in topology_result.reaches:
            if reach.reach_id in payload["reach_muskingum"]:
                continue
            source_id = None
            for base_id in base_lengths:
                if reach.reach_id.startswith(f"{base_id}_") or reach.reach_id.startswith(f"{base_id}__"):
                    source_id = base_id
                    break
            source = payload["reach_muskingum"].get(source_id or "")
            if source is None and source_id:
                source = payload["reach_muskingum"].get(f"{source_id}_UP") or payload["reach_muskingum"].get(f"{source_id}_DOWN")
            payload["reach_muskingum"][reach.reach_id] = dict(source or {"k_hours": 1.0, "x": 0.2})
        return payload

    def _rerun_split_point(
        self,
        topology_result,
        controls: list[dict[str, Any]],
        upstream_reach_id: str,
        run_id: str,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Rebuild the spatial mapping and run routing for a split query point."""
        required = ("runoff_wide", "runoff_grid_geojson", "grid_static")
        missing = [key for key in required if not self.paths.get(key)]
        if missing:
            raise PointQueryError(f"缺少任意点重跑所需输入：{', '.join(missing)}")
        for key in required:
            if not Path(self.paths[key]).is_file():
                raise PointQueryError(f"任意点重跑输入不存在：{self.paths[key]}")

        from integration.runoff_adapter import aggregate_runoff_to_reach_segments
        from integration.spatial_mapping import build_grid_to_unit_weights

        output_dir.mkdir(parents=True, exist_ok=True)
        topology_path = output_dir / "temporary_split_topology.json"
        topology_payload = topology_result.to_mapping(include_grids=False)
        topology_payload.update(
            {
                "topology_status": "TEMPORARY_CONTROL_SPLIT_RERUN",
                "run_id": run_id,
                "control_points": controls,
            }
        )
        write_json(topology_path, topology_payload)
        mapping_path = output_dir / "grid_to_unit_weight.csv"
        mapping_summary = build_grid_to_unit_weights(
            self.paths["dem"],
            topology_path,
            self.paths["runoff_grid_geojson"],
            self.paths["grid_static"],
            mapping_path,
            float(self.config.get("stream_threshold_km2", 1.0)),
            controls=controls,
        )
        routing_input_path = output_dir / "runoff_by_routing_unit.csv"
        adapter_summary = aggregate_runoff_to_reach_segments(
            self.paths["runoff_wide"],
            mapping_path,
            topology_path,
            routing_input_path,
        )
        state_path = self.paths.get("initial_routing_state")
        engine = RoutingEngine(
            topology=Topology.from_mapping(topology_payload),
            parameters=RoutingParameters.from_mapping(self._parameter_payload_for_topology(topology_result)),
            initial_state=read_state_json(state_path if state_path and Path(state_path).is_file() else None),
            control_points=[],
        )
        result = engine.run(read_runoff_csv(routing_input_path))
        component_path = output_dir / "component_flow.csv"
        reach_path = output_dir / "reach_flow.csv"
        state_output_path = output_dir / "final_routing_state.json"
        write_component_flows_csv(component_path, result.component_flows, run_id)
        write_reach_flows_csv(reach_path, result.reach_flows, run_id)
        write_json(state_output_path, result.final_state.to_mapping())
        rows = [row for row in result.reach_flows if row.reach_id == upstream_reach_id]
        if not rows:
            raise PointQueryError(f"切分重跑没有生成上游河段 {upstream_reach_id} 的过程线")
        return {
            "time": [row.time.isoformat(sep=" ") for row in rows],
            "flow_cms": [float(row.outflow_cms) for row in rows],
            "rerun_id": run_id,
            "output_dir": str(output_dir),
            "mapping": mapping_summary,
            "adapter": adapter_summary,
        }

    def query(
        self,
        *,
        lon: float,
        lat: float,
        channel_width_m: float | None = None,
        run_id: str = "",
        rerun_output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise PointQueryError("经纬度范围无效")
        width = self.default_width_m if channel_width_m in (None, 0) else float(channel_width_m)
        if width <= 0 or width > 1000:
            raise PointQueryError("河道宽度必须在 0 到 1000 m 之间")
        cell = self._point_cell(float(lon), float(lat))
        nearest_cell, nearest_reach, centerline_distance = self._nearest_stream(cell)
        effective_half_width = max(width / 2.0, self.grid.cellsize / 2.0)
        point_type = "RIVER" if centerline_distance <= effective_half_width else "SLOPE"
        path, path_reach = self._path_to_stream(cell)
        reach_id = nearest_reach if point_type == "RIVER" else path_reach
        if not reach_id or reach_id not in self.reach_by_id:
            raise PointQueryError("点位 D8 路径未能连接到提取河网")
        accumulation = float(self.topology_result.accumulation_area_km2[cell])
        reach = self.reach_by_id[reach_id]
        flow_by_reach = self._load_reach_flow(run_id)
        rows = flow_by_reach.get(reach_id, [])
        times = [row["time"] for row in rows]
        reach_inflow = [float(row.get("total_inflow_cms") or 0.0) for row in rows]
        reach_outflow = [float(row.get("outflow_cms") or 0.0) for row in rows]
        params = self.config.get("reach_muskingum", {}).get(reach_id, {"k_hours": 1.0, "x": 0.2})
        topology_action = "NO_RIVER_SPLIT_REQUIRED"
        control_node_id = None
        upstream_reach_id = None
        downstream_reach_id = None
        requires_rerun = False
        temporary = None
        rerun_controls = self.control_points
        if point_type == "RIVER":
            original_reach_id = next(
                (
                    item.reach_id
                    for item in self.base_topology_result.reaches
                    if nearest_cell in item.cells
                ),
                None,
            )
            point_control_id = f"point_{cell[0]}_{cell[1]}"
            point_control = {
                "control_id": point_control_id,
                "name": "任意点临时控制节点",
                "snapped": {
                    "reach_id": original_reach_id,
                    "cell": [nearest_cell[0], nearest_cell[1]],
                },
            }
            point_node = None
            for node in self.topology_result.control_nodes:
                if node.get("cell") == [nearest_cell[0], nearest_cell[1]]:
                    point_node = node
                    topology_action = "EXISTING_CONTROL_NODE"
                    break
            if point_node is None:
                temporary = split_topology_at_control_points(
                    self.base_topology_result,
                    self.mask,
                    [*self.control_points, point_control],
                )
                for node in temporary.control_nodes:
                    if node["control_id"] == point_control_id:
                        point_node = node
                        break
                topology_action = "TEMPORARY_CONTROL_NODE_SPLIT"
            if point_node is None:
                raise PointQueryError("任意点未能生成临时控制节点")
            control_node_id = point_node["node_id"]
            upstream_reach_id = point_node.get("upstream_reach_id")
            downstream_reach_id = point_node.get("downstream_reach_id")
            split_rows = flow_by_reach.get(str(upstream_reach_id), [])
            distance_to_channel = centerline_distance
            if split_rows:
                times = [row["time"] for row in split_rows]
                flow = [float(row.get("outflow_cms") or 0.0) for row in split_rows]
                method = "FORMAL_CONTROL_SPLIT_REACH_OUTPUT"
                requires_rerun = False
                details = {
                    "control_node_id": control_node_id,
                    "upstream_reach_id": upstream_reach_id,
                    "downstream_reach_id": downstream_reach_id,
                    "routing_source": "PERSISTED_SPLIT_TOPOLOGY",
                    "channel_width_m": width,
                }
            else:
                if temporary is None:
                    temporary = self.topology_result
                    topology_action = "EXISTING_CONTROL_NODE_RERUN"
                else:
                    rerun_controls = [*self.control_points, point_control]
                rerun_id = f"{run_id or self.config.get('run_id', 'integrated_run')}__point_{cell[0]}_{cell[1]}"
                rerun_dir = Path(rerun_output_dir) if rerun_output_dir else self.project_root / "data_products" / "point_flow_queries" / rerun_id
                try:
                    rerun = self._rerun_split_point(
                        temporary,
                        list(rerun_controls),
                        str(upstream_reach_id),
                        rerun_id,
                        rerun_dir,
                    )
                except Exception as exc:
                    if isinstance(exc, PointQueryError):
                        raise
                    raise PointQueryError(f"任意点切分后重跑失败：{exc}") from exc
                times = rerun["time"]
                flow = rerun["flow_cms"]
                method = "TEMPORARY_CONTROL_SPLIT_RERUN"
                requires_rerun = False
                details = {
                    "control_node_id": control_node_id,
                    "upstream_reach_id": upstream_reach_id,
                    "downstream_reach_id": downstream_reach_id,
                    "routing_source": "POINT_QUERY_RERUN",
                    "rerun_id": rerun["rerun_id"],
                    "rerun_status": "COMPLETED",
                    "mapping_row_count": rerun["mapping"]["mapping_row_count"],
                    "mapping_max_source_fraction_error": rerun["mapping"]["max_source_fraction_error"],
                    "channel_width_m": width,
                }
        else:
            unit_area = next((float(unit.get("area_km2", 0.0)) for unit in self._topology_units() if unit.get("reach_id") == reach_id), 0.0)
            area_fraction = min(1.0, accumulation / unit_area) if unit_area > 0 else 0.0
            component_rows = self._load_component_flow(run_id, reach_id)
            local = []
            for time in times:
                local_flow = sum(float(item.get("slope_outflow_cms") or 0.0) for item in component_rows.get(time, []))
                local.append(local_flow * area_fraction)
            distance_to_channel = max(len(path) - 1, 0) * self.grid.cellsize
            travel_hours = distance_to_channel / (self.default_velocity_kmh * 1000.0)
            k_hours = max(travel_hours, 0.05)
            flow = self._muskingum_series(local, k_hours, 0.2, float(self.config.get("time_step_hours", 1.0)))
            method = "SLOPE_D8_SUBCATCHMENT_LINEAR_RESERVOIR"
            details = {"area_fraction_of_local_reach_unit": round(area_fraction, 6), "travel_hours_to_channel": round(travel_hours, 4), "default_velocity_kmh": self.default_velocity_kmh, "channel_width_m_for_classification": width}
        x, y = cell_center_xy(self.grid, *cell)
        out_lon, out_lat = self.inverse_transformer.transform(x, y)
        return {
            "schema_version": "point_flow_query.v1",
            "run_id": run_id or str(self.config.get("run_id", "")),
            "point": {"lon": float(lon), "lat": float(lat), "snapped_lon": float(out_lon), "snapped_lat": float(out_lat), "cell": list(cell)},
            "classification": {"point_type": point_type, "reach_id": reach_id, "nearest_stream_cell": list(nearest_cell), "centerline_distance_m": round(centerline_distance, 3), "distance_to_channel_m": round(distance_to_channel, 3), "channel_width_m": width, "effective_half_width_m": effective_half_width},
            "catchment": {"point_accumulation_area_km2": round(accumulation, 6), "target_reach_incremental_area_km2": round(next((float(unit.get("area_km2", 0.0)) for unit in self._topology_units() if unit.get("reach_id") == reach_id), 0.0), 6), "path_cell_count": len(path)},
            "method": method,
            "method_details": details,
            "series": {"time": times, "flow_cms": flow, "unit": "m3/s"},
            "topology": {
                "topology_action": topology_action,
                "control_node_id": control_node_id,
                "upstream_reach_id": upstream_reach_id,
                "downstream_reach_id": downstream_reach_id,
                "split_status": (
                    "RERUN_COMPLETED"
                    if method == "TEMPORARY_CONTROL_SPLIT_RERUN"
                    else (
                        "FORMAL_SPLIT_RESULT"
                        if method == "FORMAL_CONTROL_SPLIT_REACH_OUTPUT"
                        else ("REQUIRES_RERUN" if requires_rerun else "NOT_APPLICABLE")
                    )
                ),
                "requires_rerun": requires_rerun,
            },
            "quality": {"status": "RERUN_COMPLETED" if method == "TEMPORARY_CONTROL_SPLIT_RERUN" else ("FORMAL_SPLIT_RESULT" if not requires_rerun and point_type == "RIVER" else "ESTIMATED_POINT_FLOW"), "warning": "任意点已按控制节点规则重构拓扑并重新执行汇流。" if method == "TEMPORARY_CONTROL_SPLIT_RERUN" else ("任意点结果来自已持久化的控制节点切分河段。" if point_type == "RIVER" else "任意点为坡面点，结果由 D8 路径和坡面线性水库计算。")},
        }

    def _topology_units(self) -> list[dict[str, Any]]:
        import json
        return json.loads(self.paths["topology"].read_text(encoding="utf-8-sig")).get("units", [])
