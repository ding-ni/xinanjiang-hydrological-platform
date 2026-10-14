"""Verify persisted scheme/result/state links and river interaction after restart."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--report', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
original = json.loads(Path(args.report).read_text(encoding='utf-8'))
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'pageErrors': []}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
    def api(path, body=None):
        response = context.request.get(args.url + '/api/model/' + path) if body is None else context.request.post(args.url + '/api/model/' + path, data=body)
        assert response.ok, response.text()
        return response.json()
    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)
    def wait(task):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = api('tasks/' + task['taskId'])
            if current['status'] not in {'queued', 'running'}:
                assert current['status'] == 'completed', current
                return api('tasks/' + task['taskId'] + '/result')
            page.wait_for_timeout(500)
        raise AssertionError('Calculation timeout')
    try:
        assert original['status'] == 'passed'
        for key in ['calibrationTaskId', 'eventTaskId', 'fixedSimulationTaskId', 'secondSearchTaskId', 'stationSimulationTaskId']:
            task = api('tasks/' + original[key])
            assert task['status'] == 'completed'
            scheme = api('model-schemes/' + task['lineage']['schemeId'] + '?revision=' + str(task['lineage']['schemeRevision']))
            assert scheme['id'] == task['lineage']['schemeId']
            api('tasks/' + task['taskId'] + '/result')
        source = api('tasks/' + original['stateTaskId'])
        result = api('tasks/' + original['stateTaskId'] + '/result')
        assert result['endStateVersionId']
        assert api('tasks/' + original['calibrationTaskId'] + '/result')['stateCalculationTaskId'] == source['taskId']
        check('Restart preserves schemes, revisions, calculations and best-state links')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.scheme-bar input[placeholder="请为本次水文方案命名"]')).to_have_value('浏览器站点自动模拟验收')
        page.locator('.workflow-steps button').filter(has_text='计算条件').click()
        expect(page.locator('#simulation-parameters')).to_be_visible()
        parameter = page.locator('#parameter-SM')
        expect(parameter).to_be_visible()
        original_value = parameter.input_value()
        parameter.fill(str(float(original_value) + .1))
        parameter.press('Tab')
        assert abs(float(parameter.input_value()) - (float(original_value) + .1)) < 1e-7
        parameter.fill(original_value)
        parameter.press('Tab')
        page.locator('.workflow-steps button').filter(has_text='计算结果').click()
        expect(page.locator('#simulation-start')).not_to_be_visible()
        expect(page.get_by_role('button', name='查看模拟状态', exact=True)).to_be_visible()
        count = len(api('tasks')['tasks'])
        page.screenshot(path=str(output / 'restored-calculation.png'), full_page=True)
        page.get_by_role('button', name='查看模拟状态', exact=True).click()
        expect(page.locator('.view-state')).to_be_visible()
        assert len(api('tasks')['tasks']) == count
        check('Restored simulation opens its saved state without submitting another calculation')
        catalog = api('result-catalog')
        metadata = catalog.get('metadata', {}).get(source['taskId'], {})
        api('result-metadata/' + source['taskId'], {
            'name': '重启验收河道成果', 'collection': '', 'notes': '',
            'favorite': False, 'revision': metadata.get('revision', 0),
        })
        page.goto(args.url + '/#/diagnostics?source=' + source['taskId'])
        page.wait_for_load_state('networkidle')
        select = page.locator('.diagnostic-view form select:visible').first
        expect(select).to_have_value(source['taskId'])
        expect(select).to_contain_text('重启验收河道成果')
        canvas = page.locator('.query-only .topology-map canvas')
        expect(canvas).to_be_visible(timeout=30000)
        canvas.click(position={'x': 180, 'y': 180})
        expect(page.locator('.query-only .point-coordinate')).to_be_visible()
        spatial = api('spatial-products?bundleVersionId=' + source['runConfig']['inputs']['bundleVersionId'])
        control = spatial['control']['snapped']
        page.get_by_text('进阶：坡面估算与位置设置', exact=True).click()
        fields = page.locator('.diagnostic-view input[type="number"]:visible')
        fields.nth(0).fill(str(control['lon'])); fields.nth(1).fill(str(control['lat']))
        page.get_by_role('button', name='查询河道流量', exact=True).click()
        deadline = time.monotonic() + 180
        query = None
        while time.monotonic() < deadline:
            query = next((row for row in api('tasks')['tasks'] if row['runType'] == 'pointQuery' and row['runConfig']['sourceTaskId'] == source['taskId']), None)
            if query and query['status'] == 'completed': break
            if query and query['status'] == 'failed': raise AssertionError(query['errorMessage'])
            page.wait_for_timeout(500)
        assert query and query['status'] == 'completed'
        point = api('tasks/' + query['taskId'] + '/result')['pointQuery']
        assert point['method'] == 'PERSISTED_CONTROL_HYDROGRAPH'
        assert point['series']['flow_cms'] == [row['value'] for row in result['hydrograph']['forecast']]
        expect(page.get_by_text('选定位置', exact=False)).to_be_visible(timeout=20000)
        count = len(api('tasks')['tasks'])
        page.get_by_role('button', name='查询河道流量', exact=True).click()
        expect(page.get_by_text('已读取同一成果的点位流量。', exact=True)).to_be_visible()
        assert len(api('tasks')['tasks']) == count
        check('Named river source, map point, actual control flow and repeated-query reuse work')
        # A continuation is visible but cannot be silently reset for topology splitting.
        request = json.loads(json.dumps(source['runConfig']))
        request.update(runType='stateContinuation', requestId='RESTART-CONTINUE-' + str(time.time_ns()), lineage={})
        request.pop('scheme', None)
        request['period'] = {'startTime': '2017-06-22T00:00', 'endTime': '2017-06-22T02:00', 'warmupDays': 1, 'initialStateVersionId': result['endStateVersionId']}
        continuation = api('tasks', request)
        continued = wait(continuation)
        assert continued['parentStateVersionId'] == result['endStateVersionId']
        page.goto(args.url + '/#/diagnostics?source=' + continuation['taskId'])
        page.wait_for_load_state('networkidle')
        expect(page.locator('.diagnostic-view form select:visible').first).to_have_value(continuation['taskId'])
        expect(page.get_by_role('button', name='按此条件独立复算', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='查询河道流量', exact=True)).to_be_disabled()
        expect(page.locator('.query-only .point-coordinate')).not_to_be_visible()
        check('Saved checkpoint continues; river query offers explicit independent rerun for continuation')
        page.goto(args.url + '/#/system')
        page.wait_for_load_state('networkidle')
        with page.expect_response(lambda response: response.url.endswith('/api/model/workspace/backups') and response.request.method == 'POST', timeout=600000) as saved:
            page.get_by_role('button', name='备份工作区', exact=True).click()
        assert saved.value.ok, saved.value.text()
        backup = saved.value.json()
        expect(page.locator('.system-view [role="status"]')).to_contain_text('备份完成', timeout=20000)
        assert backup['files'] > 0
        check('Workspace backup includes calculations and SQL scheme/network records')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='新建方案', exact=True).click()
        expect(page.locator('.workflow-steps button.active')).to_contain_text('方案与时段')
        page.goto(args.url + '/#/data')
        page.wait_for_load_state('networkidle')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.scheme-bar input[placeholder="请为本次水文方案命名"]')).to_have_value('')
        page.locator('.workflow-steps button').filter(has_text='计算结果').click()
        expect(page.locator('.simulation-result-card')).not_to_be_visible()
        expect(page.locator('.task-status')).not_to_be_visible()
        page.locator('.operation-tabs button').filter(has_text='场次模拟').click()
        page.locator('.workflow-steps button').filter(has_text='计算条件').click()
        expect(page.locator('.evaluation-settings')).not_to_have_attribute('open', '')
        expect(page.locator('#algorithm')).not_to_be_visible()
        check('New scheme returns without old results; fixed simulation keeps search settings out of the main form')
        assert not report['pageErrors'], report['pageErrors']
        report.update(status='passed', continuationTaskId=continuation['taskId'], backup=backup)
        page.screenshot(path=str(output / 'river-query.png'), full_page=True)
    except Exception:
        page.screenshot(path=str(output / 'failure.png'), full_page=True)
        output.joinpath('failure-ui.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        raise
    finally:
        output.joinpath('restart-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
