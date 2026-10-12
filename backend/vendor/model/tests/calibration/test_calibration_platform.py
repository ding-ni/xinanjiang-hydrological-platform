import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from calibration_platform import get_calibration_catalog, run_calibration_task
from calibration_platform.comparison import compare_formal_calibrations
from calibration_platform.objectives import evaluate_objective
from calibration_platform.routing import apply_routing_candidate


class CalibrationPlatformTests(unittest.TestCase):
    def test_perfect_series_has_zero_loss_for_nse_and_kge(self):
        from datetime import datetime, timedelta
        start = datetime(2026, 1, 1)
        observed = {start + timedelta(hours=i): float(i + 1) for i in range(4)}
        result = evaluate_objective(
            observed,
            dict(observed),
            [{"metric": "NSE", "weight": 0.5}, {"metric": "KGE", "weight": 0.5}],
        )
        self.assertAlmostEqual(result["score"], 1.0)
        self.assertAlmostEqual(result["loss"], -1.0)

    def test_regional_aliases_do_not_modify_base_parameters(self):
        base = {
            "time_step_hours": 1.0,
            "default_slope_k_hours": {"RS": 2.0, "RI": 8.0, "RG": 24.0},
            "reach_muskingum": {"R1": {"k_hours": 1.0, "x": 0.2}},
        }
        updated, initial = apply_routing_candidate(
            base,
            {"slope_k.RS": 1.0, "muskingum.k_scale": 1.2, "muskingum.x": 0.1, "initial.control_flow_cms": 5.0},
        )
        self.assertEqual(base["default_slope_k_hours"]["RS"], 2.0)
        self.assertEqual(updated["default_slope_k_hours"]["RS"], 1.0)
        self.assertAlmostEqual(updated["reach_muskingum"]["R1"]["k_hours"], 1.2)
        self.assertAlmostEqual(updated["reach_muskingum"]["R1"]["x"], 0.1)
        self.assertEqual(initial, 5.0)

    def test_catalog_exposes_staged_modes(self):
        catalog = get_calibration_catalog()
        self.assertEqual(catalog["modes"], ["ROUTING_ONLY", "RUNOFF_ONLY", "JOINT"])
        self.assertIn("muskingum.k_scale", catalog["routing_parameter_aliases"])

    def test_real_routing_case_runs_through_public_service(self):
        root = Path(__file__).resolve().parents[2]
        payload = json.loads((root / "config/calibration_routing_2017.json").read_text(encoding="utf-8"))
        payload["project_root"] = str(root)
        payload["algorithm"] = "RANDOM_SEARCH"
        payload["algorithm_config"] = {"max_evaluations": 2, "seed": 7}
        with tempfile.TemporaryDirectory() as temporary_directory:
            payload["output_dir"] = temporary_directory
            result = run_calibration_task(payload)
        self.assertIn(result["status"], {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"})
        self.assertEqual(result["mode"], "ROUTING_ONLY")
        self.assertEqual(result["evaluations"], 2)
        self.assertIn("NSE", result["best_metrics"]["terms"])

    def test_formal_comparison_requires_identical_split_and_reports_boundaries(self):
        groups = ["CALIBRATION", "VALIDATION", "INDEPENDENT_TEST"]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_dirs = {}
            for strategy, increment in (("FIXED", 0.0), ("ANCHORED", 0.1)):
                run_dir = root / strategy
                run_dir.mkdir()
                run_dirs[strategy] = run_dir
                report = {
                    "groups": {
                        group: {
                            "event_count": 1,
                            "qualified_count": 0,
                            "qualified_rate_percent": 0.0,
                            "mean_NSE": 0.4 + increment,
                            "mean_KGE": 0.3 + increment,
                            "mean_abs_peak_error_percent": 30.0 - increment,
                            "mean_abs_volume_error_percent": 25.0 - increment,
                            "median_peak_time_error_hours": 2.0,
                        }
                        for group in groups
                    },
                    "common_parameter_set": {"runoff.KI": 0.06},
                    "parameter_space": {"bounds": {"runoff.KI": [0.003, 0.06]}},
                }
                (run_dir / "formal_multi_event_report.json").write_text(
                    json.dumps(report), encoding="utf-8"
                )
                pd.DataFrame([
                    {
                        "event_id": f"E{index}",
                        "group": group,
                        "NSE": 0.4 + increment,
                        "KGE": 0.3 + increment,
                        "peak_error_percent": 30.0 - increment,
                        "volume_error_percent": 25.0 - increment,
                        "peak_time_error_hours": 2.0,
                    }
                    for index, group in enumerate(groups)
                ]).to_csv(run_dir / "event_metrics.csv", index=False)
            result = compare_formal_calibrations(run_dirs, root / "comparison")
            self.assertTrue(result["event_split_identical"])
            self.assertEqual(result["higher_external_mean_NSE_strategy"], "ANCHORED")
            self.assertEqual(result["boundary_parameters"], ["runoff.KI"])
            self.assertTrue((root / "comparison/group_metric_deltas.csv").is_file())


if __name__ == "__main__":
    unittest.main()
