"""Last explicit daily parameters and historical/running calculations are separate."""
import argparse
import json
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:18894')
parser.add_argument('--output',required=True)
args=parser.parse_args();output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
report={'status':'failed','checks':[],'pageErrors':[]}
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':900})
    page=context.new_page();page.on('pageerror',lambda failure:report['pageErrors'].append(str(failure)))
    def api(path,body=None):
        response=context.request.get(args.url+'/api/model/'+path) if body is None else context.request.post(args.url+'/api/model/'+path,data=body)
        assert response.ok,(path,response.text());return response.json()
    def go(path):page.goto(args.url+'/#/'+path);page.wait_for_load_state('networkidle')
    def check(name):report['checks'].append(name);print('PASS',name,flush=True)
    try:
        rows=api('operations')['operations']
        original=next(row for row in rows if row['status']=='completed' and row['operation']=='hindcast')
        first=next(row for row in api('parameter-schemes')['schemes'] if row['id']==original['runConfig']['parameterSchemeId'])
        second=api('parameter-schemes',{'name':'跨页参数选择验收','sourceTaskId':first['sourceTaskId']})
        go('forecast?operation='+original['operationId'])
        expect(page.locator('#forecast-parameter')).to_have_value(first['id'])
        expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
        go('simulation');page.locator('#simulation-parameter').select_option(second['id'])
        go('forecast')
        expect(page.locator('#forecast-parameter')).to_have_value(second['id'])
        expect(page.locator('.daily-result canvas')).to_have_count(0)
        check('Ordinary page navigation keeps the last explicit parameter and hides the previous incompatible process')
        go('forecast?operation='+original['operationId'])
        expect(page.locator('#forecast-parameter')).to_have_value(first['id'])
        expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
        go('simulation')
        expect(page.locator('#simulation-parameter')).to_have_value(second['id'])
        check('Opening historical parameters restores their result without replacing the staff selection')
        submitted=api('operations',{'operation':'continuousSimulation','requestId':'PARAM-BROWSER-'+uuid.uuid4().hex,
            'parameterSchemeId':first['id'],'context':first['context'],'period':{'startTime':'2017-06-19T11:00','endTime':'2017-06-20T11:00'}})
        key='hydrology.daily.'+first['contextKey']+'.simulation'
        page.evaluate('(data)=>{const saved=JSON.parse(localStorage.getItem(data.key)||"{}");localStorage.setItem(data.key,JSON.stringify({...saved,parameterSchemeId:data.id,operationId:data.operation}));}',{'key':key,'id':second['id'],'operation':submitted['operationId']})
        go('simulation')
        expect(page.locator('#simulation-parameter')).to_have_value(first['id'])
        if api('operations/'+submitted['operationId'])['status'] in {'queued','running'}:
            expect(page.locator('#simulation-parameter')).to_be_disabled()
        check('An active calculation displays its frozen parameter without accepting a change')
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            current=api('operations/'+submitted['operationId'])
            if current['status']=='completed':break
            assert current['status'] not in {'failed','cancelled'},current
            page.wait_for_timeout(300)
        assert current['status']=='completed'
        assert not report['pageErrors'],report['pageErrors']
        report['status']='passed'
    finally:
        if report['status']!='passed':print(page.locator('body').inner_text(),flush=True)
        page.screenshot(path=str(output/'last-state.png'),full_page=True)
        output.joinpath('acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
