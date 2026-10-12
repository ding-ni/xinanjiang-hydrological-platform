"""[DATA-QC-01] Data quality checks shared by both preparation modes."""
from __future__ import annotations

from typing import Iterable

import pandas as pd

from ..core.validation import ModelValidationError, validate_forcing


def normalize_forcing(frame: pd.DataFrame, time_column: str = "time") -> pd.DataFrame:
    data = frame.copy()
    if not isinstance(data.index, pd.DatetimeIndex):
        if time_column not in data.columns:
            raise ModelValidationError(f"missing time column: {time_column}")
        data[time_column] = pd.to_datetime(data[time_column], errors="raise")
        data = data.set_index(time_column)
    data = data.sort_index()
    validate_forcing(data)
    return data


def merge_forcing(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    items = [normalize_forcing(frame) for frame in frames]
    if not items:
        raise ModelValidationError("at least one forcing data frame is required")
    merged = pd.concat(items).sort_index()
    validate_forcing(merged)
    return merged
