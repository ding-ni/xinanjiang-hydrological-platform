import json
import tempfile
import unittest
from pathlib import Path

from xaj_routing.topology_visual import build_topology_payload, write_topology_overview_html


class TopologyVisualTests(unittest.TestCase):
    def test_build_topology_payload_marks_control_reach(self):
        topology = {
            "basin_id": "nan_an",
            "spatial_version": "test_v1",
            "cell_size_m": 30,
            "reaches": [
                {
                    "reach_id": "R0001",
                    "downstream_reach_id": "R0002",
                    "length_km": 1.2,
                    "start_cell": [1, 1],
                    "end_cell": [2, 2],
                    "cell_count": 3,
                },
                {
                    "reach_id": "R0002",
                    "length_km": 2.3,
                    "start_cell": [2, 2],
                    "end_cell": [3, 3],
                    "cell_count": 4,
                },
            ],
            "units": [
                {
                    "unit_id": "U1",
                    "reach_id": "R0001",
                    "downstream_unit_id": "U2",
                    "area_km2": 4.0,
                    "zone_type": "non_glacier",
                    "outlet_cell_id": [2, 2],
                },
                {
                    "unit_id": "U2",
                    "reach_id": "R0002",
                    "area_km2": 5.0,
                    "zone_type": "non_glacier",
                    "outlet_cell_id": [3, 3],
                },
            ],
        }
        control = {
            "control_id": "xufan_70115800",
            "snapped": {
                "reach_id": "R0002",
                "cell": [3, 3],
                "distance_m": 12.5,
                "accumulation_area_km2": 9.0,
            },
        }

        payload = build_topology_payload(topology, control)

        self.assertEqual(payload["summary"]["reach_count"], 2)
        self.assertEqual(payload["summary"]["unit_count"], 2)
        self.assertEqual(payload["summary"]["control_reach_id"], "R0002")
        self.assertEqual(payload["summary"]["terminal_reaches"], ["R0002"])
        self.assertEqual(payload["reaches"][1]["upstream"], ["R0001"])
        self.assertTrue(payload["reaches"][1]["is_control"])
        self.assertTrue(payload["units"][1]["is_control"])

    def test_write_topology_overview_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology_path = root / "topology.json"
            control_path = root / "control.json"
            out_html = root / "topology.html"
            summary_out = root / "summary.json"
            topology_path.write_text(
                json.dumps(
                    {
                        "basin_id": "nan_an",
                        "spatial_version": "test_v1",
                        "reaches": [
                            {
                                "reach_id": "R0001",
                                "length_km": 1.2,
                                "start_cell": [1, 1],
                                "end_cell": [2, 2],
                            }
                        ],
                        "units": [
                            {
                                "unit_id": "U1",
                                "reach_id": "R0001",
                                "area_km2": 4.0,
                                "outlet_cell_id": [2, 2],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            control_path.write_text(
                json.dumps(
                    {
                        "control_id": "xufan_70115800",
                        "snapped": {"reach_id": "R0001", "cell": [2, 2]},
                    }
                ),
                encoding="utf-8",
            )

            summary = write_topology_overview_html(
                topology_json=topology_path,
                control_point_json=control_path,
                out_html=out_html,
                summary_out=summary_out,
            )

            html_text = out_html.read_text(encoding="utf-8")
            self.assertEqual(summary["control_reach_id"], "R0001")
            self.assertIn("南安溪-徐畈汇流拓扑可视化", html_text)
            self.assertIn("xufan_70115800", summary_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
