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
from calibration_platform.production_tasks import ProductionCalibrationTaskManager


SUCCESS_PROGRAM = r"""
import json
import sys
from pathlib import Path
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for progress, stage in ((20, 'BOUNDARY_WLM_100'), (60, 'MULTI_SEED'), (90, 'CONTINUOUS_WATER_BALANCE'), (100, 'REVIEW_REQUIRED')):
    print(f'workflow_progress={progress} stage={stage}', flush=True)
report = {
    'workflow_id': 'TEST_WORKFLOW',
    'status': 'REVIEW_REQUIRED',
    'production_approved': False,
    'boundary': {'boundary_resolved': True},
    'multi_seed_stability': {'stable': True},
    'continuous_water_balance': {'status': 'SUCCEEDED'},
    'production_gate': {'failed_check_ids': ['PET_PROVENANCE']},
    'representative_run': 'SEED_2',
}
(out / 'production_workflow_report.json').write_text(json.dumps(report), encoding='utf-8')
(out / 'review_candidate_parameter_set.json').write_text('{}', encoding='utf-8')
"""


FORMAL_SUCCESS_PROGRAM = r"""
import json
import sys
from pathlib import Path
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
report = {
    'calibration_scope': 'RUNOFF_ONLY_ROUTING_FROZEN',
    'algorithm': 'DIFFERENTIAL_EVOLUTION',
    'common_parameter_set': {'runoff.WM': 180.0},
    'resolved_runoff_parameter_set': {'runoff.WM': 180.0},
    'groups': {}, 'split': {}, 'objective': {'loss': 0.4},
}
(out / 'formal_multi_event_report.json').write_text(json.dumps(report), encoding='utf-8')
"""


class ProductionTaskManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        (self.root / "precip").mkdir()
        (self.root / "frontend").mkdir()
        (self.root / "frontend" / "index.html").write_text(
            "<h1>platform</h1>", encoding="utf-8"
        )
        for name in (
            "parameters.json",
            "objective.json",
            "quality.json",
            "state.json",
            "pet.json",
            "initial.json",
            "continuous.json",
            "event_catalog.json",
            "data_review.json",
        ):
            (self.root / name).write_text("{}", encoding="utf-8")
        profile = {
            "profiles": {
                "TEST_PROFILE": {
                    "data_root": str(self.root / "data"),
                    "precipitation_directory": str(self.root / "precip"),
                    "event_catalog": str(self.root / "event_catalog.json"),
                    "data_review_config": str(self.root / "data_review.json"),
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
            }
        }
        workflows = {
            "workflows": {
                "TEST_WORKFLOW": {
                    "name_zh": "测试生产工作流",
                    "description": "快速替代进程",
                    "base_calibration_profile_id": "TEST_PROFILE",
                    "continuous_balance_config": str(self.root / "continuous.json"),
                    "defaults": {
                        "algorithm": "DIFFERENTIAL_EVOLUTION",
                        "max_evaluations": 4,
                        "population_size": 4,
                        "generations": 1,
                        "differential_weight": 0.8,
                        "crossover_probability": 0.7,
                        "state_strategy": "OBSERVED_FLOW_ANCHORED",
                        "split_seed": 42,
                        "search_seeds": [1, 2, 3],
                        "wlm_upper_bounds_mm": [100.0, 130.0],
                    },
                    "stability_criteria": {},
                    "production_criteria": {},
                }
            }
        }
        self.profiles_path = self.root / "profiles.json"
        self.workflows_path = self.root / "workflows.json"
        self.profiles_path.write_text(json.dumps(profile), encoding="utf-8")
        self.workflows_path.write_text(json.dumps(workflows), encoding="utf-8")
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.shutdown(wait=True)
        self.temporary.cleanup()

    def production_manager(self, command_builder=None, task_root=None, guard=None):
        command_builder = command_builder or (
            lambda request, output: [
                sys.executable,
                "-u",
                "-c",
                SUCCESS_PROGRAM,
                str(output),
            ]
        )
        manager = ProductionCalibrationTaskManager(
            project_root=self.root,
            task_root=task_root or self.root / "production_tasks",
            workflows_path=self.workflows_path,
            calibration_profiles_path=self.profiles_path,
            command_builder=command_builder,
            execution_guard=guard,
        )
        self.managers.append(manager)
        return manager

    def formal_manager(self):
        manager = FormalCalibrationTaskManager(
            project_root=self.root,
            task_root=self.root / "formal_tasks",
            profiles_path=self.profiles_path,
            command_builder=lambda request, output: [
                sys.executable,
                "-u",
                "-c",
                FORMAL_SUCCESS_PROGRAM,
                str(output),
            ],
        )
        self.managers.append(manager)
        return manager

    @staticmethod
    def wait_for(manager, task_id, terminal=True, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = manager.get_task(task_id)
            if terminal and status["status"] in {
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
                "INTERRUPTED",
            }:
                return status
            if not terminal and status["status"] == "RUNNING":
                return status
            time.sleep(0.03)
        raise AssertionError(f"task {task_id} did not reach expected state")

    def test_request_validation_is_strict(self):
        manager = self.production_manager()
        self.assertEqual(manager.catalog()["status"], "READY_ASYNC")
        with self.assertRaises(FormalCalibrationTaskError):
            manager.submit({"workflow_id": "MISSING"})
        with self.assertRaises(FormalCalibrationTaskError):
            manager.submit({"workflow_id": "TEST_WORKFLOW", "split_seed": 1.5})
        with self.assertRaises(FormalCalibrationTaskError):
            manager.submit(
                {"workflow_id": "TEST_WORKFLOW", "search_seeds": [1, 2.5, 3]}
            )
        with self.assertRaises(FormalCalibrationTaskError):
            manager.submit({"workflow_id": "TEST_WORKFLOW", "shell": "bad"})

    def test_success_persists_review_outcome_and_safe_files(self):
        manager = self.production_manager()
        submitted = manager.submit(
            {"workflow_id": "TEST_WORKFLOW", "task_id": "PROD-SUCCESS"}
        )
        self.assertEqual(submitted["status"], "QUEUED")
        status = self.wait_for(manager, "PROD-SUCCESS")
        self.assertEqual(status["status"], "SUCCEEDED")
        self.assertEqual(status["workflow_outcome"], "REVIEW_REQUIRED")
        self.assertFalse(status["production_approved"])
        result = manager.get_result("PROD-SUCCESS")
        self.assertEqual(result["representative_run"], "SEED_2")
        self.assertTrue(
            manager.resolve_output_file(
                "PROD-SUCCESS", "review_candidate_parameter_set.json"
            ).is_file()
        )
        with self.assertRaises(FormalCalibrationTaskError):
            manager.resolve_output_file("PROD-SUCCESS", "../status.json")

    def test_failure_cancel_restart_and_duplicate_are_explicit(self):
        failure = self.production_manager(
            lambda request, output: [
                sys.executable,
                "-u",
                "-c",
                "import sys; print('failed', flush=True); sys.exit(3)",
            ],
            task_root=self.root / "failed_tasks",
        )
        failure.submit({"workflow_id": "TEST_WORKFLOW", "task_id": "PROD-FAILED"})
        status = self.wait_for(failure, "PROD-FAILED")
        self.assertEqual(status["status"], "FAILED")
        with self.assertRaises(FormalCalibrationTaskConflict):
            failure.get_result("PROD-FAILED")

        cancel = self.production_manager(
            lambda request, output: [
                sys.executable,
                "-u",
                "-c",
                "import time; print('running', flush=True); time.sleep(30)",
            ],
            task_root=self.root / "cancel_tasks",
        )
        cancel.submit({"workflow_id": "TEST_WORKFLOW", "task_id": "PROD-CANCEL"})
        self.wait_for(cancel, "PROD-CANCEL", terminal=False)
        cancel.cancel("PROD-CANCEL")
        self.assertEqual(self.wait_for(cancel, "PROD-CANCEL")["status"], "CANCELLED")

        restart_root = self.root / "restart_tasks"
        restart_dir = restart_root / "PROD-RESTART"
        restart_dir.mkdir(parents=True)
        (restart_dir / "status.json").write_text(
            json.dumps({"task_id": "PROD-RESTART", "status": "RUNNING"}),
            encoding="utf-8",
        )
        restart = self.production_manager(task_root=restart_root)
        self.assertEqual(restart.get_task("PROD-RESTART")["status"], "INTERRUPTED")

        success = self.production_manager(task_root=self.root / "duplicate_tasks")
        success.submit({"workflow_id": "TEST_WORKFLOW", "task_id": "PROD-DUP"})
        with self.assertRaises(FormalCalibrationTaskConflict):
            success.submit({"workflow_id": "TEST_WORKFLOW", "task_id": "PROD-DUP"})

    def test_shared_execution_guard_waits_and_cancel_releases_cleanly(self):
        guard = threading.Lock()
        guard.acquire()
        manager = self.production_manager(guard=guard)
        manager.submit({"workflow_id": "TEST_WORKFLOW", "task_id": "PROD-WAIT"})
        deadline = time.time() + 5
        while time.time() < deadline:
            if manager.get_task("PROD-WAIT")["stage"] == "WAITING_FOR_CALIBRATION_SLOT":
                break
            time.sleep(0.03)
        self.assertEqual(
            manager.get_task("PROD-WAIT")["stage"], "WAITING_FOR_CALIBRATION_SLOT"
        )
        manager.cancel("PROD-WAIT")
        guard.release()
        self.assertEqual(self.wait_for(manager, "PROD-WAIT")["status"], "CANCELLED")

    def test_http_workflow_routes(self):
        formal = self.formal_manager()
        production = self.production_manager()
        server = create_platform_server(
            "127.0.0.1",
            0,
            formal,
            self.root / "frontend",
            production_manager=production,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(
                base + "/api/calibration/workflows/catalog", timeout=5
            ) as response:
                catalog = json.loads(response.read().decode("utf-8"))
            self.assertEqual(catalog["module_id"], "PRODUCTION-CALIBRATION-10")
            request = urllib.request.Request(
                base + "/api/calibration/workflows/tasks",
                data=json.dumps(
                    {"workflow_id": "TEST_WORKFLOW", "task_id": "PROD-HTTP"}
                ).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 202)
            self.wait_for(production, "PROD-HTTP")
            with urllib.request.urlopen(
                base + "/api/calibration/workflows/tasks/PROD-HTTP/results",
                timeout=5,
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertEqual(result["workflow_outcome"], "REVIEW_REQUIRED")
            with urllib.request.urlopen(
                base
                + "/api/calibration/workflows/tasks/PROD-HTTP/files/review_candidate_parameter_set.json",
                timeout=5,
            ) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
