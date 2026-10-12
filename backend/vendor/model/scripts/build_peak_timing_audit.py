"""Audit rainfall, observed-flow and simulated-flow peak timing."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--grid-static", required=True, type=Path)
    parser.add_argument("--precip-dir", required=True, type=Path)
    parser.add_argument("--hydrograph-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    events = pd.read_csv(args.events, encoding="utf-8-sig")
    grids = pd.read_csv(args.grid_static)
    years: dict[int, xr.Dataset] = {}
    rows = []
    try:
        for _, event in events.iterrows():
            year = int(event["year"])
            if year not in years:
                years[year] = xr.open_dataset(args.precip_dir / f"nananxi_precip_hourly_1km_{year}.nc", engine="netcdf4")
            ds = years[year]
            times = pd.DatetimeIndex(ds["time"].values)
            mask = (times >= pd.Timestamp(event["start"])) & (times <= pd.Timestamp(event["end"]))
            values = np.asarray(ds["precipitation"].values, dtype=float)[mask]
            grid_values = np.stack([values[:, int(r.array_row), int(r.array_col)] for r in grids.itertuples()], axis=1)
            weights = grids["area_km2"].to_numpy(dtype=float)
            rain = np.average(grid_values, axis=1, weights=weights)
            rain_times = times[mask]
            rain_peak = rain_times[int(np.nanargmax(rain))]
            hydro_path = args.hydrograph_dir / f"xf-{event['report_flood_code']}.csv"
            hydro = pd.read_csv(hydro_path, parse_dates=["time"])
            observed_peak = hydro.loc[hydro["observed_flow_m3_s"].idxmax(), "time"]
            simulated_peak = hydro.loc[hydro["simulated_flow_m3_s"].idxmax(), "time"]
            rows.append({
                "flood_code": str(event["report_flood_code"]),
                "rain_peak_time": rain_peak.isoformat(),
                "observed_peak_time": pd.Timestamp(observed_peak).isoformat(),
                "simulated_peak_time": pd.Timestamp(simulated_peak).isoformat(),
                "rain_to_observed_peak_hours": (pd.Timestamp(observed_peak) - rain_peak).total_seconds() / 3600.0,
                "rain_to_simulated_peak_hours": (pd.Timestamp(simulated_peak) - rain_peak).total_seconds() / 3600.0,
                "simulated_minus_observed_peak_hours": (pd.Timestamp(simulated_peak) - pd.Timestamp(observed_peak)).total_seconds() / 3600.0,
            })
    finally:
        for ds in years.values():
            ds.close()
    frame = pd.DataFrame(rows)
    frame.to_csv(args.output_dir / "peak_timing_audit.csv", index=False, encoding="utf-8-sig")
    md = [
        "# 雨峰与流量峰时序核查", "",
        f"- 场次数：`{len(frame)}`。",
        f"- 模拟峰相对实测峰平均偏移：`{frame.simulated_minus_observed_peak_hours.mean():.2f} h`。",
        f"- 模拟峰相对实测峰中位偏移：`{frame.simulated_minus_observed_peak_hours.median():.2f} h`。",
        f"- 模拟峰提前场次：`{int((frame.simulated_minus_observed_peak_hours < 0).sum())}`。",
        f"- 模拟峰滞后场次：`{int((frame.simulated_minus_observed_peak_hours > 0).sum())}`。",
        "",
        "雨峰采用当前 1 km 网格、按网格面积加权的流域平均降雨峰值；它用于诊断时序，不等同于报告的 12 站雨峰。",
        "若雨峰与实测流量峰已经错位，而模拟峰与实测峰的偏移方向不稳定，优先查降雨空间/时间输入；若模拟峰相对实测峰稳定滞后，则优先查坡面水库和河道 Muskingum 参数。",
        "",
        "逐场结果见 `peak_timing_audit.csv`。",
    ]
    (args.output_dir / "peak_timing_audit.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"audited peak timing for {len(frame)} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
