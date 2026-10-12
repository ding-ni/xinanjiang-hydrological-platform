"""[CAL-COMPARE-01] Compare completed formal calibration runs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


GROUP_ORDER = ["CALIBRATION", "VALIDATION", "INDEPENDENT_TEST"]
METRIC_COLUMNS = [
    "qualified_rate_percent",
    "mean_NSE",
    "mean_KGE",
    "mean_abs_peak_error_percent",
    "mean_abs_volume_error_percent",
    "median_peak_time_error_hours",
]


def _read_run(run_dir: Path) -> dict[str, Any]:
    report_path = run_dir / "formal_multi_event_report.json"
    event_path = run_dir / "event_metrics.csv"
    if not report_path.is_file() or not event_path.is_file():
        raise FileNotFoundError(f"missing formal calibration outputs in {run_dir}")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    events = pd.read_csv(event_path, encoding="utf-8-sig")
    required = {"event_id", "group", "NSE", "KGE", "peak_error_percent", "volume_error_percent"}
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"event metrics missing columns in {run_dir}: {sorted(missing)}")
    return {"report": report, "events": events}


def _parameter_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    bounds = report["parameter_space"]["bounds"]
    values = report["common_parameter_set"]
    rows = []
    for name, value in values.items():
        lower, upper = (float(item) for item in bounds[name])
        value = float(value)
        tolerance = max(1.0e-9, (upper - lower) * 1.0e-6)
        rows.append({
            "parameter": name,
            "value": value,
            "minimum": lower,
            "maximum": upper,
            "at_lower_bound": abs(value - lower) <= tolerance,
            "at_upper_bound": abs(value - upper) <= tolerance,
        })
    return rows


def compare_formal_calibrations(
    run_dirs: dict[str, str | Path],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Compare runs with identical events and write platform-ready artifacts."""
    if len(run_dirs) < 2:
        raise ValueError("at least two calibration runs are required")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    loaded = {name: _read_run(Path(path).resolve()) for name, path in run_dirs.items()}
    strategies = list(loaded)
    base_name, comparison_name = strategies[0], strategies[1]

    reference_name = next(iter(loaded))
    reference_keys = set(
        zip(loaded[reference_name]["events"].event_id, loaded[reference_name]["events"].group)
    )
    for name, item in loaded.items():
        keys = set(zip(item["events"].event_id, item["events"].group))
        if keys != reference_keys:
            raise ValueError(f"event split differs between {reference_name} and {name}")

    group_rows = []
    for strategy, item in loaded.items():
        for group, values in item["report"]["groups"].items():
            group_rows.append({"strategy": strategy, "group": group, **values})
    group_frame = pd.DataFrame(group_rows)
    group_frame["group"] = pd.Categorical(group_frame["group"], GROUP_ORDER, ordered=True)
    group_frame = group_frame.sort_values(["group", "strategy"])
    group_frame.to_csv(output / "group_metrics_comparison.csv", index=False, encoding="utf-8-sig")

    group_delta_rows = []
    for group in GROUP_ORDER:
        indexed = group_frame[group_frame.group == group].set_index("strategy")
        row = {"group": group, "comparison": f"{comparison_name}_minus_{base_name}"}
        for column in METRIC_COLUMNS:
            row[f"delta_{column}"] = float(
                indexed.loc[comparison_name, column] - indexed.loc[base_name, column]
            )
        group_delta_rows.append(row)
    group_delta_frame = pd.DataFrame(group_delta_rows)
    group_delta_frame.to_csv(output / "group_metric_deltas.csv", index=False, encoding="utf-8-sig")

    event_frames = []
    for strategy, item in loaded.items():
        frame = item["events"].copy()
        frame.insert(0, "strategy", strategy)
        event_frames.append(frame)
    all_events = pd.concat(event_frames, ignore_index=True)
    all_events.to_csv(output / "event_metrics_by_strategy.csv", index=False, encoding="utf-8-sig")

    base = loaded[base_name]["events"].set_index(["event_id", "group"])
    comparison = loaded[comparison_name]["events"].set_index(["event_id", "group"])
    delta = pd.DataFrame(index=base.index)
    for column in ("NSE", "KGE", "peak_error_percent", "volume_error_percent", "peak_time_error_hours"):
        delta[f"{comparison_name}_minus_{base_name}_{column}"] = comparison[column] - base[column]
    delta.reset_index().to_csv(output / "event_metric_deltas.csv", index=False, encoding="utf-8-sig")

    parameter_sets = {
        name: item["report"]["common_parameter_set"] for name, item in loaded.items()
    }
    parameters_identical = all(
        parameter_sets[name] == parameter_sets[reference_name] for name in strategies[1:]
    )
    parameter_frames = []
    for strategy, item in loaded.items():
        frame = pd.DataFrame(_parameter_rows(item["report"]))
        frame.insert(0, "strategy", strategy)
        parameter_frames.append(frame)
    parameter_frame = pd.concat(parameter_frames, ignore_index=True)
    parameter_frame.to_csv(output / "parameter_boundary_diagnostics.csv", index=False, encoding="utf-8-sig")
    boundary_parameters = parameter_frame.loc[
        (parameter_frame.strategy == reference_name)
        & (parameter_frame.at_lower_bound | parameter_frame.at_upper_bound),
        "parameter",
    ].tolist()
    boundary_parameters_by_strategy = {
        strategy: parameter_frame.loc[
            (parameter_frame.strategy == strategy)
            & (parameter_frame.at_lower_bound | parameter_frame.at_upper_bound),
            "parameter",
        ].tolist()
        for strategy in strategies
    }

    external_groups = group_frame[group_frame.group.isin(["VALIDATION", "INDEPENDENT_TEST"])].copy()
    external_mean_nse = external_groups.groupby("strategy", observed=False).mean_NSE.mean().to_dict()
    higher_external_nse_strategy = max(external_mean_nse, key=external_mean_nse.get)
    report = {
        "status": "CALIBRATION_STRATEGY_COMPARISON",
        "module_id": "CAL-COMPARE-01",
        "strategies": strategies,
        "event_split_identical": True,
        "event_count": len(reference_keys),
        "parameters_identical": parameters_identical,
        "boundary_parameters": boundary_parameters,
        "boundary_parameters_by_strategy": boundary_parameters_by_strategy,
        "external_mean_NSE": external_mean_nse,
        "higher_external_mean_NSE_strategy": higher_external_nse_strategy,
        "group_metric_deltas": group_delta_rows,
        "warnings": [
            f"{strategy} touches parameter-space boundaries: {', '.join(parameters)}"
            for strategy, parameters in boundary_parameters_by_strategy.items()
            if parameters
        ],
    }

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch

        metrics = [
            ("mean_NSE", "Mean NSE"),
            ("mean_KGE", "Mean KGE"),
            ("mean_abs_peak_error_percent", "Mean absolute peak error (%)"),
            ("mean_abs_volume_error_percent", "Mean absolute volume error (%)"),
        ]
        colors = ["#2878B5", "#E07A2D"]
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
        for axis, (column, title) in zip(axes.flat, metrics):
            pivot = group_frame.pivot(index="group", columns="strategy", values=column).reindex(GROUP_ORDER)
            pivot.plot.bar(ax=axis, rot=0, legend=False, color=colors)
            axis.set_title(title)
            axis.set_xlabel("")
        fig.legend(
            handles=[Patch(color=color, label=strategy) for color, strategy in zip(colors, strategies)],
            loc="outside upper center",
            ncol=len(strategies),
            frameon=False,
        )
        fig.savefig(output / "state_strategy_comparison.png", dpi=180)
        plt.close(fig)
    except Exception as exc:
        report["plot_warning"] = repr(exc)

    (output / "comparison_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report
