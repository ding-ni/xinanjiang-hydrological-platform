import unittest

from xaj_routing.engine import RoutingEngine
from xaj_routing.models import ControlPoint, RoutingParameters, RunoffRecord
from xaj_routing.topology import Topology


class EngineTests(unittest.TestCase):
    def test_routes_units_to_downstream_outlet(self):
        topology = Topology.from_mapping(
            {
                "units": [
                    {
                        "unit_id": "U1",
                        "area_km2": 1.0,
                        "zone_type": "non_glacier",
                        "reach_id": "R1",
                    },
                    {
                        "unit_id": "U2",
                        "area_km2": 1.0,
                        "zone_type": "glacier",
                        "reach_id": "R2",
                    },
                ],
                "reaches": [
                    {"reach_id": "R1", "downstream_reach_id": "R2"},
                    {"reach_id": "R2", "downstream_reach_id": None},
                ],
            }
        )
        parameters = RoutingParameters.from_mapping(
            {
                "time_step_hours": 1.0,
                "default_slope_k_hours": {
                    "RS": 0.01,
                    "RI": 0.01,
                    "RG": 0.01,
                    "GLACIER_FAST": 0.01,
                    "GLACIER_SLOW": 0.01,
                    "GLACIER_BASE": 0.01,
                },
                "reach_muskingum": {},
            }
        )
        records = [
            RunoffRecord.from_mapping(
                {
                    "unit_id": "U1",
                    "time": "2026-08-03 00:00:00",
                    "RS": 3.6,
                }
            ),
            RunoffRecord.from_mapping(
                {
                    "unit_id": "U2",
                    "time": "2026-08-03 00:00:00",
                    "glacier_fast": 3.6,
                }
            ),
        ]

        result = RoutingEngine(topology, parameters).run(records)

        outlet = result.outlet_flows[-1]
        self.assertEqual(outlet.outlet_id, "R2")
        self.assertAlmostEqual(outlet.outflow_cms, 2.0, places=6)
        self.assertEqual(
            sorted(flow.component_type for flow in result.component_flows),
            ["GLACIER_FAST", "RS"],
        )

    def test_routes_configured_control_point(self):
        topology = Topology.from_mapping(
            {
                "units": [
                    {"unit_id": "U1", "area_km2": 1.0, "reach_id": "R1"},
                    {"unit_id": "U2", "area_km2": 1.0, "reach_id": "R2"},
                ],
                "reaches": [
                    {"reach_id": "R1", "downstream_reach_id": "R2"},
                    {"reach_id": "R2", "downstream_reach_id": None},
                ],
            }
        )
        parameters = RoutingParameters.from_mapping(
            {
                "time_step_hours": 1.0,
                "default_slope_k_hours": {
                    "RS": 0.01,
                    "RI": 0.01,
                    "RG": 0.01,
                    "GLACIER_FAST": 0.01,
                    "GLACIER_SLOW": 0.01,
                    "GLACIER_BASE": 0.01,
                },
                "reach_muskingum": {},
            }
        )
        records = [
            RunoffRecord.from_mapping(
                {"unit_id": "U1", "time": "2026-08-03 00:00:00", "RS": 3.6}
            ),
            RunoffRecord.from_mapping(
                {"unit_id": "U2", "time": "2026-08-03 00:00:00", "RS": 3.6}
            ),
        ]

        result = RoutingEngine(
            topology,
            parameters,
            control_points=[
                ControlPoint(
                    control_id="control_r1",
                    reach_id="R1",
                    name="Control R1",
                )
            ],
        ).run(records)

        self.assertEqual(len(result.control_flows), 1)
        self.assertEqual(result.control_flows[0].control_id, "control_r1")
        self.assertEqual(result.control_flows[0].reach_id, "R1")
        self.assertAlmostEqual(result.control_flows[0].outflow_cms, 1.0, places=6)
        self.assertAlmostEqual(result.outlet_flows[-1].outflow_cms, 2.0, places=6)

    def test_control_point_references_missing_reach_fails_fast(self):
        topology = Topology.from_mapping(
            {
                "units": [{"unit_id": "U1", "area_km2": 1.0, "reach_id": "R1"}],
                "reaches": [{"reach_id": "R1"}],
            }
        )
        parameters = RoutingParameters.from_mapping({"time_step_hours": 1.0})
        with self.assertRaisesRegex(ValueError, "missing reach R2"):
            RoutingEngine(
                topology,
                parameters,
                control_points=[ControlPoint(control_id="bad", reach_id="R2")],
            )

    def test_unknown_unit_fails_fast(self):
        topology = Topology.from_mapping({"units": [], "reaches": []})
        parameters = RoutingParameters.from_mapping({"time_step_hours": 1.0})
        record = RunoffRecord.from_mapping(
            {"unit_id": "missing", "time": "2026-08-03 00:00:00", "RS": 1.0}
        )
        with self.assertRaisesRegex(ValueError, "missing unit"):
            RoutingEngine(topology, parameters).run([record])

    def test_negative_runoff_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "RS must be non-negative"):
            RunoffRecord.from_mapping(
                {"unit_id": "U1", "time": "2026-08-03 00:00:00", "RS": -1.0}
            )

    def test_slope_reservoir_recedes_when_current_runoff_is_zero(self):
        topology = Topology.from_mapping(
            {
                "units": [{"unit_id": "U1", "area_km2": 1.0, "reach_id": "R1"}],
                "reaches": [{"reach_id": "R1"}],
            }
        )
        parameters = RoutingParameters.from_mapping(
            {
                "time_step_hours": 1.0,
                "default_slope_k_hours": {"RS": 1.0},
                "reach_muskingum": {},
            }
        )
        records = [
            RunoffRecord.from_mapping(
                {"unit_id": "U1", "time": "2026-08-03 00:00:00", "RS": 3.6}
            ),
            RunoffRecord.from_mapping(
                {"unit_id": "U1", "time": "2026-08-03 01:00:00", "RS": 0.0}
            ),
        ]

        result = RoutingEngine(topology, parameters).run(records)

        rs_rows = [row for row in result.component_flows if row.component_type == "RS"]
        self.assertEqual(len(rs_rows), 2)
        self.assertEqual(rs_rows[1].runoff_mm, 0.0)
        self.assertGreater(rs_rows[1].slope_outflow_cms, 0.0)
        self.assertLess(rs_rows[1].slope_outflow_cms, rs_rows[0].slope_outflow_cms)


if __name__ == "__main__":
    unittest.main()
