"""Deletion regressions use only disposable fixture workspaces."""
import shutil
import sys
import unittest
import threading
import urllib.request
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from artifact_cleanup import ArtifactCleanup
from common import now, read_json, write_json
from model_schemes import input_definition
from test_result_lifecycle import RetentionTests
from test_unified_backend import simulation


class CleanupTests(unittest.TestCase):
    setUp = RetentionTests.setUp
    result = RetentionTests.result

    def checkpoint(self, job='JOB-child', **extra):
        path = self.manager.runtime_root / 'states' / ('STATE-' + job + '.json')
        write_json(path, {'stateVersionId': 'STATE-' + job, 'sourceTaskId': job, **extra})
        return path

    def group(self, orphan=False):
        self.result('JOB-child', operationId='OP-parent', internal=True,
                    stateRole='calibrationFinal', runConfig={'runType': 'simulation'})
        self.checkpoint()
        if not orphan:
            write_json(self.manager.runtime_root / 'operations/OP-parent/status.json', {
                'taskId': 'OP-parent', 'status': 'completed', 'createdAt': now(),
                'operation': 'calibration', 'childTaskIds': ['JOB-child'], 'runConfig': {}})
        return self.manager.results.lifecycle

    def recycle(self, path):
        target = self.manager.runtime_root / 'test-bin' / path.name
        target.parent.mkdir(exist_ok=True)
        shutil.move(str(path), str(target))

    def test_generated_state_does_not_keep_its_own_task_alive(self):
        life = self.group()
        plan = ArtifactCleanup(self.manager).plan(['OP-parent'])
        self.assertEqual(plan['protected'], [])
        self.assertEqual({r['id'] for r in plan['artifacts']}, {'OP-parent', 'JOB-child', 'STATE-JOB-child'})
        with patch.object(life, '_move_to_recycle_bin', side_effect=self.recycle):
            self.assertEqual(life._purge_artifacts(['OP-parent']), ['JOB-child', 'OP-parent'])
        self.assertFalse((self.manager.root / 'JOB-child').exists())
        self.assertFalse((self.manager.runtime_root / 'states/STATE-JOB-child.json').exists())

    def test_orphans_are_previewed_and_cleaned_without_touching_gis_or_inputs(self):
        life = self.group(orphan=True)
        self.result('JOB-map', runType='spatialExport', runConfig={'runType': 'spatialExport'})
        write_json(self.manager.runtime_root / 'inputs/products/INPUT-test/manifest.json', {'rain': 'keep'})
        plan = life.orphan_cleanup()
        self.assertEqual((plan['taskCount'], plan['stateCount']), (1, 1))
        self.assertTrue((self.manager.root / 'JOB-child').exists())
        with patch.object(life, '_move_to_recycle_bin', side_effect=self.recycle):
            life.orphan_cleanup({'previewSHA256': plan['previewSHA256']})
        self.assertTrue((self.manager.root / 'JOB-map').exists())
        self.assertTrue((self.manager.runtime_root / 'inputs/products/INPUT-test/manifest.json').exists())
        self.assertEqual(life.orphan_cleanup()['artifacts'], [])

    def test_downstream_state_consumer_protects_source_task_and_state(self):
        life = self.group(orphan=True)
        self.result('JOB-consumer', runConfig={'runType': 'simulation', 'period': {'initialStateVersionId': 'STATE-JOB-child'}})
        plan = life.orphan_cleanup()
        self.assertFalse(plan['artifacts'])
        self.assertEqual({r['id'] for r in plan['protected']}, {'JOB-child', 'STATE-JOB-child'})

    def test_transitive_protection_is_closed_over_candidate_siblings(self):
        life = self.group(orphan=True)
        self.result('JOB-second', operationId='OP-parent', runConfig={'runType': 'simulation', 'lineage': {'sourceTaskId': 'JOB-child'}})
        self.checkpoint('JOB-second')
        self.result('JOB-external', runConfig={'runType': 'simulation', 'period': {'initialStateVersionId': 'STATE-JOB-second'}})
        plan = life.orphan_cleanup()
        self.assertFalse(plan['artifacts'])
        self.assertIn('JOB-child', {r['id'] for r in plan['protected']})

    def test_named_parameter_and_named_state_protection(self):
        for kind in ('parameter', 'state'):
            with self.subTest(kind=kind):
                life = self.group(orphan=True)
                if kind == 'parameter':
                    write_json(self.manager.runtime_root / 'parameter-schemes/PARAMSET-saved.json', {'sourceTaskId': 'JOB-child'})
                else:
                    with self.manager.database.connect() as db:
                        db.execute('INSERT INTO state_metadata VALUES(?,?,?,?,?)', ('STATE-JOB-child', '需要保留', 1, 1, now()))
                plan = life.orphan_cleanup()
                self.assertIn('JOB-child', {r['id'] for r in plan['protected']})

    def test_sql_scheme_references_protect_generated_state(self):
        life = self.group(orphan=True)
        request = simulation(); request['inputs']['bundleVersionId'] = 'TEST'
        request['period']['initialStateVersionId'] = 'STATE-JOB-child'
        self.manager.schemes.save({'name': '续算方案', 'definition': input_definition(request), 'calculationDraft': request})
        self.assertFalse(life.orphan_cleanup()['artifacts'])

    def test_root_job_with_external_reference_is_not_blindly_deleted(self):
        life = self.group(orphan=True)
        self.result('JOB-external', runConfig={'runType': 'simulation', 'sourceTaskId': 'JOB-child'})
        with patch.object(life, '_move_to_recycle_bin') as recycle:
            with self.assertRaisesRegex(RuntimeError, '文件仍被使用'):
                life._purge_artifacts(['JOB-child'])
            recycle.assert_not_called()

    def test_preview_is_invalid_after_a_parameter_is_saved(self):
        life = self.group(orphan=True)
        preview = life.orphan_cleanup()
        write_json(self.manager.runtime_root / 'parameter-schemes/PARAMSET-saved.json', {'sourceTaskId': 'JOB-child'})
        with patch.object(life, '_move_to_recycle_bin') as recycle:
            with self.assertRaisesRegex(RuntimeError, '预览后已变化'):
                life.orphan_cleanup({'previewSHA256': preview['previewSHA256']})
            recycle.assert_not_called()

    def test_recycle_noop_does_not_delete_task_index_and_can_retry(self):
        life = self.group(orphan=True)
        plan = life.orphan_cleanup()
        with patch.object(life, '_move_to_recycle_bin'):
            with self.assertRaisesRegex(RuntimeError, '未移走'):
                life.orphan_cleanup({'previewSHA256': plan['previewSHA256']})
        self.assertTrue((self.manager.root / 'JOB-child/status.json').exists())
        self.assertTrue((self.manager.runtime_root / 'states/STATE-JOB-child.json').exists())
        with patch.object(life, '_move_to_recycle_bin', side_effect=self.recycle):
            life.orphan_cleanup({'previewSHA256': life.orphan_cleanup()['previewSHA256']})

    def test_partial_failure_keeps_parent_and_records_moved_paths(self):
        life = self.group()
        def failing(path):
            if path.is_dir():
                raise OSError('file in use')
            self.recycle(path)
        with patch.object(life, '_move_to_recycle_bin', side_effect=failing):
            with self.assertRaisesRegex(RuntimeError, '清理未全部完成'):
                life._purge_artifacts(['OP-parent'])
        self.assertTrue((self.manager.runtime_root / 'operations/OP-parent/status.json').exists())
        self.assertTrue((self.manager.root / 'JOB-child').exists())
        journal = next((self.manager.runtime_root / 'cleanup-journal').glob('*.json'))
        self.assertEqual(read_json(journal)['status'], 'partial')
        with patch.object(life, '_move_to_recycle_bin', side_effect=self.recycle):
            life._purge_artifacts(['OP-parent'])

    def test_active_orphans_are_never_candidates_for_execution(self):
        life = self.group(orphan=True)
        self.result('JOB-child', operationId='OP-parent', status='running')
        self.assertIn('JOB-child', {r['id'] for r in life.orphan_cleanup()['protected']})

    def test_corrupt_json_and_unsafe_identifiers_fail_closed(self):
        life = self.group(orphan=True)
        cleaner = ArtifactCleanup(self.manager)
        for identifier in ('../tasks', 'JOB-../outside', 'JOB-..\\outside', 'X:/'):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                cleaner.plan([identifier])
        with patch('artifact_cleanup.read_json', side_effect=ValueError('broken')):
            with self.assertRaisesRegex(RuntimeError, '无法核对引用'):
                life.orphan_cleanup()

    def test_batch_purge_handles_shared_sources_without_mutual_keepalive(self):
        self.result('JOB-one')
        self.result('JOB-two', runConfig={'runType': 'simulation', 'sourceTaskId': 'JOB-one'})
        life = self.manager.results.lifecycle
        for key in ('JOB-one', 'JOB-two'):
            life.change(key, 'discard', {'revision': 0})
        self.assertTrue(life.result_cleanup_preview('JOB-one')['protected'])
        ids = ['JOB-one', 'JOB-two']
        preview = life.batch_cleanup(ids)
        with patch.object(life, '_move_to_recycle_bin', side_effect=self.recycle):
            result = life.batch_cleanup(ids, {'ids': ids, 'previewSHA256': preview['previewSHA256']})
        self.assertEqual(result['purgedIds'], ids)

    def test_scheme_with_leftover_temporary_result_cannot_disappear(self):
        request = simulation(); request['inputs']['bundleVersionId'] = 'TEST'
        scheme = self.manager.schemes.save({'name': '暂留', 'definition': input_definition(request), 'calculationDraft': request})
        self.result('JOB-remaining', runConfig={**request, 'scheme': {'id': scheme['id']}})
        life = self.manager.results.lifecycle
        preview = life.scheme_preview(scheme['id'])
        life.scheme_change(scheme['id'], 'discard', {'previewSHA256': preview['previewSHA256'], 'temporaryIds': []})
        with self.assertRaisesRegex(RuntimeError, '未丢弃'):
            life.purge_scheme(scheme['id'], {'previewSHA256': life.scheme_preview(scheme['id'])['previewSHA256']})
        self.assertEqual(self.manager.schemes.get(scheme['id'])['id'], scheme['id'])

    def test_named_state_protects_without_any_parameter_file(self):
        life = self.group(orphan=True)
        with self.manager.database.connect() as db:
            db.execute('INSERT INTO state_metadata VALUES(?,?,?,?,?)', ('STATE-JOB-child', '保留状态', 1, 1, now()))
        self.assertFalse(life.orphan_cleanup()['artifacts'])

    def test_http_preview_route_and_empty_post_validation(self):
        from server import PlatformHTTPServer, handler
        life = self.group(orphan=True)
        self.result('JOB-standalone')
        life.change('JOB-standalone', 'discard', {'revision': 0})
        server = PlatformHTTPServer(('127.0.0.1', 0), handler(self.manager, Path(self.temp.name)))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port) + '/api/model/'
        with urllib.request.urlopen(base + 'result-retention/JOB-standalone/deletion-preview') as response:
            preview = json.load(response)
        self.assertEqual(preview['taskCount'], 1)
        with urllib.request.urlopen(base + 'workspace/artifact-cleanup') as response:
            self.assertEqual(json.load(response)['stateCount'], 1)
        with urllib.request.urlopen(base + 'result-cleanup?ids=JOB-standalone') as response:
            batch = json.load(response)
        request = urllib.request.Request(base + 'result-cleanup', data=json.dumps({'ids': ['JOB-standalone'], 'previewSHA256': batch['previewSHA256']}).encode(), headers={'Content-Type': 'application/json'})
        with patch.object(life, '_move_to_recycle_bin', side_effect=self.recycle):
            with urllib.request.urlopen(request) as response:
                self.assertIn('JOB-standalone', json.load(response)['purgedIds'])


if __name__ == '__main__':
    unittest.main()
