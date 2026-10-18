import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration_search import sce_search, run_search
from catalog import case_catalog
from contracts import normalize_request
from test_unified_backend import calibration


class SceTests(unittest.TestCase):
    def specs(self):
        return [{'name': name, 'minimum': -3., 'maximum': 3.} for name in ['x', 'y']]

    def test_seeded_budget_and_constrained_convergence(self):
        calls=[]
        def objective(point):
            calls.append(point)
            if point['x']+point['y']>3:
                raise ValueError('constraint')
            return (point['x']-.5)**2+(point['y']+.25)**2
        config={'max_evaluations':400,'seed':42,'complexes':5}
        first=sce_search(self.specs(),objective,config)
        self.assertEqual(len(calls),first['evaluations'])
        self.assertLessEqual(len(calls),400)
        self.assertLess(first['best_loss'],.01)
        self.assertEqual(first,sce_search(self.specs(),objective,config))
        self.assertTrue(any(row.get('error') for row in first['history']))
        self.assertEqual(first['consumedConfiguration']['populationSize'],25)

    def test_incumbent_counts_once_and_small_budget_rejected(self):
        baseline={'x':0.,'y':0.}
        calls=[]
        best,outcome=run_search(self.specs(),{'algorithm':'SCE_UA','maxEvaluations':80,'populationSize':12,'seed':4},baseline,
            lambda p:calls.append(copy.deepcopy(p)) or p['x']**2+p['y']**2,
            lambda p,c:{**p,**c},dict,None,lambda *args:None)
        self.assertEqual(best,baseline)
        self.assertEqual(len(calls),outcome['evaluations'])
        self.assertEqual(outcome['history'][0]['kind'],'initial')
        self.assertEqual(sum(p==baseline for p in calls),1)
        with self.assertRaisesRegex(ValueError,'至少'):
            sce_search(self.specs(),lambda p:0,{'max_evaluations':25})

    def test_failure_cancellation_and_request_contract(self):
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            sce_search(self.specs(),lambda p:(_ for _ in ()).throw(RuntimeError('cancelled')),{'max_evaluations':40})
        with self.assertRaisesRegex(ValueError,'有效候选'):
            sce_search(self.specs(),lambda p:float('nan'),{'max_evaluations':40})
        request=calibration();request['calibration']['optimizer'].update(algorithm='SCE_UA',maxEvaluations=960,complexes=5)
        self.assertEqual(normalize_request(request,case_catalog())['calibration']['optimizer']['complexes'],5)
        request['calibration']['optimizer']['maxEvaluations']=4
        with self.assertRaisesRegex(ValueError,'至少需要'):
            normalize_request(request,case_catalog())
