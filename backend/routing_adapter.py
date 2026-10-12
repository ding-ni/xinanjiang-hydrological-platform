"""Bind registered unit parameters to the unchanged delivered routing kernels."""
from __future__ import annotations

import copy
import sys
from types import SimpleNamespace

from common import MODEL

sys.path[:0] = [str(MODEL / "code"), str(MODEL / "scripts")]

import numpy as np
from run_formal_multievent_calibration import FastRoutingRunner
from xaj_routing.point_query import PointFlowQuery
from xaj_routing.spatial import assign_cells_to_reaches


class RegisteredRoutingRunner(FastRoutingRunner):
    def run(self, times, unit_rs, unit_ri, unit_rg, routing, state=None):
        overrides = routing.unit_component_slope_k_hours
        if not overrides:
            return super().run(times, unit_rs, unit_ri, unit_rg, routing, state)
        if set(overrides) - set(self.unit_ids):
            raise ValueError("逐单元坡面参数与计算拓扑不一致")
        # Only change the scalar/vector parameter binding, never the equations.
        bound = SimpleNamespace(
            default_slope_k_hours={
                component: np.asarray([routing.slope_k_seconds(unit, component) / 3600.0
                                       for unit in self.unit_ids], dtype=float)
                for component in ("RS", "RI", "RG")},
            reach_muskingum=routing.reach_muskingum)
        return super().run(times, unit_rs, unit_ri, unit_rg, bound, state)


def inherited_parameters(payload, source_units, target_units, source_assignments, target_assignments):
    """A temporary point split must refine, not merge, registered catchments."""
    sources = {row['reach_id']: row['unit_id'] for row in source_units}
    ancestry = {}
    for cell, target in target_assignments.items():
        source = source_assignments.get(cell)
        if target is None:
            if source is not None:
                raise ValueError("点查询切分丢失原汇流单元归属")
            continue
        if source is None:
            raise ValueError("点查询切分缺少原汇流单元归属")
        ancestry.setdefault(target, set()).add(source)
    bound = copy.deepcopy(payload)
    bound['unit_component_slope_k_hours'] = {}
    bound['reach_muskingum'] = {}
    for row in target_units:
        target = row['reach_id']
        parents = ancestry.get(target, set())
        if len(parents) != 1:
            raise ValueError("点查询切分不能唯一继承汇流参数，请检查控制点拓扑")
        parent = next(iter(parents))
        if parent not in sources or parent not in payload['reach_muskingum']:
            raise ValueError("点查询来源河段未登记")
        override = payload['unit_component_slope_k_hours'].get(sources[parent])
        if override:
            bound['unit_component_slope_k_hours'][row['unit_id']] = copy.deepcopy(override)
        bound['reach_muskingum'][target] = copy.deepcopy(payload['reach_muskingum'][parent])
    return bound


class RegisteredPointFlowQuery(PointFlowQuery):
    def _parameter_payload_for_topology(self, topology_result):
        assignments = assign_cells_to_reaches(topology_result.flow_dir, topology_result.stream_raster,
                                             topology_result.reaches, self.mask)
        return inherited_parameters(self.config, self.topology_result.units, topology_result.units,
                                    self.assignments, assignments)
