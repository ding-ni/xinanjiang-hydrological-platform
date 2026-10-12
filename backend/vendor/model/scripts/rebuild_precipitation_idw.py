"""Rebuild corrected Nan'an-Xufan 1 km precipitation without overwriting source files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from data_quality_platform import run_data_quality_task  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station-dir", required=True)
    parser.add_argument("--template-grid-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--grid-static", default=str(ROOT / "data_interface" / "runoff_2017" / "grid_static.csv"))
    parser.add_argument("--start-year", type=int, default=1959)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--idw-power", type=float, default=2.0)
    parser.add_argument("--minimum-stations", type=int, default=1)
    parser.add_argument("--keep-zero-only-stations", action="store_true", help="disable the corrective zero-only-as-missing policy")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    response = run_data_quality_task(
        {
            "module_id": "DQ-PRECIP-IDW-01",
            "task_id": "PRECIP-CLI",
            "project_root": str(ROOT),
            "station_dir": str(Path(args.station_dir).resolve()),
            "template_grid_dir": str(Path(args.template_grid_dir).resolve()),
            "output_dir": str(Path(args.output_dir).resolve()),
            "grid_static": str(Path(args.grid_static).resolve()),
            "start_year": args.start_year,
            "end_year": args.end_year,
            "idw_power": args.idw_power,
            "minimum_stations": args.minimum_stations,
            "zero_only_station_year_policy": "KEEP" if args.keep_zero_only_stations else "RECLASSIFY_AS_MISSING",
            "overwrite": args.overwrite,
        }
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
