"""Safely copy a registered input bundle closure to an independent runtime."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from common import MODEL, write_json
from input_transfer import promote_input_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--source-runtime", required=True)
    parser.add_argument("--target-runtime", required=True)
    parser.add_argument("--model", default=str(MODEL))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()
    result = promote_input_bundle(args.bundle, args.source_runtime, args.target_runtime, model_root=args.model, dry_run=args.dry_run)
    if args.report:
        write_json(args.report, result)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
