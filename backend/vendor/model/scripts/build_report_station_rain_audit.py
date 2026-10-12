"""Audit report-event rainfall against the packaged station NetCDF product."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--station-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    events = pd.read_csv(args.events, encoding="utf-8-sig")
    datasets: dict[int, xr.Dataset] = {}
    rows: list[dict[str, object]] = []
    try:
        for _, event in events.iterrows():
            year = int(event["year"])
            if year not in datasets:
                path = args.station_dir / f"nananxi_station_precip_hourly_{year}.nc"
                if not path.exists():
                    raise FileNotFoundError(path)
                datasets[year] = xr.open_dataset(path, engine="netcdf4")
            dataset = datasets[year]
            times = pd.DatetimeIndex(dataset["time"].values)
            mask = (times >= pd.Timestamp(event["start"])) & (times <= pd.Timestamp(event["end"]))
            values = np.asarray(dataset["precipitation"].values, dtype=float)[mask, :]
            station_totals = np.nansum(values, axis=0)
            mean_total = float(np.nanmean(values, axis=1).sum())
            report_total = float(event["report_rain_mm"])
            rows.append(
                {
                    "flood_code": str(event["report_flood_code"]),
                    "year": year,
                    "start": event["start"],
                    "end": event["end"],
                    "hours": int(mask.sum()),
                    "station_count": int(dataset.sizes["station"]),
                    "report_rain_mm": report_total,
                    "station_mean_rain_mm": mean_total,
                    "station_max_rain_mm": float(np.nanmax(station_totals)),
                    "station_min_rain_mm": float(np.nanmin(station_totals)),
                    "station_minus_report_mm": mean_total - report_total,
                    "station_minus_report_percent": (mean_total / report_total - 1.0) * 100.0 if report_total else np.nan,
                }
            )
    finally:
        for dataset in datasets.values():
            dataset.close()

    audit = pd.DataFrame(rows)
    audit.to_csv(args.output_dir / "report_station_rain_audit.csv", index=False, encoding="utf-8-sig")
    mean_report = float(audit["report_rain_mm"].mean())
    mean_station = float(audit["station_mean_rain_mm"].mean())
    mean_delta = float(audit["station_minus_report_mm"].mean())
    mean_abs_delta = float(audit["station_minus_report_mm"].abs().mean())
    count_lower = int((audit["station_minus_report_mm"] < 0).sum())
    md = [
        "# 报告洪水站点降雨口径核查",
        "",
        "## 结论",
        "",
        "- 站点产品来自平台整理的 `xufan.mdb`，变量为 `precipitation(time, station)`。",
        "- 当前可读到的站点数为 11，不是技术报告文字中提到的 12 站；因此只能作为站点降雨诊断产品。",
        f"- 30 场报告雨量平均值：`{mean_report:.3f} mm`。",
        f"- 11 站逐小时算术平均后的平均场次雨量：`{mean_station:.3f} mm`。",
        f"- 站点产品相对报告平均少：`{mean_delta:.3f} mm`，平均绝对差：`{mean_abs_delta:.3f} mm`。",
        f"- 30 场中站点产品低于报告雨量：`{count_lower}/30` 场。",
        "",
        "## 解释",
        "",
        "报告雨量可能使用了 12 站、不同站点权重、不同洪水起止时段，或报告中的流域平均雨量字段并非简单 11 站算术平均。该结果不能直接作为报告原始降雨复现。",
        "",
        "当前模型使用 1 km 网格降雨场；下一步应分别建立：",
        "",
        "1. 当前 1 km 降雨场基线。",
        "2. 11 站逐小时降雨的空间分配诊断输入。",
        "3. 仅按报告场次总雨量校准 1 km 场的诊断输入。",
        "",
        "## 文件",
        "",
        "- 逐场结果：`report_station_rain_audit.csv`",
        "- 本脚本不会覆盖原始 NC，也不会伪造缺失的第 12 个站。",
    ]
    (args.output_dir / "report_station_rain_audit.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"audited {len(audit)} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
