# -*- coding: utf-8 -*-
"""Build a reproducible comparison between report XAJ variants and current XAJ."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from docx import Document


REPORT_CODES = [
    "31220317", "31210811", "31210627", "31210616", "31210523", "31200701", "31200123",
    "31190809", "31190709", "31190618", "31190210", "31180629", "31180620", "31180423",
    "31170619", "31170609", "31170319", "31160928", "31160624", "31160526", "31150820",
    "31150717", "31150621", "31150607", "31140712", "31140513", "31131007", "31130624",
    "31130606", "31130430",
]


def table_to_rows(table):
    return [[" ".join(cell.text.split()) for cell in row.cells] for row in table.rows]


def read_xaj_table(document, table_index):
    rows = table_to_rows(document.tables[table_index])
    result = {}
    for row in rows[2:]:
        code = row[0]
        if code not in REPORT_CODES:
            continue
        result[code] = {
            "report_rainfall_mm": float(row[1]),
            "report_observed_runoff_depth_mm": float(row[2]),
            "report_calculated_runoff_depth_mm": float(row[3]),
            "report_observed_peak_m3_s": float(row[4]),
            "report_calculated_peak_m3_s": float(row[5]),
            "report_peak_time_error_periods": float(row[6]),
            "report_nse": float(row[7]),
            "report_qualified": row[8] == "√",
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--current-metrics", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve(); output.mkdir(parents=True, exist_ok=True)
    document = Document(args.report)
    baseline = read_xaj_table(document, 14)
    stratified = read_xaj_table(document, 16)
    with Path(args.current_metrics).open(encoding="utf-8-sig", newline="") as handle:
        current = {}
        for row in csv.DictReader(handle):
            # Formal event metrics use xf-<flood_code>; older exports used flood_code.
            code = row.get("flood_code") or row.get("event_id", "").removeprefix("xf-")
            if code:
                current[code] = row
    rows = []
    for code in REPORT_CODES:
        row = {"flood_code": code}
        row.update({f"baseline_{k.removeprefix('report_')}": v for k, v in baseline[code].items()})
        row.update({f"stratified_{k.removeprefix('report_')}": v for k, v in stratified[code].items()})
        now = current.get(code, {})
        for key in ("rainfall_mm", "observed_runoff_depth_mm", "simulated_runoff_depth_mm", "observed_peak_m3_s", "simulated_peak_m3_s", "peak_error_percent", "peak_time_error_hours", "volume_error_percent", "NSE", "KGE", "qualified"):
            row[f"current_{key}"] = now.get(key)
        rows.append(row)
    fields = list(rows[0])
    with (output / "report_vs_current_30_event_comparison.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)

    def mean(key, data):
        values = [float(r[key]) for r in data if r.get(key) not in (None, "")]
        return sum(values) / len(values) if values else None

    def mean_abs(key):
        values = [abs(float(r[key])) for r in rows if r.get(key) not in (None, "")]
        return sum(values) / len(values) if values else None

    def median_abs(key):
        values = sorted(abs(float(r[key])) for r in rows if r.get(key) not in (None, ""))
        if not values:
            return None
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2

    summary = {
        # The report explicitly states 0.786 for the baseline table average;
        # retain that published value instead of recomputing it from rounded
        # row values, whose rounding/weighting convention is not documented.
        "baseline_xaj": {"event_count": 30, "mean_nse": 0.786, "mean_nse_recomputed_from_rows": mean("baseline_nse", rows), "qualified_count": sum(r["baseline_qualified"] is True for r in rows)},
        "stratified_xaj": {"event_count": 30, "mean_nse": mean("stratified_nse", rows), "qualified_count": sum(r["stratified_qualified"] is True for r in rows)},
        "current_joint_xaj_routing": {"event_count": 30, "mean_nse": mean("current_NSE", rows), "mean_kge": mean("current_KGE", rows), "mean_abs_peak_error_percent": mean_abs("current_peak_error_percent"), "mean_abs_volume_error_percent": mean_abs("current_volume_error_percent"), "median_abs_peak_time_error_hours": median_abs("current_peak_time_error_hours"), "qualified_count": sum(str(r["current_qualified"]).lower() == "true" for r in rows)},
        "deltas": {"nse_vs_baseline": mean("current_NSE", rows) - 0.786, "nse_vs_stratified": mean("current_NSE", rows) - mean("stratified_nse", rows), "qualified_vs_baseline": sum(str(r["current_qualified"]).lower() == "true" for r in rows) - 22, "qualified_vs_stratified": sum(str(r["current_qualified"]).lower() == "true" for r in rows) - 26},
        "report_swan": {"quantitative_per_event_values_available": False, "comparison_period": "2022-03-21 to 2024-07-02", "valid_points_after_qc": 796, "reported_raw_points": 803, "reported_negative_swan_values_removed": 7, "illustrated_events": ["220413", "230721", "240620", "220321", "240701", "240623"]},
    }
    (output / "report_model_comparison_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report_model_comparison.md").write_text(build_md(summary, output), encoding="utf-8")
    (output / "report_model_comparison.html").write_text(build_html(rows, summary), encoding="utf-8")
    print(f"built comparison for {len(rows)} floods")
    return 0


def build_md(summary, output):
    b = summary["baseline_xaj"]; s = summary["stratified_xaj"]; c = summary["current_joint_xaj_routing"]; w = summary["report_swan"]
    return f'''# 技术报告模型与当前模型对比

## 模型定义

| 模型 | 含义 | 数据来源 |
|---|---|---|
| 报告基准新安江 | 报告表5.4-2，传统三水源新安江基准参数 | 技术报告表5.4-1、表5.4-2 |
| 报告分层新安江 | 报告表5.4-4，按洪水等级调整参数的新安江方案 | 技术报告表5.4-3、表5.4-4 |
| 当前完整模型 | 当前 XAJ 产流 + 1 km到河段集水区映射 + 坡面线性水库 + 河段 Muskingum | 当前平台正式联合率定结果 |
| 报告 SWAN | 报告中的数据驱动 SWAN 方案 | 技术报告第5.4.2节 |

## 30场可量化对比

| 模型 | 场次 | 平均NSE/确定性系数 | 合格场次 |
|---|---:|---:|---:|
| 报告基准新安江 | {b['event_count']} | {b['mean_nse']:.3f} | {b['qualified_count']}/30 |
| 报告分层新安江 | {s['event_count']} | {s['mean_nse']:.3f} | {s['qualified_count']}/30 |
| 当前完整模型 | {c['event_count']} | {c['mean_nse']:.3f}（NSE） | {c['qualified_count']}/30 |

当前模型补充指标：平均 KGE `{c['mean_kge']:.3f}`；平均绝对洪峰误差 `{c['mean_abs_peak_error_percent']:.2f}%`；平均绝对洪量误差 `{c['mean_abs_volume_error_percent']:.2f}%`；峰现时差绝对值中位数 `{c['median_abs_peak_time_error_hours']:.1f}` 小时。逐场对比见 `report_vs_current_30_event_comparison.csv`。

## 相对报告结果

- 相对报告基准新安江：平均 NSE `{summary['deltas']['nse_vs_baseline']:+.3f}`，合格场次 `{summary['deltas']['qualified_vs_baseline']:+d}`。
- 相对报告分层新安江：平均 NSE `{summary['deltas']['nse_vs_stratified']:+.3f}`，合格场次 `{summary['deltas']['qualified_vs_stratified']:+d}`。
- 在这 30 场且当前模型同样参与率定的口径下，报告分层新安江的平均 NSE 最高；当前模型的合格场次与分层模型相同，但平均 NSE 低约 `{abs(summary['deltas']['nse_vs_stratified']):.3f}`。

## SWAN资料边界

报告第5.4.2节对 SWAN 和新安江使用了 `{w['comparison_period']}` 的连续流量资料：原始 803 个时段，剔除 SWAN 负值 7 个，最终 796 个时段。报告展示了 `220413、230721、240620、220321、240701、240623` 六场过程线，但没有提供 SWAN 逐场 CSV、逐场洪峰/洪量/NSE 数值表。因此本项目没有把图中曲线人工反 digitize，也没有伪造 SWAN 逐场指标。

报告对 SWAN 的定性结论是：过程线拟合、基流、起涨、洪峰和退水表现优于报告新安江；该结论可以作为报告证据，但不能与当前模型形成严格数值排名，直到取得 SWAN 原始输出。

## 口径限制

1. 报告基准和分层新安江的 30 场表格来自同一报告，当前模型使用当前 1 km降雨、30 m DEM河段集水区映射和当前控制断面汇流实现。
2. 当前 30 场全部用于联合率定，没有独立验证集；其数值不能当作泛化能力证明，也不构成对报告模型的公平泛化能力排名。
3. 当前模型径流深采用控制面积 64.1 km²和小时流量积分；报告表格的面积和时段边界需要最终确认。
4. 报告中“确定性系数”按当前项目对照为 NSE，但仍建议负责人确认旧程序公式。
5. 当前正式报告标记 `REVIEW_REQUIRED`：控制断面记录、长时间单调流量段质量标识、全零降雨月含义及上游水库调度影响仍需核查；PET 使用月气候诊断假设，尚未达到生产审批条件。
6. 报告 SWAN 是连续期测试，而当前模型目前按洪水事件窗口计算，两者不能直接按连续期指标比较。

## 文件

- 逐场对比：`{output / 'report_vs_current_30_event_comparison.csv'}`
- 汇总 JSON：`{output / 'report_model_comparison_summary.json'}`
- 交互网页：`{output / 'report_model_comparison.html'}`
'''


def build_html(rows, summary):
    payload = json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False, separators=(",", ":"))
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三模型对比</title><style>body{font:14px Arial,"Microsoft YaHei";margin:0;background:#f4f7f8;color:#17232d}.wrap{max-width:1400px;margin:auto;padding:24px}.head{border-bottom:1px solid #ccd8dc;padding-bottom:16px}.head h1{margin:4px 0}.muted{color:#64757d}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}.card{background:#fff;border:1px solid #d8e1e4;padding:14px}.card b{display:block;font-size:24px}.panel{background:#fff;border:1px solid #d8e1e4;padding:16px;margin-top:16px}.table{border-collapse:collapse;width:100%;font-size:12px}.table th,.table td{border-bottom:1px solid #e9eef0;padding:7px;text-align:right;white-space:nowrap}.table th{background:#edf4f5;position:sticky;top:0}.table th:first-child,.table td:first-child{text-align:left}.scroll{max-height:600px;overflow:auto}.chart{width:100%;height:280px}.legend{color:#64757d}.sw{display:inline-block;width:10px;height:10px;margin-right:3px}.b{background:#7b8790}.s{background:#2b9272}.c{background:#d66b35}.w{background:#8e63a9}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.wrap{padding:12px}}</style></head><body><main class="wrap"><div class="head"><div class="muted">TECHNICAL REPORT · XAJ · SWAN</div><h1>报告模型与当前完整产汇流模型对比</h1><div class="muted">30场报告洪水逐场对照；SWAN保留报告定性证据和数据边界。</div></div><div id="cards" class="cards"></div><section class="panel"><h2>平均确定性系数 / NSE 与合格场次</h2><svg id="summary" class="chart" viewBox="0 0 800 280"></svg><div class="legend"><span><i class="sw b"></i>报告基准新安江</span> <span><i class="sw s"></i>报告分层新安江</span> <span><i class="sw c"></i>当前完整模型</span> <span><i class="sw w"></i>报告SWAN：无逐场数值表</span></div></section><section class="panel"><h2>30场逐场对比</h2><div class="scroll"><table class="table"><thead><tr><th>洪号</th><th>报告基准NSE</th><th>报告分层NSE</th><th>当前NSE</th><th>当前KGE</th><th>基准合格</th><th>分层合格</th><th>当前合格</th><th>当前洪峰误差%</th><th>当前峰时差h</th></tr></thead><tbody id="body"></tbody></table></div></section><section class="panel"><h2>SWAN资料状态</h2><p>报告第5.4.2节使用2022-03-21至2024-07-02连续资料，803个原始时段，剔除7个SWAN负值后保留796个时段。报告只提供6场过程线图和定性结论，没有逐场数值表，因此本页不人工推算SWAN数值。</p></section></main><script>const D=__DATA__,S=D.summary;const f=(x,n=3)=>x==null?'—':Number(x).toFixed(n);const models=[['报告基准新安江','baseline_xaj','#7b8790'],['报告分层新安江','stratified_xaj','#2b9272'],['当前完整模型','current_joint_xaj_routing','#d66b35']];document.querySelector('#cards').innerHTML=models.map(([n,k])=>`<div class="card"><b>${f(S[k].mean_nse)}</b><span>${n}平均NSE</span><br><strong>${S[k].qualified_count}/30</strong><span>合格场次</span></div>`).join('')+`<div class="card"><b>796</b><span>报告SWAN有效时段</span><br><strong>6</strong><span>报告过程线场次</span></div>`;document.querySelector('#body').innerHTML=D.rows.map(r=>`<tr><td>${r.flood_code}</td><td>${f(r.baseline_nse)}</td><td>${f(r.stratified_nse)}</td><td>${f(r.current_NSE)}</td><td>${f(r.current_KGE)}</td><td>${r.baseline_qualified?'√':'×'}</td><td>${r.stratified_qualified?'√':'×'}</td><td>${String(r.current_qualified).toLowerCase()==='true'?'√':'×'}</td><td>${f(r.current_peak_error_percent,1)}</td><td>${f(r.current_peak_time_error_hours,1)}</td></tr>`).join('');const svg=document.querySelector('#summary'),base=[S.baseline_xaj.mean_nse,S.stratified_xaj.mean_nse,S.current_joint_xaj_routing.mean_nse],labels=['基准XAJ','分层XAJ','当前模型'],colors=['#7b8790','#2b9272','#d66b35'];const W=800,H=280,bar=110,gap=80,max=1;svg.innerHTML=base.map((v,i)=>{let x=90+i*(bar+gap),h=v*190;return `<rect x="${x}" y="${220-h}" width="${bar}" height="${h}" fill="${colors[i]}"/><text x="${x+bar/2}" y="${240}" text-anchor="middle" font-size="13">${labels[i]}</text><text x="${x+bar/2}" y="${210-h}" text-anchor="middle" font-size="15">${v.toFixed(3)}</text>`}).join('')+`<line x1="50" y1="220" x2="750" y2="220" stroke="#9daeb4"/>`; </script></body></html>'''.replace('__DATA__', payload)


if __name__ == "__main__":
    raise SystemExit(main())
