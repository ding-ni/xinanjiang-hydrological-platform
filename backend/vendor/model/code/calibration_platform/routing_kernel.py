"""Hourly slope-reservoir/Muskingum recurrence with per-unit/component K."""
import numpy as np

from runoff_platform.core.numeric_compilation import optional_njit


def route_arrays(depths, areas, unit_reach, downstream, decay, coefficients,
                 control_reach, slope, previous_in, previous_out):
    ntime, nunit, _ = depths.shape
    nreach = len(downstream)
    flow = np.zeros(ntime)
    for t in range(ntime):
        local = np.zeros(nreach)
        for u in range(nunit):
            for c in range(3):
                inflow = depths[t, u, c] * areas[u] * 1000.0 / 3600.0
                current = slope[u, c] * decay[u, c] + inflow * (1.0 - decay[u, c])
                slope[u, c] = current
                if unit_reach[u] >= 0:
                    local[unit_reach[u]] += current
        upstream = np.zeros(nreach)
        for r in range(nreach):
            total = local[r] + upstream[r]
            c0, c1, c2 = coefficients[r]
            outflow = c0 * total + c1 * previous_in[r] + c2 * previous_out[r]
            previous_in[r], previous_out[r] = total, outflow
            if downstream[r] >= 0:
                upstream[downstream[r]] += outflow
            if r == control_reach:
                flow[t] = outflow
    return flow, slope, previous_in, previous_out


python_route_arrays = route_arrays
route_arrays = optional_njit(route_arrays)
