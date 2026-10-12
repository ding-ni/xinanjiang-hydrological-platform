"""Flood-event runner with warm-up output exclusion."""
from __future__ import annotations

from .runner import SimulationRunner


class EventSimulationRunner:
    """[SIM-EVENT-01] Warm up state, then return only the selected event window."""

    def __init__(self, runner: SimulationRunner | None = None):
        self.runner = runner or SimulationRunner()

    def run(self, prepared_data, params, initial_state, grid, context=None):
        state = self.runner.isolated_state(initial_state)
        result = self.runner.run(prepared_data.forcing, params, state, grid, context)
        if prepared_data.evaluation_start is not None:
            result = result.loc[prepared_data.evaluation_start:]
        return result, state
