"""CLI for rainfall forcing extraction from historical NetCDF products."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .forcing import export_rainfall_forcing_csv, resolve_time_window
from .io import write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xaj-forcing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export-rainfall",
        help="export rainfall forcing CSV from yearly NetCDF files",
    )
    export_parser.add_argument("--nc-dir", required=True, help="directory containing yearly .nc files")
    export_parser.add_argument("--out", required=True, help="output rainfall forcing CSV")
    export_parser.add_argument("--manifest-out", default=None, help="optional manifest JSON output")
    export_parser.add_argument("--dataset-kind", default="auto", choices=["auto", "station", "grid", "grid1km", "grid0p1deg"])
    export_parser.add_argument("--valid-grid-geojson", default=None, help="optional valid grid GeoJSON filter")
    export_parser.add_argument("--start", default=None, help="manual start time, inclusive")
    export_parser.add_argument("--end", default=None, help="manual end time, inclusive")
    export_parser.add_argument("--event-json", default=None, help="flood event catalog JSON")
    export_parser.add_argument("--event-id", default=None, help="event id, e.g. xf-31640404 or 31640404")
    export_parser.add_argument(
        "--temp-dir", default=str(Path(tempfile.gettempdir()) / "XAJ_nc_runtime_tmp")
    )
    return parser


def run_export_rainfall(args: argparse.Namespace) -> int:
    window = resolve_time_window(
        start=args.start,
        end=args.end,
        event_json=args.event_json,
        event_id=args.event_id,
    )
    manifest = export_rainfall_forcing_csv(
        nc_dir=args.nc_dir,
        out_csv=args.out,
        window=window,
        dataset_kind=args.dataset_kind,
        valid_grid_geojson=args.valid_grid_geojson,
        temp_dir=args.temp_dir,
    )
    if args.manifest_out:
        write_json(args.manifest_out, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "export-rainfall":
        return run_export_rainfall(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
