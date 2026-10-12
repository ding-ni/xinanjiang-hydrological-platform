"""[DATA-READ-01] File readers isolated from model equations."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .quality_control import normalize_forcing


def read_forcing_csv(path, time_column: str = "time", encoding: str = "utf-8-sig") -> pd.DataFrame:
    return normalize_forcing(pd.read_csv(Path(path), encoding=encoding), time_column=time_column)


def read_observation_csv(
    path,
    time_column: str = "time",
    value_column: str = "observed",
    encoding: str = "utf-8-sig",
) -> pd.Series:
    data = pd.read_csv(Path(path), encoding=encoding)
    data[time_column] = pd.to_datetime(data[time_column], errors="raise")
    series = pd.to_numeric(data[value_column], errors="coerce")
    series.index = data[time_column]
    return series.sort_index().rename(value_column)
