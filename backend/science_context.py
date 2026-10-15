"""Immutable scientific configurations and implementation identities.

Scientific settings are explicitly selected. Registering a version never changes
the active input, parameters or state. Hashes cover the actual PET CSV contents,
and all runtime paths remain relative to their committed version directory.
"""
from __future__ import annotations

import copy
import base64
import csv
import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from pathlib import Path

from common import MODEL, ROOT, RUNTIME, now, read_json, write_json

IDENTITY_KEYS = ("modelImplementationSHA256", "scienceConfigurationSHA256", "petConfigurationSHA256")
VERSION = re.compile(r"^SCIENCE-[0-9a-f]{64}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
FILES = {"state_config": "state-config.json", "initial_land_state": "initial-land-state.json", "pet_config": "pet-config.json"}


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def digest_file(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def model_implementation_identity(model_root=None):
    model = Path(model_root or MODEL).resolve()
    paths = sorted((model / "code").rglob("*.py"))
    paths += [model / "scripts/run_formal_multievent_calibration.py"]
    records = []
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(model):
            raise ValueError("模型实现文件缺失或包含外部链接")
        records.append({"path": path.relative_to(model).as_posix(), "sha256": digest_file(path)})
    if not records:
        raise ValueError("模型实现目录为空")
    for name in ("worker.py", "routing_adapter.py", "state_initialization_adapter.py", "science_context.py", "strict_forcing.py",
                 "calibration_groups.py", "calibration_objectives.py", "calibration_search.py", "joint_calibration.py",
                 "routing_calibration.py", "original_calibration_plan.py"):
        records.append({"path": "platform/" + name, "sha256": digest_file(ROOT / name)})
    return {"modelImplementationSHA256": digest_json(records), "files": records}


def _payload(value):
    if isinstance(value, dict):
        result, source = copy.deepcopy(value), None
    else:
        source = Path(value).resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError("科学配置快照缺失或包含链接")
        result = read_json(source)
    if not isinstance(result, dict):
        raise ValueError("科学配置须为 JSON 对象")
    digest_json(result)
    return result, source


def _validate_pet(config, csv_path=None):
    mode = str(config.get("mode", "")).upper()
    if mode == "CONSTANT_ASSUMPTION":
        value = config.get("value_mm_h")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("PET 常数须为非负有限数值")
    elif mode == "MONTHLY_CLIMATOLOGY":
        values = config.get("monthly_values_mm_day")
        if not isinstance(values, list) or len(values) != 12 or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("PET 月值须为十二个非负有限数值")
    elif mode == "HOURLY_CSV":
        if csv_path is None or not Path(csv_path).is_file() or Path(csv_path).is_symlink():
            raise ValueError("逐小时 PET 的真实 CSV 依赖缺失")
        seen = set()
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            time_key, value_key = config.get("time_column", "time"), config.get("value_column", "pet_mm_h")
            if not reader.fieldnames or time_key not in reader.fieldnames or value_key not in reader.fieldnames:
                raise ValueError("PET CSV 缺少登记的时间或数值字段")
            for row in reader:
                try:
                    stamp = datetime.fromisoformat(row[time_key].replace("Z", "+00:00"))
                    value = float(row[value_key])
                except (ValueError, TypeError):
                    raise ValueError("PET CSV 含无效时间或数值")
                if stamp.tzinfo or stamp.minute or stamp.second or stamp.microsecond or stamp in seen or not math.isfinite(value) or value < 0:
                    raise ValueError("PET CSV 须为唯一的本地整点和非负有限数值")
                seen.add(stamp)
        if not seen:
            raise ValueError("PET CSV 为空")
    else:
        raise ValueError("不支持的 PET 科学配置模式")
    return mode


def _validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) - {'strategy', 'warmupHours', 'continuousOrigin'} or policy.get('strategy') not in {'independent-warmup', 'continuous-origin'}:
        raise ValueError('科学状态策略无效')
    if type(policy.get('warmupHours')) is not int or not 0 <= policy['warmupHours'] <= 8784:
        raise ValueError('科学版本预热时长无效')
    origin = policy.get('continuousOrigin')
    if origin is not None and origin != 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP':
        raise ValueError('科学连续起点须使用登记的来源起点语义，具体时刻留在任务请求中')
    if policy['strategy'] == 'continuous-origin' and origin is None:
        raise ValueError('连续科学策略缺少起点语义')
    return policy


class ScienceVersions:
    def __init__(self, runtime_root=None, model_root=None):
        self.runtime = Path(runtime_root or RUNTIME).resolve()
        self.model = Path(model_root or MODEL).resolve()
        self.root = self.runtime / "science-versions"

    def register(self, *, name, state_config, initial_land_state, pet_config, state_policy=None, provenance=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise ValueError("科学版本名称须为 1–160 字符")
        configs = {}
        source_records = {}
        source_paths = {}
        for key, supplied in (("state_config", state_config), ("initial_land_state", initial_land_state), ("pet_config", pet_config)):
            configs[key], source_paths[key] = _payload(supplied)
            if source_paths[key]:
                source_records[key] = {"name": source_paths[key].name, "sha256": digest_file(source_paths[key])}
        policy = copy.deepcopy(state_policy or {"strategy": "independent-warmup", "warmupHours": 168})
        _validate_policy(policy)
        if "pet_config" in configs["state_config"]:
            configs["state_config"]["pet_config"] = FILES["pet_config"]
        external = None
        if str(configs["pet_config"].get("mode", "")).upper() == "HOURLY_CSV":
            supplied_path = Path(str(configs["pet_config"].get("path", "")))
            if not supplied_path.is_absolute():
                if source_paths["pet_config"] is None:
                    raise ValueError("逐小时 PET 须从带依赖的配置文件登记")
                supplied_path = source_paths["pet_config"].parent / supplied_path
            external = supplied_path.resolve()
            _validate_pet(configs["pet_config"], external)
            configs["pet_config"]["path"] = "pet-data.csv"
        else:
            _validate_pet(configs["pet_config"])
        blobs = {FILES[key]: (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8") for key, value in configs.items()}
        if external:
            blobs["pet-data.csv"] = external.read_bytes()
            source_records["pet_data"] = {"name": external.name, "sha256": hashlib.sha256(blobs["pet-data.csv"]).hexdigest()}
        files = [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in sorted(blobs.items())]
        science_digest = digest_json({"files": files, "statePolicy": policy})
        pet_records = [row for row in files if row["path"] in {"pet-config.json", "pet-data.csv"}]
        pet_digest = digest_json(pet_records)
        version = "SCIENCE-" + science_digest
        folder = self.root / version
        if folder.exists():
            return self.resolve(version)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / ("pending-" + uuid.uuid4().hex)
        temporary.mkdir()
        for filename, data in blobs.items():
            (temporary / filename).write_bytes(data)
        manifest = {"schemaVersion": "1.0", "scienceVersionId": version, "name": name.strip(), "createdAt": now(),
                    "productionEligible": False, "scienceConfigurationSHA256": science_digest,
                    "petConfigurationSHA256": pet_digest, "statePolicy": policy, "files": files,
                    "sourceFiles": source_records, "provenance": copy.deepcopy(provenance or {})}
        write_json(temporary / "manifest.json", manifest)
        # A committed identity is immutable. A concurrent identical registration
        # may already have won; its contents are still verified on resolve.
        try:
            temporary.rename(folder)
        except OSError:
            if not folder.is_dir():
                raise
        return self.resolve(version)

    def resolve(self, version):
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError("科学版本未登记")
        folder = self.root / version
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("科学版本快照缺失")
        manifest = read_json(folder / "manifest.json")
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("科学版本文件清单缺失")
        allowed = set(FILES.values()) | {"pet-data.csv"}
        names = set()
        for row in files:
            if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"} or row["path"] not in allowed or row["path"] in names:
                raise ValueError("科学版本文件清单无效")
            names.add(row["path"])
            path = folder / row["path"]
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder.resolve()) or path.stat().st_size != row["bytes"] or digest_file(path) != row["sha256"]:
                raise ValueError("科学配置或 PET 依赖内容与登记摘要不一致")
        if not set(FILES.values()).issubset(names):
            raise ValueError("科学版本缺少状态、初态或 PET 配置")
        digest = digest_json({"files": files, "statePolicy": manifest.get("statePolicy")})
        pet_digest = digest_json([row for row in files if row["path"] in {"pet-config.json", "pet-data.csv"}])
        if manifest.get("schemaVersion") != "1.0" or manifest.get("scienceVersionId") != version or version != "SCIENCE-" + digest or manifest.get("scienceConfigurationSHA256") != digest or manifest.get("petConfigurationSHA256") != pet_digest or manifest.get("productionEligible") is not False:
            raise ValueError("科学版本身份或摘要被改变")
        configs = {key: read_json(folder / filename) for key, filename in FILES.items()}
        _validate_policy(manifest.get('statePolicy'))
        if str(configs["pet_config"].get("mode", "")).upper() == "HOURLY_CSV" and configs["pet_config"].get("path") != "pet-data.csv":
            raise ValueError("科学版本 PET 路径必须指向冻结依赖")
        mode = _validate_pet(configs["pet_config"], folder / "pet-data.csv")
        return {**manifest, "paths": {key: folder / filename for key, filename in FILES.items()}, "configs": configs,
                "petMode": "CONSTANT_ZERO_EXPLICIT_REQUEST" if mode == "CONSTANT_ASSUMPTION" and configs["pet_config"]["value_mm_h"] == 0 else "MONTHLY_CLIMATOLOGY_ASSUMPTION" if mode == "MONTHLY_CLIMATOLOGY" else mode}

    def list(self):
        implementation = model_implementation_identity(self.model)['modelImplementationSHA256']
        versions = []
        for folder in sorted(self.root.glob('SCIENCE-*')):
            if folder.is_dir():
                descriptor = self.resolve(folder.name)
                versions.append({**{key: value for key, value in descriptor.items() if key not in {'paths', 'configs'}},
                                 'modelImplementationSHA256': implementation})
        return {'modelImplementationSHA256': implementation, 'versions': versions}

    def export(self, version):
        descriptor = self.resolve(version)
        folder = self.root / version
        return {"schemaVersion": "1.0", "manifest": read_json(folder / "manifest.json"),
                "files": {row["path"]: base64.b64encode((folder / row["path"]).read_bytes()).decode("ascii") for row in descriptor["files"]}}

    def import_snapshot(self, snapshot):
        if not isinstance(snapshot, dict) or set(snapshot) != {"schemaVersion", "manifest", "files"} or snapshot["schemaVersion"] != "1.0":
            raise ValueError("状态携带的科学快照格式无效")
        manifest, encoded = snapshot["manifest"], snapshot["files"]
        if not isinstance(manifest, dict) or not isinstance(encoded, dict):
            raise ValueError("科学快照缺少清单或依赖文件")
        version = manifest.get("scienceVersionId")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError("科学快照身份无效")
        rows = manifest.get("files", [])
        if not isinstance(rows, list) or set(encoded) != {row.get("path") for row in rows if isinstance(row, dict)}:
            raise ValueError("科学快照依赖闭包不完整")
        allowed = set(FILES.values()) | {"pet-data.csv"}
        blobs = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"} or row["path"] not in allowed or row["path"] in blobs:
                raise ValueError("科学快照含未知或重复文件")
            try:
                data = base64.b64decode(encoded[row["path"]], validate=True)
            except (ValueError, TypeError):
                raise ValueError("科学快照文件编码无效")
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("科学快照文件内容与摘要不一致")
            blobs[row["path"]] = data
        if not set(FILES.values()).issubset(blobs):
            raise ValueError("科学快照缺少状态、初态或 PET 配置")
        digest = digest_json({"files": rows, "statePolicy": manifest.get("statePolicy")})
        pet_digest = digest_json([row for row in rows if row["path"] in {"pet-config.json", "pet-data.csv"}])
        if manifest.get("scienceConfigurationSHA256") != digest or version != "SCIENCE-" + digest or manifest.get("petConfigurationSHA256") != pet_digest or manifest.get("productionEligible") is not False:
            raise ValueError("科学快照的版本身份或生产资格被改变")
        _validate_policy(manifest.get('statePolicy'))
        existing = self.root / version
        if existing.exists():
            return self.resolve(version)
        self.root.mkdir(parents=True, exist_ok=True)
        pending = self.root / ("pending-" + uuid.uuid4().hex)
        pending.mkdir()
        for name, data in blobs.items():
            (pending / name).write_bytes(data)
        write_json(pending / "manifest.json", manifest)
        pending.rename(existing)
        return self.resolve(version)

    def apply_to_request(self, request, version):
        """Explicit per-request selection; registration never changes a default."""
        request = copy.deepcopy(request)
        descriptor = self.resolve(version)
        request["science"] = {"versionId": version, "configurationSHA256": descriptor["scienceConfigurationSHA256"],
                              "petConfigurationSHA256": descriptor["petConfigurationSHA256"]}
        for key in ("scienceVersionId", "scienceConfigurationSHA256", "petConfigurationSHA256"):
            request.setdefault("lineage", {}).pop(key, None)
        return freeze_science_selection(request, self.runtime, self.model)

    def for_request(self, request):
        selection = request.get("science", {})
        if not isinstance(selection, dict) or set(selection) - {"versionId", "configurationSHA256", "petConfigurationSHA256"}:
            raise ValueError("科学条件只能选择已登记版本，不接受本地路径或内联配置")
        evaporation = request.get("inputs", {}).get("evaporation", {})
        mode = evaporation.get('mode')
        if selection.get("versionId"):
            descriptor = self.resolve(selection["versionId"])
            strategy = (request.get('calibration') or {}).get('statePolicy')
            if strategy is not None and strategy != descriptor['statePolicy']['strategy']:
                raise ValueError('率定状态策略与所选科学版本不一致，请使用该候选冻结的状态策略')
            if request.get('runType') == 'stateInitialization':
                initialization = (request.get('initialization') or {}).get('strategy', 'FIXED_WARMUP')
                continuous = descriptor['statePolicy']['strategy'] == 'continuous-origin'
                if continuous != (initialization == 'CONTINUOUS'):
                    raise ValueError('初始化方式与所选方案不一致：连续方案请从原连续起点补算，其他方式请重新选择初始化条件')
            if mode == 'constant-zero' and descriptor['petMode'] != 'CONSTANT_ZERO_EXPLICIT_REQUEST':
                raise ValueError('所选科学版本不是场次零蒸散发，请明确选择其科学蒸散发方案')
            if mode == 'monthly-climatology-assumption' and descriptor['petMode'] != 'MONTHLY_CLIMATOLOGY_ASSUMPTION':
                raise ValueError('所选科学版本不是逐月给定蒸散发，请明确选择其科学蒸散发方案')
            return descriptor
        if mode == 'science-version':
            raise ValueError('科学蒸散发方案须明确选择已登记科学版本')
        event_zero = mode == 'constant-zero' or (mode is None and request.get("runType") == "calibration" and request.get("period", {}).get("mode") == "event")
        pet = {"mode": "CONSTANT_ASSUMPTION", "value_mm_h": 0.0, "production_eligible": False, "provenance": "Explicit event calibration zero PET assumption"} if event_zero else self.model / "config/pet_forcing_assumed_monthly.json"
        state_path = self.model / 'config/state_initialization_config.json'
        default_strategy = 'continuous-origin' if (request.get('initialization') or {}).get('strategy') == 'CONTINUOUS' or request.get('period', {}).get('continuousOrigin') else 'independent-warmup'
        policy = {'strategy': (request.get('calibration') or {}).get('statePolicy', default_strategy),
                  'warmupHours': int(read_json(state_path).get('fixed_warmup_hours', 168))}
        if policy['strategy'] == 'continuous-origin':
            # A task's explicit origin/hour range remains in its frozen request;
            # it must not make otherwise identical scientific states incompatible.
            policy['continuousOrigin'] = 'FIRST_DATABASE_EVENT_YEAR_JAN1_MINUS_FIXED_WARMUP'
        return self.register(name="场次零蒸散发" if event_zero else "逐月给定蒸散发与固定预热", state_config=self.model / "config/state_initialization_config.json",
                             initial_land_state=self.model / "config/initial_state_land.json", pet_config=pet,
                             state_policy=policy)


def inherit_state_science(request, checkpoint, runtime_root=None, model_root=None):
    """Continue the parent's PET and initialization conditions, never a default.

    The caller verifies the parent against its completed task before invoking
    this function. Explicitly choosing another version remains an error.
    """
    request = copy.deepcopy(request)
    if any(not isinstance(checkpoint.get(key), str) or not SHA.fullmatch(checkpoint[key]) for key in IDENTITY_KEYS):
        raise ValueError('历史状态缺少模型实现或科学配置身份，请重新初始化')
    registry = ScienceVersions(runtime_root, model_root)
    if checkpoint['modelImplementationSHA256'] != model_implementation_identity(registry.model)['modelImplementationSHA256']:
        from implementation_versions import frozen_worker
        try: frozen_worker(checkpoint['modelImplementationSHA256'],runtime_root or RUNTIME)
        except (ValueError,FileNotFoundError) as error: raise ValueError('父状态的模型实现已更新；'+str(error)) from None
        request.setdefault('lineage',{})['modelImplementationSHA256']=checkpoint['modelImplementationSHA256']
    version = checkpoint.get('scienceVersionId')
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('父状态缺少完整的蒸散发与初态方案，请重新初始化')
    if not (registry.root / version).is_dir():
        snapshot = checkpoint.get('scienceSnapshot')
        if not isinstance(snapshot, dict) or (snapshot.get('manifest') or {}).get('scienceVersionId') != version:
            raise ValueError('父状态的蒸散发与初态快照缺失，请重新初始化')
        registry.import_snapshot(snapshot)
    descriptor = registry.resolve(version)
    if any(checkpoint[key] != descriptor[key] for key in ('scienceConfigurationSHA256', 'petConfigurationSHA256')):
        raise ValueError('父状态的蒸散发与初态快照不一致，请重新初始化')
    selection = request.get('science') or {}
    if not isinstance(selection, dict):
        raise ValueError('蒸散发与初态方案格式无效')
    if selection.get('versionId') and selection['versionId'] != version:
        raise ValueError('续算和预报须沿用父状态的蒸散发与初态方案；换用方案请重新初始化')
    for key in ('configurationSHA256', 'petConfigurationSHA256'):
        expected = descriptor['scienceConfigurationSHA256' if key == 'configurationSHA256' else key]
        if key in selection and selection[key] != expected:
            raise ValueError('父状态的蒸散发与初态方案摘要不一致')
    request['science'] = {'versionId': version, 'configurationSHA256': descriptor['scienceConfigurationSHA256'],
                          'petConfigurationSHA256': descriptor['petConfigurationSHA256']}
    # The validated state is the authority even for older clients that still
    # send the default monthly label. No PET values are substituted here.
    request.setdefault('inputs', {})['evaporation'] = {'mode': 'science-version'}
    return request


def freeze_science_selection(request, runtime_root=None, model_root=None):
    if not isinstance(request, dict):
        raise ValueError("任务请求须为 JSON 对象")
    request = copy.deepcopy(request)
    registry = ScienceVersions(runtime_root, model_root)
    descriptor = registry.for_request(request)
    descriptor.update(model_implementation_identity(registry.model))
    expected_implementation=(request.get('lineage') or {}).get('modelImplementationSHA256')
    if expected_implementation and expected_implementation!=descriptor['modelImplementationSHA256']:
        from implementation_versions import frozen_worker
        try: frozen_worker(expected_implementation,runtime_root or RUNTIME)
        except (ValueError,FileNotFoundError) as error: raise ValueError('任务模型实现或科学配置已改变；'+str(error)) from None
        descriptor['modelImplementationSHA256']=expected_implementation
    selection = request.get("science", {})
    for key, expected in (("configurationSHA256", descriptor["scienceConfigurationSHA256"]), ("petConfigurationSHA256", descriptor["petConfigurationSHA256"])):
        if key in selection and selection[key] != expected:
            raise ValueError("科学条件摘要与已登记版本不一致")
    lineage = request.setdefault("lineage", {})
    if not isinstance(lineage, dict):
        raise ValueError("任务来源须为 JSON 对象")
    identities = {key: descriptor[key] for key in IDENTITY_KEYS}
    identities["scienceVersionId"] = descriptor["scienceVersionId"]
    identities['petMode'] = descriptor['petMode']
    for key, value in identities.items():
        if key in lineage and lineage[key] != value:
            raise ValueError("任务模型实现或科学配置已改变，请重新登记作业")
    lineage.update(identities)
    request["science"] = {"versionId": descriptor["scienceVersionId"], "configurationSHA256": descriptor["scienceConfigurationSHA256"],
                          "petConfigurationSHA256": descriptor["petConfigurationSHA256"]}
    return request, descriptor


def assert_science_compatibility(checkpoint, request):
    lineage = request.get("lineage", {})
    for key in IDENTITY_KEYS:
        parent, current = checkpoint.get(key), lineage.get(key)
        if not isinstance(parent, str) or not SHA.fullmatch(parent):
            raise ValueError("历史状态缺少模型实现或科学配置身份，请重新初始化")
        if not isinstance(current, str) or not SHA.fullmatch(current) or parent != current:
            raise ValueError("父状态的模型实现、PET 或科学配置不兼容，必须重新初始化")


def relocated_task_science(task_folder, expected_digests=None):
    """Resolve only same-task snapshots; never reuse an unrelated old E: path."""
    folder = Path(task_folder).resolve()
    request = read_json(folder / "resolved_request.json")
    expected = expected_digests or {}
    configs = {}
    evidence = {}
    for key in FILES:
        snapshot = folder / (key + "_snapshot.json")
        if snapshot.is_symlink() or not snapshot.is_file() or not snapshot.resolve().is_relative_to(folder):
            raise ValueError("率定科学快照缺失，不能静默沿用当前配置：" + key)
        digest = digest_file(snapshot)
        if key in expected and expected[key] != digest:
            raise ValueError("迁移科学快照摘要与原登记证据不一致：" + key)
        configs[key] = snapshot
        evidence[key] = {"storedPath": request.get(key), "relocatedPath": snapshot.name, "sha256": digest,
                         "verification": "EXPECTED_SHA256" if key in expected else "CURRENT_PACKAGE_SHA256"}
    return {"paths": configs, "request": request, "evidence": evidence}
