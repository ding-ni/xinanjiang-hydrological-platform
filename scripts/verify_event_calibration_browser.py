"""Real event calibration acceptance in a separately seeded workspace."""
import argparse
import copy
import json
import sys
import time
import uuid
from datetime import datetime, timedelta
import numpy as np
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:18917')
parser.add_argument('--output',required=True)
parser.add_argument('--scheme',default='SCHEME-8a5eea9752cf4cf79ffec638f98cf086')
parser.add_argument('--ui-scheme',help='Reuse an already numerically verified isolated scheme for UI checks')
args=parser.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
report={'checks':[],'errors':[],'operations':[]}
expect.set_options(timeout=45000)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':768})
    page=context.new_page()
    page.on('pageerror',lambda error:report['errors'].append(str(error)))
    def api(path,body=None):
        response=context.request.get(args.url+'/api/model/'+path,timeout=120000) if body is None else context.request.post(args.url+'/api/model/'+path,data=body,timeout=120000)
        assert response.ok,(path,response.status,response.text()[:1200])
        return response.json()
    def check(label):
        report['checks'].append(label);print('PASS',label,flush=True)
    def wait_operation(identifier):
        report['operations'].append(identifier)
        deadline=time.monotonic()+360
        while time.monotonic()<deadline:
            status=api('operations/'+identifier)
            if status['status'] not in ('queued','running','cancelling'):
                assert status['status']=='completed',status
                return api('operations/'+identifier+'/result')
            page.wait_for_timeout(400)
        raise AssertionError('real operation timeout '+identifier)
    def submit(request,kind='manualTrial',**extra):
        request=copy.deepcopy(request);request['requestId']='ACCEPT-'+uuid.uuid4().hex
        return wait_operation(api('operations',{'operation':kind,'request':request,**extra})['taskId'])
    def goto(path):
        page.goto(args.url+'/#'+path);page.reload();page.wait_for_load_state('networkidle')
    def chart_names():
        return page.locator('.process-panel .chart-host').evaluate("""el=>{
            const seen=new Set();let found;
            function walk(node){if(!node||typeof node!=='object'||seen.has(node))return;seen.add(node);
              if(node.el===el && node.component?.props.option)found=node.component.props.option.series.map(row=>row.name);
              if(node.component)walk(node.component.subTree);if(Array.isArray(node.children))node.children.forEach(walk)}
            walk(document.querySelector('#app').__vue_app__._container._vnode);return found;
        }""")
    try:
        original=api('model-schemes/'+args.scheme)
        goto('/calibration?schemeId='+args.scheme)
        expect(page.locator('.legacy-condition')).to_contain_text('2020-12-25 00:00')
        expect(page.get_by_role('button',name='开始率定',exact=True)).to_be_disabled()
        page.get_by_role('button',name='全部洪水',exact=True).click()
        expect(page.locator('.event-scope-count')).to_have_text('显示 226 场')
        before=len(api('operations')['operations'])
        page.get_by_role('button',name='人工试调',exact=True).click()
        expect(page.locator('.curve-chip')).to_have_count(1)
        assert len(api('operations')['operations'])==before
        expect(page.locator('.curve-controls')).not_to_contain_text(original['name'])
        checkbox=page.get_by_role('checkbox',name='显示自动率定',exact=True)
        assert '自动率定' in chart_names()
        checkbox.uncheck();expect(checkbox).not_to_be_checked()
        page.wait_for_timeout(200);assert '自动率定' not in chart_names()
        checkbox.check()
        for width,height in ((1366,768),(1920,1080)):
            page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            expect(page.locator('.selected-calculation')).to_contain_text('实测峰值')
            assert page.locator('.process-panel .chart-host').bounding_box()['height']>=420
            page.locator('.process-panel').screenshot(path=str(out/f'legacy-chart-{width}.png'))
        check('legacy-original-conditions-public-226-no-phantom-trials-and-automatic-visibility')

        if not args.ui_scheme:
            historical=api('operations/OP-368304527635404ea0412b8d4dcdb238/result')
            replay=wait_operation(api('operations',{'operation':'rerun','sourceTaskId':'OP-368304527635404ea0412b8d4dcdb238'})['taskId'])
            assert replay['frozenRequest']['calibration']['continuousOrigin']==historical['frozenRequest']['calibration']['continuousOrigin']
            for identifier,graph in historical['hydrographs'].items():
                np.testing.assert_allclose([row['value'] for row in replay['hydrographs'][identifier]['forecast']],
                                           [row['value'] for row in graph['forecast']],rtol=1e-10,atol=1e-10)
            check('historical-manual-result-replays-original-continuous-origin-and-flows')

        page.get_by_role('button',name='另建场次方案',exact=True).click()
        dialog=page.locator('.el-message-box')
        dialog.locator('input').fill('逐场预热验收 '+time.strftime('%H%M%S'))
        dialog.get_by_role('button',name='建立方案',exact=True).click()
        expect(page.locator('.legacy-condition')).to_have_count(0)
        expect(page.locator('.event-scope-count')).to_have_text('显示 226 场')
        expect(page.locator('.selection-counts')).to_contain_text('率定 0')
        page.get_by_role('button',name='率定设置',exact=True).click()
        settings=page.locator('.settings-dialog').filter(has=page.locator('#cal-settings-title'))
        expect(settings.locator('.scientific-conditions select').first).to_have_value('independent-warmup')
        expect(settings.get_by_text('连续演算',exact=True)).to_have_count(0)
        page.keyboard.press('Escape')
        assert api('model-schemes/'+args.scheme)['calculationDraft']==original['calculationDraft']
        check('create-event-scheme-resets-windows-origin-and-selection-with-seven-day-warmup')

        if args.ui_scheme:
            saved=api('model-schemes/'+args.ui_scheme)
            source=saved['calculationDraft']
            events=source['period']['events']
            automatic_row=next(row for row in api('operations')['operations'] if row['operation']=='calibration' and
                               row.get('runConfig',{}).get('request',{}).get('scheme',{}).get('id')==saved['id'] and row['status']=='completed')
            automatic=api('operations/'+automatic_row['taskId']+'/result')
            report['schemeId']=saved['id']
        else:
            # A small real search, using two public catalog floods with their full warmup.
            source=copy.deepcopy(original['calculationDraft'])
            catalogue=api('flood-catalog')['events']
            events=[{**next(row for row in catalogue if row['id']==identifier),'purpose':purpose}
                    for identifier,purpose in [('xf-31170619','率定'),('xf-31170319','验证')]]
            report['events']=[row['id'] for row in events]
            for key in ('sourcePlan','scheme','science'):source.pop(key,None)
            source['lineage']={}
            source['period']={'mode':'event','warmupDays':7,'events':events,'eventSet':{'selectedEventIds':[row['id'] for row in events]}}
            source['routing']={'versionId':api('database/routing-defaults')['routingVersionId']}
            source['calibration'].update(statePolicy='independent-warmup',target='runoff',searchParameters={'SM':{'minimum':8,'maximum':60}},
                                         optimizer={'algorithm':'DIFFERENTIAL_EVOLUTION','maxEvaluations':4,'populationSize':4,'strategy':'joint','seed':42})
            for key in ('continuousOrigin','jointProfile','routingSearchParameters','searchParameterOrder','evaluationMode'):source['calibration'].pop(key,None)
            source['inputs']['evaporation']={'mode':'monthly-climatology-assumption'}
            source['inputs'].update(bundleVersionId='HISTORICAL_SPATIAL_V1',sourceBundleVersionId='HISTORICAL_SPATIAL_V1',
                                    stationNetwork=api('database/network'),rainfallMethod='stationIdw',dataMode='historical-database',stationSourceId='LOCAL_OBSERVATIONS')
            source['requestId']='ACCEPT-'+uuid.uuid4().hex
            definition={'context':source['context'],'inputs':{key:source['inputs'][key] for key in ('bundleVersionId','rainfallMethod','dataMode','stationSourceId','stationNetwork','idwPower','minimumStations')},
                        'period':{key:source['period'][key] for key in ('mode','warmupDays','events')},'statePolicy':'independent-warmup','continuousOrigin':None}
            definition['period']['events']=sorted([{key:row[key] for key in ('id','start','end','purpose')} for row in events],key=lambda row:row['id'])
            saved=api('model-schemes',{'name':'真实逐场预热验收 '+time.strftime('%H%M%S'),'definition':definition,'calculationDraft':source,'editor':{}})
            report['schemeId']=saved['id']
            source['scheme']={'id':saved['id'],'revision':saved['revision']}
            automatic=submit(source,'calibration')
            assert automatic['statePolicy']=='independent-warmup'
            assert automatic['evaluationPolicy']=='EQUAL_INDEPENDENT_EVENTS'
            assert len({row['taskId'] for row in automatic['eventStates']})==2
            assert all(row['trajectoryStart']==row['start'] and row['trajectoryEnd']==row['end'] for row in automatic['eventStates'])
            check('real-automatic-search-has-independent-state-trajectories')

            # Fixed parameters must give identical graphs, scores and terminal state,
            # whether the selected floods run together or each runs alone.
            fixed=copy.deepcopy(source)
            fixed['calibration']['initialParameters']=automatic['bestParameters']
            fixed['calibration']['evaluationMode']='manual'
            trial_context={'scheme':{'id':saved['id'],'revision':saved['revision']},'scope':'current'}
            for event in events:
                single=copy.deepcopy(fixed)
                single['period']['events']=[event]
                single['period']['eventSet']['selectedEventIds']=[event['id']]
                one=submit(single,trialContext={**trial_context,'processId':event['id']})
                combined_graph=automatic['hydrographs'][event['id']]
                single_graph=one['hydrographs'][event['id']]
                assert [row['time'] for row in single_graph['forecast']]==[row['time'] for row in combined_graph['forecast']]
                np.testing.assert_allclose([row['value'] for row in single_graph['forecast']], [row['value'] for row in combined_graph['forecast']], rtol=1e-10, atol=1e-10)
                combined_metrics=next(row for row in automatic['eventMetrics'] if row['eventId']==event['id'])
                for key in ('NSE','peak_error_percent','peak_time_error_hours','volume_error_percent','nse','peakErr','timeErrH','volErr'):
                    left_metric,right_metric=one['eventMetrics'][0].get(key),combined_metrics.get(key)
                    if left_metric is not None and right_metric is not None:
                        assert np.isclose(float(left_metric),float(right_metric),rtol=1e-9,atol=1e-9), (event['id'],key,left_metric,right_metric)
                left=api('states/'+one['eventStates'][0]['endStateVersionId']+'/checkpoint')
                state=next(row for row in automatic['eventStates'] if row['eventId']==event['id'])
                right=api('states/'+state['endStateVersionId']+'/checkpoint')
                for part in ('land','routing'):
                    assert left[part].keys()==right[part].keys()
                    for key in left[part]:np.testing.assert_allclose(left[part][key],right[part][key],rtol=1e-10,atol=1e-10)
                assert left['drivingIdentity']['start']==(datetime.fromisoformat(event['start'])-timedelta(days=7)).isoformat()
                assert not left.get('parentStateVersionId')
            check('combined-and-single-flood-graphs-metrics-final-land-and-routing-states-match-within-tolerance')

        goto('/calibration?schemeId='+saved['id'])
        expect(page.locator('.process-panel canvas')).to_be_visible()
        before=len(api('operations')['operations'])
        page.get_by_role('button',name='人工试调',exact=True).click()
        page.wait_for_function("value=>Number(document.querySelector('#parameter-SM')?.value)===value",arg=automatic['bestParameters']['SM'])
        expect(page.locator('.curve-chip')).to_have_count(1)
        assert len(api('operations')['operations'])==before
        success=[]
        for sm in ('20','30','40'):
            page.locator('#parameter-SM').fill(sm);page.locator('#parameter-SM').press('Tab')
            expect(page.get_by_role('button',name='试算当前洪水',exact=True)).to_be_enabled()
            with page.expect_response(lambda r:r.url.endswith('/api/model/operations') and r.request.method=='POST') as response:
                page.get_by_role('button',name='试算当前洪水',exact=True).click()
            assert response.value.ok,response.value.text()
            success.append(wait_operation(response.value.json()['taskId']))
            expect(page.get_by_role('button',name='试算当前洪水',exact=True)).to_be_enabled()
        expect(page.locator('.curve-chip')).to_have_count(3)
        expect(page.locator('.curve-controls')).not_to_contain_text('人工试调1')
        expect(page.locator('.curve-controls')).to_contain_text('人工试调2')
        expect(page.locator('.curve-controls')).to_contain_text('人工试调3')
        assert all('无法叠加' not in page.locator('.curve-controls').inner_text() for _ in [0])
        assert api('model-schemes/'+saved['id'])['calculationDraft']==saved['calculationDraft']
        check('three-real-manual-trials-retain-two-successes-and-keep-saved-scheme')

        # Uncomputed draft values must never reach adopted parameter sets.
        page.locator('#parameter-SM').fill('50');page.locator('#parameter-SM').press('Tab')
        page.get_by_role('button',name='保存参数',exact=True).click()
        page.locator('#parameter-name').fill('已完成试算参数 '+time.strftime('%H%M%S'))
        page.locator('.parameter-dialog').get_by_role('button',name='保存',exact=True).click()
        expect(page.locator('#parameter-name')).not_to_be_visible()
        adopted=api('model-schemes/'+saved['id'])['adoptedParameter']
        assert api('parameter-schemes/'+adopted['parameter_id'])['parameters']['SM']==40
        page.reload();page.wait_for_load_state('networkidle')
        page.get_by_role('button',name='人工试调',exact=True).click()
        expect(page.locator('#parameter-SM')).to_have_value('50')
        expect(page.locator('.curve-chip')).to_have_count(3)
        check('adoption-uses-completed-result-and-session-retains-uncomputed-draft')

        result=success[-1]
        page.get_by_role('button',name='状态分布',exact=True).click()
        expect(page.get_by_label('状态所属过程')).to_have_value(next(iter(result['hydrographs'])))
        assert result['operationId'] in page.url
        check('state-entry-retains-selected-record-and-flood')
        assert not report['errors'],report['errors']
        report['status']='passed'
    except Exception as failure:
        report['failure']=str(failure)
        page.screenshot(path=str(out/'failure.png'),full_page=True)
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
