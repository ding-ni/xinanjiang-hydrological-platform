"""Replay the actual 16-flood search and run three independent manual trials."""
import argparse
import json
import time
import urllib.request
import numpy as np
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:18904')
parser.add_argument('--output',required=True)
parser.add_argument('--search-task',help='Audit an already completed full search and finish its states without searching again')
parser.add_argument('--automatic-only',action='store_true',help='Verify the complete search and final states, then stop')
args=parser.parse_args();output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
scheme='SCHEME-8a5eea9752cf4cf79ffec638f98cf086'
source='OP-c5ebbdd1a9db4c18bf503aeefe07b691'
report={'trials':[],'errors':[]}
def api(path,body=None):
    request=urllib.request.Request(args.url+'/api/model/'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=180) as response:return json.load(response)
def wait(task):
    started=time.monotonic();previous=None
    while task['status'] in ('queued','running','cancelling'):
        stage=task.get('stageLabel')
        if stage!=previous:print('CALCULATION',stage,flush=True);previous=stage
        if time.monotonic()-started>2400:raise TimeoutError('Calculation exceeded 40 minutes')
        time.sleep(2);task=api('operations/'+task['taskId'])
    assert task['status']=='completed',{key:task.get(key) for key in ('taskId','status','errorMessage','childTaskIds')}
    return task,api('operations/'+task['taskId']+'/result'),round(time.monotonic()-started,3)
before=api('model-schemes/'+scheme)
original=api('operations/'+source+'/result')
try:
    if args.search_task:
        started=time.monotonic()
        repeat=api('operations',{'operation':'stateReplay','sourceTaskId':args.search_task})
        task,finished,seconds=wait(repeat)
        replayed=api('tasks/'+args.search_task+'/result')
        report['automaticRecovery']={'searchTaskId':args.search_task,'stateOperationId':task['taskId'],'stateSeconds':seconds}
    else:
        repeat=api('operations',{'operation':'rerun','sourceTaskId':source})
        task,replayed,seconds=wait(repeat)
    assert replayed['optimizer']['evaluations']==original['optimizer']['evaluations']==960
    assert replayed['bestParameters']==original['bestParameters']
    assert replayed['eventMetrics']==original['eventMetrics']
    assert replayed['hydrographs']==original['hydrographs']
    checked=set();differences={}
    for row in original['eventStates']:
        actual=next(item for item in replayed['eventStates'] if item['eventId']==row['eventId'])
        pair=(row['endStateVersionId'],actual['endStateVersionId'])
        if pair in checked:continue
        checked.add(pair)
        left=api('states/'+pair[0]+'/checkpoint');right=api('states/'+pair[1]+'/checkpoint')
        assert left['time']==right['time']
        for category in ('land','routing'):
            assert left[category].keys()==right[category].keys()
            for key,value in left[category].items():
                a,b=np.asarray(value),np.asarray(right[category][key])
                np.testing.assert_allclose(a,b,rtol=1e-12,atol=1e-12)
                differences[category+'.'+key]=float(np.max(np.abs(a-b)))
    report['automatic']={'id':task['taskId'],'seconds':seconds,'timings':replayed.get('timings'),'evaluations':960}
    report['finalStateMaximumDifferences']=differences
    print('PASS repeat automatic search matches all saved hydrographs and event metrics',flush=True)
    if args.automatic_only:
        report['status']='passed'
        raise SystemExit(0)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1366,'height':768})
        page=context.new_page();page.set_default_timeout(60000)
        page.on('pageerror',lambda error:report['errors'].append(str(error)))
        page.goto(args.url+'/#/calibration?schemeId='+scheme);page.wait_for_load_state('networkidle')
        page.get_by_role('button',name='人工试调',exact=True).click()
        # The earliest flood has the same saved continuous origin but a shorter
        # necessary trajectory. This is a UI flow check, not a speed benchmark.
        page.locator('.flood-process').filter(has_text='31210811').first.click()
        page.get_by_role('button',name='人工参数',exact=True).click()
        choices=page.get_by_role('combobox',name='试调参数来源')
        candidates=choices.locator('optgroup[label="本方案自动率定"] option').evaluate_all('rows=>rows.map(row=>row.value)')
        assert candidates
        choices.select_option(candidates[-1])
        for index in range(3):
            input=page.locator('.parameter-panel .param-row').filter(has_text='SM').locator('input[type="number"]').first
            expect(input).to_be_enabled()
            input.fill(str(20+index));input.dispatch_event('change')
            started=time.monotonic()
            with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/api/model/operations'),timeout=60000) as posted:
                page.get_by_role('button',name='试算当前洪水',exact=True).click()
            submission=posted.value.json()
            status,trial,elapsed=wait(submission)
            expect(page.get_by_role('button',name='试算当前洪水',exact=True)).to_be_enabled(timeout=120000)
            assert len(trial['hydrographs'])==1 and 'xf-31210811' in trial['hydrographs']
            assert trial['bestParameters']['SM']==20+index
            manual=page.locator('.curve-chip').filter(has_text='人工试调')
            assert manual.count()==2,manual.count()
            assert page.locator('.curve-chip').filter(has_text='自动率定参考').count()==1
            assert api('model-schemes/'+scheme)['calculationDraft']==before['calculationDraft']
            report['trials'].append({'id':status['taskId'],'seconds':round(time.monotonic()-started,3),'timings':trial.get('timings'),'SM':20+index})
            print('PASS manual',index+1,'keeps scheme settings and two successful curves',flush=True)
        page.locator('.process-panel').screenshot(path=str(output/'three-trials.png'))
        page.get_by_role('button',name='保存参数',exact=True).click()
        page.locator('#parameter-name').fill('过程工作台验收参数')
        page.locator('.el-dialog:visible').get_by_role('button',name='保存',exact=True).click()
        expect(page.get_by_text('参数已保存：过程工作台验收参数',exact=False)).to_be_visible()
        adopted=api('model-schemes/'+scheme)
        parameter=api('parameter-schemes/'+adopted['adoptedParameter']['parameter_id'])
        assert parameter['parameters']['SM']==22
        assert adopted['calculationDraft']==before['calculationDraft'] and adopted['revision']==before['revision']
        assert not report['errors'],report['errors']
        report['savedParameterId']=parameter['id']
        report['status']='passed'
        browser.close()
finally:
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
