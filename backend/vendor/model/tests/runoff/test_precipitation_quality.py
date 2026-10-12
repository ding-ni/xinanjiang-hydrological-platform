import unittest

import numpy as np
import xarray as xr

from runoff_platform.data.precipitation_quality import (
    precipitation_quality_code,
    read_station_count,
)


class PrecipitationQualityTests(unittest.TestCase):
    def test_reads_platform_source_station_count(self):
        dataset = xr.Dataset(
            {"source_station_count": (("time",), np.asarray([1, 3], dtype=np.int16))},
            coords={"time": [0, 1]},
        )
        values, field = read_station_count(dataset, required=True)
        np.testing.assert_array_equal(values, [1, 3])
        self.assertEqual(field, "source_station_count")

    def test_reads_external_reporting_station_count(self):
        dataset = xr.Dataset(
            {"reporting_station_count": (("time",), np.asarray([2, 5], dtype=np.int16))},
            coords={"time": [0, 1]},
        )
        values, field = read_station_count(dataset, required=True)
        np.testing.assert_array_equal(values, [2, 5])
        self.assertEqual(field, "reporting_station_count")

    def test_missing_optional_field_uses_negative_sentinel(self):
        dataset = xr.Dataset(coords={"time": [0, 1, 2]})
        values, field = read_station_count(dataset)
        np.testing.assert_array_equal(values, [-1, -1, -1])
        self.assertIsNone(field)

    def test_quality_code_is_derived_from_count(self):
        np.testing.assert_array_equal(
            precipitation_quality_code(np.asarray([0, 1, 2, 7])),
            [0, 1, 2, 2],
        )


if __name__ == "__main__":
    unittest.main()
