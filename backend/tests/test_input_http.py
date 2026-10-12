"""Unified input/diagnostic HTTP boundary without loading scientific inputs."""
import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json, write_json
from input_service import InputService, MAX_WARMUP_HOURS, EVENT_ID
from server import TaskManager, ThreadingHTTPServer, handler


class InputHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        with patch("server.ThreadPoolExecutor"):
            self.manager = TaskManager(self.root / "tasks")
        self.inputs = Mock(spec=InputService)

        def checked_context(context):
            InputService._case(context["project_id"], context["resolution_id"])

        def catalog(**context):
            checked_context(context)
            return {"caseKey": "nananxi-xufan:1km", "sourceType": "model-qc", "latestPeriod": {"hours": 72}}

        def window(start=None, end=None, **context):
            checked_context(context)
            if (start is None) != (end is None):
                raise ValueError("start 和 end 必须同时提供")
            if start is not None:
                InputService._time(start)
                InputService._time(end)
            return {"sourceType": "model-qc", "start": start, "end": end,
                    "observationPreview": {"rain": {}, "river": {"flows": [1, None], "levels": [None, None]}}}

        def event(event_id, warmup_hours=168, **context):
            checked_context(context)
            if not EVENT_ID.fullmatch(event_id):
                raise ValueError("无效场次编号")
            if not 0 <= warmup_hours <= MAX_WARMUP_HOURS:
                raise ValueError("warmupHours 超出登记范围")
            if event_id != "xf-31170619":
                raise KeyError("未登记场次")
            return {"event": {"id": event_id}, "warmupPeriod": {"hours": warmup_hours}, "quality": {"accepted": True}}

        self.inputs.catalog.side_effect = catalog
        self.inputs.window.side_effect = window
        self.inputs.event.side_effect = event
        with patch("server.InputService", return_value=self.inputs):
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.manager, self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/api/model/"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.manager.close()
        self.temporary.cleanup()

    def request(self, path, method="GET", headers=None, body=None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, method=method, data=payload,
                                         headers=headers or {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), dict(error.headers)

    def saved_task(self, task_id, status="completed", with_result=True):
        folder = self.manager.root / task_id
        write_json(folder / "status.json", {"taskId": task_id, "status": status, "createdAt": "2026-10-09T00:00:00Z"})
        if with_result:
            write_json(folder / "result.json", {"taskId": task_id, "resultSource": "model-backend", "productionEligible": False})
        return folder

    def saved_history(self):
        import numpy as np
        folder = self.saved_task("JOB-history")
        fields = ["WU", "WL", "WD", "S", "RS", "RI", "RG"]
        context = {"projectId": "nananxi-xufan", "resolutionId": "1km",
                   "modelConfigId": "nananxi-xufan-1km",
                   "contextKey": "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km"}
        input_digest = "a" * 64
        metadata = {"taskId": "JOB-history", "gridIds": ["NX-001", "NX-002"],
                    "gridGeometry": [{"id": "NX-001", "bounds": [[119, 30]], "areaKm2": 1},
                                     {"id": "NX-002", "bounds": [[119.01, 30]], "areaKm2": 1}],
                    "fields": fields, "context": context, "resultSource": "model-backend",
                    "parameters": {"WM": 100}, "provenance": {"forcingVersion": "precipitation_qc_20260819",
                                                                  "inputManifestSHA256": input_digest},
                    "times": ["2017-06-19T11:00:00", "2017-06-19T12:00:00"]}
        np.savez_compressed(folder / "grid-history.npz", times=np.array(metadata["times"]),
                            **{field: np.array([[1, 2], [3, 4]], dtype=float) for field in fields})
        metadata["arraySHA256"] = hashlib.sha256((folder / "grid-history.npz").read_bytes()).hexdigest()
        write_json(folder / "grid-history.json", metadata)
        write_json(folder / "request.json", {"context": context})
        write_json(folder / "checkpoint.json", {
            "stateVersionId": "STATE-JOB-history", "sourceTaskId": "JOB-history", "context": context,
            "time": metadata["times"][-1], "inputManifestSHA256": input_digest,
            "gridIds": metadata["gridIds"], "gridGeometry": metadata["gridGeometry"],
            "land": {field: [3.0, 4.0] for field in ("WU", "WL", "WD", "S")},
        })
        write_json(folder / "result.json", {
            "taskId": "JOB-history", "context": context, "resultSource": "model-backend", "productionEligible": False,
            "stateVersionId": "STATE-JOB-history", "stateTime": metadata["times"][-1],
            "lineage": {"inputManifestSHA256": input_digest},
            "hydrograph": {"forecast": [{"time": time, "value": 1.0} for time in metadata["times"]]},
            "gridHistory": {"arraySHA256": metadata["arraySHA256"],
                            "catalogSHA256": hashlib.sha256((folder / "grid-history.json").read_bytes()).hexdigest(),
                            "steps": len(metadata["times"]), "gridCount": len(metadata["gridIds"]),
                            "start": metadata["times"][0], "end": metadata["times"][-1]},
        })
        return folder, metadata

    @staticmethod
    def refresh_history_hashes(folder):
        metadata = read_json(folder / "grid-history.json")
        digest = hashlib.sha256((folder / "grid-history.npz").read_bytes()).hexdigest()
        metadata["arraySHA256"] = digest
        write_json(folder / "grid-history.json", metadata)
        result = read_json(folder / "result.json")
        result["gridHistory"]["arraySHA256"] = digest
        result["gridHistory"]["catalogSHA256"] = hashlib.sha256((folder / "grid-history.json").read_bytes()).hexdigest()
        write_json(folder / "result.json", result)

    def test_fixed_default_context_and_successful_shapes(self):
        code, catalog, headers = self.request("inputs/catalog")
        self.assertEqual(code, 200)
        self.assertEqual(catalog["caseKey"], "nananxi-xufan:1km")
        self.inputs.catalog.assert_called_once_with(project_id="nananxi-xufan", resolution_id="1km")
        self.assertEqual(headers["Cache-Control"], "no-store")
        code, window, _ = self.request("inputs/window")
        self.assertEqual(code, 200)
        self.inputs.window.assert_called_once_with(None, None, project_id="nananxi-xufan", resolution_id="1km")
        self.assertIsNone(window["observationPreview"]["river"]["flows"][1])
        self.assertEqual(window["observationPreview"]["rain"], {})
        self.assertEqual(self.request("inputs/window?start=2017-06-19T11%3A00&end=2017-06-19T12%3A00")[0], 200)
        code, event, _ = self.request("inputs/events/xf-31170619")
        self.assertEqual(code, 200)
        self.assertEqual(event["warmupPeriod"]["hours"], 168)
        self.inputs.event.assert_called_with("xf-31170619", warmup_hours=168,
                                             project_id="nananxi-xufan", resolution_id="1km")

    def test_cross_case_resolution_and_explicit_empty_context_rejected(self):
        for query in ("projectId=selinco", "projectId=llpq", "resolutionId=0p1deg",
                      "projectId=", "resolutionId=", "projectId=..%2Foutside"):
            for route in ("inputs/catalog", "inputs/window", "inputs/events/xf-31170619"):
                with self.subTest(route=route, query=query):
                    code, error, _ = self.request(route + "?" + query)
                    self.assertEqual(code, 400)
                    self.assertIn("error", error)

    def test_duplicate_query_rejected_before_any_service_call(self):
        for path in ("inputs/catalog?projectId=nananxi-xufan&projectId=selinco",
                     "inputs/window?start=a&start=b&end=c",
                     "inputs/events/xf-31170619?warmupHours=168&warmupHours=169",
                     "inputs/catalog?unused=x&unused=y"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 400)
        self.inputs.catalog.assert_not_called()
        self.inputs.window.assert_not_called()
        self.inputs.event.assert_not_called()

    def test_warmup_is_typed_bounded_and_explicit_year_is_supported(self):
        for raw in ("true", "False", "1.5", "", "nan", "inf", "-1", "8761"):
            with self.subTest(raw=raw):
                self.assertEqual(self.request("inputs/events/xf-31170619?warmupHours=" + raw)[0], 400)
        code, response, _ = self.request("inputs/events/xf-31170619?warmupHours=8760")
        self.assertEqual(code, 200)
        self.assertEqual(response["warmupPeriod"]["hours"], 8760)
        self.inputs.event.assert_called_with("xf-31170619", warmup_hours=8760,
                                             project_id="nananxi-xufan", resolution_id="1km")

    def test_unknown_route_event_and_incomplete_window_are_errors_not_fallbacks(self):
        for path in ("inputs/unknown", "inputs/events/xf-missing", "inputs/events/xf-missing/extra"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        for path in ("inputs/window?start=2017-06-19T11%3A00",
                     "inputs/window?start=2017-06-19T11%3A30&end=2017-06-19T12%3A00",
                     "inputs/window?start=&end=", "inputs/events/..%5Coutside"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 400)
        self.inputs.window.side_effect = FileNotFoundError("registered input missing")
        self.assertEqual(self.request("inputs/window")[0], 404)
        self.assertFalse(self.inputs.catalog.called)

    def test_non_loopback_host_and_input_mutations_denied(self):
        self.assertEqual(self.request("inputs/catalog", headers={"Host": "untrusted.invalid"})[0], 400)
        self.inputs.catalog.assert_not_called()
        for route in ("inputs/catalog", "inputs/window", "inputs/events/xf-31170619"):
            with self.subTest(route=route):
                self.assertEqual(self.request(route, method="POST", body={})[0], 404)
        self.inputs.catalog.assert_not_called()
        self.inputs.window.assert_not_called()
        self.inputs.event.assert_not_called()

    def test_missing_and_unsuccessful_history_does_not_reconstruct_state(self):
        for status in ("queued", "running", "failed", "cancelled"):
            folder = self.saved_task("JOB-" + status, status)
            for suffix in ("grid-state", "grid-state/catalog"):
                with self.subTest(status=status, suffix=suffix):
                    self.assertEqual(self.request(f"tasks/{folder.name}/{suffix}")[0], 409)
        self.saved_task("JOB-old-success")
        self.assertEqual(self.request("tasks/JOB-old-success/grid-state")[0], 404)
        self.assertEqual(self.request("tasks/JOB-missing/grid-state")[0], 404)
        self.assertEqual(self.request("tasks/JOB-old-success/event-quality")[0], 404)
        self.assertEqual(self.request("tasks/..%5Coutside/grid-state")[0], 400)

    def test_saved_frame_unit_series_and_ambiguous_queries(self):
        _, metadata = self.saved_history()
        code, catalog, _ = self.request("tasks/JOB-history/grid-state/catalog")
        self.assertEqual(code, 200)
        self.assertEqual(catalog["times"], metadata["times"])
        code, frame, _ = self.request("tasks/JOB-history/grid-state?time=2017-06-19T11%3A00")
        self.assertEqual(code, 200)
        self.assertEqual(frame["time"], "2017-06-19T11:00:00")
        self.assertEqual(frame["cells"][0]["WU"], 1)
        self.assertEqual(frame["cells"][1]["WU"], 2)
        self.assertEqual(self.request("tasks/JOB-history/grid-state")[1]["time"], "2017-06-19T12:00:00")
        code, series, _ = self.request("tasks/JOB-history/grid-state?unitId=NX-002")
        self.assertEqual(code, 200)
        self.assertEqual(series["series"]["WU"], [2, 4])
        self.assertFalse(series["productionEligible"])
        self.assertEqual(self.request("tasks/JOB-history/grid-state?unitId=NX-unknown")[0], 404)
        self.assertEqual(self.request("tasks/JOB-history/grid-state?time=2017-06-19T13%3A00")[0], 400)
        self.assertEqual(self.request("tasks/JOB-history/grid-state?time=2017-06-19T11%3A00&unitId=NX-001")[0], 400)
        self.assertEqual(self.request("tasks/JOB-history/grid-state?unitId=NX-001&unitId=NX-002")[0], 400)
        self.assertEqual(self.request("tasks/JOB-history/grid-state/no-such-operation")[0], 404)

    def test_mismatched_or_corrupt_saved_history_is_never_displayed(self):
        import numpy as np
        for corruption in ("context", "input-digest", "array-hash", "catalog-hash", "shape", "nonfinite", "array-times", "checkpoint"):
            with self.subTest(corruption=corruption):
                folder, metadata = self.saved_history()
                if corruption in {"context", "input-digest", "catalog-hash"}:
                    if corruption == "context":
                        metadata["context"]["projectId"] = "selinco"
                    elif corruption == "input-digest":
                        metadata["provenance"]["inputManifestSHA256"] = "wrong-input"
                    else:
                        metadata["parameters"]["WM"] = 200
                    write_json(folder / "grid-history.json", metadata)
                elif corruption == "checkpoint":
                    checkpoint = read_json(folder / "checkpoint.json")
                    checkpoint["land"]["WU"][0] = 99
                    write_json(folder / "checkpoint.json", checkpoint)
                else:
                    with np.load(folder / "grid-history.npz", allow_pickle=False) as archive:
                        arrays = {field: archive[field].copy() for field in archive.files}
                    if corruption == "shape":
                        arrays["RS"] = np.zeros((2, 3))
                    elif corruption == "nonfinite":
                        arrays["RS"][0, 0] = float("nan")
                    elif corruption == "array-times":
                        arrays["times"][0] = "2017-06-18T11:00:00"
                    else:
                        arrays["RS"][0, 0] = 100
                    np.savez_compressed(folder / "grid-history.npz", **arrays)
                    if corruption != "array-hash":
                        self.refresh_history_hashes(folder)
                self.assertEqual(self.request("tasks/JOB-history/grid-state")[0], 409)
                self.assertEqual(self.request("tasks/JOB-history/grid-state/catalog")[0], 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
