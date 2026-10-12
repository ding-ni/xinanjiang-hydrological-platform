"""Backward-compatible API facade for frontend/backend integration."""

from runoff_platform.api.schemas import get_parameter_catalog, get_parameter_schema
from runoff_platform.api.service import (
    COMMON_OUTPUT_FIELDS, GLACIER_DIAGNOSTIC_FIELDS, LAND_DIAGNOSTIC_FIELDS,
    RequestValidationError, run_calibration_request, run_platform_request,
)

__all__ = [name for name in globals() if not name.startswith("_")]
