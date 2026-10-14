import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import read_json, write_json
from platform_config import load_settings
from workspace_service import WorkspaceLock, DraftRepository, workspace_identity
from workspace_backup import create_backup, verify_backup, restore_backup
from hydro_database import HydroDatabase
from server import main, handler
import test_input_products_http as http_fixture


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_config_is_relative_to_installation_not_shell(self):
        root = self.root / 'program'
        write_json(root / 'config/platform.json', {'schemaVersion': 1, 'workspace': '../工作资料', 'port': 8880})
        settings = load_settings(root, {})
        self.assertEqual(settings['workspace'], self.root / '工作资料')
        self.assertEqual(load_settings(root, {'XAJ_RUNTIME_DIR': 'other'})['workspace'], root / 'other')
        for value in ('', '.', 'backend/vendor/model/tasks', 'frontend-demo/app/public'):
            write_json(root / 'config/platform.json', {'workspace': value})
            with self.subTest(value=value), self.assertRaises(ValueError): load_settings(root, {})

    def test_unknown_config_and_missing_explicit_config(self):
        write_json(self.root / 'config/platform.json', {'mystery': 1})
        with self.assertRaises(ValueError): load_settings(self.root, {})
        with self.assertRaises(ValueError): load_settings(self.root, {'XAJ_CONFIG': 'missing.json'})

    def test_workspace_lock_rejects_other_port_before_task_recovery(self):
        with WorkspaceLock(self.root):
            with patch('server.RUNTIME', self.root), patch('sys.argv', ['server.py', '--port', '0']), patch('server.TaskManager') as manager:
                with self.assertRaises(RuntimeError): main()
                manager.assert_not_called()
        with WorkspaceLock(self.root): pass

    def test_workspace_lock_is_cross_process(self):
        code = 'import sys; from pathlib import Path; from workspace_service import WorkspaceLock; lock=WorkspaceLock(Path(sys.argv[1])); lock.__enter__()'
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        with WorkspaceLock(self.root):
            other = subprocess.run([sys.executable, '-B', '-c', code, str(self.root)], capture_output=True, env=env)
            self.assertNotEqual(other.returncode, 0)
            self.assertIn('RuntimeError', other.stderr.decode('utf-8', errors='replace'))
        other = subprocess.run([sys.executable, '-B', '-c', code, str(self.root)], capture_output=True, env=env)
        self.assertEqual(other.returncode, 0)

    def test_schema_migration_preserves_existing_records_and_is_repeatable(self):
        database = HydroDatabase(self.root)
        with database.connect() as db:
            db.execute("INSERT INTO schemes VALUES ('S','Name','JOB-a','{}','hash','time')")
            db.execute('PRAGMA user_version=1')
        HydroDatabase(self.root); HydroDatabase(self.root)
        with database.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM schemes').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 1)

    def test_drafts_persist_and_concurrent_revision_cannot_overwrite(self):
        repo = DraftRepository(HydroDatabase(self.root))
        body = {'key': 'hydro_flood_event_overrides_v1:nananxi-xufan', 'value': {'notes': '洪水'}, 'revision': 0}
        row = repo.save(body)
        self.assertEqual(DraftRepository(HydroDatabase(self.root)).list()[body['key']], row)
        with self.assertRaises(RuntimeError): repo.save({**body, 'value': {'notes': 'other'}})
        for change in ({'key': '../escape'}, {'value': []}, {'revision': True}):
            with self.assertRaises(ValueError): repo.save({**body, **change})

    def test_lost_acknowledgement_retries_without_revision_conflict(self):
        repo = DraftRepository(HydroDatabase(self.root))
        body = {'key': 'hydro_calibration_workspaces_v1', 'value': {'a': 1, 'b': '洪水'}, 'revision': 0}
        saved = repo.save(body)
        self.assertEqual(repo.save({**body, 'value': {'b': '洪水', 'a': 1}}), saved)
        self.assertEqual(repo.list()[body['key']]['revision'], 1)
        with self.assertRaises(RuntimeError): repo.save({**body, 'value': {'a': True, 'b': '洪水'}})
        with self.assertRaises(RuntimeError): repo.save({**body, 'revision': 2})
        updated = repo.save({**body, 'revision': 1, 'value': {'a': 2}})
        self.assertEqual(updated['revision'], 2)
        with self.assertRaises(RuntimeError): repo.save(body)

    def test_disconnect_does_not_write_a_second_response(self):
        with patch('server.InputTaskManager'), patch('server.InputService'):
            from unittest.mock import MagicMock
            request_type = handler(MagicMock(), self.root)
            request = request_type.__new__(request_type)
            for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                with patch.object(request, 'route', side_effect=error), patch.object(request, 'send_json') as send:
                    request.dispatch()
                    send.assert_not_called()

    def test_backup_restore_database_drafts_and_numerical_files(self):
        root = self.root / 'work'
        database = HydroDatabase(root)
        from model_schemes import ModelSchemes, input_definition
        from test_unified_backend import simulation
        schemes = ModelSchemes(database)
        request = simulation()
        from input_products import DEFAULT_BUNDLE_VERSION_ID
        request['inputs']['bundleVersionId'] = DEFAULT_BUNDLE_VERSION_ID
        scheme = schemes.save({'name': '备份方案', 'definition': input_definition(request), 'calculationDraft': request})
        schemes.defaults({'idwPower': 3, 'minimumStations': 2})
        DraftRepository(database).save({'key': 'hydro_calibration_workspaces_v1', 'value': {'workspaces': {'a': {'note': '原始备注'}}}, 'revision': 0})
        write_json(root / 'tasks/JOB-a/status.json', {'status': 'completed'})
        write_json(root / 'states/STATE-JOB-a.json', {'land': {'WU': [1.23456789]}})
        backup = create_backup(root)
        checked = verify_backup(backup['path'])
        restored = self.root / '另一电脑资料'
        restore_backup(backup['path'], restored)
        self.assertEqual(workspace_identity(root), workspace_identity(restored))
        self.assertEqual(DraftRepository(HydroDatabase(restored)).list(), DraftRepository(database).list())
        restored_schemes = ModelSchemes(HydroDatabase(restored))
        self.assertEqual(restored_schemes.get(scheme['id']), scheme)
        self.assertEqual(restored_schemes.defaults(), schemes.defaults())
        self.assertEqual(read_json(restored / 'states/STATE-JOB-a.json'), {'land': {'WU': [1.23456789]}})
        self.assertGreater(len(checked['files']), 2)
        with self.assertRaises(ValueError): restore_backup(backup['path'], restored)
        write_json(Path(backup['path']) / 'states/STATE-JOB-a.json', {})
        with self.assertRaises(ValueError): verify_backup(backup['path'])

    def test_active_task_and_traversal_are_rejected(self):
        write_json(self.root / 'tasks/JOB-a/status.json', {'status': 'running'})
        with self.assertRaises(RuntimeError): create_backup(self.root)
        write_json(self.root / 'tasks/JOB-a/status.json', {'status': 'completed'})
        backup = create_backup(self.root)
        path = Path(backup['path']) / 'backup-manifest.json'
        manifest = read_json(path)
        manifest['files'][0]['path'] = '../outside'
        write_json(path, manifest)
        with self.assertRaises(ValueError): restore_backup(backup['path'], self.root / 'restore')


class WorkspaceHttpTests(unittest.TestCase):
    setUp = http_fixture.InputProductsHttpTests.setUp
    tearDown = http_fixture.InputProductsHttpTests.tearDown
    request = http_fixture.InputProductsHttpTests.request

    def test_workspace_directories_drafts_backup_and_origin(self):
        code, info, _ = self.request('workspace')
        self.assertEqual(code, 200)
        self.assertEqual(info['directories'][0]['path'], str(self.fixture.runtime))
        self.assertEqual(info['schemaVersion'], 2)
        body = {'key': 'hydro_calibration_workspaces_v1', 'value': {'workspaces': {}}, 'revision': 0}
        self.assertEqual(self.request('workspace/drafts', 'POST', body)[0], 200)
        self.assertEqual(self.request('workspace/drafts', 'POST', body)[0], 200)
        self.assertEqual(self.request('workspace/drafts', 'POST', {**body, 'value': {'workspaces': {'different': {}}}})[0], 409)
        self.assertEqual(self.request('workspace/drafts', 'POST', body, {'Origin': 'http://foreign.invalid'})[0], 400)
        self.assertEqual(self.request('workspace/drafts')[1]['drafts'][body['key']]['revision'], 1)
        code, backup, _ = self.request('workspace/backups', 'POST', {})
        self.assertEqual(code, 200, backup)
        self.assertEqual(self.request('workspace/backups')[1]['backups'][0]['name'], backup['name'])
        self.assertEqual(self.request('workspace/backups', 'POST', {'path': 'outside'})[0], 400)

    def test_backup_holds_operation_parameter_and_queue_locks_in_order(self):
        entries = []
        class TracedLock:
            def __init__(self, label, lock):
                self.label, self.lock = label, lock
            def __enter__(self):
                self.lock.acquire()
                entries.append(self.label)
                return self
            def __exit__(self, *args):
                self.lock.release()

        operations = self.manager.operations
        named = self.manager.parameter_schemes
        snapshots = [(operations, 'lock', 'operations'), (named, 'lock', 'parameters'),
                     (self.manager, 'lock', 'model'), (self.input_manager, 'lock', 'inputs'),
                     (self.products, '_lock', 'products'), (self.manager.forecast_products, 'lock', 'forecast')]
        saved = []
        def assert_locked(root):
            self.assertEqual(entries[:6], [label for _, _, label in snapshots])
            acquired = []
            def other_thread():
                for owner, attribute, label in snapshots:
                    lock = getattr(owner, attribute).lock
                    success = lock.acquire(blocking=False)
                    acquired.append((label, success))
                    if success:
                        lock.release()
            peer = threading.Thread(target=other_thread)
            peer.start()
            peer.join(timeout=2)
            self.assertFalse(peer.is_alive())
            self.assertEqual(acquired, [(label, False) for _, _, label in snapshots])
            return {'name': 'BACKUP-test', 'files': 0}

        try:
            for owner, attribute, label in snapshots:
                original = getattr(owner, attribute)
                saved.append((owner, attribute, original))
                setattr(owner, attribute, TracedLock(label, original))
            with patch('server.create_backup', side_effect=assert_locked):
                self.assertEqual(self.request('workspace/backups', 'POST', {})[0], 200)
        finally:
            for owner, attribute, original in saved:
                setattr(owner, attribute, original)


if __name__ == '__main__': unittest.main(verbosity=2)
