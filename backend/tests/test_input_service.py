"""Read-only QC input contracts, missing-data handling and real-file checks."""
import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import MODEL, read_json, write_json
from input_service import InputService, FORCING_VERSION, OBSERVATION_VERSION, MAX_WARMUP_HOURS, MAX_EVENT_WINDOW_HOURS


class InputFixture:
    def __init__(self, root):
        import numpy as np
        import pandas as pd
        import xarray as xr
        self.np, self.pd, self.xr = np, pd, xr
        self.root = Path(root)
        self.times = pd.date_range("2017-12-31T22:00", "2018-01-01T03:00", freq="h")
        self.precip = np.zeros((6, 2, 2), dtype=np.float32)
        self.precip[:, 1, 0] = [1, 2, 3, 4, 5, 6]  # NX-001
        self.precip[:, 0, 1] = [9, 8, 7, 6, 5, 4]  # NX-002
        self.precip[:, 0, 0] = 100                  # Downstream NX-003
        self.counts = np.array([2, 2, 3, 3, 3, 3], dtype=np.int16)
        self.flows = [1, 2, 3, 4, 5, 6]
        self.precipitation_dir = self.root / "data_interface/precipitation_qc_20260819/1km_hourly"
        self.precipitation_dir.mkdir(parents=True)
        grids = [
            {"unit_id": "NX-001", "source_row": 11, "source_col": 15, "array_row": 1,
             "array_col": 0, "elevation_m": 255, "area_km2": 1},
            {"unit_id": "NX-002", "source_row": 11, "source_col": 16, "array_row": 0,
             "array_col": 1, "elevation_m": 468, "area_km2": 3},
            {"unit_id": "NX-003", "source_row": 12, "source_col": 16, "array_row": 0,
             "array_col": 0, "elevation_m": 100, "area_km2": 10},
        ]
        self.csv("data_interface/runoff_2017/grid_static.csv", grids)
        features = []
        for index, grid in enumerate(grids):
            x = 119 + index / 100
            ring = [[x, 30], [x + 0.01, 30], [x + 0.01, 29.99], [x, 29.99], [x, 30]]
            features.append({"type": "Feature", "properties": {"row": grid["source_row"], "col": grid["source_col"]},
                             "geometry": {"type": "Polygon", "coordinates": [ring]}})
        write_json(self.root / "data_interface/spatial/南安溪1公里有效格点.geojson",
                   {"type": "FeatureCollection", "features": features})
        write_json(self.root / "data_interface/spatial/nan_an_topology_control_split_v2.json", {
            "reaches": [{"reach_id": "R0001", "downstream_reach_id": "R0020_UP"},
                        {"reach_id": "R0020_UP", "downstream_reach_id": "R0020_DOWN"},
                        {"reach_id": "R0020_DOWN"}],
            "units": [{"unit_id": "U1", "reach_id": "R0001", "area_km2": 3},
                      {"unit_id": "U2", "reach_id": "R0020_UP", "area_km2": 0.5},
                      {"unit_id": "U3", "reach_id": "R0020_DOWN", "area_km2": 10.5}],
        })
        self.csv("data_interface/spatial/grid_to_unit_weight_control_split_v2.csv", [
            {"grid_id": "NX-001", "unit_id": "U2", "adjusted_overlap_area_km2": 0.5},
            {"grid_id": "NX-001", "unit_id": "U3", "adjusted_overlap_area_km2": 0.5},
            {"grid_id": "NX-002", "unit_id": "U1", "adjusted_overlap_area_km2": 3},
            {"grid_id": "NX-003", "unit_id": "U3", "adjusted_overlap_area_km2": 10},
        ])
        write_json(self.root / "data_interface/event_catalog/nananxi_flood_event_catalog_qc_20260819.json", {
            "packages": [{"eventId": "xf-31180101", "floodCode": "31180101", "start": "2018-01-01T00:00",
                          "end": "2018-01-01T03:00", "status": "ready", "durationHours": 4,
                          "observedInputs": {"rainCompleteness": 1.0, "riverCompleteness": 1.0},
                          "spatialRainInputs": {"modelContributingAreaKm2": 99},
                          "qcRevision": {"forcingVersion": FORCING_VERSION}}],
        })
        write_json(self.root / "config/event_quality_config.json", {
            "config_id": "TEST-QUALITY", "basin_area_km2": 64.1,
            "rules": {"maximum_duration_hours": 720, "minimum_paired_flow_hours": 1,
                      "minimum_precipitation_valid_fraction": 0.999, "require_source_station_count": True,
                      "minimum_source_stations": 1, "minimum_source_station_hour_fraction": 0.999,
                      "minimum_warmup_event_rain_mm": 1, "maximum_observed_runoff_to_precipitation_ratio": 100},
            "warnings": {},
        })
        self.write_precipitation()
        self.write_flow()

    def csv(self, relative, rows):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def write_flow(self):
        self.csv("data_interface/observations/xufan_outlet_hourly_qc.csv", [
            {"time": time.isoformat(sep=" "), "flow_m3_s": flow}
            for time, flow in zip(self.times, self.flows)
        ])

    def write_precipitation(self, missing_counts=False, omit_time=None, unit="mm"):
        for year in (2017, 2018):
            positions = [index for index, time in enumerate(self.times)
                         if time.year == year and index != omit_time]
            station_name = "source_station_count" if year == 2017 else "reporting_station_count"
            fields = {"precipitation": (("time", "lat", "lon"), self.precip[positions])}
            if not missing_counts:
                fields[station_name] = (("time",), self.counts[positions])
            dataset = self.xr.Dataset(fields, coords={"time": self.times[positions], "lat": [30, 29.99], "lon": [119, 119.01]})
            dataset["precipitation"].attrs["units"] = unit
            dataset.to_netcdf(self.precipitation_dir / f"nananxi_precip_hourly_1km_{year}.nc", engine="h5netcdf")

    def hashes(self):
        return {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}


class InputServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = InputFixture(self.temporary.name)
        self.service = InputService(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def window(self):
        return self.service.window("2017-12-31T22:00", "2018-01-01T03:00")

    def test_constructor_lazy_and_case_validation_before_reading(self):
        service = InputService(Path(self.temporary.name) / "not-registered")
        self.assertFalse(service._initialized)
        for operation in (service.catalog, service.window):
            for project, resolution in (("selinco", "1km"), ("nananxi-xufan", "0p1deg"), ("../outside", "1km")):
                with self.subTest(project=project, resolution=resolution), self.assertRaises(ValueError):
                    operation(project_id=project, resolution_id=resolution)
        self.assertFalse(service._initialized)

    def test_geometry_grid_order_and_upstream_overlap_weights(self):
        import numpy as np
        result = self.window()
        cells = result["product"]["cells"]
        self.assertEqual([cell["id"] for cell in cells], ["NX-001", "NX-002", "NX-003"])
        self.assertEqual(cells[0]["row"], 11)
        self.assertEqual(cells[0]["col"], 15)
        self.assertAlmostEqual(cells[0]["bounds"][0][0], 119)
        self.assertEqual(cells[0]["values"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(cells[2]["values"], [100] * 6)
        self.assertEqual(result["summary"]["controlAreaKm2"], 3.5)
        self.assertEqual(result["summary"]["observationAreaKm2"], 64.1)
        self.assertEqual(result["summary"]["projectAreaKm2"], 14)
        self.assertEqual(cells[2]["controlAreaKm2"], 0)
        expected = (np.array([1, 2, 3, 4, 5, 6]) * 0.5 + np.array([9, 8, 7, 6, 5, 4]) * 3) / 3.5
        np.testing.assert_allclose(result["summary"]["arealRainfallMm"], expected, rtol=1e-14)
        self.assertAlmostEqual(sum(cell["controlWeight"] for cell in cells), 1)

    def test_cross_year_fields_observations_and_provenance(self):
        result = self.window()
        self.assertEqual(len(result["product"]["times"]), 6)
        self.assertEqual(result["product"]["times"][2], "2018-01-01T00:00:00")
        self.assertEqual(result["summary"]["reportingStationCount"], [2, 2, 3, 3, 3, 3])
        self.assertEqual(result["quality"]["stationCountFields"], ["reporting_station_count", "source_station_count"])
        preview = result["observationPreview"]
        self.assertEqual(preview["rain"], {})
        self.assertEqual(preview["river"]["flows"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(preview["river"]["levels"], [None] * 6)
        self.assertEqual(preview["river"]["spatialCode"], "70115800")
        self.assertEqual(result["sourceType"], "model-qc")
        self.assertEqual(result["forcingVersion"], FORCING_VERSION)
        self.assertEqual(result["provenance"]["observationVersion"], OBSERVATION_VERSION)
        self.assertEqual(result["provenance"]["controlReachId"], "R0020_UP")
        paths = [row["path"] for row in result["provenance"]["files"]]
        self.assertTrue(any(path.endswith("_2017.nc") for path in paths))
        self.assertTrue(any(path.endswith("_2018.nc") for path in paths))
        for file in result["provenance"]["files"]:
            self.assertFalse(Path(file["path"]).is_absolute())
            self.assertEqual(file["sha256"], hashlib.sha256((self.fixture.root / file["path"]).read_bytes()).hexdigest())
        self.assertNotIn("simulated", result)
        self.assertNotIn("runId", result)
        json.dumps(result, allow_nan=False)

    def test_missing_values_are_null_not_zero_or_area_renormalized(self):
        self.fixture.precip[2, 1, 0] = float("nan")
        self.fixture.flows[4] = ""
        self.fixture.write_precipitation()
        self.fixture.write_flow()
        result = self.window()
        self.assertIsNone(result["product"]["cells"][0]["values"][2])
        self.assertIsNone(result["summary"]["arealRainfallMm"][2])
        self.assertIsNone(result["observationPreview"]["river"]["flows"][4])
        self.assertIn("PRECIPITATION_INCOMPLETE", result["quality"]["warningReasons"])
        self.assertIn("FLOW_INCOMPLETE", result["quality"]["warningReasons"])
        self.assertAlmostEqual(result["summary"]["flowCompleteness"], 5 / 6)
        json.dumps(result, allow_nan=False)

    def test_missing_station_counts_are_not_fabricated(self):
        self.fixture.write_precipitation(missing_counts=True)
        result = self.window()
        self.assertEqual(result["summary"]["reportingStationCount"], [None] * 6)
        self.assertFalse(result["quality"]["sourceStationCountAvailable"])
        self.assertIn("SOURCE_STATION_COUNT_UNAVAILABLE", result["quality"]["warningReasons"])
        audit = self.service.event("xf-31180101", warmup_hours=2)
        self.assertFalse(audit["quality"]["accepted"])
        self.assertIn("SOURCE_STATION_COUNT_UNAVAILABLE", audit["quality"]["exclusion_reasons"])

    def test_negative_clip_matches_worker_and_is_disclosed(self):
        self.fixture.precip[0, 1, 0] = -2
        self.fixture.counts[1] = 0
        self.fixture.write_precipitation()
        result = self.window()
        self.assertEqual(result["product"]["cells"][0]["values"][0], 0)
        self.assertEqual(result["quality"]["negativePrecipitationClippedCount"], 1)
        self.assertEqual(result["summary"]["reportingStationCount"][1], 0)
        self.assertIn("NEGATIVE_PRECIPITATION_CLIPPED_AS_WORKER", result["quality"]["warningReasons"])
        self.assertIn("SOURCE_STATIONS_BELOW_MINIMUM", result["quality"]["warningReasons"])

    def test_source_time_gap_remains_visible(self):
        self.fixture.write_precipitation(omit_time=3)
        result = self.window()
        self.assertEqual(len(result["product"]["times"]), 6)
        self.assertEqual(result["quality"]["missingHours"], 1)
        self.assertIsNone(result["summary"]["arealRainfallMm"][3])
        self.assertIsNone(result["summary"]["reportingStationCount"][3])
        self.assertIn("PRECIPITATION_TIME_GAP", result["quality"]["warningReasons"])
        self.assertFalse(self.service.event("xf-31180101", warmup_hours=2)["quality"]["model_input_ready"])

    def test_catalog_default_latest_paired_window_and_cache_isolation(self):
        catalog = self.service.catalog()
        self.assertEqual(catalog["rain"]["start"], "2017-12-31T22:00:00")
        self.assertEqual(catalog["river"]["end"], "2018-01-01T03:00:00")
        self.assertEqual(catalog["coverage"]["precipitation"]["years"], [2017, 2018])
        self.assertEqual(catalog["latestPeriod"]["hours"], 6)
        self.assertEqual(catalog["events"][0]["id"], "xf-31180101")
        self.assertFalse(catalog["hasStationRainfall"])
        self.assertFalse(catalog["hasWaterLevel"])
        self.assertEqual(self.service.window()["product"], self.window()["product"])
        catalog["events"][0]["start"] = "mutated"
        self.assertEqual(self.service.catalog()["events"][0]["start"], "2018-01-01T00:00")

    def test_event_audit_uses_actual_control_weights_and_warmup(self):
        import numpy as np
        result = self.service.event("xf-31180101", warmup_hours=2)
        self.assertEqual(result["event"]["eventId"], "xf-31180101")
        self.assertEqual(result["eventPeriod"]["start"], "2018-01-01T00:00:00")
        self.assertEqual(result["warmupPeriod"], {"start": "2017-12-31T22:00:00",
                                                "end": "2017-12-31T23:00:00", "hours": 2})
        self.assertEqual(result["product"]["times"][0], "2018-01-01T00:00:00")
        self.assertEqual(result["summary"]["windowHours"], 4)
        self.assertEqual(result["qualityPeriod"]["hours"], 6)
        rainfall = np.asarray(result["summary"]["arealRainfallMm"])
        all_rainfall = np.asarray(self.window()["summary"]["arealRainfallMm"])
        self.assertAlmostEqual(result["quality"]["event_rain_mm"], rainfall.sum())
        self.assertAlmostEqual(result["quality"]["warmup_event_rain_mm"], all_rainfall.sum())
        expected_depth = sum(self.fixture.flows[2:]) * 3600 / (64.1 * 1_000_000) * 1000
        self.assertAlmostEqual(result["quality"]["observed_runoff_depth_mm"], expected_depth)
        self.assertTrue(result["quality"]["accepted"])
        self.assertTrue(result["quality"]["model_input_ready"])
        self.assertEqual(result["event"]["spatialRainInputs"]["modelContributingAreaKm2"], 99)
        self.assertEqual(result["summary"]["controlAreaKm2"], 3.5)
        self.assertIsNone(self.service.event("xf-31180101", warmup_hours=0)["warmupPeriod"]["end"])

    def test_reject_bad_window_event_identifiers_and_unit(self):
        for start, end in (("2017-12-31T22:30", "2018-01-01T03:00"),
                           ("2017-12-31T22:00Z", "2018-01-01T03:00"),
                           ("2018-01-01T03:00", "2017-12-31T22:00"),
                           ("2017-01-01T00:00", "2017-12-31T23:00"),
                           ("2017-12-31T22:00", None)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.service.window(start, end)
        with self.assertRaises(FileNotFoundError):
            self.service.window("2019-01-01T00:00", "2019-01-01T01:00")
        for event in ("../outside", "/absolute", "xf/31180101"):
            with self.assertRaises(ValueError):
                self.service.event(event)
        with self.assertRaises(KeyError):
            self.service.event("xf-missing")
        for warmup in (True, -1, 1.5, "2", MAX_WARMUP_HOURS + 1):
            with self.assertRaises(ValueError):
                self.service.event("xf-31180101", warmup_hours=warmup)
        self.fixture.write_precipitation(unit="mm/s")
        with self.assertRaises(ValueError):
            self.window()

    def test_explicit_year_warmup_is_audited_not_serialized_into_product(self):
        from unittest.mock import patch
        import numpy as np
        import pandas as pd
        self.service._initialize()
        # A compact artificial full-year forcing avoids writing a large NC in
        # this contract test; real-year provenance remains a separate regression.
        full_times = pd.date_range("2017-01-01T00:00", "2018-01-01T03:00", freq="h")
        values = np.full((len(full_times), 3), 1.0)
        counts = np.full(len(full_times), 2, dtype=np.int16)
        arrays = {"times": full_times, "values": values, "counts": counts,
                  "observed": pd.Series(1.0, index=full_times), "rainfall": np.ones(len(full_times)),
                  "years": {2017, 2018}, "present": np.ones(len(full_times), dtype=bool),
                  "countFields": ["reporting_station_count"], "negativeCount": 0}
        with patch.object(self.service, "_read_arrays", return_value=arrays) as loader:
            result = self.service.event("xf-31180101", warmup_hours=365 * 24)
        self.assertEqual(loader.call_args.args[2], MAX_EVENT_WINDOW_HOURS)
        self.assertEqual(result["warmupPeriod"]["hours"], 8760)
        self.assertEqual(result["warmupPeriod"]["start"], "2017-01-01T00:00:00")
        self.assertEqual(result["qualityPeriod"]["hours"], 8764)
        self.assertEqual(len(result["product"]["times"]), 4)
        self.assertTrue(all(len(cell["values"]) == 4 for cell in result["product"]["cells"]))
        self.assertEqual(result["quality"]["warmup_event_rain_mm"], 8764)
        self.assertEqual(result["quality"]["event_rain_mm"], 4)
        self.assertFalse(result["qualityPolicy"]["eventProductIncludesWarmup"])
        self.assertEqual(result["qualityPolicy"]["maximumWarmupHours"], 8760)
        with self.assertRaises(ValueError):
            self.service.window("2017-01-01T00:00", "2018-01-01T03:00")

    def test_thread_safety_and_entire_input_tree_unchanged(self):
        before = self.fixture.hashes()
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda _: self.window(), range(8)))
        for response in responses:
            self.assertEqual(response["product"], responses[0]["product"])
        self.service.catalog()
        self.service.event("xf-31180101", warmup_hours=2)
        self.assertEqual(before, self.fixture.hashes())


@unittest.skipUnless(os.environ.get("XAJ_TEST_REAL_INPUTS") == "1", "Opt-in fixed QC numerical input regression")
class RealInputTests(unittest.TestCase):
    def test_registered_window_agrees_with_current_worker_chain_and_nc(self):
        import numpy as np
        import pandas as pd
        import xarray as xr
        service = InputService()
        result = service.window("2017-06-19T11:00", "2017-06-20T11:00")
        self.assertEqual(result["summary"]["gridCount"], 110)
        self.assertEqual(result["summary"]["contributingGridCount"], 96)
        self.assertAlmostEqual(result["summary"]["controlAreaKm2"], 63.27190569363709, places=9)
        self.assertEqual(result["summary"]["observationAreaKm2"], 64.1)
        with xr.open_dataset(MODEL / "data_interface/precipitation_qc_20260819/1km_hourly/nananxi_precip_hourly_1km_2017.nc",
                             engine="h5netcdf") as dataset:
            subset = dataset.sel(time=slice("2017-06-19T11:00", "2017-06-20T11:00"))
            direct = np.stack([subset.precipitation.values[:, int(row["array_row"]), int(row["array_col"])]
                               for row in service.grids], axis=1)
            np.testing.assert_allclose(np.array([cell["values"] for cell in result["product"]["cells"]]).T,
                                       np.maximum(direct, 0), rtol=0, atol=0)
            np.testing.assert_allclose(result["summary"]["arealRainfallMm"],
                                       np.average(direct, axis=1, weights=service.areas), rtol=1e-12, atol=1e-12)
            self.assertEqual(result["summary"]["reportingStationCount"], subset.reporting_station_count.values.tolist())
        flow = pd.read_csv(MODEL / "data_interface/observations/xufan_outlet_hourly_qc.csv")
        series = pd.Series(flow.flow_m3_s.values, index=pd.to_datetime(flow.time))
        np.testing.assert_allclose(result["observationPreview"]["river"]["flows"],
                                   series.reindex(pd.to_datetime(result["product"]["times"])).values)
        before = {row["path"]: row["sha256"] for row in result["provenance"]["files"]}
        latest = service.window()
        self.assertEqual(len(latest["product"]["times"]), 72)
        self.assertEqual(latest["summary"]["rainfallCompleteness"], 1)
        self.assertEqual(latest["summary"]["flowCompleteness"], 1)
        event = service.event("xf-31170619")
        self.assertEqual(event["warmupPeriod"]["hours"], 336)
        self.assertEqual(event["summary"]["controlAreaKm2"], result["summary"]["controlAreaKm2"])
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256((MODEL / path).read_bytes()).hexdigest(), digest)
        json.dumps(result, allow_nan=False)
        json.dumps(event, allow_nan=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
