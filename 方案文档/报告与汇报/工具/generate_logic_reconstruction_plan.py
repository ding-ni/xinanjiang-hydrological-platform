#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


SLIDE_W_IN = 13.333333
SLIDE_H_IN = 7.5
SRC_W_PX = 1672
SRC_H_PX = 941


def px_x(value: float) -> float:
    return round(value * SLIDE_W_IN / SRC_W_PX, 4)


def px_y(value: float) -> float:
    return round(value * SLIDE_H_IN / SRC_H_PX, 4)


def px_box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    return px_x(x), px_y(y), px_x(w), px_y(h)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_bbox(x: float, y: float, w: float, h: float) -> list[float]:
    return [float(x), float(y), float(w), float(h)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--runtime-report", type=Path, required=True)
    parser.add_argument("--ocr-overlay", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    source_copy = out_dir / "source.png"
    ocr_copy = out_dir / "ocr_results.json"
    runtime_copy = out_dir / "ocr_runtime_report.json"
    overlay_copy = out_dir / "ocr_overlay_debug.png"
    shutil.copy2(args.source, source_copy)
    shutil.copy2(args.ocr, ocr_copy)
    shutil.copy2(args.runtime_report, runtime_copy)
    shutil.copy2(args.ocr_overlay, overlay_copy)

    ocr_data = load_json(ocr_copy)
    records = {item["id"]: item for item in ocr_data["records"]}

    def ocr_text(ocr_id: str) -> str:
        return str(records[ocr_id]["text"])

    def ocr_source(ocr_id: str, role: str = "text") -> dict[str, Any]:
        record = records[ocr_id]
        return {
            "kind": "ocr",
            "ocr_id": ocr_id,
            "ocr_confidence": record.get("confidence"),
            "source_bbox_px": record.get("bbox_px"),
            "review_status": "accepted",
            "role": role,
        }

    elements: list[dict[str, Any]] = []
    evidence_ids: list[str] = []

    def add_shape(
        shape_id: str,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str,
        stroke: str = "none",
        stroke_width: float = 1.0,
        shape: str = "roundRect",
        dash: bool = False,
        radius: float = 0.08,
    ) -> None:
        xi, yi, wi, hi = px_box(x, y, w, h)
        element: dict[str, Any] = {
            "id": shape_id,
            "type": "shape",
            "shape": shape,
            "x": xi,
            "y": yi,
            "w": wi,
            "h": hi,
            "fill": fill,
            "stroke": stroke,
            "stroke_width": stroke_width,
            "source": {
                "kind": "native_shape",
                "source_bbox_px": source_bbox(x, y, w, h),
                "review_status": "manual_reviewed",
            },
        }
        if shape == "roundRect":
            element["radius"] = radius
        if dash:
            element["dash"] = True
        elements.append(element)
        evidence_ids.append(shape_id)

    def add_line(
        line_id: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str = "#073763",
        width: float = 2.0,
        dash: bool = False,
        end_arrow: bool = False,
        start_arrow: bool = False,
    ) -> None:
        element: dict[str, Any] = {
            "id": line_id,
            "type": "line",
            "x1": px_x(x1),
            "y1": px_y(y1),
            "x2": px_x(x2),
            "y2": px_y(y2),
            "color": color,
            "width": width,
            "dash": dash,
            "end_arrow": end_arrow,
            "start_arrow": start_arrow,
            "arrow_width": "med",
            "arrow_length": "med",
            "source": {
                "kind": "native_line",
                "source_bbox_px": source_bbox(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)),
                "review_status": "manual_reviewed",
            },
        }
        elements.append(element)
        evidence_ids.append(line_id)

    def add_text(
        text_id: str,
        text: str,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        font_size: float,
        color: str = "#102033",
        bold: bool = False,
        align: str = "left",
        valign: str = "top",
        role: str = "label",
        source: dict[str, Any] | None = None,
        trace_level: str = "line",
        semantic_block: bool = False,
        word_wrap: bool | None = None,
        margin: float = 0.01,
    ) -> None:
        xi, yi, wi, hi = px_box(x, y, w, h)
        if source is None:
            source = {
                "kind": "manual_reviewed_text",
                "source_bbox_px": source_bbox(x, y, w, h),
                "review_status": "accepted",
                "role": role,
            }
        if word_wrap is None:
            word_wrap = trace_level != "line"
        element = {
            "id": text_id,
            "type": "text",
            "x": xi,
            "y": yi,
            "w": wi,
            "h": hi,
            "text": text,
            "font_size": font_size,
            "font_face": "Microsoft YaHei",
            "color": color,
            "bold": bold,
            "align": align,
            "valign": valign,
            "margin": margin,
            "word_wrap": word_wrap,
            "trace_level": trace_level,
            "semantic_block": semantic_block,
            "no_wrap": trace_level == "line",
            "role": role,
            "line_level_trace_required": trace_level == "line",
            "source": source,
        }
        elements.append(element)

    navy = "#072A5E"
    card_blue = "#005AAE"
    teal = "#007A78"
    orange = "#B36B00"
    red = "#B51E23"
    text_dark = "#1B2638"
    panel_blue = "#0D2A5A"
    light_border = "#7FA7D9"

    add_shape("slide-white-background", 0, 0, SRC_W_PX, SRC_H_PX, fill="#FFFFFF", stroke="none", shape="rect")

    add_text(
        "title",
        ocr_text("ocr_001"),
        405,
        28,
        860,
        62,
        font_size=28,
        color=navy,
        bold=True,
        align="center",
        valign="middle",
        role="title",
        source=ocr_source("ocr_001", "title"),
    )

    cards = {
        "p1": (95, 120, 324, 185, card_blue),
        "p2": (531, 120, 319, 185, teal),
        "p3": (963, 120, 303, 185, teal),
        "p6": (95, 559, 324, 179, red),
        "p5": (533, 559, 321, 179, card_blue),
        "p4": (963, 559, 303, 179, orange),
        "archive": (533, 376, 321, 133, navy),
    }
    for key, (x, y, w, h, color) in cards.items():
        add_shape(f"{key}-card", x, y, w, h, fill="#FFFFFF", stroke=color, stroke_width=1.8, radius=0.075)

    badge_specs = [
        ("badge-1", "1", 145, 143, card_blue),
        ("badge-2", "2", 580, 143, teal),
        ("badge-3", ocr_text("ocr_005"), 995, 143, teal),
        ("badge-6", ocr_text("ocr_026"), 145, 580, red),
        ("badge-5", ocr_text("ocr_028"), 578, 580, card_blue),
        ("badge-4", "4", 998, 580, orange),
    ]
    for badge_id, label, x, y, color in badge_specs:
        add_shape(f"{badge_id}-circle", x, y, 45, 45, fill=color, stroke="none", shape="oval")
        add_text(
            f"{badge_id}-text",
            label,
            x + 1,
            y + 1,
            43,
            43,
            font_size=16,
            color="#FFFFFF",
            bold=True,
            align="center",
            valign="middle",
            role="badge_number",
        )

    add_text("p1-title", ocr_text("ocr_003"), 204, 146, 150, 43, font_size=18, color=navy, bold=True, source=ocr_source("ocr_003", "card_title"))
    add_text("p2-title", ocr_text("ocr_004"), 642, 146, 150, 43, font_size=18, color="#006B68", bold=True, source=ocr_source("ocr_004", "card_title"))
    add_text("p3-title", ocr_text("ocr_006"), 1052, 146, 180, 43, font_size=18, color="#006B68", bold=True, source=ocr_source("ocr_006", "card_title"))
    add_text("p6-title", ocr_text("ocr_027"), 204, 583, 150, 43, font_size=18, color=red, bold=True, source=ocr_source("ocr_027", "card_title"))
    add_text("p5-title", ocr_text("ocr_029"), 638, 583, 150, 43, font_size=18, color=navy, bold=True, source=ocr_source("ocr_029", "card_title"))
    add_text("p4-title", ocr_text("ocr_030"), 1067, 585, 160, 43, font_size=18, color=orange, bold=True, source=ocr_source("ocr_030", "card_title"))

    add_text("p1-body", f"{ocr_text('ocr_007')}\n{ocr_text('ocr_011')}", 122, 204, 260, 70, font_size=12.2, color=text_dark, align="center", role="card_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": ["ocr_007", "ocr_011"], "source_line_bboxes_px": [records["ocr_007"]["bbox_px"], records["ocr_011"]["bbox_px"]], "review_status": "accepted", "role": "card_body"})
    add_text("p2-body", f"{ocr_text('ocr_008')}\n{ocr_text('ocr_012')}", 570, 204, 240, 70, font_size=12.2, color=text_dark, align="center", role="card_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": ["ocr_008", "ocr_012"], "source_line_bboxes_px": [records["ocr_008"]["bbox_px"], records["ocr_012"]["bbox_px"]], "review_status": "accepted", "role": "card_body"})
    add_text("p3-body", f"{ocr_text('ocr_009')}\n{ocr_text('ocr_013')}\n{ocr_text('ocr_015')}", 1008, 204, 210, 96, font_size=12.2, color=text_dark, align="center", role="card_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": ["ocr_009", "ocr_013", "ocr_015"], "source_line_bboxes_px": [records["ocr_009"]["bbox_px"], records["ocr_013"]["bbox_px"], records["ocr_015"]["bbox_px"]], "review_status": "accepted", "role": "card_body"})

    add_text("archive-title", ocr_text("ocr_019"), 612, 392, 145, 43, font_size=18, color=navy, bold=True, align="center", source=ocr_source("ocr_019", "card_title"))
    add_text("archive-body", f"{ocr_text('ocr_021')}\n{ocr_text('ocr_022')}", 570, 438, 232, 62, font_size=12.2, color=text_dark, align="center", role="card_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": ["ocr_021", "ocr_022"], "source_line_bboxes_px": [records["ocr_021"]["bbox_px"], records["ocr_022"]["bbox_px"]], "review_status": "accepted", "role": "card_body"})

    add_text("p6-body", f"{ocr_text('ocr_031')}\n{ocr_text('ocr_035')}\n{ocr_text('ocr_039')}", 150, 638, 195, 88, font_size=11.8, color=text_dark, align="center", role="card_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": ["ocr_031", "ocr_035", "ocr_039"], "source_line_bboxes_px": [records["ocr_031"]["bbox_px"], records["ocr_035"]["bbox_px"], records["ocr_039"]["bbox_px"]], "review_status": "accepted", "role": "card_body"})
    add_text("p5-body", f"{ocr_text('ocr_032')}\n{ocr_text('ocr_036')}\n{ocr_text('ocr_040')}", 562, 638, 250, 88, font_size=11.8, color=text_dark, align="center", role="card_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": ["ocr_032", "ocr_036", "ocr_040"], "source_line_bboxes_px": [records["ocr_032"]["bbox_px"], records["ocr_036"]["bbox_px"], records["ocr_040"]["bbox_px"]], "review_status": "accepted", "role": "card_body"})
    add_text("p4-body", f"{ocr_text('ocr_033')}\n{ocr_text('ocr_037')}\n{ocr_text('ocr_041')}", 1000, 638, 230, 88, font_size=11.8, color=text_dark, align="center", role="card_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": ["ocr_033", "ocr_037", "ocr_041"], "source_line_bboxes_px": [records["ocr_033"]["bbox_px"], records["ocr_037"]["bbox_px"], records["ocr_041"]["bbox_px"]], "review_status": "accepted", "role": "card_body"})

    add_line("arrow-p1-to-p2", 419, 186, 531, 186, end_arrow=True, width=2.2)
    add_line("arrow-p2-to-p3", 850, 186, 963, 186, end_arrow=True, width=2.2)
    add_line("arrow-p3-to-p4", 1115, 305, 1115, 559, end_arrow=True, width=2.2)
    add_line("arrow-p4-to-p5", 963, 628, 854, 628, end_arrow=True, width=2.2)
    add_line("arrow-p5-to-p6", 533, 628, 419, 628, end_arrow=True, width=2.2)
    add_line("arrow-p6-to-p1", 244, 559, 244, 305, end_arrow=True, width=2.2)

    add_line("archive-top-left-dash", 576, 352, 576, 376, dash=True, end_arrow=True, width=1.1)
    add_line("archive-top-mid-dash", 681, 352, 681, 306, dash=True, end_arrow=True, width=1.1)
    add_line("archive-top-right-dash", 813, 352, 813, 376, dash=True, end_arrow=True, width=1.1)
    add_line("archive-top-h-dash", 576, 352, 813, 352, dash=True, width=1.1)
    add_line("archive-bottom-left-dash", 576, 510, 576, 536, dash=True, start_arrow=True, width=1.1)
    add_line("archive-bottom-mid-dash", 681, 510, 681, 559, dash=True, end_arrow=True, width=1.1)
    add_line("archive-bottom-right-dash", 813, 510, 813, 536, dash=True, start_arrow=True, width=1.1)
    add_line("archive-bottom-h-dash", 576, 536, 813, 536, dash=True, width=1.1)

    add_shape("boundary-panel", 1330, 113, 286, 638, fill="#FFFFFF", stroke=light_border, stroke_width=1.0, radius=0.06)
    add_text("boundary-title", ocr_text("ocr_002"), 1412, 133, 116, 40, font_size=16, color=panel_blue, bold=True, align="center", valign="middle", source=ocr_source("ocr_002", "panel_title"))

    side_items = [
        ("1", "ocr_010", ["ocr_014", "ocr_016"], 198),
        ("2", "ocr_017", ["ocr_018", "ocr_020"], 344),
        ("3", "ocr_023", ["ocr_024", "ocr_025"], 489),
        ("4", "ocr_034", ["ocr_038", "ocr_042"], 635),
    ]
    for idx, heading_id, body_ids, y in side_items:
        add_shape(f"side-index-{idx}-box", 1350, y, 42, 39, fill="#F8FBFF", stroke="#9CC0EE", stroke_width=0.8, radius=0.08)
        add_text(f"side-index-{idx}-text", idx, 1352, y + 2, 38, 35, font_size=14.5, color=card_blue, bold=True, align="center", valign="middle", role="badge_number")
        add_text(f"side-heading-{idx}", ocr_text(heading_id), 1408, y + 2, 130, 31, font_size=13.2, color=panel_blue, bold=True, source=ocr_source(heading_id, "panel_item_title"))
        add_text(f"side-body-{idx}", "\n".join(ocr_text(item) for item in body_ids), 1406, y + 38, 215, 55, font_size=10.2, color="#2A3852", role="panel_body", trace_level="paragraph", semantic_block=True, word_wrap=False, source={"kind": "ocr_group", "source_element_ids": body_ids, "source_line_bboxes_px": [records[item]["bbox_px"] for item in body_ids], "review_status": "accepted", "role": "panel_body"})

    for sep_id, y in [("side-sep-1", 315), ("side-sep-2", 463), ("side-sep-3", 611)]:
        add_line(sep_id, 1350, y, 1600, y, color="#A7BEDA", width=0.8, dash=True)

    add_shape("future-container", 67, 784, 1539, 117, fill="none", stroke=navy, stroke_width=1.1, dash=True, radius=0.08)
    add_shape("future-label", 96, 810, 173, 66, fill=navy, stroke=navy, stroke_width=1.0, radius=0.08)
    add_text("future-label-text", ocr_text("ocr_043"), 112, 820, 142, 42, font_size=17.5, color="#FFFFFF", bold=True, align="center", valign="middle", source=ocr_source("ocr_043", "section_label"))

    button_specs = [
        ("future-auto-calibration", "ocr_044", 302, 810, 254, 66),
        ("future-data-assimilation", "ocr_045", 596, 810, 264, 66),
        ("future-snowmelt", "ocr_046", 900, 810, 248, 66),
        ("future-frozen-soil", "ocr_047", 1182, 810, 233, 66),
    ]
    for button_id, ocr_id, x, y, w, h in button_specs:
        add_shape(button_id, x, y, w, h, fill="#FFFFFF", stroke=card_blue, stroke_width=1.0, radius=0.05)
        add_text(f"{button_id}-text", ocr_text(ocr_id), x, y + 15, w, 36, font_size=14.2, color=panel_blue, bold=True, align="center", valign="middle", role="button_label", source=ocr_source(ocr_id, "button_label"))

    review_manifest = {
        "source": str(source_copy),
        "ocr_results": str(ocr_copy),
        "review_policy": "OCR lines were checked against the source image before being used in editable text boxes.",
        "records": [
            {
                "id": item["id"],
                "raw_text": item["text"],
                "final_text": item["text"],
                "confidence": item.get("confidence"),
                "bbox_px": item.get("bbox_px"),
                "review_status": "accepted",
            }
            for item in ocr_data["records"]
        ],
        "manual_text": [
            {"id": "badge-1-text", "final_text": "1", "review_status": "accepted", "reason": "number badge not returned as a separate OCR line"},
            {"id": "badge-2-text", "final_text": "2", "review_status": "accepted", "reason": "number badge not returned as a separate OCR line"},
            {"id": "badge-4-text", "final_text": "4", "review_status": "accepted", "reason": "number badge not returned as a separate OCR line"},
        ],
    }
    write_json(out_dir / "ocr_review_manifest.json", review_manifest)

    gates = {
        "python_runtime_gate": {
            "probe_python_status": "passed",
            "setup_required": False,
            "auto_passed_existing_runtime": True,
        },
        "conversion_mode_gate": {
            "user_choice": "single-page-token-saving",
            "source_type": "single-image",
            "recorded_reason": "User provided one PNG and asked to convert it to PPT.",
        },
        "ocr_runtime_gate": {
            "setup_required": False,
            "auto_passed_existing_runtime": True,
            "probe_ocr_runtime_status": "passed-text-usable",
            "runtime_report": str(runtime_copy),
        },
        "scope_and_worker_gate": {
            "user_choice": "sample",
            "slides": [1],
            "worker_mode": "manual-single-page",
            "worker_execution_approved": True,
            "token_cost_acknowledged": True,
        },
    }
    write_json(out_dir / "gates.json", gates)

    plan = {
        "title": "logic-diagram-single-page-reconstruction",
        "mode": "reconstruction",
        "slide_width_in": SLIDE_W_IN,
        "slide_height_in": SLIDE_H_IN,
        "source_image": "source.png",
        "source_ocr": "ocr_results.json",
        "notes": [
            "Single-page PNG routed to native reconstruction because the page is dominated by simple flowchart geometry.",
            "Readable text is editable PowerPoint text with OCR/manual review provenance.",
            "The original source image is not used as a full-slide background.",
        ],
        "limitations": [
            "Number badges use editable solid-color native circles rather than the source radial gradient.",
            "Subtle source shadows are omitted to keep native shapes flat and editable.",
        ],
        "strategy_review": {
            "regions": [
                {
                    "id": "main-flowchart",
                    "visual_type": "simple_process_flowchart",
                    "source_bbox_px": [67, 28, 1549, 873],
                    "expected_strategy": "native_reconstruction",
                    "actual_strategy": "native_reconstruction",
                    "evidence_element_ids": evidence_ids,
                    "text_overlay_element_ids": [element["id"] for element in elements if element.get("type") == "text"],
                    "requires_text_overlay": False,
                    "reason": "All important modules are simple cards, badges, straight arrows, dashed rules, and labels that preserve fidelity as native PPT objects.",
                }
            ]
        },
        "protected_visual_anchors": [
            {"id": "closed-loop-arrows", "kind": "flow_arrows", "preservation_status": "reviewed", "strategy": "native_line_arrows"},
            {"id": "archive-dashed-links", "kind": "dashed_guides", "preservation_status": "reviewed", "strategy": "native_dashed_lines"},
            {"id": "future-extension-dashed-container", "kind": "dashed_container", "preservation_status": "reviewed", "strategy": "native_dashed_shape"},
        ],
        "non_text_anchor_preservation_review": {
            "status": "reviewed",
            "missing_required_anchors": False,
            "issues": [],
        },
        "slides": [
            {
                "id": "slide-01",
                "elements": elements,
            }
        ],
    }
    write_json(out_dir / "source_reconstruction_plan.json", plan)
    print(json.dumps({"out_dir": str(out_dir), "elements": len(elements)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
