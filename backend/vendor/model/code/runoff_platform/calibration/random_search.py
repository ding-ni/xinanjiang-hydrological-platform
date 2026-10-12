"""Reproducible uniform random search."""
from __future__ import annotations

import numpy as np

from .base import CalibrationAlgorithm, CalibrationResult, evaluate_candidate


class RandomSearch(CalibrationAlgorithm):
    name = "RANDOM_SEARCH"

    def optimize(self, space, objective, config):
        rng = np.random.default_rng(config.seed)
        specs = space.active
        history = []
        best_parameters, best_loss = {}, float("inf")
        for _ in range(config.max_evaluations):
            candidate = {
                spec.name: float(rng.choice(spec.values)) if spec.values else float(rng.uniform(spec.minimum, spec.maximum))
                for spec in specs
            }
            loss = evaluate_candidate(candidate, objective, history)
            if loss < best_loss:
                best_parameters, best_loss = candidate, loss
        return CalibrationResult(self.name, best_parameters, best_loss, len(history), history)
