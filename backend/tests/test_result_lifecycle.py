import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import now, write_json, read_json
from server import TaskManager
from test_unified_backend import simulation
from model_schemes import input_definition


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        with patch('server.ThreadPoolExecutor'):
            self.manager=TaskManager(Path(self.temp.name)/'tasks')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.manager.close)

    def result(self,identifier='JOB-test',**extra):
        status={'taskId':identifier,'createdAt':now(),'status':'completed','runType':'simulation',
                'runConfig':{'runType':'simulation'},**extra}
        write_json(self.manager.root/identifier/'status.json',status)
        write_json(self.manager.root/identifier/'result.json',{'taskId':identifier,'parameters':{'WM':100}})
        self.manager.results.ensure_name(status)
        return status

    def test_save_favorite_discard_restore_preserves_values_and_conflicts(self):
        self.result();repo=self.manager.results;life=repo.lifecycle
        file=self.manager.root/'JOB-test/result.json';before=file.read_bytes()
        self.assertEqual(repo.catalog()['results'][0]['retention'],'temporary')
        saved=life.change('JOB-test','save',{'revision':0})
        self.assertTrue(repo.catalog()['results'][0]['saved'])
        self.assertFalse(repo.catalog()['results'][0]['favorite'])
        with self.assertRaises(RuntimeError):life.change('JOB-test','discard',{'revision':0})
        life.change('JOB-test','discard',{'revision':saved['revision']})
        self.assertEqual(repo.catalog()['results'],[])
        self.assertEqual(repo.catalog(include_trashed=True)['results'][0]['retention'],'trashed')
        life.change('JOB-test','restore',{'revision':2})
        self.assertEqual(repo.catalog()['results'][0]['retention'],'saved')
        self.assertEqual(file.read_bytes(),before)

    def test_active_result_not_discarded_and_scheme_deletion_is_previewed(self):
        request=simulation();request['inputs']['bundleVersionId']='TEST'
        scheme=self.manager.schemes.save({'name':'测试','definition':input_definition(request),'calculationDraft':request})
        self.result(runConfig={**request,'scheme':{'id':scheme['id']}})
        life=self.manager.results.lifecycle
        preview=life.scheme_preview(scheme['id'])
        with self.assertRaises(RuntimeError):life.scheme_change(scheme['id'],'discard',{'previewSHA256':'wrong','temporaryIds':[]})
        life.scheme_change(scheme['id'],'discard',{'previewSHA256':preview['previewSHA256'],'temporaryIds':['JOB-test']})
        self.assertEqual(life.scheme_records()[scheme['id']]['trashed'],1)
        self.assertTrue((self.manager.root/'JOB-test/result.json').is_file())
        preview=life.scheme_preview(scheme['id'])
        life.scheme_change(scheme['id'],'restore',{'previewSHA256':preview['previewSHA256'],'temporaryIds':[]})
        self.assertEqual(self.manager.schemes.get(scheme['id'])['id'],scheme['id'])
        self.result('JOB-running',status='running')
        with self.assertRaises(RuntimeError):life.change('JOB-running','discard',{'revision':0})

    def test_old_records_are_not_prompted_and_old_favorites_are_saved(self):
        row=self.result(createdAt='2020-01-01T00:00:00Z')
        repo=self.manager.results
        with self.manager.database.connect() as db:
            db.execute('INSERT INTO result_metadata VALUES(?,?,?,?,?,?,?)',(row['taskId'],'旧收藏','','',1,1,now()))
        self.assertTrue(repo.catalog()['results'][0]['legacy'])
        repo.update(row['taskId'],{'name':'保留','collection':'','notes':'','favorite':False,'revision':1})
        self.assertEqual(repo.catalog()['results'][0]['retention'],'saved')
        self.assertFalse(repo.catalog()['results'][0]['favorite'])
        life=repo.lifecycle
        life.change(row['taskId'],'discard',{'revision':0})
        life.change(row['taskId'],'restore',{'revision':1})
        self.assertTrue(repo.catalog()['results'][0]['legacy'])
        self.assertEqual(repo.catalog()['results'][0]['retention'],'saved')

    def test_bad_retention_payloads_are_validation_errors(self):
        self.result();life=self.manager.results.lifecycle
        for body in (None, [], {'revision':True}):
            with self.assertRaises(ValueError):life.change('JOB-test','discard',body)
        request=simulation();request['inputs']['bundleVersionId']='TEST'
        scheme=self.manager.schemes.save({'name':'测试','definition':input_definition(request),'calculationDraft':request})
        preview=life.scheme_preview(scheme['id'])
        for body in (None, [], {'previewSHA256':preview['previewSHA256'],'temporaryIds':[{}]}):
            with self.assertRaises(ValueError):life.scheme_change(scheme['id'],'discard',body)
