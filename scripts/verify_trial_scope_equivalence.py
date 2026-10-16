"""Compare current-window and all-window trials with unchanged numerical conditions."""
import argparse
import copy
import json
import time
import uuid
import urllib.request
from pathlib import Path
import numpy as np

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:18902')
parser.add_argument('--source-report',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
def api(path,body=None):
    request=urllib.request.Request(args.url+'/api/model/'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
def finish(identifier):
    while True:
        row=api('operations/'+identifier)
        if row['status'] not in ('queued','running','cancelling'):
            assert row['status']=='completed',row
            return api('operations/'+identifier+'/result')
        time.sleep(.5)
source=json.loads(Path(args.source_report).read_text(encoding='utf-8'))
current=api('operations/'+source['resultIds'][-1]+'/result')
before=api('model-schemes/'+source['schemeId'])
request=copy.deepcopy(current['frozenRequest'])
request.pop('scheme',None);request.pop('science',None)
# All purposes and the original origin stay intact; only the display/evaluation scope changes.
request['period'].pop('manualProcessId',None);request['period'].pop('windows',None)
request['inputs'].update(bundleVersionId='HISTORICAL_SPATIAL_V1',sourceBundleVersionId='HISTORICAL_SPATIAL_V1',dataMode='historical-database')
for key in ('bundleManifestSHA256','preparationTaskId'):request['inputs'].pop(key,None)
request['lineage']={}
body={'operation':'manualTrial','requestId':'SCOPE-'+uuid.uuid4().hex,'request':request,'rainfallMethod':'stationIdw',
      'trialContext':{'scheme':{'id':source['schemeId'],'revision':before['revision']},'scope':'all','parameterSource':'same completed trial'}}
started=time.monotonic();operation=api('operations',body);all_result=finish(operation['taskId']);elapsed=time.monotonic()-started
assert set(all_result['hydrographs'])=={'CALIBRATION','VALIDATION','INDEPENDENT'}
report={'status':'passed','allOperation':operation['taskId'],'allSeconds':round(elapsed,3),'allStages':all_result['timings'],
        'currentSeconds':source['timings'][-1]['seconds'],'currentStages':current['timings'],'checks':[]}
for key in current['hydrographs']:
    a,b=current['hydrographs'][key],all_result['hydrographs'][key]
    assert a['forecast']==b['forecast'] and a['metrics']==b['metrics']
    assert current['processDrivingSHA256'][key]==all_result['processDrivingSHA256'][key]
    report['checks'].append(key+' curve, metrics and antecedent driving exactly equal')
    state_a=next(row for row in current['eventStates'] if row['eventId']==key)
    # Continuous jobs share a complete trajectory; compare checkpoints only at
    # the same true terminal time, never the last state of a longer trajectory.
    state_b=next(row for row in all_result['eventStates'] if row['eventId']==key)
    sa=api('states/'+state_a['endStateVersionId']+'/checkpoint')
    sb=api('states/'+state_b['endStateVersionId']+'/checkpoint')
    report['checks'].append(key+' checkpoint times '+sa['time']+' / '+sb['time'])
request['period']['manualProcessId']='INDEPENDENT'
single_body=copy.deepcopy(body);single_body.update(requestId='SCOPE-'+uuid.uuid4().hex,request=request)
single_body['trialContext'].update(scope='current',processId='INDEPENDENT')
started=time.monotonic();last=finish(api('operations',single_body)['taskId'])
report['lastWindowSeconds']=round(time.monotonic()-started,3)
assert list(last['hydrographs'])==['INDEPENDENT']
assert last['hydrographs']['INDEPENDENT']['forecast']==all_result['hydrographs']['INDEPENDENT']['forecast']
a=api('states/'+last['endStateVersionId']+'/checkpoint');b=api('states/'+all_result['endStateVersionId']+'/checkpoint')
assert a['time']==b['time']
for field in ('land','routing'):assert a[field]==b[field],field
report['checks'].append('current independent window and all windows: complete land/routing terminal states exactly equal at same time')
after=api('model-schemes/'+source['schemeId'])
assert before['calculationDraft']==after['calculationDraft'] and before['revision']==after['revision']
# Direct fixed-parameter simulation is the existing independent numerical path.
for entry in all_result['eventStates']:
    task=api('tasks/'+(entry.get('stateCalculationTaskId') or entry['taskId']))
    result=api('tasks/'+task['taskId']+'/result')
    a=all_result['hydrographs'][entry['eventId']]['forecast'];b=result['hydrograph']['forecast']
    saved={row['time']:row['value'] for row in b}
    assert np.allclose([row['value'] for row in a],[saved[row['time']] for row in a],rtol=1e-10,atol=1e-10)
report['checks'].append('all trial windows agree with existing fixed simulation at 1e-10 tolerance')
output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
