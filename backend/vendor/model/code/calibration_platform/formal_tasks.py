"""CAL-ASYNC-06: persistent asynchronous wrapper for formal calibration.

The hydrological equations remain in ``run_formal_multievent_calibration.py``.
This module owns only platform concerns: request validation, one-at-a-time job
execution, progress persistence, cancellation and safe result-file access.
"""

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


FORMAL_CALIBRATION_MODULE_ID = "MULTI-EVENT-CALIBRATION-06"
ACTIVE_STATUSES = {"QUEUED", "RUNNING", "CANCELLING"}
TERMINAL_STATUSES = {"CANCELLED", "SUCCEEDED", "FAILED", "INTERRUPTED"}
ALGORITHMS = {"DIFFERENTIAL_EVOLUTION", "RANDOM_SEARCH", "GRID_SEARCH"}
STATE_STRATEGIES = {"FIXED_WARMUP", "OBSERVED_FLOW_ANCHORED", "CONTINUOUS"}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
EVALUATION_PATTERN = re.compile(r"\bevaluation=(\d+)\b")
FINAL_EVALUATION_PATTERN = re.compile(r"\bfinal evaluation (\d+)/(\d+)\b")


class FormalCalibrationTaskError(ValueError):
    """Base class for invalid task requests and state transitions."""


class FormalCalibrationTaskNotFound(FormalCalibrationTaskError):
    """Raised when a task id does not exist below the managed task root."""


class FormalCalibrationTaskConflict(FormalCalibrationTaskError):
    """Raised when a task id already exists or a transition is invalid."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _resolve_config_path(value: str, project_root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def build_formal_calibration_command(
    project_root: str | Path,
    request: dict[str, Any],
    output_dir: str | Path,
) -> list[str]:
    """Build the one authoritative shell-free formal calibration command."""
    root = Path(project_root).resolve()
    script = root / "scripts" / "run_formal_multievent_calibration.py"
    if not script.is_file():
        raise FormalCalibrationTaskError(f"formal calibration script not found: {script}")
    return [
        sys.executable,
        "-u",
        str(script),
        "--data-root",
        request["data_root"],
        "--event-catalog",
        request["event_catalog"],
        "--output-dir",
        str(Path(output_dir).resolve()),
        "--algorithm",
        request["algorithm"],
        "--max-evaluations",
        str(request["max_evaluations"]),
        "--population-size",
        str(request["population_size"]),
        "--generations",
        str(request["generations"]),
        "--differential-weight",
        str(request["differential_weight"]),
        "--crossover-probability",
        str(request["crossover_probability"]),
        "--objective-config",
        request["objective_config"],
        "--parameter-space",
        request["parameter_space"],
        "--base-parameter-set",
        request["base_parameter_set"],
        "--data-review-config",
        request["data_review_config"],
        "--qualification-config",
        request["qualification_config"],
        "--precip-dir",
        request["precipitation_directory"],
        "--event-quality-config",
        request["event_quality_config"],
        "--state-config",
        request["state_config"],
        "--pet-config",
        request["pet_config"],
        "--initial-land-state",
        request["initial_land_state"],
        "--state-strategy",
        request["state_strategy"],
        "--split-seed",
        str(request["split_seed"]),
        "--calibration-ratio",
        str(request["calibration_ratio"]),
        "--validation-ratio",
        str(request["validation_ratio"]),
        "--seed",
        str(request["search_seed"]),
    ]


class FormalCalibrationTaskManager:
    """Run formal multi-event calibration as persistent background tasks."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        task_root: str | Path | None = None,
        profiles_path: str | Path | None = None,
        command_builder: Callable[[dict[str, Any], Path], list[str]] | None = None,
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
            else self.project_root / "validation" / "platform_calibration_tasks"
        )
        self.profiles_path = (
            Path(profiles_path).resolve()
            if profiles_path is not None
            else self.project_root / "config" / "platform_calibration_profiles.json"
        )
        self.task_root.mkdir(parents=True, exist_ok=True)
        self._profiles_payload = self._load_profiles()
        self._command_builder = command_builder or self._build_formal_command
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="formal-calibration",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancel_requested: set[str] = set()
        self._execution_guard = execution_guard
        self._recover_interrupted_tasks()

    # [CAL-PROFILE-06A] Profiles keep filesystem paths and model configs out of browsers.
    def _load_profiles(self) -> dict[str, Any]:
        payload = json.loads(self.profiles_path.read_text(encoding="utf-8-sig"))
        profiles = payload.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            raise FormalCalibrationTaskError("calibration profile file has no profiles")
        return payload

    def catalog(self) -> dict[str, Any]:
        profiles = []
        for profile_id, profile in self._profiles_payload["profiles"].items():
            profiles.append(
                {
                    "profile_id": profile_id,
                    "name_zh": profile.get("name_zh", profile_id),
                    "description": profile.get("description", ""),
                    "module_id": profile.get("module_id", FORMAL_CALIBRATION_MODULE_ID),
                    "calibration_scope": profile.get("calibration_scope"),
                    "defaults": profile.get("defaults", {}),
                }
            )
        return {
            "module_id": FORMAL_CALIBRATION_MODULE_ID,
            "status": "READY_ASYNC",
            "algorithms": sorted(ALGORITHMS),
            "state_strategies": sorted(STATE_STRATEGIES),
            "task_statuses": sorted(ACTIVE_STATUSES | TERMINAL_STATUSES),
            "profiles": profiles,
        }

    # [CAL-CONTRACT-06B] Validate a small whitelist; no arbitrary CLI or output path is accepted.
    def _normalize_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise FormalCalibrationTaskError("request body must be a JSON object")
        allowed = {
            "module_id",
            "task_id",
            "profile_id",
            "algorithm",
            "max_evaluations",
            "population_size",
            "generations",
            "differential_weight",
            "crossover_probability",
            "state_strategy",
            "split_seed",
            "search_seed",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise FormalCalibrationTaskError(f"unsupported task fields: {unknown}")
        module_id = str(payload.get("module_id", FORMAL_CALIBRATION_MODULE_ID)).upper()
        if module_id != FORMAL_CALIBRATION_MODULE_ID:
            raise FormalCalibrationTaskError(f"unsupported module_id: {module_id}")
        profile_id = str(payload.get("profile_id", ""))
        profiles = self._profiles_payload["profiles"]
        if profile_id not in profiles:
            raise FormalCalibrationTaskError(f"unknown calibration profile: {profile_id}")
        defaults = profiles[profile_id].get("defaults", {})
        task_id = str(payload.get("task_id") or self._new_task_id())
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise FormalCalibrationTaskError(
                "task_id must be 3-64 characters using letters, numbers, dot, underscore or hyphen"
            )
        algorithm = str(payload.get("algorithm", defaults.get("algorithm", "DIFFERENTIAL_EVOLUTION"))).upper()
        if algorithm not in ALGORITHMS:
            raise FormalCalibrationTaskError(f"unsupported algorithm: {algorithm}")

        normalized: dict[str, Any] = {
            "module_id": module_id,
            "task_id": task_id,
            "profile_id": profile_id,
            "algorithm": algorithm,
            "max_evaluations": self._integer(payload, defaults, "max_evaluations", 1, 10000),
            "population_size": self._integer(payload, defaults, "population_size", 4, 1000),
            "generations": self._integer(payload, defaults, "generations", 1, 10000),
            "differential_weight": self._number(payload, defaults, "differential_weight", 0.000001, 2.0),
            "crossover_probability": self._number(payload, defaults, "crossover_probability", 0.0, 1.0),
            "state_strategy": str(payload.get("state_strategy", defaults.get("state_strategy", "FIXED_WARMUP"))).upper(),
            "split_seed": self._integer(payload, defaults, "split_seed", 0, 2147483647),
            "search_seed": self._integer(payload, defaults, "search_seed", 0, 2147483647),
        }
        if normalized["state_strategy"] not in STATE_STRATEGIES:
            raise FormalCalibrationTaskError(
                f"unsupported state_strategy: {normalized['state_strategy']}"
            )
        if algorithm == "DIFFERENTIAL_EVOLUTION" and normalized["max_evaluations"] < 4:
            raise FormalCalibrationTaskError(
                "DIFFERENTIAL_EVOLUTION requires max_evaluations >= 4"
            )
        return normalized

    @staticmethod
    def _integer(
        payload: dict[str, Any], defaults: dict[str, Any], name: str, minimum: int, maximum: int
    ) -> int:
        value = payload.get(name, defaults.get(name))
        if isinstance(value, bool):
            raise FormalCalibrationTaskError(f"{name} must be an integer")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise FormalCalibrationTaskError(f"{name} must be an integer") from exc
        if value is not None and isinstance(value, float) and not value.is_integer():
            raise FormalCalibrationTaskError(f"{name} must be an integer")
        if not minimum <= number <= maximum:
            raise FormalCalibrationTaskError(f"{name} must be in [{minimum}, {maximum}]")
        return number

    @staticmethod
    def _number(
        payload: dict[str, Any], defaults: dict[str, Any], name: str, minimum: float, maximum: float
    ) -> float:
        try:
            number = float(payload.get(name, defaults.get(name)))
        except (TypeError, ValueError) as exc:
            raise FormalCalibrationTaskError(f"{name} must be a number") from exc
        if not minimum <= number <= maximum:
            raise FormalCalibrationTaskError(f"{name} must be in [{minimum}, {maximum}]")
        return number

    @staticmethod
    def _new_task_id() -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"CAL-{stamp}-{uuid.uuid4().hex[:8]}"

    def _resolve_request(self, request: dict[str, Any]) -> dict[str, Any]:
        profile = self._profiles_payload["profiles"][request["profile_id"]]
        required_paths = {
            "data_root": True,
            "precipitation_directory": True,
            "event_catalog": False,
            "parameter_space": False,
            "objective_config": False,
            "event_quality_config": False,
            "state_config": False,
            "pet_config": False,
            "initial_land_state": False,
        }
        resolved = dict(request)
        for name, directory_expected in required_paths.items():
            if name not in profile:
                raise FormalCalibrationTaskError(
                    f"profile {request['profile_id']} is missing {name}"
                )
            path = _resolve_config_path(str(profile[name]), self.project_root)
            if directory_expected and not path.is_dir():
                raise FormalCalibrationTaskError(f"profile directory does not exist: {path}")
            if not directory_expected and not path.is_file():
                raise FormalCalibrationTaskError(f"profile file does not exist: {path}")
            resolved[name] = str(path)
        optional_paths = {
            "base_parameter_set": "config/parameter_set_land.json",
            "data_review_config": "config/control_section_data_review_xufan.json",
            "qualification_config": "config/qualification_config_event.json",
        }
        for name, default_value in optional_paths.items():
            value = profile.get(name, default_value)
            resolved[name] = str(_resolve_config_path(str(value), self.project_root))
        resolved["calibration_ratio"] = float(profile["defaults"].get("calibration_ratio", 0.667))
        resolved["validation_ratio"] = float(profile["defaults"].get("validation_ratio", 0.201))
        resolved["calibration_scope"] = profile.get("calibration_scope")
        return resolved

    # [CAL-COMMAND-06C] List arguments plus shell=False prevent command injection.
    def _build_formal_command(self, request: dict[str, Any], output_dir: Path) -> list[str]:
        return build_formal_calibration_command(self.project_root, request, output_dir)

    # [CAL-LIFECYCLE-06D] Every state transition is persisted below its task directory.
    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = self._normalize_request(payload)
        resolved = self._resolve_request(request)
        task_id = request["task_id"]
        task_dir = self._task_dir(task_id, must_exist=False)
        with self._lock:
            if task_dir.exists():
                raise FormalCalibrationTaskConflict(f"task_id already exists: {task_id}")
            task_dir.mkdir(parents=True)
            (task_dir / "output").mkdir()
            _atomic_write_json(task_dir / "request.json", request)
            _atomic_write_json(task_dir / "resolved_request.json", resolved)
            status = {
                "module_id": FORMAL_CALIBRATION_MODULE_ID,
                "task_id": task_id,
                "profile_id": request["profile_id"],
                "status": "QUEUED",
                "stage": "WAITING_FOR_WORKER",
                "progress_percent": 0.0,
                "evaluation_completed": 0,
                "evaluation_total": request["max_evaluations"],
                "final_evaluation_completed": 0,
                "final_evaluation_total": None,
                "submitted_at": _utc_now(),
                "started_at": None,
                "finished_at": None,
                "message": "任务已进入率定队列",
            }
            _atomic_write_json(task_dir / "status.json", status)
            future = self._executor.submit(self._run_task, task_id)
            self._futures[task_id] = future
        return status

    def _run_task(self, task_id: str) -> None:
        task_dir = self._task_dir(task_id)
        output_dir = task_dir / "output"
        log_path = task_dir / "task.log"
        guard_acquired = False
        if self._execution_guard is not None:
            self._update_status(
                task_id,
                stage="WAITING_FOR_CALIBRATION_SLOT",
                message="等待其他率定任务完成",
            )
            self._execution_guard.acquire()
            guard_acquired = True
        try:
            with self._lock:
                if task_id in self._cancel_requested:
                    self._update_status(
                        task_id,
                        status="CANCELLED",
                        stage="CANCELLED_BEFORE_START",
                        finished_at=_utc_now(),
                        message="任务在启动前已取消",
                    )
                    return
            request = json.loads(
                (task_dir / "resolved_request.json").read_text(encoding="utf-8")
            )
            self._update_status(
                task_id,
                status="RUNNING",
                stage="PREPARING_INPUTS",
                progress_percent=1.0,
                started_at=_utc_now(),
                message="正在读取洪水场次与气象资料",
            )
            command = self._command_builder(request, output_dir)
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                log.write(f"[{_utc_now()}] command={json.dumps(command, ensure_ascii=False)}\n")
                with self._lock:
                    if task_id in self._cancel_requested:
                        self._update_status(
                            task_id,
                            status="CANCELLED",
                            stage="CANCELLED_BEFORE_PROCESS",
                            finished_at=_utc_now(),
                            message="任务在计算进程启动前已取消",
                        )
                        return
                popen_options: dict[str, Any] = {
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
                    popen_options["creationflags"] = subprocess.CREATE_NO_WINDOW
                process = subprocess.Popen(command, **popen_options)
                with self._lock:
                    self._processes[task_id] = process
                    cancel_after_start = task_id in self._cancel_requested
                if cancel_after_start and process.poll() is None:
                    process.terminate()
                assert process.stdout is not None
                try:
                    for line in process.stdout:
                        log.write(line)
                        self._record_progress(task_id, line, request["max_evaluations"])
                finally:
                    process.stdout.close()
                return_code = process.wait()
            with self._lock:
                self._processes.pop(task_id, None)
                cancelled = task_id in self._cancel_requested
            if cancelled:
                self._update_status(
                    task_id,
                    status="CANCELLED",
                    stage="CANCELLED",
                    finished_at=_utc_now(),
                    exit_code=return_code,
                    message="率定任务已取消",
                )
                return
            if return_code != 0:
                raise RuntimeError(
                    f"formal calibration process exited with code {return_code}: "
                    f"{self._log_tail(log_path)}"
                )
            result = self._build_result(task_id, request, output_dir)
            _atomic_write_json(task_dir / "result.json", result)
            self._update_status(
                task_id,
                status="SUCCEEDED",
                stage="COMPLETED",
                progress_percent=100.0,
                finished_at=_utc_now(),
                exit_code=return_code,
                output_files=result["output_files"],
                message="正式多场洪水率定与三组评价完成",
            )
        except Exception as exc:
            with self._lock:
                self._processes.pop(task_id, None)
                cancelled = task_id in self._cancel_requested
            if cancelled:
                self._update_status(
                    task_id,
                    status="CANCELLED",
                    stage="CANCELLED",
                    finished_at=_utc_now(),
                    message="率定任务已取消",
                )
            else:
                self._update_status(
                    task_id,
                    status="FAILED",
                    stage="FAILED",
                    finished_at=_utc_now(),
                    error=f"{type(exc).__name__}: {exc}",
                    message="率定任务失败，请查看任务日志",
                )
        finally:
            if guard_acquired:
                self._execution_guard.release()

    def _record_progress(self, task_id: str, line: str, evaluation_total: int) -> None:
        final_match = FINAL_EVALUATION_PATTERN.search(line)
        if final_match:
            completed, total = (int(value) for value in final_match.groups())
            progress = 80.0 + 20.0 * min(completed / max(total, 1), 1.0)
            self._update_status(
                task_id,
                stage="EVALUATING_ALL_EVENTS",
                progress_percent=round(progress, 2),
                final_evaluation_completed=completed,
                final_evaluation_total=total,
                message=f"正在用冻结参数评价全部场次：{completed}/{total}",
            )
            return
        evaluation_match = EVALUATION_PATTERN.search(line)
        if evaluation_match:
            completed = int(evaluation_match.group(1))
            progress = 5.0 + 75.0 * min(completed / max(evaluation_total, 1), 1.0)
            self._update_status(
                task_id,
                stage="SEARCHING_PARAMETERS",
                progress_percent=round(progress, 2),
                evaluation_completed=completed,
                message=f"正在搜索统一参数：{completed}/{evaluation_total}",
            )

    def _build_result(
        self, task_id: str, request: dict[str, Any], output_dir: Path
    ) -> dict[str, Any]:
        report_path = output_dir / "formal_multi_event_report.json"
        if not report_path.is_file():
            raise RuntimeError("formal calibration completed without formal_multi_event_report.json")
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        output_files = {
            path.name: f"/api/calibration/tasks/{task_id}/files/{path.name}"
            for path in sorted(output_dir.iterdir())
            if path.is_file()
        }
        return {
            "module_id": FORMAL_CALIBRATION_MODULE_ID,
            "task_id": task_id,
            "profile_id": request["profile_id"],
            "status": "SUCCEEDED",
            "calibration_scope": report.get("calibration_scope"),
            "algorithm": report.get("algorithm"),
            "common_parameter_set": report.get("common_parameter_set", {}),
            "resolved_runoff_parameter_set": report.get("resolved_runoff_parameter_set", {}),
            "groups": report.get("groups", {}),
            "split": report.get("split", {}),
            "objective": report.get("objective", {}),
            "warnings": [report["plot_warning"]] if report.get("plot_warning") else [],
            "output_files": output_files,
        }

    def _update_status(self, task_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            path = self._task_dir(task_id) / "status.json"
            status = json.loads(path.read_text(encoding="utf-8"))
            status.update(updates)
            _atomic_write_json(path, status)
            return status

    # [CAL-CANCEL-06E] Queued futures and live subprocesses use one cancellation contract.
    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            status = self.get_task(task_id)
            if status["status"] in TERMINAL_STATUSES:
                return status
            self._cancel_requested.add(task_id)
            future = self._futures.get(task_id)
            process = self._processes.get(task_id)
            if future is not None and future.cancel():
                return self._update_status(
                    task_id,
                    status="CANCELLED",
                    stage="CANCELLED_IN_QUEUE",
                    finished_at=_utc_now(),
                    message="排队中的率定任务已取消",
                )
            status = self._update_status(
                task_id,
                status="CANCELLING",
                stage="STOPPING_PROCESS",
                message="正在停止率定进程",
            )
            if process is not None and process.poll() is None:
                process.terminate()
            return status

    def get_task(self, task_id: str) -> dict[str, Any]:
        path = self._task_dir(task_id) / "status.json"
        if not path.is_file():
            raise FormalCalibrationTaskNotFound(f"task not found: {task_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = []
        for path in self.task_root.iterdir():
            status_path = path / "status.json"
            if path.is_dir() and status_path.is_file():
                try:
                    tasks.append(json.loads(status_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(tasks, key=lambda item: item.get("submitted_at", ""), reverse=True)

    def get_result(self, task_id: str) -> dict[str, Any]:
        status = self.get_task(task_id)
        if status["status"] != "SUCCEEDED":
            raise FormalCalibrationTaskConflict(
                f"task result is not available while status is {status['status']}"
            )
        path = self._task_dir(task_id) / "result.json"
        if not path.is_file():
            raise FormalCalibrationTaskConflict("task succeeded but result.json is missing")
        return json.loads(path.read_text(encoding="utf-8"))

    def get_log_tail(self, task_id: str, maximum_characters: int = 20000) -> str:
        """Return a bounded log tail without exposing an arbitrary filesystem path."""
        self.get_task(task_id)
        if maximum_characters < 1 or maximum_characters > 100000:
            raise FormalCalibrationTaskError("maximum_characters must be in [1, 100000]")
        path = self._task_dir(task_id) / "task.log"
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-maximum_characters:]

    # [CAL-FILES-06F] Only flat files physically contained by this task's output are served.
    def resolve_output_file(self, task_id: str, filename: str) -> Path:
        self.get_task(task_id)
        if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise FormalCalibrationTaskError("invalid output filename")
        output_dir = (self._task_dir(task_id) / "output").resolve()
        candidate = (output_dir / filename).resolve()
        try:
            candidate.relative_to(output_dir)
        except ValueError as exc:
            raise FormalCalibrationTaskError("output path escapes the task directory") from exc
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
            raise FormalCalibrationTaskError("task path escapes the managed root") from exc
        if must_exist and not candidate.is_dir():
            raise FormalCalibrationTaskNotFound(f"task not found: {task_id}")
        return candidate

    def _recover_interrupted_tasks(self) -> None:
        for path in self.task_root.iterdir():
            status_path = path / "status.json"
            if not path.is_dir() or not status_path.is_file():
                continue
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("status") in ACTIVE_STATUSES:
                status.update(
                    {
                        "status": "INTERRUPTED",
                        "stage": "SERVER_RESTARTED",
                        "finished_at": _utc_now(),
                        "message": "平台服务重启，原后台进程状态无法恢复；请重新提交任务",
                    }
                )
                _atomic_write_json(status_path, status)

    @staticmethod
    def _log_tail(path: Path, maximum_characters: int = 3000) -> str:
        if not path.is_file():
            return "task log is missing"
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-maximum_characters:].strip()

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)


__all__ = [
    "FORMAL_CALIBRATION_MODULE_ID",
    "FormalCalibrationTaskConflict",
    "FormalCalibrationTaskError",
    "FormalCalibrationTaskManager",
    "FormalCalibrationTaskNotFound",
    "build_formal_calibration_command",
]
