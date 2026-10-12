"""Public data-quality task API used by backend, CLI and frontend adapters."""

from .service import get_data_quality_catalog, run_data_quality_task

__all__ = ["get_data_quality_catalog", "run_data_quality_task"]
