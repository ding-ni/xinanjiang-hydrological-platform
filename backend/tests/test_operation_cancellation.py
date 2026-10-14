"""Stopping cannot race past child registration or the final result commit."""
import copy
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_json
from hydrological_operations import HydrologicalOperations, OperationCancelled


class OperationCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.identifier = 'OP-cancellation'
        self.operation = object.__new__(HydrologicalOperations)
        self.operation.root = self.root
        self.operation.lock = threading.RLock()
        self.operation.cancelled = set()
        self.rows = {}
        self.service = SimpleNamespace(get=lambda identifier: self.rows[identifier])
        self.service.cancel = lambda identifier: self.rows[identifier].update(status='cancelled')
        self.operation.manager = self.service
        self.operation.inputs = self.service
        write_json(self.root / self.identifier / 'status.json', {
            'taskId': self.identifier, 'status': 'running', 'childTaskIds': []})
        self.errors = []

    def tearDown(self):
        self.temporary.cleanup()

    def exercise(self, kind):
        entered, release, cancellation_started, cancellation_finished = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event())
        created_id = 'DATA-race' if kind == 'preparation' else 'JOB-race'
        def delayed_submit(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('test did not release the queue submit')
            self.rows[created_id] = {'taskId': created_id, 'status': 'running'}
            return copy.deepcopy(self.rows[created_id])
        self.service.submit = Mock(side_effect=delayed_submit)
        request = {'context': {'projectId': 'nananxi-xufan', 'resolutionId': '1km'},
                   'inputs': {'bundleVersionId': 'INPUT-test'}, 'period': {
                       'startTime': '2024-07-01T00:00:00', 'endTime': '2024-07-02T00:00:00'}}
        def invoke():
            try:
                if kind == 'preparation':
                    self.operation.prepare(self.identifier, request, 'reuse')
                else:
                    self.operation.child(self.identifier, request, '正在计算')
            except OperationCancelled:
                pass
            except Exception as error:
                self.errors.append(error)
        def cancel():
            cancellation_started.set()
            self.operation.cancel(self.identifier)
            cancellation_finished.set()
        def wait_for_cancel(identifier, *args, **kwargs):
            if not cancellation_finished.wait(2):
                raise RuntimeError('cancellation did not observe queued child')
            self.operation.check_cancelled(identifier)
        self.operation.wait = wait_for_cancel
        with patch('scheme_inputs.reuse_preparation', side_effect=delayed_submit):
            worker = threading.Thread(target=invoke)
            worker.start()
            self.assertTrue(entered.wait(2))
            stopper = threading.Thread(target=cancel)
            stopper.start()
            self.assertTrue(cancellation_started.wait(2))
            self.assertFalse(cancellation_finished.wait(.05))
            release.set()
            worker.join(3)
            stopper.join(3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(stopper.is_alive())
        self.assertEqual(self.errors, [])
        row = self.operation.get(self.identifier)
        self.assertEqual(row['status'], 'cancelled')
        self.assertEqual(self.rows[created_id]['status'], 'cancelled')
        if kind == 'preparation':
            self.assertEqual(row['preparationTaskId'], created_id)
        else:
            self.assertEqual(row['childTaskIds'], [created_id])
        # Another operation can submit immediately; stopping this one does not
        # poison the single operation queue or a later staff calculation.
        later = 'OP-after-cancellation'
        write_json(self.root / later / 'status.json', {'taskId': later, 'status': 'running', 'childTaskIds': []})
        self.operation.check_cancelled(later)

    def test_stop_during_model_submission_cancels_the_newly_registered_child(self):
        self.exercise('model')

    def test_stop_during_preparation_submission_cancels_new_data_work(self):
        self.exercise('preparation')

    def test_already_stopped_operation_cannot_enqueue_model_or_data_work(self):
        self.operation.cancel(self.identifier)
        self.service.submit = Mock()
        request = {'inputs': {'bundleVersionId': 'INPUT-test'}, 'context': {'projectId': 'nananxi-xufan',
            'resolutionId': '1km'}, 'period': {'startTime': '2024-07-01T00:00:00', 'endTime': '2024-07-02T00:00:00'}}
        with patch('scheme_inputs.reuse_preparation') as prepare:
            with self.assertRaises(OperationCancelled):
                self.operation.child(self.identifier, request, '计算')
            with self.assertRaises(OperationCancelled):
                self.operation.prepare(self.identifier, request, 'reuse')
            self.service.submit.assert_not_called()
            prepare.assert_not_called()

    def execution_request(self):
        write_json(self.root / self.identifier / 'request.json', {
            'operation': 'eventSimulation',
            'context': {'projectId': 'nananxi-xufan', 'resolutionId': '1km'}})
        self.operation.update(self.identifier, name='取消检查')

    def test_stop_before_result_commit_cannot_revive_the_operation(self):
        self.execution_request()
        finished, release = threading.Event(), threading.Event()
        def delayed_return(*args):
            finished.set()
            if not release.wait(3):
                raise RuntimeError('test did not release the simulation result')
            return {'primaryTaskId': 'JOB-finished'}
        self.operation.event_simulation = delayed_return
        worker = threading.Thread(target=self.operation.execute, args=(self.identifier,))
        worker.start()
        try:
            self.assertTrue(finished.wait(3))
            cancelled = self.operation.cancel(self.identifier)
            self.assertEqual(cancelled['status'], 'cancelled')
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.operation.get(self.identifier)['status'], 'cancelled')
        self.assertFalse((self.root / self.identifier / 'result.json').exists())

    def test_stop_waits_for_started_result_commit_and_returns_completed(self):
        self.execution_request()
        entered, release, cancellation_started, cancellation_finished = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event())
        self.operation.event_simulation = lambda *args: {'primaryTaskId': 'JOB-finished'}
        def delayed_result_write(path, value):
            if Path(path).name == 'result.json':
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test did not release the final result commit')
            write_json(path, value)
        cancelled = []
        def cancel():
            cancellation_started.set()
            cancelled.append(self.operation.cancel(self.identifier))
            cancellation_finished.set()
        with patch('hydrological_operations.write_json', side_effect=delayed_result_write):
            worker = threading.Thread(target=self.operation.execute, args=(self.identifier,))
            stopper = threading.Thread(target=cancel)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                stopper.start()
                self.assertTrue(cancellation_started.wait(3))
                self.assertFalse(cancellation_finished.wait(.05))
            finally:
                release.set()
                worker.join(3)
                if stopper.ident is not None:
                    stopper.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertTrue(cancellation_finished.is_set())
        self.assertEqual(cancelled[0]['status'], 'completed')
        self.assertEqual(self.operation.get(self.identifier)['status'], 'completed')
        self.assertTrue((self.root / self.identifier / 'result.json').exists())
        self.assertNotIn(self.identifier, self.operation.cancelled)


if __name__ == '__main__':
    unittest.main()
