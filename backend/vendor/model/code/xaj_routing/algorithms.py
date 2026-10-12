"""Small hydrologic routing algorithms used by the routing engine."""

from __future__ import annotations

import math
from dataclasses import dataclass


def runoff_mm_to_cms(runoff_mm: float, area_km2: float, time_step_seconds: float) -> float:
    """Convert runoff depth over an area to discharge."""
    if area_km2 < 0:
        raise ValueError("area_km2 must be non-negative")
    if time_step_seconds <= 0:
        raise ValueError("time_step_seconds must be positive")
    return runoff_mm * area_km2 * 1000.0 / time_step_seconds


def linear_reservoir_step(
    inflow_cms: float,
    previous_outflow_cms: float,
    k_seconds: float,
    time_step_seconds: float,
) -> float:
    """Route one step with an exact linear reservoir solution."""
    if k_seconds <= 0:
        raise ValueError("linear reservoir k_seconds must be positive")
    if time_step_seconds <= 0:
        raise ValueError("time_step_seconds must be positive")
    recession = math.exp(-time_step_seconds / k_seconds)
    return previous_outflow_cms * recession + inflow_cms * (1.0 - recession)


@dataclass(frozen=True)
class MuskingumCoefficients:
    c0: float
    c1: float
    c2: float


def muskingum_coefficients(
    k_seconds: float,
    x: float,
    time_step_seconds: float,
) -> MuskingumCoefficients:
    """Return Muskingum routing coefficients for one time step."""
    if k_seconds <= 0:
        raise ValueError("Muskingum k_seconds must be positive")
    if not 0.0 <= x <= 0.5:
        raise ValueError("Muskingum x must be between 0 and 0.5")
    if time_step_seconds <= 0:
        raise ValueError("time_step_seconds must be positive")

    denominator = k_seconds - k_seconds * x + 0.5 * time_step_seconds
    if denominator <= 0:
        raise ValueError("invalid Muskingum denominator")

    c0 = (-k_seconds * x + 0.5 * time_step_seconds) / denominator
    c1 = (k_seconds * x + 0.5 * time_step_seconds) / denominator
    c2 = (k_seconds - k_seconds * x - 0.5 * time_step_seconds) / denominator
    return MuskingumCoefficients(c0=c0, c1=c1, c2=c2)


def muskingum_step(
    current_inflow_cms: float,
    previous_inflow_cms: float,
    previous_outflow_cms: float,
    k_seconds: float,
    x: float,
    time_step_seconds: float,
) -> float:
    """Route one river reach by Muskingum."""
    coeffs = muskingum_coefficients(k_seconds, x, time_step_seconds)
    return (
        coeffs.c0 * current_inflow_cms
        + coeffs.c1 * previous_inflow_cms
        + coeffs.c2 * previous_outflow_cms
    )
