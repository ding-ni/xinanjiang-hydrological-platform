"""Control-section contributing-area calculations shared by diagnostics."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

import numpy as np

from .topology import Topology


def contributing_reach_ids(topology: Topology, control_reach_id: str) -> set[str]:
    """Return all reaches whose downstream chain reaches the control section."""
    if control_reach_id not in topology.reaches:
        raise ValueError(f"control reach not found in topology: {control_reach_id}")
    result: set[str] = set()
    for start_reach_id in topology.reaches:
        current = start_reach_id
        visited: set[str] = set()
        while current is not None and current not in visited:
            if current == control_reach_id:
                result.add(start_reach_id)
                break
            visited.add(current)
            current = topology.reaches[current].downstream_reach_id
    return result


def contributing_grid_areas(
    mapping_path: str | Path,
    topology: Topology,
    control_reach_id: str,
    grid_ids: Iterable[str],
) -> np.ndarray:
    """Return each source grid's mapped area upstream of a control reach."""
    ordered_grid_ids = [str(value) for value in grid_ids]
    grid_index = {grid_id: index for index, grid_id in enumerate(ordered_grid_ids)}
    upstream_reaches = contributing_reach_ids(topology, control_reach_id)
    upstream_units = {
        unit_id
        for unit_id, unit in topology.units.items()
        if unit.reach_id in upstream_reaches
    }
    areas = np.zeros(len(ordered_grid_ids), dtype=float)
    with Path(mapping_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            grid_id = str(row["grid_id"])
            if grid_id in grid_index and str(row["unit_id"]) in upstream_units:
                areas[grid_index[grid_id]] += float(row["adjusted_overlap_area_km2"])
    if np.any(areas < 0.0) or not np.isfinite(areas).all():
        raise ValueError("control-section grid areas must be finite and non-negative")
    if float(np.sum(areas)) <= 0.0:
        raise ValueError("control section has no contributing mapped grid area")
    return areas


def contributing_topology_area(topology: Topology, control_reach_id: str) -> float:
    """Sum routing-unit area upstream of a control reach."""
    upstream_reaches = contributing_reach_ids(topology, control_reach_id)
    return float(
        sum(
            unit.area_km2
            for unit in topology.units.values()
            if unit.reach_id in upstream_reaches
        )
    )


__all__ = [
    "contributing_grid_areas",
    "contributing_reach_ids",
    "contributing_topology_area",
]
