"""Command line entry for the integrated runoff-routing workflow."""

from __future__ import annotations

import argparse
import json

from .pipeline import run_integrated_case


def main() -> int:
    parser = argparse.ArgumentParser(prog="runoff-routing-integration")
    parser.add_argument("config", help="integrated case JSON")
    args = parser.parse_args()
    print(json.dumps(run_integrated_case(args.config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
