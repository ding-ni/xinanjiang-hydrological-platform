"""Calibration facade used by API, CLI and tests."""
from __future__ import annotations

from .base import CalibrationConfig, ParameterSpace
from .differential_evolution import DifferentialEvolution
from .grid_search import GridSearch
from .random_search import RandomSearch


ALGORITHMS = {
    "GRID_SEARCH": GridSearch,
    "RANDOM_SEARCH": RandomSearch,
    "DIFFERENTIAL_EVOLUTION": DifferentialEvolution,
}


class CalibrationEngine:
    """[CAL-ENGINE-01] Select an algorithm without coupling it to model code."""

    def run(self, parameter_space, objective, config=None):
        space = parameter_space if isinstance(parameter_space, ParameterSpace) else ParameterSpace.from_config(parameter_space)
        cfg = config if isinstance(config, CalibrationConfig) else CalibrationConfig.from_dict(config or {})
        name = cfg.algorithm.upper()
        if name not in ALGORITHMS:
            raise ValueError(f"unsupported calibration algorithm: {cfg.algorithm}")
        return ALGORITHMS[name]().optimize(space, objective, cfg)
