import unittest

import numpy as np

from xaj_routing.raster_formats import AsciiGrid
from xaj_routing.spatial import build_grid_topology
from xaj_routing.spatial_products import (
    outlet_candidates_from_result,
    placeholder_control_from_candidate,
    snap_point_to_stream,
)


class SpatialProductTests(unittest.TestCase):
    def test_outlet_candidates_and_snap_point(self):
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
        grid = AsciiGrid(
            elevation=dem,
            ncols=5,
            nrows=5,
            xllcorner=0.0,
            yllcorner=0.0,
            cellsize=100.0,
            nodata=None,
        )
        result = build_grid_topology(
            dem=dem,
            cell_size_m=100.0,
            stream_threshold_km2=0.03,
            basin_id="demo",
            spatial_version="spatial_test_v1",
        )

        candidates = outlet_candidates_from_result(result, grid, grid_crs="EPSG:32650")
        self.assertGreaterEqual(len(candidates), 1)
        self.assertIn("reach_id", candidates[0])

        row, col = candidates[0]["cell"]
        x = candidates[0]["x"]
        y = candidates[0]["y"]
        snapped = snap_point_to_stream(
            result=result,
            grid=grid,
            x=x,
            y=y,
            point_crs="EPSG:32650",
            grid_crs="EPSG:32650",
            control_id="demo_control",
        )

        self.assertEqual(snapped["snapped"]["cell"], [row, col])
        self.assertEqual(snapped["snapped"]["reach_id"], candidates[0]["reach_id"])
        self.assertEqual(snapped["snapped"]["distance_m"], 0.0)

        placeholder = placeholder_control_from_candidate(
            candidates[0],
            control_id="demo_control",
            name="Demo control",
        )
        self.assertEqual(placeholder["status"], "placeholder_from_auto_outlet")
        self.assertEqual(placeholder["snapped"]["reach_id"], candidates[0]["reach_id"])


if __name__ == "__main__":
    unittest.main()
