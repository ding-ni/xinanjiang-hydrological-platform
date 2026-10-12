"""Verify a clean release and a nonempty workspace across backup and restore."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "开发产物/同事方案完整接入验收_20261012"
RELEASE = ROOT / "交付包/统一平台-1.11.0-20261013"
ARCHIVE = Path(str(RELEASE) + ".zip")
WORKSPACE = ROOT / "runtime-20261012"
BACKUP = ROOT / "交付包/升级工作区备份-1.11.0-20261013"
PYTHON = ROOT / "backend/.venv/Scripts/python.exe"
SOURCE_TASK = None
ACTIVE = {"queued", "running", "cancelling"}
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
from package_platform import verify as verify_program
from platform_config import APP_VERSION
from science_context import model_implementation_identity
from verify_delivery_state import checksum, inspect
from workspace_backup import create_backup, digest, restore_backup, verify_backup
from workspace_service import WorkspaceLock


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def emit(stage, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=False), flush=True)


def inactive(workspace):
    ongoing = []
    for collection in ("tasks", "input-tasks"):
        for path in (workspace / collection).glob("*/status.json"):
            status = read(path)
            if status.get("status") in ACTIVE:
                ongoing.append({"collection": collection, "taskId": status.get("taskId"), "status": status["status"]})
    if ongoing:
        raise RuntimeError("工作区仍有活动任务：" + json.dumps(ongoing, ensure_ascii=False))


def preflight():
    if not SOURCE_TASK:
        raise ValueError("请指定已完成的冻结重放任务编号 --source-task")
    request = read(WORKSPACE / "tasks" / SOURCE_TASK / "request.json")
    implementation = model_implementation_identity()["modelImplementationSHA256"]
    expected = request["lineage"]["modelImplementationSHA256"]
    return {"installation": str(ROOT), "interpreter": str(PYTHON),
            "actualInterpreter": sys.executable,
            "release": str(RELEASE), "releaseExists": RELEASE.exists(),
            "zip": str(ARCHIVE), "zipExists": ARCHIVE.exists(),
            "workspace": str(WORKSPACE), "backup": str(BACKUP), "backupExists": BACKUP.exists(),
            "sourceTaskId": SOURCE_TASK, "recordedImplementationSHA256": expected,
            "currentImplementationSHA256": implementation, "sourceImplementationMatched": implementation == expected,
            "ports": [18893, 18894], "protectedPorts": [8878, 18890, 18892]}


def verify_zip(path, directory, manifest):
    expected = {row["path"] for row in manifest["files"]} | {"release-manifest.json"}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(names) != len(expected) or set(names) != expected:
            raise AssertionError("ZIP 文件列表与发布清单不一致")
        if len({name.casefold() for name in names}) != len(names):
            raise AssertionError("ZIP 有大小写重复项")
        for item in entries:
            relative = Path(item.filename)
            if item.is_dir() or relative.is_absolute() or ".." in relative.parts or "\\" in item.filename or ":" in item.filename:
                raise AssertionError("ZIP 包含无效路径")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise AssertionError("ZIP 包含外部链接")
        if archive.read("release-manifest.json") != (directory / "release-manifest.json").read_bytes():
            raise AssertionError("ZIP 的发布清单内容不一致")
        for row in manifest["files"]:
            entry = archive.getinfo(row["path"])
            hasher = hashlib.sha256()
            with archive.open(entry) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if entry.file_size != row["bytes"] or hasher.hexdigest() != row["sha256"]:
                raise AssertionError("ZIP 文件摘要不一致：" + row["path"])
    return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size, "entries": len(expected),
            "onlyManifestFiles": True, "allFileSHA256Matched": True}


def archive_and_extract(target):
    verification = verify_program(RELEASE)
    if verification["version"] != APP_VERSION:
        raise AssertionError("发布版本与当前源码版本不符")
    manifest = read(RELEASE / "release-manifest.json")
    expected = {row["path"] for row in manifest["files"]} | {"release-manifest.json"}
    actual = {path.relative_to(RELEASE).as_posix() for path in RELEASE.rglob("*") if path.is_file()}
    if actual != expected:
        raise AssertionError("发布目录存在未登记或缺失文件")
    if not ARCHIVE.exists():
        with zipfile.ZipFile(ARCHIVE, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in sorted(expected):
                archive.write(RELEASE / name, arcname=name)
    zip_verification = verify_zip(ARCHIVE, RELEASE, manifest)
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(ARCHIVE) as archive:
        archive.extractall(target)
    extracted_verification = verify_program(target)
    extracted_actual = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
    if extracted_actual != expected:
        raise AssertionError("解压目录文件列表不一致")
    return {"program": verification, "zip": zip_verification, "extracted": extracted_verification}


def api(port, path, body=None, timeout=180):
    request = Request(f"http://127.0.0.1:{port}/api/model/{path}",
                      data=None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def closed_port(port):
    receipt = {"port": port, "address": "127.0.0.1"}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            receipt.update({"socketConnected": True, "connectError": 0})
    except OSError as error:
        receipt.update({"socketConnected": False, "connectError": getattr(error, "winerror", None) or error.errno,
                        "socketMessage": str(error)})
    completed = subprocess.run(["netstat.exe", "-ano", "-p", "tcp"], capture_output=True,
                               text=True, errors="replace", timeout=15, check=True)
    rows = []
    for line in completed.stdout.splitlines():
        pieces = line.split()
        if len(pieces) >= 5 and pieces[0] == "TCP" and pieces[1].rsplit(":", 1)[-1] == str(port):
            rows.append({"local": pieces[1], "remote": pieces[2], "state": pieces[3], "pid": pieces[4]})
    listeners = [row for row in rows if row["state"] == "LISTENING" or row["remote"] in {"0.0.0.0:0", "[::]:0"}]
    receipt.update({"netstatRows": rows, "listenerCount": len(listeners),
                    "closed": not listeners and not receipt.get("socketConnected"),
                    "verificationMethod": "NETSTAT_NO_LISTENER_AND_SOCKET_NOT_CONNECTED"})
    return receipt


class OwnedService:
    def __init__(self, installation, workspace, port, folder, report):
        self.installation, self.workspace, self.port = installation, workspace, port
        self.folder, self.report = folder, report
        self.process, self.log = None, None
        self.record = {"installation": str(installation), "workspace": str(workspace), "port": port}
        self.report["services"].append(self.record)

    def start(self):
        inactive(self.workspace)
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                raise RuntimeError(f"验收端口 {self.port} 已占用；保留现有进程")
        environment = {key: value for key, value in os.environ.items() if key not in {"XAJ_CONFIG", "XAJ_RUNTIME_DIR", "PYTHONPATH"}}
        environment.update({"XAJ_RUNTIME_DIR": str(self.workspace), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        command = [str(PYTHON), "-B", "-u", str(self.installation / "backend/server.py"),
                   "--port", str(self.port), "--frontend", str(self.installation / "frontend-demo/app/dist-unified")]
        log_path = self.folder / f"service-{self.port}.log"
        self.log = log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(command, cwd=self.installation / "backend", env=environment,
                                        stdout=self.log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.record.update({"pid": self.process.pid, "command": command, "log": str(log_path), "owned": True})
        emit("service-start", port=self.port, pid=self.process.pid)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"验收服务 {self.port} 已退出，参见 {log_path}")
            try:
                api(self.port, "health", timeout=2)
                self.record["ready"] = True
                return self
            except (HTTPError, URLError, TimeoutError, OSError):
                time.sleep(0.5)
        raise TimeoutError(f"验收服务 {self.port} 启动超时")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            # Only the Popen handle created by this helper authorizes this PID tree.
            if os.name == "nt":
                stopped = subprocess.run(["taskkill.exe", "/PID", str(self.process.pid), "/T", "/F"],
                                         capture_output=True, text=True, errors="replace", timeout=20)
                self.record["stopExitCode"] = stopped.returncode
                self.record["stopOutput"] = (stopped.stdout + stopped.stderr).strip()
            else:
                self.process.terminate()
            self.process.wait(timeout=20)
        if self.log is not None:
            self.log.close()
        if self.process is not None:
            self.record["exitCode"] = self.process.poll()
            self.record["stopped"] = self.process.poll() is not None
            emit("service-stop", port=self.port, pid=self.process.pid, stopped=self.record["stopped"])


def durable_capture(mode, port, installation, baseline, output, log):
    # All durable collections and every saved state are compared before replay.
    result = {"status": "failed", "mode": mode,
              "inspector": str(ROOT / "scripts/verify_delivery_state.py") + ":inspect"}
    try:
        _api, current = inspect(f"http://127.0.0.1:{port}", installation.resolve())
        if current["version"] != APP_VERSION or not current["taskCount"] or not current["checkpoints"]:
            raise AssertionError("版本、任务或非空状态不符")
        result["current"] = current
        result["checkpointCount"] = len(current["checkpoints"])
        if mode == "capture":
            if baseline.exists():
                raise AssertionError("应使用新的基线文件")
            save(baseline, current)
        else:
            original = read(baseline)
            result["equal"] = {key: original[key] == current[key]
                               for key in ("version", "workspaceId", "collections", "results", "checkpoints")}
            if not all(result["equal"].values()):
                raise AssertionError("资料恢复比对不一致：" + json.dumps(result["equal"]))
        result["status"] = "passed"
    except BaseException as error:
        result["failure"] = str(error)
        result["traceback"] = traceback.format_exc()
        raise
    finally:
        save(output, result)
        save(log, result)
    emit("durable-" + mode, taskCount=result["current"]["taskCount"],
         collections=len(result["current"]["collections"]), results=len(result["current"]["results"]),
         checkpoints=len(result["current"]["checkpoints"]))
    return {"path": str(output), "status": "passed", "current": result["current"], **({"equal": result["equal"]} if "equal" in result else {})}


def replay(port, restored, folder):
    original_request = read(restored / "tasks" / SOURCE_TASK / "request.json")
    original_result = api(port, f"tasks/{SOURCE_TASK}/result")
    request = copy.deepcopy(original_request)
    request["requestId"] = "RESTORE-REPLAY-" + uuid4().hex
    if {key: value for key, value in request.items() if key != "requestId"} != original_request:
        raise AssertionError("重放改变了原请求条件")
    implementation = api(port, "science-versions")["modelImplementationSHA256"]
    if implementation != original_request["lineage"]["modelImplementationSHA256"]:
        raise AssertionError("最终实现摘要与原冻结任务不同，须由主任务核查")
    save(folder / "重放冻结请求.json", request)
    task = api(port, "tasks", request)
    task_id = task["taskId"]
    emit("restored-replay-start", taskId=task_id, sourceTaskId=SOURCE_TASK)
    deadline, next_report = time.monotonic() + 1200, time.monotonic() + 20
    while time.monotonic() < deadline:
        status = api(port, f"tasks/{task_id}")
        if status["status"] not in ACTIVE:
            if status["status"] != "completed":
                raise RuntimeError("恢复后重放计算失败：" + json.dumps(status, ensure_ascii=False))
            break
        if time.monotonic() >= next_report:
            emit("restored-replay-progress", taskId=task_id, status=status["status"],
                 progress=status.get("progress"), stageLabel=status.get("stageLabel"))
            next_report = time.monotonic() + 20
        time.sleep(0.5)
    else:
        raise TimeoutError("恢复后原冻结请求重放超时")
    repeated = api(port, f"tasks/{task_id}/result")
    fields = ("hydrograph", "hydrographs", "bestParameters", "calibrationMetrics", "validationMetrics",
              "eventMetrics", "science", "scienceSnapshot", "continuousOrigin", "actualWarmupHours",
              "statePolicy", "productionEligible")
    equal = {name: original_result.get(name) == repeated.get(name) for name in fields}
    equal["sourceResultUnchanged"] = api(port, f"tasks/{SOURCE_TASK}/result") == original_result
    lineage_keys = ("inputBundleVersionId", "inputBundleManifestSHA256", "forcingVersion", "observationVersion",
                    "routingVersionId", "routingManifestSHA256", "modelImplementationSHA256",
                    "scienceVersionId", "scienceConfigurationSHA256", "petConfigurationSHA256", "petMode")
    equal["scientificLineage"] = all(original_result["lineage"].get(key) == repeated["lineage"].get(key) for key in lineage_keys)
    result = {"sourceTaskId": SOURCE_TASK, "taskId": task_id, "equal": equal,
              "hydrographSHA256": checksum(repeated["hydrograph"]),
              "hydrographsSHA256": checksum(repeated["hydrographs"]), "science": repeated["science"],
              "continuousOrigin": repeated["continuousOrigin"], "actualWarmupHours": repeated["actualWarmupHours"],
              "productionEligible": repeated["productionEligible"],
              "meaning": "工作区恢复的可复现性验收；不计为新增泛化或生产资格验证"}
    save(folder / "恢复后冻结请求重放.json", result)
    if not all(equal.values()):
        raise AssertionError("恢复后重放存在差异：" + json.dumps(equal, ensure_ascii=False))
    emit("restored-replay-passed", taskId=task_id, equal=equal)
    return result


def execute():
    preparation = preflight()
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise RuntimeError("本验收应使用现有 backend/.venv 的解释器")
    if not preparation["sourceImplementationMatched"]:
        raise RuntimeError("最终实现摘要与原任务不符，须由主任务核查；未执行迁移")
    if not RELEASE.is_dir() or BACKUP.exists():
        raise RuntimeError("发布目录未就绪，或命名备份已存在；未覆盖任何已有资料")
    inactive(WORKSPACE)
    folder = ARTIFACTS / ("发布迁移验收 中文路径 " + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
    folder.mkdir(exist_ok=False)
    target = folder / "解压程序 含空格"
    restored = folder / "恢复工作区 含空格"
    baseline = folder / "原工作区基线.json"
    report_path = folder / "发布与迁移验收.json"
    index_folder = ARTIFACTS / "发布迁移验收"
    report = {"status": "running", "preflight": preparation, "artifact": str(folder), "services": []}
    services = []
    save(report_path, report)
    emit("acceptance-start", artifact=str(folder))
    try:
        report["releaseVerification"] = archive_and_extract(target)
        save(report_path, report)
        emit("release-verified", files=report["releaseVerification"]["program"]["files"])
        source = OwnedService(ROOT, WORKSPACE, 18893, folder, report)
        services.append(source)
        source.start()
        report["sourceCapture"] = durable_capture("capture", 18893, ROOT, baseline,
                                                   folder / "原工作区捕获.json", folder / "原工作区捕获.log")
        source.stop()
        with WorkspaceLock(WORKSPACE):
            inactive(WORKSPACE)
            report["backup"] = create_backup(WORKSPACE, BACKUP)
        manifest = verify_backup(BACKUP)
        report["backupVerification"] = {"path": str(BACKUP), "files": len(manifest["files"]),
                                        "workspaceId": manifest["workspaceId"], "bytes": manifest["bytes"],
                                        "manifestSHA256": digest(BACKUP / "backup-manifest.json"), "verified": True}
        report["restore"] = restore_backup(BACKUP, restored)
        emit("backup-restored", files=len(manifest["files"]), backup=str(BACKUP), workspace=str(restored))
        save(report_path, report)
        destination = OwnedService(target, restored, 18894, folder, report)
        services.append(destination)
        destination.start()
        report["restoredComparison"] = durable_capture("compare", 18894, target, baseline,
                                                       folder / "恢复工作区比较.json", folder / "恢复工作区比较.log")
        report["frozenRequestReplay"] = replay(18894, restored, folder)
        report["sourceRequestSHA256"] = digest(WORKSPACE / "tasks" / SOURCE_TASK / "request.json")
        report["sourceResultSHA256"] = digest(WORKSPACE / "tasks" / SOURCE_TASK / "result.json")
        backup_rows = {row["path"]: row["sha256"] for row in manifest["files"]}
        for name in ("request.json", "result.json"):
            key = f"tasks/{SOURCE_TASK}/{name}"
            if digest(WORKSPACE / key) != backup_rows[key]:
                raise AssertionError("原冻结任务被改变：" + key)
        report["originalFrozenTaskUnchanged"] = True
        report["finalZipSHA256"] = digest(ARCHIVE)
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["failure"] = str(error)
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        cleanup_errors = []
        for service in reversed(services):
            try:
                service.stop()
            except Exception as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            report["cleanupErrors"] = cleanup_errors
            report["status"] = "failed"
        report["testPortClosed"] = {str(port): closed_port(port) for port in (18893, 18894)}
        if not all(row["closed"] for row in report["testPortClosed"].values()):
            report["status"] = "failed"
        save(report_path, report)
        index_folder.mkdir(exist_ok=True)
        save(index_folder / "发布与迁移验收.json", {**report, "detailedReport": str(report_path)})
        emit("acceptance-finished", status=report["status"], report=str(report_path), backup=str(BACKUP))
    if report["status"] != "passed":
        raise RuntimeError("服务清理未通过，请核查验收记录")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="仅在主任务确认发布包就绪后执行")
    parser.add_argument("--source-task", required=True)
    parser.add_argument("--release", type=Path, default=RELEASE)
    parser.add_argument("--runtime", type=Path, default=WORKSPACE)
    parser.add_argument("--backup", type=Path, default=BACKUP)
    parser.add_argument("--output", type=Path, default=ARTIFACTS)
    options = parser.parse_args()
    SOURCE_TASK = options.source_task
    RELEASE = options.release.resolve()
    ARCHIVE = Path(str(RELEASE) + ".zip")
    WORKSPACE = options.runtime.resolve()
    BACKUP = options.backup.resolve()
    ARTIFACTS = options.output.resolve()
    if options.execute:
        execute()
    else:
        emit("read-only-preparation", **preflight())
