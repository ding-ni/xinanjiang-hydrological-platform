"""Read-only browser checks for old calculation parameter restoration."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', default='http://127.0.0.1:18894')
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'pageErrors': []}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    page = context.new_page()
    page.on('pageerror', lambda failure: report['pageErrors'].append(str(failure)))

    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, (path, response.text())
        return response.json()

    def go(path):
        page.goto(args.url + '/#/' + path)
        page.wait_for_load_state('networkidle')

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    try:
        tasks = api('tasks')['tasks']
        parameters = api('parameter-schemes')['schemes']
        operations = api('operations')['operations']
        children = {identifier for row in operations for identifier in row.get('childTaskIds', [])}
        named_ids = {row['id'] for row in parameters}
        named_sources = {row['sourceTaskId'] for row in parameters}
        old = next(row for row in tasks if row['status'] == 'completed'
                   and row['runType'] == 'continuousSimulation'
                   and row['taskId'] not in children | named_sources
                   and row['runConfig'].get('lineage', {}).get('parameterVersionId') not in named_ids
                   and row['runConfig'].get('lineage', {}).get('parameterSchemeId') not in named_ids)
        old_id = old['taskId']
        named_source = next(row for row in tasks if row['status'] == 'completed'
                            and row['runType'] == 'continuousSimulation'
                            and row['taskId'] in named_sources)
        original = next(row for row in parameters if row['sourceTaskId'] == named_source['taskId'])
        staff = next(row for row in parameters if row['id'] != original['id'])
        go('simulation')
        page.locator('#simulation-parameter').select_option(staff['id'])
        go('simulation?task=' + old_id)
        expect(page.locator('#simulation-parameter')).to_have_value('')
        expect(page.get_by_role('button', name='开始模拟', exact=True)).to_be_disabled()
        expect(page.locator('.el-message-box')).to_have_count(0)
        check('An unnamed old simulation opens a draft with no unrelated daily parameter')

        go('forecast?task=' + old_id)
        expect(page.locator('#forecast-parameter')).to_have_value('')
        expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
        page.locator('#forecast-parameter').select_option(staff['id'])
        expect(page.locator('.daily-result canvas')).to_have_count(0)
        check('The old JOB process stays frozen; explicitly changing parameters clears its curve')

        go('forecast?task=' + named_source['taskId'])
        expect(page.locator('#forecast-parameter')).to_have_value(original['id'])
        expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
        go('simulation?task=' + named_source['taskId'])
        expect(page.locator('#simulation-parameter')).to_have_value(original['id'])
        check('Both old calculation pages restore an explicitly associated saved parameter')

        go('archive?task=' + old_id)
        row = page.locator('.task-table tbody tr').filter(has=page.locator('td[title="' + old_id + '"]'))
        expect(row).to_have_count(1)
        row.get_by_role('button', name='按原条件打开', exact=True).click()
        expect(page.locator('#simulation-parameter')).to_have_value('')
        expect(page.locator('.el-message-box')).to_have_count(0)
        check('Archive restores the old conditions without saving a candidate or substituting daily parameters')

        no_state = next(row for row in tasks if row['status'] == 'completed' and row['runType'] == 'pointQuery')
        go('state?source=' + no_state['taskId'])
        page.get_by_role('link', name='模拟此时段', exact=True).click()
        expect(page.locator('#simulation-parameter')).to_have_value('')
        check('A result without a saved model state opens a simulation draft with no unrelated parameter')

        assert len(api('tasks')['tasks']) == len(tasks)
        assert len(api('operations')['operations']) == len(operations)
        assert len(api('parameter-schemes')['schemes']) == len(parameters)
        assert not report['pageErrors'], report['pageErrors']
        report.update(status='passed', oldTaskId=old_id, namedTaskId=named_source['taskId'],
                      emptyStateTaskId=no_state['taskId'])
    finally:
        if report['status'] != 'passed':
            print(page.locator('body').inner_text(), flush=True)
        page.screenshot(path=str(output / 'last-state.png'), full_page=True)
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
