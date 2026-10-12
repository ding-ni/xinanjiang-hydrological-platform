# -*- coding: utf-8 -*-
"""Build a traceable 30-event comparison package for the Xufan floods.

The technical report contains row-level summary metrics for 30 floods, but
only five baseline XAJ hydrograph images. This script keeps that boundary
explicit and never fabricates missing baseline time series.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import shutil
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from docx import Document
from PIL import Image


REPORT_CODES = [
    "31220317", "31210811", "31210627", "31210616", "31210523", "31200701", "31200123",
    "31190809", "31190709", "31190618", "31190210", "31180629", "31180620", "31180423",
    "31170619", "31170609", "31170319", "31160928", "31160624", "31160526", "31150820",
    "31150717", "31150621", "31150607", "31140712", "31140513", "31131007", "31130624",
    "31130606", "31130430",
]

# The report captions contain a typo for 31190618 ("3119618").
REPORT_BASELINE_IMAGES = {
    "31240619": ("image95.tiff", "31240619"),
    "31220317": ("image96.tiff", "31220317"),
    "31210627": ("image97.tiff", "31210627"),
    "31190709": ("image98.tiff", "31190709"),
    "31190618": ("image99.tiff", "31190618"),
}


def clean_cell(value: str) -> str:
    return " ".join(str(value).split())


def read_report_table(document: Document, table_index: int) -> dict[str, dict[str, object]]:
    table = document.tables[table_index]
    result: dict[str, dict[str, object]] = {}
    for row in table.rows[2:]:
        values = [clean_cell(cell.text) for cell in row.cells]
        if not values or values[0] not in REPORT_CODES:
            continue
        result[values[0]] = {
            "rainfall_mm": float(values[1]),
            "observed_runoff_depth_mm": float(values[2]),
            "calculated_runoff_depth_mm": float(values[3]),
            "observed_peak_m3_s": float(values[4]),
            "calculated_peak_m3_s": float(values[5]),
            "peak_time_error_periods": float(values[6]),
            "nse": float(values[7]),
            "qualified": values[8] == "√",
        }
    return result


def read_current_metrics(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame["flood_code"] = frame["event_id"].astype(str).str.replace("xf-", "", regex=False)
    return frame.set_index("flood_code")


def json_value(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def copy_report_baseline_images(report_path: Path, output_dir: Path) -> dict[str, str]:
    image_dir = output_dir / "report_baseline_figures"
    image_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    with zipfile.ZipFile(report_path) as archive:
        for code, (source_name, _) in REPORT_BASELINE_IMAGES.items():
            source = f"word/media/{source_name}"
            data = archive.read(source)
            target = image_dir / f"{code}.png"
            with Image.open(io.BytesIO(data)) as image:
                image.convert("RGBA").save(target, format="PNG")
            copied[code] = str(target.relative_to(output_dir)).replace("\\", "/")
    return copied


def copy_hydrographs(hydro_dir: Path, output_dir: Path) -> dict[str, dict[str, object]]:
    data_dir = output_dir / "data" / "hydrographs"
    figure_dir = output_dir / "current_model_figures"
    data_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    curves: dict[str, dict[str, object]] = {}
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    for code in REPORT_CODES:
        source = hydro_dir / f"xf-{code}.csv"
        if not source.exists():
            raise FileNotFoundError(source)
        target = data_dir / source.name
        shutil.copy2(source, target)
        frame = pd.read_csv(source)
        frame["time"] = pd.to_datetime(frame["time"])
        frame["observed_flow_m3_s"] = pd.to_numeric(frame["observed_flow_m3_s"], errors="coerce")
        frame["simulated_flow_m3_s"] = pd.to_numeric(frame["simulated_flow_m3_s"], errors="coerce")
        curves[code] = {
            "times": [x.isoformat() for x in frame["time"]],
            "observed": [json_value(x) for x in frame["observed_flow_m3_s"]],
            "simulated": [json_value(x) for x in frame["simulated_flow_m3_s"]],
            "csv": str(target.relative_to(output_dir)).replace("\\", "/"),
        }
        fig, ax = plt.subplots(figsize=(10.5, 4.2), dpi=150)
        ax.plot(frame["time"], frame["observed_flow_m3_s"], color="#126782", linewidth=1.5, label="实测流量")
        ax.plot(frame["time"], frame["simulated_flow_m3_s"], color="#d36b35", linewidth=1.5, label="当前模型模拟流量")
        ax.set_title(f"徐畈洪水 {code}：实测与当前模型模拟过程线")
        ax.set_xlabel("时间")
        ax.set_ylabel("流量 (m³/s)")
        ax.grid(True, color="#d8e2e3", linewidth=0.6)
        ax.legend(loc="upper right", frameon=False)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(figure_dir / f"{code}.png", bbox_inches="tight")
        plt.close(fig)
    return curves


def build_rows(report_baseline, report_stratified, current: pd.DataFrame, baseline_images):
    rows = []
    for code in REPORT_CODES:
        b = report_baseline[code]
        s = report_stratified[code]
        c = current.loc[code]
        current_nse = float(c["NSE"])
        baseline_nse = float(b["nse"])
        stratified_nse = float(s["nse"])
        if current_nse - baseline_nse >= 0.05:
            diagnosis = "当前模型NSE明显高于报告基准"
        elif current_nse - baseline_nse <= -0.10:
            diagnosis = "当前模型NSE明显低于报告基准"
        else:
            diagnosis = "当前模型与报告基准接近"
        row = {
            "flood_code": code,
            "report_baseline_rainfall_mm": b["rainfall_mm"],
            "report_baseline_observed_runoff_depth_mm": b["observed_runoff_depth_mm"],
            "report_baseline_calculated_runoff_depth_mm": b["calculated_runoff_depth_mm"],
            "report_baseline_observed_peak_m3_s": b["observed_peak_m3_s"],
            "report_baseline_calculated_peak_m3_s": b["calculated_peak_m3_s"],
            "report_baseline_peak_time_error_periods": b["peak_time_error_periods"],
            "report_baseline_nse": baseline_nse,
            "report_baseline_qualified": b["qualified"],
            "report_stratified_nse": stratified_nse,
            "report_stratified_qualified": s["qualified"],
            "current_rainfall_mm": float(c["rainfall_mm"]),
            "current_observed_runoff_depth_mm": float(c["observed_runoff_depth_mm"]),
            "current_simulated_runoff_depth_mm": float(c["simulated_runoff_depth_mm"]),
            "current_observed_peak_m3_s": float(c["observed_peak_m3_s"]),
            "current_simulated_peak_m3_s": float(c["simulated_peak_m3_s"]),
            "current_peak_error_percent": float(c["peak_error_percent"]),
            "current_peak_time_error_hours": float(c["peak_time_error_hours"]),
            "current_volume_error_percent": float(c["volume_error_percent"]),
            "current_NSE": current_nse,
            "current_KGE": float(c["KGE"]),
            "current_qualified_by_NSE": bool(c["qualified"]),
            "current_minus_report_baseline_NSE": current_nse - baseline_nse,
            "current_minus_report_stratified_NSE": current_nse - stratified_nse,
            "report_baseline_curve": baseline_images.get(code),
            "report_baseline_curve_available": code in baseline_images,
            "diagnosis": diagnosis,
        }
        rows.append(row)
    return rows


def build_summary(rows):
    frame = pd.DataFrame(rows)
    return {
        "event_count": len(frame),
        "curve_coverage": {
            "current_model": int(frame["flood_code"].nunique()),
            "report_baseline": int(frame["report_baseline_curve_available"].sum()),
            "report_baseline_missing": int((~frame["report_baseline_curve_available"]).sum()),
            "report_baseline_images_total": len(REPORT_BASELINE_IMAGES),
            "report_baseline_extra_images": sorted(set(REPORT_BASELINE_IMAGES) - set(frame["flood_code"])),
        },
        "report_baseline_xaj": {
            "mean_nse_published": 0.786,
            "mean_nse_from_rounded_rows": float(frame["report_baseline_nse"].mean()),
            "qualified_count_reported": int(frame["report_baseline_qualified"].sum()),
        },
        "report_stratified_xaj": {
            "mean_nse_from_rows": float(frame["report_stratified_nse"].mean()),
            "qualified_count_reported": int(frame["report_stratified_qualified"].sum()),
        },
        "current_joint_xaj_routing": {
            "mean_nse": float(frame["current_NSE"].mean()),
            "mean_kge": float(frame["current_KGE"].mean()),
            "mean_abs_peak_error_percent": float(frame["current_peak_error_percent"].abs().mean()),
            "mean_abs_volume_error_percent": float(frame["current_volume_error_percent"].abs().mean()),
            "mean_peak_time_error_hours": float(frame["current_peak_time_error_hours"].mean()),
            "median_abs_peak_time_error_hours": float(frame["current_peak_time_error_hours"].abs().median()),
            "qualified_count_by_nse": int(frame["current_qualified_by_NSE"].sum()),
        },
        "deltas": {
            "current_nse_minus_report_published_baseline": float(frame["current_NSE"].mean() - 0.786),
            "current_nse_minus_report_stratified": float(frame["current_NSE"].mean() - frame["report_stratified_nse"].mean()),
        },
        "diagnostic_experiments": {
            "report_rainfall_total_scaling": {
                "mean_nse_before": 0.6892687563,
                "mean_nse_after": 0.7389399567,
                "mean_kge_before": 0.5668064341,
                "mean_kge_after": 0.6923494828,
                "mean_abs_peak_error_before_percent": 26.46810127,
                "mean_abs_peak_error_after_percent": 18.47113305,
                "mean_abs_volume_error_before_percent": 33.35511059,
                "mean_abs_volume_error_after_percent": 22.22859539,
                "qualified_count_before_by_nse": 26,
                "qualified_count_after_by_nse": 26,
            },
            "rainfall_input_audit": {
                "report_mean_mm": 130.1533333,
                "current_1km_mean_mm": 125.5259367,
                "station_11_mean_mm": 122.3432857,
                "current_1km_minus_report_mm": -4.6273967,
                "station_11_minus_report_mm": -7.8100476,
                "current_1km_closer_events": 23,
                "station_count": 11,
                "report_text_station_count": 12,
            },
        },
        "data_quality_and_fairness": {
            "current_calibration_events": 30,
            "current_independent_validation_events": 0,
            "current_state_warmup_hours": 168,
            "current_observed_flow_anchor": False,
            "current_pet_mode": "MONTHLY_CLIMATOLOGY diagnostic assumption",
            "current_control_section_data_review": "REVIEW_REQUIRED",
            "current_routing_mapping_max_relative_area_error": 0.03632934,
        },
    }


def fmt(value, digits=3):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{float(value):.{digits}f}"


def build_markdown(rows, summary, output_dir: Path):
    s = summary
    frame = pd.DataFrame(rows)
    large_losses = frame.sort_values("current_minus_report_baseline_NSE").head(8)
    gains = frame.sort_values("current_minus_report_baseline_NSE", ascending=False).head(5)
    timing = frame.reindex(frame["current_peak_time_error_hours"].abs().sort_values(ascending=False).index).head(5)
    lines = []
    lines.extend([
        "# 南安溪徐畈30场洪水：报告基准XAJ与当前完整模型对比分析",
        "",
        "> 汇报版数据分析稿，生成时间：2026-09-05。当前完整模型指：XAJ产流 + 1 km产流网格到30 m DEM河段集水区映射 + 坡面线性水库 + 河段Muskingum汇流。",
        "> 本文严格区分报告公开的汇总指标、报告原始过程线图和当前模型逐时CSV；报告没有公开的逐时序列不进行反推。",
        "",
        "## 一、汇报结论",
        "",
        f"1. 当前模型30场平均NSE为 **{fmt(s['current_joint_xaj_routing']['mean_nse'])}**，比报告基准XAJ公开平均值0.786低 **{abs(s['deltas']['current_nse_minus_report_published_baseline']):.3f}**；比报告分层XAJ平均NSE **{fmt(s['report_stratified_xaj']['mean_nse_from_rows'])}**低 **{abs(s['deltas']['current_nse_minus_report_stratified']):.3f}**。",
        f"2. 当前模型的形状和时序指标仍有可用基础：平均KGE **{fmt(s['current_joint_xaj_routing']['mean_kge'])}**，平均绝对洪峰误差 **{s['current_joint_xaj_routing']['mean_abs_peak_error_percent']:.2f}%**，平均绝对径流量误差 **{s['current_joint_xaj_routing']['mean_abs_volume_error_percent']:.2f}%**，峰现时差绝对值中位数 **{s['current_joint_xaj_routing']['median_abs_peak_time_error_hours']:.1f} h**。",
        f"3. 不能把当前模型的“26/30合格”和报告的“22/30合格”直接判为同一标准下的优劣：当前平台合格定义是 **NSE≥0.5**，而报告表中的合格标志显然不是单纯NSE阈值（例如报告31210523的NSE为0.472但标记合格）。",
        f"4. 差距首先由输入和结构共同造成：仅把当前1 km场的每场总雨量缩放到报告雨量，平均NSE就从0.689提高到0.739，平均绝对洪峰误差从26.47%降到18.47%，但峰时差没有改善，说明总雨量不是唯一原因。",
        f"5. 当前结果仍属于 **DIAGNOSTIC_ONLY_DATA_REVIEW_REQUIRED**：30场全部参与率定，没有独立验证集；PET为诊断假设；控制断面资料仍待核查。因此当前数值适合作为模型改进定位依据，不应作为最终生产预报精度结论。",
        "",
        "## 二、模型和数据口径",
        "",
        "| 对象 | 计算/报告含义 | 过程线数据状态 | 评价注意 |\n|---|---|---|---|\n| 报告基准XAJ | 技术报告表5.4-1、表5.4-2；传统次洪XAJ方案 | 仅公开5场图像，未公开30场逐时CSV | 官方表中平均确定性系数为0.786 |\n| 报告分层XAJ | 技术报告表5.4-3、表5.4-4；按洪水等级调整参数 | 未公开30场逐时CSV | 30场逐场指标可读，平均NSE按行计算为0.839 |\n| 当前完整模型 | 当前平台30场联合率定结果 | 30场逐时CSV齐全 | 全部30场参与率定，无独立检验集 |\n| 报告SWAN | 技术报告连续期SWAN结果 | 只有连续期说明和部分图件 | 没有30场同口径数值，不做严格排名 |",
        "",
        "报告基准参数表还存在列顺序歧义：报告文字排列表头为`KC、WUM、WLM、WM、C`，但出现`WUM=150、WLM=20、WM=80`会违反`WM≥WUM+WLM`。项目接口目前按物理可行解释使用`WM=150、WUM=20、WLM=80`，这一解释需要负责人最终确认。",
        "",
        "## 三、总体指标对比",
        "",
        "| 模型 | 场次 | 平均NSE/确定性系数 | 平均KGE | 平均绝对洪峰误差 | 平均绝对洪量误差 | 峰时差绝对值中位数 | 报告/内部合格 |\n|---|---:|---:|---:|---:|---:|---:|---:|\n| 报告基准XAJ | 30 | 0.786（报告公布） | 未公开 | 未公开 | 未公开 | 未公开 | 22/30（报告标志） |\n| 报告分层XAJ | 30 | 0.839 | 未公开 | 未公开 | 未公开 | 未公开 | 26/30（报告标志） |\n| 当前完整模型 | 30 | 0.689 | 0.567 | 26.47% | 33.36% | 2.0 h | 26/30（NSE≥0.5） |",
        "",
        f"报告基准30行NSE按表内保留三位小数重算的平均值为 **{s['report_baseline_xaj']['mean_nse_from_rounded_rows']:.6f}**，与报告公布的0.786不一致；本文以报告公布值0.786作为正式比较基准，并把行重算值只作为复核信息。",
        "",
        "## 四、30场逐场对比",
        "",
        "表中`ΔNSE(当前-报告基准)`为当前模型减报告基准；`当前合格`采用当前平台NSE≥0.5定义，不能直接与报告的合格标志混用。",
        "",
        "| 洪号 | 报告基准NSE | 报告分层NSE | 当前NSE | ΔNSE | 当前KGE | 当前洪峰误差 | 当前洪量误差 | 当前峰时差 | 当前合格 | 报告曲线 |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|",
    ])
    for row in rows:
        lines.append(
            f"| {row['flood_code']} | {fmt(row['report_baseline_nse'])} | {fmt(row['report_stratified_nse'])} | {fmt(row['current_NSE'])} | {row['current_minus_report_baseline_NSE']:+.3f} | {fmt(row['current_KGE'])} | {row['current_peak_error_percent']:+.1f}% | {row['current_volume_error_percent']:+.1f}% | {row['current_peak_time_error_hours']:+.1f} h | {'√' if row['current_qualified_by_NSE'] else '×'} | {'有图' if row['report_baseline_curve_available'] else '无逐时'} |"
        )
    lines.extend([
        "",
        "### 逐场重点",
        "",
        "当前模型相对报告基准NSE下降最大的8场：",
        "",
        "| 洪号 | 报告基准NSE | 当前NSE | ΔNSE | 当前洪峰误差 | 当前径流量误差 | 峰时差 |\n|---|---:|---:|---:|---:|---:|---:|",
    ])
    for _, row in large_losses.iterrows():
        lines.append(f"| {row['flood_code']} | {row['report_baseline_nse']:.3f} | {row['current_NSE']:.3f} | {row['current_minus_report_baseline_NSE']:+.3f} | {row['current_peak_error_percent']:+.1f}% | {row['current_volume_error_percent']:+.1f}% | {row['current_peak_time_error_hours']:+.1f} h |")
    lines.extend([
        "",
        "当前模型相对报告基准NSE改善最大的5场，说明新汇流结构并非所有洪水都变差：",
        "",
        "| 洪号 | 报告基准NSE | 当前NSE | ΔNSE | 当前洪峰误差 | 当前峰时差 |\n|---|---:|---:|---:|---:|---:|",
    ])
    for _, row in gains.iterrows():
        lines.append(f"| {row['flood_code']} | {row['report_baseline_nse']:.3f} | {row['current_NSE']:.3f} | {row['current_minus_report_baseline_NSE']:+.3f} | {row['current_peak_error_percent']:+.1f}% | {row['current_peak_time_error_hours']:+.1f} h |")
    lines.extend([
        "",
        "当前峰时差绝对值最大的5场：",
        "",
        "| 洪号 | 当前峰时差 | 当前NSE | 当前洪峰误差 | 解释优先级 |\n|---|---:|---:|---:|---|",
    ])
    for _, row in timing.iterrows():
        priority = "先复核多峰事件配对和事件窗" if abs(row["current_peak_time_error_hours"]) >= 24 else "复核降雨时序和汇流参数"
        lines.append(f"| {row['flood_code']} | {row['current_peak_time_error_hours']:+.1f} h | {row['current_NSE']:.3f} | {row['current_peak_error_percent']:+.1f}% | {priority} |")
    lines.extend([
        "",
        "## 五、过程线可视化结果与数据边界",
        "",
        f"- 当前模型：30/30场有逐时过程线CSV，格式为`time, observed_flow_m3_s, simulated_flow_m3_s`；同时生成30张PNG和一个交互网页。",
        f"- 报告基准XAJ：共找到5张原始过程线图，其中与本次30场表格重合4场：`31220317、31210627、31190709、31190618`；另有1张为表外洪号`31240619`。",
        f"- 本次30场中的其余26场：报告只有表格指标，没有逐时序列；不能用报告峰值、径流深、NSE和峰时差唯一确定每个时刻的流量。",
        "- 因此本项目不把当前模型曲线复制成报告基准曲线，也不把人工读取图像得到的近似值冒充原始模拟数据。",
        "",
        "5场报告原图：",
        "",
    ])
    for code in REPORT_BASELINE_IMAGES:
        lines.append(f"### 报告基准过程线图：{code}\n\n![报告基准XAJ {code}](report_baseline_figures/{code}.png)\n")
    lines.extend([
        "",
        "## 六、效果差异的原因分析",
        "",
        "### 6.1 降雨输入口径不一致：已证实的重要来源",
        "",
        f"站点审计显示，报告30场平均雨量为 **{s['diagnostic_experiments']['rainfall_input_audit']['report_mean_mm']:.3f} mm**，当前1 km场平均为 **{s['diagnostic_experiments']['rainfall_input_audit']['current_1km_mean_mm']:.3f} mm**，平均少 **{abs(s['diagnostic_experiments']['rainfall_input_audit']['current_1km_minus_report_mm']):.3f} mm**；当前可读站点为11站，而报告正文写12站。11站算术平均更少，平均为 **{s['diagnostic_experiments']['rainfall_input_audit']['station_11_mean_mm']:.3f} mm**。",
        "",
        "降雨总量校准诊断的结果是：",
        "",
        "| 指标 | 当前完整模型 | 仅校准到报告场次总雨量 | 变化 |\n|---|---:|---:|---:|\n| 平均NSE | 0.6893 | 0.7389 | +0.0497 |\n| 平均KGE | 0.5668 | 0.6923 | +0.1255 |\n| 平均绝对洪峰误差 | 26.47% | 18.47% | -8.00个百分点 |\n| 平均绝对洪量误差 | 33.36% | 22.23% | -11.13个百分点 |\n| 峰时差绝对值中位数 | 2.0 h | 3.0 h | 未改善 |\n| 内部NSE合格场次 | 26/30 | 26/30 | 未增加 |",
        "",
        "结论：当前模型存在降雨总量偏低或口径差异，但峰时问题没有因总量修正消失，仍需检查小时分配、空间权重和汇流路径。",
        "",
        "### 6.2 控制断面没有按河段中部正式切分：高风险结构问题",
        "",
        "此前拓扑审计发现，徐畈控制断面吸附到`R0020`中部，不是`R0020`末端。R0020约1.26 km，控制点沿段位置约33.6%，下游仍约0.84 km。旧流程在完整演算R0020后记录控制流量，并把整个`U_R0020_NON_GLACIER`作为R0020上游单元，所以控制站流量会混入断面下游河段的传播结果。",
        "",
        "诊断切分结果为：",
        "",
        "- R0020原河段：43个30 m河网像元，面积2.574 km²；\n- R0020_UP：15个像元，0.8991 km²；\n- R0020_DOWN：28个像元，1.6749 km²；\n- 切分前后面积差0，1 km产流网格未分配数0；\n- 切分后河段数量31→32；\n- 但诊断结果当时尚未给`R0020_UP`和`R0020_DOWN`配置独立Muskingum参数，因此必须把“切分、参数、输出控制流量”作为一个完整闭环正式重跑。",
        "",
        "这会优先影响峰现时间、局地入流位置和控制断面洪峰，不能仅通过调大/调小统一河道参数补偿。",
        "",
        "### 6.3 1 km产流网格与30 m河段集水区的映射误差：需要守恒和路径两项同时检查",
        "",
        "当前方案的思路是合理的：30 m DEM用于D8、河网和河段集水区，1 km网格用于XAJ产流，之后按空间相交/面积权重把`RS、RI、RG`映射到河段单元。但面积审计中仍有最大约3.63%的路由映射相对误差标记，说明总量守恒虽然接近，但部分1 km网格的入流归属仍可能被分配到错误河段或被按调整系数补齐。",
        "",
        "需要分别检查：1 km网格与河段集水区相交面积是否守恒；D8路径终点是否落在正确河段；无河段邻近网格是否沿D8路径继续追踪到下游河段；控制断面切分后映射是否重新生成；RS/RI/RG是否分别守恒，而不只是总径流守恒。",
        "",
        "### 6.4 产流参数和汇流参数被联合率定，误差来源可能互相补偿",
        "",
        "当前正式联合率定同时调整XAJ产流参数和汇流参数，得到的参数包括`WM=120、B=0.55、C≈0.327、SM≈15.85、KI≈0.0436、KG=0.06`以及坡面线性水库和Muskingum参数。报告基准解释值为`WM=150、B=0.16、C=0.37、SM=15、KI/KG`来自日尺度值换算或报告参数表。两者物理含义和时间步长口径并不完全相同。",
        "",
        "联合率定在没有独立验证集时可能用产流参数补偿错误的降雨或拓扑，也可能用汇流参数补偿产流量偏差。下一步应先固定已核实的产流参数，单独率定汇流；再固定汇流结构和参数，回头率定产流；最后做联合率定并保留独立检验。",
        "",
        "### 6.5 初始状态、PET和资料质量仍是未排除因素",
        "",
        "当前正式结果使用168 h固定预热，不继承场次间状态，不使用实测流量锚定；PET采用`MONTHLY_CLIMATOLOGY`诊断假设，不是经过确认的逐小时蒸发资料。资料审计还保留了长时间单调流量段、全零降雨月含义、可能的上游水库调度影响等待核查项。这些因素会直接影响基流、退水段、起涨点和长历时多峰洪水。",
        "",
        "### 6.6 峰现时差的统计方法在长历时多峰洪水上不够稳健",
        "",
        "当前峰时差主要使用整场`idxmax()`比较单一最大峰。31190210约+112 h、31210523约+75 h，且两个事件窗口较长，可能包含多个降雨峰和多个流量响应过程。若实测和模拟选择了不同子过程的最大峰，这个时间差不能直接解释为河道传播时间。应增加多峰事件识别、起涨点、各子峰匹配和事件窗口切分，再用于率定汇流参数。",
        "",
        "## 七、此前已完成的排查和改进工作汇总",
        "",
        "1. **降雨质控**：完成疑似全0站年剔除和QC标记输出，重新生成站点NC和1 km降雨场；但报告12站与当前11站仍未严格闭合。",
        "2. **空间分辨率调整**：确认1 km DEM只有约110个有效网格，不适合直接提取准确河网、坡向和D8；改为30 m DEM派生河网/河段集水区，1 km网格保留为产流网格。",
        "3. **拓扑和控制断面**：发现徐畈控制点落在R0020中部而旧输出取R0020末端；已生成R0020_UP/R0020_DOWN诊断切分，切分面积守恒且1 km网格零未分配，但尚需正式接入独立河段参数并重跑30场。",
        "4. **任意点查询**：已实现河网吸附、临时控制节点、拓扑重构、1 km映射重建、RS/RI/RG聚合、坡面线性水库和河段Muskingum重跑，并返回`TEMPORARY_CONTROL_SPLIT_RERUN`/`RERUN_COMPLETED`状态。",
        "5. **坐标错位修复**：统一DEM、河网和控制点的地理裁剪窗口及Canvas CSS尺寸换算，修复鼠标点击位置与显示点、DEM和河网错位问题。",
        "6. **汇流稳定性检查**：统一Muskingum参数下31个河段系数未发现负值，暂未发现公式级数值不稳定；优先级应先放在断面切分、映射路径和参数口径。",
        "7. **降雨总量消融试验**：仅调整每场总雨量后平均NSE提升约0.050，证明输入总量是重要原因但不是唯一原因。",
        "",
        "## 八、建议的改进顺序",
        "",
        "### 第一优先级：建立可公平复核的基准",
        "",
        "- 向负责人取得报告基准XAJ30场逐时模拟CSV，或取得可运行的原始基准程序、参数、降雨、事件窗和初始状态；\n- 明确报告平均NSE0.786的计算方式、合格判据、控制面积和峰时差正负号；\n- 将当前平台合格判据拆成与报告一致的峰值、洪量、峰时和NSE四项，不再只用NSE≥0.5冒充报告合格率。",
        "",
        "### 第二优先级：正式完成控制断面切分",
        "",
        "- 对R0020按控制点将河段切为`R0020_UP`和`R0020_DOWN`；\n- 重新生成二者集水区、1 km网格映射和上下游拓扑；\n- 给两个河段分别生成参数记录；\n- 控制站输出必须读取`R0020_UP`末端，流域出口读取下游完整路径；\n- 用30场逐时过程线重新率定/评估，并单独报告切分前后差异。",
        "",
        "### 第三优先级：拆开产流、坡面和河道汇流率定",
        "",
        "- 先固定降雨和产流参数，率定`RS_k、RI_k、RG_k、Muskingum K、X`；\n- 再固定汇流参数，率定`WM、B、C、SM、KI、KG`；\n- 最后联合率定，但必须使用独立验证集和参数先验/物理范围；\n- 对RS、RI、RG分别做水量守恒检查和逐段累计入流检查。",
        "",
        "### 第四优先级：修正资料和评价",
        "",
        "- 找回第12个报告雨量站或解释其缺失原因；\n- 用真实逐小时PET替换月气候假设；\n- 对多峰洪水做子事件切分和峰匹配；\n- 核查长单调流量段、全零降雨月和水库调度；\n- 采用按年份或时间段独立验证，而不是30场全部率定后直接当作泛化能力。",
        "",
        "## 九、交付文件",
        "",
        f"- 本分析稿：`{output_dir / '南安溪徐畈30场洪水_报告基准XAJ与当前模型对比分析.md'}`",
        f"- 逐场扩展指标：`{output_dir / 'report_vs_current_30_event_detailed.csv'}`",
        f"- 汇总JSON：`{output_dir / 'report_vs_current_30_event_summary.json'}`",
        f"- 交互网页：`{output_dir / 'report_vs_current_30_event_dashboard.html'}`",
        f"- 当前模型30场PNG：`{output_dir / 'current_model_figures'}`",
        f"- 报告基准5场原图：`{output_dir / 'report_baseline_figures'}`",
        "",
        "## 十、汇报时建议的口头表述",
        "",
        "> 当前模型与报告基准相比，平均NSE低约0.097，但这不是单一算法优劣结论。输入方面当前可读站点为11站而报告文字为12站，1 km降雨场平均比报告雨量少约4.63 mm；把每场总雨量校准后NSE能提高约0.050。结构方面，徐畈控制断面位于R0020中部，旧结果按R0020末端输出，控制断面切分尚需正式重跑。评价方面当前30场全部参加率定、没有独立验证集，且当前合格定义与报告不同。因此下一步先统一数据和评价口径，再完成控制断面切分和分层率定，最后用独立洪水验证模型改进是否真正有效。",
    ])
    return "\n".join(lines) + "\n"


def build_html(rows, curves, summary, baseline_images, output_dir: Path):
    payload = {"rows": rows, "curves": curves, "summary": summary, "baseline_images": baseline_images}
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>南安溪徐畈30场模型对比</title>
<style>
:root{--bg:#f2f5f2;--ink:#20302d;--muted:#647470;--line:#d6e0db;--paper:#fff;--blue:#126782;--orange:#d36b35;--green:#2d7d61;--red:#bd4d43;--yellow:#aa791f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 "Microsoft YaHei","Noto Sans CJK SC",sans-serif}.wrap{max-width:1500px;margin:auto;padding:28px 30px 56px}.mast{border-bottom:1px solid var(--line);padding:6px 0 20px}.eyebrow{color:var(--blue);font-size:12px;letter-spacing:.08em}.mast h1{font-size:28px;line-height:1.2;margin:8px 0}.muted{color:var(--muted)}.notice{margin-top:14px;padding:12px 14px;background:#fff5e6;border-left:4px solid var(--yellow)}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.stat{background:var(--paper);border:1px solid var(--line);padding:15px 16px}.stat b{display:block;font-size:28px;line-height:1.1;font-variant-numeric:tabular-nums}.stat span{color:var(--muted)}
.grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(360px,.7fr);gap:16px;align-items:start}.section{background:var(--paper);border:1px solid var(--line);padding:16px;margin-top:16px}.section h2{font-size:17px;margin:0 0 12px}.chart{width:100%;height:330px;display:block}.barlabel{font-size:12px;fill:var(--ink)}.axis{stroke:#98aaa3;stroke-width:1}.gridline{stroke:#e4ebe7;stroke-width:1}.bar{opacity:.92}.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px}.swatch{display:inline-block;width:10px;height:10px;margin-right:5px}.blue{background:var(--blue)}.orange{background:var(--orange)}.green{background:var(--green)}
.controls{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.field{display:flex;flex-direction:column;gap:4px}.field label{font-size:12px;color:var(--muted)}select{font:inherit;border:1px solid #b9cbc3;background:#fff;padding:8px 34px 8px 10px;color:var(--ink)}.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:12px 0}.fact{border-bottom:1px solid var(--line);padding:8px 0}.fact b{display:block;font-size:18px;font-variant-numeric:tabular-nums}.fact span{font-size:12px;color:var(--muted)}
#hydro{height:320px}.report-figure{width:100%;height:auto;display:block;border:1px solid var(--line);margin-top:10px}.missing{min-height:300px;display:flex;align-items:center;justify-content:center;text-align:center;color:var(--muted);background:#f7faf8;border:1px dashed #b8c9c1;padding:30px}.small{font-size:12px;color:var(--muted)}.table-wrap{overflow:auto;max-height:590px}.table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}.table th,.table td{border-bottom:1px solid #e7eeea;padding:8px 7px;text-align:right}.table th{position:sticky;top:0;background:#eef5f0;color:var(--muted);font-weight:500}.table th:first-child,.table td:first-child{text-align:left}.table tbody tr{cursor:pointer}.table tbody tr:hover,.table tbody tr.active{background:#eaf3ef}.up{color:var(--green)}.down{color:var(--red)}.status-ok{color:var(--green)}.status-bad{color:var(--red)}
.coverage{display:grid;grid-template-columns:1fr 1fr;gap:12px}.coverage div{padding:12px 14px;border-left:3px solid var(--blue);background:#f7faf8}.coverage b{font-size:22px}.footer{margin-top:22px;color:var(--muted);font-size:12px}@media(max-width:1000px){.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.wrap{padding:18px 12px 40px}.mast h1{font-size:23px}.stats,.facts,.coverage{grid-template-columns:1fr 1fr}.section{padding:12px}.table{font-size:11px}}
</style></head><body><main class="wrap">
 <header class="mast"><div class="eyebrow">NAN'ANXI · XUFAN · 30 FLOOD EVENTS</div><h1>报告基准 XAJ 与当前完整产汇流模型</h1><div class="muted">逐场指标对比、当前模型过程线和报告原始曲线证据</div><div class="notice"><b>曲线数据边界：</b>当前模型30场逐时CSV齐全；报告基准XAJ在目标30场中仅有4场原始过程线图，另外26场未公开逐时模拟序列；另有1张表外洪号31240619原图。本页面不反推、不伪造缺失曲线。</div></header>
<section class="stats" id="stats"></section>
<div class="grid"><div><section class="section"><h2>平均NSE与报告曲线覆盖</h2><svg id="summaryChart" class="chart" viewBox="0 0 760 330" role="img" aria-label="报告基准、报告分层和当前模型平均NSE对比"></svg><div class="legend"><span><i class="swatch blue"></i>报告基准XAJ</span><span><i class="swatch green"></i>报告分层XAJ</span><span><i class="swatch orange"></i>当前完整模型</span></div></section>
<section class="section"><h2>30场逐场指标</h2><div class="table-wrap"><table class="table"><thead><tr><th>洪号</th><th>报告基准NSE</th><th>分层NSE</th><th>当前NSE</th><th>ΔNSE</th><th>当前KGE</th><th>洪峰误差</th><th>洪量误差</th><th>峰时差</th><th>当前合格</th></tr></thead><tbody id="eventTable"></tbody></table></div></section></div>
<div><section class="section"><div class="controls"><div class="field"><label for="eventSelect">选择洪水场次</label><select id="eventSelect"></select></div><div class="small" id="curveSource"></div></div><div class="facts" id="facts"></div><svg id="hydro" class="chart" viewBox="0 0 760 320" role="img" aria-label="当前模型实测与模拟流量过程线"></svg><div class="legend"><span><i class="swatch blue"></i>实测流量</span><span><i class="swatch orange"></i>当前模型模拟流量</span></div><div class="small" id="curveNote"></div></section>
<section class="section"><h2 id="reportFigureTitle">报告基准过程线证据</h2><div id="reportFigure"></div></section>
<section class="section"><h2>曲线覆盖情况</h2><div class="coverage"><div><b id="currentCurveCount"></b><br><span class="small">当前模型逐时曲线</span></div><div><b id="reportCurveCount"></b><br><span class="small">报告基准原始图</span></div></div><p class="small">报告其余场次只有汇总指标，不能由峰值、洪量和NSE唯一恢复逐时过程。</p></section></div></div>
<p class="footer">当前30场结果为全部参与率定的诊断结果，当前合格标志按平台NSE≥0.5定义；报告合格标志采用报告自身口径。详情见同目录Markdown汇报稿。</p>
</main><script>
const DATA=__PAYLOAD__;
const rows=DATA.rows, curves=DATA.curves, S=DATA.summary;
const $=id=>document.getElementById(id);
const f=(v,n=3)=>v==null?'—':Number(v).toFixed(n);
const codeOf=(r)=>r.flood_code;
function renderStats(){const c=S.current_joint_xaj_routing;const cards=[[f(S.report_baseline_xaj.mean_nse_published),'报告基准平均NSE'],[f(S.report_stratified_xaj.mean_nse_from_rows),'报告分层平均NSE'],[f(c.mean_nse),'当前模型平均NSE'],[`${S.curve_coverage.report_baseline}/30`,'报告基准有原始曲线']];$('stats').innerHTML=cards.map(x=>`<div class="stat"><b>${x[0]}</b><span>${x[1]}</span></div>`).join('');$('currentCurveCount').textContent=S.curve_coverage.current_model+'/30';$('reportCurveCount').textContent=S.curve_coverage.report_baseline+'/30'}
function renderSummary(){const svg=$('summaryChart'), W=760,H=330,p={l:70,r:18,t:20,b:55}, vals=[S.report_baseline_xaj.mean_nse_published,S.report_stratified_xaj.mean_nse_from_rows,S.current_joint_xaj_routing.mean_nse], labs=['报告基准XAJ','报告分层XAJ','当前完整模型'], colors=['#126782','#2d7d61','#d36b35']; const y=v=>H-p.b-v*(H-p.t-p.b); let out=''; [0,.25,.5,.75,1].forEach(v=>{out+=`<line class="gridline" x1="${p.l}" y1="${y(v)}" x2="${W-p.r}" y2="${y(v)}"/><text class="barlabel" x="${p.l-10}" y="${y(v)+4}" text-anchor="end">${v.toFixed(2)}</text>`}); const bw=135,gap=65; vals.forEach((v,i)=>{const x=p.l+55+i*(bw+gap);out+=`<rect class="bar" x="${x}" y="${y(v)}" width="${bw}" height="${H-p.b-y(v)}" fill="${colors[i]}"/><text class="barlabel" x="${x+bw/2}" y="${y(v)-8}" text-anchor="middle">${v.toFixed(3)}</text><text class="barlabel" x="${x+bw/2}" y="${H-p.b+25}" text-anchor="middle">${labs[i]}</text>`});out+=`<line class="axis" x1="${p.l}" y1="${H-p.b}" x2="${W-p.r}" y2="${H-p.b}"/><text class="barlabel" x="${W/2}" y="${H-7}" text-anchor="middle">平均NSE / 报告确定性系数</text>`;svg.innerHTML=out}
function renderTable(){const body=$('eventTable');body.innerHTML=rows.map((r,i)=>`<tr data-i="${i}"><td>${codeOf(r)}</td><td>${f(r.report_baseline_nse)}</td><td>${f(r.report_stratified_nse)}</td><td>${f(r.current_NSE)}</td><td class="${r.current_minus_report_baseline_NSE>=0?'up':'down'}">${r.current_minus_report_baseline_NSE>=0?'+':''}${f(r.current_minus_report_baseline_NSE)}</td><td>${f(r.current_KGE)}</td><td>${f(r.current_peak_error_percent,1)}%</td><td>${f(r.current_volume_error_percent,1)}%</td><td>${f(r.current_peak_time_error_hours,1)} h</td><td class="${r.current_qualified_by_NSE?'status-ok':'status-bad'}">${r.current_qualified_by_NSE?'√':'×'}</td></tr>`).join('');body.querySelectorAll('tr').forEach(tr=>tr.addEventListener('click',()=>select(Number(tr.dataset.i))))}
function svgPath(values,x,y){return values.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+','+y(v||0).toFixed(1)).join(' ')}
function drawHydro(i){const r=rows[i], c=curves[codeOf(r)], svg=$('hydro'), W=760,H=320,p={l:52,r:18,t:18,b:44}, all=c.observed.concat(c.simulated).filter(v=>v!=null), max=Math.max(...all,1), x=j=>p.l+j/(c.times.length-1||1)*(W-p.l-p.r), y=v=>H-p.b-(v/max)*(H-p.t-p.b); let out=''; [0,.25,.5,.75,1].forEach(v=>{const yy=y(v*max);out+=`<line class="gridline" x1="${p.l}" y1="${yy}" x2="${W-p.r}" y2="${yy}"/><text class="barlabel" x="${p.l-8}" y="${yy+4}" text-anchor="end">${(v*max).toFixed(0)}</text>`});out+=`<path d="${svgPath(c.observed,x,y)}" fill="none" stroke="#126782" stroke-width="2"/><path d="${svgPath(c.simulated,x,y)}" fill="none" stroke="#d36b35" stroke-width="2"/><line class="axis" x1="${p.l}" y1="${H-p.b}" x2="${W-p.r}" y2="${H-p.b}"/><line class="axis" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${H-p.b}"/><text class="barlabel" x="${p.l}" y="${H-13}">${c.times[0].slice(0,10)}</text><text class="barlabel" x="${W-p.r}" y="${H-13}" text-anchor="end">${c.times[c.times.length-1].slice(0,10)}</text><text class="barlabel" x="${W/2}" y="${H-5}" text-anchor="middle">时间</text><text class="barlabel" x="12" y="${H/2}" transform="rotate(-90 12 ${H/2})" text-anchor="middle">流量 (m³/s)</text>`;svg.innerHTML=out;$('facts').innerHTML=[[f(r.current_NSE),'当前NSE'],[f(r.current_KGE),'当前KGE'],[f(r.current_peak_error_percent,1)+'%','洪峰误差'],[f(r.current_peak_time_error_hours,1)+' h','峰时差']].map(x=>`<div class="fact"><b>${x[0]}</b><span>${x[1]}</span></div>`).join('');$('curveSource').textContent=`${codeOf(r)} · ${c.times.length}个时段`;$('curveNote').textContent=`当前模型实测径流深 ${f(r.current_observed_runoff_depth_mm,1)} mm，模拟 ${f(r.current_simulated_runoff_depth_mm,1)} mm；报告基准NSE ${f(r.report_baseline_nse)}，当前-报告基准 ΔNSE ${r.current_minus_report_baseline_NSE>=0?'+':''}${f(r.current_minus_report_baseline_NSE)}。`;const img=DATA.baseline_images[codeOf(r)];$('reportFigureTitle').textContent=`报告基准过程线证据 · ${codeOf(r)}`;$('reportFigure').innerHTML=img?`<img class="report-figure" src="${img}" alt="报告基准XAJ ${codeOf(r)} 原始过程线图"><p class="small">此图为技术报告内嵌原始图像，图中没有可导出的逐时数值表。</p>`:`<div class="missing">报告未公开 ${codeOf(r)} 的逐时基准模拟序列。<br>当前页仅展示报告指标和当前模型真实逐时曲线。</div>`;document.querySelectorAll('#eventTable tr').forEach((tr,j)=>tr.classList.toggle('active',j===i))}
function select(i){$('eventSelect').value=codeOf(rows[i]);drawHydro(i)}
renderStats();renderSummary();renderTable();$('eventSelect').innerHTML=rows.map(r=>`<option value="${codeOf(r)}">${codeOf(r)}</option>`).join('');$('eventSelect').addEventListener('change',e=>select(rows.findIndex(r=>codeOf(r)===e.target.value)));select(0);
</script></body></html>'''.replace("__PAYLOAD__", payload_text)
    return html


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path(r"D:\研究生\XAJ\docs\【技术报告】典型中小流域洪水预报方案构建项目-终稿(1).docx"))
    parser.add_argument("--current-metrics", type=Path, default=Path(r"D:\研究生\XAJ\data_products\visualizations\nan_an\selected_30_joint_calibration_20260823\joint_calibration_event_metrics_enriched.csv"))
    parser.add_argument("--hydrograph-dir", type=Path, default=Path(r"D:\研究生\XAJ\data_products\visualizations\nan_an\selected_30_joint_calibration_20260823\hydrographs"))
    parser.add_argument("--output-dir", type=Path, default=Path(r"D:\研究生\XAJ\outputs\报告基准XAJ与当前模型30场对比分析_20260905"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    document = Document(args.report)
    baseline = read_report_table(document, 14)
    stratified = read_report_table(document, 16)
    current = read_current_metrics(args.current_metrics)
    missing = [code for code in REPORT_CODES if code not in baseline or code not in stratified or code not in current.index]
    if missing:
        raise RuntimeError(f"Missing event data: {missing}")
    baseline_images = copy_report_baseline_images(args.report, args.output_dir)
    curves = copy_hydrographs(args.hydrograph_dir, args.output_dir)
    rows = build_rows(baseline, stratified, current, baseline_images)
    summary = build_summary(rows)
    frame = pd.DataFrame(rows)
    frame.to_csv(args.output_dir / "report_vs_current_30_event_detailed.csv", index=False, encoding="utf-8-sig")
    (args.output_dir / "report_vs_current_30_event_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "南安溪徐畈30场洪水_报告基准XAJ与当前模型对比分析.md").write_text(build_markdown(rows, summary, args.output_dir), encoding="utf-8")
    (args.output_dir / "report_vs_current_30_event_dashboard.html").write_text(build_html(rows, curves, summary, baseline_images, args.output_dir), encoding="utf-8")
    readme = """# 对比分析产物

打开 `report_vs_current_30_event_dashboard.html` 查看30场逐场选择、当前模型实测-模拟过程线和报告基准曲线证据。

重要边界：报告基准XAJ在原报告中共找到5张过程线图，其中4张属于本次30场，另1张为表外洪号31240619；本次30场其余26场没有公开逐时模拟序列。因此网页和Markdown只展示报告公开内容，不补造基准曲线。
"""
    (args.output_dir / "README.md").write_text(readme, encoding="utf-8")
    print(f"built detailed comparison package: {args.output_dir}")
    print(json.dumps(summary["current_joint_xaj_routing"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
