"""End-to-end local HTTP boundary, synthetic inputs and mocked worker queues."""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DEFAULT_INPUT_BUNDLE, read_json, write_json
from server import InputTaskManager, TaskManager, ThreadingHTTPServer, handler
from test_input_version_contracts import InputVersionFixture, model_request


class InputProductsHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = InputVersionFixture(self.temporary.name)
        self.products = self.fixture.products
        self.original_hashes = self.fixture.model.hashes()
        with patch("server.ThreadPoolExecutor", side_effect=lambda **_: Mock()):
            self.manager = TaskManager(self.fixture.runtime / "tasks", products=self.products)
            self.input_manager = InputTaskManager(self.fixture.runtime / "input-tasks", products=self.products)
        http_handler = handler(self.manager, self.fixture.root, input_manager=self.input_manager, products=self.products)
        http_handler.log_message = lambda *_: None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), http_handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/model/"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.input_manager.close()
        self.manager.close()
        self.assertEqual(self.fixture.model.hashes(), self.original_hashes)
        self.temporary.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        payload = None if body is None else body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        request = urllib.request.Request(self.base + path, method=method, data=payload, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), dict(error.headers)

    def test_lists_and_source_registration_expose_no_filesystem_paths_or_production_claims(self):
        code, versions, headers = self.request("input-products")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(versions["defaultBundleVersionId"], DEFAULT_INPUT_BUNDLE)
        self.assertEqual(len(versions["versions"]), 1)
        self.assertEqual(self.request("input-sources")[1], {"sources": []})
        code, source, _ = self.request("input-sources", "POST", self.fixture.source_payload())
        self.assertEqual(code, 201)
        self.assertFalse(source["productionEligible"])
        self.assertEqual(source["scientificAuthenticity"], "USER_DECLARED_NOT_INDEPENDENTLY_VERIFIED")
        self.assertEqual(source["missingValueCount"], 1)
        self.assertEqual(source["negativeValueCount"], 1)
        self.assertEqual(self.request("input-sources")[1]["sources"], [source])
        self.assertNotIn(str(self.fixture.root), json.dumps(source))
        self.assertNotIn("file", source)
        self.assertEqual(self.manager.executor.submit.call_count, 0)
        self.assertEqual(self.input_manager.executor.submit.call_count, 0)

    def test_processing_queue_result_cancel_and_registration_are_separate_states(self):
        code, task, _ = self.request("input-tasks", "POST", self.fixture.processing_request())
        self.assertEqual(code, 202)
        self.assertTrue(task["taskId"].startswith("DATA-"))
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["resultSource"], "input-processing")
        self.assertFalse(task["productionEligible"])
        route = "input-tasks/" + task["taskId"]
        self.assertEqual(self.request(route)[1]["taskId"], task["taskId"])
        self.assertEqual(self.request("input-tasks")[1]["tasks"][0]["taskId"], task["taskId"])
        self.assertEqual(self.request(route + "/result")[0], 409)
        self.assertEqual(self.request(route + "/register", "POST", {})[0], 409)
        self.assertEqual(self.request(route + "/log")[1]["tail"], "")
        self.assertEqual(self.request(route + "/cancel", "POST", {})[1]["status"], "cancelled")
        self.assertEqual(self.request(route + "/register", "POST", {})[0], 409)
        self.assertEqual(len(self.request("input-products")[1]["versions"]), 1)
        self.assertEqual(self.input_manager.executor.submit.call_count, 1)
        self.manager.executor.submit.assert_not_called()

    def test_completion_does_not_register_but_explicit_verified_registration_is_idempotent(self):
        product = self.fixture.finished_product()
        route = "input-tasks/" + product["taskId"]
        self.assertEqual(self.request(route + "/result")[0], 200)
        self.assertEqual(len(self.request("input-products")[1]["versions"]), 1)
        self.assertEqual(self.request("inputs/catalog?bundleVersionId=" + product["versionId"])[0], 404)
        code, version, _ = self.request(route + "/register", "POST", {})
        self.assertEqual(code, 200)
        self.assertEqual(version["bundleVersionId"], product["versionId"])
        self.assertFalse(version["productionEligible"])
        self.assertEqual(self.request(route + "/register", "POST", {})[1], version)
        versions = self.request("input-products")[1]
        self.assertEqual(len(versions["versions"]), 2)
        self.assertEqual(versions["defaultBundleVersionId"], DEFAULT_INPUT_BUNDLE)
        self.assertEqual(self.request(route)[1]["registeredBundleVersionId"], product["versionId"])

    def test_input_selection_uses_distinct_catalog_window_events_and_never_changes_default(self):
        baseline_events = read_json(self.products.resolve()["paths"]["events"])["packages"]
        product = self.fixture.finished_product("twice", register=True, factor=2, events=[])
        selected = "bundleVersionId=" + product["versionId"]
        code, catalog, _ = self.request("inputs/catalog?" + selected)
        self.assertEqual(code, 200)
        self.assertEqual(catalog["bundleVersionId"], product["versionId"])
        self.assertEqual(catalog["events"], [])
        default = self.request("inputs/catalog")[1]
        self.assertEqual(default["bundleVersionId"], DEFAULT_INPUT_BUNDLE)
        self.assertEqual(len(default["events"]), 1)
        code, window, _ = self.request("inputs/window?start=2017-12-31T22%3A00&end=2018-01-01T03%3A00&" + selected)
        self.assertEqual(code, 200)
        base_window = self.request("inputs/window")[1]
        self.assertEqual(window["bundleVersionId"], product["versionId"])
        self.assertEqual(window["product"]["cells"][0]["values"], [2 * value for value in base_window["product"]["cells"][0]["values"]])
        event_id = baseline_events[0]["eventId"]
        self.assertEqual(self.request("inputs/events/" + event_id + "?warmupHours=2&" + selected)[0], 404)
        self.assertEqual(self.request("inputs/events/" + event_id + "?warmupHours=2")[0], 200)
        # Returning to a previously queried version cannot reuse another version's cache.
        self.assertEqual(self.request("inputs/catalog?" + selected)[1]["events"], [])
        self.assertEqual(self.request("inputs/window")[1]["product"], base_window["product"])

    def test_default_and_explicit_baseline_reverify_files_even_with_warmed_real_reader_caches(self):
        routes = ("inputs/catalog", "inputs/window", "inputs/events/xf-31180101?warmupHours=2")
        queries = [route + (("&" if "?" in route else "?") + "bundleVersionId=" + DEFAULT_INPUT_BUNDLE)
                   for route in routes]
        all_routes = list(routes) + queries
        before = {}
        for route in all_routes:
            code, payload, _ = self.request(route)
            self.assertEqual(code, 200)
            self.assertEqual(payload["bundleVersionId"], DEFAULT_INPUT_BUNDLE)
            before[route] = payload
        paths = [self.products.resolve()["paths"]["flow"], *self.fixture.model.precipitation_dir.glob("*.nc")]
        for path in paths:
            self.assertTrue(path.resolve().is_relative_to(self.fixture.model.root.resolve()))
        original_bytes = {path: path.read_bytes() for path in paths}
        original_precipitation = self.fixture.model.precip.copy()
        original_flow = list(self.fixture.model.flows)
        try:
            # Actual temporary source changes would otherwise mix new NC rain
            # with already cached old flow/catalog under the same bundle SHA.
            self.fixture.model.precip[:, 1, 0] *= 2
            self.fixture.model.flows = [2 * value for value in self.fixture.model.flows]
            self.fixture.model.write_precipitation()
            self.fixture.model.write_flow()
            for route in all_routes:
                with self.subTest(route=route, stage="tampered"):
                    code, error, _ = self.request(route)
                    self.assertEqual(code, 400)
                    self.assertIn("SHA256", error["error"])
                    self.assertNotIn("product", error)
                    self.assertNotIn("events", error)
            self.assertEqual(self.request("simulation-tasks", "POST", model_request())[0], 400)
            self.manager.executor.submit.assert_not_called()
        finally:
            # Restore bytes only in the synthetic model root, even on assertion
            # failure. Existing tearDown verifies the whole original hash tree.
            for path, content in original_bytes.items():
                path.write_bytes(content)
            self.fixture.model.precip = original_precipitation
            self.fixture.model.flows = original_flow
        for route in all_routes:
            with self.subTest(route=route, stage="restored"):
                code, payload, _ = self.request(route)
                self.assertEqual(code, 200)
                self.assertEqual(payload, before[route])

    def test_mutations_require_loopback_same_origin_json_and_bounded_body(self):
        for route, body in (("input-sources", self.fixture.source_payload()),
                            ("input-tasks", self.fixture.processing_request()),
                            ("input-tasks/DATA-missing/cancel", {}), ("input-tasks/DATA-missing/register", {})):
            for headers in ({"Origin": "https://untrusted.invalid"}, {"Host": "untrusted.invalid"},
                            {"Content-Type": "text/plain"}, {"Content-Length": "2097153"}):
                with self.subTest(route=route, headers=headers):
                    self.assertEqual(self.request(route, "POST", body, headers)[0], 400)
        for body in (b"", b"{not-json", [], None, b"null"):
            with self.subTest(body=body):
                self.assertEqual(self.request("input-sources", "POST", body)[0], 400)
                self.assertEqual(self.request("input-tasks", "POST", body)[0], 400)
        self.assertEqual(self.request("input-sources")[1]["sources"], [])
        self.assertEqual(self.request("input-tasks")[1]["tasks"], [])

    def test_context_version_identifiers_duplicate_queries_and_unknown_routes_are_errors(self):
        for query in ("bundleVersionId=latest", "bundleVersionId=current", "bundleVersionId=",
                      "bundleVersionId=..%2Foutside", "bundleVersionId=INPUT-..%5Coutside",
                      "bundleVersionId=F%3A%2Foutside", "bundleVersionId=A&bundleVersionId=B",
                      "projectId=selinco", "resolutionId=0p1deg", "unused=a&unused=b"):
            with self.subTest(query=query):
                self.assertEqual(self.request("inputs/catalog?" + query)[0], 400)
        for route in ("input-tasks/DATA-missing", "input-tasks/DATA-missing/result",
                      "input-tasks/DATA-missing/unknown", "input-tasks/DATA-missing/result/extra"):
            with self.subTest(route=route):
                self.assertEqual(self.request(route)[0], 404)
        for route in ("input-tasks/..%5Coutside", "input-tasks/F%3Aoutside"):
            self.assertEqual(self.request(route)[0], 400)
        for key, value in (("projectId", "selinco"), ("resolutionId", "0p1deg"), ("path", "F:/outside.nc"),
                           ("operation", "execute"), ("start", "2017-12-31T22:30")):
            with self.subTest(key=key):
                self.assertEqual(self.request("input-tasks", "POST", {**self.fixture.processing_request(), key: value})[0], 400)

    def test_concurrent_processing_is_idempotent_and_cancel_does_not_touch_model_queue(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda _: self.request("input-tasks", "POST", self.fixture.processing_request()), range(8)))
        self.assertEqual({row[0] for row in responses}, {202})
        self.assertEqual(len({row[1]["taskId"] for row in responses}), 1)
        self.assertEqual(self.input_manager.executor.submit.call_count, 1)
        changed = {**self.fixture.processing_request(), "name": "Other conditions"}
        self.assertEqual(self.request("input-tasks", "POST", changed)[0], 400)
        with patch("server.case_catalog", self.fixture.catalog):
            code, model_task, _ = self.request("simulation-tasks", "POST", model_request())
        self.assertEqual(code, 202)
        data_id = responses[0][1]["taskId"]
        self.assertEqual(self.request("input-tasks/" + data_id + "/cancel", "POST", {})[1]["status"], "cancelled")
        self.assertEqual(self.request("tasks/" + model_task["taskId"])[1]["status"], "queued")
        self.assertEqual(self.request("tasks/" + data_id)[0], 404)
        self.assertEqual(self.request("input-tasks/" + model_task["taskId"])[0], 404)

    def test_running_processing_cancel_targets_only_its_worker_and_residue_stays_unregistered(self):
        # Test the real orchestration/cancellation branch, not a scientific process.
        _, data_task, _ = self.request("input-tasks", "POST", self.fixture.processing_request())
        with patch("server.case_catalog", self.fixture.catalog):
            _, model_task, _ = self.request("simulation-tasks", "POST", model_request())
        started, stopped = threading.Event(), threading.Event()
        process = Mock(pid=43210)
        process.poll.return_value = None

        def spawn(*args, **kwargs):
            started.set()
            return process

        def wait():
            if not stopped.wait(timeout=5):
                raise RuntimeError("Synthetic worker was not cancelled")
            return 0

        def stop_tree(*args, **kwargs):
            stopped.set()
            return Mock(returncode=0)

        process.wait.side_effect = wait
        process.terminate.side_effect = stopped.set
        worker = threading.Thread(target=self.input_manager.execute, args=(data_task["taskId"],), daemon=True)
        with patch("server.subprocess.Popen", side_effect=spawn) as spawn_worker, patch("server.subprocess.run", side_effect=stop_tree) as terminate_tree:
            worker.start()
            try:
                self.assertTrue(started.wait(timeout=3))
                code, status, _ = self.request("input-tasks/" + data_task["taskId"] + "/cancel", "POST", {})
                self.assertEqual(code, 200)
                self.assertIn(status["status"], ("cancelling", "cancelled"))
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(self.input_manager.get(data_task["taskId"])["status"], "cancelled")
                self.assertEqual(self.manager.get(model_task["taskId"])["status"], "queued")
                command = spawn_worker.call_args.args[0]
                self.assertIn(str(self.input_manager.worker_script), command)
                self.assertIn(str(self.input_manager.root / data_task["taskId"]), command)
                if os.name == "nt":
                    self.assertEqual(terminate_tree.call_args.args[0], ["taskkill.exe", "/PID", "43210", "/T", "/F"])
                else:
                    process.terminate.assert_called_once()
                    terminate_tree.assert_not_called()
            finally:
                stopped.set()
                worker.join(timeout=3)
        self.assertEqual(self.request("input-tasks/" + data_task["taskId"] + "/result")[0], 409)
        self.assertEqual(self.request("input-tasks/" + data_task["taskId"] + "/register", "POST", {})[0], 409)
        self.assertEqual(len(self.request("input-products")[1]["versions"]), 1)

    def test_model_requests_pin_selected_bundle_and_reject_conflicts_before_queueing(self):
        product = self.fixture.finished_product(register=True)
        request = model_request(bundle_version=product["versionId"])
        with patch("server.case_catalog", self.fixture.catalog):
            code, task, _ = self.request("simulation-tasks", "POST", request)
            self.assertEqual(code, 202)
            self.assertEqual(task["runConfig"]["inputs"]["bundleVersionId"], product["versionId"])
            self.assertEqual(task["lineage"]["inputBundleVersionId"], product["versionId"])
            self.assertEqual(task["lineage"]["inputBundleManifestSHA256"], task["runConfig"]["inputs"]["bundleManifestSHA256"])
            conflict = copy.deepcopy(request)
            conflict["requestId"] = "MODEL-conflict"
            conflict["inputs"]["bundleManifestSHA256"] = "b" * 64
            self.assertEqual(self.request("simulation-tasks", "POST", conflict)[0], 400)
            conflict["inputs"].pop("bundleManifestSHA256")
            conflict["lineage"]["forcingVersion"] = "other"
            self.assertEqual(self.request("simulation-tasks", "POST", conflict)[0], 400)
            self.assertEqual(self.request("simulation-tasks", "POST", model_request(bundle_version=DEFAULT_INPUT_BUNDLE))[0], 400)
        self.assertEqual(self.manager.executor.submit.call_count, 1)
        self.assertEqual(len(self.request("tasks")[1]["tasks"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
