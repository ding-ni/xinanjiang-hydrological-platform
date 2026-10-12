"""[STATE-INITIALIZATION-03] Routing-state initialization strategies."""

from __future__ import annotations

import math

from xaj_routing.models import ControlPoint, RoutingState
from xaj_routing.topology import Topology

from .core import StateInitializationError


def upstream_reaches(topology: Topology, control_reach_id: str) -> set[str]:
    if control_reach_id not in topology.reaches:
        raise StateInitializationError(
            f"control reach does not exist in topology: {control_reach_id}"
        )
    result = {control_reach_id}
    changed = True
    while changed:
        changed = False
        for reach_id, reach in topology.reaches.items():
            if reach.downstream_reach_id in result and reach_id not in result:
                result.add(reach_id)
                changed = True
    return result


# [STATE-INITIALIZATION-03D] Observed control-flow anchored baseflow state.
def build_observed_flow_anchored_state(
    topology: Topology,
    control_point: ControlPoint,
    observed_flow_cms: float,
) -> RoutingState:
    """Distribute control flow by upstream unit area and store it as RG state."""
    flow = float(observed_flow_cms)
    if not math.isfinite(flow) or flow < 0.0:
        raise StateInitializationError(
            "observed_flow_cms must be finite and non-negative"
        )
    if flow == 0.0:
        return RoutingState()

    upstream = upstream_reaches(topology, control_point.reach_id)
    units = [
        unit
        for unit in topology.units.values()
        if unit.reach_id in upstream and unit.area_km2 > 0.0
    ]
    total_area = sum(unit.area_km2 for unit in units)
    if total_area <= 0.0:
        raise StateInitializationError("control point has no positive-area upstream units")

    local_by_reach: dict[str, float] = {}
    state = RoutingState()
    for unit in units:
        share = flow * unit.area_km2 / total_area
        state.slope_outflow_cms[unit.unit_id] = {"RG": share}
        local_by_reach[unit.reach_id] = local_by_reach.get(unit.reach_id, 0.0) + share

    cumulative: dict[str, float] = {}
    for reach_id in topology.reaches_upstream_first():
        if reach_id not in upstream:
            continue
        inflow = local_by_reach.get(reach_id, 0.0) + cumulative.get(reach_id, 0.0)
        state.reach_inflow_cms[reach_id] = inflow
        state.reach_outflow_cms[reach_id] = inflow
        downstream = topology.reaches[reach_id].downstream_reach_id
        if downstream in upstream:
            cumulative[downstream] = cumulative.get(downstream, 0.0) + inflow

    anchored = state.reach_outflow_cms.get(control_point.reach_id, 0.0)
    if abs(anchored - flow) > max(1.0e-9, flow * 1.0e-9):
        raise StateInitializationError(
            f"routing anchor does not close at control reach: {anchored} != {flow}"
        )
    return state
