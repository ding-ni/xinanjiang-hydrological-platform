import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from calibration_platform.formal_tasks import (
    FormalCalibrationTaskConflict,
    FormalCalibrationTaskError,
    FormalCalibrationTaskManager,
)
from calibration_platform.http_adapter import create_platform_server


SUCCESS_PROGRAM = r"""
import json
import sys
from pathlib import Path
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
print('evaluation=1 loss=0.5 cal_nse=0.1', flush=True)
print('evaluation=2 loss=0.4 cal_nse=0.2', flush=True)
print('final evaluation 1/2', flush=True)
print('final evaluation 2/2', flush=True)
groups = {
    name: {
        'event_count': 2,
        'qualified_count': 1,
        'qualified_rate_percent': 50.0,
        'mean_NSE': 0.6,
        'mean_KGE': 0.5,
        'mean_abs_peak_error_percent': 10.0,
        'mean_abs_volume_error_percent': 8.0,
        'median_peak_time_error_hours': 1.0,
    }
    for name in ('CALIBRATION', 'VALIDATION', 'INDEPENDENT_TEST')
}
report = {
    'calibration_scope': 'RUNOFF_ONLY_ROUTING_FROZEN',
    'algorithm': 'DIFFERENTIAL_EVOLUTION',
    'common_parameter_set': {'runoff.WM': 180.0},
    'resolved_runoff_parameter_set': {'runoff.WM': 180.0},
    'groups': groups,
    'split': {'counts': {'CALIBRATION': 1, 'VALIDATION': 1, 'INDEPENDENT_TEST': 1}},
    'objective': {'loss': 0.4},
}
(out / 'formal_multi_event_report.json').write_text(json.dumps(report), encoding='utf-8')
(out / 'event_metrics.csv').write_text('event_id,NSE\nE1,0.6\n', encoding='utf-8')
"""


class FormalTaskManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        (self.root / "precip").mkdir()
        (self.root / "frontend").mkdir()
        (self.root / "frontend/index.html").write_text("<h1>platform</h1>", encoding="utf-8")
        for name in (
            "parameters.json",
            "objective.json",
            "quality.json",
            "state.json",
            "pet.json",
            "initial.json",
            "event_catalog.json",
        ):
            (self.root / name).write_text("{}", encoding="utf-8")
        profile = {
            "schema_version": "1.0",
            "profiles": {
                "TEST": {
                    "module_id": "MULTI-EVENT-CALIBRATION-06",
                    "calibration_scope": "RUNOFF_ONLY_ROUTING_FROZEN",
                    "data_root": str(self.root / "data"),
                    "precipitation_directory": str(self.root / "precip"),
                    "event_catalog": str(self.root / "event_catalog.json"),
                    "parameter_space": str(self.root / "parameters.json"),
                    "objective_config": str(self.root / "objective.json"),
                    "event_quality_config": str(self.root / "quality.json"),
                    "state_config": str(self.root / "state.json"),
                    "pet_config": str(self.root / "pet.json"),
                    "initial_land_state": str(self.root / "initial.json"),
                    "defaults": {
                        "algorithm": "DIFFERENTIAL_EVOLUTION",
                        "max_evaluations": 4,
                        "population_size": 4,
                        "generations": 1,
                        "differential_weight": 0.8,
                        "crossover_probability": 0.7,
                        "state_strategy": "OBSERVED_FLOW_ANCHORED",
                        "split_seed": 42,
                        "search_seed": 7,
                        "calibration_ratio": 0.667,
                        "validation_ratio": 0.201,
                    },
                }
            },
        }
        self.profiles_path = self.root / "profiles.json"
        self.profiles_path.write_text(json.dumps(profile), encoding="utf-8")
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.shutdown(wait=True)
        self.temporary.cleanup()

    def manager(self, command_builder=None, task_root=None):
        command_builder = command_builder or (
            lambda request, output: [sys.executable, "-u", "-c", SUCCESS_PROGRAM, str(output)]
        )
        manager = FormalCalibrationTaskManager(
            project_root=self.root,
            task_root=task_root or self.root / "tasks",
            profiles_path=self.profiles_path,
            command_builder=command_builder,
        )
        self.managers.append(manager)
        return manager

    @staticmethod
    def wait_for(manager, task_id, terminal=True, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = manager.get_task(task_id)
            if terminal and status["status"] in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
                return status
            if not terminal and status["status"] == "RUNNING":
                return status
            time.sleep(0.03)
        raise AssertionError(f"task {task_id} did not reach expected state")

    def test_request_whitelist_profile_and_duplicate_validation(self):
        manager = self.manager()
        catalog = manager.catalog()
        self.assertEqual(catalog["status"], "READY_ASYNC")
        self.assertEqual(catalog["profiles"][0]["profile_id"], "TEST")
        with self.assertRaises(FormalCalibrationTaskError):
            manager.submit({"profile_id": "TEST", "output_dir": "elsewhere"})
        with self.assertRaises(FormalCalibrationTaskError):
            manager.submit({"profile_id": "MISSING"})
        manager.submit({"profile_id": "TEST", "task_id": "CAL-DUPLICATE"})
        with self.assertRaises(FormalCalibrationTaskConflict):
            manager.submit({"profile_id": "TEST", "task_id": "CAL-DUPLICATE"})

    def test_success_persists_progress_result_and_safe_files(self):
        manager = self.manager()
        submitted = manager.submit({"profile_id": "TEST", "task_id": "CAL-SUCCESS"})
        self.assertEqual(submitted["status"], "QUEUED")
        status = self.wait_for(manager, "CAL-SUCCESS")
        self.assertEqual(status["status"], "SUCCEEDED")
        self.assertEqual(status["progress_percent"], 100.0)
        result = manager.get_result("CAL-SUCCESS")
        self.assertEqual(result["groups"]["VALIDATION"]["mean_NSE"], 0.6)
        self.assertTrue(manager.resolve_output_file("CAL-SUCCESS", "event_metrics.csv").is_file())
        self.assertIn("evaluation=2", manager.get_log_tail("CAL-SUCCESS"))
        with self.assertRaises(FormalCalibrationTaskError):
            manager.resolve_output_file("CAL-SUCCESS", "../status.json")

    def test_failure_and_result_conflict_are_explicit(self):
        manager = self.manager(
            lambda request, output: [
                sys.executable,
                "-u",
                "-c",
                "import sys; print('intentional failure', flush=True); sys.exit(3)",
            ]
        )
        manager.submit({"profile_id": "TEST", "task_id": "CAL-FAILED"})
        status = self.wait_for(manager, "CAL-FAILED")
        self.assertEqual(status["status"], "FAILED")
        self.assertIn("code 3", status["error"])
        with self.assertRaises(FormalCalibrationTaskConflict):
            manager.get_result("CAL-FAILED")

    def test_running_task_can_be_cancelled(self):
        manager = self.manager(
            lambda request, output: [
                sys.executable,
                "-u",
                "-c",
                "import time; print('started', flush=True); time.sleep(30)",
            ]
        )
        manager.submit({"profile_id": "TEST", "task_id": "CAL-CANCEL"})
        self.wait_for(manager, "CAL-CANCEL", terminal=False)
        manager.cancel("CAL-CANCEL")
        status = self.wait_for(manager, "CAL-CANCEL")
        self.assertEqual(status["status"], "CANCELLED")

    def test_restart_marks_active_persisted_task_interrupted(self):
        task_root = self.root / "restart_tasks"
        task_dir = task_root / "CAL-RESTART"
        task_dir.mkdir(parents=True)
        (task_dir / "status.json").write_text(
            json.dumps({"task_id": "CAL-RESTART", "status": "RUNNING"}),
            encoding="utf-8",
        )
        manager = self.manager(task_root=task_root)
        status = manager.get_task("CAL-RESTART")
        self.assertEqual(status["status"], "INTERRUPTED")
        self.assertEqual(status["stage"], "SERVER_RESTARTED")

    def test_http_submit_query_result_and_static_frontend(self):
        manager = self.manager()
        server = create_platform_server("127.0.0.1", 0, manager, self.root / "frontend")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                self.assertIn(b"platform", response.read())
            body = json.dumps({"profile_id": "TEST", "task_id": "CAL-HTTP"}).encode("utf-8")
            request = urllib.request.Request(
                base + "/api/calibration/tasks",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 202)
            self.wait_for(manager, "CAL-HTTP")
            with urllib.request.urlopen(base + "/api/calibration/tasks/CAL-HTTP/results", timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertEqual(result["status"], "SUCCEEDED")
            with urllib.request.urlopen(base + "/api/calibration/tasks/CAL-HTTP/files/event_metrics.csv", timeout=5) as response:
                self.assertIn(b"event_id", response.read())
            bad = urllib.request.Request(
                base + "/api/calibration/tasks",
                data=json.dumps({"profile_id": "TEST", "shell": "bad"}).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(context.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
