"""Persisted parent states must belong to matching, successful model tasks."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json, write_json
from server import TaskManager
from state_integrity import validated_parent_state


CONTEXT = {"projectId": "nananxi-xufan", "resolutionId": "1km", "modelConfigId": "nananxi-xufan-1km",
           "contextKey": "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km"}
PARAMETERS = {"KC": 0.8, "WM": 140, "WUM": 25, "WLM": 80, "B": 0.3, "C": 0.1,
              "SM": 30, "EX": 1.5, "KI": 0.01, "KG": 0.005, "IM": 0.02}
STATE_ID = "STATE-JOB-parent-test"
TASK_ID = "JOB-parent-test"


class StateIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name)
        self.tasks = self.runtime / "tasks"
        self.folder = self.tasks / TASK_ID
        self.state_path = self.runtime / "states" / f"{STATE_ID}.json"
        self.checkpoint = {"stateVersionId": STATE_ID, "sourceTaskId": TASK_ID, "context": copy.deepcopy(CONTEXT),
                           "time": "2017-06-20T11:00:00", "inputManifestSHA256": "a" * 64,
                           "land": {"WU": [10, 12]}, "gridIds": ["NX-001", "NX-002"],
                           "parameters": copy.deepcopy(PARAMETERS)}
        self.status = {"taskId": TASK_ID, "status": "completed", "createdAt": "2026-10-09T00:00:00Z"}
        self.result = {"taskId": TASK_ID, "stateVersionId": STATE_ID, "context": copy.deepcopy(CONTEXT),
                       "resultSource": "model-backend", "stateTime": self.checkpoint["time"],
                       "lineage": {"inputManifestSHA256": self.checkpoint["inputManifestSHA256"]}}
        self.request = {"requestId": "TEST-PARENT", "context": copy.deepcopy(CONTEXT)}
        self.persist()

    def tearDown(self):
        self.temporary.cleanup()

    def persist(self):
        write_json(self.state_path, self.checkpoint)
        write_json(self.folder / "checkpoint.json", self.checkpoint)
        write_json(self.folder / "status.json", self.status)
        write_json(self.folder / "result.json", self.result)
        write_json(self.folder / "request.json", self.request)

    def validate(self):
        return validated_parent_state(self.state_path, self.tasks, STATE_ID)

    def test_matching_completed_and_archived_parent_accepted(self):
        for status in ("completed", "archived"):
            with self.subTest(status=status):
                self.status["status"] = status
                self.persist()
                self.assertEqual(self.validate(), self.checkpoint)

    def test_invalid_version_and_path_identifiers_rejected_before_reading(self):
        for version in (None, "STATE-preview", "STATE-JOB-../outside", "STATE-JOB-..\\outside", "STATE-JOB-", "JOB-parent-test"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validated_parent_state(self.runtime / "not-present.json", self.tasks, version)

    def test_checkpoint_state_version_and_source_task_must_match(self):
        for key, value in (("sourceTaskId", "JOB-another"), ("sourceTaskId", "../outside"),
                           ("sourceTaskId", None), ("stateVersionId", "STATE-JOB-another"), ("stateVersionId", None)):
            with self.subTest(key=key, value=value):
                checkpoint = copy.deepcopy(self.checkpoint)
                checkpoint[key] = value
                write_json(self.state_path, checkpoint)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_unsuccessful_or_mismatched_task_status_rejected(self):
        for status in ("queued", "running", "cancelling", "failed", "cancelled", "unknown", None):
            with self.subTest(status=status):
                self.status["status"] = status
                self.persist()
                with self.assertRaises(ValueError):
                    self.validate()
        self.status.update(status="completed", taskId="JOB-another")
        self.persist()
        with self.assertRaises(ValueError):
            self.validate()

    def test_public_state_cannot_diverge_from_source_task_checkpoint(self):
        for key, value in (("time", "2017-06-20T12:00:00"), ("gridIds", ["NX-003"]),
                           ("land", {"WU": [99, 12]}), ("inputManifestSHA256", "wrong-input")):
            with self.subTest(key=key):
                checkpoint = copy.deepcopy(self.checkpoint)
                checkpoint[key] = value
                write_json(self.state_path, checkpoint)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_result_task_version_origin_context_digest_and_time_must_match(self):
        mutations = [("taskId", "JOB-another"), ("stateVersionId", "STATE-JOB-another"),
                     ("resultSource", "static-preview"), ("resultSource", None),
                     ("context", {**CONTEXT, "resolutionId": "0p1deg"}), ("context", None),
                     ("lineage", {"inputManifestSHA256": "wrong-input"}), ("lineage", {}),
                     ("stateTime", "2017-06-20T12:00:00"), ("stateTime", None)]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                result = copy.deepcopy(self.result)
                result[key] = value
                write_json(self.folder / "result.json", result)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_missing_input_digest_rejected_even_if_checkpoint_copies_agree(self):
        self.checkpoint["inputManifestSHA256"] = None
        self.result["lineage"]["inputManifestSHA256"] = None
        self.persist()
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_source_artifact_does_not_fall_back_to_public_state(self):
        missing_root = self.runtime / "not-registered-tasks"
        with self.assertRaises(FileNotFoundError):
            validated_parent_state(self.state_path, missing_root, STATE_ID)

    def test_legacy_successful_result_uses_matching_frozen_request_context(self):
        self.result.pop("context")
        self.result["contextKey"] = CONTEXT["contextKey"]
        for status in ("completed", "archived"):
            with self.subTest(status=status):
                self.status["status"] = status
                self.persist()
                self.assertEqual(self.validate(), self.checkpoint)

    def test_legacy_result_and_frozen_request_context_must_match(self):
        self.result.pop("context")
        self.result["contextKey"] = CONTEXT["contextKey"]
        mutations = [
            ("result-key", "another-context"),
            ("request-key", "another-context"),
            ("request-case", {**CONTEXT, "resolutionId": "0p1deg"}),
            ("request-type", None),
            ("request-type", "nananxi-xufan:1km"),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                result = copy.deepcopy(self.result)
                request = copy.deepcopy(self.request)
                if key == "result-key":
                    result["contextKey"] = value
                elif key == "request-key":
                    request["context"]["contextKey"] = value
                else:
                    request["context"] = value
                write_json(self.folder / "result.json", result)
                write_json(self.folder / "request.json", request)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_legacy_result_without_frozen_request_does_not_guess_context(self):
        self.result.pop("context")
        self.result["contextKey"] = CONTEXT["contextKey"]
        self.persist()
        with patch("state_integrity.read_json", wraps=read_json) as read_artifact:
            def read_without_request(path):
                if Path(path).name == "request.json":
                    raise FileNotFoundError(path)
                return read_json(path)
            read_artifact.side_effect = read_without_request
            with self.assertRaises(FileNotFoundError):
                self.validate()

    def catalog_stub(self, bundle=None):
        return {"cases": {"nananxi-xufan:1km": {
            "ready": True, "operations": ["stateContinuation"], "unsupported": {}, "parameters": PARAMETERS,
        }}}

    def continuation_request(self):
        return {"requestId": "TEST-PARENT-CONTINUATION", "runType": "stateContinuation", "context": copy.deepcopy(CONTEXT),
                "period": {"startTime": "2017-06-20T12:00", "endTime": "2017-06-21T12:00", "warmupDays": 7,
                           "initialStateVersionId": STATE_ID},
                "parameters": copy.deepcopy(PARAMETERS), "inputs": {"evaporation": {"mode": "monthly-climatology-assumption"}},
                "lineage": {}}

    def test_task_manager_rejects_failed_parent_before_queueing(self):
        with patch("server.RUNTIME", self.runtime), patch("server.case_catalog", self.catalog_stub), patch("server.ThreadPoolExecutor"):
            manager = TaskManager(self.tasks)
            try:
                self.status["status"] = "failed"
                self.persist()
                with self.assertRaises(ValueError):
                    manager.submit(self.continuation_request())
                manager.executor.submit.assert_not_called()
                self.assertEqual([path.name for path in self.tasks.iterdir()], [TASK_ID])
            finally:
                manager.close()

    def test_task_manager_allows_verified_parent_but_rejects_every_core_identity_change(self):
        with patch("server.RUNTIME", self.runtime), patch("server.case_catalog", self.catalog_stub), patch("server.ThreadPoolExecutor"):
            manager = TaskManager(self.tasks)
            try:
                for key, value in (("projectId", "selinco"), ("resolutionId", "0p1deg"),
                                   ("modelConfigId", "nananxi-xufan-other-model"),
                                   ("contextKey", "nananxi-xufan:other-config")):
                    with self.subTest(key=key):
                        stored_context = {**CONTEXT, key: value}
                        self.checkpoint["context"] = copy.deepcopy(stored_context)
                        self.result["context"] = copy.deepcopy(stored_context)
                        self.request["context"] = copy.deepcopy(stored_context)
                        self.persist()
                        # The source task is successful and its saved artifacts
                        # agree; rejection must happen at the continuation handoff.
                        self.assertEqual(self.validate(), self.checkpoint)
                        with self.assertRaisesRegex(ValueError, "父状态与请求上下文不一致"):
                            manager.submit(self.continuation_request())
                        manager.executor.submit.assert_not_called()
                        self.assertEqual([path.name for path in self.tasks.iterdir()], [TASK_ID])
                self.checkpoint["context"] = copy.deepcopy(CONTEXT)
                self.result["context"] = copy.deepcopy(CONTEXT)
                self.request["context"] = copy.deepcopy(CONTEXT)
                self.persist()
                task = manager.submit(self.continuation_request())
                self.assertEqual(task["status"], "queued")
                self.assertEqual(task["runConfig"]["period"]["initialStateVersionId"], STATE_ID)
                manager.executor.submit.assert_called_once()
            finally:
                manager.close()

    def test_task_manager_ignores_optional_descriptions_only_at_continuation_handoff(self):
        descriptions = {"projectName": "南安溪徐畈", "modelFrameworkId": "distributed-xinanjiang"}
        scenarios = [({}, descriptions), (descriptions, {}),
                     (descriptions, {"projectName": "南安溪（徐畈断面）", "modelFrameworkId": "display-only-label"})]
        with patch("server.RUNTIME", self.runtime), patch("server.case_catalog", self.catalog_stub), patch("server.ThreadPoolExecutor"):
            manager = TaskManager(self.tasks)
            try:
                for index, (source_labels, request_labels) in enumerate(scenarios):
                    with self.subTest(source=source_labels, request=request_labels):
                        stored_context = {**CONTEXT, **source_labels}
                        self.checkpoint["context"] = copy.deepcopy(stored_context)
                        self.result["context"] = copy.deepcopy(stored_context)
                        self.request["context"] = copy.deepcopy(stored_context)
                        self.persist()
                        self.assertEqual(self.validate(), self.checkpoint)
                        request = self.continuation_request()
                        request["requestId"] += f"-DESCRIPTIONS-{index}"
                        request["context"].update(request_labels)
                        task = manager.submit(request)
                        self.assertEqual(task["status"], "queued")
                        self.assertEqual(task["runConfig"]["context"], request["context"])
                        self.assertEqual(task["runConfig"]["period"]["initialStateVersionId"], STATE_ID)
                        self.assertEqual(manager.executor.submit.call_count, index + 1)
                        self.assertEqual(read_json(self.folder / "checkpoint.json")["context"], stored_context)
                        self.assertEqual(read_json(self.folder / "result.json")["context"], stored_context)
                        # Optional labels are ignored only between a verified
                        # parent and the new request, never within saved artifacts.
                        for field in descriptions:
                            result = copy.deepcopy(self.result)
                            result["context"][field] = "modified-only-in-result"
                            write_json(self.folder / "result.json", result)
                            with self.assertRaisesRegex(ValueError, "父状态与成功结果"):
                                self.validate()
                        self.persist()
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
