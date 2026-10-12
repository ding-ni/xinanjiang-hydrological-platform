import unittest

import numpy as np

from runoff_platform.data.precipitation_idw import idw_interpolate_hourly, sanitize_station_year


class PrecipitationPreprocessingTests(unittest.TestCase):
    def test_zero_only_station_year_is_reclassified_as_missing(self):
        values = np.asarray(
            [
                [1.0, 0.0, np.nan],
                [0.0, 0.0, np.nan],
                [3.0, 0.0, np.nan],
            ]
        )
        cleaned, summaries = sanitize_station_year(values, ["active", "zero-only", "missing"])
        self.assertTrue(np.isnan(cleaned[:, 1]).all())
        self.assertEqual(summaries[0].status, "ACTIVE")
        self.assertEqual(summaries[1].status, "ZERO_ONLY_RECLASSIFIED_MISSING")
        self.assertEqual(summaries[2].status, "NO_DATA")

    def test_idw_uses_only_available_stations_and_reports_count(self):
        values = np.asarray([[2.0, np.nan], [4.0, np.nan]])
        output, counts = idw_interpolate_hourly(
            values,
            station_x=np.asarray([0.0, 10.0]),
            station_y=np.asarray([0.0, 0.0]),
            target_x=np.asarray([2.0, 5.0]),
            target_y=np.asarray([0.0, 0.0]),
            minimum_stations=1,
        )
        np.testing.assert_allclose(output, [[2.0, 2.0], [4.0, 4.0]])
        np.testing.assert_array_equal(counts, [1, 1])

    def test_minimum_station_rule_returns_missing_targets(self):
        output, counts = idw_interpolate_hourly(
            np.asarray([[2.0, np.nan]]),
            station_x=np.asarray([0.0, 10.0]),
            station_y=np.asarray([0.0, 0.0]),
            target_x=np.asarray([5.0]),
            target_y=np.asarray([0.0]),
            minimum_stations=2,
        )
        self.assertTrue(np.isnan(output[0, 0]))
        self.assertEqual(int(counts[0]), 1)

    def test_missing_colocated_station_falls_back_to_available_station(self):
        output, _ = idw_interpolate_hourly(
            np.asarray([[np.nan, 7.0]]),
            station_x=np.asarray([0.0, 10.0]),
            station_y=np.asarray([0.0, 0.0]),
            target_x=np.asarray([0.0]),
            target_y=np.asarray([0.0]),
        )
        self.assertAlmostEqual(float(output[0, 0]), 7.0)


if __name__ == "__main__":
    unittest.main()
