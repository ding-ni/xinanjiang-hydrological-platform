"""[DATA-01] Stable contracts between data, model and calibration layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd


@dataclass(frozen=True)
class EventWindow:
    event_id: str
    start: pd.Timestamp
    end: pd.Timestamp
    warmup_hours: int = 0

    def __post_init__(self):
        if self.end < self.start:
            raise ValueError("event end must not be earlier than start")
        if self.warmup_hours < 0:
            raise ValueError("warmup_hours must be non-negative")


@dataclass
class PreparedData:
    forcing: pd.DataFrame
    observation: Optional[pd.Series] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    evaluation_start: Optional[pd.Timestamp] = None

    def evaluation_slice(self, values: pd.Series) -> pd.Series:
        if self.evaluation_start is None:
            return values
        return values.loc[self.evaluation_start:]
