"""Real API regression on an explicitly isolated workspace server."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:18898')
parser.add_argument('--output',default='开发产物/hydrological-workspace-api.json')
args=parser.parse_args();sys.stdout.reconfigure(encoding='utf-8')
base=args.url+'/api/model/'
def call(path,body=None):
    request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
workspace=call('workspace')
assert any('开发产物' in row['path'] for row in workspace['directories'] if row['id']=='workspace'),workspace
report={}
def finish(row):
    started=time.perf_counter()
    while time.perf_counter()-started<900:
        status=call('operations/'+row['taskId'])
        if status['status'] in {'completed','archived'}:return call('results/'+row['taskId'])
        if status['status'] not in {'queued','running','cancelling'}:raise AssertionError(status)
        time.sleep(1)
    raise TimeoutError('验收作业未完成')
parameter=call('parameter-schemes',{'name':'验收参数（隔离工作区）','sourceTaskId':'JOB-benchmark-single-batch-1-0'})
started=time.perf_counter()
operation=call('operations',{'operation':'eventSimulation','parameterSchemeId':parameter['id'],'events':['xf-31880808','xf-31070314']})
payload=finish(operation);result=payload['result'];identifier=operation['taskId']
assert len(payload['processes'])==2,payload['processes']
report['simulation']={'id':identifier,'seconds':round(time.perf_counter()-started,3),'timings':result['timings']}
call('result-metadata/'+identifier,{'name':'两场洪水模拟验收','collection':'','notes':'1988与2007','favorite':False,'revision':0})
catalog=call('result-catalog')['results']
row=next(row for row in catalog if row['id']==identifier);assert row['saved']
assert not any(row['id'] in payload['status']['childTaskIds'] for row in catalog)
process=next(row for row in payload['processes'] if row['id']=='xf-31880808')
sections=call('river-sections?resultId='+identifier+'&processId='+process['id'])['sections']
outlet=next(row for row in sections if row['id']=='SECTION-XUFAN')
query={'resultId':identifier,'processId':process['id'],'sectionId':'SECTION-XUFAN'}
answer=call('result-section-queries',query)
assert answer['cacheHit'] and answer['taskId'] is None
assert answer['result']['pointQuery']['series']['flow_cms']==[row['value'] for row in process['hydrograph']['forecast']]
lon,lat=outlet['definition']['queried']
new=call('river-sections',{'name':'徐畈复用断面','resultId':identifier,'processId':process['id'],'point':{'lon':lon,'lat':lat}})
renamed=call('river-sections/'+new['id']+'/rename',{'name':'徐畈保存断面','revision':new['revision']})
assert renamed['definition']==new['definition']
request=call('results/'+identifier+'/scheme-conditions')
assert len(request['period']['events'])==2
report['sections']={'savedSection':new['id'],'location':new['definition'],'exitFlowExact':True}
started=time.perf_counter()
replay=finish(call('operations',{'operation':'rerun','sourceTaskId':identifier}))
assert set(result['hydrographs'])==set(replay['result']['hydrographs'])
for key in result['hydrographs']:
    assert result['hydrographs'][key]==replay['result']['hydrographs'][key]
report['replay']={'id':replay['status']['taskId'],'seconds':round(time.perf_counter()-started,3),'exact':True}
target=Path(args.output);target.parent.mkdir(exist_ok=True,parents=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)
