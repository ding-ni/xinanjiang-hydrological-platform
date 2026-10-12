"""Budgeted orchestration around the delivered search and runoff kernels."""
from __future__ import annotations

import copy
import math


def run_search(specs, config, initial, evaluate, candidate_params, resolved, search, progress):
    """Only evaluate() sees data. Validation periods must never be passed to it.

    Each staged search retains the incoming solution if its trials are worse.
    The incumbent evaluation consumes one unit of that stage's budget; it is
    not a warm-start population for the unchanged vendor optimizer.
    """
    total = config["maxEvaluations"]
    original_population = config.get('initializationPolicy') == 'SOURCE_INITIAL_CANDIDATES'
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
    valid = sum(row.get('loss') is not None and math.isfinite(row['loss']) for row in history)
    return best, {"algorithm": config["algorithm"], "strategy": "staged" if staged else "joint",
                  "best_parameters": resolved(best), "best_loss": selected_loss,
                  "evaluations": completed, "validEvaluations": valid, "invalidEvaluations": completed - valid,
                  "terminationReason": 'BUDGET_EXHAUSTED' if completed >= total else 'SEARCH_SPACE_EXHAUSTED',
                  "baselineReevaluated": True, "history": history, "stages": stages}
