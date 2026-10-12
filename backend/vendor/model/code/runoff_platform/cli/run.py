"""Run simulation or calibration JSON requests from the command line."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..api.service import run_calibration_request, run_platform_request


def main():
    parser = argparse.ArgumentParser(description="Modular runoff platform runner")
    parser.add_argument("mode", choices=["simulation", "calibration"])
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.request.read_text(encoding="utf-8-sig"))
    response = run_platform_request(payload) if args.mode == "simulation" else run_calibration_request(payload)
    text = json.dumps(response, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
