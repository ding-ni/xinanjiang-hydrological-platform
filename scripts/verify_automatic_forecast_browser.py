"""Real auto forecast UI acceptance. Use only an isolated staff acceptance server."""
import argparse
import json
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'checks': [], 'pageErrors': [], 'status': 'failed'}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width':1366,'height':768})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
    def api(path, body=None):
        target = args.url+'/api/model/'+path
        response = context.request.get(target) if body is None else context.request.post(target, data=body)
        assert response.ok, (path,response.status,response.text())
        return response.json()
    def check(name):
        report['checks'].append(name)
        print('PASS',name,flush=True)
    def finish(task):
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            row = api('tasks/'+task['taskId'])
            if row['status']=='completed': return api('tasks/'+task['taskId']+'/result')
            assert row['status'] not in {'failed','cancelled'}, row
            page.wait_for_timeout(300)
        raise AssertionError('Seed calculation timed out')
    def layout(label):
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
        page.screenshot(path=str(output/(label+'.png')),full_page=True)
    try:
        case = api('catalog')['cases']['nananxi-xufan:1km']
        scope = {'contextKey':'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km',
                 'projectId':'nananxi-xufan','resolutionId':'1km','modelConfigId':'nananxi-xufan-1km'}
        seed = api('simulation-tasks', {'schemaVersion':'2.0','requestId':'AUTO-BROWSER-SEED-'+uuid.uuid4().hex,
            'runType':'simulation','context':scope,'period':{'startTime':'2017-06-19T11:00','endTime':'2017-06-19T12:00','warmupDays':1},
            'parameters':case['parameters'],'inputs':{'evaporation':{'mode':'monthly-climatology-assumption'}},'lineage':{}})
        finish(seed)
        parameter = api('parameter-schemes', {'name':'自动预报验收 · 直接采用','sourceTaskId':seed['taskId']})
        page.goto(args.url+'/#/forecast');page.wait_for_load_state('networkidle')
        (output/'initial-ui.txt').write_text(page.locator('body').inner_text(),encoding='utf-8')
        expect(page.get_by_role('tab',name='自动预报',exact=True)).to_have_attribute('aria-selected','true')
        expect(page.locator('#auto-parameter')).to_have_value(parameter['id'])
        expect(page.locator('#auto-interval')).to_have_value('1')
        expect(page.locator('#auto-lead')).to_have_value('24')
        assert not page.get_by_text('审核通过',exact=True).count()
        check('Default automatic tab, saved parameter, 1h interval / independent 24h horizon, no release gate')
        product = {'name':'浏览器逐小时产品','contextKey':scope['contextKey'],'sourceId':'browser-weather',
            'sourceLabel':'验收情景 · 全域均匀雨量','issueTime':'2017-06-19T11:00:00','unit':'mm',
            'timeConvention':'hour-ending','hourlyRainfallMm':[1,2,3]}
        page.locator('.source-details summary').click()
        page.locator('input[type=file]').set_input_files({'name':'product.json','mimeType':'application/json','buffer':json.dumps(product).encode()})
        expect(page.locator('#auto-source')).to_have_value('browser-weather')
        page.locator('.source-details summary').click()
        expect(page.get_by_role('button',name='格式示例',exact=True)).not_to_be_visible()
        # A true single forecast must not enable any recurring schedule.
        page.get_by_role('tab',name='手动预报',exact=True).click()
        expect(page.locator('#auto-interval')).to_have_count(0)
        page.locator('#auto-first').fill('2017-06-19T11:00')
        page.locator('#auto-lead').fill('2');page.locator('#auto-lead').press('Tab')
        with page.expect_response(lambda r:r.url.endswith('/operations') and r.request.method=='POST') as manual_submitted:
            page.get_by_role('button',name='开始预报',exact=True).click()
        manual_task=manual_submitted.value.json()
        assert manual_task['operation']=='manualForecast',manual_task
        expect(page.locator('.daily-result canvas')).to_be_visible(timeout=180000)
        assert not api('automatic-forecasts')['schedules']
        assert api('operations/'+manual_task['taskId']+'/result')['issue']['mode']=='manual'
        layout('manual-1366x768')
        check('Manual product-driven forecast runs once; no automatic schedule or interval input')
        page.get_by_role('tab',name='自动预报',exact=True).click()
        page.locator('#auto-first').fill('2017-06-19T11:00')
        page.locator('#auto-lead').fill('2');page.locator('#auto-lead').press('Tab')
        with page.expect_response(lambda r:r.url.endswith('/automatic-forecasts') and r.request.method=='POST') as submitted:
            page.get_by_role('button',name='自动开始',exact=True).click()
        assert submitted.value.status==202,submitted.value.text()
        schedule = submitted.value.json();report['scheduleId']=schedule['id']
        page.close()
        # No browser page remains to run a JS interval. Server owns both cycles.
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            row=api('automatic-forecasts/'+schedule['id'])
            if row['completedCount']>=2:break
            if row.get('issues'):raise AssertionError(row)
            if row.get('retryAfter'):raise AssertionError(row)
            time.sleep(.5)
        else: raise AssertionError(row)
        check('Two real forecasts completed after the browser page closed')
        records=api('automatic-forecasts/'+schedule['id']+'/issues')['issues']
        assert len(records)==2
        newest=api('operations/'+records[0]['operationId']+'/result')
        earlier=api('operations/'+records[1]['operationId']+'/result')
        assert newest['gridCount']==110 and len(newest['hydrograph']['forecast'])==2
        observation=api('tasks/'+newest['observationTaskId']+'/result')
        assert observation['parentStateVersionId']==earlier['observationStateVersionId']
        assert newest['endStateVersionId'] is None
        check('110 basin grids; next issue continues observed state, never forecast end state')
        page=context.new_page();page.on('pageerror',lambda error:report['pageErrors'].append(str(error)))
        page.goto(args.url+'/#/forecast');page.wait_for_load_state('networkidle')
        expect(page.locator('#auto-record option')).to_have_count(2,timeout=30000)
        expect(page.locator('.daily-result canvas')).to_be_visible(timeout=30000)
        expect(page.get_by_text('预报洪峰 · m³/s',exact=True)).to_be_visible()
        expect(page.locator('.schedule-state')).to_contain_text('等待降雨产品',timeout=30000)
        layout('automatic-1366x768')
        page.set_viewport_size({'width':1920,'height':1080});layout('automatic-1920x1080')
        check('Both viewports fit controls, chart, issue record and waiting reason without horizontal overflow')
        assert page.locator('.source-details').get_attribute('open') is None
        expect(page.get_by_role('button',name='修改名称',exact=True)).not_to_be_visible()
        check('Technical product details and result management are collapsed by default')
        page.get_by_role('button',name='关闭自动预报',exact=True).click()
        expect(page.locator('.schedule-state')).to_contain_text('已关闭',timeout=15000)
        expect(page.locator('.catch-up')).to_contain_text('补做 2 次')
        assert api('automatic-forecasts/'+schedule['id'])['enabled'] is False
        page.get_by_role('button',name='自动开始',exact=True).click()
        expect(page.get_by_role('button',name='关闭自动预报',exact=True)).to_be_visible()
        assert len(api('automatic-forecasts')['schedules'])==1
        assert api('automatic-forecasts/'+schedule['id'])['nextIssueTime']=='2017-06-19T13:00:00'
        page.get_by_role('button',name='关闭自动预报',exact=True).click()
        expect(page.locator('.schedule-state')).to_contain_text('已关闭')
        # The acceptance server freezes the scheduler clock at 14:00. Supply
        # archived 13:00 rain: exactly 13 and 14 remain, then the batch must stop.
        api('forecast-rain',{**product,'issueTime':'2017-06-19T13:00:00','hourlyRainfallMm':[4,5,6]})
        page.get_by_role('button',name='补做预报',exact=True).click()
        expect(page.locator('.schedule-state')).to_contain_text('正在补做',timeout=15000)
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            row=api('automatic-forecasts/'+schedule['id'])
            if not row['enabled'] and row['completedCount']==4:break
            assert not row.get('retryAfter'), {k:row.get(k) for k in ('status','message','issues')}
            page.wait_for_timeout(500)
        else:raise AssertionError({k:row.get(k) for k in ('enabled','status','completedCount','message','issues')})
        expect(page.locator('.schedule-state')).to_contain_text('补做完成',timeout=240000)
        row=api('automatic-forecasts/'+schedule['id'])
        assert row['enabled'] is False and row['completedCount']==4,row
        assert row['nextIssueTime']=='2017-06-19T15:00:00' and row['catchUpUntil']=='2017-06-19T14:00:00',row
        all_records=api('automatic-forecasts/'+schedule['id']+'/issues')['issues']
        assert len(all_records)==len({r['issueTime'] for r in all_records})==4
        expect(page.get_by_role('button',name='补做预报',exact=True)).to_be_disabled()
        catch_result=api('operations/'+all_records[1]['operationId']+'/result')
        catch_observed=api('tasks/'+catch_result['observationTaskId']+'/result')
        assert catch_observed['parentStateVersionId']==newest['observationStateVersionId']
        check('Start resumes same schedule; finite catch-up fills two gaps with original observed state, no duplicates')
        layout('catch-up-1920x1080')
        page.get_by_role('tab',name='历史回算',exact=True).click()
        expect(page.locator('#forecast-parameter')).to_be_visible()
        check('Historical hindcast remains separate and usable')
        assert not report['pageErrors'],report['pageErrors']
        report['status']='passed'
    except Exception as error:
        report['failure']=str(error)[:5000]
        if not page.is_closed():
            page.screenshot(path=str(output/'failure.png'),full_page=True)
            (output/'failure-ui.txt').write_text(page.locator('body').inner_text(),encoding='utf-8')
        raise
    finally:
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
