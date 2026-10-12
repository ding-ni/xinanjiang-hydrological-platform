"""Run a data-quality platform task from a JSON request file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from data_quality_platform import get_data_quality_catalog, run_data_quality_task  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="?", help="JSON task request")
    parser.add_argument("--response", default=None, help="optional JSON response path")
    parser.add_argument("--catalog", action="store_true", help="print the callable module catalog")
    args = parser.parse_args()
    if args.catalog:
        response = get_data_quality_catalog()
    else:
        if not args.request:
            parser.error("request is required unless --catalog is used")
        response = run_data_quality_task(json.loads(Path(args.request).read_text(encoding="utf-8-sig")))
    text = json.dumps(response, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.response:
        Path(args.response).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
