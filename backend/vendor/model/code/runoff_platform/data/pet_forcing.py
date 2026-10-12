"""[PET-FORCING-02] Shared potential-evapotranspiration forcing interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


def _resolve(value: str, base: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


class PetProvider:
    """Resolve auditable hourly PET without coupling it to model equations.

    Supported modes are a constant diagnostic assumption, a 12-month
    climatological diagnostic assumption, and a real hourly CSV time series.
    Assumption modes are always marked as ineligible for production approval.
    """

    def __init__(self, config: Mapping[str, Any], base: str | Path):
        self.config = dict(config)
        self.base = Path(base).resolve()
        self.mode = str(self.config.get("mode", "")).upper()
        self.series: pd.Series | None = None
        self.value_mm_h: float | None = None
        self.monthly_values_mm_day: np.ndarray | None = None

        if self.mode == "CONSTANT_ASSUMPTION":
            value = float(self.config.get("value_mm_h", -1.0))
            if not np.isfinite(value) or value < 0.0:
                raise ValueError("constant PET value_mm_h must be finite and non-negative")
            self.value_mm_h = value
            self.production_eligible = False
        elif self.mode == "MONTHLY_CLIMATOLOGY":
            monthly = np.asarray(self.config.get("monthly_values_mm_day", []), dtype=float)
            if monthly.shape != (12,):
                raise ValueError("monthly PET requires exactly 12 monthly_values_mm_day values")
            if not np.isfinite(monthly).all() or np.any(monthly < 0.0):
                raise ValueError("monthly PET values must be finite and non-negative")
            self.monthly_values_mm_day = monthly
            self.production_eligible = False
        elif self.mode == "HOURLY_CSV":
            path = _resolve(str(self.config.get("path", "")), self.base)
            if not path.is_file():
                raise FileNotFoundError(f"hourly PET CSV not found: {path}")
            frame = pd.read_csv(path, encoding="utf-8-sig")
            time_column = str(self.config.get("time_column", "time"))
            value_column = str(self.config.get("value_column", "pet_mm_h"))
            if time_column not in frame or value_column not in frame:
                raise ValueError("hourly PET CSV is missing configured columns")
            frame[time_column] = pd.to_datetime(frame[time_column], errors="raise")
            values = pd.to_numeric(frame[value_column], errors="coerce")
            if values.isna().any() or (values < 0.0).any():
                raise ValueError("hourly PET CSV contains missing or negative values")
            if frame[time_column].duplicated().any():
                raise ValueError("hourly PET CSV contains duplicate timestamps")
            self.series = pd.Series(
                values.to_numpy(dtype=float),
                index=pd.DatetimeIndex(frame[time_column]),
            ).sort_index()
            self.production_eligible = bool(self.config.get("production_eligible", False))
        else:
            raise ValueError(
                "pet.mode must be CONSTANT_ASSUMPTION, MONTHLY_CLIMATOLOGY or HOURLY_CSV"
            )

    @classmethod
    def from_file(cls, path: str | Path) -> "PetProvider":
        config_path = Path(path).resolve()
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        return cls(config, config_path.parent)

    def values(self, times: pd.DatetimeIndex) -> np.ndarray:
        index = pd.DatetimeIndex(times)
        if index.has_duplicates:
            raise ValueError("requested PET timestamps contain duplicates")
        if self.mode == "CONSTANT_ASSUMPTION":
            assert self.value_mm_h is not None
            return np.full(len(index), self.value_mm_h, dtype=float)
        if self.mode == "MONTHLY_CLIMATOLOGY":
            assert self.monthly_values_mm_day is not None
            return self.monthly_values_mm_day[index.month.to_numpy() - 1] / 24.0
        assert self.series is not None
        aligned = self.series.reindex(index)
        if aligned.isna().any():
            missing = int(aligned.isna().sum())
            raise ValueError(f"hourly PET does not cover forcing: {missing} hours missing")
        return aligned.to_numpy(dtype=float)

    def report(self) -> dict[str, Any]:
        result = dict(self.config)
        result["mode"] = self.mode
        result["production_eligible"] = self.production_eligible
        if self.mode == "MONTHLY_CLIMATOLOGY":
            result["hourly_conversion"] = "monthly_values_mm_day / 24"
        if not self.production_eligible:
            result["warning"] = (
                "PET is an assumption for diagnostic simulation and blocks production approval"
            )
        return result


def provider_from_container(config: Mapping[str, Any], config_path: str | Path) -> PetProvider:
    """Load a referenced PET config, with legacy inline-config compatibility."""
    source_path = Path(config_path).resolve()
    if config.get("pet_config"):
        return PetProvider.from_file(_resolve(str(config["pet_config"]), source_path.parent))
    if isinstance(config.get("pet"), Mapping):
        return PetProvider(config["pet"], source_path.parent)
    raise ValueError("configuration must define pet_config or an inline pet object")


__all__ = ["PetProvider", "provider_from_container"]
