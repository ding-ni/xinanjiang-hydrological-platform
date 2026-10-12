"""Command-line interface for local routing tests."""

from __future__ import annotations

import argparse

from .engine import RoutingEngine
from .io import (
    read_control_points_json,
    read_parameters_json,
    read_runoff_csv,
    read_state_json,
    read_topology_json,
    write_component_flows_csv,
    write_control_flows_csv,
    write_json,
    write_outlet_flows_csv,
    write_reach_flows_csv,
)
from .sample_data import generate_routing_parameters_json, generate_synthetic_runoff_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xaj-routing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run routing from local files")
    run_parser.add_argument("--runoff", required=True, help="runoff CSV path")
    run_parser.add_argument("--topology", required=True, help="topology JSON path")
    run_parser.add_argument("--parameters", required=True, help="routing parameters JSON path")
    run_parser.add_argument("--state-in", default=None, help="optional initial state JSON path")
    run_parser.add_argument("--run-id", default="local_run", help="run id written to outputs")
    run_parser.add_argument("--out", required=True, help="reach flow output CSV path")
    run_parser.add_argument("--component-out", default=None, help="optional component output CSV path")
    run_parser.add_argument("--outlet-out", default=None, help="optional outlet output CSV path")
    run_parser.add_argument("--control-points", default=None, help="optional control-point JSON path")
    run_parser.add_argument("--control-out", default=None, help="optional control-point flow CSV path")
    run_parser.add_argument("--state-out", required=True, help="final state JSON path")

    runoff_parser = subparsers.add_parser(
        "generate-synthetic-runoff",
        help="generate a topology-aligned runoff CSV for local integration tests",
    )
    runoff_parser.add_argument("--topology", required=True, help="topology JSON path")
    runoff_parser.add_argument("--out", required=True, help="output runoff CSV path")
    runoff_parser.add_argument("--start-time", default="2026-08-03 00:00:00")
    runoff_parser.add_argument("--periods", type=int, default=12)
    runoff_parser.add_argument("--time-step-hours", type=float, default=1.0)
    runoff_parser.add_argument("--peak-rs-mm", type=float, default=5.0)

    params_parser = subparsers.add_parser(
        "generate-routing-parameters",
        help="generate first-pass routing parameters from topology reach lengths",
    )
    params_parser.add_argument("--topology", required=True, help="topology JSON path")
    params_parser.add_argument("--out", required=True, help="output parameters JSON path")
    params_parser.add_argument("--time-step-hours", type=float, default=1.0)
    params_parser.add_argument("--channel-velocity-kmh", type=float, default=3.0)
    params_parser.add_argument("--muskingum-x", type=float, default=0.2)
    return parser


def run_command(args: argparse.Namespace) -> int:
    records = read_runoff_csv(args.runoff)
    topology = read_topology_json(args.topology)
    parameters = read_parameters_json(args.parameters)
    state = read_state_json(args.state_in)
    control_points = read_control_points_json(args.control_points)
    if args.control_out and not control_points:
        raise ValueError("--control-out requires --control-points")

    engine = RoutingEngine(
        topology=topology,
        parameters=parameters,
        initial_state=state,
        control_points=control_points,
    )
    result = engine.run(records)

    write_reach_flows_csv(args.out, result.reach_flows, run_id=args.run_id)
    if args.component_out:
        write_component_flows_csv(args.component_out, result.component_flows, run_id=args.run_id)
    if args.outlet_out:
        write_outlet_flows_csv(args.outlet_out, result.outlet_flows, run_id=args.run_id)
    if args.control_out:
        write_control_flows_csv(args.control_out, result.control_flows, run_id=args.run_id)
    write_json(args.state_out, result.final_state.to_mapping())

    print(
        "routing complete: "
        f"{len(result.component_flows)} component rows, "
        f"{len(result.reach_flows)} reach rows, "
        f"{len(result.outlet_flows)} outlet rows, "
        f"{len(result.control_flows)} control rows"
    )
    return 0


def generate_synthetic_runoff_command(args: argparse.Namespace) -> int:
    summary = generate_synthetic_runoff_csv(
        topology_path=args.topology,
        out_path=args.out,
        start_time=args.start_time,
        periods=args.periods,
        time_step_hours=args.time_step_hours,
        peak_rs_mm=args.peak_rs_mm,
    )
    print(
        "synthetic runoff complete: "
        f"{summary['row_count']} rows, "
        f"{summary['unit_count']} units, "
        f"{summary['periods']} periods"
    )
    return 0


def generate_routing_parameters_command(args: argparse.Namespace) -> int:
    summary = generate_routing_parameters_json(
        topology_path=args.topology,
        out_path=args.out,
        time_step_hours=args.time_step_hours,
        default_channel_velocity_kmh=args.channel_velocity_kmh,
        muskingum_x=args.muskingum_x,
    )
    print(
        "routing parameters complete: "
        f"{summary['reach_count']} reaches, "
        f"time_step_hours={summary['time_step_hours']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    if args.command == "generate-synthetic-runoff":
        return generate_synthetic_runoff_command(args)
    if args.command == "generate-routing-parameters":
        return generate_routing_parameters_command(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
