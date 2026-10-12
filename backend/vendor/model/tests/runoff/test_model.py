import unittest

import pandas as pd

from runoff_platform.core.types import (
    GlacierParams,
    GlacierState,
    LandParams,
    LandState,
    daily_source_separation_to_hourly,
    hourly_source_separation_to_daily,
)
from runoff_platform.models.glacier import simulate_glacier
from runoff_platform.models.xaj import simulate_land


def hourly_forcing():
    index = pd.date_range("2020-01-01", periods=12, freq="h")
    return pd.DataFrame({"precipitation": [0, 0, 2, 8, 20, 4, 0, 0, 0, 0, 0, 0], "temperature": [5.0] * 12, "pet": [0.05] * 12}, index=index)


class ModelTests(unittest.TestCase):
    def test_xaj_balance_and_nonnegative_components(self):
        result = simulate_land(hourly_forcing(), state=LandState(swe=0))
        self.assertLess(result.water_balance_error.abs().max(), 1e-9)
        self.assertTrue((result[["RS", "RI", "RG"]] >= 0).all().all())
        self.assertLess(abs((result.RS + result.RI + result.RG - result.total_runoff)).max(), 1e-12)

    def test_zero_rain_zero_free_water_has_no_runoff(self):
        forcing = pd.DataFrame({"precipitation": [0.0] * 4, "temperature": [5.0] * 4, "pet": [0.0] * 4}, index=pd.date_range("2020-01-01", periods=4, freq="h"))
        result = simulate_land(forcing, state=LandState(swe=0, snow_liquid=0, S=0, FR=0))
        self.assertEqual(result.total_runoff.sum(), 0)

    def test_daily_source_coefficients_are_rejected_by_hourly_model(self):
        params = LandParams(
            KI=0.30,
            KG=0.37,
            source_coefficient_time_step_hours=24.0,
        )
        with self.assertRaisesRegex(ValueError, "1小时|1-hour"):
            simulate_land(hourly_forcing(), params=params, state=LandState(swe=0))

    def test_source_separation_time_conversion_round_trip(self):
        groundwater_fraction = 0.37 / 0.67
        ki_hour, kg_hour = daily_source_separation_to_hourly(0.67, groundwater_fraction)
        total_day, recovered_fraction = hourly_source_separation_to_daily(ki_hour, kg_hour)
        self.assertAlmostEqual(total_day, 0.67, places=12)
        self.assertAlmostEqual(recovered_fraction, groundwater_fraction, places=12)
        self.assertAlmostEqual((1.0 - ki_hour - kg_hour) ** 24, 0.33, places=12)

    def test_snow_exhaustion_uses_remaining_ice_energy(self):
        forcing = pd.DataFrame({"precipitation": [0.0], "temperature": [24.0], "pet": [0.0]}, index=pd.date_range("2020-07-01", periods=1, freq="h"))
        params = GlacierParams()
        result = simulate_glacier(forcing, params=params, state=GlacierState(swe=1.0, snow_liquid=0, glacier_storage=0))
        expected = params.ice_ddf * (1 - 1 / (params.snow.snow_ddf * 24 / 24))
        self.assertAlmostEqual(result.iloc[0].icemelt, expected)
        self.assertLess(result.iloc[0].icemelt, params.ice_ddf)
        self.assertLess(abs(result.iloc[0].water_balance_error), 1e-9)


if __name__ == "__main__":
    unittest.main()
