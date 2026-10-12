"""Verify the teacher delivery package without changing model parameters."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path


def count_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    invalid_json = []
    for path in root.rglob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            invalid_json.append({"path": str(path.relative_to(root)), "error": str(exc)})

    mapping_path = root / "data_interface/grid_to_unit_weight.csv"
    fractions = defaultdict(float)
    methods = defaultdict(int)
    with mapping_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fractions[row["grid_id"]] += float(row["source_fraction"])
            methods[row["mapping_method"]] += 1
    max_fraction_error = max(abs(value - 1.0) for value in fractions.values())

    output_dir = root / "validation/integrated_2017"
    calibration_summary_path = root / "validation/calibration_routing_2017/calibration_summary.json"
    calibration_summary = (
        json.loads(calibration_summary_path.read_text(encoding="utf-8-sig"))
        if calibration_summary_path.exists() else None
    )
    negative_reach_flow_count = 0
    with (output_dir / "reach_flow.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            negative_reach_flow_count += float(row["outflow_cms"]) < 0.0

    report = {
        "status": "PASS" if not invalid_json and max_fraction_error < 1.0e-10 and negative_reach_flow_count == 0 and calibration_summary is not None else "FAIL",
        "json_file_count": len(list(root.rglob("*.json"))),
        "invalid_json": invalid_json,
        "runoff_grid_count": len(fractions),
        "max_source_fraction_error": max_fraction_error,
        "mapping_methods": dict(methods),
        "runoff_wide_rows": count_rows(root / "data_interface/runoff_2017/runoff_components_wide.csv"),
        "routing_input_rows": count_rows(output_dir / "runoff_by_routing_unit.csv"),
        "reach_flow_rows": count_rows(output_dir / "reach_flow.csv"),
        "control_flow_rows": count_rows(output_dir / "control_flow.csv"),
        "outlet_flow_rows": count_rows(output_dir / "outlet_flow.csv"),
        "negative_reach_flow_count": negative_reach_flow_count,
        "calibration_status": None if calibration_summary is None else calibration_summary["status"],
        "calibration_evaluations": None if calibration_summary is None else calibration_summary["evaluations"],
        "calibration_baseline_nse": None if calibration_summary is None else calibration_summary["baseline_metrics"]["NSE"],
        "calibration_best_nse": None if calibration_summary is None else calibration_summary["best_metrics"]["NSE"],
    }
    (root / "validation/package_verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
