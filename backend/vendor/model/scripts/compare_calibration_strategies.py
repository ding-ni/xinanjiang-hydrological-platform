"""CLI for CAL-COMPARE-01 formal calibration comparison."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from calibration_platform.comparison import compare_formal_calibrations  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixed-run", required=True)
    parser.add_argument("--anchored-run", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = compare_formal_calibrations(
        {
            "FIXED_WARMUP": args.fixed_run,
            "OBSERVED_FLOW_ANCHORED": args.anchored_run,
        },
        args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
