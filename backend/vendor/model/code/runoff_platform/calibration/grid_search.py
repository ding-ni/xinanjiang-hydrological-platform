"""Deterministic Cartesian grid search."""
from __future__ import annotations

from itertools import islice, product

import numpy as np

from .base import CalibrationAlgorithm, CalibrationResult, evaluate_candidate


class GridSearch(CalibrationAlgorithm):
    name = "GRID_SEARCH"

    def optimize(self, space, objective, config):
        specs = space.active
        axes = [spec.values or np.linspace(spec.minimum, spec.maximum, spec.levels).tolist() for spec in specs]
        history = []
        best_parameters, best_loss = {}, float("inf")
        for values in islice(product(*axes), config.max_evaluations):
            candidate = dict(zip((spec.name for spec in specs), values))
            loss = evaluate_candidate(candidate, objective, history)
            if loss < best_loss:
                best_parameters, best_loss = candidate, loss
        return CalibrationResult(self.name, best_parameters, best_loss, len(history), history)
