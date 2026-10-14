"""128-hour historical grid-rainfall hindcast in an isolated local workspace."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:18894')
parser.add_argument('--output',required=True)
args=parser.parse_args()
output=Path(args.output).resolve()
output.mkdir(parents=True,exist_ok=True)
report={'status':'failed','checks':[],'pageErrors':[]}
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':900})
    page=context.new_page()
    page.on('pageerror',lambda failure:report['pageErrors'].append(str(failure)))
    def api(path,body=None):
        target=args.url+'/api/model/'+path
        response=context.request.get(target) if body is None else context.request.post(target,data=body)
        assert response.ok,(path,response.text())
        return response.json()
    def check(name):
        report['checks'].append(name)
        print('PASS',name,flush=True)
    defaults=api('preparation-defaults')
    try:
        api('preparation-defaults',{'rainfallMethod':'stationIdw','stationSourceId':'LOCAL_OBSERVATIONS'})
        parameter=next(row for row in api('parameter-schemes')['schemes'] if row['name']=='浏览器水文作业参数')
        page.goto(args.url+'/#/forecast');page.wait_for_load_state('networkidle')
        page.locator('#forecast-parameter').select_option(parameter['id'])
        page.locator('#forecast-issue-time').fill('2024-07-01T00:00')
        page.locator('#forecast-end-time').fill('2024-07-06T08:00')
        before={row['operationId'] for row in api('operations')['operations']}
        page.get_by_role('button',name='开始回算',exact=True).click()
        deadline=time.monotonic()+240
        operation=None
        while time.monotonic()<deadline:
            operation=next((row for row in api('operations')['operations'] if row['operationId'] not in before),None)
            if operation and operation['status']=='completed':break
            if operation:assert operation['status'] not in {'failed','cancelled'},operation
            page.wait_for_timeout(400)
        assert operation and operation['status']=='completed',operation
        result=api('operations/'+operation['operationId']+'/result')
        graph=result['issues'][0]['hydrograph']
        assert len(graph['forecast'])==128
        assert graph['forecast'][0]['time']=='2024-07-01T01:00:00'
        assert graph['forecast'][-1]['time']=='2024-07-06T08:00:00'
        assert len(graph['forcing']['precipitation'])==128
        assert any(value>0 for value in graph['forcing']['precipitation'])
        assert len(graph['obs'])==128
        check('128-hour historical hindcast automatically prepares local station rainfall and evaluates observed flow')
        page.reload();page.wait_for_load_state('networkidle')
        expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
        assert page.locator('.forecast-view').bounding_box()['x']+page.locator('.forecast-view').bounding_box()['width']<=1367
        page.screenshot(path=str(output/'hindcast-1366.png'),full_page=True)
        check('Result and controls fit the 1366 pixel staff workstation')
        page.set_viewport_size({'width':900,'height':900})
        page.wait_for_timeout(700)
        box=page.locator('.forecast-view').bounding_box()
        assert box['x']+box['width']<=901,box
        page.screenshot(path=str(output/'hindcast-900.png'),full_page=True)
        check('900 pixel layout keeps complete controls and a readable process chart')
        page.locator('#forecast-parameter').select_option(parameter['id'])
        page.locator('#forecast-end-time').fill('2024-07-06T07:00')
        page.locator('#forecast-end-time').press('Tab')
        expect(page.locator('.daily-result canvas')).to_have_count(0)
        assert api('operations/'+operation['operationId'])['status']=='completed'
        check('Edited conditions clear the current chart and leave the saved calculation intact')
        assert not report['pageErrors'],report['pageErrors']
        report['operationId']=operation['operationId'];report['status']='passed'
    finally:
        api('preparation-defaults',{key:defaults[key] for key in ['rainfallMethod','stationSourceId','sourceBundleVersionId','idwPower','minimumStations'] if key in defaults})
        output.joinpath('acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
