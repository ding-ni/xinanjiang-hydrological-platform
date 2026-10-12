"""Topology helpers for routing units and river reaches."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .models import ReachInfo, UnitInfo


@dataclass(frozen=True)
class Topology:
    units: dict[str, UnitInfo]
    reaches: dict[str, ReachInfo]
    control_nodes: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Topology":
        units = {
            item["unit_id"]: UnitInfo.from_mapping(item)
            for item in data.get("units", [])
        }
        reaches = {
            item["reach_id"]: ReachInfo.from_mapping(item)
            for item in data.get("reaches", [])
        }
        control_nodes = tuple(data.get("control_nodes") or ())
        topology = cls(units=units, reaches=reaches, control_nodes=control_nodes)
        topology.validate()
        return topology

    def validate(self) -> None:
        for unit in self.units.values():
            if unit.area_km2 < 0:
                raise ValueError(f"unit {unit.unit_id} has negative area")
            if unit.reach_id and unit.reach_id not in self.reaches:
                raise ValueError(f"unit {unit.unit_id} references missing reach {unit.reach_id}")
        for reach in self.reaches.values():
            downstream = reach.downstream_reach_id
            if downstream and downstream not in self.reaches:
                raise ValueError(
                    f"reach {reach.reach_id} references missing downstream reach {downstream}"
                )
        self.reaches_upstream_first()

    def reaches_upstream_first(self) -> list[str]:
        indegree = {reach_id: 0 for reach_id in self.reaches}

        for reach_id, reach in self.reaches.items():
            downstream = reach.downstream_reach_id
            if downstream:
                indegree[downstream] += 1

        queue = deque(sorted([reach_id for reach_id, degree in indegree.items() if degree == 0]))
        ordered: list[str] = []

        downstream_by_reach = {
            reach_id: reach.downstream_reach_id for reach_id, reach in self.reaches.items()
        }
        while queue:
            reach_id = queue.popleft()
            ordered.append(reach_id)
            downstream = downstream_by_reach[reach_id]
            if downstream:
                indegree[downstream] -= 1
                if indegree[downstream] == 0:
                    queue.append(downstream)

        if len(ordered) != len(self.reaches):
            raise ValueError("reach topology contains a cycle")
        return ordered

    def outlet_reach_ids(self) -> list[str]:
        return sorted(
            reach_id
            for reach_id, reach in self.reaches.items()
            if reach.downstream_reach_id is None
        )

    def control_node(self, control_id: str) -> dict[str, Any] | None:
        """Return the persisted control node metadata, if present."""
        for node in self.control_nodes:
            if str(node.get("control_id")) == str(control_id):
                return node
        return None
