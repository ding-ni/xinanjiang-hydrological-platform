from __future__ import annotations

import unittest
from pathlib import Path

from integration.web_api import IntegratedResultsService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class IntegratedResultsServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = IntegratedResultsService(PROJECT_ROOT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.service.shutdown()

    def test_overview_and_archive_reference_the_same_run(self) -> None:
        overview = self.service.summary()
        archive = self.service.list_runs()

        self.assertGreaterEqual(archive["total"], 1)
        self.assertEqual(archive["items"][0]["run_id"], overview["run_id"])
        self.assertEqual(overview["source_grid_count"], 110)
        self.assertEqual(overview["reach_count"], 32)

    def test_topology_exposes_business_objects_without_server_paths(self) -> None:
        topology = self.service.topology()

        self.assertEqual(len(topology["reaches"]), 32)
        self.assertEqual(len(topology["units"]), 32)
        self.assertEqual(topology["sections"][0]["reach_id"], "R0020")
        self.assertEqual(topology["sections"][0]["upstream_reach_id"], "R0020_UP")
        self.assertEqual(topology["sections"][0]["downstream_reach_id"], "R0020_DOWN")
        self.assertEqual(topology["control_nodes"][0]["status"], "CONTROL_NODE_SPLIT")
        self.assertNotIn("path", str(topology).lower())

    def test_event_catalog_exposes_all_qc_events_and_marks_simulation_status(self) -> None:
        catalog = self.service.event_catalog()
        current = self.service.event_detail(catalog["current_event_id"])
        observed_only = self.service.event_detail("xf-31240619")

        self.assertEqual(catalog["total"], 226)
        self.assertEqual(catalog["simulation_ready_count"], int(current["simulation_available"]))
        self.assertTrue(current["run_input_available"])
        self.assertFalse(observed_only["simulation_available"])
        self.assertTrue(observed_only["observed_available"])
        self.assertNotIn("references", str(catalog).lower())

    def test_dem_preview_uses_the_registered_30_m_dem(self) -> None:
        dem = self.service.dem_preview()

        self.assertEqual(dem["resolution_m"], 30.0)
        self.assertEqual(dem["crs"], "EPSG:32650")
        self.assertGreater(len(dem["grid"]), 1)
        self.assertGreater(dem["max_elevation_m"], dem["min_elevation_m"])
        self.assertIn("未发现可标记为 3 m", dem["notice"])

    def test_non_registered_event_cannot_run_the_2017_case(self) -> None:
        with self.assertRaises(ValueError):
            self.service.submit({"event_id": "xf-31240619"})

    def test_observed_only_event_returns_real_observed_series_without_simulation(self) -> None:
        series = self.service.event_series("xf-31240619")

        self.assertEqual(series["event_status"], "OBSERVED_ONLY")
        self.assertGreater(len(series["time"]), 1)
        self.assertEqual(len(series["time"]), len(series["observed"]))
        self.assertEqual(len(series["time"]), len(series["simulated"]))
        self.assertTrue(all(value is None for value in series["simulated"]))

    def test_series_and_diagnostics_share_the_current_run_id(self) -> None:
        run_id = self.service.summary()["run_id"]
        if self.service.summary()["run_status"] != "COMPLETED":
            self.skipTest("base routing results are not included in this package")
        series = self.service.series(run_id)
        diagnostics = self.service.diagnostics(run_id)

        self.assertEqual(series["run_id"], run_id)
        self.assertEqual(diagnostics["run_id"], run_id)
        self.assertEqual(len(series["time"]), 241)
        self.assertEqual(len(series["observed"]), len(series["simulated"]))
        self.assertTrue(diagnostics["components"])
        self.assertAlmostEqual(series["metrics"]["nse"], 0.3906, places=3)

    def test_unknown_run_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            self.service.series("missing-run")

    def test_point_query_rebuilds_and_reruns_when_split_reach_is_not_persisted(self) -> None:
        if self.service.summary()["run_status"] != "COMPLETED":
            self.skipTest("point rerun requires base routing results")
        result = self.service.point_flow_query(
            {
                "lon": 119.519596908172,
                "lat": 30.0978640819064,
                "channel_width_m": 20,
                "run_id": self.service.summary()["run_id"],
            }
        )

        self.assertEqual(result["method"], "TEMPORARY_CONTROL_SPLIT_RERUN")
        self.assertEqual(result["quality"]["status"], "RERUN_COMPLETED")
        self.assertEqual(result["topology"]["split_status"], "RERUN_COMPLETED")
        self.assertFalse(result["topology"]["requires_rerun"])
        self.assertEqual(result["method_details"]["rerun_status"], "COMPLETED")
        self.assertEqual(result["method_details"]["rerun_product_id"], result["point_query_id"])
        self.assertNotIn("rerun_output_dir", str(result))
        self.assertNotIn("D:\\研究生\\XAJ", str(result))
        self.assertEqual(len(result["series"]["time"]), 241)

    def test_spatial_rainfall_uses_real_grid_ids(self) -> None:
        geometry = self.service.grid_geometry()
        rain = self.service.flood_grid_rainfall("31640404", "1964-04-04T20:00")
        geometry_ids = {feature["properties"]["unit_id"] for feature in geometry["features"]}
        rain_ids = {cell["unit_id"] for cell in rain["cells"]}
        self.assertEqual(len(geometry_ids), 110)
        self.assertEqual(geometry_ids, rain_ids)
        self.assertTrue(all(cell["quality_flag"] in {"OK", "MISSING", "REVIEW_HIGH"}
                            for cell in rain["cells"]))

    def test_grid_state_snapshot_matches_a_unit_time_series(self) -> None:
        run_id = self.service.summary()["run_id"]
        series = self.service.grid_state(run_id, unit_id="NX-001",
                                         start="2017-06-19T11:00", end="2017-06-19T13:00")
        snapshot = self.service.grid_state(run_id, unit_id="NX-001",
                                           timestamp="2017-06-19T12:00")
        self.assertEqual(series["record_count"], 3)
        self.assertEqual(snapshot["record_count"], 1)
        self.assertEqual(snapshot["records"][0]["total_runoff_mm"],
                         series["records"][1]["total_runoff_mm"])


if __name__ == "__main__":
    unittest.main()
