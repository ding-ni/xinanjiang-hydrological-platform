"""Contracts, durable orchestration and real numerical handoff regression tests."""
import copy
import os
import sys
import tempfile
import json
import threading
import time
import urllib.request
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from common import RUNTIME, read_json, write_json
from contracts import normalize_request
from server import TaskManager, handler, ThreadingHTTPServer


def simulation(start="2017-06-19T11:00", end="2017-06-20T11:00"):
    return {"schemaVersion": "2.0", "requestId": "TEST-REQUEST", "runType": "simulation",
            "context": {"contextKey": "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km", "projectId": "nananxi-xufan", "resolutionId": "1km", "modelConfigId": "nananxi-xufan-1km"},
            "period": {"startTime": start, "endTime": end, "warmupDays": 1},
            "parameters": case_catalog()["cases"]["nananxi-xufan:1km"]["parameters"],
            "inputs": {"evaporation": {"mode": "monthly-climatology-assumption"}}, "lineage": {}}


def calibration():
    case = case_catalog()["cases"]["nananxi-xufan:1km"]
    events = [copy.deepcopy(row) for row in case["events"] if row["start"].startswith("2017")]
    for row, purpose in zip(events, ("率定", "验证", "独立检验")):
        row["purpose"] = purpose
    request = simulation()
    request.update(runType="calibration", calibration={"initialParameters": case["parameters"],
                   "optimizer": {"algorithm": "DIFFERENTIAL_EVOLUTION", "maxEvaluations": 4, "populationSize": 4},
                   "objectives": {"metrics": {"nse": True, "peak": True, "time": True}, "weights": {"nse": 0.5, "peak": 0.25, "time": 0.25}, "tolerances": {"peakPercent": 10, "peakTimeHours": 2}}})
    request["inputs"]["evaporation"] = {"mode": "constant-zero", "valueMmPerHour": 0}
    request["period"] = {"mode": "event", "warmupDays": 1, "events": events, "eventSet": {"selectedEventIds": [row["id"] for row in events], "version": "TEST-EVENTS"}}
    return request


class ContractTests(unittest.TestCase):
    def test_reference_request(self):
        self.assertEqual(normalize_request(simulation(), case_catalog())["runType"], "simulation")

    def test_reject_cross_case_and_resolution(self):
        for key, value in [("projectId", "selinco"), ("resolutionId", "0p1deg"), ("contextKey", "nananxi-xufan:other-config")]:
            request = simulation()
            request["context"][key] = value
            with self.assertRaises(ValueError):
                normalize_request(request, case_catalog())

    def test_reject_malformed_nested_request(self):
        mutations = [lambda r: r['inputs'].update(evaporation=None),
                     lambda r: r['period'].update(events=[None]),
                     lambda r: r['period'].update(eventSet=None),
                     lambda r: r['period']['eventSet'].update(selectedEventIds=[{}]),
                     lambda r: r['calibration']['objectives'].update(metrics=None),
                     lambda r: r['calibration']['objectives'].update(weights=None),
                     lambda r: r['calibration']['objectives']['metrics'].update(nse='yes'),
                     lambda r: r['calibration']['optimizer'].update(seed=-1)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                request = calibration()
                mutation(request)
                with self.assertRaises(ValueError):
                    normalize_request(request, case_catalog())

    def test_reject_nonfinite_and_forecast(self):
        for value in (None, float("nan"), float("inf"), True, "bad"):
            request = simulation()
            request["parameters"]["WM"] = value
            with self.assertRaises(ValueError):
                normalize_request(request, case_catalog())
        request = simulation()
        request["runType"] = "forecast"
        with self.assertRaises(ValueError):
            normalize_request(request, case_catalog())

    def test_reject_constraints_pet_and_non_hourly(self):
        for mutation in (lambda r: r["parameters"].update(WM=100), lambda r: r["inputs"].clear(), lambda r: r["period"].update(startTime="2017-06-19T11:30")):
            request = simulation()
            mutation(request)
            with self.assertRaises(ValueError):
                normalize_request(request, case_catalog())

    def test_requires_explicit_parent_for_continuation(self):
        request = simulation()
        request['runType'] = 'stateContinuation'
        with self.assertRaises(ValueError):
            normalize_request(request, case_catalog())
        for key in ('context', 'period', 'inputs', 'parameters'):
            request = simulation()
            request[key] = None
            with self.assertRaises(ValueError):
                normalize_request(request, case_catalog())

    def test_calibration_window_is_owned_by_registered_qc_catalog(self):
        request = calibration()
        request['period']['events'][0]['end'] = '2017-03-21T13:00:00'
        with self.assertRaisesRegex(ValueError, '窗口'):
            normalize_request(request, case_catalog())


class TaskTests(unittest.TestCase):
    def test_idempotency_cancel_restart_archive_clone(self):
        with tempfile.TemporaryDirectory() as temporary, patch("server.ThreadPoolExecutor"):
            manager = TaskManager(Path(temporary) / "tasks")
            request = simulation()
            task = manager.submit(request)
            self.assertEqual(task["taskId"], manager.submit(request)["taskId"])
            changed = copy.deepcopy(request)
            changed["parameters"]["KC"] = 0.9
            with self.assertRaises(ValueError):
                manager.submit(changed)
            self.assertEqual(manager.cancel(task["taskId"])["status"], "cancelled")
            cloned = manager.clone(task["taskId"])
            restarted = TaskManager(manager.root)
            self.assertEqual(restarted.get(cloned["taskId"])["status"], "failed")
            with self.assertRaises(RuntimeError):
                manager.result(task["taskId"])
            result = {"resultVersionId": "TEST-RESULT", "productionEligible": False}
            write_json(manager.root / task["taskId"] / "result.json", result)
            manager.update(task["taskId"], status="completed")
            self.assertEqual(manager.archive(task["taskId"])["status"], "archived")
            self.assertFalse(manager.result(task["taskId"])["productionEligible"])

    def test_http_contract_and_concurrent_status_reads(self):
        with tempfile.TemporaryDirectory() as temporary, patch('server.ThreadPoolExecutor'):
            manager = TaskManager(Path(temporary) / 'tasks')
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(manager, Path(temporary)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}/api/model/'

            def request(path, body=None, headers=None):
                req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers or {'Content-Type': 'application/json'})
                try:
                    with urllib.request.urlopen(req) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read())

            try:
                self.assertEqual(request('health')[0], 200)
                self.assertEqual(request('tasks', simulation(), {'Content-Type': 'application/json', 'Origin': 'https://untrusted.invalid'})[0], 400)
                invalid = simulation()
                invalid['context']['resolutionId'] = '0p1deg'
                self.assertEqual(request('tasks', invalid)[0], 400)
                self.assertEqual(request('forecast-tasks', simulation())[0], 400)
                self.assertEqual(request('calibration-tasks', simulation())[0], 400)
                code, task = request('tasks', simulation())
                self.assertEqual(code, 202)
                self.assertEqual(request(f'tasks/{task["taskId"]}/result')[0], 409)
                with patch.object(manager, 'replayability', side_effect=AssertionError('polling must not audit replayability')):
                    listed = request('tasks?includeReplayability=false')
                self.assertEqual(listed[0], 200)
                self.assertEqual(listed[1]['tasks'][0]['taskId'], task['taskId'])
                from concurrent.futures import ThreadPoolExecutor
                def update(index):
                    manager.update(task['taskId'], progress=index)
                    self.assertEqual(request(f'tasks/{task["taskId"]}')[0], 200)
                with ThreadPoolExecutor(max_workers=6) as executor:
                    list(executor.map(update, range(24)))
                self.assertEqual(request(f'tasks/{task["taskId"]}/cancel', {})[1]['status'], 'cancelled')
                self.assertFalse(manager.available_artifact(task['taskId']))
                self.assertFalse(manager.available_artifact('JOB-missing'))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                manager.close()


@unittest.skipUnless(os.environ.get("XAJ_TEST_REAL_MODEL") == "1", "Opt-in real model numerical regression")
class NumericalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        runtime = Path(self.temporary.name) / "runtime"
        for target in (__name__ + ".RUNTIME", "worker.RUNTIME"):
            patcher = patch(target, runtime)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_job(self, request, suffix):
        from worker import ModelJob
        task_id = f"JOB-numerical-{suffix}"
        folder = RUNTIME / "tasks" / task_id
        write_json(folder / "request.json", normalize_request(request, case_catalog()))
        write_json(folder / "status.json", {"taskId": task_id, "status": "running"})
        ModelJob(folder).run()
        write_json(folder / "status.json", {"taskId": task_id, "status": "completed"})
        return read_json(folder / "result.json"), read_json(folder / "checkpoint.json")

    def test_continuation_equals_continuous(self):
        import numpy as np
        whole, whole_state = self.run_job(simulation(end="2017-06-21T11:00"), "whole")
        first, _ = self.run_job(simulation(), "first")
        request = simulation(start="2017-06-20T12:00", end="2017-06-21T11:00")
        request["period"]["initialStateVersionId"] = first["stateVersionId"]
        second, second_state = self.run_job(request, "second")
        a = [row["value"] for row in whole["hydrograph"]["forecast"]]
        b = [row["value"] for row in first["hydrograph"]["forecast"] + second["hydrograph"]["forecast"]]
        np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-12)
        for key in whole_state["land"]:
            np.testing.assert_allclose(whole_state["land"][key], second_state["land"][key], rtol=1e-12, atol=1e-12)
        for key in whole_state["routing"]:
            np.testing.assert_allclose(whole_state["routing"][key], second_state["routing"][key], rtol=1e-12, atol=1e-12)
        self.assertEqual(whole["gridCount"], 110)
        self.assertFalse(whole["productionEligible"])
        self.assertTrue(all(np.isfinite(a)))
        self.assertEqual(second_state['parentStateVersionId'], first['stateVersionId'])
        self.assertEqual(second_state['forcingVersion'], 'precipitation_qc_20260819')
        self.assertEqual(second_state['observationVersion'], 'xufan_outlet_hourly_qc_20260819')
        self.assertEqual(second_state['petMode'], 'MONTHLY_CLIMATOLOGY_ASSUMPTION')
        self.assertEqual(second_state['inputManifestSHA256'], second['lineage']['inputManifestSHA256'])
        rainfall = whole['hydrograph']['forcing']['precipitation']
        self.assertEqual(len(rainfall), len(a))
        np.testing.assert_allclose(rainfall, first['hydrograph']['forcing']['precipitation'] + second['hydrograph']['forcing']['precipitation'], rtol=1e-12, atol=1e-12)
        self.assertTrue(all(np.isfinite(rainfall)))
        self.assertGreaterEqual(min(rainfall), 0)

    def test_real_three_group_calibration(self):
        from worker import ModelJob
        request = calibration()
        events = request['period']['events']
        folder = RUNTIME / "numerical-tests" / "calibration"
        write_json(folder / "request.json", normalize_request(request, case_catalog()))
        write_json(folder / "status.json", {"taskId": "JOB-calibration"})
        ModelJob(folder).run()
        result = read_json(folder / "result.json")
        self.assertEqual({row["group"] for row in result["eventMetrics"]}, {"CALIBRATION", "VALIDATION", "INDEPENDENT_TEST"})
        self.assertEqual(set(result["hydrographs"]), {row["id"] for row in events})
        self.assertEqual(result["optimizer"]["evaluations"], 4)
        self.assertFalse(result["independentTest"]["passed"])
        self.assertFalse(result["productionEligible"])
        for hydrograph in result['hydrographs'].values():
            self.assertEqual(len(hydrograph['forcing']['precipitation']), len(hydrograph['forecast']))
            self.assertEqual(hydrograph['forcing']['temperature'], [])

    def test_cancel_running_real_model_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = TaskManager(Path(temporary) / "tasks")
            task = manager.submit(simulation('2017-01-01T00:00', '2017-12-31T23:00'))
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    status = manager.get(task['taskId'])
                    if status['progress'] >= 10 or status['status'] not in {'queued', 'running'}:
                        break
                    time.sleep(0.05)
                self.assertEqual(status['status'], 'running')
                self.assertGreaterEqual(status['progress'], 10)
                process = manager.processes[task['taskId']]
                manager.cancel(task['taskId'])
                process.wait(timeout=10)
                deadline = time.monotonic() + 10
                while manager.get(task['taskId'])['status'] == 'cancelling' and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(manager.get(task['taskId'])['status'], 'cancelled')
                self.assertIsNotNone(process.poll())
                self.assertFalse(manager.available_artifact(task['taskId']))
                with self.assertRaises(RuntimeError):
                    manager.result(task['taskId'])
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
