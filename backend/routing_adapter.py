"""Bind registered unit parameters to the unchanged delivered routing kernels."""
from __future__ import annotations

import copy
import sys

from common import MODEL

sys.path[:0] = [str(MODEL / "code"), str(MODEL / "scripts")]

from run_formal_multievent_calibration import FastRoutingRunner
from xaj_routing.point_query import PointFlowQuery
from xaj_routing.spatial import assign_cells_to_reaches


class RegisteredRoutingRunner(FastRoutingRunner):
    """Stable public adapter; the shared fast runner now resolves unit K itself."""


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
