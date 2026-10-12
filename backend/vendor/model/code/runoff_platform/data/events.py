"""Flood-event data preparation with an explicit warm-up period."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .contracts import EventWindow, PreparedData
from .quality_control import normalize_forcing


class EventDataProvider:
    """[DATA-EVENT-01] Extract one event plus optional pre-event warm-up forcing."""

    def prepare(
        self,
        forcing: pd.DataFrame,
        event: EventWindow,
        observation: Optional[pd.Series] = None,
    ) -> PreparedData:
        data = normalize_forcing(forcing)
        warmup_start = event.start - pd.Timedelta(hours=event.warmup_hours)
        selected = data.loc[warmup_start:event.end]
        expected = int((event.end - warmup_start) / pd.Timedelta(hours=1)) + 1
        if len(selected) != expected:
            raise ValueError("event or warm-up forcing is incomplete")
        obs = observation.loc[event.start:event.end].sort_index() if observation is not None else None
        return PreparedData(
            selected,
            obs,
            {
                "simulation_mode": "EVENT",
                "event_id": event.event_id,
                "event_start": event.start.isoformat(),
                "event_end": event.end.isoformat(),
                "warmup_hours": event.warmup_hours,
            },
            evaluation_start=event.start,
        )
