"""Continuous runner with state carry-over and optional checkpoints."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

from .runner import SimulationRunner


class ContinuousSimulationRunner:
    """[SIM-CONT-01] Run continuous forcing and expose final state/checkpoint."""

    def __init__(self, runner: SimulationRunner | None = None):
        self.runner = runner or SimulationRunner()

    def run(self, prepared_data, params, state, grid, context=None, checkpoint_path=None):
        result = self.runner.run(prepared_data.forcing, params, state, grid, context)
        if checkpoint_path is not None:
            self.save_checkpoint(checkpoint_path, state, result.index[-1])
        return result

    @staticmethod
    def save_checkpoint(path, state, time):
        payload = {"time": time.isoformat(), "state": asdict(state)}
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
