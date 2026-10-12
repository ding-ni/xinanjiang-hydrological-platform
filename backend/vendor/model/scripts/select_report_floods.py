"""Select and trace the 30 flood events from the platform event catalog."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


REPORT_VALUES = {
    "31220317": (159.1, 149.7, 167.6, 56.0, 44.0, -1.0, 0.864, False),
    "31210811": (226.3, 183.9, 171.7, 78.0, 54.0, 24.0, 0.831, False),
    "31210627": (148.6, 124.4, 109.5, 95.0, 121.0, -2.0, 0.819, False),
    "31210616": (150.0, 123.5, 132.7, 97.0, 95.0, -1.0, 0.898, True),
    "31210523": (36.4, 48.7, 42.5, 23.0, 24.0, -74.0, 0.472, True),
    "31200701": (319.1, 278.3, 268.5, 143.0, 113.0, -1.0, 0.907, False),
    "31200123": (107.9, 75.3, 63.4, 22.0, 26.0, 0.0, 1.0, True),
    "31190809": (130.1, 64.8, 68.2, 57.0, 52.0, 0.0, 0.987, True),
    "31190709": (137.1, 120.5, 139.3, 108.0, 79.0, -1.0, 0.603, False),
    "31190618": (184.0, 123.7, 111.1, 108.0, 145.0, 1.0, 0.833, False),
    "31190210": (171.3, 158.3, 159.6, 26.0, 21.0, -111.0, 1.0, True),
    "31180629": (133.6, 88.5, 73.6, 32.0, 38.0, -1.0, 0.395, True),
    "31180620": (78.5, 46.2, 41.8, 44.0, 44.0, -1.0, 0.764, True),
    "31180423": (47.0, 41.1, 36.3, 44.0, 52.0, 0.0, 0.759, True),
    "31170619": (190.3, 188.6, 172.0, 225.0, 173.0, 0.0, 0.722, False),
    "31170609": (146.0, 105.4, 100.9, 54.0, 49.0, 3.0, 0.898, True),
    "31170319": (57.0, 78.2, 64.4, 39.0, 38.0, -1.0, 1.0, True),
    "31160928": (125.0, 59.5, 63.2, 51.0, 43.0, 1.0, 0.805, True),
    "31160624": (127.5, 80.8, 70.1, 43.0, 40.0, 0.0, 0.852, True),
    "31160526": (189.3, 166.9, 160.4, 86.0, 96.0, -1.0, 0.865, True),
    "31150820": (82.6, 43.1, 34.5, 23.0, 27.0, 10.0, 0.672, True),
    "31150717": (96.5, 41.2, 34.7, 76.0, 67.0, -1.0, 0.915, True),
    "31150621": (73.6, 39.5, 32.3, 33.0, 40.0, 2.0, 0.802, True),
    "31150607": (165.0, 101.5, 95.1, 76.0, 61.0, 1.0, 0.834, True),
    "31140712": (103.6, 78.6, 70.9, 53.0, 59.0, 0.0, 0.884, True),
    "31140513": (93.6, 86.1, 71.0, 63.0, 54.0, -1.0, 0.810, False),
    "31131007": (91.3, 49.9, 48.5, 42.0, 37.0, 1.0, 0.958, True),
    "31130624": (147.3, 108.2, 122.2, 74.0, 54.0, 3.0, 0.777, True),
    "31130606": (145.5, 104.8, 99.0, 137.0, 128.0, -1.0, 0.862, True),
    "31130430": (41.5, 32.2, 28.0, 47.0, 56.0, -1.0, 0.916, True),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    catalog_path = Path(args.catalog).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    by_code = {str(item.get("floodCode")): item for item in payload.get("packages", [])}
    rows = []
    selected = []
    missing = []
    for code, values in REPORT_VALUES.items():
        item = by_code.get(code)
        if item is None:
            missing.append(code)
            continue
        selected.append(item)
        rain, obs_depth, old_depth, obs_peak, old_peak, old_lag, old_nse, old_ok = values
        observed = item.get("observedInputs", {})
        rows.append({
            "report_flood_code": code,
            "event_id": item.get("eventId"),
            "year": item.get("year", item.get("start", "")[:4]),
            "start": item.get("start"),
            "end": item.get("end"),
            "catalog_status": item.get("status"),
            "catalog_peak_m3_s": observed.get("observedPeakFlowM3s"),
            "catalog_peak_time": observed.get("observedPeakTime"),
            "catalog_rain_mm": observed.get("gridBasinAverageTotalRainMm"),
            "report_rain_mm": rain,
            "report_observed_runoff_depth_mm": obs_depth,
            "report_calculated_runoff_depth_mm": old_depth,
            "report_observed_peak_m3_s": obs_peak,
            "report_calculated_peak_m3_s": old_peak,
            "report_peak_time_error_periods": old_lag,
            "report_nse": old_nse,
            "report_qualified": old_ok,
            "source_catalog": str(catalog_path),
            "source_references": ";".join(str(x) for x in item.get("references", {}).get("rain", []) + item.get("references", {}).get("river", [])),
        })
    selected_payload = {**payload, "packages": selected, "selectedFor": "report_30_flood_joint_calibration", "sourceCatalog": str(catalog_path)}
    (output / "selected_30_events.json").write_text(json.dumps(selected_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "selected_30_events.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["report_flood_code"])
        writer.writeheader(); writer.writerows(rows)
    trace = [
        "# 30场报告洪水来源追踪",
        "",
        f"- 原始事件索引：`{catalog_path}`",
        f"- 报告洪号：{len(REPORT_VALUES)} 场",
        f"- 精确匹配：{len(selected)} 场",
        f"- 缺失：{len(missing)} 场" + (f"（{', '.join(missing)}）" if missing else ""),
        "- 当前完整模型输入以事件索引、1 km降雨 NetCDF 和徐畈实测流量工作簿为准。",
        "- 报告中的旧计算值仅作为对照列，不作为本次率定输入。",
        "- `xufan.mdb.fl_code` 是另一套 25 场在线/试运行目录，不能替代这 30 场历史事件索引。",
    ]
    (output / "source_trace.md").write_text("\n".join(trace) + "\n", encoding="utf-8")
    if missing:
        raise SystemExit(f"missing flood codes: {', '.join(missing)}")
    print(f"selected {len(selected)} events -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
