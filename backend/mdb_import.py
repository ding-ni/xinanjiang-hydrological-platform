"""Explicit MDB-to-immutable-input import; originals are never opened by ADO.

The Access connection uses Mode=Read and a subprocess timeout on a disposable
query copy. Rainfall, observations, and events form one independent bundle;
the existing 226-event baseline remains the default.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from common import MODEL, clean, now, read_json, write_json
from input_products import InputProducts, DEFAULT_BUNDLE_VERSION_ID, TIME_CONVENTION, file_sha256, manifest_digest
from observation_quality import DEFAULT_OBSERVATION_POLICY, evaluate_observation_coverage


# Same explicitly supplied station mapping as the 20261012 delivery's
# scripts/run_mdb_flood.py. These are not guessed from rain-gauge names.
XUFAN_COORDINATES = {
    "87001": (119.461603, 30.139650), "87002": (119.459018, 30.130794),
    "87003": (119.492530, 30.139403), "87004": (119.465508, 30.105292),
    "87005": (119.482141, 30.116191), "87006": (119.476389, 30.090278),
    "87007": (119.507222, 30.118056), "87008": (119.509898, 30.111903),
    "87009": (119.516944, 30.076667), "87010": (119.517716, 30.074049),
}
CONTROL_STATION = "CGB087012"
COORDINATE_PROVENANCE = "20261012 delivery scripts/run_mdb_flood.py: XUFAN_CHECKED_COORDINATES; not independently production-certified"
TARGET_NC_CENTRE = "NC_GRID_AXIS_CENTRE"
TARGET_LEGACY_REPLAY = "LEGACY_CLOSED_RING_VERTEX_MEAN"
QC_PLATFORM_DIAGNOSTIC = "PLATFORM_DIAGNOSTIC_OBSERVATION_20261012"
QC_SOURCE_ORIGINAL = "SOURCE_ORIGINAL_20261011"
SOURCE_QUALITY_SHA256 = "5f9c78c76140ca6755464473a596c2db77af0dd426232e35a6d6d7b22749e3b3"
SOURCE_QUALITY_TASK = "CAL-20261011-104708-95c5368f"


def source_quality_snapshot(path):
    """Read the known CAL quality snapshot, without adding acceptance rules."""
    path = Path(path).resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("来源方案QC须提供原任务完整质量快照")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != SOURCE_QUALITY_SHA256:
        raise ValueError("来源方案QC快照SHA256与原任务不一致，拒绝替换其筛选规则")
    config = json.loads(content.decode("utf-8-sig"))
    if not isinstance(config, dict) or config.get("rules", {}).get("maximum_duration_hours") != 720 or "observation_coverage" in config:
        raise ValueError("来源方案QC须保持原720小时及原观测接受口径")
    return content, {"sourceTaskId": SOURCE_QUALITY_TASK, "originalName": path.name,
                     "sha256": digest, "bytes": len(content)}


def interpolation_targets(grids, geometry, nc_longitude, nc_latitude, policy=TARGET_NC_CENTRE):
    if policy not in {TARGET_NC_CENTRE, TARGET_LEGACY_REPLAY}:
        raise ValueError("MDB目标坐标策略无效")
    if len(grids) != len(nc_longitude) or len(grids) != len(nc_latitude):
        raise ValueError("MDB目标网格与NC中心坐标数量不同")
    rings = {(int(feature["properties"]["row"]), int(feature["properties"]["col"])):
             feature["geometry"]["coordinates"][0] for feature in geometry["features"]}
    targets = []
    for index, row in enumerate(grids):
        longitude, latitude = float(nc_longitude[index]), float(nc_latitude[index])
        if policy == TARGET_LEGACY_REPLAY:
            ring = rings.get((int(row["source_row"]), int(row["source_col"])))
            if not ring or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("源重放须为含重复首尾顶点的明确闭合ring")
            # Preserve the source method exactly, including its duplicated
            # closing vertex. This is deliberately not a geometric centroid.
            longitude = float(sum(point[0] for point in ring) / len(ring))
            latitude = float(sum(point[1] for point in ring) / len(ring))
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise ValueError("MDB目标经纬度不是有限值")
        targets.append({"unit_id": row["unit_id"], "longitude": longitude, "latitude": latitude})
    return targets


def parse_record_time(value):
    text = str(value).strip().replace("/", "-").replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            result = datetime.strptime(text, pattern)
            break
        except ValueError:
            continue
    else:
        raise ValueError("MDB时刻无效：" + text)
    if result.minute or result.second or result.microsecond or not 1960 <= result.year <= 2100:
        raise ValueError("MDB记录时刻必须是无时区整小时：" + text)
    return result


def nonnegative_value(value):
    if value is None or isinstance(value, bool):
        return None, "MISSING_OR_INVALID"
    try:
        result = float(str(value).strip())
    except (ValueError, TypeError, OverflowError):
        return None, "MISSING_OR_INVALID"
    if not math.isfinite(result):
        return None, "NONFINITE_AS_MISSING"
    if result < 0:
        return None, "NEGATIVE_AS_MISSING"
    if result > 3.4028234663852886e38:
        raise ValueError("源值超出float32表达范围")
    return result, "VALID"


def _canonical_source_value(value):
    try:
        number = float(str(value).strip())
        if math.isfinite(number):
            return ("NUMBER", number)
    except (TypeError, ValueError, OverflowError):
        pass
    return ("INVALID", str(value).strip() if value is not None else "")


def normalize_records(records, *, station_field, time_field, value_field, allowed_stations=None):
    """Same station/hour identical raw values dedupe; any conflicting value rejects.

    Invalid numeric values remain missing. Duplicate conflict checks occur on raw
    canonical values, before reclassification, so -1 and -2 cannot merge as NaN.
    """
    result, raw_seen = {}, {}
    audit = {"raw_records": len(records), "retained_records": 0, "identical_duplicates": 0,
             "missing_or_invalid": 0, "negative_as_missing": 0, "nonfinite_as_missing": 0}
    for row in records:
        if any(key not in row for key in (station_field, time_field, value_field)):
            raise ValueError("MDB记录字段不完整")
        station = str(row[station_field]).strip()
        if not station or allowed_stations is not None and station not in allowed_stations:
            raise ValueError("源记录没有明确站点映射：" + station)
        timestamp = parse_record_time(row[time_field])
        value, flag = nonnegative_value(row[value_field])
        canonical = _canonical_source_value(row[value_field])
        key = (station, timestamp)
        if key in raw_seen:
            if raw_seen[key] != canonical:
                raise ValueError(f"同站同小时源值冲突，拒绝导入：{station} {timestamp.isoformat()}")
            audit["identical_duplicates"] += 1
            continue
        raw_seen[key] = canonical
        result[key] = value
        if flag != "VALID":
            audit[{"NEGATIVE_AS_MISSING": "negative_as_missing", "NONFINITE_AS_MISSING": "nonfinite_as_missing"}.get(flag, "missing_or_invalid")] += 1
    audit["retained_records"] = len(result)
    return result, audit


def normalize_events(records):
    result = {}
    for row in records:
        code = str(row.get("flcd", "")).strip()
        if not re.fullmatch(r"[0-9]{1,30}", code):
            raise ValueError("MDB洪水编号无效")
        start, end = parse_record_time(row.get("s_dt")), parse_record_time(row.get("e_dt"))
        if end < start:
            raise ValueError("MDB洪水结束早于开始")
        item = {"eventId": "xf-" + code, "floodCode": code,
                "start": start.isoformat(), "end": end.isoformat(),
                "durationHours": int((end - start).total_seconds() / 3600) + 1}
        if code in result and result[code] != item:
            raise ValueError("同编号洪水窗口冲突：" + code)
        result[code] = item
    if not result:
        raise ValueError("MDB没有洪水窗口")
    return sorted(result.values(), key=lambda item: (item["start"], item["floodCode"]))


def parse_query_output(stdout):
    text = stdout.decode("utf-16" if stdout.startswith(b"\xff\xfe") else "mbcs" if os.name == "nt" else "utf-8", errors="strict")
    lines = text.splitlines()
    marker = re.fullmatch(r"__MDB_QUERY_COMPLETE__,([0-9]+)", lines[-1] if lines else "")
    if not marker:
        raise RuntimeError("MDB_DRIVER_INCOMPLETE_OUTPUT: 缺少完整查询结束标记，拒绝截断记录")
    reader = csv.DictReader(io.StringIO("\n".join(lines[:-1])), strict=True)
    if not reader.fieldnames:
        raise RuntimeError("MDB_DRIVER_EMPTY_OUTPUT: 未读取到字段，不伪造记录")
    records = list(reader)
    if len(records) != int(marker.group(1)) or any(None in row or any(value is None for value in row.values()) for row in records):
        raise RuntimeError("MDB_DRIVER_INCOMPLETE_OUTPUT: 查询记录数或字段数不一致，拒绝截断记录")
    return records, reader.fieldnames


def query_mdb_copy(snapshot, query_folder, sql, *, timeout=35, runner=subprocess.run):
    if not re.match(r"^\s*SELECT\s", sql, re.I) or re.search(r"\b(INTO|UPDATE|DELETE|INSERT|DROP|ALTER|CREATE)\b|;", sql, re.I):
        raise ValueError("MDB查询仅允许单条只读SELECT")
    if not 1 <= timeout <= 60:
        raise ValueError("MDB单次查询超时须为1到60秒")
    folder = Path(query_folder)
    folder.mkdir(parents=True, exist_ok=True)
    query_path = folder / ("query-" + uuid.uuid4().hex + Path(snapshot).suffix)
    shutil.copyfile(snapshot, query_path)
    before = file_sha256(snapshot)
    script = Path(__file__).with_name("mdb_query_readonly.vbs")
    executable = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/cscript.exe")
    command = [executable, "//B", "//Nologo", f"//T:{max(1, int(timeout - 1))}", str(script), str(query_path), sql]
    receipt_path = query_path.with_suffix(".query.json")
    receipt = {"createdAt": now(), "query": sql, "queryCopy": query_path.name,
               "sourceSHA256": before, "queryCopySHA256Before": file_sha256(query_path),
               "driverExecutable": executable, "timeoutSeconds": timeout,
               "queryPolicy": "COPY_ONLY_ADO_MODE_READ_TIMEOUT", "status": "RUNNING"}
    write_json(receipt_path, receipt)
    try:
        process = runner(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        receipt.update(status="DRIVER_TIMEOUT", errorType=type(exc).__name__,
                       queryCopySHA256After=file_sha256(query_path), sourceSHA256After=file_sha256(snapshot))
        write_json(receipt_path, receipt)
        raise RuntimeError("MDB_DRIVER_TIMEOUT: 只读Access查询超时；未生成或登记输入产品") from exc
    finally:
        if file_sha256(snapshot) != before:
            raise RuntimeError("不可变MDB快照在查询期间改变")
    if process.returncode:
        stderr = (process.stderr or b"").decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")
        receipt.update(status="DRIVER_OR_QUERY_FAILED", returnCode=process.returncode, diagnostic=stderr.strip()[:2000],
                       queryCopySHA256After=file_sha256(query_path), sourceSHA256After=file_sha256(snapshot))
        write_json(receipt_path, receipt)
        raise RuntimeError("MDB_DRIVER_UNAVAILABLE_OR_QUERY_FAILED: " + stderr.strip()[:2000])
    stdout = process.stdout or b""
    output_path = query_path.with_suffix(".records.csv")
    output_path.write_bytes(stdout)
    try:
        records, fields = parse_query_output(stdout)
    except (RuntimeError, csv.Error, UnicodeError) as exc:
        receipt.update(status="INCOMPLETE_OUTPUT", returnCode=process.returncode,
                       diagnostic=str(exc),
                       queryCopySHA256After=file_sha256(query_path), sourceSHA256After=file_sha256(snapshot),
                       recordsFile=output_path.name, recordsSHA256=file_sha256(output_path))
        write_json(receipt_path, receipt)
        raise RuntimeError("MDB_DRIVER_INCOMPLETE_OUTPUT: " + str(exc)) from exc
    receipt.update(status="COMPLETED", fields=fields, recordCount=len(records), returnCode=process.returncode,
                   queryCopySHA256After=file_sha256(query_path), sourceSHA256After=file_sha256(snapshot),
                   recordsFile=output_path.name, recordsSHA256=file_sha256(output_path))
    write_json(receipt_path, receipt)
    return records


def _record_scope(event_records):
    events = normalize_events(event_records)
    earliest = parse_record_time(events[0]["start"])
    start = datetime(earliest.year, 1, 1) - timedelta(hours=168)
    end = max(parse_record_time(event["end"]) for event in events)
    return {"type": "REQUIRED_CONTINUOUS_INTERVAL_PLUS_FULL_FL_CODE", "start": start.isoformat(), "end": end.isoformat(),
            "rainStations": sorted(XUFAN_COORDINATES), "flowStation": CONTROL_STATION, "eventWindowsScope": "FULL_FL_CODE_TABLE",
            "notFullRainFlowTableComparison": True}


def _record_queries(scope):
    start, end = parse_record_time(scope["start"]), parse_record_time(scope["end"])
    if end < start:
        raise ValueError("MDB查询结束早于开始")
    date_clause = "BETWEEN #" + start.strftime("%m/%d/%Y %H:%M:%S") + "# AND #" + end.strftime("%m/%d/%Y %H:%M:%S") + "#"
    return {"events": "SELECT flcd,s_dt,e_dt FROM fl_code ORDER BY s_dt,flcd",
            "rain": "SELECT HS_Code,YMDHM,DTRN FROM ReFactorDT WHERE HS_Code BETWEEN 87001 AND 87010 AND YMDHM " + date_clause + " ORDER BY YMDHM,HS_Code",
            "flow": "SELECT stcd,ymdhm,q FROM st_river_r WHERE stcd='CGB087012' AND ymdhm " + date_clause + " ORDER BY ymdhm"}


def read_mdb_records(snapshot, query_folder, *, timeout=35, query=query_mdb_copy, scope=None):
    records = {"events": query(snapshot, query_folder, "SELECT flcd,s_dt,e_dt FROM fl_code ORDER BY s_dt,flcd", timeout=timeout)}
    scope = scope or _record_scope(records["events"])
    sqls = _record_queries(scope)
    for role in ("rain", "flow"):
        records[role] = query(snapshot, query_folder, sqls[role], timeout=timeout)
    records["_queryScope"] = copy.deepcopy(scope)
    return records


def reuse_verified_records(products, task_id, snapshot, *, comparison=False, scope=None):
    """Resume only complete, hashed SELECT receipts from this isolated runtime."""
    if not isinstance(task_id, str) or not re.fullmatch(r"DATA-mdb-[0-9a-f]{32}", task_id):
        raise ValueError("只可复用同runtime明确MDB任务的已核验查询记录")
    folder = products.tasks_root / task_id
    request = read_json(folder / "request.json")
    scope = scope or request.get("recordQueryScope")
    if not scope or scope.get("type") != "REQUIRED_CONTINUOUS_INTERVAL_PLUS_FULL_FL_CODE":
        raise ValueError("历史查询没有明确的完整请求范围，不可复用")
    expected = _record_queries(scope)
    digest = file_sha256(snapshot)
    records, evidence = {}, []
    query_folder = folder / ("comparison-copies" if comparison else "query-copies")
    for path in sorted(query_folder.glob("*.query.json")):
        receipt = read_json(path)
        role = next((key for key, sql in expected.items() if sql == receipt.get("query")), None)
        if role is None or role in records or receipt.get("status") != "COMPLETED":
            raise ValueError("复用记录须为唯一、完整且范围完全一致的SELECT")
        if any(receipt.get(key) != digest for key in ("sourceSHA256", "sourceSHA256After", "queryCopySHA256Before", "queryCopySHA256After")):
            raise ValueError("复用查询源或副本摘要不一致")
        filename = receipt.get("recordsFile", "")
        if not re.fullmatch(r"query-[0-9a-f]{32}\.records\.csv", filename):
            raise ValueError("复用记录文件名无效")
        output_path = query_folder / filename
        if output_path.is_symlink() or file_sha256(output_path) != receipt.get("recordsSHA256"):
            raise ValueError("复用查询记录摘要不一致")
        records[role], fields = parse_query_output(output_path.read_bytes())
        if fields != receipt.get("fields") or len(records[role]) != receipt.get("recordCount"):
            raise ValueError("复用记录字段或数量不一致")
        evidence.extend([path, output_path])
    if set(records) != set(expected) or not comparison and _record_scope(records["events"]) != scope:
        raise ValueError("复用查询范围或三张表闭包不完整")
    records["_queryScope"] = copy.deepcopy(scope)
    records["_verifiedQueryArtifacts"] = [products.reference(path) for path in evidence] + [products.reference(folder / "request.json")]
    records["_reusedQueryTask"] = task_id
    return records


def semantic_summary(records):
    rain, rain_audit = normalize_records(records["rain"], station_field="HS_Code", time_field="YMDHM", value_field="DTRN", allowed_stations=XUFAN_COORDINATES)
    flow, flow_audit = normalize_records(records["flow"], station_field="stcd", time_field="ymdhm", value_field="q", allowed_stations={CONTROL_STATION})
    events = normalize_events(records["events"])
    def raw_digest(rows, station_field, time_field, value_field):
        canonical = {(str(row[station_field]).strip(), parse_record_time(row[time_field])):
                     _canonical_source_value(row[value_field]) for row in rows}
        return manifest_digest([[station, time.isoformat(), value] for (station, time), value in sorted(canonical.items())])
    return {"scope": records.get("_queryScope", {"type": "PROVIDED_RECORDS_ONLY"}),
            "rain": {"audit": rain_audit, "recordsSHA256": manifest_digest([[station, time.isoformat(), value] for (station, time), value in sorted(rain.items())]),
                     "sourceValuesSHA256": raw_digest(records["rain"], "HS_Code", "YMDHM", "DTRN")},
            "flow": {"audit": flow_audit, "recordsSHA256": manifest_digest([[station, time.isoformat(), value] for (station, time), value in sorted(flow.items())]),
                     "sourceValuesSHA256": raw_digest(records["flow"], "stcd", "ymdhm", "q")},
            "events": {"count": len(events), "recordsSHA256": manifest_digest(events)}}


def compare_mdb_records(before, after):
    old, new = semantic_summary(before), semantic_summary(after)
    if old["scope"] != new["scope"]:
        raise ValueError("MDB记录语义比对范围不一致")
    def differences(left, right):
        added, removed = sorted(right.keys() - left.keys()), sorted(left.keys() - right.keys())
        changed = sorted(key for key in left.keys() & right.keys() if left[key] != right[key])
        def sample(keys):
            result = []
            for key in keys[:20]:
                label = {"station": key[0], "time": key[1].isoformat()} if isinstance(key, tuple) else {"eventId": key}
                result.append({**label, "before": left.get(key), "after": right.get(key)})
            return result
        return {"addedCount": len(added), "removedCount": len(removed), "changedCount": len(changed),
                "addedSamples": sample(added), "removedSamples": sample(removed), "changedSamples": sample(changed), "sampleLimit": 20}
    details, raw_details = {}, {}
    for role, station_field, time_field, value_field, allowed in (
            ("rain", "HS_Code", "YMDHM", "DTRN", XUFAN_COORDINATES),
            ("flow", "stcd", "ymdhm", "q", {CONTROL_STATION})):
        kwargs = {"station_field": station_field, "time_field": time_field, "value_field": value_field, "allowed_stations": allowed}
        left, _ = normalize_records(before[role], **kwargs)
        right, _ = normalize_records(after[role], **kwargs)
        details[role] = differences(left, right)
        raw_left = {(str(row[station_field]).strip(), parse_record_time(row[time_field])): _canonical_source_value(row[value_field]) for row in before[role]}
        raw_right = {(str(row[station_field]).strip(), parse_record_time(row[time_field])): _canonical_source_value(row[value_field]) for row in after[role]}
        raw_details[role] = differences(raw_left, raw_right)
    details["events"] = differences({row["eventId"]: row for row in normalize_events(before["events"])},
                                    {row["eventId"]: row for row in normalize_events(after["events"])})
    return {"status": "VERIFIED_RECORD_SEMANTICS", "before": old, "after": new,
            "sameNormalizedRain": old["rain"]["recordsSHA256"] == new["rain"]["recordsSHA256"],
            "sameNormalizedFlow": old["flow"]["recordsSHA256"] == new["flow"]["recordsSHA256"],
            "sameEventWindows": old["events"]["recordsSHA256"] == new["events"]["recordsSHA256"],
            "sameSourceRainValues": old["rain"]["sourceValuesSHA256"] == new["rain"]["sourceValuesSHA256"],
            "sameSourceFlowValues": old["flow"]["sourceValuesSHA256"] == new["flow"]["sourceValuesSHA256"],
            "normalizedDifferences": details, "sourceValueDifferences": raw_details,
            "comparisonScope": new["scope"]}


def _csv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def import_mdb_bundle(source_path, *, products=None, name="MDB独立17场资料", provenance_note="20261012资料接入，尚未生产验收", timeout=35, records=None, register=True, compare_with=None, reuse_query_task=None, target_coordinate_policy=TARGET_NC_CENTRE, quality_policy=QC_PLATFORM_DIAGNOSTIC, source_quality_config=None):
    if target_coordinate_policy not in {TARGET_NC_CENTRE, TARGET_LEGACY_REPLAY}:
        raise ValueError("MDB目标坐标策略无效")
    if quality_policy not in {QC_PLATFORM_DIAGNOSTIC, QC_SOURCE_ORIGINAL}:
        raise ValueError("MDB质量政策无效")
    quality_bytes, quality_origin = None, None
    if quality_policy == QC_SOURCE_ORIGINAL:
        if target_coordinate_policy != TARGET_LEGACY_REPLAY or not source_quality_config:
            raise ValueError("来源方案QC须显式选择原闭环均值float64及原任务质量快照")
        quality_bytes, quality_origin = source_quality_snapshot(source_quality_config)
    elif source_quality_config is not None:
        raise ValueError("原任务质量快照仅用于明确的来源方案QC")
    products = products or InputProducts()
    original = Path(source_path).resolve()
    original_hash = file_sha256(original)
    source = products.register_mdb_source(original, name=name, provenance_note=provenance_note)
    snapshot = products._path(read_json(source["path"])["databaseFile"])
    task_id = "DATA-mdb-" + uuid.uuid4().hex
    folder = products.tasks_root / task_id
    parent = products.resolve()
    request = {"requestId": task_id, "operation": "mdbImport", "projectId": "nananxi-xufan", "resolutionId": "1km",
               "name": name, "sourceId": source["sourceId"], "sourceSHA256": source["sourceSHA256"],
               "sourceManifestSHA256": source["manifestSHA256"], "databaseSHA256": original_hash,
               "parentBundleVersionId": parent["bundleVersionId"], "parentManifestSHA256": parent["manifestSHA256"],
               "productionEligible": False, "targetCoordinatePolicy": target_coordinate_policy,
               "forcingValueDtype": "float64" if target_coordinate_policy == TARGET_LEGACY_REPLAY else "float32",
               "sourceQcPolicy": quality_policy}
    if quality_bytes is not None:
        snapshot_path = folder / "source-quality-config-snapshot.json"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(quality_bytes)
        request.update(sourceQcOrigin=quality_origin, sourceQualitySnapshot=products.reference(snapshot_path),
                       eventQualityConfigurationSHA256=quality_origin["sha256"])
    write_json(folder / "request.json", request)
    write_json(folder / "status.json", {"taskId": task_id, "status": "running", "createdAt": now(), "operation": "mdbImport"})
    audit = {"taskId": task_id, "originalSHA256": original_hash, "originalNeverOpenedByADO": True,
             "queryPolicy": "COPY_ONLY_ADO_MODE_READ_TIMEOUT", "sourceId": source["sourceId"],
             "semanticComparison": {"status": "REQUESTED_PENDING" if compare_with else "NOT_REQUESTED"}}
    try:
        if reuse_query_task:
            if records is not None:
                raise ValueError("复用已核验查询不能同时注入记录")
            records = reuse_verified_records(products, reuse_query_task, snapshot)
        if records is None:
            records = read_mdb_records(snapshot, folder / "query-copies", timeout=timeout)
        audit["recordQueryScope"] = records.get("_queryScope", {"type": "PROVIDED_RECORDS_ONLY"})
        audit["reusedQueryTask"] = records.get("_reusedQueryTask")
        audit["verifiedQueryArtifacts"] = records.get("_verifiedQueryArtifacts", [])
        request["recordQueryScope"] = audit["recordQueryScope"]
        write_json(folder / "request.json", request)
        audit["recordSemantics"] = semantic_summary(records)
        if compare_with:
            comparison_source = products.register_mdb_source(compare_with, name="MDB对照只读快照", provenance_note="源记录语义比对")
            comparison_snapshot = products._path(read_json(comparison_source["path"])["databaseFile"])
            try:
                comparison_records = (reuse_verified_records(products, reuse_query_task, comparison_snapshot, comparison=True, scope=records.get("_queryScope")) if reuse_query_task
                                      else read_mdb_records(comparison_snapshot, folder / "comparison-copies", timeout=timeout, scope=records.get("_queryScope")))
                audit["semanticComparison"] = compare_mdb_records(comparison_records, records)
                audit["verifiedQueryArtifacts"].extend(comparison_records.get("_verifiedQueryArtifacts", []))
            except RuntimeError as exc:
                audit["semanticComparison"] = {"status": "UNVERIFIED_DRIVER_FAILURE", "error": str(exc)}
            except ValueError as exc:
                audit["semanticComparison"] = {"status": "UNVERIFIED_RECORD_VALIDATION_FAILURE", "error": str(exc)}
        write_json(folder / "import-audit.json", audit)
        result = build_mdb_product(products, source, parent, folder, request, records, audit)
        write_json(folder / "result.json", result)
        write_json(folder / "status.json", {"taskId": task_id, "status": "completed", "createdAt": now(), "operation": "mdbImport"})
        if register:
            result["registeredProduct"] = products.register_task(task_id)
        return result
    except Exception as exc:
        if audit["semanticComparison"]["status"] == "REQUESTED_PENDING":
            audit["semanticComparison"] = {"status": "UNVERIFIED_DRIVER_FAILURE" if isinstance(exc, RuntimeError) else "UNVERIFIED_RECORD_VALIDATION_FAILURE", "error": str(exc)}
        audit.update(status="FAILED", errorType=type(exc).__name__, error=str(exc), productRegistered=False)
        write_json(folder / "import-audit.json", audit)
        write_json(folder / "status.json", {"taskId": task_id, "status": "failed", "error": str(exc), "operation": "mdbImport"})
        raise
    finally:
        if file_sha256(original) != original_hash:
            raise RuntimeError("原MDB在导入期间发生外部改变；检查审计记录")
        if quality_origin and file_sha256(source_quality_config) != quality_origin["sha256"]:
            raise RuntimeError("原质量快照在导入期间发生改变；检查审计记录")


def build_mdb_product(products, source, parent, task_folder, request, records, audit):
    # Pin the HDF5 backend before xarray can discover unrelated binary engines.
    import h5py
    import h5netcdf
    import numpy as np
    import pandas as pd
    import xarray as xr
    from pyproj import Transformer
    code = str(MODEL / "code")
    if code not in sys.path:
        sys.path.insert(0, code)
    from runoff_platform.data.precipitation_idw import idw_interpolate_hourly
    from runoff_platform.data.event_quality import evaluate_event_quality
    from xaj_routing.control_area import contributing_grid_areas
    from xaj_routing.io import read_topology_json

    events = normalize_events(records["events"])
    rain, rain_audit = normalize_records(records["rain"], station_field="HS_Code", time_field="YMDHM", value_field="DTRN", allowed_stations=XUFAN_COORDINATES)
    flow, flow_audit = normalize_records(records["flow"], station_field="stcd", time_field="ymdhm", value_field="q", allowed_stations={CONTROL_STATION})
    earliest = parse_record_time(events[0]["start"])
    last_event = max(parse_record_time(event["end"]) for event in events)
    origin = datetime(earliest.year, 1, 1) - timedelta(hours=168)
    start_year, end_year = origin.year, last_event.year
    product_id = "INPUT-MDB-" + uuid.uuid4().hex
    product_folder = products.products_root / product_id
    product_folder.mkdir(parents=True, exist_ok=False)
    precip_dir = product_folder / "precipitation"
    precip_dir.mkdir()
    with parent["paths"]["grids"].open(encoding="utf-8-sig", newline="") as handle:
        grids = list(csv.DictReader(handle))
    rows = np.array([int(row["array_row"]) for row in grids])
    cols = np.array([int(row["array_col"]) for row in grids])
    template_path = next(iter(sorted(parent["precipitationDir"].glob("nananxi_precip_hourly_1km_*.nc"))))
    with xr.open_dataset(template_path, engine="h5netcdf") as template:
        lat, lon = np.asarray(template.lat.values), np.asarray(template.lon.values)
    if rows.min() < 0 or cols.min() < 0 or rows.max() >= len(lat) or cols.max() >= len(lon):
        raise ValueError("MDB目标网格坐标与父空间资料不符")
    areas = contributing_grid_areas(parent["paths"]["mapping"], read_topology_json(parent["paths"]["topology"]), "R0020_UP", [row["unit_id"] for row in grids])
    control_indexes = np.flatnonzero(areas > 0)
    projection = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
    station_ids = sorted(XUFAN_COORDINATES)
    sx, sy = projection.transform([XUFAN_COORDINATES[station][0] for station in station_ids], [XUFAN_COORDINATES[station][1] for station in station_ids])
    policy = request["targetCoordinatePolicy"]
    dtype = request["forcingValueDtype"]
    targets = interpolation_targets(grids, read_json(parent["paths"]["geometry"]), lon[cols], lat[rows], policy)
    target_digest = manifest_digest(targets)
    tx, ty = projection.transform([row["longitude"] for row in targets], [row["latitude"] for row in targets])
    if not all(np.isfinite(value).all() for value in (sx, sy, tx, ty)):
        raise ValueError("MDB站点投影坐标无效")
    forcing_version = "mdb-rain-" + product_id.removeprefix("INPUT-MDB-")
    observation_version = "mdb-flow-" + product_id.removeprefix("INPUT-MDB-")
    yearly, all_values, all_counts, all_times, files = [], [], [], [], []
    target_longitude = np.full((len(lat), len(lon)), np.nan, dtype=float)
    target_latitude = np.full((len(lat), len(lon)), np.nan, dtype=float)
    target_longitude[rows, cols] = [row["longitude"] for row in targets]
    target_latitude[rows, cols] = [row["latitude"] for row in targets]
    for year in range(start_year, end_year + 1):
        times = pd.date_range(datetime(year, 1, 1), datetime(year + 1, 1, 1) - timedelta(hours=1), freq="h")
        station_values = np.full((len(times), len(station_ids)), np.nan)
        for column, station in enumerate(station_ids):
            station_values[:, column] = [rain.get((station, timestamp.to_pydatetime()), np.nan) for timestamp in times]
        interpolated, counts = idw_interpolate_hourly(station_values, np.asarray(sx), np.asarray(sy), np.asarray(tx), np.asarray(ty), power=2, minimum_stations=1)
        corrected = np.full((len(times), len(lat), len(lon)), np.nan, dtype=dtype)
        corrected[:, rows, cols] = interpolated.astype(dtype)
        dataset = xr.Dataset({"precipitation": (("time", "lat", "lon"), corrected),
                              "interpolation_target_longitude": (("lat", "lon"), target_longitude),
                              "interpolation_target_latitude": (("lat", "lon"), target_latitude),
                              "source_station_count": (("time",), counts),
                              "precipitation_quality_code": (("time",), np.where(counts == 0, 0, np.where(counts == 1, 1, 2)).astype(np.int8))},
                             coords={"time": times, "lat": lat, "lon": lon},
                             attrs={"Conventions": "CF-1.10", "bundle_version_id": product_id, "forcing_version": forcing_version,
                                    "time_convention": TIME_CONVENTION, "source_database_sha256": request["databaseSHA256"],
                                    "missing_policy": "NO_ZERO_FILL_NEGATIVE_INVALID_AS_MISSING", "idw_power": 2.0,
                                    "coordinate_provenance": COORDINATE_PROVENANCE,
                                    "target_coordinate_policy": policy, "target_coordinates_sha256": target_digest,
                                    "forcing_value_dtype": dtype, "source_replay_only": int(policy == TARGET_LEGACY_REPLAY),
                                    "zero_only_station_year_policy": "KEEP_WITH_UNVERIFIED_ZERO_OBSERVATION_NOTE"})
        dataset.precipitation.attrs.update(units="mm", long_name="hourly precipitation amount", cell_methods="time: sum")
        dataset.precipitation.attrs["coordinates"] = "interpolation_target_longitude interpolation_target_latitude"
        dataset.interpolation_target_longitude.attrs.update(units="degrees_east", long_name="Actual IDW evaluation longitude under the explicit target policy")
        dataset.interpolation_target_latitude.attrs.update(units="degrees_north", long_name="Actual IDW evaluation latitude under the explicit target policy")
        dataset.source_station_count.attrs.update(units="1", long_name="finite valid source stations actually used")
        output = precip_dir / f"nananxi_precip_hourly_1km_{year}.nc"
        dataset.to_netcdf(output, engine="h5netcdf", encoding={"precipitation": {"zlib": True, "complevel": 4, "dtype": dtype}})
        dataset.close()
        files.append(output)
        all_times.extend(times.to_pydatetime())
        all_values.append(interpolated.astype(dtype))
        all_counts.append(counts)
        yearly.append({"year": year, "hours": len(times), "hours_without_valid_station": int((counts == 0).sum()),
                       "minimum_station_count": int(counts.min()), "maximum_station_count": int(counts.max())})
    times = pd.DatetimeIndex(all_times)
    values, counts = np.concatenate(all_values), np.concatenate(all_counts)
    observed = pd.Series([flow.get((CONTROL_STATION, timestamp.to_pydatetime()), np.nan) for timestamp in times], index=times, dtype=float)
    flow_path = product_folder / "observed-flow.csv"
    _csv(flow_path, ["time", "flow_m3_s"], [{"time": time.isoformat(), "flow_m3_s": value if np.isfinite(value) else ""} for time, value in observed.items()])
    source_qc = request["sourceQcPolicy"] == QC_SOURCE_ORIGINAL
    quality_path = product_folder / "event-quality-config.json"
    if source_qc:
        frozen_quality = products._path(request["sourceQualitySnapshot"])
        if file_sha256(frozen_quality) != request["eventQualityConfigurationSHA256"]:
            raise ValueError("来源质量快照偏离请求冻结摘要")
        quality_path.write_bytes(frozen_quality.read_bytes())
        quality_config = read_json(quality_path)
    else:
        quality_config = copy.deepcopy(read_json(parent["paths"]["quality"]))
        quality_config["config_id"] = "MDB-STRICT-OBSERVATION-20261012"
        quality_config["observation_coverage"] = {**DEFAULT_OBSERVATION_POLICY, "enforce": True,
                                                "policy_status": "PROVISIONAL_AUDIT_DEFAULTS_NOT_PRODUCTION_STANDARD"}
        # Preserve the previously registered diagnostic product's wider window
        # policy; source reproduction above copies the original 720h unchanged.
        quality_config["rules"]["maximum_duration_hours"] = max(744, quality_config["rules"].get("maximum_duration_hours", 0))
        write_json(quality_path, quality_config)
    quality_digest = file_sha256(quality_path)
    packages = []
    for event in events:
        start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
        event_mask = (times >= start) & (times <= end)
        audit_mask = (times >= start - pd.Timedelta(hours=168)) & (times <= end)
        quality = evaluate_event_quality(event_id=event["eventId"], start=start, end=end, times=times[audit_mask],
                                         grid_precipitation_mm=values[audit_mask][:, control_indexes],
                                         grid_areas_km2=areas[control_indexes], observed_flow_cms=observed,
                                         source_station_count=counts[audit_mask], config=quality_config)
        coverage = evaluate_observation_coverage(times[event_mask], observed.loc[start:end].to_numpy(), quality_config)
        quality["observation_coverage"] = coverage
        quality["exclusion_reasons"] = list(dict.fromkeys(quality["exclusion_reasons"] + coverage["exclusion_reasons"]))
        quality["warning_reasons"] = list(dict.fromkeys(quality["warning_reasons"] + coverage["warning_reasons"]))
        warmup_ready = bool(np.isfinite(values[audit_mask]).all() and (counts[audit_mask] > 0).all())
        quality.update(accepted=not quality["exclusion_reasons"], model_input_ready=warmup_ready and not quality["exclusion_reasons"],
                       source_qc_policy=request["sourceQcPolicy"], event_quality_configuration_sha256=quality_digest,
                       observed_volume_scope=coverage["volume_scope"], flow_completeness=coverage["coverage_fraction"])
        valid_flow = observed.loc[start:end].dropna()
        record = {**event, "status": "ready" if quality["accepted"] else "review", "quality": quality,
                  "observedInputs": {"rainDatasetId": forcing_version, "rainCompleteness": float(np.isfinite(values[event_mask]).all(axis=1).mean()),
                                     "riverRecords": int(len(valid_flow)), "riverCompleteness": coverage["coverage_fraction"],
                                     "observedPeakFlowM3s": float(valid_flow.max()) if len(valid_flow) else None,
                                     "observedPeakTime": valid_flow.idxmax().isoformat() if len(valid_flow) else None,
                                     "volumeM3": float(valid_flow.sum() * 3600) if coverage["coverage_fraction"] == 1 else None,
                                     "maskedVolumeM3": float(valid_flow.sum() * 3600) if len(valid_flow) else None, "volumeScope": coverage["volume_scope"]},
                  "spatialRainInputs": {"modelContributingAreaKm2": float(areas.sum()), "stationCountFields": ["source_station_count"]},
                  "qcRevision": {"forcingVersion": forcing_version, "observationVersion": observation_version,
                                 "windowSource": "MDB_FL_CODE_UNCHANGED", "windowChanged": False,
                                 "sourceQcPolicy": request["sourceQcPolicy"], "eventQualityConfigurationSHA256": quality_digest}}
        packages.append(record)
    events_path = product_folder / "event-catalog.json"
    write_json(events_path, {"schemaVersion": "1.1", "packages": packages, "count": len(packages), "bundleVersionId": product_id,
                             "sourceDatabaseSHA256": request["databaseSHA256"], "originalSource": "fl_code"})
    mapping_path = product_folder / "station-mapping.json"
    write_json(mapping_path, {"coordinates": XUFAN_COORDINATES, "coordinateProvenance": COORDINATE_PROVENANCE,
                              "flowStation": CONTROL_STATION, "mappedSection": "70115800", "stationMappingProductionVerified": False})
    target_path = product_folder / "interpolation-targets.csv"
    _csv(target_path, ["unit_id", "longitude", "latitude"], targets)
    audit.update(status="COMPLETED", productId=product_id, rainRecords=rain_audit, flowRecords=flow_audit,
                 years=yearly, eventCount=len(packages), acceptedEventCount=sum(event["quality"]["accepted"] for event in packages),
                 continuousOrigin=origin.isoformat(), requestedContinuousEnd=last_event.isoformat())
    audit.update(targetCoordinatePolicy=policy, forcingValueDtype=dtype, targetCoordinatesSHA256=target_digest,
                 sourceQcPolicy=request["sourceQcPolicy"], eventQualityConfigurationSHA256=quality_digest,
                 sourceQcOrigin=request.get("sourceQcOrigin"),
                 acceptedEventIds=[row["eventId"] for row in packages if row["quality"]["accepted"]],
                 excludedEvents=[{"eventId": row["eventId"], "exclusionReasons": row["quality"]["exclusion_reasons"]}
                                 for row in packages if not row["quality"]["accepted"]],
                 sourceReplayOnly=policy == TARGET_LEGACY_REPLAY,
                 targetCoordinateWarning="Legacy closed-ring vertex mean duplicates the first vertex and shifts evaluation points; source replay only, centre policy requires fresh calibration." if policy == TARGET_LEGACY_REPLAY else None)
    audit_path = product_folder / "import-audit.json"
    write_json(audit_path, audit)
    write_json(task_folder / "import-audit.json", audit)
    paths = {role: products.reference(path) for role, path in parent["paths"].items()}
    paths.update(flow=products.reference(flow_path), events=products.reference(events_path), quality=products.reference(quality_path))
    file_paths = [parent["paths"][role] for role in ("grids", "geometry", "topology", "mapping")]
    file_paths += files + [flow_path, events_path, quality_path, mapping_path, target_path, audit_path, source["path"], source["manifestPath"]]
    file_paths += [products._path({"root": entry["root"], "path": entry["path"]}) for entry in source["manifest"]["immutableSourceFiles"]]
    file_paths += [products._path(reference) for reference in audit.get("verifiedQueryArtifacts", [])]
    if source_qc:
        file_paths.append(products._path(request["sourceQualitySnapshot"]))
    file_paths = list(dict.fromkeys(file_paths))
    summary = {"eventCount": len(packages), "acceptedEventCount": audit["acceptedEventCount"], "gridCount": len(grids),
               "continuousOrigin": origin.isoformat(), "requestedContinuousEnd": last_event.isoformat(),
               "fullCalendarYearProducts": True, "rainSourceRecords": rain_audit["retained_records"], "flowSourceRecords": flow_audit["retained_records"],
               "sourceQcPolicy": request["sourceQcPolicy"], "eventQualityConfigurationSHA256": quality_digest,
               "acceptedEventIds": audit["acceptedEventIds"], "excludedEvents": audit["excludedEvents"]}
    manifest = {"schemaVersion": "1.0", "bundleVersionId": product_id, "projectId": "nananxi-xufan", "resolutionId": "1km",
                "name": request["name"], "operation": "mdbImport", "createdAt": now(), "sourceTaskId": task_folder.name,
                "requestSHA256": manifest_digest(request), "parentBundleVersionId": parent["bundleVersionId"],
                "parentManifestSHA256": parent["manifestSHA256"], "sourceId": source["sourceId"], "sourceType": "model-qc",
                "sourceLabel": "独立MDB原始雨量、徐畈流量和fl_code场次；严格缺测与逐时有效站IDW",
                "sourceSHA256": source["sourceSHA256"], "sourceManifestSHA256": source["manifestSHA256"],
                "sourceOrigin": {"type": "mdb-snapshot", "databaseSHA256": request["databaseSHA256"], "originalName": source["manifest"]["originalName"]},
                "recordQueryScope": audit["recordQueryScope"],
                "targetCoordinatePolicy": policy, "forcingValueDtype": dtype, "targetCoordinatesSHA256": target_digest,
                "sourceReplayOnly": policy == TARGET_LEGACY_REPLAY,
                "sourceQcPolicy": request["sourceQcPolicy"], "eventQualityConfigurationSHA256": quality_digest,
                "sourceQcOrigin": request.get("sourceQcOrigin"),
                "timeConvention": TIME_CONVENTION, "forcingVersion": forcing_version, "observationVersion": observation_version,
                "modelInputVersion": product_id, "productionEligible": False, "forcingPolicy": "STRICT_MISSING",
                "coverage": {"years": list(range(start_year, end_year + 1)), "start": times[0].isoformat(), "end": times[-1].isoformat()},
                "summary": summary, "paths": paths, "precipitationDirectory": products.reference(precip_dir),
                "files": [products.file_record(path) for path in file_paths],
                "qualityNotes": ["与旧固定226场基线分开登记，不切换默认版本。", "缺测、非法值和负雨保留缺测；同站同小时冲突拒绝。",
                                 "仅小时有效站参与IDW；零雨原记录保留，其观测真实性尚待核实。", "完整年NC的未记录小时为NaN，真正所需历史强迫缺测会拒绝模拟。",
                                 "雨流查询限17场所需连续区间，区间外NaN代表未查询，未声明原库该小时缺测。",
                                 "新资料暂用覆盖门槛0.8、最长缺测48h、涨峰退水覆盖0.8，可调整且尚非生产标准；旧基线门槛不变。", "未授予生产资格。"]}
    if policy == TARGET_LEGACY_REPLAY:
        manifest["qualityNotes"].append("仅冻结源候选数值重放：复用源闭合ring全部顶点均值（含重复首尾点），本资料偏心约139m；不是几何中心。科学中心方案须重新率定和验证。")
    if source_qc:
        manifest["sourceLabel"] = "同事20261011来源方案：原MDB、原闭环顶点均值float64及原任务QC720小时"
        manifest["qualityNotes"] = [note for note in manifest["qualityNotes"] if not note.startswith("新资料暂用覆盖门槛")]
        manifest["qualityNotes"].extend(["严格复用原任务QC快照及其摘要，720小时上限和观测接受规则不增加强制覆盖门槛。",
                                        "80%覆盖、48小时缺口和分阶段覆盖仅为诊断警告，不改变来源方案接受集合。",
                                        "17窗口完整保留；原QC排除的721小时窗口仅允许按原固定参数补算对比，不参加率定或独立样本统计。",
                                        "缺测流量仅共同有效小时评价，不补零、不插值、不外推完整洪量。"])
    manifest_path = product_folder / "manifest.json"
    write_json(manifest_path, manifest)
    digest = file_sha256(manifest_path)
    public = products._descriptor(manifest, digest, manifest_path)["public"]
    public["registered"] = False
    return {"taskId": task_folder.name, "productVersionId": product_id, "bundleVersionId": product_id,
            "manifestSHA256": digest, "productionEligible": False, "resultSource": "mdb-import", "summary": summary, "product": public}


__all__ = ["import_mdb_bundle", "normalize_records", "normalize_events", "semantic_summary", "compare_mdb_records", "query_mdb_copy", "read_mdb_records"]
