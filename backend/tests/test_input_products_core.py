"""Local input products: no source writes, no fabricated rain, no silent switch."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json, write_json
from input_products import InputProducts, DEFAULT_BUNDLE_VERSION_ID, file_sha256
from input_product_worker import process_task
from test_input_service import InputFixture


class InputProductCoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = InputFixture(self.root / "model")
        self.products = InputProducts(self.root / "model", self.root / "runtime")

    def tearDown(self):
        self.temporary.cleanup()

    def source_payload(self, values=None):
        return {"schemaVersion": "1.0", "projectId": "nananxi-xufan", "resolutionId": "1km",
                "name": "Isolated contract fixture, not measured observations", "provenanceNote": "Unit-test-only artificial values",
                "unit": "mm", "crs": "EPSG:4326", "timeConvention": "SOURCE_LOCAL_NAIVE_HOURLY",
                "times": [time.isoformat() for time in self.fixture.times],
                "stations": [{"id": "TEST-STATION", "lon": 119, "lat": 30,
                              "values": [0, 0, 0, 0, 0, 0] if values is None else values}]}

    def processing_request(self, operation="qcWindow", **extra):
        return {"requestId": "DATA-TEST-UNIQUE", "projectId": "nananxi-xufan", "resolutionId": "1km",
                "operation": operation, "name": "Isolated diagnostic fixture product",
                "start": "2017-12-31T22:00", "end": "2018-01-01T03:00", **extra}

    def execute(self, request, suffix="test"):
        task_id = "DATA-" + suffix
        folder = self.products.tasks_root / task_id
        normalized = self.products.normalize_processing_request(request)
        write_json(folder / "request.json", normalized)
        write_json(folder / "status.json", {"taskId": task_id, "status": "running"})
        result = process_task(folder, self.products)
        return folder, result

    def complete(self, folder):
        write_json(folder / "status.json", {"taskId": folder.name, "status": "completed"})

    def test_baseline_stays_original_and_read_only(self):
        before = self.fixture.hashes()
        baseline = self.products.resolve()
        self.assertEqual(baseline["bundleVersionId"], DEFAULT_BUNDLE_VERSION_ID)
        self.assertEqual(baseline["forcingVersion"], "precipitation_qc_20260819")
        self.assertEqual(baseline["observationVersion"], "xufan_outlet_hourly_qc_20260819")
        self.assertEqual(baseline["modelInputVersion"], "nananxi-qc20260819-control-split-v2")
        self.assertEqual(baseline["precipitationDir"], self.fixture.precipitation_dir)
        self.assertEqual(len(self.products.list_versions()["versions"]), 1)
        self.assertEqual(self.products.list_sources(), {"sources": []})
        self.assertTrue((self.products.inputs_root / "fixed-baseline-manifest.json").is_file())
        self.assertFalse(self.products.sources_root.exists())
        self.assertFalse(self.products.products_root.exists())
        self.assertEqual(before, self.fixture.hashes())

    def test_baseline_changed_file_does_not_redefine_same_version(self):
        baseline = self.products.resolve()
        path = self.fixture.root / "config/event_quality_config.json"
        changed = read_json(path)
        changed["config_id"] = "CHANGED-AFTER-BASELINE-PIN"
        write_json(path, changed)
        new_instance = InputProducts(self.products.model_root, self.products.runtime_root)
        with self.assertRaises(ValueError):
            new_instance.resolve()
        pinned = read_json(self.products.inputs_root / "fixed-baseline-manifest.json")
        self.assertEqual(pinned["manifestSHA256"], baseline["manifestSHA256"])

    def test_source_keeps_null_negative_and_zero_without_authenticating_data(self):
        payload = self.source_payload([0, -1, None, 2, 0, 0])
        source = self.products.register_source(payload)
        self.assertEqual(source["stationCount"], 1)
        self.assertEqual(source["negativeValueCount"], 1)
        self.assertEqual(source["missingValueCount"], 1)
        self.assertFalse(source["productionEligible"])
        self.assertEqual(source["scientificAuthenticity"], "USER_DECLARED_NOT_INDEPENDENTLY_VERIFIED")
        snapshot = read_json(self.products.source(source["sourceId"])["path"])
        self.assertEqual(snapshot["stations"][0]["values"], payload["stations"][0]["values"])
        payload["stations"][0]["values"][0] = 999
        self.assertEqual(read_json(self.products.source(source["sourceId"])["path"])["stations"][0]["values"][0], 0)
        self.assertEqual(self.products.list_sources()["sources"][0]["sourceId"], source["sourceId"])
        self.assertNotIn("path", source)

    def test_invalid_source_rejected_before_creating_any_files(self):
        mutations = [lambda p: p.update(unit="mm/h"), lambda p: p.update(crs="EPSG:32650"),
                     lambda p: p.update(projectId="selinco"), lambda p: p.update(sourcePath="C:/secret"),
                     lambda p: p["times"].__setitem__(1, p["times"][0]),
                     lambda p: p["times"].__setitem__(1, "2017-12-31T23:00Z"),
                     lambda p: p["stations"][0]["values"].__setitem__(0, True),
                     lambda p: p["stations"][0]["values"].__setitem__(0, "1"),
                     lambda p: p["stations"][0]["values"].__setitem__(0, float("inf")),
                     lambda p: p["stations"][0].update(lon=True), lambda p: p.update(stations=[])]
        for mutation in mutations:
            payload = self.source_payload()
            mutation(payload)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.products.register_source(payload)
            self.assertFalse(self.products.runtime_root.exists())

    def test_normalization_binds_hashes_and_rejects_short_window_annual_policy(self):
        source = self.products.register_source(self.source_payload())
        request = self.processing_request("stationIdw", sourceId=source["sourceId"])
        normalized = self.products.normalize_processing_request(request)
        self.assertEqual(normalized["zeroOnlyPolicy"], "KEEP")
        self.assertEqual(normalized["sourceSHA256"], source["sourceSHA256"])
        self.assertEqual(normalized["parentBundleVersionId"], DEFAULT_BUNDLE_VERSION_ID)
        for change in ({"zeroOnlyPolicy": "ANNUAL_RECLASSIFY"}, {"idwPower": 0.9},
                       {"minimumStations": True}, {"minimumStations": 2}, {"sourceId": "../outside"},
                       {"parentBundleVersionId": ""}, {"outputDir": "C:/outside"}, {"start": "1959-01-01T00:00"}):
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                self.products.normalize_processing_request({**request, **change})

    def test_qc_window_cross_year_registration_and_event_statistics(self):
        import numpy as np
        import xarray as xr
        before = self.fixture.hashes()
        folder, result = self.execute(self.processing_request())
        self.assertFalse(result["product"]["registered"])
        self.assertFalse(result["productionEligible"])
        self.assertEqual(self.products.list_versions()["defaultBundleVersionId"], DEFAULT_BUNDLE_VERSION_ID)
        self.assertEqual(len(self.products.list_versions()["versions"]), 1)
        with self.assertRaises(KeyError):
            self.products.resolve(result["productVersionId"])
        with self.assertRaises(ValueError):
            self.products.register_task(folder.name)
        self.complete(folder)
        registered = self.products.register_task(folder.name)
        bundle = self.products.resolve(registered["bundleVersionId"])
        self.assertEqual(registered, self.products.register_task(folder.name))
        self.assertEqual(bundle["paths"]["flow"], self.fixture.root / "data_interface/observations/xufan_outlet_hourly_qc.csv")
        self.assertEqual(bundle["observationVersion"], "xufan_outlet_hourly_qc_20260819")
        self.assertNotEqual(bundle["forcingVersion"], "precipitation_qc_20260819")
        for year in (2017, 2018):
            with xr.open_dataset(bundle["precipitationDir"] / f"nananxi_precip_hourly_1km_{year}.nc", engine="h5netcdf") as ds:
                positions = [index for index, timestamp in enumerate(self.fixture.times) if timestamp.year == year]
                np.testing.assert_array_equal(ds.precipitation.values[:, 1, 0], self.fixture.precip[positions, 1, 0])
        event = read_json(bundle["paths"]["events"])["packages"][0]
        self.assertEqual(event["spatialRainInputs"]["modelContributingAreaKm2"], 3.5)
        self.assertNotEqual(event["spatialRainInputs"]["modelContributingAreaKm2"], 99)
        self.assertEqual(event["observedInputs"]["reportingRainStations"], None)
        self.assertIn("WARMUP_INPUT_INCOMPLETE", event["quality"]["exclusion_reasons"])
        self.assertEqual(before, self.fixture.hashes())

    def test_station_short_window_zero_is_real_zero_missing_negative_remain_missing(self):
        import numpy as np
        import xarray as xr
        source = self.products.register_source(self.source_payload([0, -1, None, 2, 0, 0]))
        source_hash = self.products.source(source["sourceId"])["sourceSHA256"]
        folder, result = self.execute(self.processing_request("stationIdw", sourceId=source["sourceId"]), "station")
        self.complete(folder)
        registered = self.products.register_task(folder.name)
        bundle = self.products.resolve(registered["bundleVersionId"])
        with xr.open_dataset(bundle["precipitationDir"] / "nananxi_precip_hourly_1km_2017.nc", engine="h5netcdf") as ds:
            self.assertEqual(ds.precipitation.values[0, 1, 0], 0)
            self.assertTrue(np.isnan(ds.precipitation.values[1, 1, 0]))
            self.assertEqual(ds.source_station_count.values.tolist(), [1, 0])
        with xr.open_dataset(bundle["precipitationDir"] / "nananxi_precip_hourly_1km_2018.nc", engine="h5netcdf") as ds:
            self.assertTrue(np.isnan(ds.precipitation.values[0, 1, 0]))
            np.testing.assert_array_equal(ds.precipitation.values[1:, 1, 0], [2, 0, 0])
        self.assertEqual(result["summary"]["negativeSourceValuesReclassifiedMissing"], 1)
        self.assertEqual(self.products.source(source["sourceId"])["sourceSHA256"], source_hash)

    def test_short_window_all_zero_station_is_not_reclassified(self):
        import xarray as xr
        source = self.products.register_source(self.source_payload())
        folder, result = self.execute(self.processing_request("stationIdw", sourceId=source["sourceId"]), "zero")
        self.complete(folder)
        registered = self.products.register_task(folder.name)
        bundle = self.products.resolve(registered["bundleVersionId"])
        for path in bundle["precipitationDir"].glob("*.nc"):
            with xr.open_dataset(path, engine="h5netcdf") as ds:
                self.assertTrue((ds.precipitation.values[:, 1, 0] == 0).all())
                self.assertTrue((ds.source_station_count.values == 1).all())
        self.assertEqual(result["summary"]["rainfallCompleteness"], 1)

    def test_two_station_idw_agrees_with_unchanged_original_kernel(self):
        import numpy as np
        import xarray as xr
        from pyproj import Transformer
        payload = self.source_payload([1, 2, 3, 4, 5, 6])
        payload["stations"].append({"id": "TEST-STATION-2", "lon": 119.03, "lat": 30.01,
                                    "values": [7, 8, 9, 10, 11, 12]})
        source = self.products.register_source(payload)
        folder, result = self.execute(self.processing_request("stationIdw", sourceId=source["sourceId"]), "two-stations")
        from runoff_platform.data.precipitation_idw import idw_interpolate_hourly
        self.complete(folder)
        registered = self.products.register_task(folder.name)
        bundle = self.products.resolve(registered["bundleVersionId"])
        projection = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
        sx, sy = projection.transform([row["lon"] for row in payload["stations"]], [row["lat"] for row in payload["stations"]])
        tx, ty = projection.transform([119, 119.01, 119], [29.99, 30, 30])
        expected, counts = idw_interpolate_hourly(np.array([row["values"] for row in payload["stations"]]).T,
                                                np.array(sx), np.array(sy), np.array(tx), np.array(ty))
        parts = []
        for path in sorted(bundle["precipitationDir"].glob("*.nc")):
            with xr.open_dataset(path, engine="h5netcdf") as ds:
                parts.append(np.stack([ds.precipitation.values[:, 1, 0], ds.precipitation.values[:, 0, 1],
                                       ds.precipitation.values[:, 0, 0]], axis=1))
                self.assertTrue((ds.source_station_count.values == 2).all())
        np.testing.assert_allclose(np.concatenate(parts), expected.astype(np.float32), rtol=0, atol=0)
        np.testing.assert_array_equal(counts, [2] * 6)
        self.assertEqual(result["summary"]["rainfallCompleteness"], 1)

    def test_registered_bundle_rejects_later_success_task_request_changes(self):
        folder, result = self.execute(self.processing_request(), "request-tampered")
        self.complete(folder)
        registered = self.products.register_task(folder.name)
        request = read_json(folder / "request.json")
        request["name"] = "Changed after explicit registration"
        write_json(folder / "request.json", request)
        with self.assertRaises(ValueError):
            self.products.resolve(registered["bundleVersionId"])

    def test_worker_rejects_source_changed_after_frozen_request(self):
        source = self.products.register_source(self.source_payload())
        normalized = self.products.normalize_processing_request(self.processing_request("stationIdw", sourceId=source["sourceId"]))
        folder = self.products.tasks_root / "DATA-source-corrupt"
        write_json(folder / "request.json", normalized)
        write_json(folder / "status.json", {"taskId": folder.name, "status": "running"})
        source_path = self.products.source(source["sourceId"])["path"]
        changed = read_json(source_path)
        changed["stations"][0]["values"][0] = 123
        write_json(source_path, changed)
        with self.assertRaises(ValueError):
            process_task(folder, self.products)
        self.assertFalse(self.products.products_root.exists())

    def test_cancelled_and_tampered_products_cannot_register(self):
        folder, result = self.execute(self.processing_request(), "tampered")
        write_json(folder / "status.json", {"taskId": folder.name, "status": "cancelled"})
        with self.assertRaises(ValueError):
            self.products.register_task(folder.name)
        self.complete(folder)
        nc = next((self.products.products_root / result["productVersionId"] / "precipitation").glob("*.nc"))
        with nc.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaises(ValueError):
            self.products.register_task(folder.name)
        self.assertFalse(self.products.registry_root.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
