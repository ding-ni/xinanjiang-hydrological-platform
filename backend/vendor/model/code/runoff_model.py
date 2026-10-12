"""Backward-compatible facade for the modular runoff platform.

New code should import from ``runoff_platform``. Existing scripts that import
``runoff_model`` continue to work through this file.
"""

from runoff_platform.core.results import validate_result
from runoff_platform.core.types import (
    GlacierParams, GlacierState, GridInfo, LandParams, LandState,
    NUMERICAL, PHYSICAL, PETParams, PhaseParams, RunContext,
    RunoffConversionParams, SnowParams, daily_recession_to_hourly,
)
from runoff_platform.models.common import partition_precip, pet_hourly, potential_melt
from runoff_platform.models.conversion import saturation_infiltration_conversion
from runoff_platform.models.glacier import simulate_glacier
from runoff_platform.models.snow import snow_step
from runoff_platform.models.xaj import (
    fill_tension_storage, simulate_land, simulate_xaj, xaj_evaporation,
    xaj_runoff_generation, xaj_source_separation,
)

__all__ = [name for name in globals() if not name.startswith("_")]
