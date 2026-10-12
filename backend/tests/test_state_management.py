"""State selection, SQLite metadata and observed-flow initialization contracts."""
import copy
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import case_catalog
from common import read_json, write_json
from contracts import normalize_request
from hydro_database import HydroDatabase
from state_repository import StateRepository
from state_initialization_adapter import freeze_initialization, resolve_anchor, anchored_routing
from test_unified_backend import simulation


class InitializationContracts(unittest.TestCase):
    def test_strategy_is_typed_and_only_accepted_for_initialization(self):
        request = simulation()
        for value in (None, [], 'FIXED_WARMUP', {}, {'strategy': 'unknown'}, {'strategy': 'FIXED_WARMUP', 'path': 'x'}):
            request.update(runType='stateInitialization', initialization=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_request(request, case_catalog())
        request.update(runType='simulation', initialization={'strategy': 'FIXED_WARMUP'})
        with self.assertRaises(ValueError):
            normalize_request(request, case_catalog())

    def test_initialization_requires_one_state_time(self):
        request = simulation()
        request.update(runType='stateInitialization', initialization={'strategy': 'OBSERVED_FLOW_ANCHORED'})
        with self.assertRaises(ValueError):
            normalize_request(request, case_catalog())
        request['period']['endTime'] = request['period']['startTime']
        self.assertEqual(normalize_request(request, case_catalog())['initialization']['strategy'], 'OBSERVED_FLOW_ANCHORED')

    def test_anchor_exact_hour_zero_missing_duplicates_and_source_hash(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'observed.csv'
            bundle = {'paths': {'flow': path}, 'observationVersion': 'OBS-test'}
            request = {'period': {'endTime': '2017-06-20T11:00'}, 'initialization': {'strategy': 'OBSERVED_FLOW_ANCHORED'}}
            for values in ([0], [12.75]):
                pd.DataFrame({'time': ['2017-06-20T11:00'], 'flow_m3_s': values}).to_csv(path, index=False)
                frozen = freeze_initialization(copy.deepcopy(request), bundle)
                self.assertEqual(frozen['initialization']['anchor']['flowCms'], values[0])
                self.assertEqual(frozen['initialization']['anchor']['observationSHA256'], hashlib.sha256(path.read_bytes()).hexdigest())
                frozen['initialization']['anchor']['flowCms'] = 1000
                with self.assertRaises(ValueError):
                    freeze_initialization(frozen, bundle)
            for times, values in [(['2017-06-20T10:00'], [10]), (['2017-06-20T11:00'] * 2, [1, 2]), (['2017-06-20T11:00'], [None]), (['2017-06-20T11:00'], [-1])]:
                pd.DataFrame({'time': times, 'flow_m3_s': values}).to_csv(path, index=False)
                with self.subTest(values=values), self.assertRaises(ValueError):
                    resolve_anchor(request, bundle)


class StateMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = SimpleNamespace(runtime_root=self.root, root=self.root / 'tasks', database=HydroDatabase(self.root))
        self.repository = StateRepository(self.manager)
        self.id = 'STATE-JOB-metadata'
        checkpoint = {'stateVersionId': self.id, 'sourceTaskId': 'JOB-metadata', 'context': {'projectId': 'nananxi-xufan'},
                      'time': '2017-06-20T11:00:00', 'inputManifestSHA256': 'digest', 'land': {'WU': [1, 2]}}
        write_json(self.root / 'states' / f'{self.id}.json', checkpoint)
        write_json(self.manager.root / 'JOB-metadata/checkpoint.json', checkpoint)
        write_json(self.manager.root / 'JOB-metadata/status.json', {'taskId': 'JOB-metadata', 'status': 'completed'})
        write_json(self.manager.root / 'JOB-metadata/result.json', {'taskId': 'JOB-metadata', 'stateVersionId': self.id,
                   'resultSource': 'model-backend', 'context': checkpoint['context'], 'stateTime': checkpoint['time'], 'lineage': {'inputManifestSHA256': 'digest'}})

    def tearDown(self):
        self.temp.cleanup()

    def test_metadata_persists_without_modifying_checkpoint(self):
        before = self.repository.checkpoint(self.id)
        row = self.repository.update(self.id, {'name': '  洪水前状态 ', 'favorite': True, 'revision': 0})
        self.assertEqual(row['name'], '洪水前状态')
        self.assertEqual(StateRepository(self.manager).metadata()[self.id], row)
        self.assertEqual(self.repository.checkpoint(self.id), before)
        row = self.repository.update(self.id, {'name': '', 'favorite': False, 'revision': 1})
        self.assertEqual(row['revision'], 2)

    def test_conflicting_revisions_do_not_overwrite_metadata(self):
        self.repository.update(self.id, {'name': 'A', 'favorite': True, 'revision': 0})
        with self.assertRaises(RuntimeError):
            self.repository.update(self.id, {'name': 'B', 'favorite': False, 'revision': 0})
        self.assertEqual(self.repository.metadata()[self.id]['name'], 'A')

    def test_invalid_fields_paths_and_unsuccessful_states(self):
        for body in ({}, {'name': 'a' * 81, 'favorite': False, 'revision': 0}, {'name': 'a', 'favorite': 1, 'revision': 0}, {'name': 'a', 'favorite': True, 'revision': True}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.repository.update(self.id, body)
        for state in ('../outside', None, 'STATE-JOB-..\\outside'):
            with self.assertRaises(ValueError):
                self.repository.checkpoint(state)
        write_json(self.manager.root / 'JOB-metadata/status.json', {'taskId': 'JOB-metadata', 'status': 'failed'})
        with self.assertRaises(ValueError):
            self.repository.update(self.id, {'name': 'a', 'favorite': False, 'revision': 0})
        self.assertEqual(self.repository.metadata(), {})


@unittest.skipUnless(os.environ.get('XAJ_TEST_REAL_MODEL') == '1', 'Real initialization numerical regression')
class AnchorNumericalTests(unittest.TestCase):
    def test_anchor_matches_delivered_initializer_and_continuation(self):
        import numpy as np
        import pandas as pd
        import worker
        from state_initialization.routing import build_observed_flow_anchored_state
        with tempfile.TemporaryDirectory() as folder, patch('worker.RUNTIME', Path(folder)):
            def run(request, suffix):
                task_id = 'JOB-anchor-' + suffix
                task_folder = Path(folder) / 'tasks' / task_id
                write_json(task_folder / 'request.json', normalize_request(request, case_catalog()))
                write_json(task_folder / 'status.json', {'taskId': task_id, 'status': 'running'})
                job = worker.ModelJob(task_folder)
                job.run()
                write_json(task_folder / 'status.json', {'taskId': task_id, 'status': 'completed'})
                return job, read_json(task_folder / 'checkpoint.json'), read_json(task_folder / 'result.json')
            request = simulation('2017-06-20T11:00', '2017-06-20T11:00')
            request.update(runType='stateInitialization', initialization={'strategy': 'FIXED_WARMUP'})
            fixed_job, fixed, _ = run(request, 'fixed')
            request['initialization']['strategy'] = 'OBSERVED_FLOW_ANCHORED'
            job, checkpoint, result = run(request, 'observed')
            self.assertEqual(checkpoint['land'], fixed['land'])
            anchor = checkpoint['initialization']['anchor']
            self.assertEqual(anchor['flowCms'], float(job.observed.loc[pd.Timestamp(anchor['time'])]))
            expected = job.router.standard_state_to_fast(build_observed_flow_anchored_state(job.router.topology, job.router.control_point, anchor['flowCms']))
            for field in expected:
                np.testing.assert_array_equal(checkpoint['routing'][field], expected[field])
            self.assertAlmostEqual(checkpoint['routing']['prev_out'][job.router.control_reach], anchor['flowCms'])
            self.assertEqual(checkpoint['initialization'], result['initialization'])
            continuation = simulation('2017-06-20T12:00', '2017-06-20T14:00')
            continuation['runType'] = 'stateContinuation'
            continuation['period']['initialStateVersionId'] = checkpoint['stateVersionId']
            next_job, final, answer = run(continuation, 'next')
            start, end = pd.Timestamp('2017-06-20T12:00'), pd.Timestamp('2017-06-20T14:00')
            event = next_job.load([{'event_id': 'expected', 'year': 2017, 'start': start, 'end': end, 'group': 'SIMULATION'}], 0)[0]
            event.pet_mm_h = worker.PetProvider.from_file(worker.MODEL / 'config/pet_forcing_assumed_monthly.json').values(event.times)
            flow, land, routing = worker.kernels.route_event_fast(event, next_job.params, next_job.routing, next_job.router, next_job.mapping,
                {key: np.array(value) for key, value in checkpoint['land'].items()}, expected)
            np.testing.assert_allclose([row['value'] for row in answer['hydrograph']['forecast']], flow.values, rtol=1e-12)
            for field in land:
                np.testing.assert_allclose(final['land'][field], land[field], rtol=1e-12)
            for field in routing:
                np.testing.assert_allclose(final['routing'][field], routing[field], rtol=1e-12)


if __name__ == '__main__':
    unittest.main(verbosity=2)
