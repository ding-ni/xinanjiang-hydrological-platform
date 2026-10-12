"""CLI for independent calibration tasks."""

from __future__ import annotations

import argparse
import json

from .service import run_calibration_task


def main() -> int:
    parser = argparse.ArgumentParser(prog="xaj-calibration")
    parser.add_argument("task_json")
    args = parser.parse_args()
    payload = json.loads(open(args.task_json, encoding="utf-8-sig").read())
    print(json.dumps(run_calibration_task(payload), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
