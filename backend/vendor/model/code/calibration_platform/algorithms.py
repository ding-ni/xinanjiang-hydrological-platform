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
    initial_candidates = []
    initial_audit = []
    for item in config.get('initial_candidates', []):
        submitted = item.get('parameters', item)
        values, reason = [], None
        for spec in active:
            value = submitted.get(spec['name'])
            if value is None or not math.isfinite(float(value)) or not spec['minimum'] <= float(value) <= spec['maximum']:
                reason = f"missing or out of bounds: {spec['name']}"
                break
            values.append(float(value))
        if reason is None and values not in initial_candidates:
            initial_candidates.append(values)
        initial_audit.append({'source': item.get('source', 'provided'), 'accepted': reason is None, 'reason': reason})

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

    # A caller may reevaluate the current frozen solution first. Count that
    # real evaluation inside the same budget and seed the population with it.
    # Historical callers without this field keep their original contract.
    incumbent = config.get("initial_evaluation")
    initial_vector = None
    if incumbent is not None:
        loss = float(incumbent["loss"])
        coordinates = incumbent.get("coordinates", {})
        if not math.isfinite(loss):
            raise ValueError("current frozen candidate has no finite objective")
        history.append({"evaluation": 1, "kind": "initial", "loss": loss,
                        "parameters": dict(incumbent.get("parameters", {}))})
        best = ({}, loss, {"loss": loss})
        if all(coordinates.get(spec["name"]) is not None for spec in active):
            initial_vector = np.asarray([coordinates[spec["name"]] for spec in active], dtype=float)
            lower_seed = np.asarray([spec["minimum"] for spec in active], dtype=float)
            upper_seed = np.asarray([spec["maximum"] for spec in active], dtype=float)
            if not np.isfinite(initial_vector).all() or np.any(initial_vector < lower_seed) or np.any(initial_vector > upper_seed):
                initial_vector = None

    if algorithm == "GRID_SEARCH":
        axes = [spec.get("values") or np.linspace(spec["minimum"], spec["maximum"], int(spec.get("levels", 5))).tolist() for spec in active]
        iterator = islice(product(*axes), max_evaluations - len(history))
        for values in iterator:
            evaluate(_candidate(active, values))
    elif algorithm == "RANDOM_SEARCH":
        rng = np.random.default_rng(seed)
        for index in range(max_evaluations - len(history)):
            values = initial_candidates[index] if index < len(initial_candidates) else [rng.choice(spec["values"]) if spec.get("values") else rng.uniform(spec["minimum"], spec["maximum"]) for spec in active]
            evaluate(_candidate(active, values))
    elif algorithm == "DIFFERENTIAL_EVOLUTION":
        if max_evaluations < 4:
            raise ValueError("DIFFERENTIAL_EVOLUTION requires max_evaluations >= 4")
        rng = np.random.default_rng(seed)
        size = min(max(int(config.get("population_size", 12)), 4), max_evaluations)
        lower = np.asarray([spec["minimum"] for spec in active], dtype=float)
        upper = np.asarray([spec["maximum"] for spec in active], dtype=float)
        population = rng.uniform(lower, upper, size=(size, len(active)))
        for index, values in enumerate(initial_candidates[:size]):
            population[index] = values
        if initial_vector is not None:
            population[0] = initial_vector
            losses = np.asarray([float(incumbent['loss'])] +
                                [evaluate(_candidate(active, row)) for row in population[1:]])
        else:
            # A heterogeneous reach-X baseline has no single X coordinate. Its
            # original evaluation still competes, without inventing a seed.
            size = min(size, max_evaluations - len(history))
            population = population[:size]
            losses = np.asarray([evaluate(_candidate(active, row)) for row in population])
        generations = int(config.get("generations", 20))
        if 'initial_candidates' in config:
            generations = max(generations, math.ceil(max(0, max_evaluations - size) / size))
        for _ in range(generations if size >= 4 else 0):
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
    result = {"algorithm": algorithm, "best_parameters": best[0], "best_loss": best[1], "best_metrics": best[2],
            "evaluations": len(history), "history": history,
            "termination_reason": "BUDGET_EXHAUSTED" if len(history) >= max_evaluations else "SEARCH_SPACE_EXHAUSTED"}
    if 'initial_candidates' in config:
        result['initial_candidates'] = initial_audit
    return result
