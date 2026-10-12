"""Run the integrated model directly from an Access MDB flood database.

This is deliberately a file-based adapter around the existing model contracts:
MDB -> station/hourly forcing -> 1 km grid XAJ -> routing engine -> report.
It never replaces the source MDB and writes every derived product to a run
specific directory so a platform run is auditable.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer


MODEL_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = MODEL_ROOT / "code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from runoff_platform.core.parameters import apply_parameter_overrides
from runoff_platform.core.types import GridInfo, LandParams, LandState, RunContext
from runoff_platform.data.precipitation_idw import idw_interpolate_hourly
from runoff_platform.models.xaj import simulate_land
from integration.validation import compare_control_flow
from xaj_routing.engine import RoutingEngine
from xaj_routing.io import (
    read_control_points_json,
    read_parameters_json,
    read_state_json,
    read_topology_json,
    write_component_flows_csv,
    write_control_flows_csv,
    write_json,
    write_outlet_flows_csv,
    write_reach_flows_csv,
)


# [MDB-01] Coordinates are not stored in rain_gauge; they are the checked
# station coordinates used by the supplied spatial data package.
STATION_COORDS = {
    "87001": (119.461603, 30.139650),
    "87002": (119.459018, 30.130794),
    "87003": (119.492530, 30.139403),
    "87004": (119.465508, 30.105292),
    "87005": (119.482141, 30.116191),
    "87006": (119.476389, 30.090278),
    "87007": (119.507222, 30.118056),
    "87008": (119.509898, 30.111903),
    "87009": (119.516944, 30.076667),
    "87010": (119.517716, 30.074049),
}


def _ado_script() -> Path:
    candidates = [
        Path(__file__).resolve().parents[4] / "mdb_ado_query.vbs",
        MODEL_ROOT.parent.parent / "mdb_ado_query.vbs",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("mdb_ado_query.vbs is required beside the model package")


def _ado_query(mdb: Path, sql: str) -> list[dict[str, str]]:
    """Execute a read-only ADO query and parse the script's CSV-safe output."""
    completed = subprocess.run(
        ["cscript.exe", "//nologo", str(_ado_script()), str(mdb), sql],
        check=True,
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
    )
    rows = list(csv.DictReader(io.StringIO(completed.stdout)))
    if not rows:
        raise ValueError(f"MDB query returned no rows: {sql}")
    return rows


def _parse_mdb_time(value: str) -> datetime:
    text = str(value).strip().replace("/", "-")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise ValueError(f"invalid MDB datetime: {value!r}")


def _sql_datetime(value: datetime) -> str:
    return value.strftime("#%Y/%m/%d %H:%M:%S#")


def _read_event(mdb: Path, flood_code: str) -> tuple[datetime, datetime]:
    rows = _ado_query(mdb, f"SELECT flcd,s_dt,e_dt FROM fl_code WHERE flcd={int(flood_code)}")
    row = rows[0]
    return _parse_mdb_time(row["s_dt"]), _parse_mdb_time(row["e_dt"])


def _read_rainfall(mdb: Path, start: datetime, end: datetime) -> pd.DataFrame:
    # [MDB-02] ReFactorDT is the normalized hourly station rainfall table.
    sql = (
        "SELECT HS_Code,YMDHM,DTRN FROM ReFactorDT "
        f"WHERE HS_Code BETWEEN 87001 AND 87010 "
        f"AND YMDHM>={_sql_datetime(start)} AND YMDHM<={_sql_datetime(end)} "
        "ORDER BY YMDHM,HS_Code"
    )
    rows = _ado_query(mdb, sql)
    frame = pd.DataFrame(rows)
    frame["time"] = frame["YMDHM"].map(_parse_mdb_time)
    frame["station_id"] = frame["HS_Code"].astype(str).str.strip()
    frame["rainfall_mm"] = pd.to_numeric(frame["DTRN"], errors="coerce").fillna(0.0).clip(lower=0.0)
    wide = frame.pivot_table(index="time", columns="station_id", values="rainfall_mm", aggfunc="sum")
    times = pd.date_range(start=start, end=end, freq="h")
    wide = wide.reindex(times).fillna(0.0)
    for station in STATION_COORDS:
        if station not in wide:
            wide[station] = np.nan
    return wide[sorted(STATION_COORDS)]


def _read_observed_flow(mdb: Path, start: datetime, end: datetime, output: Path) -> pd.DataFrame:
    # [MDB-03] CGB087012 is the XuFan control-section discharge series.
    sql = (
        "SELECT stcd,ymdhm,q FROM st_river_r "
        "WHERE stcd='CGB087012' "
        f"AND ymdhm>={_sql_datetime(start)} AND ymdhm<={_sql_datetime(end)} "
        "ORDER BY ymdhm"
    )
    rows = _ado_query(mdb, sql)
    frame = pd.DataFrame(rows)
    frame["time"] = frame["ymdhm"].map(_parse_mdb_time)
    frame["observed_flow_m3_s"] = pd.to_numeric(frame["q"], errors="coerce")
    frame = frame[["time", "observed_flow_m3_s"]].dropna().drop_duplicates("time").sort_values("time")
    frame.to_csv(output, index=False, encoding="utf-8")
    return frame.set_index("time")


def _grid_cells() -> tuple[list[dict], np.ndarray, np.ndarray]:
    spatial = MODEL_ROOT / "data_interface" / "spatial"
    static_path = MODEL_ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"
    geojson = next(spatial.glob("*有效格点.geojson"))
    with static_path.open("r", encoding="utf-8-sig", newline="") as handle:
        static = list(csv.DictReader(handle))
    static_by_rc = {(int(row["source_row"]), int(row["source_col"])): row for row in static}
    payload = json.loads(geojson.read_text(encoding="utf-8"))
    cells = []
    for feature in payload["features"]:
        prop = feature["properties"]
        row = static_by_rc[(int(prop["row"]), int(prop["col"]))]
        geometry = feature["geometry"]
        coords = geometry["coordinates"][0]
        lon = float(sum(point[0] for point in coords) / len(coords))
        lat = float(sum(point[1] for point in coords) / len(coords))
        cells.append({**row, "lon": lon, "lat": lat})
    cells.sort(key=lambda row: row["unit_id"])
    return (
        cells,
        np.asarray([float(row["lon"]) for row in cells]),
        np.asarray([float(row["lat"]) for row in cells]),
    )


def _grid_forcing(stations: pd.DataFrame, cells: list[dict], output: Path) -> pd.DataFrame:
    # [FORCING-01] IDW is performed in UTM 50N, matching the DEM/grid CRS.
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    station_xy = np.asarray(transformer.transform(
        np.asarray([STATION_COORDS[s][0] for s in stations.columns]),
        np.asarray([STATION_COORDS[s][1] for s in stations.columns]),
    )).T
    target_xy = np.asarray(transformer.transform(
        np.asarray([float(row["lon"]) for row in cells]),
        np.asarray([float(row["lat"]) for row in cells]),
    )).T
    values, station_count = idw_interpolate_hourly(
        stations.to_numpy(dtype=float), station_xy[:, 0], station_xy[:, 1],
        target_xy[:, 0], target_xy[:, 1], power=2.0, minimum_stations=1,
    )
    values = np.nan_to_num(values, nan=0.0)
    rows = []
    for time_index, timestamp in enumerate(stations.index):
        for cell_index, cell in enumerate(cells):
            rows.append({
                "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "unit_id": cell["unit_id"],
                "precipitation_mm": float(values[time_index, cell_index]),
                "temperature_c_assumed": 15.0,
                "pet_mm_h_assumed": 0.08,
                "source_station_count": int(station_count[time_index]),
                "forcing_quality_flag": "TEMP_PET_ASSUMED",
            })
    frame = pd.DataFrame(rows)
    frame.to_csv(output, index=False, encoding="utf-8")
    return frame


def _load_land_params(parameter_path: Path) -> tuple[LandParams, dict]:
    payload = json.loads(parameter_path.read_text(encoding="utf-8-sig"))
    params = LandParams()
    overrides = payload.get("land_parameters", payload)
    apply_parameter_overrides(params, overrides)
    return params, overrides


def _run_grid_xaj(forcing: pd.DataFrame, cells: list[dict], params: LandParams, flood_code: str) -> pd.DataFrame:
    # [RUNOFF-01] Each grid gets its own continuous state over the warm-up.
    forcing["time_dt"] = pd.to_datetime(forcing["time"])
    output = []
    for cell in cells:
        grid_forcing = forcing[forcing["unit_id"] == cell["unit_id"]].copy()
        grid_forcing = grid_forcing.set_index("time_dt")
        meteo = pd.DataFrame({
            "precipitation": grid_forcing["precipitation_mm"],
            "temperature": grid_forcing["temperature_c_assumed"],
            "pet": grid_forcing["pet_mm_h_assumed"],
        })
        result = simulate_land(
            meteo,
            params=params,
            state=LandState(swe=0.0, snow_liquid=0.0, WU=12.0, WL=55.0, WD=30.0, S=5.0, FR=0.5),
            grid=GridInfo(
                unit_id=cell["unit_id"], area_km2=float(cell["area_km2"]),
                elevation_m=float(cell["elevation_m"]), slope_degree=10.0,
            ),
            context=RunContext(run_id=f"MDB-{flood_code}", scenario_id="MDB_DIRECT", param_set_id="MDB_PARAMETER_SET", simulation_mode="CONTINUOUS"),
        )
        result = result.reset_index().rename(columns={"index": "time"})
        output.append(result)
    combined = pd.concat(output, ignore_index=True)
    combined["time"] = pd.to_datetime(combined["time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    return combined


def _write_runoff_wide(result: pd.DataFrame, output: Path) -> None:
    fields = ["time", "unit_id", "zone_type", "area_km2", "elevation_m", "slope_degree", "RS", "RI", "RG", "total_runoff", "water_balance_error", "quality_flag", "param_set_id"]
    result[fields].rename(columns={"total_runoff": "total_runoff_mm"}).to_csv(output, index=False, encoding="utf-8")


def _metrics(observed: pd.DataFrame, simulated: pd.DataFrame) -> dict:
    merged = observed.join(simulated, how="inner").dropna()
    obs = merged["observed_flow_m3_s"].to_numpy(float)
    sim = merged["simulated_flow_m3_s"].to_numpy(float)
    if len(obs) == 0:
        raise ValueError("no overlapping observed and simulated hours")
    denom = float(np.sum((obs - obs.mean()) ** 2))
    nse = None if denom == 0.0 else float(1.0 - np.sum((obs - sim) ** 2) / denom)
    r_obs = np.corrcoef(obs, sim)[0, 1] if len(obs) > 1 else 0.0
    alpha = float(np.std(sim, ddof=1) / np.std(obs, ddof=1)) if len(obs) > 1 and np.std(obs, ddof=1) else 0.0
    beta = float(sim.mean() / obs.mean()) if obs.mean() else None
    kge = None if beta is None else float(1.0 - math.sqrt((r_obs - 1.0) ** 2 + (alpha - 1.0) ** 2 + (beta - 1.0) ** 2))
    obs_peak_time = merged.index[int(np.argmax(obs))]
    sim_peak_time = merged.index[int(np.argmax(sim))]
    return {
        "paired_hour_count": int(len(merged)), "nse": nse, "kge": kge,
        "rmse_m3_s": float(np.sqrt(np.mean((obs - sim) ** 2))),
        "pbias_percent": float(100.0 * (sim.sum() - obs.sum()) / obs.sum()) if obs.sum() else None,
        "observed_peak_m3_s": float(obs.max()), "simulated_peak_m3_s": float(sim.max()),
        "peak_relative_error_percent": float(100.0 * (sim.max() - obs.max()) / obs.max()) if obs.max() else None,
        "observed_peak_time": obs_peak_time.strftime("%Y-%m-%d %H:%M:%S"),
        "simulated_peak_time": sim_peak_time.strftime("%Y-%m-%d %H:%M:%S"),
        "peak_time_error_hours": float((sim_peak_time - obs_peak_time).total_seconds() / 3600.0),
    }


def _compare整理(result_path: Path, flood_code: str, start: datetime, end: datetime, observed: pd.DataFrame, output: Path) -> dict | None:
    files = sorted(result_path.rglob(f"*{flood_code}*整理*.xlsx")) if result_path.is_dir() else []
    if not files:
        return None
    frame = pd.read_excel(files[0])
    if len(frame.columns) < 4:
        return None
    frame = frame.iloc[:, :4].copy()
    frame.columns = ["time", "rainfall", "observed", "simulated"]
    frame["time"] = pd.to_datetime(frame["time"])
    frame["observed"] = pd.to_numeric(frame["observed"], errors="coerce")
    frame["simulated"] = pd.to_numeric(frame["simulated"], errors="coerce")
    frame = frame[frame["time"].between(start, end)]
    legacy = frame.set_index("time")[["observed", "simulated"]].rename(columns={"observed": "observed_flow_m3_s", "simulated": "simulated_flow_m3_s"})
    common = observed.join(legacy[["simulated_flow_m3_s"]], how="inner").dropna()
    payload = {"source_file": str(files[0]), "metrics": _metrics_from_pair(common)}
    write_json(output, payload)
    return payload


def run(args: argparse.Namespace) -> dict:
    # [RUN-ENTRY] Resolve source, event, and output before any model call.
    mdb = Path(args.mdb).resolve()
    if not mdb.exists():
        raise FileNotFoundError(mdb)
    event_start, event_end = _read_event(mdb, args.flood_code)
    warmup_start = event_start - timedelta(days=args.warmup_days)
    out = Path(args.output_dir).resolve() if args.output_dir else MODEL_ROOT / "validation" / "mdb_runs" / str(args.flood_code)
    out.mkdir(parents=True, exist_ok=True)
    parameter_path = Path(args.parameter_set).resolve() if args.parameter_set else MODEL_ROOT / "data_interface" / "runoff_2017" / "xaj_parameter_set.json"
    params, overrides = _load_land_params(parameter_path)
    stations = _read_rainfall(mdb, warmup_start, event_end)
    stations.to_csv(out / "mdb_rainfall_station.csv", encoding="utf-8")
    observed = _read_observed_flow(mdb, event_start, event_end, out / "mdb_observed_flow.csv")
    cells, _, _ = _grid_cells()
    forcing = _grid_forcing(stations, cells, out / "mdb_grid_forcing.csv")
    runoff = _run_grid_xaj(forcing, cells, params, str(args.flood_code))
    _write_runoff_wide(runoff, out / "mdb_runoff_components_wide.csv")

    # [ROUTING-01] Reuse the checked topology and area-overlap map; only the
    # forcing is replaced by the current MDB run.
    spatial = MODEL_ROOT / "data_interface" / "spatial"
    base_validation = MODEL_ROOT / "validation" / "integrated_2017_control_split_v2"
    mapping = base_validation / "grid_to_unit_weight.csv"
    topology = spatial / "nan_an_topology_control_split_v2.json"
    routing_params = MODEL_ROOT / "config" / "routing_parameters_control_split_v2.json"
    control_points = spatial / "xufan_70115800_control_point_snapped_v1.json"
    from integration.runoff_adapter import aggregate_runoff_to_reach_segments
    routing_input = out / "mdb_routing_input.csv"
    aggregate_runoff_to_reach_segments(out / "mdb_runoff_components_wide.csv", mapping, topology, routing_input)
    engine = RoutingEngine(read_topology_json(topology), read_parameters_json(routing_params), read_state_json(None), read_control_points_json(control_points))
    routed = engine.run(__import__("xaj_routing.io", fromlist=["read_runoff_csv"]).read_runoff_csv(routing_input))
    run_id = f"mdb_{args.flood_code}"
    write_component_flows_csv(out / "component_flow.csv", routed.component_flows, run_id)
    write_reach_flows_csv(out / "reach_flow.csv", routed.reach_flows, run_id)
    write_outlet_flows_csv(out / "outlet_flow.csv", routed.outlet_flows, run_id)
    write_control_flows_csv(out / "control_flow.csv", routed.control_flows, run_id)
    write_json(out / "final_routing_state.json", routed.final_state.to_mapping())

    # [EVAL-01] Evaluate only the declared flood window, not the warm-up.
    control = pd.read_csv(out / "control_flow.csv")
    control["time"] = pd.to_datetime(control["time"])
    control = control[control["time"].between(event_start, event_end)].set_index("time")
    control = control.rename(columns={"outflow_cms": "simulated_flow_m3_s"})[["simulated_flow_m3_s"]]
    paired = observed.join(control, how="inner")
    metrics = _metrics_from_pair(paired)
    legacy_comparison = _compare整理(
        Path(args.compare_dir) if args.compare_dir else Path(__file__).resolve().parents[4] / "整理结果",
        str(args.flood_code),
        event_start,
        event_end,
        observed,
        out / "comparison_整理结果.json",
    )
    try:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(10, 4.8))
        plt.plot(paired.index, paired["observed_flow_m3_s"], label="MDB observed", linewidth=2)
        plt.plot(paired.index, paired["simulated_flow_m3_s"], label="MDB direct run", linewidth=1.8)
        if legacy_comparison:
            legacy_file = Path(legacy_comparison["source_file"])
            legacy_frame = pd.read_excel(legacy_file).iloc[:, :4]
            legacy_frame["time"] = pd.to_datetime(legacy_frame.iloc[:, 0])
            legacy_frame = legacy_frame[legacy_frame["time"].isin(paired.index)]
            plt.plot(legacy_frame["time"], pd.to_numeric(legacy_frame.iloc[:, 3], errors="coerce"), label="整理结果旧过程", linewidth=1.4, linestyle="--")
        plt.ylabel("Flow (m3/s)")
        plt.xlabel("Time")
        plt.title(f"Flood {args.flood_code}: MDB direct run")
        plt.grid(alpha=0.25)
        plt.legend()
        plt.tight_layout()
        plt.savefig(out / "mdb_hydrograph.png", dpi=160)
        plt.close()
    except Exception as exc:
        legacy_comparison = {**(legacy_comparison or {}), "plot_warning": str(exc)}
    report = {
        "status": "MDB_DIRECT_RUN_COMPLETED",
        "source": {"mdb": str(mdb), "rain_table": "ReFactorDT", "flow_table": "st_river_r", "control_station": "CGB087012", "flood_code": str(args.flood_code)},
        "window": {"warmup_start": warmup_start.isoformat(sep=" "), "event_start": event_start.isoformat(sep=" "), "event_end": event_end.isoformat(sep=" ")},
        "grid": {"count": len(cells), "method": "UTM50N IDW power=2, 1 km effective cells"},
        "forcing": {"temperature_c": 15.0, "pet_mm_h": 0.08, "assumption": "MDB has no temperature/PET series; values are explicit temporary assumptions"},
        "parameters": {"file": str(parameter_path), "values": overrides},
        "metrics": metrics,
        "comparison_with_整理结果": legacy_comparison,
        "outputs": {"directory": str(out), "runoff": str(out / "mdb_runoff_components_wide.csv"), "control_flow": str(out / "control_flow.csv")},
    }
    write_json(out / "mdb_run_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _metrics_from_pair(pair: pd.DataFrame) -> dict:
    """Metrics adapter for a paired observed/simulated frame."""
    observed = pair[["observed_flow_m3_s"]]
    simulated = pair[["simulated_flow_m3_s"]]
    return _metrics(observed, simulated)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run XAJ-routing directly from xufan MDB")
    parser.add_argument("--mdb", required=True)
    parser.add_argument("--flood-code", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--parameter-set")
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--compare-dir", help="directory containing same-event 整理结果 xlsx")
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
