"""Persistent asynchronous manager for production calibration workflows."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .formal_tasks import (
    ACTIVE_STATUSES,
    ALGORITHMS,
    STATE_STRATEGIES,
    TASK_ID_PATTERN,
    TERMINAL_STATUSES,
    FormalCalibrationTaskConflict,
    FormalCalibrationTaskError,
    FormalCalibrationTaskNotFound,
)


PRODUCTION_CALIBRATION_MODULE_ID = "PRODUCTION-CALIBRATION-10"
WORKFLOW_PROGRESS_PATTERN = re.compile(
    r"\bworkflow_progress=([0-9.]+)\s+stage=([^\s]+)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _resolve(value: str, root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


class ProductionCalibrationTaskManager:
    """[CAL-PRODUCTION-TASK-10] Run complete production acceptance workflows."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        task_root: str | Path | None = None,
        workflows_path: str | Path | None = None,
        calibration_profiles_path: str | Path | None = None,
        command_builder: Callable[[Path, Path], list[str]] | None = None,
        max_workers: int = 1,
        execution_guard: threading.Lock | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.task_root = (
            Path(task_root).resolve()
            if task_root is not None
            else self.project_root / "validation" / "platform_production_workflows"
        )
        self.workflows_path = (
            Path(workflows_path).resolve()
            if workflows_path is not None
            else self.project_root / "config" / "production_calibration_workflows.json"
        )
        self.calibration_profiles_path = (
            Path(calibration_profiles_path).resolve()
            if calibration_profiles_path is not None
            else self.project_root / "config" / "platform_calibration_profiles.json"
        )
        self.task_root.mkdir(parents=True, exist_ok=True)
        self._workflows = json.loads(self.workflows_path.read_text(encoding="utf-8-sig"))
        self._calibration_profiles = json.loads(
            self.calibration_profiles_path.read_text(encoding="utf-8-sig")
        )
        if not self._workflows.get("workflows"):
            raise FormalCalibrationTaskError("production workflow config has no workflows")
        self._command_builder = command_builder or self._default_command
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="production-calibration",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancel_requested: set[str] = set()
        self._execution_guard = execution_guard
        self._recover_interrupted()

    def catalog(self) -> dict[str, Any]:
        workflows = []
        for workflow_id, workflow in self._workflows["workflows"].items():
            workflows.append(
                {
                    "workflow_id": workflow_id,
                    "name_zh": workflow.get("name_zh", workflow_id),
                    "description": workflow.get("description", ""),
                    "module_id": PRODUCTION_CALIBRATION_MODULE_ID,
                    "defaults": workflow.get("defaults", {}),
                    "stages": [
                        "CAL-WLM-BOUNDARY-07",
                        "CAL-MULTI-SEED-08",
                        "CAL-CONTINUOUS-BALANCE-09",
                        "CAL-PRODUCTION-GATE-10",
                    ],
                }
            )
        return {
            "module_id": PRODUCTION_CALIBRATION_MODULE_ID,
            "status": "READY_ASYNC",
            "workflows": workflows,
            "task_statuses": sorted(ACTIVE_STATUSES | TERMINAL_STATUSES),
        }

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise FormalCalibrationTaskError("request body must be a JSON object")
        allowed = {
            "module_id",
            "task_id",
            "workflow_id",
            "algorithm",
            "max_evaluations",
            "population_size",
            "generations",
            "differential_weight",
            "crossover_probability",
            "state_strategy",
            "split_seed",
            "search_seeds",
            "wlm_upper_bounds_mm",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise FormalCalibrationTaskError(f"unsupported workflow fields: {unknown}")
        module_id = str(payload.get("module_id", PRODUCTION_CALIBRATION_MODULE_ID)).upper()
        if module_id != PRODUCTION_CALIBRATION_MODULE_ID:
            raise FormalCalibrationTaskError(f"unsupported module_id: {module_id}")
        workflow_id = str(payload.get("workflow_id", ""))
        workflows = self._workflows["workflows"]
        if workflow_id not in workflows:
            raise FormalCalibrationTaskError(f"unknown production workflow: {workflow_id}")
        defaults = workflows[workflow_id].get("defaults", {})
        task_id = str(payload.get("task_id") or self._new_task_id())
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise FormalCalibrationTaskError("invalid task_id")
        algorithm = str(payload.get("algorithm", defaults.get("algorithm", "DIFFERENTIAL_EVOLUTION"))).upper()
        if algorithm not in ALGORITHMS:
            raise FormalCalibrationTaskError(f"unsupported algorithm: {algorithm}")
        state_strategy = str(payload.get("state_strategy", defaults.get("state_strategy", "OBSERVED_FLOW_ANCHORED"))).upper()
        if state_strategy not in STATE_STRATEGIES:
            raise FormalCalibrationTaskError(f"unsupported state_strategy: {state_strategy}")
        normalized = {
            "module_id": module_id,
            "task_id": task_id,
            "workflow_id": workflow_id,
            "algorithm": algorithm,
            "max_evaluations": self._integer(payload, defaults, "max_evaluations", 1, 10000),
            "population_size": self._integer(payload, defaults, "population_size", 4, 1000),
            "generations": self._integer(payload, defaults, "generations", 1, 10000),
            "differential_weight": self._number(payload, defaults, "differential_weight", 0.000001, 2.0),
            "crossover_probability": self._number(payload, defaults, "crossover_probability", 0.0, 1.0),
            "state_strategy": state_strategy,
            "split_seed": self._integer(payload, defaults, "split_seed", 0, 2147483647),
            "search_seeds": self._integer_list(payload, defaults, "search_seeds", 3, 10, 0, 2147483647),
            "wlm_upper_bounds_mm": self._number_list(payload, defaults, "wlm_upper_bounds_mm", 2, 6, 40.000001, 500.0),
        }
        if algorithm == "DIFFERENTIAL_EVOLUTION" and normalized["max_evaluations"] < 4:
            raise FormalCalibrationTaskError("DIFFERENTIAL_EVOLUTION requires max_evaluations >= 4")
        return normalized

    @staticmethod
    def _integer(payload, defaults, name, minimum, maximum) -> int:
        raw = payload.get(name, defaults.get(name))
        if isinstance(raw, bool):
            raise FormalCalibrationTaskError(f"{name} must be an integer")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise FormalCalibrationTaskError(f"{name} must be an integer") from exc
        if isinstance(raw, float) and not raw.is_integer():
            raise FormalCalibrationTaskError(f"{name} must be an integer")
        if isinstance(raw, str) and str(value) != raw.strip():
            raise FormalCalibrationTaskError(f"{name} must be an integer")
        if not minimum <= value <= maximum:
            raise FormalCalibrationTaskError(f"{name} must be in [{minimum}, {maximum}]")
        return value

    @staticmethod
    def _number(payload, defaults, name, minimum, maximum) -> float:
        try:
            value = float(payload.get(name, defaults.get(name)))
        except (TypeError, ValueError) as exc:
            raise FormalCalibrationTaskError(f"{name} must be a number") from exc
        if not minimum <= value <= maximum:
            raise FormalCalibrationTaskError(f"{name} must be in [{minimum}, {maximum}]")
        return value

    @classmethod
    def _integer_list(cls, payload, defaults, name, min_length, max_length, minimum, maximum):
        values = payload.get(name, defaults.get(name))
        if not isinstance(values, list) or not min_length <= len(values) <= max_length:
            raise FormalCalibrationTaskError(
                f"{name} must be an array with {min_length}-{max_length} values"
            )
        result = []
        for value in values:
            if isinstance(value, bool):
                raise FormalCalibrationTaskError(f"{name} must contain integers")
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise FormalCalibrationTaskError(f"{name} must contain integers") from exc
            if isinstance(value, float) and not value.is_integer():
                raise FormalCalibrationTaskError(f"{name} must contain integers")
            if isinstance(value, str) and str(number) != value.strip():
                raise FormalCalibrationTaskError(f"{name} must contain integers")
            if not minimum <= number <= maximum:
                raise FormalCalibrationTaskError(f"{name} value is outside allowed range")
            result.append(number)
        if len(set(result)) != len(result):
            raise FormalCalibrationTaskError(f"{name} must not contain duplicates")
        return result

    @classmethod
    def _number_list(cls, payload, defaults, name, min_length, max_length, minimum, maximum):
        values = payload.get(name, defaults.get(name))
        if not isinstance(values, list) or not min_length <= len(values) <= max_length:
            raise FormalCalibrationTaskError(
                f"{name} must be an array with {min_length}-{max_length} values"
            )
        try:
            result = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise FormalCalibrationTaskError(f"{name} must contain numbers") from exc
        if any(not minimum <= value <= maximum for value in result):
            raise FormalCalibrationTaskError(f"{name} value is outside allowed range")
        if result != sorted(set(result)):
            raise FormalCalibrationTaskError(f"{name} must be unique and ascending")
        return result

    @staticmethod
    def _new_task_id() -> str:
        return f"PROD-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    def _resolve_request(self, request: dict[str, Any]) -> dict[str, Any]:
        workflow = self._workflows["workflows"][request["workflow_id"]]
        profile_id = str(workflow["base_calibration_profile_id"])
        profiles = self._calibration_profiles.get("profiles", {})
        if profile_id not in profiles:
            raise FormalCalibrationTaskError(f"base calibration profile not found: {profile_id}")
        profile = profiles[profile_id]
        resolved = dict(request)
        resolved["profile_id"] = profile_id
        for name, directory_expected in {
            "data_root": True,
            "precipitation_directory": True,
            "event_catalog": False,
            "data_review_config": False,
            "parameter_space": False,
            "objective_config": False,
            "event_quality_config": False,
            "state_config": False,
            "pet_config": False,
            "initial_land_state": False,
        }.items():
            path = _resolve(str(profile[name]), self.project_root)
            if directory_expected and not path.is_dir():
                raise FormalCalibrationTaskError(f"profile directory does not exist: {path}")
            if not directory_expected and not path.is_file():
                raise FormalCalibrationTaskError(f"profile file does not exist: {path}")
            resolved[name] = str(path)
        continuous = _resolve(str(workflow["continuous_balance_config"]), self.project_root)
        if not continuous.is_file():
            raise FormalCalibrationTaskError(f"continuous balance config not found: {continuous}")
        resolved["continuous_balance_config"] = str(continuous)
        resolved["calibration_ratio"] = float(profile["defaults"].get("calibration_ratio", 0.667))
        resolved["validation_ratio"] = float(profile["defaults"].get("validation_ratio", 0.201))
        resolved["stability_criteria"] = dict(workflow["stability_criteria"])
        resolved["production_criteria"] = dict(workflow["production_criteria"])
        return resolved

    def _default_command(self, request_path: Path, output_dir: Path) -> list[str]:
        script = self.project_root / "scripts" / "run_production_calibration_workflow.py"
        if not script.is_file():
            raise FormalCalibrationTaskError(f"production workflow script not found: {script}")
        command = ([sys.executable, "--xaj-script", str(script)] if getattr(sys, "frozen", False)
                   else [sys.executable, "-u", str(script)])
        return command + [
            "--request",
            str(request_path),
            "--output-dir",
            str(output_dir),
        ]

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = self._normalize(payload)
        resolved = self._resolve_request(request)
        task_id = request["task_id"]
        task_dir = self._task_dir(task_id, must_exist=False)
        with self._lock:
            if task_dir.exists():
                raise FormalCalibrationTaskConflict(f"task_id already exists: {task_id}")
            task_dir.mkdir(parents=True)
            (task_dir / "output").mkdir()
            _write_json(task_dir / "request.json", request)
            _write_json(task_dir / "resolved_request.json", resolved)
            status = {
                "module_id": PRODUCTION_CALIBRATION_MODULE_ID,
                "task_id": task_id,
                "workflow_id": request["workflow_id"],
                "status": "QUEUED",
                "stage": "WAITING_FOR_WORKER",
                "progress_percent": 0.0,
                "submitted_at": _now(),
                "started_at": None,
                "finished_at": None,
                "message": "生产参数验收工作流已进入队列",
            }
            _write_json(task_dir / "status.json", status)
            self._futures[task_id] = self._executor.submit(self._run_task, task_id)
        return status

    def _run_task(self, task_id: str) -> None:
        task_dir = self._task_dir(task_id)
        output_dir = task_dir / "output"
        log_path = task_dir / "task.log"
        acquired = False
        if self._execution_guard is not None:
            self._update_status(task_id, stage="WAITING_FOR_CALIBRATION_SLOT", message="等待其他率定任务完成")
            self._execution_guard.acquire()
            acquired = True
        try:
            with self._lock:
                if task_id in self._cancel_requested:
                    self._update_status(task_id, status="CANCELLED", stage="CANCELLED_BEFORE_START", finished_at=_now())
                    return
            request_path = task_dir / "resolved_request.json"
            self._update_status(
                task_id,
                status="RUNNING",
                stage="PREPARING_WORKFLOW",
                progress_percent=1.0,
                started_at=_now(),
                message="正在生成边界与多种子试验配置",
            )
            command = self._command_builder(request_path, output_dir)
            with self._lock:
                if task_id in self._cancel_requested:
                    self._update_status(
                        task_id,
                        status="CANCELLED",
                        stage="CANCELLED_BEFORE_PROCESS",
                        finished_at=_now(),
                        message="任务在计算进程启动前已取消",
                    )
                    return
            options: dict[str, Any] = {
                "cwd": str(self.project_root),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
                "shell": False,
            }
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                log.write(f"[{_now()}] command={json.dumps(command, ensure_ascii=False)}\n")
                process = subprocess.Popen(command, **options)
                with self._lock:
                    self._processes[task_id] = process
                    cancel_after_start = task_id in self._cancel_requested
                if cancel_after_start and process.poll() is None:
                    self._terminate_process_tree(process)
                assert process.stdout is not None
                try:
                    for line in process.stdout:
                        log.write(line)
                        match = WORKFLOW_PROGRESS_PATTERN.search(line)
                        if match:
                            progress, stage = match.groups()
                            self._update_status(
                                task_id,
                                stage=stage,
                                progress_percent=min(max(float(progress), 0.0), 100.0),
                                message=f"生产参数工作流：{stage}",
                            )
                finally:
                    process.stdout.close()
                return_code = process.wait()
            with self._lock:
                self._processes.pop(task_id, None)
                cancelled = task_id in self._cancel_requested
            if cancelled:
                self._update_status(task_id, status="CANCELLED", stage="CANCELLED", finished_at=_now(), exit_code=return_code)
                return
            if return_code != 0:
                raise RuntimeError(f"production workflow exited with code {return_code}: {self.get_log_tail(task_id, 3000)}")
            report_path = output_dir / "production_workflow_report.json"
            if not report_path.is_file():
                raise RuntimeError("production workflow report is missing")
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
            files = {
                path.name: f"/api/calibration/workflows/tasks/{task_id}/files/{path.name}"
                for path in sorted(output_dir.iterdir())
                if path.is_file()
            }
            result = {
                "module_id": PRODUCTION_CALIBRATION_MODULE_ID,
                "task_id": task_id,
                "workflow_id": report["workflow_id"],
                "status": "SUCCEEDED",
                "workflow_outcome": report["status"],
                "production_approved": report["production_approved"],
                "boundary": report["boundary"],
                "multi_seed_stability": report["multi_seed_stability"],
                "continuous_water_balance": report["continuous_water_balance"],
                "production_gate": report["production_gate"],
                "representative_run": report["representative_run"],
                "output_files": files,
            }
            _write_json(task_dir / "result.json", result)
            self._update_status(
                task_id,
                status="SUCCEEDED",
                stage=report["status"],
                progress_percent=100.0,
                workflow_outcome=report["status"],
                production_approved=report["production_approved"],
                output_files=files,
                exit_code=return_code,
                finished_at=_now(),
                message=(
                    "生产参数已通过全部验收"
                    if report["production_approved"]
                    else "工作流完成，但存在未通过的生产验收项"
                ),
            )
        except Exception as exc:
            with self._lock:
                self._processes.pop(task_id, None)
                cancelled = task_id in self._cancel_requested
            if cancelled:
                self._update_status(task_id, status="CANCELLED", stage="CANCELLED", finished_at=_now())
            else:
                self._update_status(
                    task_id,
                    status="FAILED",
                    stage="FAILED",
                    error=f"{type(exc).__name__}: {exc}",
                    message="生产参数工作流失败，请查看日志",
                    finished_at=_now(),
                )
        finally:
            if acquired:
                self._execution_guard.release()

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            status = self.get_task(task_id)
            if status["status"] in TERMINAL_STATUSES:
                return status
            self._cancel_requested.add(task_id)
            future = self._futures.get(task_id)
            process = self._processes.get(task_id)
            if future is not None and future.cancel():
                return self._update_status(task_id, status="CANCELLED", stage="CANCELLED_IN_QUEUE", finished_at=_now())
            result = self._update_status(task_id, status="CANCELLING", stage="STOPPING_PROCESS", message="正在停止生产参数工作流")
            if process is not None and process.poll() is None:
                self._terminate_process_tree(process)
            return result

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if process.poll() is None:
                process.terminate()
        else:
            process.terminate()

    def _update_status(self, task_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            path = self._task_dir(task_id) / "status.json"
            status = json.loads(path.read_text(encoding="utf-8"))
            status.update(updates)
            _write_json(path, status)
            return status

    def get_task(self, task_id: str) -> dict[str, Any]:
        path = self._task_dir(task_id) / "status.json"
        if not path.is_file():
            raise FormalCalibrationTaskNotFound(f"task not found: {task_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = []
        for directory in self.task_root.iterdir():
            status_path = directory / "status.json"
            if directory.is_dir() and status_path.is_file():
                try:
                    tasks.append(json.loads(status_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(tasks, key=lambda item: item.get("submitted_at", ""), reverse=True)

    def get_result(self, task_id: str) -> dict[str, Any]:
        status = self.get_task(task_id)
        if status["status"] != "SUCCEEDED":
            raise FormalCalibrationTaskConflict(f"workflow result is unavailable while status is {status['status']}")
        return json.loads((self._task_dir(task_id) / "result.json").read_text(encoding="utf-8"))

    def get_log_tail(self, task_id: str, maximum_characters: int = 20000) -> str:
        self.get_task(task_id)
        if not 1 <= maximum_characters <= 100000:
            raise FormalCalibrationTaskError("maximum_characters must be in [1, 100000]")
        path = self._task_dir(task_id) / "task.log"
        return path.read_text(encoding="utf-8", errors="replace")[-maximum_characters:] if path.is_file() else ""

    def resolve_output_file(self, task_id: str, filename: str) -> Path:
        self.get_task(task_id)
        if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise FormalCalibrationTaskError("invalid output filename")
        output = (self._task_dir(task_id) / "output").resolve()
        candidate = (output / filename).resolve()
        try:
            candidate.relative_to(output)
        except ValueError as exc:
            raise FormalCalibrationTaskError("output path escapes task directory") from exc
        if not candidate.is_file():
            raise FormalCalibrationTaskNotFound(f"output file not found: {filename}")
        return candidate

    def _task_dir(self, task_id: str, must_exist: bool = True) -> Path:
        if not TASK_ID_PATTERN.fullmatch(str(task_id)):
            raise FormalCalibrationTaskError("invalid task_id")
        candidate = (self.task_root / str(task_id)).resolve()
        try:
            candidate.relative_to(self.task_root.resolve())
        except ValueError as exc:
            raise FormalCalibrationTaskError("task path escapes managed root") from exc
        if must_exist and not candidate.is_dir():
            raise FormalCalibrationTaskNotFound(f"task not found: {task_id}")
        return candidate

    def _recover_interrupted(self) -> None:
        for directory in self.task_root.iterdir():
            path = directory / "status.json"
            if not directory.is_dir() or not path.is_file():
                continue
            try:
                status = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("status") in ACTIVE_STATUSES:
                status.update(
                    {
                        "status": "INTERRUPTED",
                        "stage": "SERVER_RESTARTED",
                        "finished_at": _now(),
                        "message": "平台服务重启，生产工作流已中断；请重新提交",
                    }
                )
                _write_json(path, status)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)


__all__ = ["PRODUCTION_CALIBRATION_MODULE_ID", "ProductionCalibrationTaskManager"]
