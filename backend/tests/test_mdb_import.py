"""Strict MDB records, immutable registration and shared frozen input regression."""
from __future__ import annotations

import copy
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import read_json, write_json
from input_products import InputProducts, DEFAULT_BUNDLE_VERSION_ID, file_sha256
from input_service import InputService
from mdb_import import (normalize_records, normalize_events, semantic_summary,
                        compare_mdb_records, import_mdb_bundle, query_mdb_copy, read_mdb_records)
from mdb_import import reuse_verified_records
from mdb_import import interpolation_targets, TARGET_NC_CENTRE, TARGET_LEGACY_REPLAY
from mdb_import import QC_SOURCE_ORIGINAL, QC_PLATFORM_DIAGNOSTIC
from observation_quality import evaluate_observation_coverage, masked_volume_metrics
from strict_forcing import load_hourly_forcing
from test_input_service import InputFixture


def rainfall(values):
    start = datetime(2018, 1, 1)
    return [{"HS_Code": "87001", "YMDHM": (start + timedelta(hours=i)).isoformat(sep=" "), "DTRN": value}
            for i, value in enumerate(values)]


def records():
    return {"rain": rainfall([0, 2, 3, 4]),
            "flow": [{"stcd": "CGB087012", "ymdhm": f"2018-01-01 {hour:02d}:00:00", "q": hour + 1}
                     for hour in range(4)],
            "events": [{"flcd": "31180101", "s_dt": "2018-01-01 00:00:00", "e_dt": "2018-01-01 03:00:00"}]}


class StrictRecordTests(unittest.TestCase):
    def normalize(self, rows):
        return normalize_records(rows, station_field="HS_Code", time_field="YMDHM", value_field="DTRN", allowed_stations={"87001"})

    def test_missing_invalid_negative_nan_are_not_zero(self):
        rows = rainfall([0, None, "bad", -1, "NaN", 2])
        result, audit = self.normalize(rows)
        self.assertEqual(list(result.values()), [0, None, None, None, None, 2])
        self.assertEqual(audit["negative_as_missing"], 1)
        self.assertEqual(audit["missing_or_invalid"], 2)
        self.assertEqual(audit["nonfinite_as_missing"], 1)

    def test_identical_duplicate_kept_once_not_summed(self):
        rows = rainfall([2])
        rows.append({**rows[0], "DTRN": "2.0"})
        result, audit = self.normalize(rows)
        self.assertEqual(list(result.values()), [2])
        self.assertEqual(audit["identical_duplicates"], 1)

    def test_conflicting_duplicates_reject_even_two_invalid_values(self):
        for first, second in [(1, 2), (-1, -2), (None, 0)]:
            rows = rainfall([first])
            rows.append({**rows[0], "DTRN": second})
            with self.subTest(first=first), self.assertRaisesRegex(ValueError, "冲突"):
                self.normalize(rows)

    def test_unmapped_station_nonhourly_and_conflicting_event_reject(self):
        rows = rainfall([1])
        for mutation in ({"HS_Code": "unknown"}, {"YMDHM": "2018-01-01 00:30:00"}):
            with self.assertRaises(ValueError):
                self.normalize([{**rows[0], **mutation}])
        events = records()["events"]
        with self.assertRaisesRegex(ValueError, "窗口冲突"):
            normalize_events(events + [{**events[0], "e_dt": "2018-01-01 04:00:00"}])

    def test_semantic_comparison_ignores_identical_duplicate_but_detects_value_change(self):
        before, after = records(), records()
        after["rain"].append(copy.deepcopy(after["rain"][0]))
        summary = compare_mdb_records(before, after)
        self.assertTrue(summary["sameNormalizedRain"])
        self.assertTrue(summary["sameNormalizedFlow"])
        after["rain"][1]["DTRN"] = 20
        changed = compare_mdb_records(before, after)
        self.assertFalse(changed["sameNormalizedRain"])
        self.assertEqual(changed["normalizedDifferences"]["rain"]["changedCount"], 1)
        self.assertEqual(changed["normalizedDifferences"]["rain"]["changedSamples"][0]["after"], 20)

    def test_source_value_changes_remain_visible_after_missing_reclassification(self):
        before, after = records(), records()
        before["rain"][0]["DTRN"], after["rain"][0]["DTRN"] = -1, -2
        comparison = compare_mdb_records(before, after)
        self.assertTrue(comparison["sameNormalizedRain"])
        self.assertFalse(comparison["sameSourceRainValues"])
        self.assertEqual(comparison["normalizedDifferences"]["rain"]["changedCount"], 0)
        self.assertEqual(comparison["sourceValueDifferences"]["rain"]["changedCount"], 1)

    def test_iso_event_records_are_valid_but_timezone_is_not_assumed(self):
        source = records()
        source["rain"][0]["YMDHM"] = "2018-01-01T00:00:00"
        self.assertEqual(list(self.normalize(source["rain"])[0].values())[0], 0)
        source["rain"][0]["YMDHM"] = "2018-01-01T00:00:00+08:00"
        with self.assertRaises(ValueError):
            self.normalize(source["rain"])

    def test_reading_scope_covers_needed_history_and_compares_same_interval(self):
        calls = []
        source = records()
        def query(snapshot, folder, sql, **kwargs):
            calls.append(sql)
            return source["events"] if "FROM fl_code" in sql else source["rain"] if "FROM ReFactorDT" in sql else source["flow"]
        data = read_mdb_records("TEST.mdb", "TEST-QUERY", query=query)
        self.assertEqual(data["_queryScope"]["start"], "2017-12-25T00:00:00")
        self.assertEqual(data["_queryScope"]["end"], "2018-01-01T03:00:00")
        self.assertIn("#12/25/2017 00:00:00#", calls[1])
        self.assertIn("#01/01/2018 03:00:00#", calls[2])
        self.assertNotIn("BETWEEN #", calls[0])
        later = copy.deepcopy(source)
        later["events"][0]["s_dt"] = "2020-01-01 00:00:00"
        later["events"][0]["e_dt"] = "2020-01-01 03:00:00"
        source = later
        comparison = read_mdb_records("TEST.mdb", "TEST-QUERY", query=query, scope=data["_queryScope"])
        self.assertEqual(comparison["_queryScope"], data["_queryScope"])
        self.assertFalse(compare_mdb_records(data, comparison)["sameEventWindows"])

    def test_closed_ring_source_mean_is_explicitly_distinct_from_grid_centre(self):
        grids = [{"unit_id": "G1", "source_row": 1, "source_col": 1}]
        geometry = {"features": [{"properties": {"row": 1, "col": 1}, "geometry": {
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}]}
        centre = interpolation_targets(grids, geometry, [.5], [.5])
        legacy = interpolation_targets(grids, geometry, [.5], [.5], TARGET_LEGACY_REPLAY)
        self.assertEqual((centre[0]["longitude"], centre[0]["latitude"]), (.5, .5))
        self.assertEqual((legacy[0]["longitude"], legacy[0]["latitude"]), (.4, .4))
        self.assertAlmostEqual(centre[0]["longitude"] - legacy[0]["longitude"], .1)
        with self.assertRaises(ValueError):
            interpolation_targets(grids, geometry, [.5], [.5], "ASSUMED")


class ObservationCoverageTests(unittest.TestCase):
    def setUp(self):
        self.times = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(100)]

    def test_coverage_long_gap_and_phase_flags_new_policy_reject_old_report_only(self):
        flow = [1.0] * 100
        flow[49] = 10
        flow[:49] = [None] * 49
        quality = evaluate_observation_coverage(self.times, flow, {"observation_coverage": {}})
        self.assertFalse(quality["accepted"])
        self.assertEqual(quality["maximum_missing_gap_hours"], 49)
        self.assertAlmostEqual(quality["coverage_fraction"], .51)
        self.assertIn("OBSERVED_COVERAGE_BELOW_MINIMUM", quality["exclusion_reasons"])
        self.assertIn("OBSERVED_MISSING_GAP_EXCEEDS_MAXIMUM", quality["exclusion_reasons"])
        self.assertIn("OBSERVED_PHASE_COVERAGE_BELOW_MINIMUM", quality["exclusion_reasons"])
        legacy = evaluate_observation_coverage(self.times, flow)
        self.assertTrue(legacy["accepted"])
        self.assertEqual(legacy["exclusion_reasons"], [])
        self.assertEqual(legacy["policy"]["compatibility"], "HISTORICAL_REPORT_ONLY")

    def test_peak_coverage_is_estimated_and_full_volume_missing_is_not_extrapolated(self):
        quality = evaluate_observation_coverage(self.times, [1] * 99 + [None])
        self.assertFalse(quality["true_peak_confirmed"])
        result = masked_volume_metrics(self.times, [1] * 99 + [None], [2] * 100)
        self.assertEqual(result["masked_observed_volume_m3"], 99 * 3600)
        self.assertEqual(result["masked_volume_bias_percent"], 100)
        self.assertIsNone(result["complete_observed_volume_m3"])
        self.assertFalse(result["full_volume_error_available"])
        self.assertEqual(result["volume_scope"], "COMMON_VALID_HOURS_ONLY")

    def test_thresholds_are_configurable_and_invalid_values_reject(self):
        flow = [1] * 90 + [None] * 10
        relaxed = {"observation_coverage": {"minimum_observed_coverage": .8, "minimum_phase_coverage": .8}}
        self.assertTrue(evaluate_observation_coverage(self.times, flow, relaxed)["accepted"])
        strict = {"observation_coverage": {"minimum_observed_coverage": .95}}
        self.assertFalse(evaluate_observation_coverage(self.times, flow, strict)["accepted"])
        with self.assertRaises(ValueError):
            evaluate_observation_coverage(self.times, flow, {"observation_coverage": {"minimum_observed_coverage": True}})


class ImportRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = InputFixture(self.root / "model")
        self.products = InputProducts(self.fixture.root, self.root / "runtime")
        self.source_path = self.root / "original.mdb"
        self.source_path.write_bytes(b"TEST_ONLY_IMMUTABLE_MDB_SOURCE")

    def tearDown(self):
        self.temp.cleanup()

    def import_fixture(self):
        return import_mdb_bundle(self.source_path, products=self.products, records=records(), name="TEST fixture MDB")

    def test_source_is_content_addressed_idempotent_and_checks_snapshot_tampering(self):
        original = file_sha256(self.source_path)
        a = self.products.register_mdb_source(self.source_path, name="TEST", provenance_note="TEST only")
        b = self.products.register_mdb_source(self.source_path, name="TEST again", provenance_note="TEST only")
        self.assertEqual(a["sourceId"], b["sourceId"])
        self.assertEqual(a["manifest"]["databaseSHA256"], original)
        self.assertEqual(file_sha256(self.source_path), original)
        database = self.products._path(read_json(a["path"])["databaseFile"])
        database.write_bytes(b"TAMPER-TEST")
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.products.source(a["sourceId"])

    def test_independent_bundle_preserves_default_and_same_viewer_worker_nc(self):
        before = self.fixture.hashes()
        original = file_sha256(self.source_path)
        imported = self.import_fixture()
        product_id = imported["registeredProduct"]["bundleVersionId"]
        bundle = self.products.resolve(product_id)
        self.assertEqual(self.products.resolve()["bundleVersionId"], DEFAULT_BUNDLE_VERSION_ID)
        self.assertEqual(self.products.resolve()["paths"]["flow"], self.fixture.root / "data_interface/observations/xufan_outlet_hourly_qc.csv")
        self.assertNotEqual(bundle["paths"]["flow"], self.products.resolve()["paths"]["flow"])
        service = InputService(self.fixture.root, bundle)
        displayed = service.window("2018-01-01T00:00", "2018-01-01T03:00")
        times, values, counts, audit = load_hourly_forcing(bundle, service.grids, "2018-01-01T00:00", "2018-01-01T03:00")
        self.assertEqual(displayed["product"]["times"], [time.isoformat() for time in times])
        self.assertEqual([cell["values"] for cell in displayed["product"]["cells"]], values.T.tolist())
        self.assertEqual(displayed["observationPreview"]["river"]["flows"], [1, 2, 3, 4])
        self.assertEqual(counts.tolist(), [1] * 4)
        self.assertEqual(len(service.catalog()["events"]), 1)
        self.assertEqual(before, self.fixture.hashes())
        self.assertEqual(original, file_sha256(self.source_path))
        json.dumps(imported, allow_nan=False)

    def test_full_year_unrecorded_future_is_missing_but_requested_prefix_works(self):
        imported = self.import_fixture()
        bundle = self.products.resolve(imported["bundleVersionId"])
        service = InputService(self.fixture.root, bundle)
        service._initialize()
        _, values, _, _ = load_hourly_forcing(bundle, service.grids, "2018-01-01T00:00", "2018-01-01T02:00")
        self.assertEqual(values[:, 0].tolist(), [0, 2, 3])
        with self.assertRaisesRegex(ValueError, "实际所需强迫时段缺测"):
            load_hourly_forcing(bundle, service.grids, "2017-12-31T23:00", "2018-01-01T02:00")
        result = service.window("2018-01-01T03:00", "2018-01-01T05:00")
        self.assertEqual(result["product"]["cells"][0]["values"], [4, None, None])

    def test_hourly_idw_uses_only_valid_stations_and_keeps_real_zero(self):
        source = records()
        source["rain"] = rainfall([0, None, -1, "bad"])
        source["rain"] += [{**row, "HS_Code": "87002"} for row in rainfall([-1, 2, 3, None])]
        imported = import_mdb_bundle(self.source_path, products=self.products, records=source)
        bundle = self.products.resolve(imported["bundleVersionId"])
        service = InputService(self.fixture.root, bundle)
        displayed = service.window("2018-01-01T00:00", "2018-01-01T03:00")
        self.assertEqual(displayed["summary"]["reportingStationCount"], [1, 1, 1, 0])
        self.assertEqual(displayed["product"]["cells"][0]["values"], [0, 2, 3, None])
        with self.assertRaisesRegex(ValueError, "实际所需强迫时段缺测"):
            load_hourly_forcing(bundle, service.grids, "2018-01-01T00:00", "2018-01-01T03:00")

    def test_source_replay_preserves_float64_and_keeps_other_registered_products(self):
        before = self.import_fixture()
        source = records()
        source["rain"][1]["DTRN"] = 1.1234567890123456
        legacy = import_mdb_bundle(self.source_path, products=self.products, records=source,
                                   target_coordinate_policy=TARGET_LEGACY_REPLAY)
        self.assertNotEqual(before["bundleVersionId"], legacy["bundleVersionId"])
        self.assertEqual(self.products.resolve(before["bundleVersionId"])["manifestSHA256"], before["manifestSHA256"])
        bundle = self.products.resolve(legacy["bundleVersionId"])
        self.assertEqual(bundle["manifest"]["targetCoordinatePolicy"], TARGET_LEGACY_REPLAY)
        self.assertEqual(bundle["manifest"]["forcingValueDtype"], "float64")
        self.assertTrue(bundle["manifest"]["sourceReplayOnly"])
        self.assertFalse(bundle["manifest"]["productionEligible"])
        service = InputService(self.fixture.root, bundle)
        shown = service.window("2018-01-01T00:00", "2018-01-01T03:00")
        times, actual, counts, _ = load_hourly_forcing(bundle, service.grids, "2018-01-01T00:00", "2018-01-01T03:00")
        self.assertAlmostEqual(actual[1, 0], 1.1234567890123456, places=14)
        self.assertEqual(shown["product"]["cells"][0]["values"][1], actual[1, 0])
        import xarray as xr
        with xr.open_dataset(bundle["precipitationDir"] / "nananxi_precip_hourly_1km_2018.nc", engine="h5netcdf") as dataset:
            self.assertEqual(str(dataset.precipitation.dtype), "float64")
            self.assertEqual(dataset.attrs["target_coordinate_policy"], TARGET_LEGACY_REPLAY)

    def test_source_qc_copies_exact_snapshot_and_reports_partial_flow_without_new_rejection(self):
        old = self.import_fixture()
        quality_path = self.root / "source-event-quality.json"
        config = read_json(self.fixture.root / "config/event_quality_config.json")
        config["rules"]["maximum_duration_hours"] = 720
        write_json(quality_path, config)
        digest = file_sha256(quality_path)
        # A small explicit test snapshot substitutes its known digest. Real
        # imports remain pinned to the CAL source snapshot constant.
        source = records()
        source["flow"] = [source["flow"][2]]
        with patch("mdb_import.SOURCE_QUALITY_SHA256", digest):
            imported = import_mdb_bundle(self.source_path, products=self.products, records=source,
                                        target_coordinate_policy=TARGET_LEGACY_REPLAY,
                                        quality_policy=QC_SOURCE_ORIGINAL, source_quality_config=quality_path)
        bundle = self.products.resolve(imported["bundleVersionId"])
        self.assertEqual(bundle["paths"]["quality"].read_bytes(), quality_path.read_bytes())
        self.assertEqual(bundle["manifest"]["eventQualityConfigurationSHA256"], digest)
        self.assertEqual(bundle["public"]["sourceQcPolicy"], QC_SOURCE_ORIGINAL)
        self.assertNotIn("observation_coverage", read_json(bundle["paths"]["quality"]))
        event = InputService(self.fixture.root, bundle).event("xf-31180101", warmup_hours=0)
        self.assertTrue(event["quality"]["accepted"])
        self.assertAlmostEqual(event["quality"]["flow_completeness"], .25)
        coverage = event["quality"]["observation_coverage"]
        self.assertFalse(coverage["policy"]["enforce"])
        self.assertEqual(coverage["exclusion_reasons"], [])
        self.assertIn("OBSERVED_COVERAGE_BELOW_MINIMUM", coverage["warning_reasons"])
        source_event = read_json(bundle["paths"]["events"])["packages"][0]
        self.assertIsNone(source_event["observedInputs"]["volumeM3"])
        self.assertEqual(source_event["observedInputs"]["maskedVolumeM3"], 3 * 3600)
        self.assertEqual(self.products.resolve(old["bundleVersionId"])["manifestSHA256"], old["manifestSHA256"])

    def test_source_qc_720h_limit_preserves_721h_window_as_excluded(self):
        start = datetime(2018, 1, 1)
        source = {"events": [{"flcd": "31180101", "s_dt": start.isoformat(), "e_dt": (start + timedelta(hours=720)).isoformat()}],
                  "rain": rainfall([1] * 721),
                  "flow": [{"stcd": "CGB087012", "ymdhm": (start + timedelta(hours=i)).isoformat(), "q": 1}
                           for i in range(721)]}
        quality_path = self.root / "source-quality.json"
        config = read_json(self.fixture.root / "config/event_quality_config.json")
        config["rules"]["maximum_duration_hours"] = 720
        write_json(quality_path, config)
        with patch("mdb_import.SOURCE_QUALITY_SHA256", file_sha256(quality_path)):
            imported = import_mdb_bundle(self.source_path, products=self.products, records=source,
                                        target_coordinate_policy=TARGET_LEGACY_REPLAY,
                                        quality_policy=QC_SOURCE_ORIGINAL, source_quality_config=quality_path)
        bundle = self.products.resolve(imported["bundleVersionId"])
        service = InputService(self.fixture.root, bundle)
        self.assertEqual(len(service.catalog()["events"]), 1)
        event = service.event("xf-31180101", warmup_hours=0)
        self.assertEqual(len(event["product"]["times"]), 721)
        self.assertFalse(event["quality"]["accepted"])
        self.assertEqual(event["quality"]["exclusion_reasons"], ["DURATION_EXCEEDS_MAXIMUM"])
        self.assertEqual(bundle["manifest"]["summary"]["acceptedEventIds"], [])
        self.assertEqual(bundle["manifest"]["summary"]["excludedEvents"][0]["eventId"], "xf-31180101")

    def test_source_qc_rejects_wrong_snapshot_or_implicit_target_before_registration(self):
        quality_path = self.root / "wrong-quality.json"
        write_json(quality_path, read_json(self.fixture.root / "config/event_quality_config.json"))
        for kwargs in ({"target_coordinate_policy": TARGET_NC_CENTRE},
                       {"target_coordinate_policy": TARGET_LEGACY_REPLAY},
                       {"target_coordinate_policy": TARGET_LEGACY_REPLAY, "quality_policy": QC_PLATFORM_DIAGNOSTIC}):
            request = {"quality_policy": QC_SOURCE_ORIGINAL, "source_quality_config": quality_path, **kwargs}
            with self.subTest(request=request), self.assertRaises(ValueError):
                import_mdb_bundle(self.source_path, products=self.products, records=records(), **request)
        self.assertFalse(self.products.sources_root.exists())
        self.assertFalse(self.products.tasks_root.exists())

    def test_strict_loader_negative_is_missing_and_event_audit_excludes_warmup_counts(self):
        self.fixture.precip[0, 1, 0] = -2
        self.fixture.write_precipitation()
        bundle = self.products.resolve()
        bundle["manifest"]["forcingPolicy"] = "STRICT_MISSING"
        service = InputService(self.fixture.root, bundle)
        window = service.window("2017-12-31T22:00", "2018-01-01T03:00")
        self.assertIsNone(window["product"]["cells"][0]["values"][0])
        self.assertEqual(window["quality"]["negativePrecipitationClippedCount"], 0)
        self.assertEqual(window["quality"]["negativePrecipitationReclassifiedMissingCount"], 1)
        self.assertIn("NEGATIVE_PRECIPITATION_RECLASSIFIED_MISSING", window["quality"]["warningReasons"])
        event = service.event("xf-31180101", warmup_hours=2)
        self.assertEqual(event["inputQuality"]["strictForcingAudit"]["requested_hours"], 4)
        self.assertEqual(event["inputQuality"]["negativePrecipitationReclassifiedMissingCount"], 0)
        self.assertFalse(event["quality"]["model_input_ready"])

    def test_timeout_fails_before_registering_a_product_and_original_stays_unchanged(self):
        before = file_sha256(self.source_path)
        with patch("mdb_import.read_mdb_records", side_effect=RuntimeError("MDB_DRIVER_TIMEOUT")):
            with self.assertRaisesRegex(RuntimeError, "TIMEOUT"):
                import_mdb_bundle(self.source_path, products=self.products)
        self.assertFalse(self.products.registry_root.exists())
        failed = next(self.products.tasks_root.glob("DATA-mdb-*"))
        self.assertEqual(read_json(failed / "status.json")["status"], "failed")
        self.assertFalse(read_json(failed / "import-audit.json")["productRegistered"])
        self.assertEqual(before, file_sha256(self.source_path))

    def test_query_only_copy_select_timeout_and_readonly_script(self):
        def runner(command, **kwargs):
            self.assertEqual(kwargs["timeout"], 2)
            self.assertNotEqual(Path(command[-2]), self.source_path)
            self.assertIn("//B", command)
            self.assertEqual(Path(command[-2]).read_bytes(), self.source_path.read_bytes())
            return subprocess.CompletedProcess(command, 0, b'"flcd","s_dt","e_dt"\n"31180101","2018-01-01 00:00:00","2018-01-01 03:00:00"\n__MDB_QUERY_COMPLETE__,1\n', b"")
        queried = query_mdb_copy(self.source_path, self.root / "query-copies", "SELECT flcd,s_dt,e_dt FROM fl_code", timeout=2, runner=runner)
        self.assertEqual(queried[0]["flcd"], "31180101")
        script = Path(__file__).resolve().parents[1] / "mdb_query_readonly.vbs"
        self.assertIn("cn.Mode = 1", script.read_text(encoding="utf-8"))
        self.assertIn("Mode=Read", script.read_text(encoding="utf-8"))
        self.assertNotIn("WScript.Echo", script.read_text(encoding="utf-8"))
        self.assertIn("WScript.StdOut.WriteLine", script.read_text(encoding="utf-8"))
        self.assertIn("__MDB_QUERY_COMPLETE__", script.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            query_mdb_copy(self.source_path, self.root / "query-copies", "SELECT * INTO other FROM fl_code", runner=runner)
        with patch("mdb_import.subprocess.run", side_effect=subprocess.TimeoutExpired("driver", 2)):
            with self.assertRaisesRegex(RuntimeError, "TIMEOUT"):
                query_mdb_copy(self.source_path, self.root / "query-copies", "SELECT * FROM fl_code", timeout=2, runner=subprocess.run)

    def test_zero_exit_truncated_output_is_never_treated_as_complete_records(self):
        for output in (b'"flcd","s_dt","e_dt"\n"31180101",',
                       b'"flcd","s_dt","e_dt"\n__MDB_QUERY_COMPLETE__,1\n',
                       b'"flcd","s_dt","e_dt"\n"31180101",\n__MDB_QUERY_COMPLETE__,1\n'):
            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, output, b"")
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, "INCOMPLETE_OUTPUT"):
                query_mdb_copy(self.source_path, self.root / "query-copies", "SELECT * FROM fl_code", timeout=2, runner=runner)

    def test_reuse_requires_complete_source_bound_query_receipts_and_rechecks_csv(self):
        task_id = "DATA-mdb-" + "a" * 32
        folder = self.products.tasks_root / task_id
        source = records()
        def runner(command, **kwargs):
            sql = command[-1]
            role = "events" if "FROM fl_code" in sql else "rain" if "FROM ReFactorDT" in sql else "flow"
            rows = source[role]
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            output = (stream.getvalue() + f"__MDB_QUERY_COMPLETE__,{len(rows)}\n").encode("ascii")
            return subprocess.CompletedProcess(command, 0, output, b"")
        def query(snapshot, query_folder, sql, **kwargs):
            return query_mdb_copy(snapshot, query_folder, sql, timeout=2, runner=runner)
        queried = read_mdb_records(self.source_path, folder / "query-copies", query=query)
        write_json(folder / "request.json", {"recordQueryScope": queried["_queryScope"]})
        reused = reuse_verified_records(self.products, task_id, self.source_path)
        self.assertTrue(compare_mdb_records(queried, reused)["sameNormalizedRain"])
        self.assertEqual(reused["_reusedQueryTask"], task_id)
        imported = import_mdb_bundle(self.source_path, products=self.products, reuse_query_task=task_id)
        self.assertEqual(imported["summary"]["eventCount"], 1)
        next((folder / "query-copies").glob("*.records.csv")).write_bytes(b"TAMPER-TEST")
        with self.assertRaisesRegex(ValueError, "摘要不一致"):
            reuse_verified_records(self.products, task_id, self.source_path)
        with self.assertRaises(ValueError):
            reuse_verified_records(self.products, "../other-task", self.source_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
