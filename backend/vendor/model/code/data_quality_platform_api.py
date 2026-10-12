"""Compatibility import for backend projects that use a flat module path."""

from data_quality_platform import get_data_quality_catalog, run_data_quality_task

__all__ = ["get_data_quality_catalog", "run_data_quality_task"]
