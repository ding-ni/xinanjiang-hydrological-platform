"""Exercise real control/interior routing, reuse and continuation in isolation."""
import argparse
import json
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:18894')
parser.add_argument('--output',required=True)
args=parser.parse_args()
output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
report={'status':'failed','checks':[],'pageErrors':[]}
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':900})
    page=context.new_page();page.on('pageerror',lambda failure:report['pageErrors'].append(str(failure)))
    def api(path,body=None):
        target=args.url+'/api/model/'+path
        response=context.request.get(target) if body is None else context.request.post(target,data=body)
        assert response.ok,(path,response.text())
        return response.json()
    def finish(identifier):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            status=api('tasks/'+identifier)
            if status['status'] in {'completed','archived'}:return api('tasks/'+identifier+'/result')
            assert status['status'] not in {'failed','cancelled'},status
            page.wait_for_timeout(300)
        raise AssertionError('Task timeout '+identifier)
    def check(name):
        report['checks'].append(name);print('PASS',name,flush=True)
    def open_query(identifier):
        page.goto(args.url+'/?river-check='+str(time.time_ns())+'#/diagnostics?source='+identifier+'&label=河道查询验收');page.wait_for_load_state('networkidle')
        expect(page.locator('.diagnostic-view form select:visible').first).to_have_value(identifier)
        expect(page.locator('.query-only .topology-map canvas')).to_be_visible(timeout=45000)
        summary=page.locator('summary').filter(has_text='进阶：坡面估算与位置设置')
        if summary.locator('..').get_attribute('open') is None:summary.click()
    def set_point(point):
        fields=page.locator('.diagnostic-view input[type=number]:visible')
        fields.nth(0).fill(str(point['lon']));fields.nth(1).fill(str(point['lat']));fields.nth(1).press('Tab')
    def query(point):
        set_point(point)
        previous={row['taskId'] for row in api('tasks')['tasks']}
        page.get_by_role('button',name='查询河道流量',exact=True).click()
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            row=next((row for row in api('tasks')['tasks'] if row['taskId'] not in previous and row['runType']=='pointQuery'),None)
            if row:
                result=finish(row['taskId'])
                expect(page.locator('.diagnostic-view .result canvas').first).to_be_visible(timeout=30000)
                return row,result
            page.wait_for_timeout(250)
        raise AssertionError(page.locator('.diagnostic-view').inner_text())
    try:
        model_context={'contextKey':'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km','projectId':'nananxi-xufan','resolutionId':'1km','modelConfigId':'nananxi-xufan-1km'}
        case=api('catalog')['cases']['nananxi-xufan:1km']
        request={'schemaVersion':'2.0','runType':'simulation','requestId':'RIVER-'+uuid.uuid4().hex,
            'context':model_context,'period':{'startTime':'2017-06-19T11:00','endTime':'2017-06-20T11:00','warmupDays':1},
            'parameters':case['parameters'],'inputs':{'evaporation':{'mode':'monthly-climatology-assumption'}},'lineage':{}}
        source=api('simulation-tasks',request);saved=finish(source['taskId'])
        spatial=api('spatial-products?bundleVersionId='+saved['lineage']['inputBundleVersionId'])
        control=spatial['control']['snapped'];interior={'lon':119.45537819524547,'lat':30.143731102817043}
        open_query(source['taskId'])
        page.locator('.query-only .topology-map canvas').click(position={'x':200,'y':200})
        expect(page.locator('.point-coordinate')).to_be_visible()
        _task,at_control=query(control)
        assert at_control['pointQuery']['method']=='PERSISTED_CONTROL_HYDROGRAPH'
        assert at_control['pointQuery']['series']['flow_cms']==[row['value'] for row in saved['hydrograph']['forecast']]
        check('Map selection and the located control section read the original saved discharge exactly')
        count=len(api('tasks')['tasks'])
        page.get_by_role('button',name='查询河道流量',exact=True).click()
        expect(page.get_by_text('已读取同一成果的点位流量。',exact=True)).to_be_visible()
        assert len(api('tasks')['tasks'])==count
        check('The same point reuses its saved result without creating a new task')
        period=page.locator('.diagnostic-view input[type=datetime-local]')
        period.first.fill('2017-06-19T10:00');period.first.press('Tab')
        expect(page.get_by_role('button',name='模拟所选时段',exact=True)).to_be_visible()
        page.get_by_role('button',name='查询河道流量',exact=True).click()
        expect(page.locator('.diagnostic-view>.error')).to_contain_text('查询时段须落在来源模拟覆盖内')
        assert len(api('tasks')['tasks'])==count
        check('Uncovered hours offer a simulation entry without creating an out-of-period query')
        period.first.fill('2017-06-19T11:00');period.first.press('Tab')
        _interior_task,at_interior=query(interior)
        assert at_interior['pointQuery']['quality']['status']=='RERUN_COMPLETED'
        assert at_interior['pointQuery']['topology']['topology_action']=='TEMPORARY_CONTROL_NODE_SPLIT'
        assert at_interior['pointQuery']['location']['distanceM']<=300
        check('The river interior executes topology splitting and reports the actual section')
        page.screenshot(path=str(output/'interior.png'),full_page=True)
        continued_request=json.loads(json.dumps(source['runConfig']))
        continued_request.update(runType='stateContinuation',requestId='RIVER-CONT-'+uuid.uuid4().hex,lineage={})
        continued_request.pop('scheme',None)
        continued_request['period']={'startTime':'2017-06-20T12:00','endTime':'2017-06-20T14:00','warmupDays':0,'initialStateVersionId':saved['endStateVersionId']}
        continued=api('tasks',continued_request);continued_result=finish(continued['taskId'])
        open_query(continued['taskId'])
        _continuation_control_task,continued_control=query(control)
        assert continued_control['pointQuery']['method']=='PERSISTED_CONTROL_HYDROGRAPH'
        assert continued_control['pointQuery']['series']['flow_cms']==[row['value'] for row in continued_result['hydrograph']['forecast']]
        check('Continuation control flow remains queryable from its saved hydrograph')
        count=len(api('tasks')['tasks']);set_point(interior)
        page.get_by_role('button',name='查询河道流量',exact=True).click()
        expect(page.locator('.diagnostic-view>.error')).to_contain_text('河道中间断面需独立复算',timeout=45000)
        expect(page.get_by_role('button',name='按此条件独立复算',exact=True)).to_be_visible()
        assert len(api('tasks')['tasks'])==count
        check('Continuation interior query offers independent simulation without resetting saved routing state')
        assert not report['pageErrors'],report['pageErrors']
        report.update(status='passed',sourceTaskId=source['taskId'],continuationTaskId=continued['taskId'])
    finally:
        if report['status']!='passed':print(page.locator('body').inner_text(),flush=True)
        page.screenshot(path=str(output/'last-state.png'),full_page=True)
        output.joinpath('acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
