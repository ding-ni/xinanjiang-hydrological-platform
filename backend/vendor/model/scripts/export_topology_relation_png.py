"""Export a readable PNG preview of the DEM-derived reach topology."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch


font_manager.fontManager.addfont(r"C:\Windows\Fonts\msyh.ttc")
plt.rcParams["font.family"] = font_manager.FontProperties(fname=r"C:\Windows\Fonts\msyh.ttc").get_name()
plt.rcParams["axes.unicode_minus"] = False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", required=True)
    parser.add_argument("--control-point", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    topology = json.loads(Path(args.topology).read_text(encoding="utf-8"))
    control = json.loads(Path(args.control_point).read_text(encoding="utf-8"))
    reaches = topology["reaches"]
    units = {unit["reach_id"]: unit for unit in topology["units"]}
    control_reach = control["snapped"]["reach_id"]
    terminal_reach = next(reach["reach_id"] for reach in reaches if not reach.get("downstream_reach_id"))

    cells = [cell for reach in reaches for cell in (reach["start_cell"], reach["end_cell"])]
    cols = [cell[1] for cell in cells]
    rows = [cell[0] for cell in cells]
    fig, ax = plt.subplots(figsize=(14, 10), dpi=180)
    ax.set_facecolor("#f4f7f8")
    ax.set_xlim(min(cols) - 15, max(cols) + 15)
    ax.set_ylim(max(rows) + 15, min(rows) - 15)
    ax.set_aspect("equal")
    ax.grid(color="#dfe7e9", linewidth=0.5, alpha=0.7)

    max_area = max(unit["area_km2"] for unit in units.values())
    for reach in reaches:
        x1, y1 = reach["start_cell"][1], reach["start_cell"][0]
        x2, y2 = reach["end_cell"][1], reach["end_cell"][0]
        reach_id = reach["reach_id"]
        color = "#d36542" if reach_id == control_reach else "#745f91" if reach_id == terminal_reach else "#186c86"
        linewidth = 3.2 if reach_id == control_reach else 3.0 if reach_id == terminal_reach else 1.8
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=10,
                linewidth=linewidth,
                color=color,
                alpha=0.9,
            )
        )
        unit = units[reach_id]
        ax.scatter(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            s=20 + 90 * unit["area_km2"] / max_area,
            color="#c78b35",
            alpha=0.42,
            edgecolors="#8d6426",
            linewidths=0.5,
            zorder=3,
        )
        ax.text(
            (x1 + x2) / 2 + 0.7,
            (y1 + y2) / 2 - 0.8,
            reach_id,
            fontsize=7,
            color=color,
            weight="bold" if reach_id in {control_reach, terminal_reach} else "normal",
        )

    control_cell = control["snapped"]["cell"]
    ax.scatter(control_cell[1], control_cell[0], s=130, marker="D", color="#d36542", edgecolors="white", linewidths=1.2, zorder=5)
    ax.annotate("徐畈控制断面\nR0020", xy=(control_cell[1], control_cell[0]), xytext=(control_cell[1] + 10, control_cell[0] - 8), arrowprops={"arrowstyle": "->", "color": "#d36542"}, fontsize=9, color="#9b432d", weight="bold")

    outlet = next(reach for reach in reaches if reach["reach_id"] == terminal_reach)
    ax.annotate("总出口 R0015", xy=(outlet["end_cell"][1], outlet["end_cell"][0]), xytext=(outlet["end_cell"][1] - 24, outlet["end_cell"][0] - 5), arrowprops={"arrowstyle": "->", "color": "#745f91"}, fontsize=9, color="#5c4877", weight="bold")
    ax.set_xlabel("DEM 栅格列号")
    ax.set_ylabel("DEM 栅格行号")
    fig.suptitle("南安溪—徐畈 30 m DEM 河段拓扑关系", fontsize=16, weight="bold", y=0.98)
    fig.text(0.5, 0.945, "箭头：上游 → 下游；橙色：徐畈控制河段；紫色：总出口河段；圆点：汇流单元面积相对大小", ha="center", fontsize=9, color="#667981")
    ax.text(0.99, 0.01, f"{len(reaches)} 条河段 · {len(units)} 个汇流单元 · 河网阈值 1.0 km²\n控制断面集水面积 {control['snapped']['accumulation_area_km2']:.2f} km²", transform=ax.transAxes, ha="right", va="bottom", fontsize=9, color="#667981")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
