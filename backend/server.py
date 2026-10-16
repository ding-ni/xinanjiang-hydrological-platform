"""One local HTTP origin for the Vue platform and persistent model jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import socket
import threading
import uuid
import traceback
import io
import zipfile
import csv
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from common import ROOT, WORKSPACE, RUNTIME, read_json, write_json, clean, now, safe_task
from catalog import case_catalog
from contracts import freeze_input_selection, freeze_routing_selection, normalize_request
from input_products import InputProducts
from input_service import InputService
from task_diagnostics import TaskDiagnostics
from state_integrity import assert_state_input_compatibility, validated_parent_state
from routing_versions import RoutingVersions
from diagnostic_jobs import normalize_diagnostic
from hydro_database import HydroDatabase
from state_initialization_adapter import freeze_initialization
from state_repository import StateRepository
from result_repository import ResultRepository
from state_import import import_checkpoint
from platform_config import SETTINGS
from workspace_service import WorkspaceLock, workspace_identity, workspace_info, DraftRepository
from workspace_backup import create_backup
from forecast_products import ForecastProducts, freeze_forecast_parameters
from science_context import ScienceVersions, freeze_science_selection, inherit_state_science
from original_calibration_plan import validate_original_request
from model_schemes import ModelSchemes
from parameter_schemes import ParameterSchemes
from hydrological_operations import HydrologicalOperations, flood_catalog

LABELS = {"calibration": "洪水场次率定", "simulation": "连续模拟", "stateInitialization": "状态初始化", "stateContinuation": "状态续算", "forecast": "洪水预报"}
RUN_TYPES = {"calibration": "eventCalibration", "simulation": "continuousSimulation", "stateInitialization": "stateInitialization", "stateContinuation": "stateContinuation", "forecast": "forecast"}
ACTIVE = {"queued", "running", "cancelling"}


class PlatformHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class TaskManager:
    worker_script = ROOT / "worker.py"

    def __init__(self, root=None, products=None):
        self.root = Path(root or RUNTIME / "tasks")
        self.runtime_root = self.root.parent
        self.products = products or InputProducts(runtime_root=self.runtime_root)
        self.routing_versions = RoutingVersions(runtime_root=self.runtime_root)
        self.science_versions = ScienceVersions(runtime_root=self.runtime_root)
        self.forecast_products = ForecastProducts(self.runtime_root)
        self.input_tasks = None
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = HydroDatabase(self.runtime_root)
        self.schemes = ModelSchemes(self.database)
        self.parameter_schemes = ParameterSchemes(self)
        self.operations = None
        self.states = StateRepository(self)
        self.results = ResultRepository(self)
        from river_sections import RiverSections
        self.river_sections = RiverSections(self)
        self.lock = threading.RLock()
        self.processes = {}
        self.cancelled = set()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="unified-model")
        for file in self.root.glob("*/status.json"):
            status = read_json(file)
            if status["status"] in ACTIVE:
                status = {**status, "status": "failed", "stageLabel": "服务重启，任务已中断", "errorMessage": "上次计算未完成，不自动重跑或认定成功。", "completedAt": now()}
                write_json(file, status)
            self.database.index_task(status, self.root.name)

    def get(self, task_id):
        with self.lock:
            folder = safe_task(self.root, task_id)
            if not (folder / "status.json").is_file():
                raise KeyError("找不到任务")
            status = read_json(folder / "status.json")
            if status["status"] == "running" and (folder / "progress.json").exists():
                status.update(read_json(folder / "progress.json"))
            return status

    def list(self, include_replay=False):
        # Older numerical artifacts have no submission time; retain them without
        # inventing a date or allowing one legacy record to break all new jobs.
        rows = sorted([self.get(p.parent.name) for p in self.root.glob("*/status.json")], key=lambda row: str(row.get("createdAt") or ""), reverse=True)
        if include_replay:
            audit_context={}
            for row in rows:
                row['replayability'] = self.replayability(row['taskId'], row, audit_context)
        return rows

    def replayability(self, task_id, status=None, audit_context=None):
        """Cheap availability audit; full content hashes are checked on submit."""
        reasons = []
        audit_context={} if audit_context is None else audit_context
        try:
            row = status or self.get(task_id)
            if row.get('status') not in {'completed', 'archived'}:
                reasons.append('计算尚未完成或未成功')
            folder = safe_task(self.root, task_id)
            request = row.get('runConfig') or (read_json(folder / 'request.json') if (folder / 'request.json').is_file() else {})
            if not (folder / 'result.json').is_file():
                reasons.append('成果文件缺失')
            if request.get('runType') not in {'calibration', 'simulation', 'stateInitialization'}:
                reasons.append('历史记录不含支持复算的完整数值请求')
            for key in ('context', 'inputs', 'routing', 'science', 'period'):
                if not request.get(key):
                    reasons.append('冻结条件缺少：' + key)
            if request.get('inputs', {}).get('bundleVersionId'):
                try:
                    bundle = self.products.resolve(request['inputs']['bundleVersionId'], verify=False)
                    if not bundle['precipitationDir'].is_dir() or any(not path.is_file() for path in bundle['paths'].values()):
                        reasons.append('原输入资料文件缺失')
                except (ValueError, KeyError, FileNotFoundError) as error:
                    reasons.append('原输入资料不可用：' + str(error))
            if request.get('routing', {}).get('versionId'):
                try: self.routing_versions.resolve(request['routing']['versionId'])
                except (ValueError, KeyError, FileNotFoundError) as error: reasons.append('原汇流版本不可用：' + str(error))
            if request.get('science', {}).get('versionId'):
                try: self.science_versions.resolve(request['science']['versionId'])
                except (ValueError, KeyError, FileNotFoundError) as error: reasons.append('原科学配置不可用：' + str(error))
            parent = request.get('period', {}).get('initialStateVersionId')
            if parent and not (self.runtime_root / 'states' / (str(parent) + '.json')).is_file():
                reasons.append('原起算状态文件缺失')
            expected=(request.get('lineage') or {}).get('modelImplementationSHA256')
            if expected:
                from science_context import model_implementation_identity
                if 'current' not in audit_context: audit_context['current']=model_implementation_identity()['modelImplementationSHA256']
                if expected!=audit_context['current']:
                    if expected not in audit_context:
                        try:
                            from implementation_versions import frozen_worker
                            frozen_worker(expected,self.runtime_root)
                            audit_context[expected]=None
                        except (ValueError,KeyError,FileNotFoundError) as error: audit_context[expected]=str(error)
                    if audit_context[expected]: reasons.append('原计算程序不可用：'+audit_context[expected])
        except (ValueError, KeyError, FileNotFoundError) as error:
            reasons.append('历史记录不可用：' + str(error))
        return {'available': not reasons, 'reasons': reasons, 'verification': 'availability-only; content hashes checked before calculation'}

    def submit(self, request, operation_id=None, state_role=None):
        if isinstance(request, dict) and request.get('inputs', {}).get('sourceBundleVersionId'):
            source = request['inputs']['sourceBundleVersionId']
            preparation = safe_task(self.runtime_root / 'input-tasks', request['inputs'].get('preparationTaskId'))
            status, prepared, config = read_json(preparation / 'status.json'), read_json(preparation / 'result.json'), read_json(preparation / 'request.json')
            from scheme_inputs import normalize_preparation
            effective_method = request.get('lineage', {}).get('automaticInputPreparation', {}).get('rainfallMethod') or request['inputs'].get('rainfallMethod', config.get('rainfallMethod', 'reuse'))
            automatic_source=request.get('lineage', {}).get('automaticInputPreparation', {}).get('sourceId')
            if automatic_source:
                parent_manifest=self.products.resolve(source)['manifest']
                if automatic_source!=(parent_manifest.get('sourceId') or (parent_manifest.get('sourceOrigin') or {}).get('sourceId')):
                    raise ValueError('自动准备的站点来源与原资料来源不一致')
            selection = request.get('scheme')
            frozen_definition = None
            if selection and request['inputs'].get('dataMode') == 'historical-database':
                from model_schemes import preparation_binding_key
                record = self.schemes.get(selection['id'], selection['revision'])
                with self.database.connect() as db:
                    binding = db.execute('SELECT * FROM model_scheme_prepared_inputs WHERE scheme_id=? AND definition_sha256=?',
                                         (selection['id'], preparation_binding_key(record, request))).fetchone()
                if binding and binding['preparation_task_id'] == preparation.name and binding['bundle_version_id'] == request['inputs']['bundleVersionId']:
                    frozen_definition = config
            check = normalize_preparation({key: config[key] for key in ('operation', 'requestId', 'name', 'projectId', 'resolutionId', 'rainfallMethod', 'idwPower', 'minimumStations', 'operationPurpose') if key in config} | {
                'parentBundleVersionId': source, 'period': request['period'], 'idwPower': request['inputs'].get('idwPower', 2), 'minimumStations': request['inputs'].get('minimumStations', 1), 'rainfallMethod': effective_method,
                **({'stationSourceId': request['inputs']['stationSourceId']} if request['inputs'].get('stationSourceId') else {}),
                **({'stationNetwork': request['inputs']['stationNetwork']} if request['inputs'].get('stationNetwork') else {}),
                'statePolicy': (request.get('calibration') or {}).get('statePolicy', 'continuous-origin' if request['period'].get('continuousOrigin') else 'independent-warmup'),
                'science': request.get('science', {}), 'evaporation': request.get('inputs', {}).get('evaporation', {}),
                'continuousOrigin': (request.get('calibration') or {}).get('continuousOrigin') or request['period'].get('continuousOrigin')}, self.products, frozen_definition=frozen_definition)
            if status.get('status') not in {'completed', 'archived'} or prepared.get('issues') or status.get('registeredBundleVersionId') != request['inputs']['bundleVersionId'] or config.get('preparationKey') != check['preparationKey']:
                raise ValueError('计算资料与本次方案时段不一致或仍有缺测问题，请重新准备')
            if request['inputs'].get('rainfallMethod') == 'stationIdw' and request['inputs'].get('stationNetwork') != config.get('stationNetwork'):
                raise ValueError('站网已改变，请保存方案并重新准备计算资料')
        request = self.schemes.freeze(request)
        if isinstance(request, dict) and request.get('runType') == 'spatialExport':
            from spatial_products import normalize_spatial
            return self._enqueue(normalize_spatial(request, self.products), run_type='spatialExport', label='空间成果制作')
        if isinstance(request, dict) and request.get("runType") in {"pointQuery", "diagnosticExperiment", "productionWorkflow"}:
            request = normalize_diagnostic(request, self)
            return self._enqueue(request, task_prefix="JOB", run_type=request["runType"],
                                 label={"pointQuery": "任意点流量查询", "diagnosticExperiment": "诊断实验", "productionWorkflow": "完整参数诊断验收"}[request['runType']])
        request = validate_original_request(request, self.runtime_root)
        request, bundle = freeze_input_selection(request, self.products)
        request = freeze_routing_selection(request, bundle, self.routing_versions)
        request = normalize_request(request, case_catalog(bundle), runtime_root=self.runtime_root)
        request = freeze_initialization(request, bundle)
        parent = request.get("period", {}).get("initialStateVersionId")
        checkpoint = None
        if parent:
            if not str(parent).startswith("STATE-JOB-") or any(mark in str(parent) for mark in ("/", "\\")):
                raise ValueError("父状态版本无效")
            checkpoint = validated_parent_state(self.runtime_root / "states" / f"{parent}.json", self.root, parent)
            if any(checkpoint["context"].get(key) != request["context"].get(key)
                   for key in ("projectId", "resolutionId", "modelConfigId", "contextKey")):
                raise ValueError("父状态与请求上下文不一致")
            request = inherit_state_science(request, checkpoint, self.runtime_root)
        request, _science = freeze_science_selection(request, self.runtime_root)
        if checkpoint is not None:
            assert_state_input_compatibility(checkpoint, request, self.products)
            if request['runType'] == 'forecast':
                self.forecast_products.freeze(request)
                freeze_forecast_parameters(request, checkpoint, self)
        return self._enqueue(request, operation_id=operation_id, state_role=state_role)

    def _enqueue(self, request, task_prefix="JOB", run_type=None, label=None, source="model-backend", operation_id=None, state_role=None):
        if self.worker_script.name=='worker.py' and (request.get('lineage') or {}).get('modelImplementationSHA256'):
            from implementation_versions import snapshot_current, frozen_worker
            actual=snapshot_current(self.runtime_root)
            if request['lineage']['modelImplementationSHA256']!=actual:
                frozen_worker(request['lineage']['modelImplementationSHA256'],self.runtime_root)
        if request.get("runType") == "calibration" and request.get("period", {}).get("mode") == "continuous":
            run_type = run_type or "continuousCalibration"
            label = label or "连续时段率定"
        if (request.get('calibration') or {}).get('evaluationMode') == 'manual':
            label = '人工参数试调'
            if request['calibration'].get('simulationOnly'):
                label, run_type = '场次模拟', 'eventSimulation'
        elif (request.get('calibration') or {}).get('target') == 'routing':
            label = '坡面汇流率定'
        elif (request.get('calibration') or {}).get('target') == 'joint':
            label = '产流、坡面与河道联合率定'
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            request_id = request.get("requestId")
            for existing in self.list():
                if request_id and existing.get("requestId") == request_id:
                    if existing.get("requestDigest") != digest:
                        raise ValueError("同一请求编号不能对应不同条件，请创建新作业")
                    return existing
                if request.get('runType') == 'pointQuery' and existing.get('runType') == 'pointQuery' and existing.get('status') in {'queued', 'running', 'completed', 'archived'}:
                    identity = {key: value for key, value in request.items() if key != 'requestId'}
                    previous = {key: value for key, value in existing.get('runConfig', {}).items() if key != 'requestId'}
                    if identity == previous:
                        return existing
            task_id = f"{task_prefix}-{uuid.uuid4().hex}"
            folder = safe_task(self.root, task_id)
            folder.mkdir()
            status = {"taskId": task_id, "requestId": request_id, "requestDigest": digest,
                      "runType": run_type or RUN_TYPES[request["runType"]], "runTypeLabel": label or LABELS[request["runType"]],
                      "contextKey": request.get("context", {}).get("contextKey", "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km"), "runConfig": request,
                      "status": "queued", "progress": 0, "stageLabel": "等待模型计算",
                      "createdAt": now(), "startedAt": None, "completedAt": None, "resultSource": source,
                      "resultVersionId": None, "errorMessage": "", "lineage": request.get("lineage", {}), "productionEligible": False}
            if operation_id:
                status.update(operationId=operation_id, stateRole=state_role, internal=True)
            write_json(folder / "request.json", request)
            write_json(folder / "status.json", status)
            self.database.index_task(status, self.root.name)
            self.executor.submit(self.execute, task_id)
            return status

    def update(self, task_id, **updates):
        with self.lock:
            path = safe_task(self.root, task_id) / "status.json"
            status = {**read_json(path), **updates}
            write_json(path, status)
            self.database.index_task(status, self.root.name)
            if status.get('status') == 'completed' and hasattr(self, 'results'):
                self.results.ensure_name(status)
            return status

    def execute(self, task_id):
        folder = safe_task(self.root, task_id)
        with self.lock:
            if task_id in self.cancelled:
                return
            self.update(task_id, status="running", startedAt=now(), stageLabel="启动真实计算进程")
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "XAJ_RUNTIME_DIR": str(self.runtime_root)}
            log = (folder / "task.log").open("w", encoding="utf-8")
            try:
                worker_script=self.worker_script
                if worker_script.name=='worker.py':
                    from science_context import model_implementation_identity
                    from implementation_versions import frozen_worker, execution_environment
                    expected=(read_json(folder/'request.json').get('lineage') or {}).get('modelImplementationSHA256')
                    if expected and expected!=model_implementation_identity()['modelImplementationSHA256']:
                        worker_script=frozen_worker(expected,self.runtime_root)
                        environment.update(execution_environment(worker_script,self.runtime_root))
                process = subprocess.Popen([sys.executable, "-B", "-u", str(worker_script), "--task", str(folder)],
                                           cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except Exception as error:
                log.close()
                self.update(task_id, status="failed", errorMessage=str(error), completedAt=now())
                return
            self.processes[task_id] = process
        code = process.wait()
        log.close()
        with self.lock:
            self.processes.pop(task_id, None)
            if task_id in self.cancelled:
                self.update(task_id, status="cancelled", stageLabel="已停止", completedAt=now())
            elif code == 0 and (folder / "result.json").is_file():
                result = read_json(folder / "result.json")
                self.update(task_id, status="completed", progress=100,
                            stageLabel="空间成果制作完成" if result.get('runType') == 'spatialExport' else "资料加工完成，等待显式登记" if isinstance(self, InputTaskManager) else "真实模型计算完成", completedAt=now(),
                            resultVersionId=result["resultVersionId"], lineage=result.get("lineage", {}),
                            **({"productVersionId": result["productVersionId"]} if "productVersionId" in result else {}))
                if isinstance(self, InputTaskManager) and self.get(task_id)['runConfig'].get('operation') == 'schemeInput':
                    try:
                        version = result.get('preparedBundleVersionId')
                        if not version:
                            version = self.products.register_task(task_id)['bundleVersionId']
                        self.products.resolve(version)
                        self.update(task_id, registeredBundleVersionId=version,
                                    stageLabel='资料检查有待处理项' if result.get('issues') else '计算资料已备齐')
                    except Exception as exc:
                        self.update(task_id, status='failed', errorMessage=str(exc), stageLabel='资料准备未完成')
                elif not isinstance(self, InputTaskManager) and not self.get(task_id).get('operationId') and result.get('calibrationMode') == 'continuous' and result.get('statePolicy') == 'continuous-origin':
                    try:
                        request = read_json(folder / 'request.json')
                        windows = request['period']['windows']
                        replay = {key: copy_value for key, copy_value in request.items() if key not in {'calibration', 'sourcePlan', 'period', 'lineage', 'requestId', 'science', 'scheme'}}
                        replay['inputs'] = {key: value for key, value in request['inputs'].items() if key not in {'sourceBundleVersionId', 'preparationTaskId', 'rainfallMethod'}}
                        replay.update(requestId='BEST-' + task_id, runType='simulation', parameters=result['bestParameters'],
                                      period={'startTime': min(row['start'] for row in windows), 'endTime': max(row['end'] for row in windows),
                                              'warmupDays': request['period'].get('warmupDays', 7), 'continuousOrigin': result['continuousOrigin']},
                                      science={'versionId': request['science']['versionId']},
                                      lineage={'sourceTaskId': task_id, 'parameterVersionId': result['parameterVersionId'],
                                               **{key: value for key, value in request.get('lineage', {}).items() if key.startswith('scheme')}})
                        if result.get('routingCandidate'):
                            replay['routing'] = {'versionId': result['routingCandidate']['routingVersionId']}
                        child = self.submit(replay)
                        result['stateCalculationTaskId'] = child['taskId']
                        write_json(folder / 'result.json', result)
                    except Exception as exc:
                        result['stateCalculationError'] = str(exc)
                        write_json(folder / 'result.json', result)
            else:
                lines = (folder / "task.log").read_text(encoding="utf-8", errors="replace").splitlines()
                message = lines[-1] if lines else f"模型进程退出：{code}"
                self.update(task_id, status="failed", stageLabel="计算失败，请核查输入", completedAt=now(), errorMessage=message)

    def cancel(self, task_id):
        with self.lock:
            status = self.get(task_id)
            if status["status"] not in ACTIVE:
                return status
            self.cancelled.add(task_id)
            process = self.processes.get(task_id)
            if process is not None:
                if process.poll() is None:
                    if os.name == "nt":
                        # Windows venv python.exe is a launcher with a real Python
                        # child. Stopping only the launcher leaves the model alive.
                        stopped = subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                                 capture_output=True, timeout=10,
                                                 creationflags=subprocess.CREATE_NO_WINDOW)
                        if stopped.returncode and process.poll() is None:
                            self.cancelled.discard(task_id)
                            raise RuntimeError("计算进程未能停止，请保留服务窗口并核查任务日志")
                    else:
                        process.terminate()
                return self.update(task_id, status="cancelling", stageLabel="正在停止模型进程")
            return self.update(task_id, status="cancelled", stageLabel="已取消排队", completedAt=now())

    def result(self, task_id):
        if self.get(task_id)["status"] not in {"completed", "archived"}:
            raise RuntimeError("任务尚未成功完成，没有可用结果")
        return read_json(safe_task(self.root, task_id) / "result.json")

    def available_artifact(self, task_id):
        try:
            return self.get(task_id)["status"] in {"completed", "archived"}
        except (KeyError, FileNotFoundError, ValueError):
            return False

    def archive(self, task_id):
        self.result(task_id)
        return self.update(task_id, status="archived", archivedAt=now(), stageLabel="已归档")

    def clone(self, task_id):
        request = read_json(safe_task(self.root, task_id) / "request.json")
        if request.get('runType') == 'stateImport':
            raise ValueError('导入状态请在起算状态页用于续算')
        request["requestId"] = f"COPY-{uuid.uuid4().hex}"
        request.setdefault("lineage", {})["sourceTaskId"] = task_id
        return self.submit(request)

    def close(self):
        if self.operations is not None:
            self.operations.close()
        if self.input_tasks is not None:
            self.input_tasks.close()
        for status in self.list():
            if status["status"] in ACTIVE:
                self.cancel(status["taskId"])
        self.executor.shutdown(wait=True)


class InputTaskManager(TaskManager):
    """Separate cancellable processing queue; never auto-register products."""
    worker_script = ROOT / "input_product_worker.py"

    def __init__(self, root=None, products=None):
        super().__init__(root or RUNTIME / "input-tasks", products=products)

    def submit(self, request):
        request = self.products.normalize_processing_request(request)
        return self._enqueue(request, task_prefix="DATA", run_type="inputProcessing",
                             label="降雨资料时段提取" if request["operation"] == "qcWindow" else "站点雨量整编与空间插值", source="input-processing")

    def register(self, task_id):
        with self.lock:
            self.result(task_id)
            version = self.products.register_task(task_id)
            self.update(task_id, registeredBundleVersionId=version["bundleVersionId"], stageLabel="资料版本已登记（诊断）")
            return version


def handler(manager, frontend, input_manager=None, products=None, *, verbose_requests=False):
    products = products or manager.products
    if input_manager is None:
        if manager.input_tasks is None:
            manager.input_tasks = InputTaskManager(manager.runtime_root / "input-tasks", products=products)
        input_manager = manager.input_tasks
    if manager.operations is None:
        manager.operations = HydrologicalOperations(manager, input_manager)
    input_readers = {}
    input_reader_lock = threading.RLock()
    diagnostics = TaskDiagnostics(manager)
    class Handler(BaseHTTPRequestHandler):
        def log_request(self, code="-", size="-"):
            # Routine page loads and polling stay quiet; HTTP failures remain
            # visible, and log_error/exception tracebacks keep their defaults.
            if verbose_requests or (str(code).isdigit() and int(code) >= 400):
                super().log_request(code, size)

        def send_json(self, status, payload):
            body = json.dumps(clean(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_body(self):
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ValueError("只接受 application/json 请求")
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                raise ValueError("拒绝跨站作业提交；请使用平台同源入口")
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 2 * 1024 * 1024:
                raise ValueError("请求体必须为 1 B 到 2 MB 的 JSON")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def route(self):
            hostname = urlsplit('http://' + self.headers.get('Host', '')).hostname
            if hostname not in {"127.0.0.1", "localhost"}:
                raise ValueError("本地平台只接受 loopback Host，不支持公网访问")
            path = unquote(urlsplit(self.path).path)
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("查询字段不能重复")
            argument = lambda key, default=None: query.get(key, [default])[0]
            prefix = "/api/model"
            if path == f'{prefix}/forecast-rain':
                if self.command == 'GET':
                    self.send_json(200, {'products': manager.forecast_products.list()})
                elif self.command == 'POST':
                    self.send_json(200, manager.forecast_products.register(self.read_body()))
                else:
                    raise KeyError('接口不存在')
                return
            if path == f"{prefix}/workspace" and self.command == 'GET':
                self.send_json(200, workspace_info(manager.runtime_root))
                return
            if path == f"{prefix}/workspace/drafts":
                drafts = DraftRepository(manager.database)
                if self.command == 'GET':
                    self.send_json(200, {'drafts': drafts.list()})
                elif self.command == 'POST':
                    self.send_json(200, drafts.save(self.read_body()))
                else:
                    raise KeyError('接口不存在')
                return
            if path == f"{prefix}/workspace/backups":
                if self.command == 'GET':
                    rows = []
                    for file in (manager.runtime_root / 'backups').glob('BACKUP-*/backup-manifest.json'):
                        info = read_json(file)
                        rows.append({'name': file.parent.name, 'path': str(file.parent), 'createdAt': info['createdAt'], 'files': len(info['files']), 'bytes': info['bytes']})
                    self.send_json(200, {'backups': sorted(rows, key=lambda r: r['createdAt'], reverse=True)})
                elif self.command == 'POST':
                    if self.read_body() != {}: raise ValueError('备份请求无需目录参数')
                    with manager.operations.lock, manager.parameter_schemes.lock, manager.lock, input_manager.lock, products._lock, manager.forecast_products.lock:
                        self.send_json(200, create_backup(manager.runtime_root))
                else:
                    raise KeyError('接口不存在')
                return
            if path.startswith(f"{prefix}/database/"):
                database = manager.database
                operation = path[len(f"{prefix}/database/"):]
                if argument("projectId", "nananxi-xufan") != "nananxi-xufan":
                    raise ValueError("本资料库对应南安溪-徐畈，请切换流域后查询")
                if self.command == 'GET' and operation in {'catalog', 'process'}:
                    from historical_inputs import HistoricalInputs
                    with input_reader_lock:
                        if not hasattr(manager, 'historical_inputs'):
                            manager.historical_inputs = HistoricalInputs(database, products.model_root)
                    service = manager.historical_inputs
                    options = {'project_id': argument('projectId', 'nananxi-xufan'), 'resolution_id': argument('resolutionId', '1km')}
                    payload = service.catalog(**options) if operation == 'catalog' else service.process(
                        start=argument('start'), end=argument('end'), event_id=argument('eventId'), **options)
                elif operation == 'network' and self.command in {'GET', 'POST'}:
                    from station_network import local_station_network
                    payload = local_station_network(database, self.read_body() if self.command == 'POST' else None)
                elif operation == 'preparation-defaults' and self.command in {'GET', 'POST'}:
                    payload = manager.schemes.history_defaults(self.read_body() if self.command == 'POST' else None)
                elif operation == 'routing-defaults' and self.command in {'GET','POST'}:
                    with manager.lock:
                        payload=manager.routing_versions.defaults(self.read_body() if self.command == 'POST' else None)
                elif self.command == "GET" and operation == "summary":
                    payload = database.summary()
                elif self.command == "GET" and operation == "stations":
                    payload = {"stations": database.stations()}
                elif self.command == 'GET' and operation == 'station-directory':
                    with database.connect() as db:
                        payload = {'stations': [dict(row) for row in db.execute('SELECT station_id AS id,name,kind,lon,lat FROM stations WHERE project_id=? ORDER BY station_id', ('nananxi-xufan',))],
                                   'aliases': [dict(row) for row in db.execute('SELECT * FROM station_aliases WHERE project_id=?', ('nananxi-xufan',))]}
                elif self.command == 'GET' and operation == 'sources':
                    with database.connect() as db:
                        payload = {'sources': [dict(row) for row in db.execute('SELECT id,category,relative_path AS path,sha256,records,imported_at AS importedAt FROM source_files ORDER BY id')]}
                elif self.command == "GET" and operation == "monitor":
                    payload = database.monitor(argument("end"))
                elif self.command == "GET" and operation == "window":
                    offset = argument("offsetHours", "0")
                    if not offset.isascii() or not offset.isdecimal():
                        raise ValueError("显示起点须为查询时段内的非负整数小时")
                    payload = database.window(argument("stationId"), argument("variable"), argument("start"), argument("end"), offsetHours=int(offset), sourceId=argument('sourceId'))
                elif self.command == "GET" and operation == "export":
                    with database.export_window(argument("stationId"), argument("variable"), argument("start"), argument("end"), sourceId=argument('sourceId')) as (payload, rows):
                        # A bounded CSV buffer keeps decades of observations
                        # out of memory. Closing the connection frames the body.
                        stream = io.StringIO(newline="")
                        writer = csv.writer(stream)
                        writer.writerow(["time", "station_id", "variable", "value", "unit", "recorded", "source_id"])
                        self.send_response(200)
                        self.send_header("Content-Type", "text/csv; charset=utf-8")
                        self.send_header("Content-Disposition", 'attachment; filename="observations.csv"')
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.close_connection = True
                        self.end_headers()
                        self.wfile.write(b"\xef\xbb\xbf")
                        for index, row in enumerate(rows, 1):
                            writer.writerow([row["time"], payload["station"]["id"], payload["variable"], row["value"], payload["unit"], row["recorded"], row["sourceId"]])
                            if index % 512 == 0:
                                self.wfile.write(stream.getvalue().encode("utf-8"))
                                stream.seek(0)
                                stream.truncate(0)
                        if stream.tell():
                            self.wfile.write(stream.getvalue().encode("utf-8"))
                    return
                elif self.command == "GET" and operation == "events":
                    payload = {"events": database.events(argument("search", ""))}
                elif self.command == "POST" and operation == "rain-source":
                    prepared = database.prepare_rain_source(self.read_body())
                    payload = products.register_source(prepared["payload"], origin=prepared["origin"])
                elif self.command == "POST" and operation == "backup":
                    self.read_body()
                    payload = database.backup()
                elif self.command == "GET" and operation == "schemes":
                    payload = {"schemes": database.schemes()}
                elif self.command == "POST" and operation == "schemes":
                    body = self.read_body()
                    task = manager.get(body.get("taskId"))
                    if task["status"] not in {"completed", "archived"}:
                        raise ValueError("请从已完成计算保存方案")
                    if task['runType'] == 'stateImport':
                        raise ValueError('导入状态请在起算状态页用于续算')
                    payload = database.save_scheme(body.get("name"), task["taskId"], read_json(safe_task(manager.root, task["taskId"]) / "request.json"))
                elif self.command == "POST" and operation.startswith("schemes/") and operation.endswith("/run") and operation.count("/") == 2:
                    self.read_body()
                    payload = manager.submit(database.scheme_request(operation.split("/")[1]))
                elif self.command == 'GET' and operation.startswith('schemes/') and operation.count('/') == 1:
                    payload = database.scheme_request(operation.split('/')[1])
                else:
                    raise KeyError("资料库接口不存在")
                self.send_json(200, payload)
                return
            if self.command == "GET" and path == f"{prefix}/health":
                self.send_json(200, {"status": "ok", "schemaVersion": "2.0", "service": "unified-xaj-platform"})
                return
            if path == f'{prefix}/model-schemes' and self.command in {'GET', 'POST'}:
                payload = manager.schemes.list() if self.command == 'GET' else manager.schemes.save(self.read_body())
                self.send_json(200, payload)
                return
            if path.startswith(f'{prefix}/model-schemes/') and self.command == 'POST' and path.rsplit('/', 1)[-1] in {'rename', 'discard-draft'}:
                pieces = path[len(f'{prefix}/model-schemes/'):].split('/')
                if len(pieces) != 2:
                    raise KeyError('方案不存在')
                method = manager.schemes.rename if pieces[1] == 'rename' else manager.schemes.discard_editor
                self.send_json(200, method(pieces[0], self.read_body()))
                return
            if path == f'{prefix}/scheme-input-assets' and self.command == 'GET':
                from delivered_schemes import scheme_assets
                self.send_json(200, {'assets': scheme_assets(manager.schemes, argument('schemeId'))})
                return
            if path.startswith(f'{prefix}/model-schemes/') and path.endswith('/history-note') and self.command == 'POST':
                pieces = path[len(f'{prefix}/model-schemes/'):].split('/')
                if len(pieces) != 2:
                    raise KeyError('方案不存在')
                self.send_json(200, manager.schemes.history_note(pieces[0], self.read_body()))
                return
            if path.startswith(f'{prefix}/model-schemes/') and path.endswith('/draft') and self.command == 'POST':
                pieces = path[len(f'{prefix}/model-schemes/'):].split('/')
                if len(pieces) != 2:
                    raise KeyError('方案不存在')
                self.send_json(200, manager.schemes.save_editor(pieces[0], self.read_body()))
                return
            if path == f'{prefix}/flood-catalog' and self.command == 'GET':
                if argument('projectId', 'nananxi-xufan') != 'nananxi-xufan' or argument('resolutionId', '1km') != '1km':
                    raise ValueError('请选择南安溪徐畈1 km洪水目录')
                self.send_json(200, flood_catalog(manager.database))
                return
            if path == f'{prefix}/parameter-schemes' and self.command in {'GET', 'POST'}:
                self.send_json(200, manager.parameter_schemes.list() if self.command == 'GET' else manager.parameter_schemes.save(self.read_body()))
                return
            if path.startswith(f'{prefix}/parameter-schemes/') and self.command == 'GET':
                self.send_json(200, manager.parameter_schemes.get(path.rsplit('/', 1)[-1]))
                return
            if path.startswith(f'{prefix}/parameter-schemes/') and path.endswith('/rename') and self.command == 'POST':
                pieces = path[len(f'{prefix}/parameter-schemes/'):].split('/')
                if len(pieces) != 2:
                    raise KeyError('参数不存在')
                self.send_json(200, manager.parameter_schemes.rename(pieces[0], self.read_body()))
                return
            if path == f'{prefix}/operations' and self.command in {'GET', 'POST'}:
                self.send_json(200 if self.command == 'GET' else 202,
                    manager.operations.list(include_replay=True) if self.command == 'GET' else manager.operations.submit(self.read_body()))
                return
            if path.startswith(f'{prefix}/operations/'):
                pieces = path[len(f'{prefix}/operations/'):].split('/')
                if self.command == 'GET' and len(pieces) == 1:
                    payload = manager.operations.get(pieces[0],include_replay=True)
                elif self.command == 'GET' and len(pieces) == 2 and pieces[1] == 'result':
                    payload = manager.operations.result(pieces[0])
                elif self.command == 'POST' and len(pieces) == 2 and pieces[1] == 'cancel':
                    self.read_body()
                    payload = manager.operations.cancel(pieces[0])
                else:
                    raise KeyError('计算接口不存在')
                self.send_json(200, payload)
                return
            if path == f'{prefix}/station-network' and self.command in {'GET', 'POST'}:
                from station_network import station_network, observation_adapter
                parent = products.resolve(argument('bundleVersionId'))
                parent = observation_adapter(products, parent, argument('sourceId'))
                self.send_json(200, station_network(products, parent, self.read_body() if self.command == 'POST' else None))
                return
            if path == f'{prefix}/preparation-defaults' and self.command in {'GET', 'POST'}:
                body = self.read_body() if self.command == 'POST' else None
                if body is not None:
                    selected = {**manager.schemes.defaults(), **body}
                    products.resolve(selected['sourceBundleVersionId'])
                    if selected.get('stationSourceId') not in {None, 'LOCAL_OBSERVATIONS'}:
                        products.source(selected['stationSourceId'])
                self.send_json(200, manager.schemes.defaults(body))
                return
            if path.startswith(f'{prefix}/model-schemes/') and self.command == 'GET':
                self.send_json(200, manager.schemes.get(path.rsplit('/', 1)[-1], int(argument('revision')) if argument('revision') else None))
                return
            if path == f'{prefix}/scheme-preparations' and self.command == 'POST':
                from scheme_inputs import reuse_preparation
                self.send_json(202, reuse_preparation(input_manager, self.read_body()))
                return
            if self.command == "GET" and path == f"{prefix}/catalog":
                payload = case_catalog(database=manager.database)
                payload["routingVersions"] = manager.routing_versions.list()["versions"]
                self.send_json(200, payload)
                return
            if self.command == "GET" and path == f"{prefix}/spatial-products":
                bundle = products.resolve(argument("bundleVersionId"))
                from diagnostic_jobs import spatial_payload
                self.send_json(200, spatial_payload(bundle))
                return
            if path == f'{prefix}/numeric-status' and self.command == 'GET':
                from numeric_runtime import status
                self.send_json(200,status(manager.runtime_root))
                return
            if path == f"{prefix}/routing-versions" and self.command in {"GET", "POST"}:
                with manager.lock:
                    payload = manager.routing_versions.list() if self.command == "GET" else manager.routing_versions.create(self.read_body())
                self.send_json(200 if self.command == "GET" else 201, payload)
                return
            if self.command == 'GET' and path == f'{prefix}/science-versions':
                self.send_json(200, manager.science_versions.list())
                return
            if path == f'{prefix}/science-conditions':
                if self.command == 'GET':
                    payload = manager.science_versions.conditions(argument('versionId'))
                elif self.command == 'POST':
                    payload = manager.science_versions.register_conditions(self.read_body())
                else:
                    raise KeyError('起算条件接口不存在')
                self.send_json(200, payload)
                return
            if self.command == 'GET' and path.startswith(f'{prefix}/science-versions/'):
                version = path[len(f'{prefix}/science-versions/'):]
                self.send_json(200, manager.science_versions.export(version))
                return
            if self.command == "GET" and path == f"{prefix}/input-products":
                self.send_json(200, products.list_versions())
                return
            if path == f"{prefix}/input-sources" and self.command in {"GET", "POST"}:
                self.send_json(200 if self.command == "GET" else 201,
                               products.list_sources() if self.command == "GET" else products.register_source(self.read_body()))
                return
            if path == f"{prefix}/input-tasks" and self.command in {"GET", "POST"}:
                self.send_json(200 if self.command == "GET" else 202,
                               {"tasks": input_manager.list()} if self.command == "GET" else input_manager.submit(self.read_body()))
                return
            if path.startswith(f"{prefix}/input-tasks/"):
                pieces = path[len(f"{prefix}/input-tasks/"):].split("/")
                task_id, operation = pieces[0], pieces[1] if len(pieces) == 2 else ""
                if len(pieces) > 2:
                    raise KeyError("资料处理接口不存在")
                if self.command == "GET" and not operation:
                    payload = input_manager.get(task_id)
                elif self.command == "GET" and operation == "result":
                    payload = input_manager.result(task_id)
                elif self.command == "GET" and operation == "log":
                    log_path = safe_task(input_manager.root, task_id) / "task.log"
                    payload = {"taskId": task_id, "tail": log_path.read_text(encoding="utf-8", errors="replace")[-20000:] if log_path.is_file() else ""}
                elif self.command == "POST" and operation in {"cancel", "register"}:
                    self.read_body()  # enforce same-origin + bounded JSON for every mutation
                    payload = getattr(input_manager, operation)(task_id)
                else:
                    raise KeyError("资料处理接口不存在")
                self.send_json(200, payload)
                return
            if self.command == "GET" and path.startswith(f"{prefix}/inputs/"):
                context = {"project_id": argument("projectId", "nananxi-xufan"), "resolution_id": argument("resolutionId", "1km")}
                # Omission selects the fixed baseline but never bypasses its
                # pinned-file verification. Otherwise cached flow could be
                # mixed with changed rain while retaining an old bundle ID.
                bundle = products.resolve(argument("bundleVersionId"))
                identity = (bundle["bundleVersionId"], bundle["manifestSHA256"])
                with input_reader_lock:
                    if identity not in input_readers:
                        input_readers[identity] = InputService(bundle=bundle)
                    selected_inputs = input_readers[identity]
                operation = path[len(f"{prefix}/inputs/"):]
                if operation == "catalog":
                    payload = selected_inputs.catalog(**context)
                elif operation == "window":
                    payload = selected_inputs.window(argument("start"), argument("end"), **context)
                elif operation.startswith("events/") and operation.count("/") == 1:
                    payload = selected_inputs.event(operation.split("/")[1], warmup_hours=int(argument("warmupHours", 168)), **context)
                else:
                    raise KeyError("输入查询接口不存在")
                self.send_json(200, payload)
                return
            if path == f"{prefix}/tasks":
                self.send_json(200 if self.command == "GET" else 202, {"tasks": manager.list(include_replay=True)} if self.command == "GET" else manager.submit(self.read_body()))
                return
            if self.command == "POST" and path in {f"{prefix}/calibration-tasks", f"{prefix}/simulation-tasks", f"{prefix}/forecast-tasks"}:
                request = self.read_body()
                expected = {"calibration-tasks": "calibration", "simulation-tasks": "simulation", "forecast-tasks": "forecast"}[path.rsplit('/', 1)[-1]]
                if not isinstance(request, dict) or request.get('runType') != expected:
                    raise ValueError("任务类型与提交接口不一致，不能自动替换计算类型")
                self.send_json(202, manager.submit(request))
                return
            if path.startswith(f"{prefix}/tasks/"):
                pieces = path[len(f"{prefix}/tasks/"):].split("/")
                if self.command == "GET" and len(pieces) in {2, 3} and pieces[1] == "grid-state":
                    if len(pieces) == 3 and pieces[2] != "catalog":
                        raise KeyError("网格诊断接口不存在")
                    if argument("time") and argument("unitId"):
                        raise ValueError("time 和 unitId 只能选择一个查询方式")
                    payload = diagnostics.catalog(pieces[0]) if len(pieces) == 3 else diagnostics.state(pieces[0], argument("time"), argument("unitId"))
                    self.send_json(200, payload)
                    return
                task_id, operation = pieces[0], pieces[1] if len(pieces) == 2 else ""
                if len(pieces) > 2:
                    raise KeyError("接口不存在")
                if self.command == "GET" and not operation:
                    payload = manager.get(task_id)
                    payload['replayability'] = manager.replayability(task_id, payload)
                elif self.command == "GET" and operation == "result":
                    payload = manager.result(task_id)
                elif self.command == 'GET' and operation in {'spatial-package', 'spatial-preview'}:
                    from spatial_products import artifact_bytes
                    filename = argument('name') if operation == 'spatial-preview' else None
                    if operation == 'spatial-preview' and not filename:
                        raise ValueError('请选择空间图件')
                    body = artifact_bytes(safe_task(manager.root, task_id), manager.result(task_id), filename)
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png' if filename else 'application/zip')
                    self.send_header('Content-Disposition', f'{"inline" if filename else "attachment"}; filename="{filename or task_id + "-spatial.zip"}"')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif self.command == "GET" and operation == "log":
                    log_path = safe_task(manager.root, task_id) / "task.log"
                    manager.get(task_id)
                    payload = {"taskId": task_id, "tail": log_path.read_text(encoding="utf-8", errors="replace")[-20000:] if log_path.is_file() else ""}
                elif self.command == "GET" and operation == "report-package":
                    manager.result(task_id)
                    folder = safe_task(manager.root, task_id).resolve()
                    allowed = {"request.json", "result.json", "status.json", "task.log", "input-manifest.json", "event-quality.json", "period-quality.json", "routing-evidence.json", "formal-diagnostic-reports.json", "reach_flow.csv", "component_flow.csv", "annual_water_balance_metrics.csv", "continuous_hourly_flow.csv", "continuous_water_balance_report.json", "production-gate-report.json"}
                    allowed.update({'checkpoint.json', 'source-checkpoint.json', 'forecast-rain.json', 'forecast-end-state.json'})
                    allowed.add('overlap-group-metrics.csv')
                    candidates = [p for p in folder.iterdir() if p.name in allowed and p.is_file()]
                    for child in folder.rglob('*'):
                        relative_parts = child.relative_to(folder).parts[:-1]
                        if child.is_file() and child.name in allowed and relative_parts and all(part == 'continuous-balance' or part.startswith('JOB-') for part in relative_parts):
                            candidates.append(child)
                    if any(p.is_symlink() or not p.resolve().is_relative_to(folder) for p in candidates):
                        raise ValueError("报告文件越界")
                    if sum(p.stat().st_size for p in candidates) > 64 * 1024 * 1024:
                        raise ValueError("报告包超出 64 MB 限制，请使用分项结果")
                    stream = io.BytesIO()
                    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as package:
                        for candidate in candidates:
                            package.write(candidate, str(candidate.relative_to(folder)).replace("\\", "/"))
                    body = stream.getvalue()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f'attachment; filename="{task_id}-reports.zip"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif self.command == "GET" and operation == "event-quality":
                    payload = diagnostics.event_quality(task_id)
                elif self.command == "POST" and operation in {"cancel", "archive", "clone"}:
                    origin = self.headers.get("Origin")
                    if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                        raise ValueError("拒绝跨站任务操作")
                    payload = getattr(manager, operation)(task_id)
                else:
                    raise KeyError("接口不存在")
                self.send_json(200, payload)
                return
            if self.command == "GET" and path == f"{prefix}/state-catalog":
                self.send_json(200, {"metadata": manager.states.metadata(), "checkpointImport": True})
                return
            if self.command == 'POST' and path == f'{prefix}/states/import':
                self.send_json(200, import_checkpoint(manager, self.read_body()))
                return
            if self.command == 'GET' and path == f'{prefix}/result-catalog':
                self.send_json(200, manager.results.catalog())
                return
            if self.command == 'GET' and path.startswith(f'{prefix}/result-catalog/') and path.endswith('/processes'):
                pieces = path[len(f'{prefix}/result-catalog/'):].split('/')
                if len(pieces) != 2:
                    raise KeyError('成果不存在')
                self.send_json(200, {'processes': manager.results.processes(pieces[0])})
                return
            if self.command == 'GET' and path.startswith(f'{prefix}/results/') and path.endswith('/scheme-conditions'):
                self.send_json(200, manager.results.scheme_request(path[len(f'{prefix}/results/'):-len('/scheme-conditions')]))
                return
            if self.command == 'GET' and path.startswith(f'{prefix}/results/') and path.endswith('/parameter-snapshot'):
                self.send_json(200, manager.parameter_schemes.from_result(path[len(f'{prefix}/results/'):-len('/parameter-snapshot')]))
                return
            if self.command == 'GET' and path.startswith(f'{prefix}/results/'):
                identifier = path[len(f'{prefix}/results/'):]
                self.send_json(200, {'result': manager.results.result(identifier), 'status': manager.results.status(identifier), 'processes': manager.results.processes(identifier)})
                return
            if path == f'{prefix}/river-sections':
                if self.command == 'GET':
                    payload = manager.river_sections.list(argument('resultId'), argument('processId'))
                elif self.command == 'POST':
                    payload = manager.river_sections.create(self.read_body())
                else:
                    raise KeyError('断面接口不存在')
                self.send_json(200, payload)
                return
            if self.command == 'POST' and path == f'{prefix}/river-sections/locate':
                self.send_json(200, manager.river_sections.locate(self.read_body()))
                return
            if self.command == 'POST' and path.startswith(f'{prefix}/river-sections/') and path.endswith('/rename'):
                pieces = path[len(f'{prefix}/river-sections/'):].split('/')
                if len(pieces) != 2:
                    raise KeyError('断面不存在')
                self.send_json(200, manager.river_sections.rename(pieces[0], self.read_body()))
                return
            if path == f'{prefix}/result-section-queries' and self.command == 'POST':
                self.send_json(202, manager.river_sections.query(self.read_body()))
                return
            if path.startswith(f'{prefix}/result-section-queries/') and self.command == 'GET':
                self.send_json(200, manager.river_sections.query_result(path[len(f'{prefix}/result-section-queries/'):]))
                return
            if self.command == 'POST' and path.startswith(f'{prefix}/result-metadata/'):
                self.send_json(200, manager.results.update(path[len(f'{prefix}/result-metadata/'):], self.read_body()))
                return
            if self.command == 'GET' and path.startswith(f'{prefix}/result-metadata/'):
                self.send_json(200, manager.results.annotation(path[len(f'{prefix}/result-metadata/'):]))
                return
            if path.startswith(f"{prefix}/states/"):
                pieces = path[len(f"{prefix}/states/"):].split("/")
                if len(pieces) != 2:
                    raise KeyError("状态接口不存在")
                state_id, operation = pieces
                if self.command == "POST" and operation == "metadata":
                    payload = manager.states.update(state_id, self.read_body())
                elif self.command == "GET" and operation == "checkpoint":
                    payload = manager.states.checkpoint(state_id)
                else:
                    raise KeyError("状态接口不存在")
                self.send_json(200, payload)
                return
            if self.command == "GET" and path == f"{prefix}/states":
                rows = [read_json(p) for p in sorted((manager.runtime_root / "states").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)]
                from science_context import model_implementation_identity
                from implementation_versions import frozen_worker
                available={model_implementation_identity()['modelImplementationSHA256']:True}
                for row in rows:
                    identity=row.get('modelImplementationSHA256') or row.get('lineage',{}).get('modelImplementationSHA256')
                    if identity not in available:
                        try: frozen_worker(identity,manager.runtime_root);available[identity]=True
                        except (ValueError,KeyError,FileNotFoundError): available[identity]=False
                    row['implementationReplayable']=available[identity]
                self.send_json(200, {"states": [row for row in rows if manager.available_artifact(row["sourceTaskId"])]})
                return
            if self.command == "GET" and path == f"{prefix}/parameter-versions":
                rows = [read_json(p) for p in (manager.runtime_root / "parameters").glob("*.json")]
                self.send_json(200, {"versions": [row for row in rows if manager.available_artifact(row["taskId"])]})
                return
            if self.command == 'GET' and path.startswith(f'{prefix}/parameter-versions/') and path.endswith('/original-plan'):
                from original_calibration_plan import original_plan_payload
                pieces = path[len(f'{prefix}/parameter-versions/'):].split('/')
                if len(pieces) != 2:
                    raise KeyError('原方案设置不存在')
                self.send_json(200, original_plan_payload(pieces[0], manager.runtime_root, products=manager.products,
                                                         operation=argument('operation', 'search')))
                return
            if path.startswith("/api/") or self.command != "GET":
                raise KeyError("接口不存在")
            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (frontend / relative).resolve()
            try:
                target.relative_to(frontend)
            except ValueError:
                raise ValueError("静态文件路径越界")
            if not target.is_file():
                raise KeyError("找不到前端构建，请先运行统一启动脚本")
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", (mimetypes.guess_type(target.name)[0] or "application/octet-stream") + ("; charset=utf-8" if target.suffix in {".html", ".js", ".json", ".css"} else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache" if target.suffix in {".html", ".json"} else "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self):
            try:
                self.route()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # The browser may leave after a successful commit; do not send a
                # second HTTP response or turn a disconnected client into a 500.
                return
            except (ValueError, TypeError) as error:
                self.send_json(400, {"error": str(error)})
            except (KeyError, FileNotFoundError) as error:
                self.send_json(404, {"error": str(error)})
            except RuntimeError as error:
                self.send_json(409, {"error": str(error)})
            except Exception as error:
                traceback.print_exc()
                self.send_json(500, {"error": f"{type(error).__name__}: {error}"})

        do_GET = dispatch
        do_POST = dispatch

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=SETTINGS['port'])
    parser.add_argument("--frontend", default=str(SETTINGS['frontend']))
    parser.add_argument("--verbose-requests", action="store_true", help="显示全部 HTTP 访问日志")
    parser.add_argument("--open-browser", action="store_true", help="服务就绪后打开平台页面")
    args = parser.parse_args()
    # Reserve the port before recovering unfinished jobs in the runtime.
    server = PlatformHTTPServer((args.host, args.port), BaseHTTPRequestHandler)
    manager = None
    ownership = WorkspaceLock(RUNTIME)
    try:
        ownership.__enter__()
        workspace_identity(RUNTIME)
        manager = TaskManager()
        from numeric_runtime import start as start_numeric_runtime
        start_numeric_runtime(RUNTIME)
        print("Preparing hydrological database from existing observations...", flush=True)
        manager.database.import_existing()
        from delivered_schemes import migrate_delivered_schemes
        for issue in migrate_delivered_schemes(manager.schemes, manager.products):
            print('历史记录待关联：' + json.dumps(issue, ensure_ascii=False), flush=True)
        server.RequestHandlerClass = handler(manager, Path(args.frontend).resolve(), verbose_requests=args.verbose_requests)
        address = f"http://{args.host}:{server.server_port}"
        print(f"Unified XAJ platform: {address}", flush=True)
        if args.open_browser:
            try:
                opened = webbrowser.open(address)
            except Exception:
                opened = False
            if not opened:
                print(f"浏览器未自动打开，请手动访问：{address}", file=sys.stderr, flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if manager is not None:
                manager.close()
        finally:
            ownership.__exit__()


if __name__ == "__main__":
    main()
