"""Core routing engine."""

from __future__ import annotations

from collections import defaultdict

from .algorithms import linear_reservoir_step, muskingum_step, runoff_mm_to_cms
from .models import (
    ControlFlow,
    ControlPoint,
    ComponentFlow,
    OutletFlow,
    ReachFlow,
    RoutingParameters,
    RoutingResult,
    RoutingState,
    RunoffRecord,
)
from .topology import Topology


class RoutingEngine:
    """Routes standard runoff components through slope and channel paths."""

    def __init__(
        self,
        topology: Topology,
        parameters: RoutingParameters,
        initial_state: RoutingState | None = None,
        control_points: list[ControlPoint] | None = None,
    ) -> None:
        self.topology = topology
        self.parameters = parameters
        self.state = initial_state or RoutingState()
        resolved_controls = []
        for control in control_points or ():
            node = self.topology.control_node(control.control_id)
            effective_reach_id = (
                str(node.get("upstream_reach_id"))
                if node and node.get("upstream_reach_id")
                else control.reach_id
            )
            if effective_reach_id not in self.topology.reaches:
                raise ValueError(
                    f"control point {control.control_id} references missing reach "
                    f"{effective_reach_id}"
                )
            if effective_reach_id != control.reach_id:
                from dataclasses import replace

                control = replace(control, reach_id=effective_reach_id)
            resolved_controls.append(control)
        self.control_points = tuple(resolved_controls)

    def run(self, records: list[RunoffRecord]) -> RoutingResult:
        records_by_time: dict[object, list[RunoffRecord]] = defaultdict(list)
        for record in records:
            if record.unit_id not in self.topology.units:
                raise ValueError(f"runoff record references missing unit {record.unit_id}")
            records_by_time[record.time].append(record)

        component_flows: list[ComponentFlow] = []
        reach_flows: list[ReachFlow] = []
        outlet_flows: list[OutletFlow] = []
        control_flows: list[ControlFlow] = []
        reach_order = self.topology.reaches_upstream_first()
        outlet_reach_ids = set(self.topology.outlet_reach_ids())
        controls_by_reach: dict[str, list[ControlPoint]] = defaultdict(list)
        for control in self.control_points:
            controls_by_reach[control.reach_id].append(control)

        for current_time in sorted(records_by_time):
            local_inflow_by_reach: dict[str, float] = defaultdict(float)

            for record in records_by_time[current_time]:
                unit = self.topology.units[record.unit_id]
                for _, component_type, runoff_mm in record.component_depths():
                    previous = self.state.slope_outflow_cms.get(record.unit_id, {}).get(
                        component_type, 0.0
                    )
                    if runoff_mm == 0.0 and previous == 0.0:
                        continue
                    inflow_cms = runoff_mm_to_cms(
                        runoff_mm,
                        unit.area_km2,
                        self.parameters.time_step_seconds,
                    )
                    slope_outflow = linear_reservoir_step(
                        inflow_cms=inflow_cms,
                        previous_outflow_cms=previous,
                        k_seconds=self.parameters.slope_k_seconds(
                            record.unit_id, component_type
                        ),
                        time_step_seconds=self.parameters.time_step_seconds,
                    )

                    self.state.slope_outflow_cms.setdefault(record.unit_id, {})[
                        component_type
                    ] = slope_outflow

                    if unit.reach_id:
                        local_inflow_by_reach[unit.reach_id] += slope_outflow

                    component_flows.append(
                        ComponentFlow(
                            time=current_time,
                            unit_id=record.unit_id,
                            reach_id=unit.reach_id,
                            component_type=component_type,
                            runoff_mm=runoff_mm,
                            inflow_cms=inflow_cms,
                            slope_outflow_cms=slope_outflow,
                        )
                    )

            upstream_outflow_by_reach: dict[str, float] = defaultdict(float)
            for reach_id in reach_order:
                reach = self.topology.reaches[reach_id]
                local_inflow = local_inflow_by_reach.get(reach_id, 0.0)
                total_inflow = local_inflow + upstream_outflow_by_reach.get(reach_id, 0.0)

                channel_params = self.parameters.reach_muskingum.get(reach_id)
                if channel_params:
                    outflow = muskingum_step(
                        current_inflow_cms=total_inflow,
                        previous_inflow_cms=self.state.reach_inflow_cms.get(reach_id, 0.0),
                        previous_outflow_cms=self.state.reach_outflow_cms.get(reach_id, 0.0),
                        k_seconds=channel_params.k_hours * 3600.0,
                        x=channel_params.x,
                        time_step_seconds=self.parameters.time_step_seconds,
                    )
                else:
                    outflow = total_inflow

                self.state.reach_inflow_cms[reach_id] = total_inflow
                self.state.reach_outflow_cms[reach_id] = outflow
                reach_flows.append(
                    ReachFlow(
                        time=current_time,
                        reach_id=reach_id,
                        local_inflow_cms=local_inflow,
                        total_inflow_cms=total_inflow,
                        outflow_cms=outflow,
                    )
                )
                for control in controls_by_reach.get(reach_id, []):
                    control_flows.append(
                        ControlFlow(
                            time=current_time,
                            control_id=control.control_id,
                            control_name=control.name,
                            reach_id=reach_id,
                            outflow_cms=outflow,
                        )
                    )

                if reach.downstream_reach_id:
                    upstream_outflow_by_reach[reach.downstream_reach_id] += outflow
                elif reach_id in outlet_reach_ids:
                    outlet_flows.append(
                        OutletFlow(
                            time=current_time,
                            outlet_id=reach_id,
                            outflow_cms=outflow,
                        )
                    )

        return RoutingResult(
            component_flows=component_flows,
            reach_flows=reach_flows,
            outlet_flows=outlet_flows,
            final_state=self.state,
            control_flows=control_flows,
        )
