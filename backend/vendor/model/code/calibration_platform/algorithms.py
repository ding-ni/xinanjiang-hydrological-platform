"""CAL-ALGORITHM: bounded, reproducible search algorithms."""

from __future__ import annotations

from itertools import islice, product
import math

import numpy as np


def _candidate(specs, values):
    return {spec["name"]: float(value) for spec, value in zip(specs, values)}


def search(specs, objective, config):
    active = [spec for spec in specs if spec.get("calibratable", True)]
    if not active:
        raise ValueError("no calibratable parameters")
    algorithm = str(config.get("algorithm", "RANDOM_SEARCH")).upper()
    max_evaluations = int(config.get("max_evaluations", 100))
    seed = int(config.get("seed", 42))
    history = []
    best = ({}, float("inf"), None)

    def evaluate(candidate):
        nonlocal best
        try:
            result = objective(candidate)
            loss = float(result["loss"] if isinstance(result, dict) else result)
            error = None
        except (ValueError, ArithmeticError, FloatingPointError) as exc:
            result, loss, error = None, float("inf"), str(exc)
        record = {"evaluation": len(history) + 1, "loss": loss if math.isfinite(loss) else None, "parameters": dict(candidate)}
        if result is not None and isinstance(result, dict):
            record["metrics"] = result
        if error:
            record["error"] = error
        history.append(record)
        if loss < best[1]:
            best = (dict(candidate), loss, result)
        return loss

    if algorithm == "GRID_SEARCH":
        axes = [spec.get("values") or np.linspace(spec["minimum"], spec["maximum"], int(spec.get("levels", 5))).tolist() for spec in active]
        iterator = islice(product(*axes), max_evaluations)
        for values in iterator:
            evaluate(_candidate(active, values))
    elif algorithm == "RANDOM_SEARCH":
        rng = np.random.default_rng(seed)
        for _ in range(max_evaluations):
            values = [rng.choice(spec["values"]) if spec.get("values") else rng.uniform(spec["minimum"], spec["maximum"]) for spec in active]
            evaluate(_candidate(active, values))
    elif algorithm == "DIFFERENTIAL_EVOLUTION":
        if max_evaluations < 4:
            raise ValueError("DIFFERENTIAL_EVOLUTION requires max_evaluations >= 4")
        rng = np.random.default_rng(seed)
        size = min(max(int(config.get("population_size", 12)), 4), max_evaluations)
        lower = np.asarray([spec["minimum"] for spec in active], dtype=float)
        upper = np.asarray([spec["maximum"] for spec in active], dtype=float)
        population = rng.uniform(lower, upper, size=(size, len(active)))
        losses = np.asarray([evaluate(_candidate(active, row)) for row in population])
        generations = int(config.get("generations", 20))
        for _ in range(generations):
            for index in range(size):
                if len(history) >= max_evaluations:
                    break
                choices = np.delete(np.arange(size), index)
                a, b, c = rng.choice(choices, 3, replace=False)
                mutant = np.clip(population[a] + float(config.get("differential_weight", .8)) * (population[b] - population[c]), lower, upper)
                mask = rng.random(len(active)) < float(config.get("crossover_probability", .7))
                mask[rng.integers(0, len(active))] = True
                trial = np.where(mask, mutant, population[index])
                trial_loss = evaluate(_candidate(active, trial))
                if trial_loss < losses[index]:
                    population[index], losses[index] = trial, trial_loss
            if len(history) >= max_evaluations:
                break
    else:
        raise ValueError(f"unsupported calibration algorithm: {algorithm}")
    return {"algorithm": algorithm, "best_parameters": best[0], "best_loss": best[1], "best_metrics": best[2], "evaluations": len(history), "history": history}
