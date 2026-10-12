"""Compare two formal calibration rounds through CAL-COMPARE-01."""
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
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--candidate-run", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--baseline-label", default="STAGE1")
    parser.add_argument("--candidate-label", default="STAGE2")
    args = parser.parse_args()
    report = compare_formal_calibrations(
        {
            args.baseline_label: args.baseline_run,
            args.candidate_label: args.candidate_run,
        },
        args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
