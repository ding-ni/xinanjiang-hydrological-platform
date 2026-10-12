import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from xaj_routing.control_area import (
    contributing_grid_areas,
    contributing_reach_ids,
    contributing_topology_area,
)
from xaj_routing.topology import Topology


class ControlAreaTests(unittest.TestCase):
    def setUp(self):
        self.topology = Topology.from_mapping(
            {
                "reaches": [
                    {"reach_id": "R1", "downstream_reach_id": "R2"},
                    {"reach_id": "R2", "downstream_reach_id": "R3"},
                    {"reach_id": "R3", "downstream_reach_id": None},
                ],
                "units": [
                    {"unit_id": "U1", "reach_id": "R1", "area_km2": 2.0},
                    {"unit_id": "U2", "reach_id": "R2", "area_km2": 3.0},
                    {"unit_id": "U3", "reach_id": "R3", "area_km2": 4.0},
                ],
            }
        )

    def test_upstream_reaches_stop_at_control(self):
        self.assertEqual(contributing_reach_ids(self.topology, "R2"), {"R1", "R2"})
        self.assertEqual(contributing_topology_area(self.topology, "R2"), 5.0)

    def test_grid_area_excludes_downstream_units(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["grid_id", "unit_id", "adjusted_overlap_area_km2"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"grid_id": "G1", "unit_id": "U1", "adjusted_overlap_area_km2": 1.5},
                        {"grid_id": "G1", "unit_id": "U3", "adjusted_overlap_area_km2": 0.5},
                        {"grid_id": "G2", "unit_id": "U2", "adjusted_overlap_area_km2": 3.0},
                    ]
                )
            areas = contributing_grid_areas(path, self.topology, "R2", ["G1", "G2"])
            np.testing.assert_allclose(areas, [1.5, 3.0])


if __name__ == "__main__":
    unittest.main()
