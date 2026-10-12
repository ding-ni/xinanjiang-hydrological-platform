"""Start the local runoff platform frontend and asynchronous calibration API."""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from calibration_platform.formal_tasks import FormalCalibrationTaskManager  # noqa: E402
from calibration_platform.http_adapter import create_platform_server  # noqa: E402
from calibration_platform.production_tasks import ProductionCalibrationTaskManager  # noqa: E402
from integration.web_api import IntegratedResultsService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--task-root", default=str(ROOT / "validation" / "platform_calibration_tasks"))
    parser.add_argument(
        "--workflow-task-root",
        default=str(ROOT / "validation" / "platform_production_workflows"),
    )
    parser.add_argument("--profiles", default=str(ROOT / "config" / "platform_calibration_profiles.json"))
    parser.add_argument(
        "--workflows",
        default=str(ROOT / "config" / "production_calibration_workflows.json"),
    )
    parser.add_argument("--frontend", default=str(ROOT / "frontend"))
    args = parser.parse_args()

    execution_guard = threading.Lock()
    manager = FormalCalibrationTaskManager(
        project_root=ROOT,
        task_root=args.task_root,
        profiles_path=args.profiles,
        execution_guard=execution_guard,
    )
    production_manager = ProductionCalibrationTaskManager(
        project_root=ROOT,
        task_root=args.workflow_task_root,
        workflows_path=args.workflows,
        calibration_profiles_path=args.profiles,
        execution_guard=execution_guard,
    )
    integrated_service = IntegratedResultsService(ROOT)
    server = create_platform_server(
        args.host,
        args.port,
        manager,
        args.frontend,
        production_manager=production_manager,
        integrated_service=integrated_service,
    )
    print(f"Runoff platform: http://{args.host}:{server.server_port}", flush=True)
    print("Press Ctrl+C to stop the server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping platform server...", flush=True)
    finally:
        server.server_close()
        for task in manager.list_tasks():
            if task.get("status") in {"QUEUED", "RUNNING", "CANCELLING"}:
                manager.cancel(task["task_id"])
        for task in production_manager.list_tasks():
            if task.get("status") in {"QUEUED", "RUNNING", "CANCELLING"}:
                production_manager.cancel(task["task_id"])
        manager.shutdown(wait=True)
        production_manager.shutdown(wait=True)
        integrated_service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
