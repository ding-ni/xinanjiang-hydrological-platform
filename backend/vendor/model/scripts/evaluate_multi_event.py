"""Evaluate many Nan'an-Xufan flood events with one fixed model parameter set.

This is a baseline evaluation utility, not an automatic calibration routine. It
keeps event selection, warm-up, parameter provenance and qualification rules in
the output so the result can be reproduced and later replaced by a calibrated
multi-event experiment.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from integration.runoff_adapter import aggregate_runoff_to_routing_units  # noqa: E402
from calibration_platform.qualification import (  # noqa: E402
    qualification_definition,
    qualify_event,
)
from runoff_platform.config_loader import load_parameter_set  # noqa: E402
from runoff_platform.core.types import GridInfo, LandState, RunContext  # noqa: E402
from runoff_platform.models.xaj import simulate_land  # noqa: E402
from xaj_routing.engine import RoutingEngine  # noqa: E402
from xaj_routing.io import (  # noqa: E402
    read_control_points_json,
    read_parameters_json,
    read_runoff_csv,
    read_topology_json,
)


DEFAULT_DATA_ROOT = Path(r"E:\产流模块\南安溪-徐畈数据资料整合")
DEFAULT_WARMUP_HOURS = 168
DEFAULT_TEMPERATURE_C = 15.0
DEFAULT_PET_MM_H = 0.08


def _find_package_root(data_root: Path) -> Path:
    candidates = list(data_root.glob("**/02_分布式新安江模型输入资料"))
    if not candidates:
        raise FileNotFoundError("cannot find 02_分布式新安江模型输入资料")
    return candidates[0]


def _load_events(data_root: Path, years: set[int]) -> tuple[list[dict], dict]:
    package_root = _find_package_root(data_root)
    index_path = package_root / "洪水场次" / "南安溪洪水场次资料索引.json"
    payload = json.loads(index_path.read_text(encoding="utf-8-sig"))
    all_events = payload["packages"]
    selected = []
    exclusions = defaultdict(int)
    for event in all_events:
        start = pd.Timestamp(event["start"])
        end = pd.Timestamp(event["end"])
        observed = event.get("observedInputs", {})
        reason = None
        if start.year not in years or end.year != start.year:
            reason = "year_not_supported_or_cross_year"
        elif observed.get("rainCompleteness", 0.0) < 0.999:
            reason = "rain_incomplete"
        elif observed.get("riverCompleteness", 0.0) < 0.999:
            reason = "river_incomplete"
        elif end <= start:
            reason = "invalid_window"
        if reason:
            exclusions[reason] += 1
            continue
        selected.append({**event, "_start": start, "_end": end})
    selected.sort(key=lambda item: item["_start"])
    return selected, {
        "catalog_count": len(all_events),
        "selected_count": len(selected),
        "exclusions": dict(exclusions),
        "selected_years": sorted(years),
    }


def _load_observed(data_root: Path) -> pd.Series:
    package_root = _find_package_root(data_root)
    candidates = list(package_root.glob("**/徐畈出口逐小时水位流量.xlsx"))
    if not candidates:
        raise FileNotFoundError("cannot find hourly Xufan flow workbook")
    frame = pd.read_excel(candidates[0], sheet_name="逐小时水位流量", usecols=[0, 2])
    frame.columns = ["time", "flow"]
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["flow"] = pd.to_numeric(frame["flow"], errors="coerce")
    frame = frame.dropna().drop_duplicates("time").set_index("time").sort_index()
    return frame["flow"]


def _load_year_precip(precip_dir: Path, year: int) -> tuple[pd.DatetimeIndex, np.ndarray]:
    path = precip_dir / f"nananxi_precip_hourly_1km_{year}.nc"
    if not path.exists():
        raise FileNotFoundError(path)
    with xr.open_dataset(path, engine="h5netcdf") as dataset:
        times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
        values = np.asarray(dataset["precipitation"].values, dtype=float)
    return times, values


def _metrics(observed: pd.Series, simulated: pd.Series) -> dict[str, float | int | bool | None]:
    paired = pd.concat([observed.rename("observed"), simulated.rename("simulated")], axis=1).dropna()
    if paired.empty:
        return {"paired_hours": 0, "qualified": False}
    obs = paired["observed"].to_numpy(float)
    sim = paired["simulated"].to_numpy(float)
    mean_obs = float(np.mean(obs))
    denominator = float(np.sum((obs - mean_obs) ** 2))
    nse = None if denominator == 0.0 else 1.0 - float(np.sum((obs - sim) ** 2)) / denominator
    std_obs = float(np.std(obs))
    std_sim = float(np.std(sim))
    corr = float(np.corrcoef(obs, sim)[0, 1]) if std_obs > 0 and std_sim > 0 else 0.0
    alpha = std_sim / std_obs if std_obs > 0 else float("nan")
    beta = float(np.mean(sim) / mean_obs) if mean_obs != 0 else float("nan")
    kge = None if not np.isfinite(alpha + beta) else 1.0 - math.sqrt((corr - 1.0) ** 2 + (alpha - 1.0) ** 2 + (beta - 1.0) ** 2)
    obs_peak = float(np.max(obs))
    sim_peak = float(np.max(sim))
    obs_peak_time = paired.index[int(np.argmax(obs))]
    sim_peak_time = paired.index[int(np.argmax(sim))]
    peak_error = 100.0 * (sim_peak - obs_peak) / obs_peak if obs_peak else float("nan")
    volume_error = 100.0 * (float(np.sum(sim)) - float(np.sum(obs))) / float(np.sum(obs)) if np.sum(obs) else float("nan")
    peak_time_error = abs((sim_peak_time - obs_peak_time).total_seconds()) / 3600.0
    result = {
        "paired_hours": int(len(paired)),
        "NSE": nse,
        "KGE": kge,
        "PBIAS_percent": volume_error,
        "observed_peak_m3_s": obs_peak,
        "simulated_peak_m3_s": sim_peak,
        "peak_error_percent": peak_error,
        "peak_time_error_hours": peak_time_error,
        "volume_error_percent": volume_error,
    }
    result["qualified"] = qualify_event(result)
    return result


def _simulate_event(
    event: dict,
    precip_times: pd.DatetimeIndex,
    precip_values: np.ndarray,
    grids: list[dict],
    params,
    initial_state: dict,
    warmup_hours: int,
    runoff_path: Path,
) -> None:
    start = event["_start"]
    end = event["_end"]
    warmup_start = start - pd.Timedelta(hours=warmup_hours)
    mask = (precip_times >= warmup_start) & (precip_times <= end)
    times = precip_times[mask]
    if len(times) == 0 or times[0] != warmup_start or times[-1] != end:
        raise ValueError(f"precipitation does not cover warm-up/event window for {event['eventId']}")
    time_positions = np.flatnonzero(mask)
    with runoff_path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["time", "unit_id", "zone_type", "area_km2", "elevation_m", "slope_degree", "RS_mm", "RI_mm", "RG_mm", "total_runoff_mm", "water_balance_error", "quality_flag", "param_set_id"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for grid in grids:
            row = int(grid["array_row"])
            col = int(grid["array_col"])
            precipitation = precip_values[time_positions, row, col]
            forcing = pd.DataFrame(
                {"precipitation": np.maximum(precipitation, 0.0), "temperature": DEFAULT_TEMPERATURE_C, "pet": DEFAULT_PET_MM_H},
                index=times,
            )
            state = LandState(**initial_state)
            result = simulate_land(
                forcing,
                params=params,
                state=state,
                grid=GridInfo(
                    unit_id=grid["unit_id"], zone_type="NON_GLACIER", area_km2=float(grid["area_km2"]), elevation_m=float(grid["elevation_m"]), slope_degree=10.0,
                ),
                context=RunContext(run_id=event["eventId"], scenario_id="MULTI_EVENT_BASELINE", param_set_id="NANANXI_XAJ_REFERENCE_V1", simulation_mode="EVENT"),
            )
            for timestamp, output in result.iterrows():
                writer.writerow({
                    "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"), "unit_id": grid["unit_id"], "zone_type": "NON_GLACIER",
                    "area_km2": grid["area_km2"], "elevation_m": grid["elevation_m"], "slope_degree": 10.0,
                    "RS_mm": output["RS"], "RI_mm": output["RI"], "RG_mm": output["RG"], "total_runoff_mm": output["total_runoff"],
                    "water_balance_error": output["water_balance_error"], "quality_flag": output["quality_flag"], "param_set_id": "NANANXI_XAJ_REFERENCE_V1",
                })


def _evaluate_one_event(task: dict) -> dict:
    """Worker entry point used by the multi-event process pool."""
    event = task["event"]
    event["_start"] = pd.Timestamp(event["_start"])
    event["_end"] = pd.Timestamp(event["_end"])
    root = Path(task["root"])
    data_root = Path(task["data_root"])
    package_root = _find_package_root(data_root)
    precip_dir = package_root / "降雨资料" / "1公里逐小时降雨场"
    precip_times, precip_values = _load_year_precip(precip_dir, event["_start"].year)
    grids = list(csv.DictReader((root / "data_interface" / "runoff_2017" / "grid_static.csv").open("r", encoding="utf-8-sig", newline="")))
    params = load_parameter_set(root / "config" / "parameter_set_land.json")
    initial_payload = json.loads((root / "config" / "initial_state_land.json").read_text(encoding="utf-8"))
    initial_state = {key: initial_payload[key] for key in ("swe", "snow_liquid", "WU", "WL", "WD", "S", "FR")}
    topology_path = root / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"
    mapping_path = root / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"
    routing_params_path = root / "validation" / "calibration_routing_2017" / "best_routing_parameters.json"
    controls_path = root / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"
    observed = pd.Series(task["observed_values"], index=pd.to_datetime(task["observed_times"]))
    with tempfile.TemporaryDirectory(prefix="multi_event_worker_") as temp:
        temp_dir = Path(temp)
        runoff_path = temp_dir / "runoff.csv"
        routing_path = temp_dir / "routing.csv"
        _simulate_event(event, precip_times, precip_values, grids, params, initial_state, task["warmup_hours"], runoff_path)
        aggregate_runoff_to_routing_units(runoff_path, mapping_path, topology_path, routing_path)
        engine = RoutingEngine(
            topology=read_topology_json(topology_path), parameters=read_parameters_json(routing_params_path),
            control_points=read_control_points_json(controls_path),
        )
        result = engine.run(read_runoff_csv(routing_path))
    control = pd.DataFrame([{"time": row.time, "flow": row.outflow_cms} for row in result.control_flows])
    if control.empty:
        metrics = {"paired_hours": 0, "qualified": False}
    else:
        control["time"] = pd.to_datetime(control["time"])
        control = control.groupby("time")["flow"].mean()
        metrics = _metrics(observed.loc[event["_start"] : event["_end"]], control.loc[event["_start"] : event["_end"]])
    year = event["_start"].year
    group = "CALIBRATION_CANDIDATE" if year <= 2016 else ("VALIDATION" if year <= 2020 else "INDEPENDENT_TEST")
    return {"event_id": event["eventId"], "year": year, "start": event["_start"].isoformat(), "end": event["_end"].isoformat(), "group": group, **metrics}


def run(args: argparse.Namespace) -> dict:
    data_root = Path(args.data_root)
    package_root = _find_package_root(data_root)
    precip_dir = package_root / "降雨资料" / "1公里逐小时降雨场"
    available_years = {int(path.stem.rsplit("_", 1)[-1]) for path in precip_dir.glob("*.nc")}
    years = set(range(args.start_year, args.end_year + 1)) & available_years
    events, selection = _load_events(data_root, years)
    if args.max_events:
        events = events[: args.max_events]
        selection["selected_count_after_limit"] = len(events)
    if not events:
        raise RuntimeError("no eligible events found")

    grid_file = ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"
    grids = list(csv.DictReader(grid_file.open("r", encoding="utf-8-sig", newline="")))
    params = load_parameter_set(ROOT / "config" / "parameter_set_land.json")
    initial_state_payload = json.loads((ROOT / "config" / "initial_state_land.json").read_text(encoding="utf-8"))
    initial_state = {key: initial_state_payload[key] for key in ("swe", "snow_liquid", "WU", "WL", "WD", "S", "FR")}
    topology_path = ROOT / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"
    mapping_path = ROOT / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"
    routing_params_path = ROOT / "validation" / "calibration_routing_2017" / "best_routing_parameters.json"
    controls_path = ROOT / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"
    observed = _load_observed(data_root)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    tasks = []
    for event in events:
        obs = observed.loc[event["_start"] : event["_end"]]
        tasks.append({
            "event": event, "root": str(ROOT), "data_root": str(data_root), "warmup_hours": args.warmup_hours,
            "observed_times": [time.isoformat() for time in obs.index], "observed_values": [float(value) for value in obs.values],
        })
    workers = max(1, int(args.workers))
    if workers == 1:
        results = (_evaluate_one_event(task) for task in tasks)
        for number, row in enumerate(results, start=1):
            summary_rows.append(row)
            print(f"[{number}/{len(events)}] {row['event_id']} qualified={row.get('qualified')} NSE={row.get('NSE')}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_evaluate_one_event, task) for task in tasks]
            for number, future in enumerate(futures, start=1):
                row = future.result()
                summary_rows.append(row)
                print(f"[{number}/{len(events)}] {row['event_id']} qualified={row.get('qualified')} NSE={row.get('NSE')}", flush=True)

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(output_dir / "event_metrics.csv", index=False, encoding="utf-8-sig")
    grouped = {}
    for name, frame in summary.groupby("group"):
        grouped[name] = {"event_count": int(len(frame)), "qualified_count": int(frame["qualified"].sum()), "qualified_rate_percent": float(frame["qualified"].mean() * 100.0), "mean_NSE": float(frame["NSE"].mean()), "mean_KGE": float(frame["KGE"].mean()), "mean_abs_peak_error_percent": float(frame["peak_error_percent"].abs().mean()), "mean_abs_volume_error_percent": float(frame["volume_error_percent"].abs().mean())}
    report = {
        "status": "BASELINE_MULTI_EVENT_EVALUATION",
        "selection": selection,
        "parameter_set": "config/parameter_set_land.json",
        "routing_parameter_set": "validation/calibration_routing_2017/best_routing_parameters.json",
        "forcing_assumptions": {"temperature_c": DEFAULT_TEMPERATURE_C, "pet_mm_h": DEFAULT_PET_MM_H, "warmup_hours": args.warmup_hours},
        "qualification_rule": qualification_definition(),
        "overall": {"event_count": int(len(summary)), "qualified_count": int(summary["qualified"].sum()), "qualified_rate_percent": float(summary["qualified"].mean() * 100.0), "mean_NSE": float(summary["NSE"].mean()), "mean_KGE": float(summary["KGE"].mean()), "mean_abs_peak_error_percent": float(summary["peak_error_percent"].abs().mean()), "mean_abs_volume_error_percent": float(summary["volume_error_percent"].abs().mean())},
        "groups": grouped,
    }
    (output_dir / "multi_event_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
    axes[0].scatter(summary["observed_peak_m3_s"], summary["simulated_peak_m3_s"], c=summary["qualified"].map({True: "#2c7fb8", False: "#d95f0e"}), alpha=0.8)
    lim = max(summary["observed_peak_m3_s"].max(), summary["simulated_peak_m3_s"].max()) * 1.05
    axes[0].plot([0, lim], [0, lim], "k--", linewidth=1)
    axes[0].set(xlabel="Observed peak flow (m³/s)", ylabel="Simulated peak flow (m³/s)", title="Multi-event peak comparison")
    rate = summary.groupby("group")["qualified"].mean().mul(100).reindex(["CALIBRATION_CANDIDATE", "VALIDATION", "INDEPENDENT_TEST"])
    rate.plot.bar(ax=axes[1], color=["#7fcdbb", "#41b6c4", "#225ea8"], rot=20)
    axes[1].set(xlabel="Evaluation group", ylabel="Qualified rate (%)", title="Qualified-rate screening")
    axes[1].set_ylim(0, 100)
    fig.savefig(output_dir / "multi_event_qualification.png", dpi=180)
    plt.close(fig)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--start-year", type=int, default=2001)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--warmup-hours", type=int, default=DEFAULT_WARMUP_HOURS)
    parser.add_argument("--max-events", type=int, default=0, help="debug limit; zero means all eligible events")
    parser.add_argument("--workers", type=int, default=4, help="parallel event workers")
    parser.add_argument("--output-dir", default=str(ROOT / "validation" / "multi_event_2001_2024"))
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
