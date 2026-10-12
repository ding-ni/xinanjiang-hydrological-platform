"""Run the operational state handoff through the UI on an isolated service."""
import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18888')
parser.add_argument('--evidence', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
evidence = json.loads(Path(args.evidence).read_text(encoding='utf-8'))
report = {'status': 'failed', 'checks': [], 'errors': []}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))

    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, (response.status, response.text())
        return response.json()

    def check(label):
        report['checks'].append(label)
        print('PASS', label, flush=True)

    try:
        page.goto(args.url + '/#/state')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button', name='用于续算', exact=True)).to_be_enabled(timeout=60000)
        expect(page.locator('.context-status')).to_contain_text('服务正常')
        parent_id = 'STATE-' + evidence['tasks']['custom-first']
        parent = next(row for row in api('states')['states'] if row['stateVersionId'] == parent_id)
        page.locator('.node-item[title="' + parent_id + '"]').click()
        page.get_by_role('button', name='用于续算', exact=True).click()
        expect(page.locator('.state-block-note')).to_contain_text('另一汇流版本')
        expect(page.get_by_role('button', name='开始续算', exact=True)).to_be_disabled()
        check('selected-checkpoint-opens-continuation-with-version-check')

        page.goto(args.url + '/#/diagnostics')
        page.wait_for_load_state('networkidle')
        page.get_by_label('已登记汇流版本').select_option(evidence['routingVersionId'])
        page.get_by_role('button', name='选用此版本', exact=True).click()
        page.goto(args.url + '/#/state')
        page.wait_for_load_state('networkidle')
        page.locator('.node-item[title="' + parent_id + '"]').click()
        page.get_by_label('计算至', exact=True).fill(parent['time'][:16])
        page.get_by_role('button', name='用于续算', exact=True).click()
        next_hour = (datetime.fromisoformat(parent['time']) + timedelta(hours=1)).isoformat(timespec='minutes')
        expect(page.get_by_label('计算至', exact=True)).to_have_value(next_hour)
        expect(page.get_by_label('计算至', exact=True)).to_be_focused()
        expect(page.get_by_role('button', name='开始续算', exact=True)).to_be_enabled()
        page.get_by_label('计算至', exact=True).fill(parent['time'][:16])
        expect(page.get_by_role('button', name='开始续算', exact=True)).to_be_disabled()
        expect(page.locator('.state-block-note')).to_contain_text('晚于当前状态时刻')
        page.get_by_label('计算至', exact=True).fill('2017-06-21T11:00')
        check('continuation-hour-default-focus-and-time-validation')

        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/model/tasks')) as posted:
            page.get_by_role('button', name='开始续算', exact=True).click()
        assert posted.value.status == 202, posted.value.text()
        task = posted.value.json()
        report['taskId'] = task['taskId']
        request = task['runConfig']
        assert request['runType'] == 'stateContinuation'
        assert request['period']['initialStateVersionId'] == parent_id
        assert request['period']['startTime'][:16] == next_hour
        assert request['routing']['versionId'] == evidence['routingVersionId']
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            task = api('tasks/' + report['taskId'])
            if task['status'] not in {'queued', 'running', 'cancelling'}:
                break
            page.wait_for_timeout(500)
        assert task['status'] == 'completed', task
        result = api('tasks/' + report['taskId'] + '/result')
        expect(page.locator('.node-item.active')).to_have_attribute('title', result['stateVersionId'], timeout=20000)
        actual = next(row for row in api('states')['states'] if row['stateVersionId'] == result['stateVersionId'])
        expected = next(row for row in api('states')['states'] if row['stateVersionId'] == 'STATE-' + evidence['tasks']['custom-second'])
        assert actual['land'] == expected['land']
        assert actual['routing'] == expected['routing']
        assert actual['parentStateVersionId'] == parent_id
        page.screenshot(path=str(output / 'state-desktop.png'), full_page=True)
        check('ui-continuation-matches-existing-numerical-land-and-routing-states')

        page.goto(args.url + '/#/config')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='模型方案', exact=True).click()
        expect(page.get_by_role('link', name='编辑坡面与河网汇流参数')).to_be_visible()
        assert '参数冻结' not in page.locator('body').inner_text()
        page.screenshot(path=str(output / 'config-desktop.png'), full_page=True)
        page.get_by_role('link', name='编辑坡面与河网汇流参数').click()
        expect(page).to_have_url(args.url + '/#/diagnostics')
        assert '研究计算' not in page.locator('body').inner_text()
        check('configuration-routing-link-and-operational-copy')
        assert not report['errors'], report['errors']
        report['status'] = 'passed'
    finally:
        page.screenshot(path=str(output / 'last-state.png'), full_page=True)
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
