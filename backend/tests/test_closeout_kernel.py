"""Independent scalar and analytical checks for the liquid hourly model."""
import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import MODEL
sys.path.insert(0,str(MODEL/'code'))
from runoff_platform.core.types import LandParams, LandState
from runoff_platform.models.xaj import simulate_land
from runoff_platform.models.xaj_vectorized import simulate_xaj_liquid_grids, LAND_STATE_FIELDS
from runoff_platform.data.precipitation_idw import idw_interpolate_hourly
from xaj_routing.algorithms import linear_reservoir_step, muskingum_step, muskingum_coefficients


class CloseoutKernelTests(unittest.TestCase):
    def test_vectorized_matches_separate_scalar_equations_in_dry_wet_and_saturated_cases(self):
        params=LandParams()
        cases=[(0.,0.,0.,0.,0.),(params.WUM,params.WLM,params.WDM,params.SM,.8),(3.,30.,10.,8.,.3)]
        rain=np.array([0.,.2,2.,30.,100.,0.,0.,.01,5.,0.])
        forcing=pd.DataFrame({'precipitation':rain,'temperature':20.,'pet':np.array([.1,0.,1.,.1,0.,.2,2.,0.,.1,.4])},index=pd.date_range('2020-01-01',periods=len(rain),freq='h'))
        for wu,wl,wd,s,fr in cases:
            state=LandState(swe=0.,snow_liquid=0.,WU=wu,WL=wl,WD=wd,S=s,FR=fr)
            scalar=simulate_land(forcing,params=params,state=state)
            initial={key:np.array([value]) for key,value in zip(LAND_STATE_FIELDS,[0.,0.,wu,wl,wd,s,fr])}
            batch=simulate_xaj_liquid_grids(rain[:,None],params,forcing.pet.to_numpy(),initial,True)
            for index,key in enumerate(['RS','RI','RG']):
                np.testing.assert_allclose(batch[index][:,0],scalar[key].to_numpy(),rtol=1e-7,atol=1e-8)
            for key in ['WU','WL','WD','S','FR','EU','EL','ED','et_actual']:
                np.testing.assert_allclose(batch[4][key][:,0],scalar[key].to_numpy(),rtol=1e-10,atol=1e-9)
            self.assertLess(np.abs(batch[4]['water_balance_error']).max(),1e-10)

    def test_hand_calculated_dry_recession(self):
        p=LandParams();s=10.;fr=.4
        state={key:np.array([value]) for key,value in zip(LAND_STATE_FIELDS,[0,0,10,30,20,s,fr])}
        out=simulate_xaj_liquid_grids(np.zeros((4,1)),p,0,state,True)
        for hour in range(4):
            stored=s*(1-p.KI-p.KG)**hour
            self.assertAlmostEqual(out[1][hour,0],stored*p.KI*fr,places=7)
            self.assertAlmostEqual(out[2][hour,0],stored*p.KG*fr,places=7)
            self.assertEqual(out[0][hour,0],0.)

    def test_idw_exact_colocation_and_missing_neighbor(self):
        values=np.array([[2.,8.],[np.nan,6.],[3.,np.nan]])
        rain,count=idw_interpolate_hourly(values,np.array([0.,2.]),np.array([0.,0.]),np.array([0.,1.]),np.array([0.,0.]))
        np.testing.assert_allclose(rain,[[2.,5.],[6.,6.],[3.,3.]])
        np.testing.assert_array_equal(count,[2,1,1])

    def test_routing_impulse_volume_and_muskingum_storage(self):
        flow=0.;outputs=[]
        for hour in range(1000):
            flow=linear_reservoir_step(10. if hour==0 else 0.,flow,4*3600,3600)
            outputs.append(flow)
        self.assertAlmostEqual(sum(outputs),10.,places=10)
        k,x,dt=7200.,.2,3600.
        coeffs=muskingum_coefficients(k,x,dt)
        self.assertAlmostEqual(coeffs.c0+coeffs.c1+coeffs.c2,1.,places=14)
        self.assertGreaterEqual(min(coeffs.c0,coeffs.c1,coeffs.c2),0.)
        previous_in,previous_out=0.,0.
        for value in [10.,0.,0.,5.,0.,0.]:
            output=muskingum_step(value,previous_in,previous_out,k,x,dt)
            previous_storage=k*(x*previous_in+(1-x)*previous_out)
            storage=k*(x*value+(1-x)*output)
            self.assertAlmostEqual(storage-previous_storage,dt*.5*(value+previous_in-output-previous_out),places=8)
            previous_in,previous_out=value,output
