"""Simulate six representative independent-test floods with one fixed parameter set.

The script is deliberately an orchestration layer.  It reuses the platform's
XAJ runoff model, grid-to-routing adapter and routing engine, while keeping
event selection and output formatting explicit for inspection and reporting.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
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
from runoff_platform.core.parameters import apply_parameter_overrides  # noqa: E402
from runoff_platform.core.types import GridInfo, LandParams, LandState, RunContext  # noqa: E402
from runoff_platform.models.xaj import simulate_land  # noqa: E402
from xaj_routing.engine import RoutingEngine  # noqa: E402
from xaj_routing.io import (  # noqa: E402
    read_control_points_json,
    read_parameters_json,
    read_runoff_csv,
    read_topology_json,
)
from state_initialization.routing import build_observed_flow_anchored_state  # noqa: E402


DEFAULT_WARMUP_HOURS = 168
DEFAULT_TEMPERATURE_C = 15.0
MONTHLY_PET_MM_DAY = (3.0, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 5.5, 4.5, 3.5, 3.0, 3.0)


def _discover_data_root() -> Path:
    """Find the extracted observation package beside the platform repository."""
    candidates = sorted(ROOT.parents[1].glob("南安溪-徐畈数据资料整合"))
    if not candidates:
        raise FileNotFoundError("cannot discover 南安溪-徐畈数据资料整合; pass --data-root")
    return candidates[0]


def _data_package_root(data_root: Path) -> Path:
    candidates = list(data_root.glob("**/02_分布式新安江模型输入资料"))
    if not candidates:
        raise FileNotFoundError("cannot find 02_分布式新安江模型输入资料")
    return candidates[0]


def _load_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _select_events(metrics_path: Path, catalog_path: Path, selection: str) -> tuple[list[dict], dict]:
    """Select two events from each peak-flow tier.

    The default uses the frozen independent-test assignment from the latest
    stability run.  Tiers are formed by sorted observed peak flow; within each
    tier the endpoints are retained to show the range of responses.
    """
    metrics = pd.read_csv(metrics_path)
    catalog = _load_catalog(catalog_path)
    packages = {item["eventId"]: item for item in catalog["packages"]}
    if selection == "independent":
        frame = metrics.loc[metrics["group"].eq("INDEPENDENT_TEST")].copy()
    elif selection == "quantile":
        frame = metrics.copy()
    else:
        raise ValueError("selection must be independent or quantile")
    frame = frame.loc[frame["event_id"].isin(packages)]
    frame = frame.sort_values("observed_peak_m3_s", ascending=False).reset_index(drop=True)
    if len(frame) < 6:
        raise ValueError(f"only {len(frame)} eligible events available; six are required")

    n = len(frame)
    slices = {
        "LARGE": frame.iloc[: n // 3],
        "MEDIUM": frame.iloc[n // 3 : 2 * n // 3],
        "SMALL": frame.iloc[2 * n // 3 :],
    }
    selected: list[dict] = []
    for tier, subset in slices.items():
        positions = [0, len(subset) - 1]
        for position in positions:
            row = subset.iloc[position]
            package = packages[str(row["event_id"])]
            selected.append(
                {
                    "event_id": str(row["event_id"]),
                    "category": tier,
                    "year": int(row["year"]),
                    "start": str(row["start"]),
                    "end": str(row["end"]),
                    "observed_peak_m3_s": float(row["observed_peak_m3_s"]),
                    "observed_nse_from_reference_run": float(row["NSE"]),
                    "event_status": package.get("status"),
                    "grid_basin_average_total_rain_mm": package.get("spatialRainInputs", {}).get(
                        "gridBasinAverageTotalRainMm"
                    ),
                    "selection_rank_in_group": int(row.name) + 1,
                }
            )
    return selected, {
        "method": selection,
        "source_metrics": str(metrics_path),
        "source_catalog": str(catalog_path),
        "independent_test_count": int(len(frame)),
        "tier_rule": "observed peak flow sorted descending; three equal-size tiers; tier endpoints selected",
    }


def _load_precipitation(precip_dir: Path, year: int, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DatetimeIndex, np.ndarray]:
    path = precip_dir / f"nananxi_precip_hourly_1km_{year}.nc"
    if not path.exists():
        raise FileNotFoundError(path)
    with xr.open_dataset(path, engine="h5netcdf") as dataset:
        times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
        mask = (times >= start) & (times <= end)
        if not mask.any() or times[mask][0] != start or times[mask][-1] != end:
            raise ValueError(f"precipitation does not cover {start} to {end}")
        values = np.asarray(dataset["precipitation"].isel(time=np.flatnonzero(mask)).values, dtype=float)
    return times[mask], values


def _load_observed_flow(data_root: Path) -> pd.Series:
    workbook = next(data_root.glob("**/徐畈出口逐小时水位流量.xlsx"))
    frame = pd.read_excel(workbook, sheet_name="逐小时水位流量", usecols=[0, 2])
    frame.columns = ["time", "flow"]
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["flow"] = pd.to_numeric(frame["flow"], errors="coerce")
    return frame.dropna().drop_duplicates("time").set_index("time")["flow"].sort_index()


def _load_params(parameter_path: Path) -> tuple[LandParams, dict]:
    payload = json.loads(parameter_path.read_text(encoding="utf-8-sig"))
    values = payload.get("parameters", payload)
    params = apply_parameter_overrides(LandParams(), values)
    return params, payload


def _initial_state(params: LandParams) -> LandState:
    """Resolve the platform fraction template for the selected capacities."""
    return LandState(
        swe=0.0,
        snow_liquid=0.0,
        WU=0.6 * params.WUM,
        WL=0.6875 * params.WLM,
        WD=0.6 * max(params.WDM, 0.0),
        S=(1.0 / 3.0) * params.SM,
        FR=0.5,
    )


def _metrics(observed: pd.Series, simulated: pd.Series) -> dict:
    paired = pd.concat([observed.rename("observed"), simulated.rename("simulated")], axis=1).dropna()
    if paired.empty:
        return {"paired_hours": 0, "qualified": False}
    obs = paired["observed"].to_numpy(float)
    sim = paired["simulated"].to_numpy(float)
    mean_obs = float(np.mean(obs))
    denominator = float(np.sum((obs - mean_obs) ** 2))
    nse = None if denominator == 0.0 else 1.0 - float(np.sum((obs - sim) ** 2)) / denominator
    std_obs, std_sim = float(np.std(obs)), float(np.std(sim))
    corr = float(np.corrcoef(obs, sim)[0, 1]) if std_obs > 0.0 and std_sim > 0.0 else 0.0
    alpha = std_sim / std_obs if std_obs > 0.0 else float("nan")
    beta = float(np.mean(sim) / mean_obs) if mean_obs else float("nan")
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
        "observed_peak_m3_s": obs_peak,
        "simulated_peak_m3_s": sim_peak,
        "peak_error_percent": peak_error,
        "observed_peak_time": obs_peak_time.isoformat(),
        "simulated_peak_time": sim_peak_time.isoformat(),
        "peak_time_error_hours": peak_time_error,
        "observed_volume_m3_s_h": float(np.sum(obs)),
        "simulated_volume_m3_s_h": float(np.sum(sim)),
        "volume_error_percent": volume_error,
    }
    result["qualified"] = qualify_event(result)
    return result


def _write_event_plot(frame: pd.DataFrame, path: Path, title: str) -> None:
    fig, (ax_rain, ax_flow) = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True, gridspec_kw={"height_ratios": [1, 3]}, constrained_layout=True)
    ax_rain.bar(frame.index, frame["precipitation_mm"], width=0.03, color="#4c9bd6", alpha=0.8)
    ax_rain.invert_yaxis()
    ax_rain.set_ylabel("Rain (mm/h)")
    ax_rain.grid(True, axis="y", color="#d9dde3", linewidth=0.6)
    ax_flow.plot(frame.index, frame["observed_flow_m3_s"], color="#202124", linewidth=1.8, label="Observed")
    ax_flow.plot(frame.index, frame["simulated_flow_m3_s"], color="#1f77b4", linewidth=1.6, label="Simulated")
    ax_flow.set_ylabel("Discharge (m3/s)")
    ax_flow.set_xlabel("Time")
    ax_flow.set_title(title)
    ax_flow.grid(True, color="#d9dde3", linewidth=0.7)
    ax_flow.legend(frameon=False, ncol=2, loc="upper right")
    ax_flow.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_comparison_plot(frames: list[tuple[dict, pd.DataFrame]], path: Path) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
    colors = {"LARGE": "#b2182b", "MEDIUM": "#ef8a62", "SMALL": "#2166ac"}
    for ax, (meta, frame) in zip(axes.flat, frames):
        ax.plot(frame.index, frame["observed_flow_m3_s"], color="#202124", linewidth=1.3, label="Observed")
        ax.plot(frame.index, frame["simulated_flow_m3_s"], color=colors[meta["category"]], linewidth=1.3, label="Simulated")
        ax.set_title(f"{meta['category']} | {meta['event_id']} | {meta['year']} | peak {meta['observed_peak_m3_s']:.1f} m3/s", fontsize=9)
        ax.grid(True, color="#d9dde3", linewidth=0.6)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax.tick_params(axis="x", labelrotation=25, labelsize=8)
        ax.tick_params(axis="y", labelsize=8)
    axes[0, 0].legend(frameon=False, fontsize=9)
    fig.suptitle("Representative independent-test floods: observed versus simulated", fontsize=14)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _simulate_event(meta: dict, args, params: LandParams, grids: list[dict], observed: pd.Series, root: Path, precip_dir: Path, output_dir: Path) -> tuple[dict, pd.DataFrame]:
    start = pd.Timestamp(meta["start"])
    end = pd.Timestamp(meta["end"])
    warmup_start = start - pd.Timedelta(hours=args.warmup_hours)
    times, precipitation = _load_precipitation(precip_dir, start.year, warmup_start, end)
    n_times = len(times)
    total_area = sum(float(grid["area_km2"]) for grid in grids)
    weighted_precip = np.zeros(n_times)
    weighted_components = {component: np.zeros(n_times) for component in ("RS", "RI", "RG", "total_runoff")}
    runoff_path = output_dir / f"runoff_grid_{meta['event_id']}.csv"
    fields = ["time", "unit_id", "zone_type", "area_km2", "elevation_m", "slope_degree", "RS_mm", "RI_mm", "RG_mm", "total_runoff_mm", "water_balance_error", "quality_flag", "param_set_id"]
    with runoff_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for grid in grids:
            area = float(grid["area_km2"])
            row_index, col_index = int(grid["array_row"]), int(grid["array_col"])
            forcing = pd.DataFrame(
                {
                    "precipitation": np.maximum(precipitation[:, row_index, col_index], 0.0),
                    "temperature": DEFAULT_TEMPERATURE_C,
                    "pet": [MONTHLY_PET_MM_DAY[t.month - 1] / 24.0 for t in times],
                },
                index=times,
            )
            result = simulate_land(
                forcing,
                params=params,
                state=_initial_state(params),
                grid=GridInfo(unit_id=grid["unit_id"], zone_type="NON_GLACIER", area_km2=area, elevation_m=float(grid["elevation_m"]), slope_degree=float(args.default_slope_degree)),
                context=RunContext(run_id=meta["event_id"], scenario_id="REPRESENTATIVE_FLOOD", param_set_id=args.parameter_set_id, simulation_mode="EVENT"),
            )
            values = result["precipitation"].to_numpy(float)
            weighted_precip += values * area / total_area
            for component in weighted_components:
                weighted_components[component] += result[component].to_numpy(float) * area / total_area
            for timestamp, output in result.iterrows():
                writer.writerow({
                    "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"), "unit_id": grid["unit_id"], "zone_type": "NON_GLACIER", "area_km2": area, "elevation_m": grid["elevation_m"], "slope_degree": args.default_slope_degree,
                    "RS_mm": output["RS"], "RI_mm": output["RI"], "RG_mm": output["RG"], "total_runoff_mm": output["total_runoff"], "water_balance_error": output["water_balance_error"], "quality_flag": output["quality_flag"], "param_set_id": args.parameter_set_id,
                })

    mapping_path = root / "validation" / "integrated_2017" / "grid_to_unit_weight.csv"
    topology_path = root / "data_interface" / "spatial" / "nan_an_topology_clipped_v1.json"
    routing_path = output_dir / f"runoff_routing_units_{meta['event_id']}.csv"
    aggregate_runoff_to_routing_units(runoff_path, mapping_path, topology_path, routing_path)
    controls_path = root / "data_interface" / "spatial" / "xufan_70115800_control_point_snapped_v1.json"
    topology = read_topology_json(topology_path)
    controls = read_control_points_json(controls_path)
    if len(controls) != 1:
        raise ValueError("representative simulation requires exactly one control point")
    if start not in observed.index:
        raise ValueError(f"observed flow is missing at event start: {meta['event_id']}")
    initial_routing_state = build_observed_flow_anchored_state(
        topology,
        controls[0],
        float(observed.loc[start]),
    )
    routing_records = [
        record for record in read_runoff_csv(routing_path)
        if pd.Timestamp(record.time) >= start
    ]
    engine = RoutingEngine(
        topology=topology,
        parameters=read_parameters_json(args.routing_parameters),
        initial_state=initial_routing_state,
        control_points=controls,
    )
    routing_result = engine.run(routing_records)
    control = pd.DataFrame({"time": [row.time for row in routing_result.control_flows], "flow": [row.outflow_cms for row in routing_result.control_flows]})
    control = control.groupby(pd.to_datetime(control["time"]))["flow"].mean().sort_index()
    outlet = pd.DataFrame({"time": [row.time for row in routing_result.outlet_flows], "flow": [row.outflow_cms for row in routing_result.outlet_flows]})
    outlet = outlet.groupby(pd.to_datetime(outlet["time"]))["flow"].mean().sort_index() if not outlet.empty else pd.Series(dtype=float)
    event_index = pd.date_range(start, end, freq="h")
    observed_event = observed.reindex(event_index)
    simulated_event = control.reindex(event_index)
    frame = pd.DataFrame({"precipitation_mm": weighted_precip, "RS_mm": weighted_components["RS"], "RI_mm": weighted_components["RI"], "RG_mm": weighted_components["RG"], "total_runoff_mm": weighted_components["total_runoff"]}, index=times).loc[event_index]
    frame["observed_flow_m3_s"] = observed_event
    frame["simulated_flow_m3_s"] = simulated_event
    frame["simulated_outlet_flow_m3_s"] = outlet.reindex(event_index)
    hydrograph_path = output_dir / f"event_hydrograph_{meta['event_id']}.csv"
    frame.to_csv(hydrograph_path, index_label="time", encoding="utf-8-sig")
    metrics = _metrics(observed_event, simulated_event)
    report_meta = {**meta, **metrics, "hydrograph_csv": str(hydrograph_path), "hydrograph_png": str(output_dir / f"event_hydrograph_{meta['event_id']}.png")}
    _write_event_plot(frame, Path(report_meta["hydrograph_png"]), f"{meta['category']} flood {meta['event_id']} ({meta['year']})")
    return report_meta, frame


def run(args: argparse.Namespace) -> dict:
    root = ROOT
    data_root = Path(args.data_root).resolve() if args.data_root else _discover_data_root()
    catalog_path = Path(args.event_catalog).resolve() if args.event_catalog else root / "data_interface" / "event_catalog" / "nananxi_flood_event_catalog_qc_20260819.json"
    metrics_path = Path(args.selection_metrics).resolve() if args.selection_metrics else root / "validation" / "production_calibration_monthly_pet_20260819" / "formal_runs" / "stability_WLM_100_seed_20260819" / "event_metrics.csv"
    parameter_path = Path(args.parameter_set).resolve()
    routing_parameter_path = Path(args.routing_parameters).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected, selection_info = _select_events(metrics_path, catalog_path, args.selection)
    params, parameter_payload = _load_params(parameter_path)
    grids = list(csv.DictReader((root / "data_interface" / "runoff_2017" / "grid_static.csv").open("r", encoding="utf-8-sig", newline="")))
    observed = _load_observed_flow(data_root)
    precip_dir = root / "data_interface" / "precipitation_qc_20260819" / "1km_hourly"
    reports, frames = [], []
    for number, meta in enumerate(selected, start=1):
        report_meta, frame = _simulate_event(meta, args, params, grids, observed, root, precip_dir, output_dir)
        reports.append(report_meta)
        frames.append((report_meta, frame))
        print(f"[{number}/{len(selected)}] {meta['category']} {meta['event_id']} NSE={report_meta.get('NSE')} peak_error={report_meta.get('peak_error_percent'):.2f}%", flush=True)
    _write_comparison_plot(frames, output_dir / "representative_floods_comparison.png")
    pd.DataFrame(reports).to_csv(output_dir / "representative_flood_summary.csv", index=False, encoding="utf-8-sig")
    summary = {
        "status": "DIAGNOSTIC_REPRESENTATIVE_FLOOD_SIMULATION",
        "parameter_set": str(parameter_path),
        "parameter_set_id": args.parameter_set_id,
        "parameter_status": parameter_payload.get("status"),
        "routing_parameter_set": str(routing_parameter_path),
        "forcing_assumptions": {"temperature_c": DEFAULT_TEMPERATURE_C, "pet_mode": "MONTHLY_CLIMATOLOGY", "monthly_pet_mm_day": MONTHLY_PET_MM_DAY, "warmup_hours": args.warmup_hours, "default_slope_degree": args.default_slope_degree},
        "selection": selection_info,
        "qualification_rule": qualification_definition(),
        "events": reports,
        "overall": {"event_count": len(reports), "qualified_count": sum(bool(row.get("qualified")) for row in reports), "qualified_rate_percent": 100.0 * sum(bool(row.get("qualified")) for row in reports) / len(reports), "mean_NSE": float(np.nanmean([row.get("NSE", np.nan) for row in reports])), "mean_KGE": float(np.nanmean([row.get("KGE", np.nan) for row in reports]))},
        "limitations": ["WLM=100 mm reaches the current calibration upper bound; parameters are diagnostic candidates, not final production parameters.", "PET is monthly climatology because observed evaporation is not yet available.", "Non-glacier temperature is fixed at 15 C, so no snow phase partition is active.", "Each flood uses 168-hour land-state warm-up and observed-flow-anchored routing state, matching the formal calibration strategy; continuous inter-event state is not simulated."],
    }
    (output_dir / "representative_flood_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameter-set", required=True)
    parser.add_argument("--routing-parameters", required=True)
    parser.add_argument("--parameter-set-id", default="NANANXI_MULTI_EVENT_RESOLVED_20260818")
    parser.add_argument("--data-root", default="")
    parser.add_argument("--event-catalog", default="")
    parser.add_argument("--selection-metrics", default="")
    parser.add_argument("--selection", choices=("independent", "quantile"), default="independent")
    parser.add_argument("--warmup-hours", type=int, default=DEFAULT_WARMUP_HOURS)
    parser.add_argument("--default-slope-degree", type=float, default=10.0)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
