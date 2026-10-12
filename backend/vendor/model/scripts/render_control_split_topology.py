# -*- coding: utf-8 -*-
"""Render the control-section split topology and catchment diagnostics."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from xaj_routing.raster_formats import read_esri_ascii_grid
from xaj_routing.spatial import build_grid_topology, downstream_cell
from build_control_section_split_topology import split_reach


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ascii", required=True, type=Path)
    parser.add_argument("--topology", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    grid = read_esri_ascii_grid(args.ascii)
    result = build_grid_topology(grid.elevation, grid.cellsize, 1.0, "nan_an", "nan_an_dem_utm50_30m_clipped_v1", grid.nodata)
    topo = json.loads(args.topology.read_text(encoding="utf-8-sig"))
    control = json.loads(args.control.read_text(encoding="utf-8-sig"))["snapped"]["cell"]
    assignments = np.full(result.flow_dir.shape, np.nan)
    from xaj_routing.spatial import assign_cells_to_reaches, valid_mask
    original = next(r for r in result.reaches if r.reach_id == "R0020")
    split_reaches, up_reach, down_reach, split_index = split_reach(result, "R0020", tuple(control))
    cell_assignment = assign_cells_to_reaches(
        result.flow_dir,
        result.stream_raster,
        split_reaches,
        valid_mask(grid.elevation, grid.nodata),
    )
    for cell, reach_id in cell_assignment.items():
        if reach_id == "R0020_UP":
            assignments[cell] = 1.0
        elif reach_id == "R0020_DOWN":
            assignments[cell] = 2.0
    # The routing units are incremental catchments. Separately trace every
    # valid D8 cell to the snapped control cell to draw the cumulative control
    # watershed, which is larger than the R0020_UP incremental unit.
    mask = valid_mask(grid.elevation, grid.nodata)
    cumulative_control = np.zeros(result.flow_dir.shape, dtype=bool)
    for start in zip(*np.where(mask)):
        current = tuple(int(value) for value in start)
        visited = set()
        while current is not None and current not in visited:
            if current == tuple(control):
                cumulative_control[start] = True
                break
            visited.add(current)
            current = downstream_cell(result.flow_dir, current[0], current[1], mask)
    fig, ax = plt.subplots(figsize=(11, 9), dpi=160)
    dem = np.where(grid.elevation == grid.nodata, np.nan, grid.elevation)
    ax.imshow(dem, cmap="terrain", alpha=0.78)
    ax.imshow(np.ma.masked_invalid(assignments), cmap="coolwarm", alpha=0.55, vmin=1, vmax=2)
    ax.contour(cumulative_control.astype(float), levels=[0.5], colors="#111111", linewidths=1.2, linestyles="--", zorder=2)
    for reach in split_reaches:
        ys = [c[0] for c in reach.cells]
        xs = [c[1] for c in reach.cells]
        if reach.reach_id not in {"R0020_UP", "R0020_DOWN"}:
            ax.plot(xs, ys, color="#263238", linewidth=0.45, alpha=0.65, zorder=3)
    up_x = [c[1] for c in up_reach.cells]
    up_y = [c[0] for c in up_reach.cells]
    down_x = [c[1] for c in down_reach.cells]
    down_y = [c[0] for c in down_reach.cells]
    ax.plot(up_x, up_y, color="#1769aa", linewidth=3.2, zorder=4)
    ax.plot(down_x, down_y, color="#c75146", linewidth=3.2, zorder=4)
    ax.scatter([control[1]], [control[0]], marker="D", s=90, color="#111111", edgecolor="white", label="徐畈控制断面", zorder=6)
    ax.annotate("徐畈控制断面", (control[1], control[0]), xytext=(control[1] + 18, control[0] - 14), color="#111111", arrowprops={"arrowstyle": "->"})
    ax.set_title("南安溪 30 m DEM · R0020 控制断面切分与河网集水区")
    ax.set_xlabel("DEM 列 / 30 m")
    ax.set_ylabel("DEM 行 / 30 m")
    ax.legend(handles=[
        Patch(facecolor="#4575b4", alpha=0.55, label="R0020_UP 集水区"),
        Patch(facecolor="#d73027", alpha=0.55, label="R0020_DOWN 集水区"),
        Line2D([0], [0], color="#111111", linewidth=1.2, linestyle="--", label="控制断面累计集水区边界"),
        Line2D([0], [0], color="#1769aa", linewidth=3.2, label="R0020_UP 河段"),
        Line2D([0], [0], color="#c75146", linewidth=3.2, label="R0020_DOWN 河段"),
        Line2D([0], [0], marker="D", color="#111111", markerfacecolor="#111111", markeredgecolor="white", linestyle="None", label="徐畈控制断面"),
    ], loc="lower right")
    ax.grid(alpha=0.12)
    fig.tight_layout()
    fig.savefig(args.output_dir / "control_split_topology.png", bbox_inches="tight")
    plt.close(fig)

    with (args.mapping).open(encoding="utf-8-sig", newline="") as handle:
        mapping = list(csv.DictReader(handle))
    up = [row for row in mapping if row["reach_id"] == "R0020_UP"]
    down = [row for row in mapping if row["reach_id"] == "R0020_DOWN"]
    summary = json.loads((args.output_dir / "control_split_summary.json").read_text(encoding="utf-8"))
    html = f'''<!doctype html><meta charset="utf-8"><title>R0020控制断面切分结果</title><style>body{{font:14px Arial,"Microsoft YaHei";margin:24px;background:#f4f7f7;color:#1c3037}}.panel{{background:#fff;border:1px solid #d4e0e0;padding:16px;margin:12px 0}}img{{max-width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}td,th{{padding:7px;border-bottom:1px solid #e4eeee;text-align:right}}td:first-child,th:first-child{{text-align:left}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.stat{{background:#eef5f4;padding:12px}}.stat b{{display:block;font-size:22px;margin-top:5px}}</style><h1>徐畈控制断面 R0020 切分</h1><div class="grid"><div class="stat">上游河段像元<b>{summary['upstream_reach_cell_count']}</b></div><div class="stat">下游河段像元<b>{summary['downstream_reach_cell_count']}</b></div><div class="stat">上游单元面积<b>{summary['upstream_unit_area_km2']:.4f} km²</b></div><div class="stat">下游单元面积<b>{summary['downstream_unit_area_km2']:.4f} km²</b></div></div><section class="panel"><img src="control_split_topology.png" alt="R0020控制断面切分与集水区"></section><section class="panel"><h2>空间关系</h2><p>处理链：30 m DEM → 填洼 → D8 → 汇流累积量 → 1 km²阈值河网 → R0020控制像元切分 → 集水区分配 → 1 km网格面积相交映射。</p><p>控制断面栅格：{control}；切分前后 R0020 单元面积差：{summary['area_difference_km2']:.6f} km²；1 km网格未分配：{summary['mapping']['unassigned_grid_count']}。</p></section><section class="panel"><h2>1 km网格到切分单元</h2><table><tr><th>单元</th><th>映射记录数</th><th>映射面积 km²</th></tr><tr><td>R0020_UP</td><td>{len(up)}</td><td>{sum(float(r['adjusted_overlap_area_km2']) for r in up):.4f}</td></tr><tr><td>R0020_DOWN</td><td>{len(down)}</td><td>{sum(float(r['adjusted_overlap_area_km2']) for r in down):.4f}</td></tr></table></section></html>'''
    (args.output_dir / "control_split_topology.html").write_text(html, encoding="utf-8")
    print(args.output_dir / "control_split_topology.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
