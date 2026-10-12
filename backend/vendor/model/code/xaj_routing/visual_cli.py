"""CLI adapters for routing result visualization."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .flow_compare import parse_flow_time, write_flow_comparison_outputs
from .stream_network_visual import write_dem_stream_network_visualization
from .topology_visual import write_topology_overview_html


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xaj-visual")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare_parser = subparsers.add_parser(
        "compare-flow",
        help="compare simulated and observed flow hydrographs on one time axis",
    )
    compare_parser.add_argument("--sim-flow", required=True, help="simulated flow CSV path")
    compare_parser.add_argument("--obs-flow", required=True, help="observed flow CSV/XLSX path")
    compare_parser.add_argument("--out-html", required=True, help="standalone HTML output path")
    compare_parser.add_argument("--out-csv", default=None, help="aligned overlapping rows CSV")
    compare_parser.add_argument("--metrics-out", default=None, help="metrics JSON output path")
    compare_parser.add_argument("--sim-time-col", default=None, help="simulated time column")
    compare_parser.add_argument("--sim-flow-col", default=None, help="simulated flow column")
    compare_parser.add_argument("--obs-time-col", default=None, help="observed time column")
    compare_parser.add_argument("--obs-flow-col", default=None, help="observed flow column")
    compare_parser.add_argument("--obs-sheet", default=None, help="observed Excel sheet name")
    compare_parser.add_argument(
        "--obs-sheet-index",
        type=int,
        default=None,
        help="1-based observed Excel sheet index",
    )
    compare_parser.add_argument(
        "--obs-header-row",
        type=int,
        default=1,
        help="1-based observed Excel header row",
    )
    compare_parser.add_argument(
        "--sim-filter",
        action="append",
        default=[],
        help="optional simulated CSV filter in column=value form; repeatable",
    )
    compare_parser.add_argument(
        "--plot-domain",
        default="sim-window",
        choices=["sim-window", "overlap", "union"],
        help="time window used by the HTML plot",
    )
    compare_parser.add_argument("--plot-start", default=None, help="optional plot start time")
    compare_parser.add_argument("--plot-end", default=None, help="optional plot end time")
    compare_parser.add_argument("--max-points", type=int, default=2000)
    compare_parser.add_argument(
        "--excel-temp-dir", default=str(Path(tempfile.gettempdir()) / "XAJ_xlsx_runtime_tmp")
    )

    topology_parser = subparsers.add_parser(
        "topology-overview",
        help="export an interactive topology overview HTML",
    )
    topology_parser.add_argument("--topology", required=True, help="routing topology JSON path")
    topology_parser.add_argument("--control-point", default=None, help="optional snapped control-point JSON path")
    topology_parser.add_argument("--out-html", required=True, help="standalone HTML output path")
    topology_parser.add_argument("--summary-out", default=None, help="optional summary JSON output path")

    stream_parser = subparsers.add_parser(
        "dem-stream-network",
        help="export a DEM-derived stream network visualization",
    )
    stream_parser.add_argument("--ascii", required=True, help="clipped ESRI ASCII DEM path")
    stream_parser.add_argument("--out-html", required=True, help="standalone HTML output path")
    stream_parser.add_argument("--out-png", default=None, help="optional PNG output path")
    stream_parser.add_argument("--summary-out", default=None, help="optional summary JSON output path")
    stream_parser.add_argument("--control-point", default=None, help="optional snapped control-point JSON path")
    stream_parser.add_argument("--cell-size-m", type=float, default=30.0)
    stream_parser.add_argument("--stream-threshold-km2", type=float, default=1.0)
    stream_parser.add_argument("--basin-id", default="nan_an")
    stream_parser.add_argument("--spatial-version", default="nan_an_dem_utm50_30m_clipped_v1")
    stream_parser.add_argument("--png-scale", type=int, default=3)
    return parser


def run_compare_flow(args: argparse.Namespace) -> int:
    metrics = write_flow_comparison_outputs(
        sim_flow=args.sim_flow,
        obs_flow=args.obs_flow,
        out_html=args.out_html,
        out_csv=args.out_csv,
        metrics_out=args.metrics_out,
        sim_time_col=args.sim_time_col,
        sim_flow_col=args.sim_flow_col,
        obs_time_col=args.obs_time_col,
        obs_flow_col=args.obs_flow_col,
        obs_sheet=args.obs_sheet,
        obs_sheet_index=args.obs_sheet_index,
        obs_header_row=args.obs_header_row,
        sim_filters=_parse_filters(args.sim_filter),
        plot_domain=args.plot_domain,
        plot_start=parse_flow_time(args.plot_start) if args.plot_start else None,
        plot_end=parse_flow_time(args.plot_end) if args.plot_end else None,
        max_points=args.max_points,
        excel_temp_dir=args.excel_temp_dir,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


def _parse_filters(items: list[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid --sim-filter value: {item!r}; expected column=value")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid --sim-filter value: {item!r}; column is blank")
        filters[key] = value.strip()
    return filters


def run_topology_overview(args: argparse.Namespace) -> int:
    summary = write_topology_overview_html(
        topology_json=args.topology,
        control_point_json=args.control_point,
        out_html=args.out_html,
        summary_out=args.summary_out,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_dem_stream_network(args: argparse.Namespace) -> int:
    summary = write_dem_stream_network_visualization(
        ascii_path=args.ascii,
        out_html=args.out_html,
        out_png=args.out_png,
        summary_out=args.summary_out,
        control_point_json=args.control_point,
        cell_size_m=args.cell_size_m,
        stream_threshold_km2=args.stream_threshold_km2,
        basin_id=args.basin_id,
        spatial_version=args.spatial_version,
        png_scale=args.png_scale,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "compare-flow":
        return run_compare_flow(args)
    if args.command == "topology-overview":
        return run_topology_overview(args)
    if args.command == "dem-stream-network":
        return run_dem_stream_network(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
