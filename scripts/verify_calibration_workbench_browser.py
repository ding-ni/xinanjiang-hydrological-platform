"""Accept the name-first calibration workbench against an isolated local server."""
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
report = {'status': 'failed', 'checks': [], 'pageErrors': []}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1050})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))

    def api(path, body=None):
        target = args.url + '/api/model/' + path
        response = context.request.get(target) if body is None else context.request.post(target, data=body)
        assert response.ok, (path, response.status, response.text())
        return response.json()

    def check(text):
        report['checks'].append(text)
        print('PASS', text, flush=True)

    def fill(selector, value):
        page.locator(selector).fill(value)
        page.locator(selector).press('Tab')

    def wait_operation(before, kind):
        deadline = time.monotonic() + 300
        identifier = None
        while time.monotonic() < deadline:
            operations = api('operations')['operations']
            row = next((item for item in operations if item['operationId'] not in before and item['operation'] == kind), None)
            if row:
                identifier = row['operationId']
                if row['status'] in {'failed', 'cancelled'}:
                    raise AssertionError({key: row.get(key) for key in ('operationId', 'status', 'errorMessage', 'issues')})
                if row['status'] == 'completed':
                    page.wait_for_timeout(1800)
                    return identifier, api('operations/' + identifier + '/result')
            if page.locator('.calculation-status [role=alert]').count():
                raise AssertionError(page.locator('.calculation-status').inner_text())
            page.wait_for_timeout(350)
        raise AssertionError(('timeout', identifier, page.locator('body').inner_text()))

    def run(button, kind):
        before = {row['operationId'] for row in api('operations')['operations']}
        page.get_by_role('button', name=button, exact=True).first.click()
        return wait_operation(before, kind)

    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button', name='新建方案', exact=True)).to_be_visible()
        assert page.locator('.workbench-toolbar').count() == 0
        page.get_by_role('button', name='新建方案', exact=True).click()
        name = '浏览器率定作业-' + uuid.uuid4().hex[:6]
        page.locator('#new-scheme-name').fill(name)
        page.get_by_role('button', name='建立方案', exact=True).click()
        expect(page.locator('.scheme-heading h1')).to_have_text(name)
        expect(page.locator('.panel-heading').filter(has_text='洪水目录')).to_contain_text('226 场')
        assert '未选洪水' in page.get_by_label('洪水用途筛选').inner_text()
        assert all(row['purpose'] == '未使用' for row in api('model-schemes/' + api('model-schemes')['schemes'][0]['id'])['editor']['events'])
        check('A name opens the complete flood catalogue with no floods preselected')

        page.get_by_role('button', name='连续时段', exact=True).click()
        for selector, value in [('#cal-start', '2017-06-19T00:00'), ('#cal-end', '2017-06-19T23:00'),
                                ('#val-start', '2017-06-20T00:00'), ('#val-end', '2017-06-20T23:00'),
                                ('#ind-start', '2017-06-21T00:00'), ('#ind-end', '2017-06-21T23:00')]:
            fill(selector, value)
        page.get_by_role('button', name='率定设置', exact=True).click()
        page.locator('#algorithm').select_option('DIFFERENTIAL_EVOLUTION')
        fill('#max-evals', '4')
        page.locator('.advanced-settings summary').click()
        fill('#population-size', '4')
        fill('#continuous-origin', '2017-06-18T00:00')
        fill('#cal-warmup', '1')
        page.get_by_role('button', name='完成', exact=True).click()
        operation, result = run('开始率定', 'calibration')
        assert result['optimizer']['evaluations'] == 4
        assert result['optimizationGroups'] == ['CALIBRATION']
        assert result['statePolicy'] == 'continuous-origin'
        assert result['eventStates']
        assert not any(row['sourceTaskId'] == result['primaryTaskId'] for row in api('parameter-schemes')['schemes'])
        expect(page.locator('.result-panel')).to_contain_text('达到评价次数上限')
        expect(page.get_by_role('button', name='保存参数', exact=True)).to_be_visible()
        check('One action prepares continuous inputs, searches four candidates, saves states and leaves the staff parameter list unchanged')

        page.get_by_role('button', name='保存参数', exact=True).click()
        fill('#parameter-name', name + '参数')
        page.locator('.parameter-dialog').get_by_role('button', name='保存', exact=True).click()
        expect(page.locator('.parameter-dialog')).not_to_be_visible()
        assert sum(row['name'] == name + '参数' for row in api('parameter-schemes')['schemes']) == 1
        check('Only the named save creates a selectable parameter set')
        saved_parameter = next(row for row in api('parameter-schemes')['schemes'] if row['name'] == name + '参数')
        retained_scheme_id = result['frozenRequest']['scheme']['id']
        page.get_by_role('button', name='进入洪水模拟 →', exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('#simulation-parameter')).to_have_value(saved_parameter['id'])
        page.go_back()
        page.wait_for_load_state('networkidle')
        page.locator('.scheme-choice[data-scheme-id="' + retained_scheme_id + '"]').click()
        expect(page.locator('.scheme-heading h1')).to_have_text(name)
        page.locator('.calculation-history').filter(has_text='历次计算').locator('summary').click()
        page.locator('.history-row').first.click()
        expect(page.get_by_role('button', name='模拟状态', exact=True)).to_be_visible()
        page.get_by_role('button', name='模拟状态', exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('.state-view')).to_be_visible()
        assert 'operation=' + operation in page.url
        expect(page.locator('.state-view .page-heading')).to_contain_text(name)
        page.go_back()
        page.wait_for_load_state('networkidle')
        page.locator('.scheme-choice[data-scheme-id="' + retained_scheme_id + '"]').click()
        expect(page.locator('.scheme-heading h1')).to_have_text(name)
        check('Saved parameters enter simulation and final state opens the matching continuous calculation')

        page.get_by_role('button', name='洪水场次', exact=True).click()
        page.get_by_role('button', name='率定设置', exact=True).click()
        if page.locator('.advanced-settings').get_attribute('open') is None:
            page.locator('.advanced-settings summary').click()
        page.locator('#cal-state-policy').select_option('independent-warmup')
        page.get_by_role('button', name='完成', exact=True).click()
        page.locator('.custom-window summary').click()
        for label, start, end, purpose in [('试调洪水甲', '2017-03-19T13:00', '2017-03-29T13:00', '率定'), ('试调洪水乙', '2017-06-09T11:00', '2017-06-15T11:00', '验证')]:
            fields = page.locator('.custom-window input')
            fields.nth(0).fill(label)
            fields.nth(1).fill(start)
            fields.nth(2).fill(end)
            page.locator('.custom-window').get_by_role('button', name='添加', exact=True).click()
            page.get_by_label('搜索洪水').fill(label)
            page.locator('.flood-row').filter(has_text=label).locator('select').select_option(purpose)
        page.get_by_label('搜索洪水').fill('试调洪水')
        page.locator('.flood-process').filter(has_text='试调洪水甲').click()
        page.get_by_role('button', name='人工试调', exact=True).click()
        previous = None
        trial_tasks = set()
        for value in ['28', '30', '32', '34']:
            fill('#parameter-SM', value)
            operation, result = run('试算', 'manualTrial')
            assert result['evaluationMode'] == 'manual'
            legends = page.locator('.figure-legend').inner_text()
            assert '本次模拟' in legends
            assert ('上次模拟' in legends) == (previous is not None), legends
            assert legends.count('上次模拟') <= 1
            previous = result['taskId']
            trial_tasks.add(result['primaryTaskId'])
        check('Four manual trials keep observed, current and one previous process')
        assert not any(row['sourceTaskId'] in trial_tasks for row in api('parameter-schemes')['schemes'])
        page.screenshot(path=str(output / 'manual-workbench.png'), full_page=True)
        scheme_id = result['frozenRequest']['scheme']['id']
        report['schemeId'] = scheme_id
        report['schemeName'] = name
        report['lastOperationId'] = operation
        page.get_by_role('button', name='← 方案列表', exact=True).click()
        page.locator('.scheme-choice[data-scheme-id="' + scheme_id + '"]').click()
        expect(page.locator('.scheme-heading h1')).to_have_text(name)
        expect(page.locator('.calculation-history').filter(has_text='历次计算')).to_contain_text('5')
        check('Returning to the scheme restores all calculation records without running again')
        assert not report['pageErrors'], report['pageErrors']
        report['status'] = 'passed'
    finally:
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['status'] != 'passed':
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            output.joinpath('failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        browser.close()
