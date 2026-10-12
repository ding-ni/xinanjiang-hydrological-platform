"""Opt-in real numerical equivalence of grid replay and checkpoint handoff.

Runs short, registered QC windows in a temporary runtime. It does not write any
vendor file or publish a parameter; all numerical kernels remain unchanged.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from common import MODEL, read_json, write_json
from contracts import normalize_request


def simulation_request(start="2017-03-19T13:00", end="2017-03-21T12:00", parent=None):
    request = {"schemaVersion": "2.0", "requestId": "TEST-GRID-HISTORY",
               "runType": "stateContinuation" if parent else "simulation",
               "context": {"projectId": "nananxi-xufan", "resolutionId": "1km",
                           "modelConfigId": "nananxi-xufan-1km",
                           "contextKey": "nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km"},
               "period": {"startTime": start, "endTime": end, "warmupDays": 7},
               "parameters": case_catalog()["cases"]["nananxi-xufan:1km"]["parameters"],
               "inputs": {"evaporation": {"mode": "monthly-climatology-assumption"}}, "lineage": {}}
    if parent:
        request["period"]["initialStateVersionId"] = parent
    return normalize_request(request, case_catalog())


@unittest.skipUnless(os.environ.get("XAJ_TEST_REAL_MODEL") == "1", "Opt-in real model grid-history regression")
class GridHistoryNumericalTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import pandas as pd
        import worker
        self.np, self.pd, self.worker = np, pd, worker
        self.temporary = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temporary.name)
        self.runtime_patch = patch("worker.RUNTIME", self.test_root / "runtime")
        self.runtime_patch.start()

    def tearDown(self):
        self.runtime_patch.stop()
        self.temporary.cleanup()

    def create_job(self, request, name):
        task_id = f"JOB-history-{name}"
        folder = self.worker.RUNTIME / "tasks" / task_id
        write_json(folder / "request.json", request)
        write_json(folder / "status.json", {"taskId": task_id, "status": "running",
                                            "contextKey": request["context"]["contextKey"]})
        return self.worker.ModelJob(folder)

    def run_job(self, request, name):
        job = self.create_job(request, name)
        job.run()
        status = read_json(job.folder / "status.json")
        write_json(job.folder / "status.json", {**status, "status": "completed"})
        with self.np.load(job.folder / "grid-history.npz", allow_pickle=False) as archive:
            history = {field: archive[field].copy() for field in archive.files}
        return (job, read_json(job.folder / "result.json"),
                read_json(job.folder / "checkpoint.json"),
                read_json(job.folder / "grid-history.json"), history)

    def test_one_hour_history_matches_original_batch_kernel(self):
        np, pd = self.np, self.pd
        job = self.create_job(simulation_request(), "batch-equivalence")
        start, end = pd.Timestamp(job.period["startTime"]), pd.Timestamp(job.period["endTime"])
        event = job.load([{"event_id": job.task_id, "year": start.year, "start": start,
                           "end": end, "group": "SIMULATION"}], 168)[0]
        event.pet_mm_h = self.worker.PetProvider.from_file(MODEL / "config/pet_forcing_assumed_monthly.json").values(event.times)
        initial = job.initial_state(job.params)
        rs, ri, rg, expected_land, diagnostics = self.worker.simulate_xaj_liquid_grids(
            event.precip, job.params, event.pet_mm_h, initial, return_diagnostics=True)
        expected_flow, batch_land, expected_routing = self.worker.kernels.route_event_fast(
            event, job.params, job.routing, job.router, job.mapping, initial)
        job.run()
        checkpoint = read_json(job.folder / "checkpoint.json")
        result = read_json(job.folder / "result.json")
        metadata = read_json(job.folder / "grid-history.json")
        first = int(event.times.searchsorted(event.start))
        expected_times = [time.isoformat() for time in event.times[first:]]
        actual_flow = np.array([row["value"] for row in result["hydrograph"]["forecast"]])
        expected_flow = expected_flow.loc[start:end].to_numpy()
        np.testing.assert_allclose(actual_flow, expected_flow, rtol=1e-12, atol=1e-12)
        errors = {"controlFlowMaxAbsM3s": float(np.max(np.abs(actual_flow - expected_flow)))}
        with np.load(job.folder / "grid-history.npz", allow_pickle=False) as archive:
            self.assertEqual(archive["times"].tolist(), expected_times)
            self.assertEqual(metadata["times"], expected_times)
            self.assertEqual(len(expected_times), 48)
            for field, expected in (("RS", rs[first:]), ("RI", ri[first:]), ("RG", rg[first:]),
                                    ("precipitation", event.precip[first:]),
                                    ("et_actual", diagnostics["et_actual"][first:]),
                                    ("water_balance_error", diagnostics["water_balance_error"][first:])):
                self.assertEqual(archive[field].shape, (48, 110))
                np.testing.assert_allclose(archive[field], expected, rtol=0, atol=0)
                errors[field + "MaxAbs"] = float(np.max(np.abs(archive[field] - expected)))
            np.testing.assert_allclose(archive["pet"], np.repeat(event.pet_mm_h[first:, None], 110, axis=1), rtol=0, atol=0)
            for field, expected in expected_land.items():
                np.testing.assert_allclose(checkpoint["land"][field], expected, rtol=1e-12, atol=1e-12)
                np.testing.assert_allclose(batch_land[field], expected, rtol=0, atol=0)
                np.testing.assert_allclose(archive[field][-1], checkpoint["land"][field], rtol=0, atol=0)
                errors[field + "FinalMaxAbs"] = float(np.max(np.abs(np.array(checkpoint["land"][field]) - expected)))
            self.assertFalse(any(archive[field].dtype.kind == "O" for field in archive.files))
        for field, expected in expected_routing.items():
            np.testing.assert_allclose(checkpoint["routing"][field], expected, rtol=1e-12, atol=1e-12)
            errors[field + "FinalMaxAbs"] = float(np.max(np.abs(np.asarray(checkpoint["routing"][field]) - expected)))
        self.assertEqual(metadata["gridIds"], checkpoint["gridIds"])
        self.assertEqual(metadata["gridGeometry"], checkpoint["gridGeometry"])
        self.assertEqual(result["context"], checkpoint["context"])
        self.assertEqual(metadata["provenance"]["inputManifestSHA256"], checkpoint["inputManifestSHA256"])
        self.assertEqual(metadata["provenance"]["forcingVersion"], checkpoint["forcingVersion"])
        self.assertEqual(result["gridHistory"]["steps"], 48)
        self.assertFalse(result["productionEligible"])
        self.assertEqual(result["resultSource"], "model-backend")
        self.assertEqual(metadata["arraySHA256"], hashlib.sha256((job.folder / "grid-history.npz").read_bytes()).hexdigest())
        self.assertEqual(result["gridHistory"]["catalogSHA256"], hashlib.sha256((job.folder / "grid-history.json").read_bytes()).hexdigest())
        # Exercise the production read path on this real artifact, not only on
        # the small artificial HTTP fixture. The manager's result was just read.
        from task_diagnostics import TaskDiagnostics
        manager = Mock(root=self.worker.RUNTIME / "tasks")
        manager.result.return_value = result
        diagnostics_service = TaskDiagnostics(manager)
        self.assertEqual(diagnostics_service.catalog(job.task_id), metadata)
        final_frame = diagnostics_service.state(job.task_id)
        self.assertEqual(final_frame["time"], checkpoint["time"])
        np.testing.assert_allclose([cell["WU"] for cell in final_frame["cells"]], checkpoint["land"]["WU"], rtol=0, atol=0)
        for file in read_json(job.folder / "input-manifest.json")["files"]:
            self.assertEqual(hashlib.sha256((MODEL / file["path"]).read_bytes()).hexdigest(), file["sha256"])
        print("GRID_HISTORY_BATCH_EQUIVALENCE " + json.dumps(errors, sort_keys=True), flush=True)

    def test_saved_checkpoint_continuation_matches_whole_history(self):
        np = self.np
        whole_job, whole, whole_checkpoint, whole_meta, whole_history = self.run_job(simulation_request(), "whole")
        first_job, first, first_checkpoint, first_meta, first_history = self.run_job(
            simulation_request(end="2017-03-20T12:00"), "first")
        parent = first["stateVersionId"]
        second_job, second, second_checkpoint, second_meta, second_history = self.run_job(
            simulation_request(start="2017-03-20T13:00", parent=parent), "second")
        self.assertEqual(len(first_history["times"]), 24)
        self.assertEqual(len(second_history["times"]), 24)
        self.assertEqual(second["parentStateVersionId"], parent)
        self.assertEqual(second_checkpoint["parentStateVersionId"], parent)
        self.assertEqual(second_meta["provenance"]["parentStateVersionId"], parent)
        self.assertEqual(second_meta["times"][0], "2017-03-20T13:00:00")
        self.assertEqual(first_meta["times"] + second_meta["times"], whole_meta["times"])
        errors = {}
        for field in whole_meta["fields"]:
            joined = np.concatenate([first_history[field], second_history[field]], axis=0)
            np.testing.assert_allclose(whole_history[field], joined, rtol=1e-12, atol=1e-12)
            errors[field + "MaxAbs"] = float(np.max(np.abs(whole_history[field] - joined)))
        whole_flow = np.array([row["value"] for row in whole["hydrograph"]["forecast"]])
        joined_flow = np.array([row["value"] for row in first["hydrograph"]["forecast"] + second["hydrograph"]["forecast"]])
        np.testing.assert_allclose(whole_flow, joined_flow, rtol=1e-12, atol=1e-12)
        errors["controlFlowMaxAbsM3s"] = float(np.max(np.abs(whole_flow - joined_flow)))
        for group in ("land", "routing"):
            for field in whole_checkpoint[group]:
                np.testing.assert_allclose(whole_checkpoint[group][field], second_checkpoint[group][field], rtol=1e-12, atol=1e-12)
                errors[group + "." + field + "FinalMaxAbs"] = float(np.max(np.abs(
                    np.asarray(whole_checkpoint[group][field]) - np.asarray(second_checkpoint[group][field]))))
        for history, checkpoint in ((whole_history, whole_checkpoint), (first_history, first_checkpoint), (second_history, second_checkpoint)):
            for field in checkpoint["land"]:
                np.testing.assert_allclose(history[field][-1], checkpoint["land"][field], rtol=0, atol=0)
        manifests = read_json(second_job.folder / "input-manifest.json")
        self.assertEqual(manifests["parentState"]["stateVersionId"], parent)
        saved_parent = self.worker.RUNTIME / "states" / f"{parent}.json"
        self.assertEqual(manifests["parentState"]["sha256"], hashlib.sha256(saved_parent.read_bytes()).hexdigest())
        print("GRID_HISTORY_CONTINUATION_EQUIVALENCE " + json.dumps(errors, sort_keys=True), flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
