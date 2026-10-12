"""Continuous-simulation data preparation."""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

from .contracts import PreparedData
from .quality_control import merge_forcing, normalize_forcing


class ContinuousDataProvider:
    """[DATA-CONT-01] Prepare uninterrupted hourly forcing across files/years."""

    def from_frame(self, forcing: pd.DataFrame, observation: Optional[pd.Series] = None) -> PreparedData:
        data = normalize_forcing(forcing)
        obs = observation.sort_index() if observation is not None else None
        return PreparedData(data, obs, {"simulation_mode": "CONTINUOUS"})

    def from_frames(self, forcing_frames: Iterable[pd.DataFrame], observation=None) -> PreparedData:
        data = merge_forcing(forcing_frames)
        obs = observation.sort_index() if observation is not None else None
        return PreparedData(data, obs, {"simulation_mode": "CONTINUOUS"})
