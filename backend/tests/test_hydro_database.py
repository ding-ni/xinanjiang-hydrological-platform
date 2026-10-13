"""Transactional observation storage, immutable sources and HTTP business operations."""
import csv
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import write_json, read_json
from hydro_database import DISPLAY_LIMIT_HOURS, HydroDatabase, hour
import test_input_products_http as http_fixture
from test_input_version_contracts import model_request


def observations(root):
    root = Path(root)
    write_json(root / "spatial/stations.json", {"stations": [
        {"id": "70115800", "name": "Outlet", "lng": 119.5, "lat": 30.1},
        {"id": "70149600", "name": "Rain", "lng": 119.6, "lat": 30.2}]})
    write_json(root / "observations/catalog.json", {"rain": {"stations": [
        {"legacyCode": "87012", "spatialCode": "70115800", "name": "Outlet"},
        {"legacyCode": "87002", "spatialCode": "70149600", "name": "Rain"}]}})
    write_json(root / "observations/rain/2017.json", {"year": 2017, "timeEncoding": "hour-of-year-local",
        "stationCodes": ["87012", "87002"], "rows": [[0, 0, 0], [1, 0, None], [3, 0, 4.5], [0, 1, 2]]})
    write_json(root / "observations/river/2017.json", {"year": 2017, "timeEncoding": "hour-of-year-local",
        "station": {"spatialCode": "70115800"}, "rows": [[0, 3.2, 0], [1, None, 1.2]]})
    write_json(root / "events.json", {"packages": [{"eventId": "xf-test", "start": "2017-01-01T00:00", "end": "2017-01-01T03:00", "floodCode": "test"}]})
    return root


def extend_rain(database, public):
    write_json(public / "observations/rain/2018.json", {"year": 2018, "timeEncoding": "hour-of-year-local",
        "stationCodes": ["87012", "87002"], "rows": [[0, 0, 10], [1, 0, None], [26, 0, 14.5]]})
    database.import_existing(public, public / "events.json")


def conflicting_rain(database, time):
    with database.connect() as db:
        db.execute("INSERT INTO source_files VALUES (?,?,?,?,?,?,?)",
                   ("ALT-rain", "nananxi-xufan", "rain", "observations/rain/alternate.json", "0" * 64, 1, "test"))
        db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?)",
                   ("ALT-rain", "nananxi-xufan", "70115800", "rain", hour(time), 12))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.public = observations(self.root / "public")
        self.database = HydroDatabase(self.root / "runtime")
        self.database.import_existing(self.public, self.public / "events.json")

    def tearDown(self):
        self.temp.cleanup()

    def query(self, variable="rain", station="70115800"):
        return self.database.window(station, variable, "2017-01-01T00:00", "2017-01-01T03:00")

    def test_schema_foreign_keys_and_missing_are_distinct_from_zero(self):
        result = self.query()
        self.assertEqual((result["expected"], result["records"], result["valid"], result["zeros"], result["missing"]), (4, 3, 2, 1, 2))
        self.assertEqual([r["value"] for r in result["series"]], [0, None, None, 4.5])
        self.assertTrue(result["series"][1]["recorded"])
        self.assertFalse(result["series"][2]["recorded"])
        self.assertEqual(result["total"], 4.5)
        self.assertEqual(self.query("flow")["series"][0]["value"], 0)
        with self.database.connect() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO observations VALUES ('missing','nananxi-xufan','70115800','rain',1,0)")

    def test_monitor_uses_raw_values_and_never_fills_gaps(self):
        result = self.database.monitor("2017-01-01T03:00")
        outlet = result["stations"]["70115800"]
        self.assertEqual(outlet["rain1h"], 4.5)
        self.assertIsNone(outlet["rainTotal"])
        self.assertIsNone(outlet["flow"])
        self.assertEqual(outlet["flowSeries"][-4]["flow"], 0)
        self.assertEqual(outlet["levelSeries"][-4]["level"], 3.2)
        self.assertEqual(outlet["rainSeries"][-4]["rain"], 0)
        self.assertIsNone(outlet["rainSeries"][-2]["rain"])
        self.assertEqual(outlet["sourceType"], "database-observed")
        self.assertEqual(len(outlet["rainSeries"]), 24)
        with self.assertRaises(ValueError):
            self.database.monitor("2017-01-01T03:30")

    def test_import_is_idempotent_and_changed_source_is_rejected(self):
        before = self.database.summary()
        result = self.database.import_existing(self.public, self.public / "events.json")
        self.assertEqual(result["importedFiles"], 0)
        self.assertEqual(result["unchangedFiles"], 2)
        self.assertEqual(before, self.database.summary())
        path = self.public / "observations/rain/2017.json"
        data = read_json(path)
        data["rows"][0][2] = 99
        write_json(path, data)
        with self.assertRaisesRegex(ValueError, "发生变化"):
            self.database.import_existing(self.public, self.public / "events.json")
        self.assertEqual(self.query()["series"][0]["value"], 0)

    def test_failed_file_rolls_back_rows_and_registration(self):
        data = read_json(self.public / "observations/rain/2017.json")
        data.update(year=2018)
        data["rows"].append(data["rows"][0])
        write_json(self.public / "observations/rain/2018.json", data)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.import_existing(self.public, self.public / "events.json")
        with self.database.connect() as db:
            self.assertIsNone(db.execute("SELECT id FROM source_files WHERE id='MDB-rain-2018'").fetchone())
            self.assertEqual(db.execute("SELECT count(*) FROM observations WHERE source_id='MDB-rain-2018'").fetchone()[0], 0)

    def test_query_bounds_station_and_injection(self):
        for start, end in [("2017-01-01T00:30", "2017-01-01T03:00"), ("2017-01-02", "2017-01-01"),
                           ("2024-02-30T00:00", "2024-03-01T00:00"), ("2017-01-01", "10000-01-01"),
                           ("2017-01-01T00:00+08:00", "2017-01-02")]:
            with self.assertRaises(ValueError):
                self.database.window("70115800", "rain", start, end)
        for offset in [-1, 4, True, 1.5, "1"]:
            with self.assertRaises(ValueError):
                self.database.window("70115800", "rain", "2017-01-01T00:00", "2017-01-01T03:00", offsetHours=offset)
        with self.assertRaises(KeyError):
            self.query(station="' OR 1=1 --")
        with self.assertRaises(ValueError):
            self.query(variable="rain; DROP TABLE observations")
        self.assertEqual(self.database.events("' OR 1=1 --"), [])

    def test_cross_year_statistics_cover_the_selection_and_segments_are_contiguous(self):
        extend_rain(self.database, self.public)
        start, end = "2017-01-01T00:00", "2018-01-02T02:00"
        first = self.database.window("70115800", "rain", start, end)
        second = self.database.window("70115800", "rain", start, end, offsetHours=DISPLAY_LIMIT_HOURS)
        self.assertEqual(first["range"], {"start": start, "end": end})
        self.assertEqual((first["expected"], first["records"], first["valid"], first["missing"], first["zeros"]), (8787, 6, 4, 8783, 1))
        self.assertEqual((first["total"], first["minimum"], first["maximum"]), (29, 0, 14.5))
        self.assertEqual(first["sources"], ["MDB-rain-2017", "MDB-rain-2018"])
        for field in ("expected", "records", "valid", "missing", "zeros", "total", "minimum", "maximum", "sources", "range"):
            self.assertEqual(first[field], second[field])
        self.assertEqual(first["display"], {"start": start, "end": "2018-01-01T23:00", "hours": 8784,
                                          "offsetHours": 0, "totalHours": 8787, "limitHours": 8784,
                                          "hasPrevious": False, "hasNext": True})
        self.assertEqual(second["display"], {"start": "2018-01-02T00:00", "end": end, "hours": 3,
                                           "offsetHours": 8784, "totalHours": 8787, "limitHours": 8784,
                                           "hasPrevious": True, "hasNext": False})
        timeline = first["series"] + second["series"]
        self.assertEqual(len(timeline), first["expected"])
        self.assertEqual([hour(row["time"]) for row in timeline], list(range(hour(start), hour(end) + 1)))
        self.assertEqual(timeline[-1], {"time": end, "value": 14.5, "recorded": True, "sourceId": "MDB-rain-2018"})
        self.assertIsNone(timeline[1]["value"])
        self.assertTrue(timeline[1]["recorded"])
        self.assertIsNone(timeline[2]["value"])
        self.assertFalse(timeline[2]["recorded"])

    def test_leap_year_and_datetime_endpoints_keep_exact_inclusive_hours(self):
        annual = self.database.window("70115800", "rain", "2024-01-01T00:00", "2024-12-31T23:00")
        self.assertEqual(annual["expected"], 8784)
        self.assertEqual(len(annual["series"]), 8784)
        self.assertFalse(annual["display"]["hasNext"])
        for time in ("0001-01-01T00:00", "9999-12-31T23:00"):
            result = self.database.window("70115800", "rain", time, time)
            self.assertEqual((result["expected"], len(result["series"]), result["missing"]), (1, 1, 1))
            self.assertEqual(result["range"], {"start": time, "end": time})
        first, last = "0001-01-01T00:00", "9999-12-31T23:00"
        final = self.database.window("70115800", "rain", first, last, offsetHours=hour(last) - hour(first))
        self.assertEqual(final["series"][0]["time"], last)
        self.assertEqual(final["display"]["hours"], 1)
        with self.assertRaisesRegex(ValueError, "可表示"):
            self.database.monitor("0001-01-01T00:00")

    def test_conflict_outside_the_display_segment_rejects_query_and_export(self):
        extend_rain(self.database, self.public)
        start, end = "2017-01-01T00:00", "2018-01-02T02:00"
        conflicting_rain(self.database, end)
        with self.assertRaisesRegex(RuntimeError, "多个来源"):
            self.database.window("70115800", "rain", start, end)
        with self.assertRaisesRegex(RuntimeError, "多个来源"):
            with self.database.export_window("70115800", "rain", start, end):
                self.fail("A conflicting export must fail before yielding its stream")

    def test_rain_source_limits_are_independent_and_checked_before_station_reads(self):
        body = {"name": "rain", "stationIds": ["70115800"], "start": "2017-01-01T00:00", "end": "2018-01-02T02:00"}
        with patch.object(self.database, "window") as read:
            with self.assertRaisesRegex(ValueError, "8784"):
                self.database.prepare_rain_source(body)
            read.assert_not_called()
        body.update(stationIds=[f"S{index}" for index in range(32)], start="2024-01-01T00:00", end="2024-12-31T23:00")
        with patch.object(self.database, "window") as read:
            with self.assertRaisesRegex(ValueError, "加工容量"):
                self.database.prepare_rain_source(body)
            read.assert_not_called()

    def test_rain_source_uses_sql_values_aliases_coordinates_and_hashes(self):
        result = self.database.source_payload({"name": "window", "stationIds": ["70115800"], "start": "2017-01-01T00:00", "end": "2017-01-01T03:00"})
        self.assertEqual(result["stations"][0]["values"], [0, None, None, 4.5])
        self.assertEqual(result["stations"][0]["lon"], 119.5)
        self.assertIn("sha256", result["provenanceNote"])
        self.assertEqual(result["times"][0], "2017-01-01T00:00")

    def test_backup_restart_schemes_and_concurrent_reads(self):
        scheme = self.database.save_scheme("test", "JOB-test", {"runType": "simulation", "requestId": "OLD", "parameters": {"WM": 120}})
        restarted = HydroDatabase(self.root / "runtime")
        self.assertEqual(restarted.schemes()[0]["id"], scheme["id"])
        self.assertNotEqual(restarted.scheme_request(scheme["id"])["requestId"], "OLD")
        with ThreadPoolExecutor(max_workers=4) as pool:
            reads = list(pool.map(lambda _: self.query()["total"], range(12)))
        self.assertEqual(reads, [4.5] * 12)
        backup = self.database.backup()
        with sqlite3.connect(backup["path"]) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 8)
            self.assertEqual(db.execute("SELECT count(*) FROM schemes").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.database.backup(backup["path"])

    def test_scheme_tampering_and_unsupported_task_are_rejected(self):
        with self.assertRaises(ValueError):
            self.database.save_scheme("test", "JOB-test", {"runType": "forecast"})
        scheme = self.database.save_scheme("test", "JOB-test", {"runType": "simulation"})
        with self.database.connect() as db:
            db.execute("UPDATE schemes SET request='{}' WHERE id=?", (scheme["id"],))
        with self.assertRaises(ValueError):
            self.database.scheme_request(scheme["id"])

    def test_legacy_null_submission_time_is_indexed_without_inventing_a_date(self):
        legacy = {"taskId": "JOB-legacy", "status": "completed", "runType": None, "createdAt": None}
        self.database.index_task(legacy, "tasks")
        restarted = HydroDatabase(self.root / "runtime")
        with restarted.connect() as db:
            row = db.execute("SELECT created_at,run_type,payload FROM task_index WHERE id=?", (legacy["taskId"],)).fetchone()
        self.assertEqual(tuple(row[:2]), ("", ""))
        self.assertEqual(json.loads(row[2]), legacy)


class DatabaseHttpTests(unittest.TestCase):
    request = http_fixture.InputProductsHttpTests.request
    tearDown = http_fixture.InputProductsHttpTests.tearDown

    def setUp(self):
        http_fixture.InputProductsHttpTests.setUp(self)
        self.public = observations(self.fixture.root / "public")
        self.manager.database.import_existing(self.public, self.public / "events.json")

    def test_sql_queries_export_and_same_origin(self):
        self.assertEqual(self.request("database/summary")[1]["sourceFiles"], 2)
        self.assertEqual(len(self.request("database/stations")[1]["stations"]), 4)
        self.assertEqual(self.request("database/monitor?end=2017-01-01T03:00")[1]["stations"]["70115800"]["rain1h"], 4.5)
        query = "?stationId=70115800&variable=rain&start=2017-01-01T00:00&end=2017-01-01T03:00"
        self.assertEqual(self.request("database/window" + query)[1]["missing"], 2)
        with urllib.request.urlopen(self.base + "database/export" + query) as response:
            csv_text = response.read().decode("utf-8-sig")
            self.assertIn("2017-01-01T00:00,70115800,rain,0.0,mm,True", csv_text)
            self.assertIn("2017-01-01T02:00,70115800,rain,,mm,False", csv_text)
        self.assertEqual(self.request("database/summary?projectId=selinco")[0], 400)
        self.assertEqual(self.request("database/backup", "POST", {}, {"Origin": "http://evil.invalid"})[0], 400)
        self.assertEqual(self.request("database/backup", "POST", {})[1]["integrity"], "ok")

    def test_cross_year_http_segments_and_complete_csv_export(self):
        extend_rain(self.manager.database, self.public)
        query = "?stationId=70115800&variable=rain&start=2017-01-01T00:00&end=2018-01-02T02:00"
        code, first, _ = self.request("database/window" + query)
        self.assertEqual(code, 200)
        self.assertEqual((first["expected"], len(first["series"]), first["maximum"]), (8787, 8784, 14.5))
        code, last, _ = self.request("database/window" + query + "&offsetHours=8784")
        self.assertEqual(code, 200)
        self.assertEqual(last["display"]["hours"], 3)
        self.assertEqual(last["series"][-1]["sourceId"], "MDB-rain-2018")
        for offset in ("-1", "1.5", "true", "8787", ""):
            self.assertEqual(self.request("database/window" + query + "&offsetHours=" + offset)[0], 400)
        with patch.object(self.manager.database, "window", side_effect=AssertionError("CSV must not assemble display windows")):
            with urllib.request.urlopen(self.base + "database/export" + query, timeout=8) as response:
                self.assertEqual(response.headers["Connection"], "close")
                content = response.read()
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(content.count(b"\xef\xbb\xbf"), 1)
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        self.assertEqual(reader.fieldnames, ["time", "station_id", "variable", "value", "unit", "recorded", "source_id"])
        rows = list(reader)
        self.assertEqual(len(rows), 8787)
        self.assertEqual([hour(row["time"]) for row in rows], list(range(hour("2017-01-01T00:00"), hour("2018-01-02T02:00") + 1)))
        self.assertEqual((rows[0]["value"], rows[0]["recorded"], rows[0]["source_id"]), ("0.0", "True", "MDB-rain-2017"))
        self.assertEqual((rows[1]["value"], rows[1]["recorded"], rows[1]["source_id"]), ("", "True", "MDB-rain-2017"))
        self.assertEqual((rows[2]["value"], rows[2]["recorded"], rows[2]["source_id"]), ("", "False", ""))
        self.assertEqual((rows[-1]["time"], rows[-1]["value"], rows[-1]["source_id"]), ("2018-01-02T02:00", "14.5", "MDB-rain-2018"))
        self.manager.executor.submit.assert_not_called()
        self.input_manager.executor.submit.assert_not_called()

    def test_export_rejects_bad_requests_and_hidden_conflicts_before_csv_headers(self):
        query = "?stationId=70115800&variable=rain&start=2017-01-01T00:00&end=2018-01-02T02:00"
        for suffix in (query.replace("70115800", "unknown"), query.replace("variable=rain", "variable=bad"),
                       query.replace("2018-01-02T02:00", "2016-01-01T00:00"),
                       query.replace("2018-01-02T02:00", "10000-01-01T00:00")):
            code, error, headers = self.request("database/export" + suffix)
            self.assertIn(code, (400, 404))
            self.assertIn("error", error)
            self.assertTrue(headers["Content-Type"].startswith("application/json"))
        extend_rain(self.manager.database, self.public)
        conflicting_rain(self.manager.database, "2018-01-02T02:00")
        for operation in ("window", "export"):
            code, error, headers = self.request("database/" + operation + query)
            self.assertEqual(code, 409)
            self.assertIn("多个来源", error["error"])
            self.assertTrue(headers["Content-Type"].startswith("application/json"))

    def test_source_registration_connects_to_existing_processing_registry(self):
        body = {"name": "SQL rainfall", "stationIds": ["70115800"], "start": "2017-01-01T00:00", "end": "2017-01-01T03:00"}
        code, source, _ = self.request("database/rain-source", "POST", body)
        self.assertEqual(code, 200)
        self.assertEqual(source["missingValueCount"], 2)
        self.assertEqual(source["sourceOrigin"]["type"], "hydrology-database")
        self.assertEqual(source["sourceOrigin"]["stationIds"], ["70115800"])
        self.assertEqual(source["sourceOrigin"]["sourceFiles"][0]["id"], "MDB-rain-2017")
        self.assertEqual(len(source["sourceOrigin"]["sourceFiles"][0]["sha256"]), 64)
        self.assertIn("实测雨量", source["sourceLabel"])
        self.assertFalse(source["productionEligible"])
        self.assertEqual(self.request("input-sources")[1]["sources"][0]["sourceId"], source["sourceId"])
        payload = self.manager.database.source_payload(body)
        payload["sourceOrigin"] = source["sourceOrigin"]
        self.assertEqual(self.request("input-sources", "POST", payload)[0], 400)

    def test_scheme_submission_uses_normal_task_validation_and_index(self):
        legacy = {"taskId": "JOB-legacy", "status": "completed"}
        write_json(self.manager.root / "JOB-legacy/status.json", legacy)
        self.assertIn(legacy, self.manager.list())
        task = self.manager.submit(model_request())
        self.assertEqual(read_json(self.manager.root / "JOB-legacy/status.json"), legacy)
        self.assertEqual(self.request("database/schemes", "POST", {"name": "test", "taskId": task["taskId"]})[0], 400)
        self.manager.update(task["taskId"], status="completed")
        code, scheme, _ = self.request("database/schemes", "POST", {"name": "test", "taskId": task["taskId"]})
        self.assertEqual(code, 200)
        code, created, _ = self.request("database/schemes/" + scheme["id"] + "/run", "POST", {})
        self.assertEqual(code, 200)
        self.assertEqual(created["status"], "queued")
        self.assertNotEqual(created["taskId"], task["taskId"])
        self.assertFalse(created["productionEligible"])
        self.assertEqual(self.manager.database.summary()["tasks"], 2)


if __name__ == "__main__":
    unittest.main()
