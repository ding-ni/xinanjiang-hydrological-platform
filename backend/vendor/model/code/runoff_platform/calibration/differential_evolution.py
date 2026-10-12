"""Dependency-free differential evolution for bounded continuous parameters."""
from __future__ import annotations

import numpy as np

from .base import CalibrationAlgorithm, CalibrationResult, evaluate_candidate


class DifferentialEvolution(CalibrationAlgorithm):
    name = "DIFFERENTIAL_EVOLUTION"

    def optimize(self, space, objective, config):
        specs = space.active
        if not specs:
            raise ValueError("parameter space has no active parameters")
        rng = np.random.default_rng(config.seed)
        size = max(config.population_size, 4)
        lower = np.array([spec.minimum for spec in specs], dtype=float)
        upper = np.array([spec.maximum for spec in specs], dtype=float)
        population = rng.uniform(lower, upper, size=(size, len(specs)))
        history = []

        def as_dict(vector):
            return {spec.name: float(value) for spec, value in zip(specs, vector)}

        losses = np.array([evaluate_candidate(as_dict(vector), objective, history) for vector in population])
        max_evaluations = max(config.max_evaluations, size)
        for _ in range(config.generations):
            for index in range(size):
                if len(history) >= max_evaluations:
                    break
                choices = np.delete(np.arange(size), index)
                a, b, c = rng.choice(choices, 3, replace=False)
                mutant = np.clip(population[a] + config.differential_weight * (population[b] - population[c]), lower, upper)
                mask = rng.random(len(specs)) < config.crossover_probability
                mask[rng.integers(0, len(specs))] = True
                trial = np.where(mask, mutant, population[index])
                trial_loss = evaluate_candidate(as_dict(trial), objective, history)
                if trial_loss < losses[index]:
                    population[index], losses[index] = trial, trial_loss
            if len(history) >= max_evaluations:
                break
        best = int(np.argmin(losses))
        return CalibrationResult(self.name, as_dict(population[best]), float(losses[best]), len(history), history)
