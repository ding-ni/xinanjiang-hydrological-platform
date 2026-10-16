"""Real local UI acceptance, isolated from the user's active workspace."""
import argparse
import copy
import json
import sys
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:18902')
parser.add_argument('--source-scheme',default='SCHEME-e96c143612da42bf865ee557f303aa2e',help='Existing continuous colleague scheme in the isolated acceptance workspace')
parser.add_argument('--output',required=True)
args=parser.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
report={'checks':[],'errors':[],'timings':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':768})
    page=context.new_page()
    page.on('pageerror',lambda error:report['errors'].append(str(error)))
    def api(path,body=None):
        response=context.request.post(args.url+'/api/model/'+path,data=body,timeout=60000) if body is not None else context.request.get(args.url+'/api/model/'+path,timeout=60000)
        assert response.ok,response.text()
        return response.json()
    def check(name):
        report['checks'].append(name);print('PASS',name,flush=True)
    def wait_operation(identifier):
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            row=api('operations/'+identifier)
            if row['status'] not in ('queued','running','cancelling'):
                assert row['status']=='completed',row
                return api('operations/'+identifier+'/result')
            page.wait_for_timeout(300)
        raise AssertionError('operation timed out')
    try:
        # Small real continuous calculation; retained evaluation purposes and origin.
        source=api('model-schemes/'+args.source_scheme)
        request=copy.deepcopy(source['calculationDraft'])
        defaults=api('database/routing-defaults')
        request.pop('scheme',None);request.pop('sourcePlan',None);request['lineage']={}
        request['requestId']='ACCEPT-'+uuid.uuid4().hex
        request['routing']={'versionId':defaults['routingVersionId']}
        request['period']['warmupDays']=1
        request['calibration'].update(statePolicy='continuous-origin',continuousOrigin='2017-06-18T00:00',target='runoff',searchParameters={'SM':{'minimum':8,'maximum':60}},routingSearchParameters={})
        request['calibration'].pop('routingSearchParameters',None);request['calibration'].pop('jointProfile',None)
        request['calibration']['optimizer'].update(maxEvaluations=4,populationSize=4,strategy='joint')
        definition=copy.deepcopy(source['definition']);definition['period']=request['period'];definition['continuousOrigin']='2017-06-18T00:00'
        request['inputs'].update(bundleVersionId='HISTORICAL_SPATIAL_V1',sourceBundleVersionId='HISTORICAL_SPATIAL_V1',rainfallMethod='stationIdw',dataMode='historical-database',stationSourceId='LOCAL_OBSERVATIONS',stationNetwork=api('database/network'))
        definition['inputs']={key:request['inputs'][key] for key in ('bundleVersionId','rainfallMethod','dataMode','stationSourceId','stationNetwork','idwPower','minimumStations')}
        saved=api('model-schemes',{'name':'试调分离验收 '+time.strftime('%H%M%S'),'definition':definition,'calculationDraft':request,'editor':{}})
        report['schemeId']=saved['id']
        page.goto(args.url+'/#/calibration?schemeId='+saved['id']);page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button',name='人工试调',exact=True)).to_be_enabled(timeout=30000)
        page.get_by_role('button',name='人工试调',exact=True).click()
        expect(page.get_by_role('button',name='试算当前洪水',exact=True)).to_be_enabled(timeout=30000)
        before=api('model-schemes/'+saved['id'])
        results=[]
        for sm in ('20','30','40'):
            field=page.locator('#parameter-SM');field.fill(sm);field.press('Tab')
            start=time.monotonic()
            with page.expect_response(lambda r:r.url.endswith('/api/model/operations') and r.request.method=='POST') as pending:
                page.get_by_role('button',name='试算当前洪水',exact=True).click()
            assert pending.value.ok,pending.value.text()
            result=wait_operation(pending.value.json()['taskId']);results.append(result)
            expect(page.get_by_role('button',name='试算当前洪水',exact=True)).to_be_enabled(timeout=30000)
            report['timings'].append({'kind':'current','SM':sm,'seconds':round(time.monotonic()-start,3),'stages':result.get('timings')})
            assert result['bestParameters']['SM']==float(sm),result['bestParameters']
            assert list(result['hydrographs'])==['CALIBRATION'],list(result['hydrographs'])
            after=api('model-schemes/'+saved['id'])
            assert before['revision']==after['revision'] and before['calculationDraft']==after['calculationDraft']
        assert len(page.locator('.curve-entry').all())==2
        expect(page.locator('.curve-controls')).to_contain_text('人工试调 2')
        expect(page.locator('.curve-controls')).to_contain_text('人工试调 3')
        assert '计算条件不同' not in page.locator('.curve-controls').inner_text()
        check('three-current-trials-retain-two-curves-and-never-change-saved-scheme')
        page.locator('#parameter-WM').fill('20');page.locator('#parameter-WM').press('Tab')
        expect(page.get_by_role('button',name='试算当前洪水',exact=True)).to_be_disabled()
        assert len(page.locator('.curve-entry').all())==2
        page.get_by_role('button',name='恢复本次载入参数',exact=True).click()
        check('invalid-edit-does-not-clear-successful-curves')
        page.get_by_role('button',name='保存参数',exact=True).click()
        page.locator('#parameter-name').fill('自定义试调参数 '+time.strftime('%H%M%S'))
        page.locator('.el-dialog').filter(has=page.locator('#parameter-name')).get_by_role('button',name='保存',exact=True).click()
        expect(page.locator('#parameter-name')).not_to_be_visible(timeout=30000)
        after=api('model-schemes/'+saved['id']);assert after['adoptedParameter']
        named=api('parameter-schemes/'+after['adoptedParameter']['parameter_id'])
        assert named['parameters']['SM']==40
        assert after['calculationDraft']==before['calculationDraft']
        check('named-parameters-come-from-completed-result-and-adopt-without-changing-initial-values')
        page.get_by_role('button',name='自动率定',exact=True).click()
        with page.expect_response(lambda r:r.url.endswith('/api/model/operations') and r.request.method=='POST') as pending:
            page.get_by_role('button',name='开始率定',exact=True).click()
        assert pending.value.ok,pending.value.text()
        automatic=wait_operation(pending.value.json()['taskId'])
        expect(page.get_by_role('button',name='人工试调',exact=True)).to_be_enabled(timeout=30000)
        page.get_by_role('button',name='人工试调',exact=True).click()
        expect(page.locator('.curve-controls')).to_contain_text('自动率定参考')
        assert len(page.locator('.curve-entry').all())==3
        assert '计算条件不同' not in page.locator('.curve-controls').inner_text(),page.locator('.curve-controls').inner_text()
        check('automatic-reference-and-two-manual-results-overlay-under-same-conditions')
        page.get_by_label('试调参数来源',exact=True).select_option('result:'+(automatic.get('primaryTaskId') or automatic['taskId']))
        expect(page.locator('#parameter-SM')).to_have_value(str(automatic['bestParameters']['SM']))
        check('automatic-result-can-be-explicitly-loaded-for-manual-trial')
        for width,height in ((1366,768),(1920,1080)):
            page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(500)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(out/f'calibration-{width}.png'),full_page=True)
        report['resultIds']=[row.get('operationId') for row in results]
        assert not report['errors'],report['errors']
        report['status']='passed'
    except Exception as failure:
        report['failure']=str(failure)
        page.screenshot(path=str(out/'failure.png'),full_page=True)
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
