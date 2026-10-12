"""CAL-HTTP-06/10: HTTP adapter for formal and production calibration tasks."""

from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .formal_tasks import (
    FormalCalibrationTaskConflict,
    FormalCalibrationTaskError,
    FormalCalibrationTaskManager,
    FormalCalibrationTaskNotFound,
)
from .production_tasks import ProductionCalibrationTaskManager
from integration.web_api import IntegratedResultsService


MAX_REQUEST_BYTES = 1024 * 1024
TASK_ROUTE = re.compile(r"^/api/calibration/tasks/([^/]+)$")
TASK_CANCEL_ROUTE = re.compile(r"^/api/calibration/tasks/([^/]+)/cancel$")
TASK_RESULT_ROUTE = re.compile(r"^/api/calibration/tasks/([^/]+)/results$")
TASK_LOG_ROUTE = re.compile(r"^/api/calibration/tasks/([^/]+)/log$")
TASK_FILE_ROUTE = re.compile(r"^/api/calibration/tasks/([^/]+)/files/([^/]+)$")
WORKFLOW_TASK_ROUTE = re.compile(r"^/api/calibration/workflows/tasks/([^/]+)$")
WORKFLOW_CANCEL_ROUTE = re.compile(
    r"^/api/calibration/workflows/tasks/([^/]+)/cancel$"
)
WORKFLOW_RESULT_ROUTE = re.compile(
    r"^/api/calibration/workflows/tasks/([^/]+)/results$"
)
WORKFLOW_LOG_ROUTE = re.compile(r"^/api/calibration/workflows/tasks/([^/]+)/log$")
WORKFLOW_FILE_ROUTE = re.compile(
    r"^/api/calibration/workflows/tasks/([^/]+)/files/([^/]+)$"
)


def _handler_class(
    manager: FormalCalibrationTaskManager,
    frontend_root: Path,
    production_manager: ProductionCalibrationTaskManager | None,
    integrated_service: IntegratedResultsService | None,
):
    class PlatformRequestHandler(BaseHTTPRequestHandler):
        server_version = "RunoffCalibrationPlatform/1.0"

        # [CAL-HTTP-06A] Stable JSON routes used by frontend and platform backend.
        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if path.startswith("/api/v1/"):
                    self._handle_integrated_get()
                    return
                if path == "/api/calibration/catalog":
                    self._send_json(200, manager.catalog())
                    return
                if path == "/api/calibration/tasks":
                    self._send_json(200, {"tasks": manager.list_tasks()})
                    return
                if path == "/api/calibration/workflows/catalog":
                    self._require_production_manager()
                    self._send_json(200, production_manager.catalog())
                    return
                if path == "/api/calibration/workflows/tasks":
                    self._require_production_manager()
                    self._send_json(200, {"tasks": production_manager.list_tasks()})
                    return
                match = WORKFLOW_RESULT_ROUTE.fullmatch(path)
                if match:
                    self._require_production_manager()
                    self._send_json(
                        200,
                        production_manager.get_result(unquote(match.group(1))),
                    )
                    return
                match = WORKFLOW_LOG_ROUTE.fullmatch(path)
                if match:
                    self._require_production_manager()
                    task_id = unquote(match.group(1))
                    self._send_json(
                        200,
                        {
                            "task_id": task_id,
                            "tail": production_manager.get_log_tail(task_id),
                        },
                    )
                    return
                match = WORKFLOW_FILE_ROUTE.fullmatch(path)
                if match:
                    self._require_production_manager()
                    self._send_file(
                        production_manager.resolve_output_file(
                            unquote(match.group(1)), unquote(match.group(2))
                        ),
                        attachment=True,
                    )
                    return
                match = WORKFLOW_TASK_ROUTE.fullmatch(path)
                if match:
                    self._require_production_manager()
                    self._send_json(
                        200,
                        production_manager.get_task(unquote(match.group(1))),
                    )
                    return
                match = TASK_RESULT_ROUTE.fullmatch(path)
                if match:
                    self._send_json(200, manager.get_result(unquote(match.group(1))))
                    return
                match = TASK_LOG_ROUTE.fullmatch(path)
                if match:
                    task_id = unquote(match.group(1))
                    self._send_json(
                        200,
                        {"task_id": task_id, "tail": manager.get_log_tail(task_id)},
                    )
                    return
                match = TASK_FILE_ROUTE.fullmatch(path)
                if match:
                    self._send_file(
                        manager.resolve_output_file(
                            unquote(match.group(1)), unquote(match.group(2))
                        ),
                        attachment=True,
                    )
                    return
                match = TASK_ROUTE.fullmatch(path)
                if match:
                    self._send_json(200, manager.get_task(unquote(match.group(1))))
                    return
                if path.startswith("/api/"):
                    self._send_json(404, {"error": "API route not found"})
                    return
                self._serve_frontend(path)
            except Exception as exc:
                self._send_exception(exc)

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if path.startswith("/api/v1/"):
                    self._handle_integrated_post()
                    return
                if path == "/api/calibration/tasks":
                    self._send_json(202, manager.submit(self._read_json()))
                    return
                if path == "/api/calibration/workflows/tasks":
                    self._require_production_manager()
                    self._send_json(202, production_manager.submit(self._read_json()))
                    return
                match = WORKFLOW_CANCEL_ROUTE.fullmatch(path)
                if match:
                    self._require_production_manager()
                    self._send_json(
                        202,
                        production_manager.cancel(unquote(match.group(1))),
                    )
                    return
                match = TASK_CANCEL_ROUTE.fullmatch(path)
                if match:
                    self._send_json(202, manager.cancel(unquote(match.group(1))))
                    return
                self._send_json(404, {"error": "API route not found"})
            except Exception as exc:
                self._send_exception(exc)

        def do_PUT(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if path.startswith("/api/v1/"):
                    self._handle_integrated_put()
                    return
                self._send_json(404, {"error": "API route not found"})
            except Exception as exc:
                self._send_exception(exc)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Allow", "GET, POST, PUT, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _read_json(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise FormalCalibrationTaskError("Content-Length is required")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise FormalCalibrationTaskError("invalid Content-Length") from exc
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise FormalCalibrationTaskError("request body is too large")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FormalCalibrationTaskError("request body must be valid UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise FormalCalibrationTaskError("request body must be a JSON object")
            return payload

        @staticmethod
        def _require_production_manager() -> None:
            if production_manager is None:
                raise FormalCalibrationTaskError(
                    "production calibration workflow service is not configured"
                )

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _require_integrated_service(self) -> IntegratedResultsService:
            if integrated_service is None:
                raise FormalCalibrationTaskError("integrated runoff-routing service is not configured")
            return integrated_service

        def _handle_integrated_get(self) -> None:
            service = self._require_integrated_service()
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/v1/overview":
                self._send_json(200, service.summary())
                return
            if path == "/api/v1/data-source":
                self._send_json(200, service.data_source_status())
                return
            if path == "/api/v1/monitor/stations":
                self._send_json(200, service.monitor())
                return
            if path == "/api/v1/topology":
                self._send_json(200, service.topology())
                return
            if path == "/api/v1/topology/dem":
                self._send_json(200, service.dem_preview())
                return
            if path == "/api/v1/point-flows/context":
                self._send_json(200, service.point_query_context())
                return
            if path == "/api/v1/events":
                self._send_json(200, service.event_catalog())
                return
            if path == "/api/v1/parameters":
                self._send_json(200, service.parameters(query.get("zone_type", ["NON_GLACIER"])[0]))
                return
            if path == "/api/v1/grid-geometry":
                self._send_json(200, service.grid_geometry())
                return
            match = re.fullmatch(r"/api/v1/floods/([^/]+)/grid-rainfall", path)
            if match:
                self._send_json(200, service.flood_grid_rainfall(
                    unquote(match.group(1)), query.get("time", [""])[0]))
                return
            match = re.fullmatch(r"/api/v1/floods/([^/]+)/areal-rainfall", path)
            if match:
                self._send_json(
                    200,
                    service.flood_areal_rainfall(
                        unquote(match.group(1)),
                        timestamp=query.get("time", [None])[0],
                        start=query.get("start", [None])[0],
                        end=query.get("end", [None])[0],
                    ),
                )
                return
            match = re.fullmatch(r"/api/v1/events/([^/]+)/series", path)
            if match:
                self._send_json(
                    200,
                    service.event_series(
                        unquote(match.group(1)),
                        query.get("section_id", ["xufan_70115800"])[0],
                    ),
                )
                return
            match = re.fullmatch(r"/api/v1/events/([^/]+)", path)
            if match:
                self._send_json(200, service.event_detail(unquote(match.group(1))))
                return
            if path == "/api/v1/archive/runs":
                self._send_json(200, service.list_runs())
                return
            match = re.fullmatch(r"/api/v1/runs/([^/]+)/series", path)
            if match:
                self._send_json(
                    200,
                    service.series(
                        unquote(match.group(1)),
                        query.get("section_id", ["xufan_70115800"])[0],
                    ),
                )
                return
            match = re.fullmatch(r"/api/v1/runs/([^/]+)/diagnostics", path)
            if match:
                self._send_json(200, service.diagnostics(unquote(match.group(1))))
                return
            match = re.fullmatch(r"/api/v1/runs/([^/]+)/grid-state", path)
            if match:
                self._send_json(
                    200,
                    service.grid_state(
                        unquote(match.group(1)),
                        unit_id=query.get("unit_id", [None])[0],
                        timestamp=query.get("time", [None])[0],
                        start=query.get("start", [None])[0],
                        end=query.get("end", [None])[0],
                    ),
                )
                return
            match = re.fullmatch(r"/api/v1/runs/([^/]+)/grid-state/units", path)
            if match:
                self._send_json(200, service.grid_units(unquote(match.group(1))))
                return
            match = re.fullmatch(r"/api/v1/runs/([^/]+)", path)
            if match:
                self._send_json(200, service.task(unquote(match.group(1))))
                return
            match = re.fullmatch(r"/api/v1/events/runs/([^/]+)", path)
            if match:
                events = service.events(unquote(match.group(1)))
                body = "".join(
                    f"id: {event['event_id']}\n"
                    f"event: {event['event_type']}\n"
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    for event in events
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_json(404, {"error": "integrated API route not found"})

        def _handle_integrated_post(self) -> None:
            service = self._require_integrated_service()
            path = urlsplit(self.path).path
            if path == "/api/v1/runs/manual":
                self._send_json(202, service.submit_manual_run(self._read_json()))
                return
            if path == "/api/v1/data-source":
                filename = self.headers.get("X-File-Name", "uploaded_data")
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise ValueError("Content-Length is required for data upload")
                self._send_json(201, service.register_data_source(filename, self.rfile, int(raw_length)))
                return
            if path == "/api/v1/runs":
                self._send_json(202, service.submit(self._read_json()))
                return
            if path == "/api/v1/point-flows/query":
                self._send_json(200, service.point_flow_query(self._read_json()))
                return
            self._send_json(404, {"error": "integrated API route not found"})

        def _handle_integrated_put(self) -> None:
            service = self._require_integrated_service()
            path = urlsplit(self.path).path
            if path == "/api/v1/parameters":
                self._send_json(200, service.update_parameters(self._read_json()))
                return
            self._send_json(404, {"error": "integrated API route not found"})

        # [CAL-HTTP-06B] Static and result paths are resolved below fixed roots.
        def _serve_frontend(self, request_path: str) -> None:
            relative = "index.html" if request_path in {"", "/"} else unquote(request_path).lstrip("/")
            candidate = (frontend_root / relative).resolve()
            try:
                candidate.relative_to(frontend_root)
            except ValueError:
                self._send_json(403, {"error": "frontend path escapes document root"})
                return
            if not candidate.is_file():
                self._send_json(404, {"error": "frontend file not found"})
                return
            self._send_file(candidate, attachment=False)

        def _send_file(self, path: Path, attachment: bool) -> None:
            body = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if attachment else "no-cache")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(body)

        def _send_exception(self, exc: Exception) -> None:
            if isinstance(exc, (FormalCalibrationTaskNotFound, KeyError)):
                status = 404
            elif isinstance(exc, FormalCalibrationTaskConflict):
                status = 409
            elif isinstance(exc, (FormalCalibrationTaskError, ValueError)):
                status = 400
            elif isinstance(exc, RuntimeError):
                status = 409
            else:
                status = 500
            self._send_json(status, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, format_string: str, *args: Any) -> None:
            print(f"{self.address_string()} - {format_string % args}")

    return PlatformRequestHandler


def create_platform_server(
    host: str,
    port: int,
    manager: FormalCalibrationTaskManager,
    frontend_root: str | Path | None = None,
    production_manager: ProductionCalibrationTaskManager | None = None,
    integrated_service: IntegratedResultsService | None = None,
) -> ThreadingHTTPServer:
    """Create, but do not start, the frontend/API HTTP server."""
    root = (
        Path(frontend_root).resolve()
        if frontend_root is not None
        else manager.project_root / "frontend"
    )
    if not root.is_dir():
        raise FileNotFoundError(f"frontend directory not found: {root}")
    return ThreadingHTTPServer(
        (host, port),
        _handler_class(manager, root.resolve(), production_manager, integrated_service),
    )


__all__ = ["create_platform_server"]
