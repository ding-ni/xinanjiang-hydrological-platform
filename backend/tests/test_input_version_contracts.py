"""Input versions are frozen identities, not mutable current/latest aliases.

Every artifact in this module is synthetic and lives in TemporaryDirectory;
queues are mocked and no model worker or everyday runtime is used.
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DEFAULT_INPUT_BUNDLE, ROUTING_VERSION, read_json, write_json
from contracts import freeze_input_selection, freeze_routing_selection
from input_products import InputProducts, file_sha256, manifest_digest
from server import TaskManager
from state_integrity import assert_state_input_compatibility
from science_context import freeze_science_selection, IDENTITY_KEYS
from test_input_service import InputFixture


CONTEXT = {"projectId": "nananxi-xufan", "resolutionId": "1km",
           "modelConfigId": "nananxi-xufan-1km",
           "contextKey": "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km"}
PARAMETERS = {"KC": 0.8, "WM": 140, "WUM": 25, "WLM": 80, "B": 0.3, "C": 0.1,
              "SM": 30, "EX": 1.5, "KI": 0.01, "KG": 0.005, "IM": 0.02}


def model_request(request_id="MODEL-test", bundle_version=None):
    request = {"schemaVersion": "2.0", "requestId": request_id, "runType": "simulation",
               "context": copy.deepcopy(CONTEXT), "parameters": dict(PARAMETERS),
               "period": {"startTime": "2017-12-31T22:00", "endTime": "2018-01-01T03:00", "warmupDays": 1},
               "inputs": {"evaporation": {"mode": "monthly-climatology-assumption"}}, "lineage": {}}
    if bundle_version is not None:
        request["inputs"]["bundleVersionId"] = bundle_version
    return request


class InputVersionFixture:
    """Manual successful products allow deterministic integrity fault injection."""
    def __init__(self, root):
        self.root = Path(root)
        self.model = InputFixture(self.root / "model")
        self.runtime = self.root / "runtime"
        self.products = InputProducts(model_root=self.model.root, runtime_root=self.runtime)

    @staticmethod
    def source_payload():
        return {"schemaVersion": "1.0", "projectId": "nananxi-xufan", "resolutionId": "1km",
                "name": "Synthetic station fixture", "provenanceNote": "Not authentic measured data",
                "unit": "mm", "crs": "EPSG:4326", "timeConvention": "SOURCE_LOCAL_NAIVE_HOURLY",
                "times": ["2017-12-31T22:00", "2017-12-31T23:00", "2018-01-01T00:00"],
                "stations": [{"id": "TEST-A", "lon": 119.0, "lat": 30.0, "values": [None, -1, 0]},
                             {"id": "TEST-B", "lon": 119.01, "lat": 29.99, "values": [1, 2, 3]}]}

    @staticmethod
    def processing_request(request_id="DATA-client-test"):
        return {"requestId": request_id, "operation": "qcWindow", "projectId": "nananxi-xufan",
                "resolutionId": "1km", "name": "Synthetic QC version", "start": "2017-12-31T22:00",
                "end": "2018-01-01T03:00", "parentBundleVersionId": DEFAULT_INPUT_BUNDLE}

    def finished_product(self, suffix="one", register=False, request=None, events=None, factor=1):
        products = self.products
        request = products.normalize_processing_request(request or self.processing_request("DATA-client-" + suffix))
        task_id, version_id = "DATA-" + suffix, "INPUT-" + suffix
        manifest = copy.deepcopy(products.resolve()["manifest"])
        folder = products.products_root / version_id
        folder.mkdir(parents=True)
        # Copy only synthetic fixtures: no delivered or vendored input is touched.
        for role in ("events", "quality"):
            target = folder / (role + ".json")
            shutil.copyfile(products.resolve()["paths"][role], target)
            if role == "events" and events is not None:
                write_json(target, {"packages": events})
            manifest["paths"][role] = products.reference(target)
        precipitation = folder / "precipitation"
        precipitation.mkdir()
        for path in self.model.precipitation_dir.glob("*.nc"):
            if factor == 1:
                shutil.copyfile(path, precipitation / path.name)
            else:
                with self.model.xr.open_dataset(path, engine="h5netcdf") as original:
                    dataset = original.load()
                dataset["precipitation"] = dataset["precipitation"] * factor
                dataset["precipitation"].attrs["units"] = "mm"
                dataset.to_netcdf(precipitation / path.name, engine="h5netcdf")
        manifest.update(bundleVersionId=version_id, sourceTaskId=task_id, operation=request["operation"],
                        requestSHA256=manifest_digest(request), name="Synthetic product " + suffix,
                        parentBundleVersionId=request["parentBundleVersionId"],
                        forcingVersion="test-forcing-" + suffix, sourceLabel="Synthetic / not production data",
                        precipitationDirectory=products.reference(precipitation),
                        coverage={"start": request["start"], "end": request["end"], "hours": request["hours"], "years": [2017, 2018]})
        if request.get("sourceId"):
            manifest["sourceId"] = request["sourceId"]
        manifest["files"] = [products.file_record(products._path(reference)) for reference in manifest["paths"].values()]
        manifest["files"].extend(products.file_record(path) for path in sorted(precipitation.glob("*.nc")))
        path = folder / "manifest.json"
        write_json(path, manifest)
        digest = file_sha256(path)
        task = products.tasks_root / task_id
        status = {"taskId": task_id, "status": "completed", "createdAt": "2026-10-10T00:00:00Z",
                  "runType": "inputProcessing", "requestId": request["requestId"], "runConfig": request,
                  "productionEligible": False}
        result = {"taskId": task_id, "resultVersionId": "RESULT-" + task_id, "productVersionId": version_id,
                  "manifestSHA256": digest, "productionEligible": False, "lineage": {}}
        write_json(task / "request.json", request)
        write_json(task / "status.json", status)
        write_json(task / "result.json", result)
        if register:
            products.register_task(task_id)
        return {"taskId": task_id, "versionId": version_id, "task": task, "folder": folder,
                "manifestPath": path, "manifest": manifest, "result": result, "request": request, "status": status}

    @staticmethod
    def catalog(bundle=None):
        return {"cases": {"nananxi-xufan:1km": {"ready": True,
                "operations": ["continuousSimulation", "stateInitialization", "stateContinuation"],
                "unsupported": {}, "parameters": dict(PARAMETERS)}}}


class InputVersionContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = InputVersionFixture(self.temporary.name)
        self.products = self.fixture.products
        self.original_hashes = self.fixture.model.hashes()

    def tearDown(self):
        self.assertEqual(self.fixture.model.hashes(), self.original_hashes)
        self.temporary.cleanup()

    def test_freeze_is_nonmutating_default_and_authoritative(self):
        original = model_request()
        before = copy.deepcopy(original)
        frozen, bundle = freeze_input_selection(original, self.products)
        self.assertEqual(original, before)
        self.assertEqual(frozen["inputs"]["bundleVersionId"], DEFAULT_INPUT_BUNDLE)
        self.assertEqual(frozen["inputs"]["bundleManifestSHA256"], bundle["manifestSHA256"])
        for key in ("forcingVersion", "observationVersion"):
            self.assertEqual(frozen["lineage"][key], bundle[key])
        self.assertEqual(frozen["lineage"]["inputBundleVersionId"], DEFAULT_INPUT_BUNDLE)
        self.assertEqual(frozen["lineage"]["inputBundleManifestSHA256"], bundle["manifestSHA256"])
        self.assertNotIn("inputManifestSHA256", frozen["lineage"])

    def test_freeze_rejects_digest_lineage_conflicts_and_paths(self):
        mutations = [("inputs", "bundleManifestSHA256", "b" * 64),
                     ("lineage", "inputBundleVersionId", "INPUT-other"),
                     ("lineage", "inputBundleManifestSHA256", "b" * 64),
                     ("lineage", "forcingVersion", "other"), ("lineage", "observationVersion", "other")]
        mutations.extend(("inputs", field, "F:/outside") for field in
                         ("path", "root", "files", "precipitationDir", "modelRoot", "runtimeRoot", "url"))
        for section, key, value in mutations:
            with self.subTest(section=section, key=key):
                request = model_request()
                request[section][key] = value
                with self.assertRaises(ValueError):
                    freeze_input_selection(request, self.products)

    def test_version_and_source_identifiers_never_become_paths(self):
        for version in ("current", "latest", "../outside", "INPUT-../outside", "INPUT-..\\outside",
                        "F:/outside", "\\\\server\\share", "INPUT-a%2Fb", "INPUT-a%5Cb", "", [], {}):
            with self.subTest(version=version), self.assertRaises((ValueError, TypeError)):
                self.products.resolve(version)
        for source in ("../outside", "SRC-../outside", "SRC-..\\outside", "F:/source", "SRC-a%2Fb", None):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.products.source(source)
        with self.assertRaises(KeyError):
            self.products.resolve("INPUT-not-registered")

    def test_source_bad_schema_case_units_coordinates_values_and_fields_have_no_artifacts(self):
        mutations = [("schemaVersion", "2.0"), ("projectId", "selinco"), ("resolutionId", "0p1deg"),
                     ("unit", "mm/h"), ("crs", "EPSG:3857"), ("timeConvention", "UTC"),
                     ("name", ""), ("provenanceNote", ""), ("path", "F:/source.nc")]
        for key, value in mutations:
            with self.subTest(field=key):
                payload = self.fixture.source_payload()
                payload[key] = value
                with self.assertRaises(ValueError):
                    self.products.register_source(payload)
        for field, values in (("lon", [True, "119", None, float("nan"), float("inf"), 181]),
                              ("lat", [False, "30", None, float("nan"), -91]),
                              ("values", [[True, 1, 2], ["1", 1, 2], [float("nan"), 1, 2],
                                          [float("inf"), 1, 2], [{}, 1, 2], [1, 2], None])):
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = self.fixture.source_payload()
                    payload["stations"][0][field] = value
                    with self.assertRaises(ValueError):
                        self.products.register_source(payload)
        self.assertFalse(self.products.sources_root.exists())

    def test_source_times_and_station_count_are_typed_bounded_unique_and_continuous(self):
        times = [[], ["1959-12-31T23:00"], ["2025-01-01T00:00"], ["2017-01-01T00:00Z"],
                 ["2017-01-01T00:30"], ["2017-01-01T00:00", "2017-01-01T02:00"],
                 ["2017-01-01T00:00", "2017-01-01T00:00"], ["2017-01-01T01:00", "2017-01-01T00:00"],
                 ["2017-02-30T00:00"], [True], ["2017-01-01T00:00"] * 8785]
        for axis in times:
            with self.subTest(times=axis[:2]):
                payload = self.fixture.source_payload()
                payload["times"] = axis
                with self.assertRaises(ValueError):
                    self.products.register_source(payload)
        for count in (0, 33):
            payload = self.fixture.source_payload()
            payload["stations"] = [{**payload["stations"][0], "id": f"TEST-{index}"} for index in range(count)]
            with self.assertRaises(ValueError):
                self.products.register_source(payload)
        payload = self.fixture.source_payload()
        payload["stations"][1]["id"] = payload["stations"][0]["id"]
        with self.assertRaises(ValueError):
            self.products.register_source(payload)
        self.assertFalse(self.products.sources_root.exists())

    def test_idw_controls_are_typed_bounded_and_annual_policy_requires_complete_year(self):
        source = self.products.register_source(self.fixture.source_payload())
        request = self.fixture.processing_request()
        request.update(operation="stationIdw", sourceId=source["sourceId"], end="2018-01-01T00:00")
        normalized = self.products.normalize_processing_request(request)
        self.assertEqual(normalized["zeroOnlyPolicy"], "KEEP")
        self.assertEqual(normalized["sourceSHA256"], source["sourceSHA256"])
        for key, values in (("idwPower", [True, "2", None, 0, 5, float("nan"), float("inf")]),
                            ("minimumStations", [True, "1", 1.5, 0, 3]),
                            ("zeroOnlyPolicy", ["ANNUAL_RECLASSIFY", "ZERO_FILL", True]),
                            ("start", ["2017-12-31T21:00"]), ("end", ["2018-01-01T01:00"]),
                            ("path", ["F:/source.nc"])):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.products.normalize_processing_request({**request, key: value})
        annual = self.fixture.source_payload()
        start = datetime(2016, 1, 1)
        annual["times"] = [(start + timedelta(hours=index)).isoformat(timespec="minutes") for index in range(8784)]
        annual["stations"] = [{"id": "ZERO-YEAR", "lon": 119, "lat": 30, "values": [0] * 8784}]
        source = self.products.register_source(annual)
        normalized = self.products.normalize_processing_request({**request, "sourceId": source["sourceId"],
                     "start": "2016-01-01T00:00", "end": "2016-12-31T23:00", "zeroOnlyPolicy": "ANNUAL_RECLASSIFY"})
        self.assertEqual(normalized["hours"], 8784)
        self.assertEqual(normalized["zeroOnlyPolicy"], "ANNUAL_RECLASSIFY")

    def test_source_maximum_station_value_and_serialized_size_limits(self):
        payload = self.fixture.source_payload()
        start = datetime(2017, 1, 1)
        payload["times"] = [(start + timedelta(hours=index)).isoformat(timespec="minutes") for index in range(6250)]
        payload["stations"] = [{"id": f"MAX-{index}", "lon": 119, "lat": 30, "values": [0] * 6250} for index in range(32)]
        source = self.products.register_source(payload)
        self.assertEqual(source["stationCount"], 32)
        self.assertEqual(source["valueCount"], 200000)
        self.assertFalse(source["productionEligible"])
        payload["times"].append((start + timedelta(hours=6250)).isoformat(timespec="minutes"))
        for station in payload["stations"]:
            station["values"].append(0)
        with self.assertRaises(ValueError):
            self.products.register_source(payload)
        payload["times"].pop()
        for station in payload["stations"]:
            station["values"] = [1.2345678901234567e100] * 6250
        with self.assertRaisesRegex(ValueError, "2MB"):
            self.products.register_source(payload)
        self.assertEqual(len(self.products.list_sources()["sources"]), 1)

    def test_product_is_invisible_until_explicit_registration_and_registration_is_idempotent(self):
        product = self.fixture.finished_product()
        self.assertEqual(len(self.products.list_versions()["versions"]), 1)
        with self.assertRaises(KeyError):
            self.products.resolve(product["versionId"])
        version = self.products.register_task(product["taskId"])
        self.assertFalse(version["productionEligible"])
        self.assertTrue(version["registered"])
        self.assertFalse(version["default"])
        self.assertEqual(version, self.products.register_task(product["taskId"]))
        versions = self.products.list_versions()
        self.assertEqual(versions["defaultBundleVersionId"], DEFAULT_INPUT_BUNDLE)
        self.assertEqual(len(versions["versions"]), 2)
        self.assertEqual(self.products.resolve()["bundleVersionId"], DEFAULT_INPUT_BUNDLE)
        public = json.dumps(version, ensure_ascii=False)
        self.assertNotIn(str(self.fixture.root), public)
        for field in ("paths", "files", "precipitationDirectory", "manifestPath"):
            self.assertNotIn(field, version)

    def test_unsuccessful_product_residue_is_not_registrable_or_listed(self):
        product = self.fixture.finished_product()
        for status in ("queued", "running", "cancelling", "cancelled", "failed", "unknown"):
            with self.subTest(status=status):
                write_json(product["task"] / "status.json", {**product["status"], "status": status})
                with self.assertRaises(ValueError):
                    self.products.register_task(product["taskId"])
                self.assertEqual(len(self.products.list_versions()["versions"]), 1)
        self.assertFalse(self.products.registry_root.exists())

    def test_registration_requires_agreement_of_task_request_manifest_result_and_files(self):
        mutations = [("status", "taskId", "DATA-other"), ("result", "taskId", "DATA-other"),
                     ("result", "manifestSHA256", "b" * 64), ("result", "productionEligible", True),
                     ("request", "name", "Changed after completion")]
        for index, (role, key, value) in enumerate(mutations):
            with self.subTest(role=role, key=key):
                product = self.fixture.finished_product(str(index))
                artifact = {**product[role], key: value}
                write_json(product["task"] / (role + ".json"), artifact)
                with self.assertRaises(ValueError):
                    self.products.register_task(product["taskId"])
        product = self.fixture.finished_product("bad-file")
        write_json(product["folder"] / "quality.json", {"tampered": True})
        with self.assertRaises(ValueError):
            self.products.register_task(product["taskId"])

    def test_product_paths_roles_and_precipitation_inventory_cannot_escape_or_be_incomplete(self):
        mutations = [lambda m: m["paths"].pop("quality"),
                     lambda m: m["paths"].update(quality={"root": "runtime", "path": "../outside.json"}),
                     lambda m: m["paths"].update(quality={"root": "runtime", "path": "F:/outside.json"}),
                     lambda m: m["paths"].update(quality={"root": "runtime", "path": "\\\\server\\outside"}),
                     lambda m: m["files"].pop(), lambda m: m["files"].append(copy.deepcopy(m["files"][0]))]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                product = self.fixture.finished_product("bad-path-" + str(index))
                mutate(product["manifest"])
                write_json(product["manifestPath"], product["manifest"])
                result = {**product["result"], "manifestSHA256": file_sha256(product["manifestPath"])}
                write_json(product["task"] / "result.json", result)
                with self.assertRaises(ValueError):
                    self.products.register_task(product["taskId"])

    def test_registered_product_still_checks_frozen_source_task_and_product_integrity(self):
        for index, role in enumerate(("request", "status", "result", "manifest", "file", "registry")):
            with self.subTest(role=role):
                product = self.fixture.finished_product("registered-" + str(index), register=True)
                if role == "request":
                    write_json(product["task"] / "request.json", {**product["request"], "name": "Changed after registration"})
                elif role == "status":
                    write_json(product["task"] / "status.json", {**product["status"], "status": "cancelled"})
                elif role == "result":
                    write_json(product["task"] / "result.json", {**product["result"], "taskId": "DATA-other"})
                elif role == "manifest":
                    write_json(product["manifestPath"], {**product["manifest"], "name": "Different"})
                elif role == "file":
                    write_json(product["folder"] / "quality.json", {"tampered": True})
                else:
                    record = self.products.registry_root / (product["versionId"] + ".json")
                    write_json(record, {**read_json(record), "sourceTaskId": "DATA-other"})
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.products.resolve(product["versionId"])

    def test_source_hash_cannot_drift_after_processing_was_frozen(self):
        source = self.products.register_source(self.fixture.source_payload())
        request = {**self.fixture.processing_request(), "operation": "stationIdw",
                   "sourceId": source["sourceId"], "end": "2018-01-01T00:00"}
        product = self.fixture.finished_product("source-drift", request=request)
        descriptor = self.products.source(source["sourceId"])
        payload = read_json(descriptor["path"])
        payload["stations"][1]["values"][0] = 99
        write_json(descriptor["path"], payload)
        # Updating the source's own manifest cannot change a task's frozen SHA.
        manifest = descriptor["manifest"]
        manifest["file"] = self.products.file_record(descriptor["path"])
        write_json(descriptor["manifestPath"], manifest)
        with self.assertRaises(ValueError):
            self.products.register_task(product["taskId"])

    def test_same_request_is_one_model_job_but_other_version_is_not_same_job(self):
        product = self.fixture.finished_product(register=True)
        with patch("server.ThreadPoolExecutor"), patch("server.case_catalog", self.fixture.catalog):
            manager = TaskManager(self.fixture.runtime / "tasks", products=self.products)
        try:
            request = model_request(bundle_version=product["versionId"])
            task = manager.submit(request)
            self.assertEqual(task["taskId"], manager.submit(request)["taskId"])
            self.assertEqual(manager.executor.submit.call_count, 1)
            self.assertEqual(read_json(manager.root / task["taskId"] / "request.json")["inputs"]["bundleVersionId"], product["versionId"])
            with self.assertRaises(ValueError):
                manager.submit(model_request(bundle_version=DEFAULT_INPUT_BUNDLE))
            clone = manager.clone(task["taskId"])
            self.assertNotEqual(clone["taskId"], task["taskId"])
            self.assertEqual(clone["runConfig"]["inputs"], task["runConfig"]["inputs"])
            self.assertEqual(clone["lineage"]["sourceTaskId"], task["taskId"])
        finally:
            manager.close()

    def test_concurrent_registration_and_model_submission_are_idempotent(self):
        product = self.fixture.finished_product()
        with ThreadPoolExecutor(max_workers=4) as executor:
            versions = list(executor.map(lambda _: self.products.register_task(product["taskId"]), range(8)))
        self.assertTrue(all(version == versions[0] for version in versions))
        self.assertEqual(len(list(self.products.registry_root.glob("INPUT-*.json"))), 1)
        with patch("server.ThreadPoolExecutor"), patch("server.case_catalog", self.fixture.catalog):
            manager = TaskManager(self.fixture.runtime / "tasks", products=self.products)
        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                tasks = list(executor.map(lambda _: manager.submit(model_request(bundle_version=product["versionId"])), range(8)))
            self.assertEqual(len({task["taskId"] for task in tasks}), 1)
            self.assertEqual(manager.executor.submit.call_count, 1)
        finally:
            manager.close()

    def test_continuation_checks_bundle_digest_forcing_observation_pet_and_routing(self):
        product = self.fixture.finished_product(register=True)
        request, descriptor = freeze_input_selection(model_request(bundle_version=product["versionId"]), self.products)
        request = freeze_routing_selection(request, descriptor)
        request, _ = freeze_science_selection(request, self.fixture.runtime)
        checkpoint = {"inputBundleVersionId": product["versionId"], "inputBundleManifestSHA256": descriptor["manifestSHA256"],
                      "forcingVersion": descriptor["forcingVersion"], "observationVersion": descriptor["observationVersion"],
                      "petMode": "MONTHLY_CLIMATOLOGY_ASSUMPTION", "routingVersionId": ROUTING_VERSION,
                      "routingManifestSHA256": request["lineage"]["routingManifestSHA256"],
                      "inputManifestSHA256": "c" * 64, **{key: request['lineage'][key] for key in IDENTITY_KEYS}}
        # Different task-level hashes are allowed: child and parent use distinct windows.
        request["lineage"]["inputManifestSHA256"] = "d" * 64
        assert_state_input_compatibility(checkpoint, request)
        for key, value in (("inputBundleVersionId", DEFAULT_INPUT_BUNDLE), ("inputBundleManifestSHA256", "b" * 64),
                           ("inputBundleManifestSHA256", None), ("forcingVersion", "other"),
                           ("observationVersion", "other"), ("petMode", "REAL_PET"), ("routingVersionId", "other"),
                           ("routingManifestSHA256", "e" * 64), ("routingManifestSHA256", None)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                assert_state_input_compatibility({**checkpoint, key: value}, request)
        legacy_request, _ = freeze_input_selection(model_request(), self.products)
        with self.assertRaisesRegex(ValueError, '历史状态.*重新初始化'):
            assert_state_input_compatibility({}, legacy_request)

    def test_missing_routing_digest_is_legacy_only_without_any_new_bundle_identity(self):
        request, descriptor = freeze_input_selection(model_request(), self.products)
        request = freeze_routing_selection(request, descriptor)
        request, _ = freeze_science_selection(request, self.fixture.runtime)
        legacy = {"forcingVersion": descriptor["forcingVersion"], "observationVersion": descriptor["observationVersion"],
                  "petMode": "MONTHLY_CLIMATOLOGY_ASSUMPTION", "routingVersionId": ROUTING_VERSION,
                  **{key: request['lineage'][key] for key in IDENTITY_KEYS}}
        assert_state_input_compatibility(legacy, request)
        identities = [{"inputBundleVersionId": DEFAULT_INPUT_BUNDLE},
                      {"inputBundleManifestSHA256": descriptor["manifestSHA256"]},
                      {"inputBundleManifestSHA256": None},
                      {"inputBundleVersionId": DEFAULT_INPUT_BUNDLE, "inputBundleManifestSHA256": descriptor["manifestSHA256"]}]
        for identity in identities:
            for routing in ({}, {"routingManifestSHA256": None}):
                with self.subTest(identity=identity, routing=routing), self.assertRaisesRegex(ValueError, "缺少汇流文件摘要"):
                    assert_state_input_compatibility({**legacy, **identity, **routing}, request)
            assert_state_input_compatibility({**legacy, **identity,
                                             "routingManifestSHA256": request["lineage"]["routingManifestSHA256"]}, request)
        product = self.fixture.finished_product(register=True)
        selected, descriptor = freeze_input_selection(model_request(bundle_version=product["versionId"]), self.products)
        selected = freeze_routing_selection(selected, descriptor)
        selected, _ = freeze_science_selection(selected, self.fixture.runtime)
        checkpoint = {**legacy, "inputBundleVersionId": product["versionId"],
                      "inputBundleManifestSHA256": descriptor["manifestSHA256"],
                      "forcingVersion": descriptor["forcingVersion"], "observationVersion": descriptor["observationVersion"]}
        for routing in ({}, {"routingManifestSHA256": None}):
            with self.subTest(product=product["versionId"], routing=routing), self.assertRaisesRegex(ValueError, "缺少汇流文件摘要"):
                assert_state_input_compatibility({**checkpoint, **routing}, selected)
        assert_state_input_compatibility({**checkpoint, "routingManifestSHA256": selected["lineage"]["routingManifestSHA256"]}, selected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
