"""Immutable, local-only input source and bundle registry (no model equations)."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from common import MODEL, RUNTIME, clean, now, read_json, safe_task, write_json


DEFAULT_BUNDLE_VERSION_ID = "NANANXI_QC_20260819_CONTROL_V2"
BASELINE_FORCING_VERSION = "precipitation_qc_20260819"
BASELINE_OBSERVATION_VERSION = "xufan_outlet_hourly_qc_20260819"
BASELINE_MODEL_INPUT_VERSION = "nananxi-qc20260819-control-split-v2"
PROJECT_ID, RESOLUTION_ID = "nananxi-xufan", "1km"
TIME_CONVENTION = "SOURCE_LOCAL_NAIVE_HOURLY"
MAX_HOURS, MAX_VALUES, MAX_SOURCE_BYTES = 8784, 200000, 2 * 1024 * 1024
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,100}$")
SOURCE_ID = re.compile(r"^SRC-[A-Za-z0-9_-]{1,96}$")
PRODUCT_ID = re.compile(r"^INPUT-[A-Za-z0-9_-]{1,94}$")
DATA_TASK_ID = re.compile(r"^DATA-[A-Za-z0-9_-]{1,95}$")
HOURLY = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:00(?::00)?$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
BASELINE_PATHS = {
    "grids": "data_interface/runoff_2017/grid_static.csv",
    "geometry": "data_interface/spatial/南安溪1公里有效格点.geojson",
    "topology": "data_interface/spatial/nan_an_topology_control_split_v2.json",
    "mapping": "data_interface/spatial/grid_to_unit_weight_control_split_v2.csv",
    "flow": "data_interface/observations/xufan_outlet_hourly_qc.csv",
    "events": "data_interface/event_catalog/nananxi_flood_event_catalog_qc_20260819.json",
    "quality": "config/event_quality_config.json",
}
BASELINE_PRECIPITATION = "data_interface/precipitation_qc_20260819/1km_hourly"


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_digest(value):
    return hashlib.sha256(json.dumps(clean(value), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def hourly_time(value):
    if not isinstance(value, str) or not HOURLY.fullmatch(value):
        raise ValueError("资料时刻须为无时区的源资料本地整点 ISO 时间")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("资料时刻无效") from exc
    if not 1960 <= result.year <= 2100:
        raise ValueError("资料年份须为 1960 至 2100，实际可用范围按来源检查")
    return result


def processing_window(start, end):
    first, last = hourly_time(start), hourly_time(end)
    hours = int((last - first).total_seconds() / 3600) + 1
    if not 1 <= hours <= MAX_HOURS:
        raise ValueError(f"输入处理窗口须为 1 至 {MAX_HOURS} 个整小时（闭区间）")
    return first, last, hours


def _case(payload):
    if payload.get("projectId") != PROJECT_ID or payload.get("resolutionId") != RESOLUTION_ID:
        raise ValueError("输入产品仅登记 nananxi-xufan:1km，不能套用其他流域或尺度")


def _text(value, label, maximum, required=True):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum:
        raise ValueError(f"{label}须为非空文本，最多 {maximum} 字符")
    return value.strip()


def _fields(payload, allowed):
    unknown = set(payload).difference(allowed)
    if unknown:
        raise ValueError("资料请求包含未登记字段（不接受文件路径）：" + ",".join(sorted(unknown)))


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


class InputProducts:
    def __init__(self, model_root=None, runtime_root=None):
        self.model_root = Path(MODEL if model_root is None else model_root).resolve()
        self.runtime_root = Path(RUNTIME if runtime_root is None else runtime_root).resolve()
        self.inputs_root = self.runtime_root / "inputs"
        self.sources_root = self.inputs_root / "sources"
        self.products_root = self.inputs_root / "products"
        self.registry_root = self.inputs_root / "registry"
        self.tasks_root = self.runtime_root / "input-tasks"
        self._lock = threading.RLock()

    def _path(self, reference):
        if not isinstance(reference, dict) or set(reference) != {"root", "path"}:
            raise ValueError("输入文件引用格式无效")
        root = {"model": self.model_root, "runtime": self.runtime_root}.get(reference["root"])
        raw = reference.get("path")
        if root is None or not isinstance(raw, str) or not raw or "\\" in raw:
            raise ValueError("输入文件引用根或路径无效")
        relative = Path(raw)
        if relative.is_absolute() or ":" in raw or ".." in relative.parts:
            raise ValueError("输入文件引用不得越界")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("输入文件引用不得通过符号链接越界")
        return path

    def reference(self, path):
        path = Path(path).resolve()
        if path.is_relative_to(self.model_root):
            return {"root": "model", "path": path.relative_to(self.model_root).as_posix()}
        if path.is_relative_to(self.runtime_root):
            return {"root": "runtime", "path": path.relative_to(self.runtime_root).as_posix()}
        raise ValueError("输入产物不能引用工作区之外的文件")

    def file_record(self, path):
        path = Path(path).resolve()
        return {**self.reference(path), "sha256": file_sha256(path), "bytes": path.stat().st_size}

    def _verify_files(self, manifest):
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("输入清单没有可验证文件")
        seen = set()
        for entry in files:
            if not isinstance(entry, dict) or set(entry) != {"root", "path", "sha256", "bytes"}:
                raise ValueError("输入文件清单无效")
            path = self._path({"root": entry["root"], "path": entry["path"]})
            key = (entry["root"], entry["path"])
            if key in seen or not SHA256.fullmatch(str(entry["sha256"])):
                raise ValueError("输入文件重复或摘要无效")
            seen.add(key)
            if not path.is_file() or path.stat().st_size != entry["bytes"] or file_sha256(path) != entry["sha256"]:
                raise ValueError("输入文件缺失、大小或 SHA256 与冻结清单不一致：" + entry["path"])
        return seen

    def _baseline_manifest(self):
        paths = {role: {"root": "model", "path": value} for role, value in BASELINE_PATHS.items()}
        precipitation = {"root": "model", "path": BASELINE_PRECIPITATION}
        files = [self.file_record(self._path(reference)) for reference in paths.values()]
        nc_files = sorted(self._path(precipitation).glob("nananxi_precip_hourly_1km_*.nc"))
        if not nc_files:
            raise FileNotFoundError("固定基线降雨产品不存在")
        files.extend(self.file_record(path) for path in nc_files)
        years = [int(path.stem.rsplit("_", 1)[1]) for path in nc_files]
        return {"schemaVersion": "1.0", "bundleVersionId": DEFAULT_BUNDLE_VERSION_ID,
                "projectId": PROJECT_ID, "resolutionId": RESOLUTION_ID, "operation": "fixedBaseline",
                "name": "交付固定 QC 20260819 / 控制切分 v2", "sourceType": "model-qc",
                "sourceLabel": "合作者交付固定 QC；参考诊断资料，未获得生产资格",
                "forcingVersion": BASELINE_FORCING_VERSION,
                "observationVersion": BASELINE_OBSERVATION_VERSION,
                "modelInputVersion": BASELINE_MODEL_INPUT_VERSION, "productionEligible": False,
                "timeConvention": TIME_CONVENTION, "paths": paths, "precipitationDirectory": precipitation,
                "files": files, "coverage": {"years": years},
                "qualityNotes": ["保持原固定版本及原始路径；不会自动切换 current/latest。",
                                 "未提供已登记 QC 逐站雨量、水位和实测 PET；生产资格未验收。"]}

    @staticmethod
    def _public(manifest, digest, registration=None):
        keys = ("schemaVersion", "bundleVersionId", "projectId", "resolutionId", "name", "operation",
                "sourceType", "sourceLabel", "forcingVersion", "observationVersion", "modelInputVersion",
                "productionEligible", "timeConvention", "coverage", "qualityNotes", "summary", "createdAt",
                "sourceId", "sourceOrigin", "parentBundleVersionId", "sourceTaskId",
                "targetCoordinatePolicy", "forcingValueDtype", "sourceReplayOnly", "sourceQcPolicy",
                "eventQualityConfigurationSHA256", "sourceQcOrigin", "recordQueryScope")
        result = {key: copy.deepcopy(manifest[key]) for key in keys if key in manifest}
        result.update(manifestSHA256=digest, registered=True, default=manifest["bundleVersionId"] == DEFAULT_BUNDLE_VERSION_ID)
        if registration:
            result["registeredAt"] = registration["registeredAt"]
        return result

    def _descriptor(self, manifest, digest, manifest_path=None, verify=True, registration=None):
        _case(manifest)
        if (manifest.get("schemaVersion") != "1.0" or manifest.get("timeConvention") != TIME_CONVENTION
                or any(not isinstance(manifest.get(key), str) or not manifest[key]
                       for key in ("bundleVersionId", "forcingVersion", "observationVersion", "modelInputVersion", "sourceLabel"))):
            raise ValueError("输入产品版本、来源或时间约定不完整")
        if manifest.get("productionEligible") is not False or manifest.get("sourceType") != "model-qc":
            raise ValueError("本轮输入产品必须是未授予生产资格的模型 QC 通道")
        if set(manifest.get("paths", {})) != set(BASELINE_PATHS):
            raise ValueError("输入清单路径角色不完整")
        known = self._verify_files(manifest) if verify else {(row["root"], row["path"]) for row in manifest["files"]}
        paths = {role: self._path(value) for role, value in manifest["paths"].items()}
        if any((value["root"], value["path"]) not in known for value in manifest["paths"].values()):
            raise ValueError("输入角色未被文件摘要清单覆盖")
        directory = self._path(manifest["precipitationDirectory"])
        precipitation_files = sorted(directory.glob("nananxi_precip_hourly_1km_*.nc"))
        if not precipitation_files or any((self.reference(path)["root"], self.reference(path)["path"]) not in known
                                         for path in precipitation_files):
            raise ValueError("降雨目录为空或含未登记的 NC 文件")
        result = {key: manifest[key] for key in ("bundleVersionId", "forcingVersion", "observationVersion", "modelInputVersion")}
        result.update(manifestSHA256=digest, manifestPath=manifest_path, precipitationDir=directory,
                      paths=paths, files=copy.deepcopy(manifest["files"]), public=self._public(manifest, digest, registration),
                      manifest=copy.deepcopy(manifest), qualityNotes=copy.deepcopy(manifest.get("qualityNotes", [])),
                      sourceLabel=manifest.get("sourceLabel"), sourceType="model-qc")
        return result

    def resolve(self, bundle_version_id=None, verify=True):
        bundle_version_id = DEFAULT_BUNDLE_VERSION_ID if bundle_version_id is None else bundle_version_id
        if bundle_version_id == DEFAULT_BUNDLE_VERSION_ID:
            # First use pins the exact baseline files. A changed source file
            # must not silently receive a new digest under the same version id.
            path = self.inputs_root / "fixed-baseline-manifest.json"
            with self._lock:
                if path.exists():
                    pinned = read_json(path)
                    manifest = pinned.get("manifest", {})
                    digest = manifest_digest(manifest)
                    if (pinned.get("schemaVersion") != "1.0" or pinned.get("manifestSHA256") != digest
                            or manifest.get("bundleVersionId") != DEFAULT_BUNDLE_VERSION_ID
                            or manifest.get("forcingVersion") != BASELINE_FORCING_VERSION
                            or manifest.get("observationVersion") != BASELINE_OBSERVATION_VERSION
                            or manifest.get("modelInputVersion") != BASELINE_MODEL_INPUT_VERSION):
                        raise ValueError("固定基线登记清单被改变；不能重新定义同一版本")
                    return self._descriptor(manifest, digest, path, verify=verify)
                manifest = self._baseline_manifest()
                digest = manifest_digest(manifest)
                descriptor = self._descriptor(manifest, digest, path, verify=True)
                write_json(path, {"schemaVersion": "1.0", "manifestSHA256": digest, "manifest": manifest})
                return descriptor
        if not isinstance(bundle_version_id, str) or not PRODUCT_ID.fullmatch(bundle_version_id):
            raise ValueError("输入版本编号无效")
        record_path = self.registry_root / f"{bundle_version_id}.json"
        if not record_path.is_file():
            raise KeyError("输入产品尚未显式登记")
        registration = read_json(record_path)
        manifest_path = self.products_root / bundle_version_id / "manifest.json"
        digest = file_sha256(manifest_path)
        if (registration.get("bundleVersionId") != bundle_version_id or registration.get("manifestSHA256") != digest
                or not DATA_TASK_ID.fullmatch(str(registration.get("sourceTaskId", "")))):
            raise ValueError("输入登记记录与不可变产品清单不一致")
        manifest = read_json(manifest_path)
        if manifest.get("bundleVersionId") != bundle_version_id or manifest.get("sourceTaskId") != registration["sourceTaskId"]:
            raise ValueError("输入产品版本或来源任务与登记不一致")
        task_folder = safe_task(self.tasks_root, registration["sourceTaskId"])
        status, result = read_json(task_folder / "status.json"), read_json(task_folder / "result.json")
        request = read_json(task_folder / "request.json")
        if (status.get("taskId") != registration["sourceTaskId"] or status.get("status") not in {"completed", "archived"}
                or result.get("taskId") != registration["sourceTaskId"]
                or result.get("productVersionId") != bundle_version_id or result.get("manifestSHA256") != digest
                or manifest.get("requestSHA256") != manifest_digest(request)
                or manifest.get("operation") != request.get("operation")
                or result.get("productionEligible") is not False):
            raise ValueError("输入产品来源任务非成功状态或结果摘要错配")
        return self._descriptor(manifest, digest, manifest_path, verify=verify, registration=registration)

    def list_versions(self):
        versions = [self.resolve()["public"]]
        for path in sorted(self.registry_root.glob("INPUT-*.json")):
            versions.append(self.resolve(path.stem)["public"])
        return {"schemaVersion": "1.0", "defaultBundleVersionId": DEFAULT_BUNDLE_VERSION_ID, "versions": versions}

    def register_source(self, payload, *, origin=None):
        if not isinstance(payload, dict):
            raise ValueError("源资料须为 JSON 对象")
        _fields(payload, {"schemaVersion", "projectId", "resolutionId", "name", "provenanceNote", "unit", "crs",
                          "timeConvention", "times", "stations"})
        _case(payload)
        if payload.get("schemaVersion") != "1.0" or payload.get("unit") != "mm" or payload.get("crs") != "EPSG:4326" or payload.get("timeConvention") != TIME_CONVENTION:
            raise ValueError("源资料格式须为1.0、逐时量mm、EPSG:4326及源资料本地无时区整点")
        name = _text(payload.get("name"), "资料名", 200)
        provenance = _text(payload.get("provenanceNote"), "来源核对说明", 2000)
        times, stations = payload.get("times"), payload.get("stations")
        if not isinstance(times, list) or not 1 <= len(times) <= MAX_HOURS:
            raise ValueError("源资料时轴长度越界")
        parsed = [hourly_time(value) for value in times]
        if any(last - first != timedelta(hours=1) for first, last in zip(parsed, parsed[1:])):
            raise ValueError("源资料时轴须递增、唯一、连续整小时；缺测保留 null 记录")
        if not isinstance(stations, list) or not 1 <= len(stations) <= 32 or len(stations) * len(times) > MAX_VALUES:
            raise ValueError("源资料须为1–32站且最多200000个值")
        sanitized, ids, negative_count, missing_count = [], set(), 0, 0
        for station in stations:
            if not isinstance(station, dict) or set(station) != {"id", "lon", "lat", "values"}:
                raise ValueError("每站须显式提供 id/lon/lat/values，不接受隐藏映射")
            station_id = _text(station["id"], "站号", 80)
            if station_id in ids:
                raise ValueError("源资料站号重复")
            ids.add(station_id)
            lon, lat = station["lon"], station["lat"]
            if any(not _finite_number(value) for value in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError("站点经纬度须为有限 EPSG:4326 数值")
            values = station["values"]
            if not isinstance(values, list) or len(values) != len(times):
                raise ValueError("每站 values 必须与完整时轴一一对应")
            for value in values:
                if value is None:
                    missing_count += 1
                elif not _finite_number(value):
                    raise ValueError("站点雨量仅允许有限数值或 null；不得猜测单位或补零")
                elif value < 0:
                    negative_count += 1
            sanitized.append({"id": station_id, "lon": float(lon), "lat": float(lat), "values": list(values)})
        source = {**payload, "name": name, "provenanceNote": provenance,
                  "times": [value.isoformat(timespec="seconds") for value in parsed], "stations": sanitized}
        if len(json.dumps(source, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("源资料 JSON 超过2MB")
        with self._lock:
            source_id = "SRC-" + uuid.uuid4().hex
            folder = self.sources_root / source_id
            folder.mkdir(parents=True, exist_ok=False)
            path = folder / "source.json"
            write_json(path, source)
            manifest = {"schemaVersion": "1.0", "sourceId": source_id, "projectId": PROJECT_ID,
                        "resolutionId": RESOLUTION_ID, "name": name, "provenanceNote": provenance,
                        "sourceLabel": "用户声明站点资料；真实性与单位时次尚未独立验收",
                        "createdAt": now(), "unit": "mm", "crs": "EPSG:4326", "timeConvention": TIME_CONVENTION,
                        "coverage": {"start": source["times"][0], "end": source["times"][-1], "hours": len(times)},
                        "stationCount": len(stations), "valueCount": len(stations) * len(times),
                        "missingValueCount": missing_count, "negativeValueCount": negative_count,
                        "productionEligible": False, "scientificAuthenticity": "USER_DECLARED_NOT_INDEPENDENTLY_VERIFIED",
                        "file": self.file_record(path)}
            if origin is not None:
                manifest.update(sourceOrigin=copy.deepcopy(origin),
                                sourceLabel="水文资料库实测雨量；按所选站点和时段提取，原始年资料摘要留存",
                                scientificAuthenticity="EXISTING_OBSERVATION_FILES_NOT_PRODUCTION_CERTIFIED")
            write_json(folder / "manifest.json", manifest)
            return self.source(source_id)["public"]

    def source(self, source_id, verify=True):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ValueError("源资料编号无效")
        folder = self.sources_root / source_id
        manifest_path = folder / "manifest.json"
        manifest = read_json(manifest_path)
        if manifest.get("sourceId") != source_id or manifest.get("productionEligible") is not False:
            raise ValueError("源资料登记记录无效")
        _case(manifest)
        expected = self.reference(folder / "source.json")
        if {key: manifest.get("file", {}).get(key) for key in ("root", "path")} != expected:
            raise ValueError("源资料记录不能指向其他文件")
        if verify:
            self._verify_files({"files": [manifest["file"]]})
            if manifest.get("sourceType") == "mdb":
                self._verify_files({"files": manifest["immutableSourceFiles"]})
        public = {key: copy.deepcopy(value) for key, value in manifest.items() if key != "file"}
        public.update(sourceSHA256=manifest["file"]["sha256"], manifestSHA256=file_sha256(manifest_path), registered=True)
        public.update(start=manifest["coverage"]["start"], end=manifest["coverage"]["end"])
        return {"sourceId": source_id, "path": folder / "source.json", "manifestPath": manifest_path,
                "sourceSHA256": manifest["file"]["sha256"], "manifestSHA256": file_sha256(manifest_path),
                "manifest": manifest, "public": public}

    def list_sources(self):
        return {"sources": [self.source(path.name)["public"] for path in sorted(self.sources_root.glob("SRC-*")) if path.is_dir()]}

    def register_mdb_source(self, source_path, *, name, provenance_note):
        """CLI-only immutable MDB snapshot; never connect to the original file."""
        path = Path(source_path).resolve()
        if not path.is_file() or path.suffix.lower() not in {".mdb", ".accdb"}:
            raise ValueError("MDB源须为已有.mdb/.accdb文件")
        name = _text(name, "资料名", 200)
        provenance_note = _text(provenance_note, "来源核对说明", 2000)
        digest = file_sha256(path)
        source_id = "SRC-MDB-" + digest
        folder = self.sources_root / source_id
        with self._lock:
            if (folder / "manifest.json").is_file():
                return self.source(source_id)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("database" + path.suffix.lower())
            if target.exists() and file_sha256(target) != digest:
                raise ValueError("已有content-addressed数据库副本摘要不一致")
            if not target.exists():
                shutil.copyfile(path, target)
            if file_sha256(target) != digest or file_sha256(path) != digest:
                raise ValueError("复制期间MDB改变，拒绝登记")
            descriptor = {"schemaVersion": "1.0", "sourceType": "mdb", "sha256": digest,
                          "databaseFile": self.reference(target), "originalName": path.name,
                          "missingValuePolicy": "PRESERVE_MISSING_NO_ZERO_FILL", "productionEligible": False}
            write_json(folder / "source.json", descriptor)
            manifest = {"schemaVersion": "1.0", "sourceId": source_id, "projectId": PROJECT_ID,
                        "resolutionId": RESOLUTION_ID, "sourceType": "mdb", "name": name,
                        "provenanceNote": provenance_note, "sourceLabel": "独立MDB资料快照；原文件未连接、未更改",
                        "createdAt": now(), "productionEligible": False,
                        "scientificAuthenticity": "SOURCE_SNAPSHOT_NOT_PRODUCTION_CERTIFIED",
                        "coverage": {"start": None, "end": None}, "stationCount": None,
                        "databaseSHA256": digest, "originalName": path.name,
                        "immutableSourceFiles": [self.file_record(target)],
                        "file": self.file_record(folder / "source.json")}
            write_json(folder / "manifest.json", manifest)
            return self.source(source_id)

    def normalize_processing_request(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("输入处理请求须为 JSON 对象")
        if payload.get('operation') == 'schemeInput':
            from scheme_inputs import normalize_preparation
            return normalize_preparation(payload, self)
        _fields(payload, {"requestId", "operation", "projectId", "resolutionId", "name", "start", "end",
                          "parentBundleVersionId", "sourceId", "idwPower", "minimumStations", "zeroOnlyPolicy"})
        _case(payload)
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not ID.fullmatch(request_id):
            raise ValueError("处理请求须有唯一、有效 requestId")
        name = _text(payload.get("name"), "输入版本名", 200)
        operation = payload.get("operation")
        if operation not in {"qcWindow", "stationIdw"}:
            raise ValueError("输入处理仅支持 qcWindow / stationIdw")
        start, end, hours = processing_window(payload.get("start"), payload.get("end"))
        parent_id = payload.get("parentBundleVersionId", DEFAULT_BUNDLE_VERSION_ID)
        parent = self.resolve(parent_id)
        request = {**payload, "name": name, "start": start.isoformat(timespec="seconds"),
                   "end": end.isoformat(timespec="seconds"), "hours": hours,
                   "parentBundleVersionId": parent["bundleVersionId"], "parentManifestSHA256": parent["manifestSHA256"],
                   "productionEligible": False}
        if operation == "qcWindow":
            if set(payload).intersection({"sourceId", "idwPower", "minimumStations", "zeroOnlyPolicy"}):
                raise ValueError("qcWindow 不接受站点插值字段")
        else:
            source = self.source(payload.get("sourceId"))
            if source["manifest"].get("sourceType") == "mdb":
                raise ValueError("MDB须通过显式MDB导入生成完整输入版本，不能冒充站点JSON窗口")
            first, last = source["manifest"]["coverage"]["start"], source["manifest"]["coverage"]["end"]
            if start < hourly_time(first) or end > hourly_time(last):
                raise ValueError("站点处理窗口须完整落在源资料覆盖内")
            power, minimum = payload.get("idwPower", 2.0), payload.get("minimumStations", 1)
            if not _finite_number(power) or not 1 <= power <= 4:
                raise ValueError("idwPower 须为1至4的有限数值")
            if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= source["manifest"]["stationCount"]:
                raise ValueError("minimumStations 须为1至登记站数的整数")
            policy = payload.get("zeroOnlyPolicy", "KEEP")
            if policy not in {"KEEP", "ANNUAL_RECLASSIFY"}:
                raise ValueError("zeroOnlyPolicy 仅允许 KEEP / ANNUAL_RECLASSIFY")
            if policy == "ANNUAL_RECLASSIFY" and (start != datetime(start.year, 1, 1) or end != datetime(start.year, 12, 31, 23)):
                raise ValueError("年度全零重新判缺仅允许完整自然年，短窗口必须 KEEP")
            request.update(sourceId=source["sourceId"], sourceSHA256=source["sourceSHA256"],
                           sourceManifestSHA256=source["manifestSHA256"], idwPower=float(power),
                           minimumStations=minimum, zeroOnlyPolicy=policy)
        return request

    def register_task(self, task_id):
        if not isinstance(task_id, str) or not DATA_TASK_ID.fullmatch(task_id):
            raise ValueError("输入处理任务编号无效")
        with self._lock:
            folder = safe_task(self.tasks_root, task_id)
            status, result, request = read_json(folder / "status.json"), read_json(folder / "result.json"), read_json(folder / "request.json")
            if status.get("taskId") != task_id or status.get("status") not in {"completed", "archived"}:
                raise ValueError("只可登记成功完成的输入处理任务；失败、取消或运行中产物不可登记")
            product_id = result.get("productVersionId")
            if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
                raise ValueError("处理结果产品编号无效")
            path = self.products_root / product_id / "manifest.json"
            digest, manifest = file_sha256(path), read_json(path)
            if (result.get("taskId") != task_id or result.get("manifestSHA256") != digest
                    or manifest.get("sourceTaskId") != task_id or manifest.get("bundleVersionId") != product_id
                    or manifest.get("requestSHA256") != manifest_digest(request)
                    or manifest.get("operation") != request.get("operation")
                    or result.get("productionEligible") is not False):
                raise ValueError("任务、请求、产品清单或结果摘要不一致；不能登记")
            self._descriptor(manifest, digest, path, verify=True)
            if request.get("sourceId"):
                source = self.source(request["sourceId"])
                if source["sourceSHA256"] != request.get("sourceSHA256") or source["manifestSHA256"] != request.get("sourceManifestSHA256"):
                    raise ValueError("源资料已偏离任务冻结摘要")
            if self.resolve(request["parentBundleVersionId"])["manifestSHA256"] != request["parentManifestSHA256"]:
                raise ValueError("父输入版本已偏离任务冻结摘要")
            registration_path = self.registry_root / f"{product_id}.json"
            if registration_path.exists():
                registration = read_json(registration_path)
                if registration.get("sourceTaskId") != task_id or registration.get("manifestSHA256") != digest:
                    raise ValueError("版本已登记且不允许覆盖")
            else:
                registration = {"schemaVersion": "1.0", "bundleVersionId": product_id, "sourceTaskId": task_id,
                                "manifestSHA256": digest, "registeredAt": now(), "productionEligible": False}
                write_json(registration_path, registration)
            return self.resolve(product_id)["public"]


__all__ = ["InputProducts", "DEFAULT_BUNDLE_VERSION_ID", "file_sha256", "manifest_digest",
           "processing_window", "MAX_SOURCE_BYTES"]
