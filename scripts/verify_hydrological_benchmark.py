"""Check every saved flow, score, hourly state and checkpoint of both variants."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workspace',required=True);args=parser.parse_args()
runtime=Path(args.workspace).resolve()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from common import read_json,write_json
reports=[]
def compare(left,right,path=''):
    if isinstance(left,dict):
        assert set(left)==set(right),(path,set(left)^set(right))
        for key in left:compare(left[key],right[key],path+'/'+key)
    elif isinstance(left,list):
        assert len(left)==len(right),path
        for i,(a,b) in enumerate(zip(left,right)):compare(a,b,path+'/'+str(i))
    elif isinstance(left,(int,float)) and not isinstance(left,bool):
        np.testing.assert_allclose(left,right,rtol=1e-12,atol=1e-12,err_msg=path)
    else:assert left==right,(path,left,right)
for case in ('single','events','calibration','original'):
    paths=[runtime/f'benchmark-{case}-{variant}.json' for variant in ('hourly','batch')]
    assert all(path.is_file() for path in paths), 'Missing required benchmark: ' + case
    old,new=map(read_json,paths)
    assert len(old['runs']) == len(new['runs']) == 2, 'Expected first and warm runs: ' + case
    maximum=0
    for a,b in zip(old['runs'],new['runs']):
        assert len(a['tasks']) == len(b['tasks']) > 0, 'Task count mismatch: ' + case
        for aid,bid in zip(a['tasks'],b['tasks']):
            af,bf=runtime/'tasks'/aid,runtime/'tasks'/bid
            ar,br=read_json(af/'result.json'),read_json(bf/'result.json')
            for key in ('hydrograph','hydrographs','metrics','bestParameters','bestRoutingParameters','calibrationMetrics','validationMetrics','independentTest'):
                assert (key in ar) == (key in br), 'Result field mismatch: ' + key
                if key in ar:compare(ar[key],br[key],key)
            assert (af/'checkpoint.json').is_file() == (bf/'checkpoint.json').is_file(), 'Checkpoint mismatch'
            if (af/'checkpoint.json').is_file():
                ac,bc=read_json(af/'checkpoint.json'),read_json(bf/'checkpoint.json')
                for key in ('land','routing','parameters','time','gridIds'):compare(ac[key],bc[key],'checkpoint/'+key)
                with np.load(af/'grid-history.npz',allow_pickle=False) as aa,np.load(bf/'grid-history.npz',allow_pickle=False) as bb:
                    assert aa.files==bb.files
                    for key in aa.files:
                        if key=='times':assert aa[key].tolist()==bb[key].tolist()
                        else:
                            np.testing.assert_allclose(aa[key],bb[key],rtol=1e-12,atol=1e-12)
                            maximum=max(maximum,float(np.abs(aa[key]-bb[key]).max()))
    reports.append({'case':case,'numericalMaxAbs':maximum,'exactWithinTolerance':True,'oldSeconds':[r['seconds'] for r in old['runs']],'newSeconds':[r['seconds'] for r in new['runs']],
        'firstCallCaveat':'new processes share an existing disk compiler cache; compare warm timings for the call-pattern change'})
write_json(runtime/'benchmark-comparison.json',reports)
print(json.dumps(reports),flush=True)
