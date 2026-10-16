import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hydro_database import HydroDatabase
from model_schemes import ModelSchemes,input_definition
from test_unified_backend import calibration
from test_continuous_calibration import continuous_request
from contracts import normalize_request
from catalog import case_catalog

class TrialWorkspaceTests(unittest.TestCase):
    def test_named_parameter_retry_adopts_existing_record_after_conflict(self):
        from parameter_schemes import ParameterSchemes
        from common import write_json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = MagicMock(runtime_root=root, root=root/'tasks')
            manager.schemes = ModelSchemes(HydroDatabase(root))
            request = calibration()
            request['inputs']['bundleVersionId'] = 'INPUT-test'
            request['science'] = {'versionId':'SCIENCE-test'}
            request['initialization'] = {'strategy':'FIXED_WARMUP'}
            scheme = manager.schemes.save({'name':'试调方案','definition':input_definition(request),'calculationDraft':request})
            task_id = 'JOB-parameter-save'
            write_json(manager.root/task_id/'request.json', request)
            manager.result.return_value = {'bestParameters':request['calibration']['initialParameters'],'science':request['science']}
            manager.routing_versions.resolve.return_value = {'routingVersionId':'ROUTING-test','parameters':{'preserved':True}}
            manager.science_versions.resolve.return_value = {'statePolicy':{'strategy':'fixed-warmup','warmupHours':168}}
            manager.science_versions.export.return_value = {'versionId':'SCIENCE-test'}
            service = ParameterSchemes(manager)
            # Another page adopts a parameter after the dialog captured revision 0.
            manager.schemes.adopt_parameter(scheme['id'],'PARAMSET-another',0)
            body = {'name':'自定义参数','sourceTaskId':task_id,'requestId':'save-once','schemeId':scheme['id'],'adoptionRevision':0}
            first = service.save(body)
            self.assertIsNone(first['adoptedParameter'])
            self.assertIn('另一页面', first['adoptionError'])
            retried = service.save({**body,'adoptionRevision':1})
            self.assertEqual(retried['id'], first['id'])
            self.assertIsNone(retried['adoptionError'])
            self.assertEqual(service.save({**body,'adoptionRevision':1})['id'], first['id'])
            self.assertEqual(len(service.list()['schemes']),1)
            self.assertEqual(manager.schemes.get(scheme['id'])['calculationDraft'], scheme['calculationDraft'])

    def test_draft_trial_prepares_its_actual_period_not_saved_scheme_binding(self):
        from hydrological_operations import HydrologicalOperations
        operation = HydrologicalOperations.__new__(HydrologicalOperations)
        operation.manager = MagicMock()
        operation.inputs = MagicMock()
        operation.lock = threading.RLock()
        operation.check_cancelled = MagicMock()
        operation.update = MagicMock()
        operation.wait = MagicMock(side_effect=RuntimeError('stop-after-preparation'))
        request = calibration()
        request['inputs'].update(dataMode='historical-database', bundleVersionId='HISTORICAL_SPATIAL_V1')
        request['lineage'] = {'schemeId':'SCHEME-old', 'trialContext':{'scheme':{'id':'SCHEME-old','revision':1}}}
        with patch('scheme_inputs.reuse_preparation',return_value={'taskId':'PREP-test'}) as prepare:
            with self.assertRaisesRegex(RuntimeError,'stop-after-preparation'):
                operation.prepare('OP-test',request,'stationIdw')
        self.assertEqual(prepare.call_args.args[1]['period'],request['period'])
        operation.manager.schemes.get.assert_not_called()
        operation.manager.database.connect.assert_not_called()

    def test_process_digest_tracks_driving_not_total_period_count(self):
        import numpy as np
        import pandas as pd
        from types import SimpleNamespace
        from worker import driving_digest
        times=pd.date_range('2017-06-18',periods=72,freq='h')
        trajectory=SimpleNamespace(times=times,precip=np.ones((72,2)),pet_mm_h=np.full(72,0.1))
        evaluation=SimpleNamespace(start=times[24],end=times[47],observed=pd.Series(np.arange(24.)))
        first=driving_digest(trajectory,evaluation,['g1','g2'])
        trajectory.precip[60]=9  # Future rain outside this process cannot affect it.
        self.assertEqual(first,driving_digest(trajectory,evaluation,['g1','g2']))
        trajectory.precip[10]=2  # Antecedent rain is part of the comparison.
        self.assertNotEqual(first,driving_digest(trajectory,evaluation,['g1','g2']))

    def test_trial_does_not_save_scheme_or_overwrite_automatic_initial_values(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=ModelSchemes(HydroDatabase(folder))
            original=calibration()
            original['inputs']['bundleVersionId']='INPUT-test'
            saved=repo.save({'name':'自动条件','definition':input_definition(original),'calculationDraft':original})
            trial=copy.deepcopy(original);trial['calibration']['initialParameters']['SM']=30
            trial['scheme']={'id':saved['id'],'revision':1}
            frozen=repo.freeze_trial(trial,{'scope':'current','parameterSource':'临时参数'})
            self.assertNotIn('scheme',frozen)
            self.assertEqual(frozen['lineage']['schemeId'],saved['id'])
            self.assertEqual(frozen['calibration']['initialParameters']['SM'],30)
            self.assertEqual(repo.get(saved['id'])['calculationDraft'],saved['calculationDraft'])
            self.assertEqual(repo.get(saved['id'])['revision'],1)
            adopted=repo.adopt_parameter(saved['id'],'PARAMSET-one',0)
            self.assertEqual(repo.adopt_parameter(saved['id'],'PARAMSET-one',0),adopted)
            with self.assertRaisesRegex(RuntimeError,'另一页面'):
                repo.adopt_parameter(saved['id'],'PARAMSET-two',0)
            repo.adopt_parameter(saved['id'],'PARAMSET-two',1)
            self.assertEqual(repo.get(saved['id'])['calculationDraft'],saved['calculationDraft'])

    def test_manual_validation_flood_keeps_its_purpose_without_search_setup(self):
        request=calibration();row=next(row for row in request['period']['events'] if row['purpose']=='验证')
        request['period']['events']=[row];request['period']['eventSet']['selectedEventIds']=[row['id']]
        request['calibration'].update(evaluationMode='manual',objectives={})
        normalized=normalize_request(request,case_catalog())
        self.assertEqual(normalized['period']['events'][0]['purpose'],'验证')
        self.assertEqual(normalized['calibration']['optimizer']['maxEvaluations'],1)

    def test_continuous_single_window_retains_origin_and_is_idempotent(self):
        request=continuous_request();request['calibration']['evaluationMode']='manual'
        request['period']['manualProcessId']='VALIDATION'
        normalized=normalize_request(request,case_catalog())
        self.assertEqual([row['id'] for row in normalized['period']['windows']],['VALIDATION'])
        self.assertEqual(normalize_request(normalized,case_catalog()),normalized)
        self.assertEqual(normalized['period']['calibrationStart'],request['period']['calibrationStart'])

    def test_section_position_can_be_saved_without_a_result(self):
        from server import TaskManager
        with tempfile.TemporaryDirectory() as folder:
            manager=TaskManager(Path(folder)/'tasks')
            try:
                definition={'clicked':[119,30],'queried':[119.01,30.01],'distanceM':5,'reachId':'R1','spatialSHA256':'a'*64}
                with patch.object(manager.river_sections,'locate',return_value=definition) as locate:
                    row=manager.river_sections.create({'name':'桥下','projectId':'nananxi-xufan','point':{'lon':119,'lat':30}})
                self.assertEqual(row['definition'],definition)
                self.assertNotIn('resultId',locate.call_args.args[0])
                self.assertEqual(manager.river_sections.get(row['id'],None)['name'],'桥下')
            finally:
                manager.close()

if __name__=='__main__':unittest.main()
