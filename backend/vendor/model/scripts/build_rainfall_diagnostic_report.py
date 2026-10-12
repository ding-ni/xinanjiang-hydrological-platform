"""Build a small HTML/Markdown diagnostic for report versus current rainfall."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--current-metrics", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audit = pd.read_csv(args.audit, encoding="utf-8-sig")
    current = pd.read_csv(args.current_metrics, encoding="utf-8-sig")
    current["flood_code"] = current["flood_code"].astype(str)
    audit["flood_code"] = audit["flood_code"].astype(str)
    frame = audit.merge(current[["flood_code", "rainfall_mm", "NSE", "KGE", "peak_error_percent"]], on="flood_code", how="left")
    frame["current_minus_report_mm"] = frame["rainfall_mm"] - frame["report_rain_mm"]
    frame["station_abs_error_mm"] = frame["station_minus_report_mm"].abs()
    frame["current_abs_error_mm"] = frame["current_minus_report_mm"].abs()
    frame.to_csv(args.output_dir / "rainfall_diagnostic_events.csv", index=False, encoding="utf-8-sig")
    summary = {
        "event_count": int(len(frame)),
        "report_mean_mm": float(frame.report_rain_mm.mean()),
        "current_mean_mm": float(frame.rainfall_mm.mean()),
        "station_mean_mm": float(frame.station_mean_rain_mm.mean()),
        "current_signed_difference_mm": float(frame.current_minus_report_mm.mean()),
        "station_signed_difference_mm": float(frame.station_minus_report_mm.mean()),
        "current_mean_absolute_difference_mm": float(frame.current_abs_error_mm.mean()),
        "station_mean_absolute_difference_mm": float(frame.station_abs_error_mm.mean()),
        "current_closer_count": int((frame.current_abs_error_mm < frame.station_abs_error_mm).sum()),
        "station_closer_count": int((frame.station_abs_error_mm < frame.current_abs_error_mm).sum()),
        "station_count_min": int(frame.station_count.min()),
        "station_count_max": int(frame.station_count.max()),
    }
    (args.output_dir / "rainfall_diagnostic_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md = f'''# 报告模型降雨输入诊断

## 核心结果

| 指标 | 结果 |
|---|---:|
| 场次数 | {summary["event_count"]} |
| 报告雨量平均 | {summary["report_mean_mm"]:.3f} mm |
| 当前1 km场平均 | {summary["current_mean_mm"]:.3f} mm |
| 11站算术平均 | {summary["station_mean_mm"]:.3f} mm |
| 当前1 km场相对报告平均差 | {summary["current_signed_difference_mm"]:.3f} mm |
| 11站产品相对报告平均差 | {summary["station_signed_difference_mm"]:.3f} mm |
| 当前1 km场平均绝对差 | {summary["current_mean_absolute_difference_mm"]:.3f} mm |
| 11站产品平均绝对差 | {summary["station_mean_absolute_difference_mm"]:.3f} mm |

## 判断

- 当前1 km降雨场在逐场总量上有 **{summary["current_closer_count"]}/30** 场比11站算术平均更接近报告雨量。
- 站点NC实际包含 **{summary["station_count_min"]}-{summary["station_count_max"]} 个站**，目前不是报告所述12站的严格复现。
- 因此当前模型效果低于报告模型，不能仅归结为“总降雨量不足”；需要继续检查降雨峰值时序、空间权重、初始状态和汇流路径。

## 产物

- `rainfall_diagnostic_events.csv`：逐场降雨与当前模型指标。
- `rainfall_diagnostic_summary.json`：汇总统计。
- `rainfall_diagnostic.html`：可视化页面。
'''
    (args.output_dir / "rainfall_diagnostic.md").write_text(md, encoding="utf-8")
    data = frame[["flood_code", "report_rain_mm", "station_mean_rain_mm", "rainfall_mm", "NSE", "KGE"]].to_dict("records")
    payload = json.dumps(data, ensure_ascii=False)
    html = '''<!doctype html><meta charset="utf-8"><title>降雨输入诊断</title><style>body{font:14px Arial,'Microsoft YaHei';margin:24px;color:#203038;background:#f5f8f8}.panel{background:#fff;border:1px solid #d5e0e0;padding:16px;margin:12px 0}table{border-collapse:collapse;width:100%}th,td{padding:7px;border-bottom:1px solid #e5eeee;text-align:right}th:first-child,td:first-child{text-align:left}svg{width:100%;height:320px}</style><h1>报告模型降雨输入诊断</h1><div class="panel"><p>逐场比较报告雨量、11站算术平均和当前1 km降雨场。当前产品与报告总量的接近程度不能证明时序和空间分配一致。</p><svg id="chart" viewBox="0 0 1000 320"></svg></div><div class="panel"><table><thead><tr><th>洪号</th><th>报告 mm</th><th>11站 mm</th><th>当前1 km mm</th><th>NSE</th><th>KGE</th></tr></thead><tbody id="rows"></tbody></table></div><script>const d=__DATA__;const max=Math.max(...d.flatMap(x=>[x.report_rain_mm,x.station_mean_rain_mm,x.rainfall_mm]));const s=document.querySelector('#chart');const w=28,g=9,base=280; s.innerHTML=d.map((x,i)=>{const xx=35+i*(3*w+g),a=(v)=>base-v/max*230;return `<rect x="${xx}" y="${a(x.report_rain_mm)}" width="${w}" height="${base-a(x.report_rain_mm)}" fill="#687780"/><rect x="${xx+w}" y="${a(x.station_mean_rain_mm)}" width="${w}" height="${base-a(x.station_mean_rain_mm)}" fill="#3d9b86"/><rect x="${xx+2*w}" y="${a(x.rainfall_mm)}" width="${w}" height="${base-a(x.rainfall_mm)}" fill="#d77b42"/>`}).join('');document.querySelector('#rows').innerHTML=d.map(x=>`<tr><td>${x.flood_code}</td><td>${(+x.report_rain_mm).toFixed(1)}</td><td>${(+x.station_mean_rain_mm).toFixed(1)}</td><td>${(+x.rainfall_mm).toFixed(1)}</td><td>${(+x.NSE).toFixed(3)}</td><td>${(+x.KGE).toFixed(3)}</td></tr>`).join('');</script>'''.replace("__DATA__", payload)
    (args.output_dir / "rainfall_diagnostic.html").write_text(html, encoding="utf-8")
    print(f"built rainfall diagnostic for {len(frame)} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
