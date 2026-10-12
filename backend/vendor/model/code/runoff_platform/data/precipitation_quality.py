"""Shared readers for precipitation quality fields from external NetCDF files."""

from __future__ import annotations

from typing import Any

import numpy as np


STATION_COUNT_VARIABLES = ("source_station_count", "reporting_station_count")


def station_count_variable(dataset: Any) -> str | None:
    """Return the supported station-count variable present in a dataset."""
    return next((name for name in STATION_COUNT_VARIABLES if name in dataset), None)


def read_station_count(
    dataset: Any,
    selection: Any = slice(None),
    *,
    required: bool = False,
) -> tuple[np.ndarray, str | None]:
    """Read either supported station-count field with one stable contract."""
    name = station_count_variable(dataset)
    if name is None:
        if required:
            fields = ", ".join(STATION_COUNT_VARIABLES)
            raise ValueError(f"precipitation station-count field missing; expected one of: {fields}")
        length = int(np.asarray(dataset["time"].values[selection]).size)
        return np.full(length, -1, dtype=np.int16), None
    values = np.asarray(dataset[name].values[selection], dtype=np.int16)
    return values, name


def precipitation_quality_code(station_count: np.ndarray) -> np.ndarray:
    """Derive the platform quality code when an external file omits it."""
    counts = np.asarray(station_count, dtype=np.int16)
    return np.where(counts <= 0, 0, np.where(counts == 1, 1, 2)).astype(np.int8)


__all__ = [
    "STATION_COUNT_VARIABLES",
    "precipitation_quality_code",
    "read_station_count",
    "station_count_variable",
]
