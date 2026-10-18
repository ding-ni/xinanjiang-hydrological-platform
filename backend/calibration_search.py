"""Budgeted orchestration around the delivered search and runoff kernels."""
from __future__ import annotations

import copy
import math


def sce_population_size(dimension, complexes=5):
    if type(complexes) is not int or not 1 <= complexes <= 20:
        raise ValueError('SCE-UA复合体数须为1至20的整数')
    return complexes * (2 * dimension + 1)


def sce_search(specs, objective, config):
    """Shuffled complex evolution with competitive simplex evolution.

    Duan, Sorooshian & Gupta (1992): rank-interleaved complexes, weighted
    simplex sampling, reflection/contraction/random replacement, then shuffle.
    All objective attempts (including infeasible points) consume the budget.
    """
    import numpy as np
    active = [row for row in specs if row.get('calibratable', True)]
    n = len(active)
    if not n or any(row.get('values') for row in active):
        raise ValueError('SCE-UA需要至少一个连续活动参数')
    complexes = config.get('complexes', 5)
    size = sce_population_size(n, complexes)
    budget = config['max_evaluations']
    if budget < size + 1:
        raise ValueError(f'SCE-UA当前{n}维、{complexes}个复合体需要至少{size + 1}次评价（含初值）')
    rng = np.random.default_rng(config.get('seed', 42))
    lower = np.array([row['minimum'] for row in active], dtype=float)
    upper = np.array([row['maximum'] for row in active], dtype=float)
    span = upper - lower
    if np.any(span <= 0):
        raise ValueError('SCE-UA活动参数范围必须有正宽度')
    history, best_values, best_loss = [], {}, float('inf')
    incumbent = config.get('initial_evaluation')
    if incumbent:
        best_loss = float(incumbent['loss'])
        history.append({'evaluation': 1, 'kind': 'initial', 'loss': best_loss, 'parameters': dict(incumbent.get('parameters', {}))})

    def evaluate(point):
        nonlocal best_values, best_loss
        values = {row['name']: float(value) for row, value in zip(active, lower + point * span)}
        error = None
        try:
            result = objective(values)
            loss = float(result['loss'] if isinstance(result, dict) else result)
        except (ValueError, ArithmeticError, FloatingPointError) as failure:
            loss, error = float('inf'), str(failure)
        if not math.isfinite(loss):
            loss = float('inf')
        history.append({'evaluation': len(history) + 1, 'loss': loss if math.isfinite(loss) else None,
                        'parameters': values, **({'error': error} if error else {})})
        if loss < best_loss:
            best_values, best_loss = values, loss
        return loss

    population = rng.random((size, n))
    losses = np.full(size, np.inf)
    begin = 0
    if incumbent:
        coordinates = incumbent.get('coordinates', {})
        vector = np.array([coordinates.get(row['name'], np.nan) for row in active])
        if np.isfinite(vector).all() and np.all(vector >= lower) and np.all(vector <= upper):
            population[0] = (vector - lower) / span
            losses[0] = incumbent['loss']
            begin = 1
    for i in range(begin, size):
        losses[i] = evaluate(population[i])
    m, q = 2 * n + 1, n + 1
    probabilities = 2 * (m - np.arange(m)) / (m * (m + 1))
    shuffles, best_per_shuffle = 0, []
    reason = 'BUDGET_EXHAUSTED'
    while len(history) < budget:
        order = np.argsort(losses, kind='stable')
        population, losses = population[order], losses[order]
        for complex_index in range(complexes):
            indices = np.arange(complex_index, size, complexes)
            points, scores = population[indices].copy(), losses[indices].copy()
            for _ in range(m):
                if len(history) >= budget:
                    break
                order = np.argsort(scores, kind='stable')
                points, scores = points[order], scores[order]
                simplex = np.sort(rng.choice(m, size=q, replace=False, p=probabilities))
                worst = simplex[-1]
                centroid = points[simplex[:-1]].mean(axis=0)
                point = 2 * centroid - points[worst]
                if np.any(point < 0) or np.any(point > 1):
                    point = rng.random(n)
                loss = evaluate(point)
                if loss >= scores[worst] and len(history) < budget:
                    point = .5 * (centroid + points[worst])
                    loss = evaluate(point)
                    if loss >= scores[worst] and len(history) < budget:
                        point = rng.random(n)
                        loss = evaluate(point)
                points[worst], scores[worst] = point, loss
            population[indices], losses[indices] = points, scores
        shuffles += 1
        best_per_shuffle.append(best_loss)
        geometric_range = float(np.exp(np.log(np.maximum(np.ptp(population, axis=0), 1e-300)).mean()))
        stalled = len(best_per_shuffle) >= 10 and math.isfinite(best_loss) and abs(best_per_shuffle[-10] - best_loss) <= .001 * max(abs(best_loss), 1e-12)
        if len(history) < budget and (geometric_range <= 1e-5 or stalled):
            reason = 'CONVERGED'
            break
    if not math.isfinite(best_loss):
        raise ValueError('SCE-UA未找到有效候选，请检查资料、约束和参数范围')
    return {'algorithm': 'SCE_UA', 'best_parameters': best_values, 'best_loss': best_loss,
            'history': history, 'evaluations': len(history), 'terminationReason': reason,
            'consumedConfiguration': {'complexes': complexes, 'pointsPerComplex': m, 'simplexSize': q,
                                      'evolutionsPerComplex': m, 'populationSize': size, 'dimension': n,
                                      'seed': config.get('seed', 42), 'maxEvaluations': budget,
                                      'parameterTolerance': 1e-5, 'stagnationShuffles': 10, 'relativeImprovement': .001},
            'shuffles': shuffles}


def run_search(specs, config, initial, evaluate, candidate_params, resolved, search, progress):
    """Only evaluate() sees data. Validation periods must never be passed to it.

    Each staged search retains the incoming solution if its trials are worse.
    The incumbent evaluation consumes one unit of that stage's budget; it is
    not a warm-start population for the unchanged vendor optimizer.
    """
    total = config["maxEvaluations"]
    sce = config['algorithm'] == 'SCE_UA'
    if sce:
        search = sce_search
    original_population = config.get('initializationPolicy') == 'SOURCE_INITIAL_CANDIDATES'
    if sce and original_population:
        raise ValueError('原方案起始种群只用于原算法复算，SCE-UA请另建新计算')
    if original_population:
        if config.get('strategy', 'joint') != 'joint' or not isinstance(config.get('effectiveInitialCandidates'), list):
            raise ValueError('原方案种群方式须使用已冻结的原起始参数及额外候选')
        completed = 0
        def original_objective(values):
            nonlocal completed
            try:
                return {'loss': float(evaluate(candidate_params(copy.deepcopy(initial), values)))}
            finally:
                completed += 1
                progress(10 + 70 * completed / total, f'原方案搜索 · 参数评价 {completed}/{total}')
        size = min(max(config['populationSize'], 4), total)
        generations = max(config.get('generations', 0), math.ceil(max(0, total - size) / size))
        consumed = {'algorithm': config['algorithm'], 'max_evaluations': total,
                    'population_size': size, 'generations': generations,
                    'seed': config.get('seed', 42), 'differential_weight': config.get('differentialWeight', .8),
                    'crossover_probability': config.get('crossoverProbability', .7),
                    'initial_candidates': copy.deepcopy(config['effectiveInitialCandidates'])}
        outcome = search(specs, original_objective, consumed)
        if not outcome['best_parameters'] or not math.isfinite(outcome['best_loss']):
            raise ValueError('原方案本次预算未找到符合产流容量与河道汇流约束的候选，请增加试运行预算')
        best = candidate_params(copy.deepcopy(initial), outcome['best_parameters'])
        valid = sum(row.get('loss') is not None and math.isfinite(row['loss']) for row in outcome['history'])
        return best, {**outcome, 'strategy': 'joint', 'evaluations': completed,
                      'terminationReason': 'BUDGET_EXHAUSTED' if completed >= total else 'SEARCH_SPACE_EXHAUSTED',
                      'validEvaluations': valid, 'invalidEvaluations': completed - valid,
                      'baselineReevaluated': False, 'initializationPolicy': 'SOURCE_INITIAL_CANDIDATES',
                      'consumedConfiguration': consumed,
                      'stages': [{'id': 'joint', 'label': '原方案联合搜索', 'budget': total, 'evaluations': completed,
                                  'seed': consumed['seed'], 'parameterNames': [row['name'] for row in specs],
                                  'initialParameters': resolved(initial), 'bestParameters': resolved(best),
                                  'bestLoss': outcome['best_loss'], 'retainedInitial': False}]}
    if config['algorithm'] == 'FIXED_PARAMETERS':
        best = copy.deepcopy(initial)
        loss = float(evaluate(best))
        if not math.isfinite(loss):
            raise ValueError('当前参数未得到有效评价值')
        progress(80, '人工试调 · 当前参数计算完成')
        return best, {'algorithm': 'FIXED_PARAMETERS', 'strategy': 'manual',
                      'terminationReason': 'FIXED_PARAMETERS_EVALUATED',
                      'best_parameters': resolved(best), 'best_loss': loss, 'evaluations': 1,
                      'history': [{'evaluation': 1, 'kind': 'manual', 'loss': loss,
                                   'parameters': resolved(best)}], 'stages': []}
    staged = config.get("strategy", "joint") == "staged"
    partitions = [("joint", "联合搜索", specs, total)]
    if staged:
        storage = {"WM", "WUM", "WLM", "B", "IM"}
        partitions = [
            ("storage", "蓄水产流", [s for s in specs if s["name"].split(".")[-1] in storage], total // 2),
            ("sources", "水源划分", [s for s in specs if s["name"].split(".")[-1] not in storage], total - total // 2),
        ]
    best, stages, history = copy.deepcopy(initial), [], []
    completed = 0
    for index, (stage_id, label, active_specs, budget) in enumerate(partitions):
        incoming = copy.deepcopy(best)
        initial_parameters = resolved(incoming)

        def objective(candidate):
            nonlocal completed
            try:
                params = candidate_params(copy.deepcopy(incoming), candidate)
                return {"loss": float(evaluate(params))}
            finally:
                completed += 1
                progress(10 + 70 * completed / total, f"{label} · 参数评价 {completed}/{total}")

        baseline_loss = objective({})["loss"]
        if not math.isfinite(baseline_loss):
            raise ValueError(f'{label}当前冻结参数无有效目标值')
        offset = completed - 1
        seed = (config.get("seed", 42) + index) % 4294967296
        outcome = search(active_specs, objective, {"algorithm": config["algorithm"],
                         "max_evaluations": budget, "population_size": config["populationSize"],
                         "generations": config.get('generations', total), "seed": seed,
                         'complexes': config.get('complexes', 5),
                         'differential_weight': config.get('differentialWeight', .8),
                         'crossover_probability': config.get('crossoverProbability', .7),
                         "initial_evaluation": {"loss": baseline_loss,
                            "parameters": {s['name']: initial_parameters.get(s['name']) for s in active_specs},
                            "coordinates": initial_parameters}})
        selected_loss = outcome["best_loss"]
        retained = baseline_loss <= selected_loss
        if retained:
            selected_loss = baseline_loss
        elif outcome["best_parameters"] and math.isfinite(selected_loss):
            best = candidate_params(copy.deepcopy(incoming), outcome["best_parameters"])
        else:
            raise ValueError(f"{label}未得到有效目标函数，请检查率定资料和参数范围")
        history.extend({**row, "evaluation": offset + row["evaluation"], "stage": stage_id}
                       for row in outcome["history"])
        stages.append({"id": stage_id, "label": label, "budget": budget,
                       "evaluations": outcome["evaluations"], "seed": seed,
                       "parameterNames": [s["name"] for s in active_specs],
                       "initialParameters": initial_parameters, "bestParameters": resolved(best),
                       "initialLoss": baseline_loss, "bestLoss": selected_loss, "retainedInitial": retained})
        if sce:
            stages[-1]['consumedConfiguration'] = outcome['consumedConfiguration']
    valid = sum(row.get('loss') is not None and math.isfinite(row['loss']) for row in history)
    return best, {"algorithm": config["algorithm"], "strategy": "staged" if staged else "joint",
                  "best_parameters": resolved(best), "best_loss": selected_loss,
                  "evaluations": completed, "validEvaluations": valid, "invalidEvaluations": completed - valid,
                  "terminationReason": 'BUDGET_EXHAUSTED' if completed >= total else outcome.get('terminationReason', 'SEARCH_SPACE_EXHAUSTED'),
                  "baselineReevaluated": True, "history": history, "stages": stages}
