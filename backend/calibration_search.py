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

        baseline_loss = None
        if staged:
            baseline_loss = objective({})["loss"]
            history.append({"evaluation": completed, "stage": stage_id, "kind": "initial",
                            "loss": baseline_loss, "parameters": initial_parameters})
        offset = completed
        seed = (config.get("seed", 42) + index) % 4294967296
        outcome = search(active_specs, objective, {"algorithm": config["algorithm"],
                         "max_evaluations": budget - int(staged), "population_size": config["populationSize"],
                         "generations": total, "seed": seed})
        selected_loss = outcome["best_loss"]
        retained = staged and math.isfinite(baseline_loss) and baseline_loss <= selected_loss
        if retained:
            selected_loss = baseline_loss
        elif outcome["best_parameters"] and math.isfinite(selected_loss):
            best = candidate_params(copy.deepcopy(incoming), outcome["best_parameters"])
        else:
            raise ValueError(f"{label}未得到有效目标函数，请检查率定资料和参数范围")
        history.extend({**row, "evaluation": offset + row["evaluation"], "stage": stage_id}
                       for row in outcome["history"])
        stages.append({"id": stage_id, "label": label, "budget": budget,
                       "evaluations": outcome["evaluations"] + int(staged), "seed": seed,
                       "parameterNames": [s["name"] for s in active_specs],
                       "initialParameters": initial_parameters, "bestParameters": resolved(best),
                       "initialLoss": baseline_loss, "bestLoss": selected_loss, "retainedInitial": retained})
    return best, {"algorithm": config["algorithm"], "strategy": "staged" if staged else "joint",
                  "best_parameters": resolved(best), "best_loss": selected_loss,
                  "evaluations": completed, "history": history, "stages": stages}
