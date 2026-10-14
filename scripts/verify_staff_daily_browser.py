"""Real staff workflow acceptance against an isolated, running platform."""
import argparse
import json
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18894')
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'checks': [], 'pageErrors': [], 'status': 'failed'}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))

    def api(path, body=None):
        target = args.url + '/api/model/' + path
        response = context.request.get(target) if body is None else context.request.post(target, data=body)
        assert response.ok, (path, response.status, response.text())
        return response.json()

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def finish(identifier, prefix='operations', timeout=240):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = api(prefix + '/' + identifier)
            if row['status'] in {'completed', 'archived'}:
                return api(prefix + '/' + identifier + '/result')
            assert row['status'] not in {'failed', 'cancelled'}, row
            page.wait_for_timeout(400)
        raise AssertionError('calculation timed out: ' + identifier)

    def go(path):
        page.goto(args.url + '/#/' + path)
        page.wait_for_load_state('networkidle')

    def fill(selector, value):
        page.locator(selector).fill(value)
        page.locator(selector).press('Tab')

    def run_ui(button, kind):
        before = {row['operationId'] for row in api('operations')['operations']}
        page.get_by_role('button', name=button, exact=True).click()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = next((item for item in api('operations')['operations'] if item['operationId'] not in before and item['operation'] == kind), None)
            if row:
                return row, finish(row['operationId'])
            page.wait_for_timeout(200)
        raise AssertionError(page.locator('body').inner_text())

    try:
        case = api('catalog')['cases']['nananxi-xufan:1km']
        seed = api('simulation-tasks', {'schemaVersion': '2.0', 'requestId': 'STAFF-SEED-' + uuid.uuid4().hex,
            'runType': 'simulation', 'context': {'contextKey': 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km',
                'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'modelConfigId': 'nananxi-xufan-1km'},
            'period': {'startTime': '2017-06-19T11:00', 'endTime': '2017-06-20T11:00', 'warmupDays': 1},
            'parameters': case['parameters'], 'inputs': {'evaporation': {'mode': 'monthly-climatology-assumption'}}, 'lineage': {}})
        seed_result = finish(seed['taskId'], 'tasks')
        parameter = api('parameter-schemes', {'name': '浏览器水文作业参数', 'sourceTaskId': seed['taskId']})
        go('simulation')
        if len(api('parameter-schemes')['schemes']) == 1:
            expect(page.locator('#simulation-parameter')).to_have_value(parameter['id'])
        else:
            expect(page.locator('#simulation-parameter')).to_have_value('')
            page.locator('#simulation-parameter').select_option(parameter['id'])
        assert page.locator('.flood-table tbody input[type=checkbox]').count() == 226
        assert page.locator('.flood-table tbody input[type=checkbox]:checked').count() == 0
        assert page.locator('.sidebar a[href="#/state"]').count() == 0
        check('Saved parameters and full flood catalogue open without rainfall or state setup')
        page.locator('.flood-table tbody tr').filter(has_text='2017-06-09 11:00').locator('input[type=checkbox]').check()
        event_operation, event_result = run_ui('开始模拟', 'eventSimulation')
        assert len(event_result['eventStates']) == 1
        assert 'xf-31170609' in event_result['hydrographs']
        event_child = api('tasks/' + event_result['eventStates'][0]['taskId'])
        assert event_child['runConfig']['runType'] == 'simulation'
        assert 'calibration' not in event_child['runConfig']
        assert 'optimizer' not in event_result
        check('An independently selected historical flood runs with saved parameters and preserves its hourly state')
        page.get_by_role('tab', name='连续时段', exact=True).click()
        fill('#simulation-start', '2017-06-19T11:00')
        fill('#simulation-end', '2017-06-20T11:00')
        simulated, process = run_ui('开始模拟', 'continuousSimulation')
        assert process['hydrograph']['forecast'] == seed_result['hydrograph']['forecast']
        page.reload(); page.wait_for_load_state('networkidle')
        expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
        assert page.locator('.conditions').inner_text().count('预热') == 0
        check('One staff click performs fixed parameter simulation; reload restores its result')
        page.locator('.daily-result').get_by_role('button', name='模拟状态', exact=True).click()
        expect(page.locator('canvas.maplibregl-canvas')).to_be_visible(timeout=60000)
        expect(page.get_by_label('状态回放时刻')).to_be_visible(timeout=30000)
        assert page.locator('body').inner_text().count('进阶初始化') == 0
        assert page.locator('.state-view').inner_text().count('参数方案') == 0
        check('Result opens saved hourly state directly, without global calibration conditions')
        page.screenshot(path=str(output / 'state.png'), full_page=True)
        page.get_by_role('button', name='继续模拟', exact=True).click()
        expect(page.locator('#simulation-end')).to_be_visible()
        assert page.locator('#simulation-parameter').count() == 0
        fill('#simulation-end', '2017-06-20T14:00')
        continued, continuation = run_ui('开始续算', 'continuation')
        assert continuation['hydrograph']['forecast'][0]['time'].startswith('2017-06-20T12:00')
        check('Continuation asks only for the end time and starts after the saved hour')
        go('archive?operation=' + simulated['operationId'])
        expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
        assert page.locator('.daily-table tbody tr').count() >= 2
        page.locator('.daily-table tbody tr').filter(has_text='连续模拟').first.get_by_role('button', name='打开条件', exact=True).click()
        expect(page.locator('#simulation-start')).to_have_value('2017-06-19T11:00')
        expect(page.locator('#simulation-end')).to_have_value('2017-06-20T11:00')
        check('Archive reopens exact simulation conditions without starting another calculation')
        go('forecast')
        page.locator('#forecast-parameter').select_option(parameter['id'])
        fill('#forecast-issue-time', '2017-06-19T11:00')
        fill('#forecast-end-time', '2017-06-20T11:00')
        single, backcast = run_ui('开始回算', 'hindcast')
        assert len(backcast['issues']) == 1
        assert page.locator('textarea').count() == 0
        assert '自动预报' not in page.locator('.forecast-view').inner_text()
        check('Historical hindcast uses one parameter and issue/end times without uniform rain entry')
        page.get_by_role('tab', name='滚动回算', exact=True).click()
        fill('#forecast-issue-time', '2017-06-19T11:00')
        fill('#forecast-last-issue', '2017-06-19T13:00')
        fill('#forecast-lead', '6')
        rolled, rolling = run_ui('开始回算', 'rollingHindcast')
        assert len(rolling['issues']) == 3
        page.reload(); page.wait_for_load_state('networkidle')
        expect(page.get_by_label('查看过程')).to_be_visible(timeout=30000)
        expect(page.get_by_label('查看过程').locator('option')).to_have_count(3)
        check('Hourly rolling hindcast keeps separate issue processes and fixed parameters')
        page.screenshot(path=str(output / 'rolling.png'), full_page=True)
        go('diagnostics?source=' + process['primaryTaskId'] + '&label=浏览器水文模拟')
        expect(page.locator('.heading h2')).to_have_text('河道流量')
        expect(page.locator('canvas.maplibregl-canvas')).to_be_visible(timeout=60000)
        assert page.locator('.tabs').count() == 0
        check('Result opens river query without routing setup tabs')
        go('realtime')
        expect(page.locator('canvas.maplibregl-canvas')).to_be_visible(timeout=60000)
        assert '当前计算降雨' not in page.locator('body').inner_text()
        assert '历史资料回放：站点读取' not in page.locator('body').inner_text()
        page.screenshot(path=str(output / 'realtime.png'), full_page=True)
        check('Rain and water page keeps map, observations, replay time and compact source detail')
        assert not report['pageErrors'], report['pageErrors']
        report['status'] = 'passed'
        report['simulation'] = simulated['operationId']
        report['eventSimulation'] = event_operation['operationId']
        report['rolling'] = rolled['operationId']
    finally:
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
