"""Business metadata never changes the saved numerical experiment."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import write_json,read_json
from server import TaskManager
from result_repository import ResultRepository
from model_schemes import input_definition
from test_unified_backend import calibration


class CalculationRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        with patch('server.ThreadPoolExecutor'):
            self.manager=TaskManager(self.root/'tasks')
    def tearDown(self):
        self.manager.close();self.temp.cleanup()
    def fixture(self):
        request=calibration()
        request['scheme']={'id':'SCHEME-'+'1'*32,'name':'我的测试-16场洪水','revision':1}
        request['period']['events'][0]['start']='1988-08-08T00:00:00'
        status={'taskId':'JOB-record','runType':'eventCalibration','runConfig':request,'status':'completed',
            'createdAt':'2026-10-16T06:00:01Z','completedAt':'2026-10-16T06:02:00Z'}
        folder=self.root/'tasks/JOB-record'
        write_json(folder/'status.json',status);write_json(folder/'request.json',request)
        write_json(folder/'result.json',{'taskId':'JOB-record','bestParameters':request['calibration']['initialParameters'],
            'hydrograph':{'forecast':[{'time':'1988-08-08T00:00:00','value':1}]}})
        return status,folder
    def test_name_uses_scheme_and_calculation_time_not_first_flood(self):
        status,folder=self.fixture();before=(folder/'result.json').read_bytes()
        self.manager.results.ensure_name(status)
        row=self.manager.results.catalog()['results'][0]
        self.assertTrue(row['name'].startswith('我的测试-16场洪水'))
        self.assertIn('2026-10-16 14:00:01',row['name']);self.assertNotIn('1988',row['name'])
        self.assertFalse(row['favorite']);self.assertFalse(row['saved'])
        self.manager.results.update(status['taskId'],{'name':'人工确认','collection':'','notes':'','favorite':True,'revision':0})
        self.manager.results.ensure_name(status)
        self.assertEqual(self.manager.results.annotation(status['taskId'])['name'],'人工确认')
        self.assertEqual((folder/'result.json').read_bytes(),before)
    def test_legacy_named_results_are_favorited_once_and_can_be_unfavorited(self):
        status,_=self.fixture()
        self.manager.results.update(status['taskId'],{'name':'原保存名称','collection':'','notes':'原备注','favorite':False,'revision':0})
        with self.manager.database.connect() as db:db.execute('DELETE FROM schema_migrations WHERE version=3')
        repository=ResultRepository(self.manager)
        old=repository.annotation(status['taskId']);self.assertTrue(old['favorite']);self.assertEqual(old['notes'],'原备注')
        repository.update(status['taskId'],{key:value for key,value in {**old,'favorite':False}.items() if key in {'name','collection','notes','favorite','revision'}})
        repository=ResultRepository(self.manager)
        self.assertFalse(repository.annotation(status['taskId'])['favorite'])
    def test_repeated_search_keeps_scheme_name_type_and_process_count(self):
        status, _ = self.fixture()
        model = copy.deepcopy(status['runConfig'])
        model['lineage'] = {'schemeId': model['scheme']['id'], 'schemeName': model['scheme']['name']}
        model.pop('scheme')
        status.update(taskId='OP-repeat', operation='rerun', runConfig={'frozenRequest': model})
        write_json(self.root/'operations/OP-repeat/status.json', status)
        row = next(row for row in self.manager.results.catalog()['results'] if row['id']=='OP-repeat')
        self.assertTrue(row['name'].startswith('我的测试-16场洪水 · 自动率定'))
        self.assertEqual(row['type'], 'calibration')
        self.assertEqual(row['schemeId'], model['lineage']['schemeId'])
        self.assertEqual(row['processCount'], len(model['period']['events']))
    def test_history_name_and_old_note_are_independent_of_saved_conditions(self):
        request=calibration()
        request['inputs']['bundleVersionId']='frozen-input'
        saved=self.manager.schemes.save({'name':'洪水方案','definition':input_definition(request),'calculationDraft':request})
        before=copy.deepcopy(saved['calculationDraft']);digest=saved['calculationSHA256']
        self.manager.schemes.history_note(saved['id'],{'revision':1,'note':'原修改说明'})
        renamed=self.manager.schemes.history_note(saved['id'],{'revision':1,'name':'缩小SM范围'})
        self.assertEqual(renamed['revision'],1);self.assertEqual(renamed['calculationDraft'],before)
        self.assertEqual(renamed['calculationSHA256'],digest)
        self.assertEqual(renamed['revisions'][0]['name'],'缩小SM范围')
        self.assertEqual(renamed['revisions'][0]['note'],'原修改说明')
        self.manager.schemes.history_note(saved['id'],{'revision':1,'note':'补充说明'})
        self.assertEqual(self.manager.schemes.get(saved['id'])['revisions'][0]['name'],'缩小SM范围')
    def test_state_supplement_is_not_a_separate_business_result(self):
        self.fixture()
        write_json(self.root/'operations/OP-state/status.json',{'taskId':'OP-state','operation':'stateReplay','status':'completed','createdAt':'2026-10-16','childTaskIds':[]})
        ids=[row['id'] for row in self.manager.results.catalog()['results']]
        self.assertNotIn('OP-state',ids)


if __name__=='__main__':unittest.main()
