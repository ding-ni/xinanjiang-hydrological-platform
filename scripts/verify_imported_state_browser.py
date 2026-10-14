"""Recover the retained imported parameters' states through the staff page."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18896')
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'pageErrors': []}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))

    def api(path, body=None):
        url = args.url + '/api/model/' + path
        response = context.request.get(url) if body is None else context.request.post(url, data=body)
        assert response.ok, (path, response.text())
        return response.json()

    def check(label):
        report['checks'].append(label)
        print('PASS', label, flush=True)

    try:
        candidate = next(row for row in api('parameter-versions')['versions'] if row.get('candidateKind') == 'frozen-calibration')
        source = candidate['taskId']
        original = api('tasks/' + source + '/result')
        parameters = api('parameter-schemes')
        page.goto(args.url + '/#/state?source=' + source)
        page.wait_for_load_state('networkidle')
        if not original.get('eventStates'):
            page.get_by_role('button', name='计算状态', exact=True).click()
        else:
            api('operations', {'operation': 'stateReplay', 'sourceTaskId': source, 'context': candidate['context']})
        deadline, last_update = time.monotonic() + 1800, 0
        while time.monotonic() < deadline:
            operation = next((row for row in api('operations')['operations'] if row.get('requestId') == 'STATE-' + source), None)
            if operation:
                assert operation['status'] not in {'failed', 'cancelled'}, operation
                if operation['status'] == 'completed':
                    break
                if time.monotonic() - last_update > 45:
                    print(operation['stageLabel'], flush=True)
                    last_update = time.monotonic()
            page.wait_for_timeout(1000)
        else:
            raise AssertionError('Imported state calculation timed out')
        result = api('operations/' + operation['taskId'] + '/result')
        updated = api('tasks/' + source + '/result')
        for key, value in original.items():
            if key not in {'eventStates', 'stateCalculationTaskId', 'endStateVersionId'}:
                assert updated[key] == value, key
        assert updated['eventStates']
        assert api('parameter-schemes') == parameters
        replay = operation['runConfig']['frozenReplay']
        assert replay['calibration']['optimizer']['algorithm'] == 'FIXED_PARAMETERS'
        assert replay['calibration']['initialParameters'] == candidate['bestParameters']
        repeated = api('operations', {'operation': 'stateReplay', 'sourceTaskId': source, 'context': candidate['context']})
        assert repeated['taskId'] == operation['taskId']
        check('Imported conditions produce states with fixed parameters and repeat requests reuse the result')
        # A fresh visit uses the persisted state links rather than browser memory.
        page.goto(args.url + '/#/state?source=' + source)
        expect(page.get_by_label('状态所属过程')).to_be_visible(timeout=60000)
        assert page.get_by_label('状态所属过程').locator('option').count() == len(updated['eventStates'])
        for row in updated['eventStates'][:2]:
            page.get_by_label('状态所属过程').select_option(row['eventId'])
            expect(page.get_by_label('状态回放时刻')).to_be_visible(timeout=60000)
            expect(page.get_by_label('状态回放时刻')).to_have_value(row['end'][:19], timeout=60000)
        expect(page.locator('.spatial-pending')).to_have_count(0, timeout=60000)
        expect(page.locator('.state-layout .maplibregl-canvas')).to_be_visible()
        page.get_by_role('button', name='自由水蓄量', exact=False).click()
        expect(page.locator('.map-legend')).to_contain_text('自由水')
        assert not page.locator('[role=alert]').count(), page.locator('body').inner_text()
        page.screenshot(path=str(output / 'imported-state-map.png'), full_page=True)
        check('Recovered floods and times open the matching state map after reload')
        assert not report['pageErrors'], report['pageErrors']
        report.update(status='passed', sourceTaskId=source, operationId=operation['taskId'])
    finally:
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['status'] != 'passed':
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            output.joinpath('failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        browser.close()
