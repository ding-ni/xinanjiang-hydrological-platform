import unittest

import numpy as np

from xaj_routing.spatial import GridTopologyResult, ReachSegment
from xaj_routing.control_split import split_topology_at_control_points


def make_result(reaches):
    shape = (1, 8)
    zeros = np.zeros(shape, dtype=float)
    return GridTopologyResult(
        spatial_version="test_v1",
        basin_id="demo",
        cell_size_m=30.0,
        filled_dem=zeros,
        slope_deg=zeros,
        aspect_deg=zeros,
        flow_dir=np.zeros(shape, dtype=np.int16),
        accumulation_cells=np.ones(shape, dtype=float),
        accumulation_area_km2=np.ones(shape, dtype=float),
        stream_raster=np.ones(shape, dtype=bool),
        reaches=reaches,
        units=[],
    )


class ControlSplitTests(unittest.TestCase):
    def test_single_control_creates_node_and_two_reaches(self):
        result = make_result([
            ReachSegment("R1", (0, 0), (0, 7), tuple((0, c) for c in range(8)), None, 0.21),
        ])

        split = split_topology_at_control_points(
            result,
            mask=np.ones((1, 8), dtype=bool),
            controls=[{
                "control_id": "C1",
                "name": "Control 1",
                "snapped": {"reach_id": "R1", "cell": [0, 3]},
            }],
        )

        self.assertEqual([r.reach_id for r in split.reaches], ["R1_UP", "R1_DOWN"])
        self.assertEqual(split.reaches[0].downstream_reach_id, "R1_DOWN")
        self.assertEqual(split.reaches[1].downstream_reach_id, None)
        self.assertEqual(split.control_nodes[0]["upstream_reach_id"], "R1_UP")
        self.assertEqual(split.control_nodes[0]["downstream_reach_id"], "R1_DOWN")
        self.assertEqual(split.control_nodes[0]["cell"], [0, 3])

    def test_multiple_controls_preserve_network_links(self):
        result = make_result([
            ReachSegment("R1", (0, 0), (0, 7), tuple((0, c) for c in range(8)), "R2", 0.21),
            ReachSegment("R2", (0, 7), (0, 7), ((0, 7),), None, 0.03),
        ])

        split = split_topology_at_control_points(
            result,
            mask=np.ones((1, 8), dtype=bool),
            controls=[
                {"control_id": "C1", "snapped": {"reach_id": "R1", "cell": [0, 2]}},
                {"control_id": "C2", "snapped": {"reach_id": "R1", "cell": [0, 5]}},
            ],
        )

        self.assertEqual(
            [r.reach_id for r in split.reaches],
            ["R1__UP__C1", "R1__C1__C2", "R1__DOWN__C2", "R2"],
        )
        self.assertEqual(split.reaches[2].downstream_reach_id, "R2")
        self.assertEqual(split.reaches[3].downstream_reach_id, None)
        self.assertEqual(
            split.control_nodes[0]["downstream_reach_id"], "R1__C1__C2"
        )
        self.assertEqual(split.control_nodes[1]["upstream_reach_id"], "R1__C1__C2")


if __name__ == "__main__":
    unittest.main()
