import unittest

import numpy as np

from xaj_routing.spatial import (
    build_grid_topology,
    compute_d8_flow_dir,
    compute_flow_accumulation,
    fill_depressions_priority_flood,
)


class SpatialTests(unittest.TestCase):
    def test_fill_depressions_raises_closed_sink(self):
        dem = np.array(
            [
                [10, 10, 10, 10, 10],
                [10, 5, 5, 5, 10],
                [10, 5, 1, 5, 10],
                [10, 5, 5, 5, 4],
                [10, 10, 10, 10, 10],
            ],
            dtype=float,
        )

        filled = fill_depressions_priority_flood(dem, epsilon=0.0)

        self.assertGreater(filled[2, 2], dem[2, 2])
        self.assertAlmostEqual(filled[2, 2], 5.0)

    def test_flow_accumulation_on_east_slope(self):
        dem = np.array(
            [
                [9, 8, 7],
                [9, 8, 7],
                [9, 8, 7],
            ],
            dtype=float,
        )
        flow_dir = compute_d8_flow_dir(dem, cell_size_m=100.0)
        accumulation = compute_flow_accumulation(flow_dir)

        self.assertTrue(np.all(flow_dir[:, 0] == 1))
        self.assertTrue(np.all(flow_dir[:, 1] == 1))
        self.assertTrue(np.all(flow_dir[:, 2] == 0))
        self.assertTrue(np.allclose(accumulation[:, 0], 1.0))
        self.assertTrue(np.allclose(accumulation[:, 1], 2.0))
        self.assertTrue(np.allclose(accumulation[:, 2], 3.0))

    def test_build_grid_topology_outputs_units_and_reaches(self):
        dem = np.array(
            [
                [110, 108, 106, 104, 102],
                [108, 105, 103, 101, 99],
                [106, 103, 100, 98, 96],
                [104, 101, 98, 95, 93],
                [102, 99, 96, 93, 90],
            ],
            dtype=float,
        )

        result = build_grid_topology(
            dem=dem,
            cell_size_m=100.0,
            stream_threshold_km2=0.03,
            basin_id="demo",
            spatial_version="spatial_test_v1",
        )

        self.assertGreaterEqual(len(result.reaches), 1)
        self.assertGreaterEqual(len(result.units), 1)
        self.assertIn("unit_id", result.units[0])
        self.assertIn("reach_id", result.units[0])
        self.assertEqual(result.spatial_version, "spatial_test_v1")


if __name__ == "__main__":
    unittest.main()
