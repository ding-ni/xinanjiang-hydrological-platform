"""SQLite observations and reusable schemes; large numerical artifacts stay in files."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from common import WORKSPACE, RUNTIME, MODEL, now, read_json
from platform_config import SETTINGS

PROJECT = "nananxi-xufan"
PUBLIC = SETTINGS['projects'] / PROJECT
EPOCH = datetime(1970, 1, 1)
VARIABLES = {"rain": ("降雨量", "mm"), "level": ("水位", "m"), "flow": ("流量", "m3/s")}
DISPLAY_LIMIT_HOURS = 8784


def hour(value):
    try:
        date = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValueError("时间须为本地整点，如 2017-06-01T00:00")
    if date.tzinfo or date.minute or date.second or date.microsecond:
        raise ValueError("资料时间须为无时区的本地整小时")
    return int((date - EPOCH).total_seconds() // 3600)


def timestamp(value):
    if value is None:
        return None
    try:
        return (EPOCH + timedelta(hours=value)).isoformat(timespec="minutes")
    except (OverflowError, ValueError) as exc:
        raise ValueError("资料时刻超出可表示的整小时时间范围") from exc


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class HydroDatabase:
    def __init__(self, runtime=None):
        self.runtime = Path(runtime or RUNTIME)
        self.path = self.runtime / "database/hydrology.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.import_lock = threading.RLock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("资料库版本高于本服务支持版本")
            db.executescript((Path(__file__).with_name("hydrology_schema.sql")).read_text(encoding="utf-8"))
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES (2,?,?)', ('workspace-drafts-and-state-metadata', now()))
            db.execute('PRAGMA user_version=2')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_existing(self, public=None, events=None):
        public = Path(public or PUBLIC)
        with self.import_lock:
            catalog = read_json(public / "observations/catalog.json")
            spatial = {str(s.get("code", s["id"])): s for s in read_json(public / "spatial/stations.json")["stations"]}
            aliases = {str(s["legacyCode"]): str(s["spatialCode"]) for s in catalog["rain"]["stations"]}
            with self.connect() as db:
                db.execute("INSERT OR IGNORE INTO projects VALUES (?, ?)", (PROJECT, "南安溪-徐畈"))
                for code, s in spatial.items():
                    db.execute("INSERT OR IGNORE INTO stations VALUES (?,?,?,?,?,?,?)",
                               (PROJECT, code, s["name"], s.get("typeLabel", s.get("type", "")), s.get("lng"), s.get("lat"), encoded(s)))
                for station in catalog["rain"]["stations"]:
                    code = str(station["spatialCode"])
                    db.execute("INSERT OR IGNORE INTO stations VALUES (?,?,?,?,?,?,?)", (PROJECT, code, station["name"], "雨量站", None, None, encoded(station)))
                    db.execute("INSERT OR IGNORE INTO station_aliases VALUES (?,?,?,?)", (PROJECT, "mdb", str(station["legacyCode"]), code))
            imported = skipped = 0
            for category in ("rain", "river"):
                for path in sorted((public / "observations" / category).glob("*.json")):
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    source_id = f"MDB-{category}-{path.stem}"
                    with self.connect() as db:
                        previous = db.execute("SELECT sha256 FROM source_files WHERE id=?", (source_id,)).fetchone()
                    if previous:
                        if previous[0] != digest:
                            raise ValueError(f"已入库原始文件发生变化：{path.name}；须核查后建立新资料版本")
                        skipped += 1
                        continue
                    data = read_json(path)
                    if data.get("timeEncoding") != "hour-of-year-local":
                        raise ValueError("不支持的资料时间编码")
                    base = hour(f'{data["year"]}-01-01T00:00')
                    end = hour(f'{data["year"] + 1}-01-01T00:00')
                    rows = []
                    for row in data["rows"]:
                        if len(row) != 3 or not isinstance(row[0], int) or not base <= base + row[0] < end:
                            raise ValueError("逐小时资料行或年内时间无效")
                        if category == "rain":
                            if not isinstance(row[1], int) or not 0 <= row[1] < len(data["stationCodes"]):
                                raise ValueError("逐小时雨量站点索引无效")
                            values = [(aliases[str(data["stationCodes"][row[1]])], "rain", row[2])]
                        else:
                            values = [(str(data["station"]["spatialCode"]), variable, value) for variable, value in zip(("level", "flow"), row[1:])]
                        for station, variable, value in values:
                            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                                raise ValueError("观测值须为有限数或缺测标记 null")
                            rows.append((source_id, PROJECT, station, variable, base + row[0], value))
                    with self.connect() as db:
                        db.execute("INSERT INTO source_files VALUES (?,?,?,?,?,?,?)", (source_id, PROJECT, category, str(path.relative_to(public)), digest, len(rows), now()))
                        db.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?)", rows)
                        db.execute("""INSERT INTO observation_coverage
                            SELECT source_id, project_id, station_id, variable, count(*), count(value),
                            sum(CASE WHEN value=0 THEN 1 ELSE 0 END), min(time_hour), max(time_hour)
                            FROM observations WHERE source_id=? GROUP BY project_id,station_id,variable""", (source_id,))
                    imported += 1
            event_path = Path(events) if events else MODEL / "data_interface/event_catalog/nananxi_flood_event_catalog_qc_20260819.json"
            if event_path.exists():
                with self.connect() as db:
                    for event in read_json(event_path)["packages"]:
                        payload = encoded(event)
                        old = db.execute("SELECT payload FROM flood_events WHERE project_id=? AND id=?", (PROJECT, event["eventId"])).fetchone()
                        if old and old[0] != payload:
                            raise ValueError("固定洪水目录发生变化，不能覆盖已登记场次")
                        db.execute("INSERT OR IGNORE INTO flood_events VALUES (?,?,?,?,?,?)", (PROJECT, event["eventId"], event["start"], event["end"], str(event.get("floodCode", "")), payload))
            return {"importedFiles": imported, "unchangedFiles": skipped, **self.summary()}

    def summary(self):
        with self.connect() as db:
            coverage = [dict(r) for r in db.execute("""SELECT variable, sum(records) AS records, sum(valid) AS valid,
                sum(zeros) AS zeros,min(start_hour) AS start,max(end_hour) AS end,
                count(DISTINCT station_id) AS stations FROM observation_coverage GROUP BY variable""")]
            for row in coverage:
                row.update(start=timestamp(row["start"]), end=timestamp(row["end"]), label=VARIABLES[row["variable"]][0], unit=VARIABLES[row["variable"]][1])
            counts = {name: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for name, table in
                      (("sourceFiles", "source_files"), ("stations", "stations"), ("events", "flood_events"), ("schemes", "schemes"), ("tasks", "task_index"))}
            return {"schemaVersion": 2, "projectId": PROJECT, "storage": "SQLite", "coverage": coverage, **counts}

    def stations(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""SELECT s.station_id AS id,s.name,s.kind,s.lon,s.lat,
                c.variable,sum(c.records) AS records,sum(c.valid) AS valid,
                min(c.start_hour) AS startHour,max(c.end_hour) AS endHour
                FROM stations s JOIN observation_coverage c ON s.project_id=c.project_id AND s.station_id=c.station_id
                WHERE s.project_id=? GROUP BY s.station_id,c.variable ORDER BY s.station_id,c.variable""", (PROJECT,))]

    @staticmethod
    def _window_bounds(variable, start, end):
        if variable not in VARIABLES:
            raise ValueError("要素须为 rain、level 或 flow")
        first, last = hour(start), hour(end)
        if last < first:
            raise ValueError("查询结束时刻不能早于起始时刻")
        return first, last

    @staticmethod
    def _window_summary(db, station, variable, first, last, sourceId=None):
        site = db.execute("SELECT station_id AS id,name,lon,lat FROM stations WHERE project_id=? AND station_id=?", (PROJECT, station)).fetchone()
        if not site:
            raise KeyError("站点不在本流域资料库中")
        arguments = (PROJECT, station, variable, first, last)
        scope = ''
        if sourceId:
            if not db.execute('SELECT 1 FROM source_files WHERE id=?', (sourceId,)).fetchone():
                raise ValueError('资料来源未登记')
            scope = ' AND source_id=?'
            arguments += (sourceId,)
        conflict = db.execute("""SELECT time_hour FROM observations
            WHERE project_id=? AND station_id=? AND variable=? AND time_hour BETWEEN ? AND ?
            """ + scope + ' GROUP BY time_hour HAVING count(*)>1 LIMIT 1', arguments).fetchone()
        if conflict:
            raise RuntimeError("同一小时存在多个来源，须明确资料版本后查询")
        statistics = db.execute("""SELECT count(*) AS records,count(value) AS valid,
            coalesce(sum(CASE WHEN value=0 THEN 1 ELSE 0 END),0) AS zeros,
            sum(value) AS total,min(value) AS minimum,max(value) AS maximum
            FROM observations WHERE project_id=? AND station_id=? AND variable=? AND time_hour BETWEEN ? AND ?""" + scope, arguments).fetchone()
        sources = [row[0] for row in db.execute("""SELECT DISTINCT source_id FROM observations
            WHERE project_id=? AND station_id=? AND variable=? AND time_hour BETWEEN ? AND ?""" + scope + ' ORDER BY source_id', arguments)]
        expected = last - first + 1
        return {"projectId": PROJECT, "station": dict(site), "variable": variable, "unit": VARIABLES[variable][1],
                "sources": sources, "expected": expected, "records": statistics["records"], "valid": statistics["valid"],
                "missing": expected - statistics["valid"], "zeros": statistics["zeros"],
                "total": statistics["total"] if variable == "rain" else None,
                "maximum": statistics["maximum"], "minimum": statistics["minimum"],
                "range": {"start": timestamp(first), "end": timestamp(last)}}

    @staticmethod
    def _series_rows(records, first, last):
        records = iter(records)
        current = next(records, None)
        for time_hour in range(first, last + 1):
            recorded = current is not None and current[0] == time_hour
            yield {"time": timestamp(time_hour), "value": current[1] if recorded else None,
                   "recorded": recorded, "sourceId": current[2] if recorded else None}
            if recorded:
                current = next(records, None)

    def window(self, station, variable, start, end, offsetHours=0, sourceId=None):
        first, last = self._window_bounds(variable, start, end)
        expected = last - first + 1
        if type(offsetHours) is not int or not 0 <= offsetHours < expected:
            raise ValueError("显示起点须为查询时段内的非负整数小时")
        display_first = first + offsetHours
        display_last = min(last, display_first + DISPLAY_LIMIT_HOURS - 1)
        with self.connect() as db:
            # Statistics and the displayed segment share one read snapshot.
            db.execute("BEGIN")
            summary = self._window_summary(db, station, variable, first, last, sourceId)
            records = db.execute("""SELECT time_hour,value,source_id FROM observations
                WHERE project_id=? AND station_id=? AND variable=? AND time_hour BETWEEN ? AND ?""" + (' AND source_id=?' if sourceId else '') + ' ORDER BY time_hour',
                                 (PROJECT, station, variable, display_first, display_last) + ((sourceId,) if sourceId else ()))
            series = list(self._series_rows(records, display_first, display_last))
        return {**summary, "series": series,
                "display": {"start": timestamp(display_first), "end": timestamp(display_last),
                            "hours": len(series), "offsetHours": offsetHours, "totalHours": expected,
                            "limitHours": DISPLAY_LIMIT_HOURS, "hasPrevious": offsetHours > 0, "hasNext": display_last < last}}

    @contextmanager
    def export_window(self, station, variable, start, end, sourceId=None):
        """Validate before HTTP headers, then iterate a complete hourly range."""
        first, last = self._window_bounds(variable, start, end)
        with self.connect() as db:
            db.execute("BEGIN")
            summary = self._window_summary(db, station, variable, first, last, sourceId)
            records = db.execute("""SELECT time_hour,value,source_id FROM observations
                WHERE project_id=? AND station_id=? AND variable=? AND time_hour BETWEEN ? AND ?""" + (' AND source_id=?' if sourceId else '') + ' ORDER BY time_hour',
                                 (PROJECT, station, variable, first, last) + ((sourceId,) if sourceId else ()))
            yield summary, self._series_rows(records, first, last)

    def monitor(self, end):
        last = hour(end)
        start = timestamp(last - 23)
        grouped = {}
        for station in self.stations():
            site = grouped.setdefault(station["id"], {})
            site[station["variable"]] = self.window(station["id"], station["variable"], start, timestamp(last))
        result = {}
        for station_id, windows in grouped.items():
            rain = windows.get("rain", {}).get("series", [])
            flow = windows.get("flow", {}).get("series", [])
            level = windows.get("level", {}).get("series", [])
            cumulative, complete = 0, True
            rain_series = []
            for row in rain:
                if row["value"] is None:
                    complete = False
                else:
                    cumulative += row["value"]
                rain_series.append({**row, "label": row["time"][5:].replace("T", " "),
                                    "rain": row["value"], "cum": round(cumulative, 3) if complete else None})
            valid_times = [r["time"] for w in windows.values() for r in w["series"] if r["value"] is not None]
            result[station_id] = {
                "observationReady": bool(valid_times), "hasRainObservation": bool(rain), "hasRiverObservation": bool(flow or level),
                "rain1h": rain[-1]["value"] if rain else None,
                "rainTotal": rain_series[-1]["cum"] if rain_series else None,
                "flow": flow[-1]["value"] if flow else None, "level": level[-1]["value"] if level else None,
                "lastTime": max(valid_times).replace("T", " ") if valid_times else None,
                "quality": "历史实测", "sourceType": "database-observed", "interval": "逐小时",
                "sourceLabel": "水文资料库 · 原始实测记录", "rainSeries": rain_series,
                "flowSeries": [{**r, "label": r["time"][5:].replace("T", " "), "flow": r["value"]} for r in flow],
                "levelSeries": [{**r, "label": r["time"][5:].replace("T", " "), "level": r["value"]} for r in level],
            }
        return {"projectId": PROJECT, "start": start, "end": timestamp(last), "stations": result}

    def events(self, search=""):
        if len(search) > 100:
            raise ValueError("场次检索词过长")
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM flood_events WHERE project_id=? AND (instr(id,?)>0 OR instr(start,?)>0 OR instr(flood_code,?)>0) ORDER BY start DESC", (PROJECT, search, search, search))]

    def source_payload(self, body):
        return self.prepare_rain_source(body)["payload"]

    def prepare_rain_source(self, body):
        if not isinstance(body, dict) or set(body) != {"name", "stationIds", "start", "end"}:
            raise ValueError("请指定资料名称、雨量站和起止时间")
        ids = body["stationIds"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 32 or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids):
            raise ValueError("请选择 1 至 32 个不重复雨量站")
        # Processing keeps its separate yearly/value bounds even though raw
        # observation queries and exports can span the entire local archive.
        from input_products import MAX_VALUES, processing_window
        _, _, hours = processing_window(body["start"], body["end"])
        if len(ids) * hours > MAX_VALUES:
            raise ValueError("站点时序超过加工容量，请缩短时段")
        windows = [self.window(s, "rain", body["start"], body["end"]) for s in ids]
        if any(w["valid"] == 0 for w in windows):
            raise ValueError("选定站点在此时段没有有效雨量，请调整站点或时段")
        if any(w["station"]["lon"] is None or w["station"]["lat"] is None for w in windows):
            raise ValueError("选定雨量站缺少核实的空间坐标")
        with self.connect() as db:
            source_ids = sorted({source for window in windows for source in window["sources"]})
            evidence = [dict(db.execute("SELECT id,sha256 FROM source_files WHERE id=?", (s,)).fetchone()) for s in source_ids]
        payload = {"schemaVersion": "1.0", "projectId": PROJECT, "resolutionId": "1km", "name": body["name"],
                "provenanceNote": "现有 MDB 只读转换资料 / 数据库按实测记录提取 / 缺测保留 null / 来源摘要 " + encoded(evidence),
                "unit": "mm", "crs": "EPSG:4326", "timeConvention": "SOURCE_LOCAL_NAIVE_HOURLY",
                "times": [r["time"] for r in windows[0]["series"]],
                "stations": [{"id": w["station"]["id"], "lon": w["station"]["lon"], "lat": w["station"]["lat"], "values": [r["value"] for r in w["series"]]} for w in windows]}
        return {"payload": payload, "origin": {"type": "hydrology-database", "projectId": PROJECT,
                "schemaVersion": 1, "stationIds": list(ids), "start": payload["times"][0],
                "end": payload["times"][-1], "sourceFiles": evidence}}

    def index_task(self, status, queue):
        with self.connect() as db:
            db.execute("INSERT INTO task_index VALUES (?,?,?,?,?,?,?) ON CONFLICT(queue,id) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at",
                       (queue, status["taskId"], status.get("status") or "unknown", status.get("runType") or "", status.get("createdAt") or "", encoded(status), now()))

    def schemes(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,name,source_task_id AS sourceTaskId,created_at AS createdAt FROM schemes ORDER BY created_at DESC")]

    def save_scheme(self, name, task_id, request):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("方案名称须为 1 至 80 个字符")
        if request.get("runType") not in {"simulation", "calibration", "stateInitialization", "stateContinuation"}:
            raise ValueError("仅支持保存模拟、率定和起算状态方案")
        scheme_id = "SCHEME-" + uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO schemes VALUES (?,?,?,?,?,?)", (scheme_id, name.strip(), task_id, encoded(request), hashlib.sha256(encoded(request).encode()).hexdigest(), now()))
        return {"id": scheme_id, "name": name.strip(), "sourceTaskId": task_id}

    def scheme_request(self, scheme_id):
        with self.connect() as db:
            row = db.execute("SELECT request,sha256 FROM schemes WHERE id=?", (scheme_id,)).fetchone()
        if not row:
            raise KeyError("计算方案不存在")
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("计算方案摘要不一致")
        request = json.loads(row[0])
        request["requestId"] = "SCHEME-RUN-" + uuid.uuid4().hex
        return request

    def backup(self, destination=None):
        path = Path(destination) if destination else self.runtime / "database-backups" / f"hydrology-{uuid.uuid4().hex}.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ValueError("备份目标已存在，不覆盖已有备份")
        with self.connect() as source:
            target = sqlite3.connect(str(path))
            try:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("资料库备份完整性检查失败")
            finally:
                target.close()
        return {"name": path.name, "path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "integrity": "ok"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime")
    parser.add_argument("--backup")
    args = parser.parse_args()
    repository = HydroDatabase(args.runtime)
    print(encoded(repository.backup(args.backup) if args.backup else repository.import_existing()))
