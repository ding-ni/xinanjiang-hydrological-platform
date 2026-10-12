"""Import a separate immutable MDB input bundle without changing the default."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from common import MODEL
from input_products import InputProducts
from mdb_import import import_mdb_bundle, TARGET_NC_CENTRE, TARGET_LEGACY_REPLAY, QC_PLATFORM_DIAGNOSTIC, QC_SOURCE_ORIGINAL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mdb", required=True)
    parser.add_argument("--runtime", default=str(Path(__file__).resolve().parents[1] / "runtime-20261012"),
                        help="Independent 20261012 workspace by default; never silently import into the daily runtime")
    parser.add_argument("--model", default=str(MODEL))
    parser.add_argument("--name", default="MDB独立17场输入资料")
    parser.add_argument("--provenance-note", default="20261012终结版MDB资料接入，尚未生产验收")
    parser.add_argument("--compare-with", help="Original MDB to compare by normalized rainfall/flow/event records")
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--no-register", action="store_true")
    parser.add_argument("--reuse-verified-task", help="Reuse complete hashed SELECT receipts from a prior MDB task in this same runtime")
    parser.add_argument("--target-coordinate-policy", choices=(TARGET_NC_CENTRE, TARGET_LEGACY_REPLAY), default=TARGET_NC_CENTRE,
                        help="Use NC grid centres by default; explicit legacy closed-ring means with float64 are source replay only")
    parser.add_argument("--quality-policy", choices=(QC_PLATFORM_DIAGNOSTIC, QC_SOURCE_ORIGINAL), default=QC_PLATFORM_DIAGNOSTIC,
                        help="Choose the known source CAL quality policy explicitly for exact source reproduction")
    parser.add_argument("--source-quality-config", help="Exact source event_quality_config_snapshot.json required by SOURCE_ORIGINAL_20261011")
    args = parser.parse_args()
    products = InputProducts(args.model, args.runtime)
    try:
        result = import_mdb_bundle(args.mdb, products=products, name=args.name,
                                   provenance_note=args.provenance_note, timeout=args.timeout,
                                   register=not args.no_register, compare_with=args.compare_with,
                                   reuse_query_task=args.reuse_verified_task, target_coordinate_policy=args.target_coordinate_policy,
                                   quality_policy=args.quality_policy, source_quality_config=args.source_quality_config)
    except Exception as exc:
        print(json.dumps({"status": "failed", "errorType": type(exc).__name__, "error": str(exc),
                          "runtime": str(products.runtime_root), "defaultUnchanged": True}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
