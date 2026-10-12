import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from runoff_platform.data.pet_forcing import PetProvider, provider_from_container


class PetForcingTests(unittest.TestCase):
    def test_constant_assumption_is_hourly_and_not_production_eligible(self):
        provider = PetProvider(
            {"mode": "CONSTANT_ASSUMPTION", "value_mm_h": 0.08}, Path.cwd()
        )
        values = provider.values(pd.date_range("2020-01-01", periods=3, freq="h"))
        np.testing.assert_allclose(values, [0.08, 0.08, 0.08])
        self.assertFalse(provider.report()["production_eligible"])

    def test_monthly_climatology_converts_daily_values_and_switches_month(self):
        provider = PetProvider(
            {
                "mode": "MONTHLY_CLIMATOLOGY",
                "monthly_values_mm_day": list(range(1, 13)),
                "production_eligible": True,
            },
            Path.cwd(),
        )
        times = pd.DatetimeIndex(["2020-01-31 23:00", "2020-02-01 00:00"])
        np.testing.assert_allclose(provider.values(times), [1.0 / 24.0, 2.0 / 24.0])
        self.assertFalse(provider.report()["production_eligible"])

    def test_invalid_monthly_values_are_rejected(self):
        with self.assertRaises(ValueError):
            PetProvider(
                {"mode": "MONTHLY_CLIMATOLOGY", "monthly_values_mm_day": [3.0] * 11},
                Path.cwd(),
            )
        with self.assertRaises(ValueError):
            PetProvider(
                {
                    "mode": "MONTHLY_CLIMATOLOGY",
                    "monthly_values_mm_day": [3.0] * 11 + [-1.0],
                },
                Path.cwd(),
            )

    def test_hourly_csv_requires_complete_requested_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = root / "pet.csv"
            pd.DataFrame(
                {"time": ["2020-01-01 00:00", "2020-01-01 01:00"], "pet_mm_h": [0.1, 0.2]}
            ).to_csv(csv_path, index=False)
            provider = PetProvider(
                {
                    "mode": "HOURLY_CSV",
                    "path": "pet.csv",
                    "production_eligible": True,
                },
                root,
            )
            self.assertTrue(provider.report()["production_eligible"])
            with self.assertRaises(ValueError):
                provider.values(pd.date_range("2020-01-01", periods=3, freq="h"))

    def test_container_reference_uses_one_external_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pet_path = root / "pet.json"
            pet_path.write_text(
                json.dumps({"mode": "CONSTANT_ASSUMPTION", "value_mm_h": 0.05}),
                encoding="utf-8",
            )
            provider = provider_from_container(
                {"pet_config": "pet.json"}, root / "continuous.json"
            )
            self.assertEqual(provider.mode, "CONSTANT_ASSUMPTION")


if __name__ == "__main__":
    unittest.main()
