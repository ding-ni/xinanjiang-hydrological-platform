"""Business results, immutable named sections and explicit scientific conditions."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import write_json, read_json
from server import TaskManager
from science_context import ScienceVersions
from routing_versions import RoutingVersions
from model_schemes import input_definition
from test_unified_backend import simulation


class HydrologicalWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.manager=TaskManager(self.root/'tasks')
    def tearDown(self):
        self.manager.close();self.temp.cleanup()
    def result_fixture(self):
        request=simulation('2017-06-20T00:00','2017-06-20T02:00')
        request['inputs']['bundleVersionId']='frozen-input'
        graph={'forecast':[{'time':f'2017-06-20T0{i}:00:00','value':i+1.25} for i in range(3)]}
        for task_id in ['JOB-one','JOB-two']:
            folder=self.manager.root/task_id
            write_json(folder/'status.json',{'taskId':task_id,'status':'completed','runConfig':request,'runType':'simulation','createdAt':'2026-10-16'})
            write_json(folder/'result.json',{'taskId':task_id,'hydrograph':graph})
            write_json(folder/'routing-evidence.json',{'available':True})
        windows=[{'id':'original-a','name':'原洪水名称','start':'2017-06-20T00:00:00','end':'2017-06-20T01:00:00','purpose':'验证'},
                 {'id':'original-b','name':'自定义洪水','start':'2017-06-20T01:00:00','end':'2017-06-20T02:00:00','purpose':'率定'}]
        frozen=copy.deepcopy(request);frozen['period']={'mode':'event','events':windows,'warmupDays':7}
        folder=self.root/'operations/OP-business'
        write_json(folder/'status.json',{'taskId':'OP-business','status':'completed','primaryTaskId':'JOB-one','childTaskIds':['JOB-one','JOB-two'],
            'runConfig':{'operation':'eventSimulation','events':windows,'context':request['context']},'createdAt':'2026-10-16'})
        result={'primaryTaskId':'JOB-one','frozenRequest':frozen,'hydrographs':{row['id']:graph for row in windows},
            'eventStates':[{'eventId':row['id'],'taskId':task,'start':row['start'],'end':row['end']} for row,task in zip(windows,['JOB-one','JOB-two'])]}
        write_json(folder/'result.json',result)
        return folder,result
    def test_one_business_result_names_whole_job_and_preserves_processes(self):
        folder,result=self.result_fixture();before=(folder/'result.json').read_bytes()
        rows=self.manager.results.catalog()['results']
        self.assertEqual([row['id'] for row in rows],['OP-business'])
        self.assertFalse(rows[0]['saved'])
        self.manager.results.update('OP-business',{'name':'六月洪水成果','notes':'两场','collection':'','favorite':False,'revision':0})
        self.assertFalse(self.manager.results.catalog()['results'][0]['saved'])
        self.manager.results.update('OP-business',{'name':'六月洪水成果','notes':'两场','collection':'','favorite':True,'revision':1})
        favorite=self.manager.results.catalog()['results'][0]
        self.assertFalse(favorite['saved'])
        self.assertTrue(favorite['favorite'])
        self.assertEqual((folder/'result.json').read_bytes(),before)
        self.assertEqual(self.manager.results.process('OP-business','original-a')['purpose'],'验证')
        self.assertEqual(self.manager.results.process('OP-business','original-b')['sourceTaskId'],'JOB-two')
        with self.assertRaisesRegex(ValueError,'明确'):
            self.manager.results.process('OP-business')
    def test_result_to_scheme_preserves_all_windows_and_fixed_initial_values(self):
        self.result_fixture()
        request=self.manager.results.scheme_request('OP-business')
        self.assertEqual(len(request['period']['events']),2)
        self.assertEqual(request['calibration']['evaluationMode'],'manual')
        self.assertEqual(request['calibration']['initialParameters'],simulation()['parameters'])
        self.assertEqual(request['inputs']['bundleVersionId'],'frozen-input')
        saved=self.manager.schemes.save({'name':'六月方案','definition':input_definition(request),'calculationDraft':request})
        self.assertEqual(saved['revision'],1)
    def test_section_rename_is_durable_location_immutable_and_conflict_checked(self):
        definition={'clicked':[119.5,30.1],'queried':[119.5001,30.1001],'distanceM':15,'reachId':'R0010','spatialSHA256':'a'*64}
        with patch.object(self.manager.river_sections,'locate',return_value=definition):
            row=self.manager.river_sections.create({'name':'上游断面','resultId':'OP-business','processId':'original-a','point':{'lon':119.5,'lat':30.1}})
        renamed=self.manager.river_sections.rename(row['id'],{'name':'上游桥','revision':1})
        self.assertEqual(renamed['definition'],definition)
        self.assertEqual(self.manager.river_sections.get(row['id'],None)['name'],'上游桥')
        with self.assertRaisesRegex(RuntimeError,'其他页面'):
            self.manager.river_sections.rename(row['id'],{'name':'旧页面','revision':1})
        with self.assertRaises(ValueError):
            self.manager.river_sections.rename(row['id'],{'name':'移动位置','revision':2,'point':[119,30]})
    def test_outlet_reads_exact_selected_process_without_new_task(self):
        self.result_fixture()
        section={'id':'SECTION-XUFAN','name':'徐畈（二）','definition':{'spatialSHA256':'a'*64}}
        with patch.object(self.manager.river_sections,'bundle',return_value={}),patch.object(self.manager.river_sections,'get',return_value=section),patch('river_sections.spatial_identity',return_value='a'*64),patch('diagnostic_jobs.point_source'),patch.object(self.manager,'submit') as submit:
            answer=self.manager.river_sections.query({'resultId':'OP-business','processId':'original-a','sectionId':'SECTION-XUFAN'})
            self.assertEqual(answer['result']['pointQuery']['series']['flow_cms'],[1.25,2.25])
            submit.assert_not_called()
        with patch.object(self.manager.river_sections,'bundle',return_value={}),patch.object(self.manager.river_sections,'get',return_value=section),patch('river_sections.spatial_identity',return_value='b'*64):
            with self.assertRaisesRegex(ValueError,'重新定位'):
                self.manager.river_sections.query({'resultId':'OP-business','processId':'original-a','sectionId':'SECTION-XUFAN'})
    def test_explicit_pet_and_defaults_do_not_rewrite_old_versions(self):
        registry=ScienceVersions(self.root)
        first=registry.register_conditions({'evaporation':{'mode':'constant-assumption','valueMmPerHour':0},'statePolicy':{'strategy':'independent-warmup','warmupHours':168}})
        monthly=[3,3,3.5,4,4.5,5,5.5,5.5,4.5,3.5,3,3]
        second=registry.register_conditions({'baseVersionId':first['scienceVersionId'],'evaporation':{'mode':'monthly-climatology-assumption','monthlyValuesMmPerDay':monthly},'statePolicy':{'strategy':'independent-warmup','warmupHours':0}})
        self.assertEqual(registry.conditions(first['scienceVersionId'])['evaporation']['valueMmPerHour'],0)
        self.assertEqual(registry.conditions(second['scienceVersionId'])['evaporation']['monthlyValuesMmPerDay'],monthly)
        for bad in (-1,True,float('nan')):
            with self.assertRaises(ValueError):
                registry.register_conditions({'evaporation':{'mode':'constant-assumption','valueMmPerHour':bad},'statePolicy':{'strategy':'independent-warmup','warmupHours':168}})
        defaults=RoutingVersions(self.root);old=defaults.defaults();edited=copy.deepcopy(old['parameters']);edited['default_slope_k_hours']['RS']=3
        new=defaults.defaults({'parameters':edited,'revision':0})
        self.assertEqual(defaults.resolve(old['routingVersionId'])['parameters']['default_slope_k_hours']['RS'],2)
        self.assertEqual(defaults.defaults()['routingVersionId'],new['routingVersionId'])
        with self.assertRaises(RuntimeError):
            defaults.defaults({'parameters':edited,'revision':0})

    def test_simultaneous_section_queries_share_task_and_numeric_changes_invalidate(self):
        from concurrent.futures import ThreadPoolExecutor
        self.result_fixture()
        source=self.manager.root/'JOB-one'
        write_json(source/'request.json',{'period':{},'inputs':{},'routing':{},'context':{},
            'lineage':{'schemeId':'SCHEME-source','schemeName':'原率定方案','schemeRevision':3,
                       'modelImplementationSHA256':'a'*64}})
        (source/'routing-runoff.npz').write_bytes(b'array-content')
        section={'id':'SECTION-'+'a'*32,'name':'上游断面','definition':{'spatialSHA256':'a'*64,'queried':[119.5,30.1]}}
        tasks=[]
        def enqueue(payload,**kwargs):
            task={'taskId':'JOB-query-'+str(len(tasks)),'status':'queued','runConfig':payload}
            tasks.append(task);return task
        body={'resultId':'OP-business','processId':'original-a','sectionId':section['id']}
        with patch.object(self.manager.river_sections,'bundle',return_value={}),patch.object(self.manager.river_sections,'get',return_value=section),patch('river_sections.spatial_identity',return_value='a'*64),patch('diagnostic_jobs.point_assets',return_value={}),patch('section_cache.query_implementation',return_value='program-A') as program,patch.object(self.manager,'list',side_effect=lambda:list(tasks)),patch.object(self.manager,'_enqueue',side_effect=enqueue) as submit:
            with ThreadPoolExecutor(max_workers=2) as pool:
                pair=list(pool.map(self.manager.river_sections.query,[body,body]))
            self.assertEqual(pair[0]['taskId'],pair[1]['taskId']);self.assertEqual(submit.call_count,1)
            section_request=submit.call_args.args[0]
            self.assertEqual(section_request['runType'],'pointQuery')
            self.assertNotIn('lineage',section_request)
            self.assertNotIn('schemeId',section_request)
            section['name']='只改名称'
            self.assertEqual(self.manager.river_sections.query(body)['taskId'],pair[0]['taskId'])
            original=read_json(source/'request.json');original['parameters']={'WM':151}
            write_json(source/'request.json',original)
            self.assertNotEqual(self.manager.river_sections.query(body)['taskId'],pair[0]['taskId'])
            program.return_value='program-B'
            self.manager.river_sections.query(body)
            self.assertEqual(submit.call_count,3)

if __name__=='__main__':unittest.main()
