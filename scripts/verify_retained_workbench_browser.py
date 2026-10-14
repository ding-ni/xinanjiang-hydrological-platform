"""Continue the retained colleague scheme through real search and manual trial."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18896')
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'pageErrors': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))

    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, response.text()
        return response.json()

    def check(label):
        report['checks'].append(label)
        print('PASS', label, flush=True)

    def run(label, kind):
        before = {row['taskId'] for row in api('operations')['operations']}
        page.get_by_role('button', name=label, exact=True).click()
        deadline, last_update = time.monotonic() + 1800, 0
        while time.monotonic() < deadline:
            operation = next((row for row in api('operations')['operations'] if row['taskId'] not in before and row['operation'] == kind), None)
            if operation:
                assert operation['status'] not in {'failed', 'cancelled'}, operation
                if operation['status'] == 'completed':
                    page.wait_for_timeout(1800)
                    return operation, api('operations/' + operation['taskId'] + '/result')
                if time.monotonic() - last_update > 45:
                    print(operation['stageLabel'], flush=True)
                    last_update = time.monotonic()
            elif page.locator('.calculation-status [role=alert]').count():
                raise AssertionError(page.locator('.calculation-status').inner_text())
            page.wait_for_timeout(700)
        raise AssertionError('Retained scheme calculation timed out')

    try:
        scheme = next(row for row in api('model-schemes')['schemes'] if row.get('originalPlanSHA256'))
        page.goto(args.url + '/#/calibration?schemeId=' + scheme['id'])
        expect(page.locator('.flood-row')).to_have_count(16, timeout=20000)
        saved = api('model-schemes/' + scheme['id'])['editor']
        original_uses = {row['id']: row['purpose'] for row in saved['events'] + saved['schemeWindows']}
        page.get_by_label('洪水用途筛选').select_option('验证')
        expect(page.locator('.flood-row')).to_have_count(3)
        page.get_by_label('洪水用途筛选').select_option('all')
        page.get_by_role('button', name='自动划分', exact=True).click()
        expect(page.locator('.split-preview')).to_be_visible()
        assert {row['id']: row['purpose'] for row in api('model-schemes/' + scheme['id'])['editor']['events'] + saved['schemeWindows']} == original_uses
        # Previewing a split is not applying it.
        page.get_by_role('button', name='率定设置', exact=True).click()
        page.locator('#cal-target').select_option('joint')
        page.locator('#cal-target').select_option('runoff')
        page.locator('#max-evals').fill('4'); page.locator('#max-evals').press('Tab')
        page.locator('.advanced-settings summary').click()
        page.locator('#population-size').fill('4'); page.locator('#population-size').press('Tab')
        page.get_by_role('button', name='完成', exact=True).click()
        operation, result = run('开始率定', 'calibration')
        assert result['optimizer']['evaluations'] == 4
        assert result['eventStates'] and len(result['hydrographs']) == 16
        assert not result['frozenRequest'].get('sourcePlan')
        assert not result['frozenRequest']['calibration']['optimizer'].get('initializationPolicy')
        report['searchTaskId'] = result['primaryTaskId']
        check('The retained sixteen floods complete a real four-evaluation search with changed target')
        page.get_by_role('button', name='人工试调', exact=True).click()
        sm = page.locator('#parameter-SM')
        sm.fill(str(float(sm.input_value()) + .1)); sm.press('Tab')
        operation, trial = run('试算', 'manualTrial')
        assert trial['optimizer']['evaluations'] == 1
        assert trial['evaluationMode'] == 'manual'
        assert not trial['frozenRequest'].get('sourcePlan')
        assert trial['frozenRequest']['routing']['versionId'] == result['frozenRequest']['routing']['versionId']
        assert not api('parameter-schemes')['schemes']
        page.get_by_role('button', name='状态分布', exact=True).click()
        expect(page.locator('.state-layout .map-panel')).to_be_visible(timeout=20000)
        expect(page.get_by_label('状态回放时刻')).to_be_visible()
        check('Manual trial retains complete routing, creates no saved parameters and opens its own state map')
        report['trialTaskId'] = trial['primaryTaskId']
        page.screenshot(path=str(output / 'retained-state.png'), full_page=True)
        assert not report['pageErrors'], report['pageErrors']
        report['status'] = 'passed'
    finally:
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['status'] != 'passed':
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            output.joinpath('failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        browser.close()
