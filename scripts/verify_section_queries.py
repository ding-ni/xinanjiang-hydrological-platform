"""Measure real section query paths in the isolated 16-flood workspace."""
import argparse
import json
import time
import urllib.request
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:18904')
parser.add_argument('--output',required=True)
args=parser.parse_args()
report={'queries':[]}
result='OP-c5ebbdd1a9db4c18bf503aeefe07b691'
process='xf-31220317'
def api(path,body=None):
    request=urllib.request.Request(args.url+'/api/model/'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=180) as response:return json.load(response)
def timed(path,body):
    started=time.monotonic();answer=api(path,body)
    return answer,round(time.monotonic()-started,3)
try:
    directory=api('result-catalog/'+result+'/processes')
    selected=next(row for row in directory['processes'] if row['id']==process)
    task=api('tasks/'+selected['sourceTaskId'])
    spatial=api('spatial-products?bundleVersionId='+task['runConfig']['inputs']['bundleVersionId'])
    reaches=[row for row in spatial['topology']['reaches'] if row['length_km']>0.7 and '_UP' not in row['reach_id']]
    for index,reach in enumerate(reaches[:2]):
        start,end=reach['startLonLat'],reach['endLonLat']
        point={'lon':(start[0]+end[0])/2,'lat':(start[1]+end[1])/2}
        section,locate_seconds=timed('river-sections',{'name':'断面性能验收'+str(index+1),'projectId':'nananxi-xufan','point':point})
        body={'resultId':result,'processId':process,'sectionId':section['id']}
        query,submit_seconds=timed('result-section-queries',body)
        assert query.get('taskId'),query
        duplicate=api('result-section-queries',body)
        assert duplicate['taskId']==query['taskId'],'Concurrent query started a second task'
        started=time.monotonic();stages=[]
        while query['status'] in ('queued','running','cancelling'):
            stage=query.get('phase') or query.get('stageLabel')
            if stage and stage not in stages:
                stages.append(stage);print('SECTION',index+1,stage,flush=True)
            if time.monotonic()-started>1800:raise TimeoutError('Section acceptance exceeded 30 minutes')
            time.sleep(1)
            query=api('tasks/'+query['taskId'])
        assert query['status']=='completed',query
        answer=api('result-section-queries/'+query['taskId'])
        series=answer['pointQuery']['series']
        assert series['time'][0][:16]==selected['start'][:16]
        assert series['time'][-1][:16]==selected['end'][:16]
        assert len(series['time'])==len(series['flow_cms'])
        cached,cache_seconds=timed('result-section-queries',body)
        assert cached['cacheHit'] and cached['taskId']==query['taskId']
        renamed=api('river-sections/'+section['id']+'/rename',{'name':section['name']+'已改名','revision':section['revision']})
        assert api('result-section-queries',body)['taskId']==query['taskId']
        row={'taskId':query['taskId'],'locationSeconds':locate_seconds,'submitSeconds':submit_seconds,
            'wallSeconds':round(time.monotonic()-started,3),'cachedLookupSeconds':cache_seconds,
            'stages':stages,'timings':answer.get('timings'),
            'preparationCacheHit':answer.get('preparationCacheHit'),'calculationRange':answer.get('calculationRange'),
            'window':{'start':selected['start'],'end':selected['end']},'points':len(series['time'])}
        report['queries'].append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    assert report['queries'][1]['preparationCacheHit'],'Second section did not reuse source preparation'
    report['status']='passed'
finally:
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
