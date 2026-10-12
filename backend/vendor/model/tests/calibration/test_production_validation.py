import math
import unittest
from pathlib import Path

import numpy as np

from calibration_platform.production_validation import (
    ProductionValidationError,
    evaluate_multi_seed_stability,
    evaluate_production_gate,
    evaluate_wlm_boundary_experiment,
)
from runoff_platform.config_loader import load_parameter_set
from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids


ROOT = Path(__file__).resolve().parents[2]


def formal_report(wlm, upper, seed=1, nse=0.7, loss=0.2, split_seed=42):
    return {
        "split": {"seed": split_seed, "event_ids": ["E1", "E2", "E3"]},
        "parameter_space": {
            "bounds": {
                "runoff.WLM": [10.0, upper],
                "runoff.B": [0.1, 1.0],
            }
        },
        "common_parameter_set": {"runoff.WLM": wlm, "runoff.B": 0.5},
        "algorithm_config": {"seed": seed},
        "objective": {"loss": loss},
        "groups": {
            "VALIDATION": {"mean_NSE": nse},
            "INDEPENDENT_TEST": {"mean_NSE": nse - 0.02},
        },
    }


def passing_gate_inputs(pet_eligible=True):
    boundary = {"boundary_resolved": True, "selected_value": 80.0}
    stability = {"stable": True, "status": "PASSED"}
    continuous = {
        "pet": {
            "mode": "HOURLY_CSV" if pet_eligible else "CONSTANT_ASSUMPTION",
            "production_eligible": pet_eligible,
        },
        "minimum_observed_coverage": 1.0,
        "maximum_abs_internal_balance_error_mm": 1.0e-6,
        "groups": {
            "VALIDATION": {"mean_NSE": 0.7, "mean_volume_error_percent": 3.0},
            "INDEPENDENT_TEST": {"mean_NSE": 0.65, "mean_volume_error_percent": -4.0},
        },
    }
    event = {
        "groups": {
            "VALIDATION": {
                "mean_NSE": 0.68,
                "mean_abs_volume_error_percent": 10.0,
                "qualified_rate_percent": 65.0,
            },
            "INDEPENDENT_TEST": {
                "mean_NSE": 0.61,
                "mean_abs_volume_error_percent": 12.0,
                "qualified_rate_percent": 55.0,
            },
        }
    }
    return boundary, stability, continuous, event


class ProductionValidationTests(unittest.TestCase):
    def test_wlm_boundary_selects_best_non_boundary_run(self):
        runs = {
            "WLM_100": formal_report(100.0, 100.0, nse=0.8),
            "WLM_130": formal_report(104.0, 130.0, nse=0.7),
            "WLM_160": formal_report(120.0, 160.0, nse=0.6),
        }
        report = evaluate_wlm_boundary_experiment(runs)
        self.assertTrue(report["boundary_resolved"])
        self.assertEqual(report["selected_run"], "WLM_130")

    def test_wlm_boundary_rejects_different_event_splits(self):
        runs = {
            "A": formal_report(100.0, 100.0, split_seed=42),
            "B": formal_report(130.0, 130.0, split_seed=7),
        }
        with self.assertRaises(ProductionValidationError):
            evaluate_wlm_boundary_experiment(runs)

    def test_multi_seed_stability_and_representative_run(self):
        runs = {
            "SEED_1": formal_report(80.0, 130.0, seed=1, nse=0.70, loss=0.20),
            "SEED_2": formal_report(82.0, 130.0, seed=2, nse=0.72, loss=0.21),
            "SEED_3": formal_report(81.0, 130.0, seed=3, nse=0.71, loss=0.19),
        }
        report = evaluate_multi_seed_stability(runs)
        self.assertTrue(report["stable"])
        self.assertEqual(report["representative_run"], "SEED_3")

    def test_multi_seed_detects_unstable_parameters(self):
        runs = {
            "SEED_1": formal_report(20.0, 130.0, seed=1),
            "SEED_2": formal_report(70.0, 130.0, seed=2),
            "SEED_3": formal_report(125.0, 130.0, seed=3),
        }
        report = evaluate_multi_seed_stability(runs)
        self.assertFalse(report["stable"])
        self.assertFalse(report["checks"]["parameter_range"])

    def test_gate_approves_only_complete_passing_evidence(self):
        report = evaluate_production_gate(*passing_gate_inputs(), criteria={})
        self.assertEqual(report["status"], "PRODUCTION_APPROVED")
        self.assertTrue(report["production_approved"])

    def test_fixed_pet_and_missing_metric_block_approval(self):
        inputs = list(passing_gate_inputs(pet_eligible=False))
        inputs[2]["groups"]["VALIDATION"]["mean_NSE"] = None
        report = evaluate_production_gate(*inputs, criteria={})
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertIn("PET_PROVENANCE", report["failed_check_ids"])
        self.assertIn("CONTINUOUS_VALIDATION_NSE", report["failed_check_ids"])

    def test_dynamic_pet_diagnostics_close_hourly_water_balance(self):
        params = load_parameter_set(ROOT / "config" / "parameter_set_land_stage2_candidate.json")
        precipitation = np.asarray(
            [[0.0, 2.0], [4.0, 0.0], [12.0, 8.0], [0.0, 0.0]],
            dtype=float,
        )
        pet = np.asarray([0.05, 0.08, 0.12, 0.06], dtype=float)
        initial = {
            "swe": np.zeros(2),
            "snow_liquid": np.zeros(2),
            "WU": np.full(2, params.WUM * 0.6),
            "WL": np.full(2, params.WLM * 0.6),
            "WD": np.full(2, params.WDM * 0.6),
            "S": np.full(2, params.SM / 3.0),
            "FR": np.full(2, 0.5),
        }
        *_, diagnostics = simulate_xaj_liquid_grids(
            precipitation,
            params,
            pet,
            initial,
            return_diagnostics=True,
        )
        self.assertEqual(diagnostics["et_actual"].shape, precipitation.shape)
        self.assertLess(
            float(np.max(np.abs(diagnostics["water_balance_error"]))),
            1.0e-5,
        )
        self.assertTrue(math.isfinite(float(np.sum(diagnostics["et_actual"]))))


if __name__ == "__main__":
    unittest.main()
