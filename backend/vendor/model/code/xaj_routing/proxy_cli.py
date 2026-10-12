"""CLI for temporary routing integration inputs."""

from __future__ import annotations

import argparse
import json

from .runoff_proxy import generate_basin_average_runoff_proxy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xaj-proxy")
    subparsers = parser.add_subparsers(dest="command", required=True)

    runoff_parser = subparsers.add_parser(
        "rainfall-to-runoff",
        help="convert rainfall forcing to a standard runoff proxy CSV",
    )
    runoff_parser.add_argument("--rainfall", required=True, help="rainfall forcing CSV")
    runoff_parser.add_argument("--topology", required=True, help="routing topology JSON")
    runoff_parser.add_argument("--out", required=True, help="standard runoff CSV output")
    runoff_parser.add_argument("--manifest-out", default=None, help="manifest JSON output")
    runoff_parser.add_argument("--runoff-coefficient", type=float, default=1.0)
    runoff_parser.add_argument("--rs-fraction", type=float, default=0.6)
    runoff_parser.add_argument("--ri-fraction", type=float, default=0.3)
    runoff_parser.add_argument("--rg-fraction", type=float, default=0.1)
    return parser


def run_rainfall_to_runoff(args: argparse.Namespace) -> int:
    manifest = generate_basin_average_runoff_proxy(
        rainfall_csv=args.rainfall,
        topology_json=args.topology,
        out_csv=args.out,
        manifest_out=args.manifest_out,
        runoff_coefficient=args.runoff_coefficient,
        rs_fraction=args.rs_fraction,
        ri_fraction=args.ri_fraction,
        rg_fraction=args.rg_fraction,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "rainfall-to-runoff":
        return run_rainfall_to_runoff(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
