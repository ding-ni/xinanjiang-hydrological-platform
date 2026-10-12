from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = "source.png"
PX_PER_IN = 100.0
BLUE = "#073A7A"
MID_BLUE = "#185AA5"
LINE_BLUE = "#1E5AA8"
LIGHT_BLUE = "#EAF4FF"
VERY_LIGHT = "#F7FBFF"
TEXT = "#111111"


def inch(value: float) -> float:
    return round(float(value) / PX_PER_IN, 4)


def rect(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {"x": inch(x), "y": inch(y), "w": inch(w), "h": inch(h)}


def shape(
    eid: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str = "#FFFFFF",
    stroke: str = LINE_BLUE,
    stroke_width: float = 0.9,
    kind: str = "roundRect",
) -> dict:
    return {
        "id": eid,
        "type": "shape",
        "shape": kind,
        **rect(x, y, w, h),
        "fill": fill,
        "stroke": stroke,
        "stroke_width": stroke_width,
        "radius": 0.04,
        "source": {"kind": "native_shape", "review_status": "manual_reviewed"},
    }


def text(
    eid: str,
    x: float,
    y: float,
    w: float,
    h: float,
    value: str,
    *,
    size: float = 10.0,
    color: str = TEXT,
    bold: bool = False,
    align: str = "left",
    valign: str = "top",
    margin: float = 0.01,
    role: str = "label",
    semantic: bool = False,
    wrap: bool | None = None,
) -> dict:
    if wrap is None:
        wrap = semantic
    return {
        "id": eid,
        "type": "text",
        **rect(x, y, w, h),
        "text": value,
        "font_size": size,
        "font_face": "Microsoft YaHei",
        "color": color,
        "bold": bold,
        "align": align,
        "valign": valign,
        "margin": margin,
        "word_wrap": wrap,
        "no_wrap": not wrap,
        "trace_level": "paragraph" if semantic else "line",
        "semantic_block": semantic,
        "role": role,
        "line_level_trace_required": not semantic,
        "source": {
            "kind": "manual_reviewed_text",
            "review_status": "corrected",
            "correction_reason": "RapidOCR geometry was used, but Chinese text was manually corrected from the source image.",
            "role": role,
        },
    }


def line(
    eid: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str = "#111111",
    width: float = 1.0,
    dash: bool = False,
    end_arrow: bool = False,
    start_arrow: bool = False,
) -> dict:
    return {
        "id": eid,
        "type": "line",
        "x1": inch(x1),
        "y1": inch(y1),
        "x2": inch(x2),
        "y2": inch(y2),
        "color": color,
        "width": width,
        "dash": dash,
        "end_arrow": end_arrow,
        "start_arrow": start_arrow,
        "arrow_width": "sm",
        "arrow_length": "sm",
        "source": {"kind": "native_line", "review_status": "manual_reviewed"},
    }


def pic(eid: str, path: str, x: float, y: float, w: float, h: float) -> dict:
    return {
        "id": eid,
        "type": "picture",
        "path": path,
        **rect(x, y, w, h),
        "source": {
            "kind": "source_icon_crop",
            "reason": "Small blue icon preserved from source image; no readable source text included.",
        },
    }


def section(elements: list[dict], num: int, title: str, y: float, h: float) -> None:
    elements.append(shape(f"s{num}-outer", 23, y, 1011, h, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(shape(f"s{num}-header", 24, y + 1, 1009, 38, fill=LIGHT_BLUE, stroke=LINE_BLUE, stroke_width=0.7, kind="rect"))
    elements.append(shape(f"s{num}-badge", 24, y, 45, 38, fill=BLUE, stroke=BLUE, stroke_width=0.0, kind="rect"))
    elements.append(text(f"s{num}-badge-text", 37, y + 5, 18, 28, str(num), size=17, color="#FFFFFF", bold=True, align="center", margin=0, role="badge_number"))
    elements.append(text(f"s{num}-header-title", 70, y + 8, 925, 25, title, size=14.5, color=BLUE, bold=True, align="center", margin=0, role="section_header"))


def add_card(
    elements: list[dict],
    eid: str,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    body: str,
    icon_id: str | None = None,
    *,
    title_size: float = 11.5,
    body_size: float = 9.2,
    icon_box: tuple[float, float, float, float] | None = None,
) -> None:
    elements.append(shape(f"{eid}-box", x, y, w, h, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    if icon_id and icon_box:
        ix, iy, iw, ih = icon_box
        elements.append(pic(f"{eid}-icon", f"crops/{icon_id}.png", ix, iy, iw, ih))
    elements.append(text(f"{eid}-title", x + 20, y + 12, w - 30, 24, title, size=title_size, color=TEXT, bold=True, align="center", margin=0, role="card_title"))
    if body:
        elements.append(text(f"{eid}-body", x + 68, y + 50, w - 82, h - 58, body, size=body_size, color=TEXT, align="center", margin=0, role="card_body", semantic=True, wrap=True))


def add_crop(crops: list[dict], cid: str, bbox: tuple[int, int, int, int], allowed_ocr_ids: list[str] | None = None) -> None:
    crops.append(
        {
            "id": cid,
            "bbox": list(bbox),
            "output_name": f"{cid}.png",
            "source_kind": "source_icon_crop",
            "reason": "Tight icon crop from the source image.",
            "allow_text_overlap": False,
            "allowed_ocr_ids": allowed_ocr_ids or [],
        }
    )


def build_crop_manifest() -> None:
    crops: list[dict] = []
    for item in [
        ("s1_rain", (49, 210, 103, 257)),
        ("s1_evap", (251, 210, 303, 258)),
        ("s1_terrain", (448, 204, 500, 258)),
        ("s1_hydro", (655, 211, 707, 266)),
        ("s1_basin", (850, 210, 906, 267)),
        ("s2_db", (58, 419, 91, 456)),
        ("s2_clock", (322, 420, 364, 462)),
        ("s2_basin", (573, 411, 629, 463)),
        ("s2_gear", (811, 416, 857, 462)),
        ("s3_rain", (34, 663, 86, 712), ["ocr_044"]),
        ("s3_evap", (148, 639, 236, 697)),
        ("s3_reservoir", (348, 639, 411, 693)),
        ("s3_waves", (888, 809, 1014, 837)),
        ("s4_drops", (73, 936, 117, 980)),
        ("s4_slope", (382, 935, 440, 975)),
        ("s4_river", (684, 931, 750, 981)),
        ("s4_gauge", (929, 929, 985, 974)),
        ("s5_bars", (72, 1117, 111, 1156)),
        ("s5_magnifier", (194, 1117, 248, 1164)),
        ("s5_sliders", (326, 1116, 366, 1150)),
        ("s5_nse", (530, 1118, 574, 1154)),
        ("s5_curve", (718, 1114, 775, 1153)),
        ("s5_clock", (816, 1117, 862, 1156)),
        ("s5_fit", (940, 1117, 991, 1157)),
        ("s6_flow", (62, 1317, 181, 1378)),
        ("s6_peak", (265, 1317, 392, 1377)),
        ("s6_section", (487, 1322, 582, 1380)),
        ("s6_warn", (718, 1342, 769, 1383)),
        ("s6_monitor", (900, 1341, 963, 1385)),
        ("s6_people", (259, 1425, 315, 1459)),
    ]:
        if len(item) == 3:
            cid, bbox, allowed_ocr_ids = item
        else:
            cid, bbox = item
            allowed_ocr_ids = None
        add_crop(crops, cid, bbox, allowed_ocr_ids)
    payload = {
        "source_image": SOURCE,
        "ocr_results": "ocr_results.json",
        "output_dir": "crops",
        "debug_sheet": "crop_debug_sheet.png",
        "report": "crop_report.json",
        "ocr_overlap_threshold": 0.35,
        "crops": crops,
    }
    (ROOT / "crop_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_plan() -> None:
    elements: list[dict] = []
    elements.append(shape("page-bg", 0, 0, 1055, 1491, fill="#FFFFFF", stroke="none", stroke_width=0, kind="rect"))
    elements.append(text("title", 165, 14, 730, 48, "新安江水文模型原理与技术路线图", size=25, color=BLUE, bold=True, align="center", margin=0, role="title"))
    elements.append(text("subtitle", 320, 75, 440, 26, "以流域降雨—产流—汇流—预报为主线", size=12.8, color="#333333", align="center", margin=0, role="subtitle"))

    section(elements, 1, "数据基础", 112, 230)
    s1 = [
        (40, "降雨数据", "雨量站、\n面雨量、\n格点降雨", "s1_rain"),
        (238, "蒸发数据", "气象观测、\n蒸发皿/\n潜在蒸散", "s1_evap"),
        (436, "下垫面信息", "DEM、\n土地利用、\n土壤类型", "s1_terrain"),
        (633, "水文观测数据", "水位、\n流量、\n洪水过程", "s1_hydro"),
        (831, "流域基础资料", "河网、\n子流域、\n控制断面", "s1_basin"),
    ]
    for idx, (x, title, body, icon_id) in enumerate(s1, start=1):
        add_card(elements, f"s1-card-{idx}", x, 164, 181, 137, title, body, icon_id, icon_box=(x + 20, 214, 52, 45))
        cx = x + 90.5
        elements.append(line(f"s1-card-{idx}-down", cx, 301, cx, 320, color="#333333", width=0.8))
    elements.append(line("s1-collector", 130, 320, 932, 320, color="#333333", width=0.8))
    elements.append(line("s1-to-s2", 526, 320, 526, 356, color="#111111", width=1.1, end_arrow=True))

    section(elements, 2, "数据预处理与模型准备", 357, 160)
    step2 = [
        (45, 405, 216, "数据质控与缺测处理", "s2_db"),
        (298, 405, 208, "时空尺度统一", "s2_clock"),
        (545, 405, 208, "流域划分与\n河网概化", "s2_basin"),
        (790, 405, 216, "参数初值与\n边界条件设定", "s2_gear"),
    ]
    for idx, (x, y, w, label, icon_id) in enumerate(step2, start=1):
        elements.append(shape(f"s2-step-{idx}", x, y, w, 72, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
        elements.append(pic(f"s2-step-{idx}-icon", f"crops/{icon_id}.png", x + 15, y + 15, 39, 39))
        elements.append(text(f"s2-step-{idx}-label", x + 66, y + 16, w - 78, 42, label, size=10.5, color=TEXT, align="center", margin=0, role="process_label", semantic="\n" in label, wrap=True))
    for idx, (x1, x2) in enumerate([(261, 298), (506, 545), (753, 790)], start=1):
        elements.append(line(f"s2-arrow-{idx}", x1, 441, x2, 441, color="#111111", width=1.1, end_arrow=True))
    elements.append(text("s2-note", 374, 487, 300, 21, "形成模型输入序列与空间拓扑结构", size=9.8, color=TEXT, align="center", margin=0, role="note"))
    elements.append(line("s2-to-s3", 526, 517, 526, 536, color="#111111", width=1.1, end_arrow=True))

    section(elements, 3, "新安江模型核心原理", 537, 319)
    elements.append(pic("s3-rain-icon", "crops/s3_rain.png", 35, 664, 50, 47))
    elements.append(text("s3-rain-label", 35, 724, 66, 23, "降雨输入", size=9.5, align="center", margin=0, role="flow_label"))
    elements.append(line("s3-rain-arrow", 79, 693, 111, 693, color="#111111", width=1.2, end_arrow=True))

    # A
    elements.append(shape("s3-a-box", 112, 587, 155, 216, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(text("s3-a-title", 130, 599, 118, 23, "A. 蒸散发计算", size=9.8, color=BLUE, bold=True, align="center", margin=0, role="card_title"))
    elements.append(pic("s3-a-icon", "crops/s3_evap.png", 149, 638, 87, 58))
    elements.append(line("s3-a-dash", 124, 704, 254, 704, color=LINE_BLUE, width=0.8, dash=True))
    elements.append(text("s3-a-body", 147, 719, 85, 75, "上层张力水\n下层张力水\n深层张力水", size=9.7, align="center", margin=0, role="card_body", semantic=True, wrap=True))
    # B
    elements.append(line("s3-a-to-b", 267, 693, 298, 693, color="#111111", width=1.1, end_arrow=True))
    elements.append(shape("s3-b-box", 298, 587, 160, 216, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(text("s3-b-title", 317, 599, 123, 23, "B. 蓄满产流机制", size=9.8, color=BLUE, bold=True, align="center", margin=0, role="card_title"))
    elements.append(pic("s3-b-icon", "crops/s3_reservoir.png", 348, 639, 63, 54))
    elements.append(line("s3-b-dash", 311, 704, 445, 704, color=LINE_BLUE, width=0.8, dash=True))
    elements.append(text("s3-b-body", 314, 721, 130, 45, "流域蓄水容量曲线\n张力水蓄满后产流", size=9.0, align="center", margin=0, role="card_body", semantic=True, wrap=True))
    # C
    elements.append(line("s3-b-to-c", 458, 693, 491, 693, color="#111111", width=1.1, end_arrow=True))
    elements.append(shape("s3-c-box", 491, 587, 172, 216, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(text("s3-c-title", 500, 600, 155, 21, "C. 三层土壤含水量结构", size=9.0, color=BLUE, bold=True, align="center", margin=0, role="card_title"))
    for i, (yy, fill, left, right) in enumerate([(637, "#EEF5FF", "WU", "上层"), (686, "#DCEBFF", "WL", "下层"), (735, "#C1D7F1", "WD", "深层")], start=1):
        elements.append(shape(f"s3-c-row-{i}", 505, yy, 144, 49, fill=fill, stroke=LINE_BLUE, stroke_width=0.7, kind="rect"))
        elements.append(text(f"s3-c-row-{i}-left", 530, yy + 16, 38, 18, left, size=8.8, align="center", margin=0, role="table_cell"))
        elements.append(text(f"s3-c-row-{i}-right", 574, yy + 16, 44, 18, right, size=8.8, align="center", margin=0, role="table_cell"))
    # D
    elements.append(line("s3-c-to-d", 663, 693, 695, 693, color="#111111", width=1.1, end_arrow=True))
    elements.append(shape("s3-d-box", 695, 587, 157, 216, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(text("s3-d-title", 704, 599, 138, 45, "D. 自由水蓄水库与\n三水源划分", size=9.1, color=BLUE, bold=True, align="center", margin=0, role="card_title", semantic=True, wrap=True))
    for i, (yy, label, code) in enumerate([(672, "地表径流", "RS"), (721, "壤中流", "RI"), (770, "地下径流", "RG")], start=1):
        elements.append(line(f"s3-d-mini-arrow-{i}", 713, yy, 738, yy - 8, color=LINE_BLUE, width=1.0, end_arrow=True))
        elements.append(line(f"s3-d-mini-wave-{i}", 710, yy + 8, 735, yy + 8, color=LINE_BLUE, width=1.0))
        elements.append(text(f"s3-d-row-{i}", 741, yy - 10, 98, 22, f"{label}  {code}", size=8.5, align="left", margin=0, role="flow_label"))
        if i < 3:
            elements.append(line(f"s3-d-dash-{i}", 708, yy + 30, 840, yy + 30, color=LINE_BLUE, width=0.7, dash=True))
    # Key parameters
    elements.append(shape("s3-key-box", 890, 587, 129, 216, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=0.9, kind="roundRect"))
    elements.append(text("s3-key-title", 905, 608, 105, 23, "关键参数示意", size=9.5, bold=True, align="center", margin=0, role="card_title"))
    elements.append(text("s3-key-body", 907, 649, 100, 94, "WM、B、K、\nC、SM、EX、\nKI、KG、\nCS、CI、CG", size=9.0, align="left", margin=0, role="card_body", semantic=True, wrap=True))
    elements.append(text("s3-key-note", 904, 783, 102, 22, "进入汇流演算", size=8.6, align="center", margin=0, role="flow_label"))
    elements.append(pic("s3-key-waves", "crops/s3_waves.png", 889, 812, 126, 27))
    elements.append(line("s3-bottom-dash", 188, 836, 774, 836, color="#111111", width=0.9, dash=True))
    elements.append(line("s3-bottom-up-a", 188, 836, 188, 803, color="#111111", width=0.9, dash=True, end_arrow=True))
    elements.append(line("s3-bottom-up-d", 774, 836, 774, 803, color="#111111", width=0.9, dash=True, end_arrow=True))
    elements.append(text("s3-evap-consume", 315, 821, 123, 22, "蒸散发消耗水量", size=9.0, align="center", margin=0, role="note"))
    elements.append(line("s3-to-s4", 526, 856, 526, 873, color="#111111", width=1.1, end_arrow=True))

    section(elements, 4, "汇流演算", 873, 137)
    s4 = [
        (53, "1. 净雨与产流过程", "降雨扣除与产流量计算", "s4_drops", 274),
        (367, "2. 坡面汇流", "单位线 / 线性水库", "s4_slope", 248),
        (652, "3. 河道汇流", "马斯京根法 /\n滞时演算", "s4_river", 253),
    ]
    for idx, (x, title, body, icon_id, w) in enumerate(s4, start=1):
        elements.append(shape(f"s4-step-{idx}", x, 920, w, 79, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
        elements.append(pic(f"s4-step-{idx}-icon", f"crops/{icon_id}.png", x + 20, 935, 55, 45))
        elements.append(text(f"s4-step-{idx}-title", x + 87, 931, w - 95, 22, title, size=10.0, bold=True, align="center", margin=0, role="card_title"))
        elements.append(text(f"s4-step-{idx}-body", x + 88, 963, w - 98, 34, body, size=8.8, align="center", margin=0, role="card_body", semantic="\n" in body, wrap=True))
    elements.append(shape("s4-output-box", 919, 920, 116, 79, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(pic("s4-output-icon", "crops/s4_gauge.png", 929, 929, 56, 45))
    elements.append(text("s4-output-label", 923, 975, 72, 42, "断面流量\n输出", size=8.5, align="center", margin=0, role="output_label", semantic=True, wrap=True))
    for idx, (x1, x2) in enumerate([(327, 367), (615, 652), (905, 919)], start=1):
        elements.append(line(f"s4-arrow-{idx}", x1, 960, x2, 960, color="#111111", width=1.1, end_arrow=True))
    elements.append(line("s4-to-s5", 526, 1010, 526, 1034, color="#111111", width=1.1, end_arrow=True))

    section(elements, 5, "参数率定与模型检验", 1034, 183)
    elements.append(shape("s5-calib-outer", 40, 1080, 371, 121, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(shape("s5-calib-head", 41, 1081, 369, 30, fill=LIGHT_BLUE, stroke=LINE_BLUE, stroke_width=0.7, kind="rect"))
    elements.append(text("s5-calib-title", 181, 1084, 83, 23, "参数率定", size=10.5, color=BLUE, bold=True, align="center", margin=0, role="group_header"))
    for x in [153, 282]:
        elements.append(line(f"s5-calib-sep-{x}", x, 1111, x, 1200, color="#B9C7DA", width=0.8))
    for idx, (x, icon, label) in enumerate([(48, "s5_bars", "历史洪水选取"), (178, "s5_magnifier", "参数敏感性分析"), (306, "s5_sliders", "自动/人工率定")], start=1):
        elements.append(pic(f"s5-calib-icon-{idx}", f"crops/{icon}.png", x + 28, 1118, 45, 40))
        elements.append(text(f"s5-calib-label-{idx}", x, 1164, 105, 24, label, size=8.7, align="center", margin=0, role="cell_label"))
    elements.append(line("s5-bidirectional", 412, 1140, 505, 1140, color="#111111", width=1.0, dash=True, end_arrow=True, start_arrow=True))
    elements.append(shape("s5-eval-outer", 505, 1080, 512, 121, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(shape("s5-eval-head", 506, 1081, 510, 30, fill=LIGHT_BLUE, stroke=LINE_BLUE, stroke_width=0.7, kind="rect"))
    elements.append(text("s5-eval-title", 665, 1084, 140, 23, "模型验证与评价", size=10.5, color=BLUE, bold=True, align="center", margin=0, role="group_header"))
    for x in [596, 696, 797, 899]:
        elements.append(line(f"s5-eval-sep-{x}", x, 1111, x, 1200, color="#B9C7DA", width=0.8))
    evals = [
        (522, "s5_nse", "NSE"),
        (624, None, "相对误差\nRE"),
        (721, "s5_curve", "洪峰误差"),
        (821, "s5_clock", "峰现时间\n误差"),
        (936, "s5_fit", "过程线拟合"),
    ]
    for idx, (x, icon, label) in enumerate(evals, start=1):
        if icon:
            elements.append(pic(f"s5-eval-icon-{idx}", f"crops/{icon}.png", x + 9, 1118, 48, 38))
        else:
            elements.append(text(f"s5-eval-percent", x + 6, 1113, 44, 42, "%", size=20, color=BLUE, bold=False, align="center", margin=0, role="icon_text"))
        elements.append(text(f"s5-eval-label-{idx}", x - 2, 1156, 72, 44, label, size=8.5, align="center", margin=0, role="cell_label", semantic="\n" in label, wrap=True))
    elements.append(line("s5-to-s6", 526, 1217, 526, 1231, color="#111111", width=1.1, end_arrow=True))

    section(elements, 6, "输出成果与应用", 1231, 248)
    s6 = [
        (40, "1. 流量过程模拟", "s6_flow"),
        (228, "2. 洪峰与峰现时间预报", "s6_peak"),
        (443, "3. 断面来水预报", "s6_section"),
        (637, "4. 洪水预警与\n调度支持", "s6_warn"),
        (842, "5. 可视化分析与\n业务应用", "s6_monitor"),
    ]
    for idx, (x, label, icon) in enumerate(s6, start=1):
        elements.append(shape(f"s6-card-{idx}", x, 1284, 168, 114, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
        elements.append(text(f"s6-card-{idx}-label", x + 10, 1291, 148, 42, label, size=9.5, bold=True, align="center", margin=0, role="card_title", semantic="\n" in label, wrap=True))
        elements.append(pic(f"s6-card-{idx}-icon", f"crops/{icon}.png", x + 28, 1320, 112, 58))
        elements.append(line(f"s6-card-{idx}-down", x + 84, 1398, x + 84, 1410, color="#333333", width=0.8))
    elements.append(line("s6-collector", 124, 1410, 925, 1410, color="#333333", width=0.8))
    elements.append(line("s6-collector-down", 526, 1410, 526, 1423, color="#111111", width=1.1, end_arrow=True))
    elements.append(shape("s6-service-box", 205, 1423, 645, 47, fill="#FFFFFF", stroke=LINE_BLUE, stroke_width=1.0, kind="roundRect"))
    elements.append(pic("s6-people-icon", "crops/s6_people.png", 257, 1429, 58, 33))
    elements.append(text("s6-service-text", 326, 1434, 420, 27, "服务于流域洪水预报、预警与水资源管理", size=13.0, color=BLUE, bold=True, align="center", margin=0, role="conclusion"))

    plan = {
        "title": "新安江水文模型原理与技术路线图",
        "mode": "mixed_reconstruction",
        "slide_width_in": inch(1055),
        "slide_height_in": inch(1491),
        "source_image": SOURCE,
        "source_ocr": "ocr_results.json",
        "notes": [
            "Single-page image conversion.",
            "Readable Chinese text was manually corrected from the source image and rebuilt as native PowerPoint text.",
            "Simple panels, cards, separators and arrows were rebuilt as native PowerPoint shapes and lines.",
            "Small source-specific icons were preserved as tight local source crops."
        ],
        "limitations": [
            "Small icons are source-derived PNG crops, not vector redraws.",
            "The source has a subtle bitmap background texture; the editable version uses a clean white/native shape background."
        ],
        "strategy_review": {
            "regions": [
                {
                    "id": "small-blue-icon-system",
                    "visual_type": "product_photo_or_detailed_icon_group",
                    "source_bbox_px": [40, 160, 980, 1230],
                    "expected_strategy": "source_crop",
                    "actual_strategy": "source_icon_crop",
                    "evidence_element_ids": [element["id"] for element in elements if element.get("type") == "picture"],
                    "text_overlay_element_ids": [element["id"] for element in elements if element.get("type") == "text"],
                    "requires_text_overlay": False,
                    "reason": "Icons are small source-specific line drawings; cropping them avoids visual drift while all readable labels are native editable text."
                }
            ]
        },
        "protected_visual_anchors": [
            {"id": "section-flow-arrows", "kind": "arrow", "preservation_status": "reviewed", "strategy": "native_line", "reason": "Main reading order arrows rebuilt as native connector lines."},
            {"id": "section-cards-and-panels", "kind": "card_edge", "preservation_status": "reviewed", "strategy": "native_shape", "reason": "Panel and card boundaries rebuilt as native rectangles."}
        ],
        "non_text_anchor_preservation_review": {"status": "reviewed", "missing_required_anchors": False, "issues": []},
        "slides": [{"id": "slide-1", "elements": elements}],
    }
    (ROOT / "source_reconstruction_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    review = {
        "source_ocr": "ocr_results.json",
        "status": "manual_review_complete",
        "records": 96,
        "correction_policy": "OCR geometry retained for provenance; mojibake OCR text was replaced with manually reviewed Chinese text from the source image.",
        "accepted_to_pptx": "All meaningful visible source text was reconstructed as editable PowerPoint text.",
    }
    (ROOT / "ocr_review_manifest.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    build_crop_manifest()
    build_plan()
    print(json.dumps({"crop_manifest": "crop_manifest.json", "plan": "source_reconstruction_plan.json"}, ensure_ascii=False))
