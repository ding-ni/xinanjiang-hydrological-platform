"""Unified single-grid model runner."""
from __future__ import annotations

from copy import deepcopy

from ..core.types import GlacierState, LandState, RunContext
from ..models.glacier import simulate_glacier
from ..models.xaj import simulate_land


class SimulationRunner:
    """[SIM-01] Dispatch by zone type while preserving explicit state."""

    def run(self, forcing, params, state, grid, context: RunContext | None = None):
        context = context or RunContext()
        if grid.zone_type == "GLACIER":
            state = state or GlacierState()
            return simulate_glacier(forcing, params, state, grid, context)
        if grid.zone_type == "NON_GLACIER":
            state = state or LandState()
            return simulate_land(forcing, params, state, grid, context)
        raise ValueError(f"unsupported zone type: {grid.zone_type}")

    @staticmethod
    def isolated_state(state):
        return deepcopy(state)
