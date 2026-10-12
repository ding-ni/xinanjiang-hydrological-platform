"""Topology visualization export for routing products."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .io import read_json, write_json


def write_topology_overview_html(
    *,
    topology_json: str | Path,
    control_point_json: str | Path | None,
    out_html: str | Path,
    summary_out: str | Path | None = None,
) -> dict[str, Any]:
    topology = read_json(topology_json)
    control = read_json(control_point_json) if control_point_json else None
    payload = build_topology_payload(topology, control)

    output_path = Path(out_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(_build_html(payload))
    if summary_out:
        write_json(summary_out, payload["summary"])
    return payload["summary"]


def build_topology_payload(
    topology: dict[str, Any],
    control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reaches = list(topology.get("reaches", []))
    units = list(topology.get("units", []))
    unit_by_reach = {
        str(unit.get("reach_id")): unit
        for unit in units
        if unit.get("reach_id") not in (None, "")
    }
    reach_by_id = {str(reach["reach_id"]): reach for reach in reaches}

    upstream_reaches: dict[str, list[str]] = {reach_id: [] for reach_id in reach_by_id}
    for reach in reaches:
        downstream = reach.get("downstream_reach_id")
        if downstream:
            upstream_reaches.setdefault(str(downstream), []).append(str(reach["reach_id"]))

    upstream_units: dict[str, list[str]] = {str(unit["unit_id"]): [] for unit in units}
    for unit in units:
        downstream = unit.get("downstream_unit_id")
        if downstream:
            upstream_units.setdefault(str(downstream), []).append(str(unit["unit_id"]))

    control_reach_id = None
    control_point = None
    if control:
        snapped = control.get("snapped", {})
        control_reach_id = snapped.get("reach_id")
        control_point = {
            "control_id": control.get("control_id", ""),
            "name": control.get("name", ""),
            "reach_id": control_reach_id,
            "cell": snapped.get("cell"),
            "distance_m": _float_or_none(snapped.get("distance_m")),
            "accumulation_area_km2": _float_or_none(snapped.get("accumulation_area_km2")),
            "elevation_m": _float_or_none(snapped.get("elevation_m")),
            "lon": _float_or_none(snapped.get("lon")),
            "lat": _float_or_none(snapped.get("lat")),
        }

    view_bounds = _cell_bounds(reaches, units, control_point)
    reach_rows = []
    for reach in reaches:
        reach_id = str(reach["reach_id"])
        unit = unit_by_reach.get(reach_id, {})
        reach_rows.append(
            {
                "id": reach_id,
                "downstream": _none_to_blank(reach.get("downstream_reach_id")),
                "upstream": sorted(upstream_reaches.get(reach_id, [])),
                "length_km": float(reach.get("length_km", 0.0) or 0.0),
                "cell_count": int(reach.get("cell_count", 0) or 0),
                "start_cell": reach.get("start_cell"),
                "end_cell": reach.get("end_cell"),
                "unit_id": unit.get("unit_id", ""),
                "unit_area_km2": float(unit.get("area_km2", 0.0) or 0.0),
                "is_terminal": not bool(reach.get("downstream_reach_id")),
                "is_control": reach_id == control_reach_id,
            }
        )

    unit_rows = []
    for unit in units:
        unit_id = str(unit["unit_id"])
        unit_rows.append(
            {
                "id": unit_id,
                "reach_id": _none_to_blank(unit.get("reach_id")),
                "downstream": _none_to_blank(unit.get("downstream_unit_id")),
                "upstream": sorted(upstream_units.get(unit_id, [])),
                "zone_type": unit.get("zone_type", ""),
                "area_km2": float(unit.get("area_km2", 0.0) or 0.0),
                "elevation_mean": _float_or_none(unit.get("elevation_mean")),
                "slope_mean": _float_or_none(unit.get("slope_mean")),
                "aspect_mean": _float_or_none(unit.get("aspect_mean")),
                "glacier_fraction": _float_or_none(unit.get("glacier_fraction")),
                "moraine_fraction": _float_or_none(unit.get("moraine_fraction")),
                "outlet_cell": unit.get("outlet_cell_id"),
                "cell_count": int(unit.get("cell_count", 0) or 0),
                "is_control": unit.get("reach_id") == control_reach_id,
            }
        )

    total_area = sum(unit["area_km2"] for unit in unit_rows)
    total_length = sum(reach["length_km"] for reach in reach_rows)
    terminal_reaches = [reach["id"] for reach in reach_rows if reach["is_terminal"]]
    summary = {
        "basin_id": topology.get("basin_id", ""),
        "spatial_version": topology.get("spatial_version", ""),
        "cell_size_m": _float_or_none(topology.get("cell_size_m")),
        "reach_count": len(reach_rows),
        "unit_count": len(unit_rows),
        "total_unit_area_km2": round(total_area, 6),
        "total_reach_length_km": round(total_length, 6),
        "terminal_reaches": terminal_reaches,
        "control_id": control_point.get("control_id") if control_point else None,
        "control_reach_id": control_reach_id,
        "control_snap_distance_m": control_point.get("distance_m") if control_point else None,
        "control_accumulation_area_km2": control_point.get("accumulation_area_km2")
        if control_point
        else None,
    }
    return {
        "summary": summary,
        "bounds": view_bounds,
        "reaches": reach_rows,
        "units": unit_rows,
        "control": control_point,
    }


def _cell_bounds(
    reaches: list[dict[str, Any]],
    units: list[dict[str, Any]],
    control_point: dict[str, Any] | None,
) -> dict[str, float]:
    rows: list[float] = []
    cols: list[float] = []
    for reach in reaches:
        for key in ("start_cell", "end_cell"):
            cell = reach.get(key)
            if cell and len(cell) >= 2:
                rows.append(float(cell[0]))
                cols.append(float(cell[1]))
    for unit in units:
        cell = unit.get("outlet_cell_id")
        if cell and len(cell) >= 2:
            rows.append(float(cell[0]))
            cols.append(float(cell[1]))
    if control_point and control_point.get("cell"):
        cell = control_point["cell"]
        rows.append(float(cell[0]))
        cols.append(float(cell[1]))

    if not rows or not cols:
        return {"min_row": 0.0, "max_row": 1.0, "min_col": 0.0, "max_col": 1.0}
    padding = 10.0
    return {
        "min_row": min(rows) - padding,
        "max_row": max(rows) + padding,
        "min_col": min(cols) - padding,
        "max_col": max(cols) + padding,
    }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _none_to_blank(value: Any) -> str:
    return "" if value in (None, "") else str(value)


def _build_html(payload: dict[str, Any]) -> str:
    json_payload = json.dumps(payload, ensure_ascii=False)
    title = "南安溪-徐畈汇流拓扑可视化"
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg: #f6f8fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d4dbe7;
      --reach: #2f6fb3;
      --unit: #47a567;
      --control: #c2410c;
      --terminal: #7c3aed;
      --water: #e9f3ff;
      --shadow: rgba(23, 32, 51, 0.08);
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }
    main {
      width: min(1380px, calc(100% - 32px));
      margin: 0 auto;
      padding: 22px 0 28px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 24px;
      font-weight: 600;
    }
    .subtitle {
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 14px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      box-shadow: 0 8px 24px var(--shadow);
    }
    .stat span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }
    .stat strong {
      font-size: 18px;
      font-weight: 600;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 14px;
      align-items: start;
    }
    .map-wrap,
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 24px var(--shadow);
    }
    .map-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px 0;
      color: var(--muted);
      font-size: 13px;
    }
    .legend {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .swatch-line {
      width: 24px;
      height: 3px;
      border-radius: 999px;
      background: var(--reach);
    }
    .swatch-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--unit);
    }
    .swatch-control {
      width: 12px;
      height: 12px;
      transform: rotate(45deg);
      background: var(--control);
    }
    svg {
      display: block;
      width: 100%;
      height: auto;
    }
    .reach-line {
      stroke: var(--reach);
      stroke-width: 3;
      fill: none;
      stroke-linecap: round;
      opacity: 0.78;
      cursor: pointer;
    }
    .reach-line.control {
      stroke: var(--control);
      stroke-width: 5;
      opacity: 1;
    }
    .reach-line.terminal {
      stroke: var(--terminal);
      stroke-width: 4;
    }
    .reach-line.selected {
      stroke-width: 7;
      opacity: 1;
      filter: drop-shadow(0 3px 4px rgba(23, 32, 51, 0.25));
    }
    .unit-dot {
      fill: rgba(71, 165, 103, 0.72);
      stroke: #1d6f3b;
      stroke-width: 1.4;
      cursor: pointer;
    }
    .unit-dot.control {
      fill: rgba(194, 65, 12, 0.75);
      stroke: var(--control);
      stroke-width: 2.2;
    }
    .unit-dot.selected {
      stroke: var(--ink);
      stroke-width: 3;
    }
    .control-marker {
      fill: var(--control);
      stroke: #fff7ed;
      stroke-width: 2;
    }
    .label {
      font-size: 11px;
      fill: var(--ink);
      paint-order: stroke;
      stroke: var(--panel);
      stroke-width: 3px;
      stroke-linejoin: round;
      pointer-events: none;
    }
    .grid {
      stroke: #e8edf5;
      stroke-width: 1;
    }
    .panel {
      padding: 14px;
    }
    .panel h2 {
      margin: 0 0 8px;
      font-size: 18px;
      font-weight: 600;
    }
    .panel p {
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 13px;
    }
    .select-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 12px;
    }
    select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: var(--panel);
      color: var(--ink);
      font: inherit;
    }
    .detail {
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    .detail-title {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .detail-title strong {
      font-size: 18px;
      font-weight: 600;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 8px;
      background: var(--water);
      color: #1d4f83;
      font-size: 12px;
      white-space: nowrap;
    }
    .kv {
      display: grid;
      grid-template-columns: 132px 1fr;
      gap: 7px 10px;
      font-size: 13px;
    }
    .kv div:nth-child(odd) {
      color: var(--muted);
    }
    .list {
      margin: 12px 0 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 6px;
      font-size: 13px;
    }
    .list li {
      padding: 7px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
    }
    @media (max-width: 900px) {
      .layout {
        grid-template-columns: 1fr;
      }
      .panel {
        order: -1;
      }
    }
    @media (max-width: 560px) {
      main {
        width: min(100% - 20px, 1380px);
      }
      h1 {
        font-size: 20px;
      }
      .map-toolbar,
      .select-row {
        display: block;
      }
      .legend {
        margin-top: 8px;
      }
      select + select {
        margin-top: 8px;
      }
      .kv {
        grid-template-columns: 1fr;
      }
      .kv div:nth-child(even) {
        margin-bottom: 4px;
      }
    }
  </style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <p class="subtitle">DEM 裁剪拓扑结果：河段方向、汇流单元面积、上下游关系与徐畈控制断面挂接。</p>
  <section class="stats" id="stats"></section>
  <section class="layout">
    <div class="map-wrap">
      <div class="map-toolbar">
        <span>坐标为 DEM 栅格行列号近似位置</span>
        <div class="legend">
          <span class="legend-item"><span class="swatch-line"></span>河段</span>
          <span class="legend-item"><span class="swatch-dot"></span>汇流单元出口</span>
          <span class="legend-item"><span class="swatch-control"></span>徐畈控制断面</span>
        </div>
      </div>
      <svg id="topology-map" viewBox="0 0 920 660" role="img" aria-label="南安溪徐畈汇流拓扑图"></svg>
    </div>
    <aside class="panel">
      <h2>拓扑详情</h2>
      <p>点击图上的河段或汇流单元，查看面积、长度和上下游关系。</p>
      <div class="select-row">
        <select id="reach-select" aria-label="选择河段"></select>
        <select id="unit-select" aria-label="选择汇流单元"></select>
      </div>
      <div class="detail" id="detail"></div>
    </aside>
  </section>
</main>
<script>
const payload = __PAYLOAD__;
const svg = document.getElementById("topology-map");
const width = 920;
const height = 660;
const margin = { top: 34, right: 34, bottom: 42, left: 48 };
const plotW = width - margin.left - margin.right;
const plotH = height - margin.top - margin.bottom;
let selected = { type: "reach", id: payload.summary.control_reach_id || payload.reaches[0]?.id };

function fmt(value, digits = 3) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return value.toFixed(digits);
  return String(value);
}

function cellX(cell) {
  return margin.left + ((cell[1] - payload.bounds.min_col) / (payload.bounds.max_col - payload.bounds.min_col)) * plotW;
}

function cellY(cell) {
  return margin.top + ((cell[0] - payload.bounds.min_row) / (payload.bounds.max_row - payload.bounds.min_row)) * plotH;
}

function svgEl(name, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function renderStats() {
  const s = payload.summary;
  const stats = [
    ["汇流单元", s.unit_count],
    ["河段", s.reach_count],
    ["总面积 km2", fmt(s.total_unit_area_km2, 2)],
    ["总河长 km", fmt(s.total_reach_length_km, 2)],
    ["出口河段", s.terminal_reaches.join(", ")],
    ["徐畈挂接", `${s.control_id || "-"} -> ${s.control_reach_id || "-"}`]
  ];
  document.getElementById("stats").innerHTML = stats.map(([label, value]) => `
    <article class="stat"><span>${label}</span><strong>${value}</strong></article>
  `).join("");
}

function renderSelects() {
  const reachSelect = document.getElementById("reach-select");
  const unitSelect = document.getElementById("unit-select");
  reachSelect.innerHTML = `<option value="">选择河段</option>` + payload.reaches.map(r => {
    const suffix = r.is_control ? " · 徐畈" : r.is_terminal ? " · 出口" : "";
    return `<option value="${r.id}">${r.id}${suffix}</option>`;
  }).join("");
  unitSelect.innerHTML = `<option value="">选择单元</option>` + payload.units.map(u => {
    const suffix = u.is_control ? " · 徐畈" : "";
    return `<option value="${u.id}">${u.id}${suffix}</option>`;
  }).join("");
  reachSelect.addEventListener("change", () => {
    if (reachSelect.value) {
      selected = { type: "reach", id: reachSelect.value };
      unitSelect.value = "";
      render();
    }
  });
  unitSelect.addEventListener("change", () => {
    if (unitSelect.value) {
      selected = { type: "unit", id: unitSelect.value };
      reachSelect.value = "";
      render();
    }
  });
}

function renderMap() {
  svg.innerHTML = "";
  const defs = svgEl("defs");
  const marker = svgEl("marker", {
    id: "arrow",
    markerWidth: "10",
    markerHeight: "10",
    refX: "8",
    refY: "3",
    orient: "auto",
    markerUnits: "strokeWidth"
  });
  marker.appendChild(svgEl("path", { d: "M0,0 L0,6 L9,3 z", fill: "var(--reach)" }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  for (let i = 0; i <= 5; i += 1) {
    const x = margin.left + (plotW / 5) * i;
    svg.appendChild(svgEl("line", { x1: x, y1: margin.top, x2: x, y2: height - margin.bottom, class: "grid" }));
    const y = margin.top + (plotH / 5) * i;
    svg.appendChild(svgEl("line", { x1: margin.left, y1: y, x2: width - margin.right, y2: y, class: "grid" }));
  }

  for (const reach of payload.reaches) {
    const x1 = cellX(reach.start_cell);
    const y1 = cellY(reach.start_cell);
    const x2 = cellX(reach.end_cell);
    const y2 = cellY(reach.end_cell);
    const line = svgEl("line", {
      x1, y1, x2, y2,
      class: [
        "reach-line",
        reach.is_control ? "control" : "",
        reach.is_terminal ? "terminal" : "",
        selected.type === "reach" && selected.id === reach.id ? "selected" : ""
      ].join(" "),
      "marker-end": "url(#arrow)"
    });
    line.addEventListener("click", () => {
      selected = { type: "reach", id: reach.id };
      document.getElementById("reach-select").value = reach.id;
      document.getElementById("unit-select").value = "";
      render();
    });
    svg.appendChild(line);
  }

  const maxArea = Math.max(...payload.units.map(u => u.area_km2), 1);
  for (const unit of payload.units) {
    const radius = 4 + Math.sqrt(unit.area_km2 / maxArea) * 11;
    const circle = svgEl("circle", {
      cx: cellX(unit.outlet_cell),
      cy: cellY(unit.outlet_cell),
      r: radius.toFixed(2),
      class: [
        "unit-dot",
        unit.is_control ? "control" : "",
        selected.type === "unit" && selected.id === unit.id ? "selected" : ""
      ].join(" ")
    });
    circle.addEventListener("click", () => {
      selected = { type: "unit", id: unit.id };
      document.getElementById("unit-select").value = unit.id;
      document.getElementById("reach-select").value = "";
      render();
    });
    svg.appendChild(circle);
  }

  if (payload.control && payload.control.cell) {
    const x = cellX(payload.control.cell);
    const y = cellY(payload.control.cell);
    svg.appendChild(svgEl("rect", {
      x: x - 7,
      y: y - 7,
      width: 14,
      height: 14,
      transform: `rotate(45 ${x} ${y})`,
      class: "control-marker"
    }));
    const label = svgEl("text", { x: x + 12, y: y - 10, class: "label" });
    label.textContent = "徐畈";
    svg.appendChild(label);
  }

  for (const reach of payload.reaches) {
    if (reach.is_control || reach.is_terminal || Number(reach.length_km) >= 2.5) {
      const x = (cellX(reach.start_cell) + cellX(reach.end_cell)) / 2;
      const y = (cellY(reach.start_cell) + cellY(reach.end_cell)) / 2;
      const label = svgEl("text", { x: x + 5, y: y - 5, class: "label" });
      label.textContent = reach.id;
      svg.appendChild(label);
    }
  }
}

function renderDetail() {
  const detail = document.getElementById("detail");
  if (selected.type === "unit") {
    const unit = payload.units.find(item => item.id === selected.id);
    if (!unit) return;
    detail.innerHTML = `
      <div class="detail-title"><strong>${unit.id}</strong><span class="badge">汇流单元</span></div>
      <div class="kv">
        <div>所属河段</div><div>${fmt(unit.reach_id, 0)}</div>
        <div>下游单元</div><div>${fmt(unit.downstream, 0)}</div>
        <div>上游单元</div><div>${unit.upstream.length ? unit.upstream.join(", ") : "-"}</div>
        <div>单元面积</div><div>${fmt(unit.area_km2, 4)} km2</div>
        <div>地貌分区</div><div>${fmt(unit.zone_type, 0)}</div>
        <div>平均高程</div><div>${fmt(unit.elevation_mean, 2)} m</div>
        <div>平均坡度</div><div>${fmt(unit.slope_mean, 2)}°</div>
        <div>平均坡向</div><div>${fmt(unit.aspect_mean, 2)}°</div>
        <div>出口栅格</div><div>${unit.outlet_cell.join(", ")}</div>
      </div>
    `;
    return;
  }
  const reach = payload.reaches.find(item => item.id === selected.id) || payload.reaches[0];
  detail.innerHTML = `
    <div class="detail-title"><strong>${reach.id}</strong><span class="badge">河段${reach.is_control ? " · 徐畈控制" : reach.is_terminal ? " · 出口" : ""}</span></div>
    <div class="kv">
      <div>下游河段</div><div>${fmt(reach.downstream, 0)}</div>
      <div>上游河段</div><div>${reach.upstream.length ? reach.upstream.join(", ") : "-"}</div>
      <div>河段长度</div><div>${fmt(reach.length_km, 3)} km</div>
      <div>河段栅格数</div><div>${fmt(reach.cell_count, 0)}</div>
      <div>关联汇流单元</div><div>${fmt(reach.unit_id, 0)}</div>
      <div>单元面积</div><div>${fmt(reach.unit_area_km2, 4)} km2</div>
      <div>起点栅格</div><div>${reach.start_cell.join(", ")}</div>
      <div>终点栅格</div><div>${reach.end_cell.join(", ")}</div>
    </div>
    ${reach.is_control && payload.control ? `
      <ul class="list">
        <li>徐畈控制断面挂接到 ${payload.control.reach_id}，距离 ${fmt(payload.control.distance_m, 3)} m。</li>
        <li>挂接点集水面积 ${fmt(payload.control.accumulation_area_km2, 4)} km2，高程 ${fmt(payload.control.elevation_m, 2)} m。</li>
      </ul>
    ` : ""}
  `;
}

function render() {
  renderMap();
  renderDetail();
}

renderStats();
renderSelects();
render();
</script>
</body>
</html>
""".replace("__TITLE__", html.escape(title)).replace("__PAYLOAD__", json_payload)
