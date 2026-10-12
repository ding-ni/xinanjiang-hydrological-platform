"""Opt-in real-source/IDW/model regression, always using an isolated runtime.

The builder only returns an in-memory conversion of the existing 2017 station
JSON. It neither certifies the observations nor writes a second source file.
Set XAJ_TEST_REAL_INPUTS=1 to process those observations with the original IDW;
also set XAJ_TEST_REAL_MODEL=1 to run actual model worker subprocesses.
"""
from __future__ import annotations

import copy
import csv
import json
import math
import os
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import MODEL, WORKSPACE, read_json, write_json
from input_products import DEFAULT_BUNDLE_VERSION_ID, InputProducts, file_sha256
from input_product_worker import process_task
from input_service import InputService


def existing_2017_paths(workspace_root=None):
    root = Path(WORKSPACE if workspace_root is None else workspace_root)
    project = root / "frontend-demo/app/public/data/projects/nananxi-xufan"
    return (project / "observations/rain/2017.json",
            project / "observations/catalog.json", project / "spatial/stations.json")


def build_existing_2017_source(workspace_root=None):
    """Preserve source records and missing values; use normalized map geometry.

    station.lng/lat are the geometry coordinates used by the delivered IDW,
    not the slightly rounded sourceProperties coordinates. Unrecorded hours
    remain None, including stations which did not report at all in 2017.
    """
    rain_path, catalog_path, spatial_path = existing_2017_paths(workspace_root)
    rain, catalog, spatial = (read_json(path) for path in
                              (rain_path, catalog_path, spatial_path))
    if (rain.get("schemaVersion") != "1.0" or rain.get("year") != 2017
            or rain.get("timeEncoding") != "hour-of-year-local"
            or rain.get("columns") != ["hour", "stationIndex", "rainMm"]
            or catalog.get("rain", {}).get("unit") != "mm"):
        raise ValueError("Existing 2017 rain schema, hourly convention or unit changed")
    codes = rain.get("stationCodes")
    if (not isinstance(codes, list) or not codes
            or any(not isinstance(code, str) or not code for code in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError("Existing 2017 station codes are empty or duplicate")
    mapping_rows = catalog["rain"]["stations"]
    mapping = {row["legacyCode"]: row["spatialCode"] for row in mapping_rows}
    spatial_rows = spatial["stations"]
    by_spatial_code = {row["code"]: row for row in spatial_rows}
    if len(mapping) != len(mapping_rows) or len(by_spatial_code) != len(spatial_rows):
        raise ValueError("Station metadata mapping is not unique")
    start, stop = datetime(2017, 1, 1), datetime(2018, 1, 1)
    hours = int((stop - start).total_seconds() // 3600)
    stations = []
    for code in codes:
        if code not in mapping or mapping[code] not in by_spatial_code:
            raise ValueError("Station has no registered geometry mapping: " + code)
        row = by_spatial_code[mapping[code]]
        lon, lat = row["lng"], row["lat"]
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in (lon, lat)):
            raise ValueError("Station normalized geometry is non-finite")
        stations.append({"id": code, "lon": lon, "lat": lat,
                         "values": [None] * hours})
    seen = set()
    rows = rain.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Existing station records are not an array")
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError("Existing hourly rain record has the wrong shape")
        hour, index, value = row
        if (isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour < hours
                or isinstance(index, bool) or not isinstance(index, int)
                or not 0 <= index < len(stations)):
            raise ValueError("Existing station index or hourly index is out of bounds")
        if (hour, index) in seen:
            raise ValueError("Duplicate existing station-hour record")
        seen.add((hour, index))
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value)):
            raise ValueError("Existing rain value is neither finite numeric nor null")
        stations[index]["values"][hour] = value
    return {
        "schemaVersion": "1.0", "projectId": "nananxi-xufan", "resolutionId": "1km",
        "name": "Existing 2017 station observations (diagnostic conversion)",
        "provenanceNote": "Read-only conversion of existing rain/2017.json; legacyCode-to-spatialCode "
                          "mapping from observations/catalog.json and normalized station.lng/lat "
                          "from spatial/stations.json. Missing records remain null; original zeros "
                          "are preserved. Source authenticity and timing not independently certified.",
        "unit": "mm", "crs": "EPSG:4326",
        "timeConvention": "SOURCE_LOCAL_NAIVE_HOURLY",
        "times": [(start + timedelta(hours=hour)).isoformat(timespec="minutes")
                  for hour in range(hours)],
        "stations": stations,
    }


class Existing2017BuilderTests(unittest.TestCase):
    """Read-only checks need no opt-in, model execution or runtime registration."""

    def test_conversion_preserves_every_existing_record_and_absence(self):
        paths = existing_2017_paths()
        before = {path: file_sha256(path) for path in paths}
        source = build_existing_2017_source()
        original = read_json(paths[0])
        self.assertEqual(len(source["times"]), 8760)
        self.assertEqual(len(source["stations"]), 12)
        self.assertEqual(source["times"][0], "2017-01-01T00:00")
        self.assertEqual(source["times"][-1], "2017-12-31T23:00")
        self.assertEqual([row["id"] for row in source["stations"]], original["stationCodes"])
        for hour, index, value in original["rows"]:
            self.assertEqual(source["stations"][index]["values"][hour], value)
        values = [value for row in source["stations"] for value in row["values"]]
        self.assertEqual(sum(value is None for value in values), 61320)
        self.assertEqual(sum(value == 0 for value in values), 42646)
        self.assertEqual(sum(value is not None and value > 0 for value in values), 1154)
        self.assertEqual(sum(value is not None and value < 0 for value in values), 0)
        catalog, spatial = read_json(paths[1]), read_json(paths[2])
        mapping = {row["legacyCode"]: row["spatialCode"] for row in catalog["rain"]["stations"]}
        geometry = {row["code"]: row for row in spatial["stations"]}
        for row in source["stations"]:
            registered = geometry[mapping[row["id"]]]
            self.assertEqual((row["lon"], row["lat"]), (registered["lng"], registered["lat"]))
        self.assertLess(len(json.dumps(source, ensure_ascii=False).encode("utf-8")), 2 * 1024 * 1024)
        self.assertEqual(before, {path: file_sha256(path) for path in paths})


@unittest.skipUnless(os.environ.get("XAJ_TEST_REAL_INPUTS") == "1",
                     "Opt-in real station-source/IDW input product regression")
class RealInputProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="xaj-real-input-products-")
        cls.runtime = Path(cls.temporary.name) / "runtime"
        cls.products = InputProducts(model_root=MODEL, runtime_root=cls.runtime)
        cls.baseline = cls.products.resolve()
        cls.source_files_before = {path: file_sha256(path) for path in existing_2017_paths()}
        cls.vendor_files_before = {MODEL / record["path"]: record["sha256"]
                                   for record in cls.baseline["files"]}
        cls.source = cls.products.register_source(build_existing_2017_source())
        cls.folder, cls.processed, cls.registered = cls.execute_product({
            "requestId": "DATA-REAL-IDW-2017", "operation": "stationIdw",
            "projectId": "nananxi-xufan", "resolutionId": "1km",
            "name": "Isolated real 2017 IDW regression", "sourceId": cls.source["sourceId"],
            "start": "2017-01-01T00:00", "end": "2017-12-31T23:00",
            "parentBundleVersionId": DEFAULT_BUNDLE_VERSION_ID,
            "idwPower": 2, "minimumStations": 1, "zeroOnlyPolicy": "ANNUAL_RECLASSIFY",
        })
        cls.bundle = cls.products.resolve(cls.registered["bundleVersionId"])

    @classmethod
    def execute_product(cls, payload):
        task_id = "DATA-real-" + uuid.uuid4().hex
        folder = cls.products.tasks_root / task_id
        write_json(folder / "request.json", cls.products.normalize_processing_request(payload))
        write_json(folder / "status.json", {"taskId": task_id, "status": "running"})
        result = process_task(folder, products=cls.products)
        # Deliberately separate successful processing from explicit registration.
        write_json(folder / "status.json", {"taskId": task_id, "status": "completed"})
        registered = cls.products.register_task(task_id)
        return folder, result, registered

    @classmethod
    def tearDownClass(cls):
        try:
            for path, digest in {**cls.source_files_before, **cls.vendor_files_before}.items():
                if file_sha256(path) != digest:
                    raise AssertionError("Real regression modified a read-only source: " + str(path))
        finally:
            cls.temporary.cleanup()

    def test_real_annual_float32_idw_and_station_counts_equal_fixed_qc_exactly(self):
        import numpy as np
        import xarray as xr
        with self.baseline["paths"]["grids"].open(encoding="utf-8-sig", newline="") as stream:
            grids = list(csv.DictReader(stream))
        rows = np.array([int(row["array_row"]) for row in grids])
        columns = np.array([int(row["array_col"]) for row in grids])
        self.assertEqual(len(grids), 110)
        filename = "nananxi_precip_hourly_1km_2017.nc"
        with xr.open_dataset(self.baseline["precipitationDir"] / filename, engine="h5netcdf") as old, \
                xr.open_dataset(self.bundle["precipitationDir"] / filename, engine="h5netcdf") as new:
            before, after = old.precipitation.values[:, rows, columns], new.precipitation.values[:, rows, columns]
            self.assertEqual(after.dtype, np.dtype("float32"))
            self.assertEqual(after.shape, (8760, 110))
            np.testing.assert_array_equal(new.time.values, old.time.values)
            np.testing.assert_array_equal(new.lat.values, old.lat.values)
            np.testing.assert_array_equal(new.lon.values, old.lon.values)
            np.testing.assert_array_equal(np.isnan(after), np.isnan(before))
            np.testing.assert_array_equal(after, before)
            np.testing.assert_array_equal(new.source_station_count.values, old.reporting_station_count.values)
            np.testing.assert_array_equal(new.source_station_count.values, np.full(8760, 5))
            self.assertEqual(float(np.max(np.abs(after - before))), 0.0)
        self.assertEqual(self.source["missingValueCount"], 61320)
        self.assertEqual(self.source["negativeValueCount"], 0)
        self.assertEqual(self.processed["summary"]["gridCount"], 110)
        self.assertEqual(self.processed["summary"]["contributingGridCount"], 96)
        self.assertAlmostEqual(self.processed["summary"]["controlAreaKm2"], 63.27190569363709)
        self.assertFalse(self.source["productionEligible"])
        self.assertFalse(self.registered["productionEligible"])
        self.assertFalse(self.processed["productionEligible"])
        self.assertEqual(self.products.resolve()["bundleVersionId"], DEFAULT_BUNDLE_VERSION_ID)
        self.assertTrue(self.bundle["manifestPath"].is_relative_to(self.runtime))
        print("REAL-IDW: 963600 float32 grid-hour values, maxAbs=0 mm; stationCount differences=0", flush=True)

    def test_registered_real_product_is_consumed_by_input_service_window(self):
        old = InputService(bundle=self.baseline).window("2017-06-18T12:00", "2017-06-21T11:00")
        new = InputService(bundle=self.bundle).window("2017-06-18T12:00", "2017-06-21T11:00")
        self.assertEqual(new["bundleVersionId"], self.registered["bundleVersionId"])
        self.assertEqual(new["bundleManifestSHA256"], self.registered["manifestSHA256"])
        self.assertEqual(new["product"]["times"], old["product"]["times"])
        self.assertEqual(new["product"]["cells"], old["product"]["cells"])
        self.assertEqual(new["summary"], old["summary"])
        self.assertEqual(new["observationPreview"]["river"], old["observationPreview"]["river"])
        self.assertNotEqual(new["forcingVersion"], old["forcingVersion"])
        self.assertEqual(new["observationVersion"], old["observationVersion"])
        self.assertEqual(new["summary"]["windowHours"], 72)

    @unittest.skipUnless(os.environ.get("XAJ_TEST_REAL_MODEL") == "1",
                         "Opt-in actual worker subprocess and state/version regression")
    def test_model_subprocess_uses_new_qc_window_without_changing_numerics_or_handoff(self):
        import numpy as np
        from server import TaskManager
        from test_unified_backend import simulation

        _, _, version = self.execute_product({
            "requestId": "DATA-REAL-QC-WINDOW", "operation": "qcWindow",
            "projectId": "nananxi-xufan", "resolutionId": "1km",
            "name": "Isolated model comparison QC window",
            "start": "2017-06-18T11:00", "end": "2017-06-21T11:00",
            "parentBundleVersionId": DEFAULT_BUNDLE_VERSION_ID,
        })
        manager = TaskManager(self.runtime / "tasks", products=self.products)

        def request_for(bundle_id, start="2017-06-19T11:00", end="2017-06-20T11:00"):
            request = simulation(start=start, end=end)
            request["requestId"] = "REAL-INPUT-MODEL-" + uuid.uuid4().hex
            request["inputs"]["bundleVersionId"] = bundle_id
            return request

        def run(request):
            task = manager.submit(request)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status = manager.get(task["taskId"])
                if status["status"] not in {"queued", "running", "cancelling"}:
                    break
                time.sleep(0.05)
            folder = manager.root / task["taskId"]
            log = (folder / "task.log").read_text(encoding="utf-8", errors="replace") if (folder / "task.log").is_file() else ""
            self.assertEqual(status["status"], "completed", log[-6000:])
            result = manager.result(task["taskId"])
            self.assertFalse(result["productionEligible"])
            self.assertEqual(result["lineage"]["inputBundleVersionId"], request["inputs"]["bundleVersionId"])
            return folder, result, read_json(folder / "checkpoint.json")

        try:
            old_folder, old_result, old_state = run(request_for(DEFAULT_BUNDLE_VERSION_ID))
            new_folder, new_result, new_state = run(request_for(version["bundleVersionId"]))
            self.assertEqual({key: value for key, value in new_result["hydrograph"].items() if key != "forcing"},
                             {key: value for key, value in old_result["hydrograph"].items() if key != "forcing"})
            for field in ("precipitation", "temperature", "components"):
                self.assertEqual(new_result["hydrograph"]["forcing"][field], old_result["hydrograph"]["forcing"][field])
            self.assertEqual(new_result["hydrograph"]["forcing"]["bundleVersionId"], version["bundleVersionId"])
            self.assertEqual(len(new_result["hydrograph"]["forecast"]), 25)
            self.assertNotEqual(new_result["lineage"]["forcingVersion"], old_result["lineage"]["forcingVersion"])
            for section in ("land", "routing"):
                self.assertEqual(new_state[section].keys(), old_state[section].keys())
                for field in new_state[section]:
                    np.testing.assert_array_equal(new_state[section][field], old_state[section][field])
            with np.load(old_folder / "grid-history.npz", allow_pickle=False) as old, \
                    np.load(new_folder / "grid-history.npz", allow_pickle=False) as new:
                self.assertEqual(set(new.files), set(old.files))
                for field in new.files:
                    np.testing.assert_array_equal(new[field], old[field])
            continuation = request_for(version["bundleVersionId"], "2017-06-20T12:00", "2017-06-21T11:00")
            continuation["runType"] = "stateContinuation"
            continuation["period"]["initialStateVersionId"] = new_result["stateVersionId"]
            _, continued, continued_state = run(continuation)
            self.assertEqual(continued_state["parentStateVersionId"], new_result["stateVersionId"])
            self.assertEqual(continued_state["inputBundleManifestSHA256"], version["manifestSHA256"])
            _, whole, whole_state = run(request_for(version["bundleVersionId"], end="2017-06-21T11:00"))
            np.testing.assert_allclose(
                [row["value"] for row in whole["hydrograph"]["forecast"]],
                [row["value"] for row in new_result["hydrograph"]["forecast"] + continued["hydrograph"]["forecast"]],
                rtol=1e-12, atol=1e-12)
            for section in ("land", "routing"):
                for field in continued_state[section]:
                    np.testing.assert_allclose(continued_state[section][field], whole_state[section][field], rtol=1e-12, atol=1e-12)
            cross_version = copy.deepcopy(continuation)
            cross_version["requestId"] = "REAL-CROSS-VERSION-" + uuid.uuid4().hex
            cross_version["inputs"]["bundleVersionId"] = DEFAULT_BUNDLE_VERSION_ID
            jobs_before = {folder.name for folder in manager.root.iterdir()}
            with self.assertRaisesRegex(ValueError, "输入版本不一致"):
                manager.submit(cross_version)
            self.assertEqual({folder.name for folder in manager.root.iterdir()}, jobs_before)
            print("REAL-MODEL: baseline/new QC 25h hydrograph, all grid arrays and final land/routing identical; "
                  "same-version continuation passed; cross-version state rejected before enqueue", flush=True)
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
