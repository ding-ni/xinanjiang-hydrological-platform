"""Control-point-aware splitting of DEM-derived routing reaches."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import numpy as np

from .spatial import GridTopologyResult, ReachSegment, build_unit_info


def _control_id(control: dict[str, Any]) -> str:
    value = control.get("control_id") or control.get("id") or control.get("code")
    if not value:
        raise ValueError("control point requires control_id")
    return str(value).strip()


def _control_cell(control: dict[str, Any]) -> tuple[int, int]:
    snapped = control.get("snapped") or {}
    cell = control.get("cell") or snapped.get("cell")
    if not isinstance(cell, (list, tuple)) or len(cell) != 2:
        raise ValueError(f"control {_control_id(control)} requires snapped.cell")
    return int(cell[0]), int(cell[1])


def _control_reach_id(control: dict[str, Any]) -> str | None:
    snapped = control.get("snapped") or {}
    value = control.get("reach_id") or snapped.get("reach_id")
    return None if value in (None, "") else str(value).strip()


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    return normalized or "CONTROL"


def _segment_ids(reach_id: str, controls: list[tuple[int, str]]) -> list[str]:
    if len(controls) == 1:
        return [f"{reach_id}_UP", f"{reach_id}_DOWN"]
    ids = [f"{reach_id}__UP__{_safe_id(controls[0][1])}"]
    for previous, current in zip(controls, controls[1:]):
        ids.append(f"{reach_id}__{_safe_id(previous[1])}__{_safe_id(current[1])}")
    ids.append(f"{reach_id}__DOWN__{_safe_id(controls[-1][1])}")
    return ids


def split_topology_at_control_points(
    result: GridTopologyResult,
    mask: np.ndarray,
    controls: list[dict[str, Any]],
    zone_type: str = "non_glacier",
) -> GridTopologyResult:
    """Split every affected reach at its snapped control cells.

    The input is an unsplit DEM topology. A control point must contain a
    snapped cell and may contain a snapped reach id. The control cell belongs
    to the upstream segment, so its outflow is the flow at the control node.
    """

    if not controls:
        return result

    reach_by_id = {reach.reach_id: reach for reach in result.reaches}
    control_groups: dict[str, list[tuple[int, str, tuple[int, int], dict[str, Any]]]] = {}
    seen_control_ids: set[str] = set()
    for control in controls:
        control_id = _control_id(control)
        if control_id in seen_control_ids:
            raise ValueError(f"duplicate control point id: {control_id}")
        seen_control_ids.add(control_id)
        cell = _control_cell(control)
        reach_id = _control_reach_id(control)
        if reach_id is None:
            reach_id = next(
                (reach.reach_id for reach in result.reaches if cell in reach.cells),
                None,
            )
        if reach_id not in reach_by_id:
            raise ValueError(f"control {control_id} references missing reach {reach_id}")
        reach = reach_by_id[reach_id]
        if cell not in reach.cells:
            raise ValueError(f"control {control_id} cell {cell} is not on {reach_id}")
        index = reach.cells.index(cell)
        control_groups.setdefault(reach_id, []).append((index, control_id, cell, control))

    split_reaches_by_original: dict[str, list[ReachSegment]] = {}
    control_nodes: list[dict[str, Any]] = []
    first_replacement: dict[str, str] = {}

    for original_id, group in control_groups.items():
        target = reach_by_id[original_id]
        group.sort(key=lambda item: (item[0], item[1]))
        if len({item[0] for item in group}) != len(group):
            raise ValueError(f"multiple control points share a stream cell on {original_id}")
        indexed_controls = [(item[0], item[1]) for item in group]
        ids = _segment_ids(original_id, indexed_controls)
        boundaries = [-1] + [item[0] for item in group]
        segments: list[ReachSegment] = []
        segment_by_control_index: dict[int, ReachSegment] = {}
        for segment_index, reach_id in enumerate(ids):
            start_index = boundaries[segment_index] + 1
            end_index = (
                boundaries[segment_index + 1]
                if segment_index + 1 < len(boundaries)
                else len(target.cells) - 1
            )
            if start_index > end_index:
                continue
            cells = tuple(target.cells[start_index : end_index + 1])
            downstream_id = (
                ids[segment_index + 1]
                if segment_index + 1 < len(ids)
                else target.downstream_reach_id
            )
            segment = ReachSegment(
                reach_id=reach_id,
                start_cell=cells[0],
                end_cell=cells[-1],
                cells=cells,
                downstream_reach_id=downstream_id,
                length_km=max(len(cells) - 1, 1) * result.cell_size_m / 1000.0,
            )
            segments.append(segment)
            for control_index, _, _, _ in group:
                if start_index <= control_index <= end_index:
                    segment_by_control_index[control_index] = segment

        if not segments:
            raise ValueError(f"control split produced no segments for {original_id}")
        split_reaches_by_original[original_id] = segments
        first_replacement[original_id] = segments[0].reach_id

        for index, control_id, cell, control in group:
            upstream = segment_by_control_index[index]
            segment_position = segments.index(upstream)
            downstream = (
                segments[segment_position + 1]
                if segment_position + 1 < len(segments)
                else None
            )
            control_nodes.append(
                {
                    "node_id": f"N_CONTROL_{_safe_id(control_id)}",
                    "control_id": control_id,
                    "name": control.get("name") or control_id,
                    "cell": [cell[0], cell[1]],
                    "original_reach_id": original_id,
                    "split_index": index,
                    "upstream_reach_id": upstream.reach_id,
                    "downstream_reach_id": downstream.reach_id if downstream else None,
                    "status": "CONTROL_NODE_SPLIT",
                    "snap_distance_m": (control.get("snapped") or {}).get("distance_m"),
                }
            )

    new_reaches: list[ReachSegment] = []
    for reach in result.reaches:
        replacements = split_reaches_by_original.get(reach.reach_id)
        if replacements:
            new_reaches.extend(replacements)
            continue
        downstream = first_replacement.get(
            reach.downstream_reach_id or "", reach.downstream_reach_id
        )
        new_reaches.append(
            ReachSegment(
                reach_id=reach.reach_id,
                start_cell=reach.start_cell,
                end_cell=reach.end_cell,
                cells=reach.cells,
                downstream_reach_id=downstream,
                length_km=reach.length_km,
            )
        )

    units = build_unit_info(
        basin_id=result.basin_id,
        spatial_version=result.spatial_version + "_control_nodes_v1",
        dem=result.filled_dem,
        slope_deg=result.slope_deg,
        aspect_deg=result.aspect_deg,
        flow_dir=result.flow_dir,
        stream_raster=result.stream_raster,
        reaches=new_reaches,
        mask=np.asarray(mask, dtype=bool),
        cell_size_m=result.cell_size_m,
        zone_type=zone_type,
    )
    return replace(
        result,
        spatial_version=result.spatial_version + "_control_nodes_v1",
        reaches=new_reaches,
        units=units,
        control_nodes=sorted(control_nodes, key=lambda item: item["control_id"]),
    )


__all__ = ["split_topology_at_control_points"]
