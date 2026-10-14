"""Check station preparation and submission from a saved hydrological scheme."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'requests': [], 'pageErrors': []}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
    def response_record(response):
        if '/api/model/' in response.url and response.request.method == 'POST':
            report['requests'].append({'url': response.url, 'status': response.status,
                                       'body': response.text()[:2000]})
    page.on('response', response_record)
    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, response.text()
        return response.json()
    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        page.screenshot(path=str(output / 'initial.png'), full_page=True)
        prior = {row['taskId'] for row in api('tasks')['tasks']}
        expect(page.locator('#simulation-parameters')).to_be_visible()
        page.get_by_role('button', name='开始模拟', exact=True).click()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            task = next((row for row in api('tasks')['tasks'] if row['taskId'] not in prior
                         and row['runType'] == 'continuousSimulation'), None)
            if task and task['status'] in {'failed', 'cancelled'}:
                raise AssertionError(task.get('errorMessage'))
            if task and task['status'] == 'completed':
                report.update(status='passed', taskId=task['taskId'])
                print('PASS Station preparation continues into a real fixed-parameter simulation', flush=True)
                break
            if any(row['status'] >= 400 for row in report['requests']):
                raise AssertionError(report['requests'])
            page.wait_for_timeout(500)
        assert report['status'] == 'passed', page.locator('.view-cal').inner_text()
        page.screenshot(path=str(output / 'result.png'), full_page=True)
    finally:
        output.joinpath('report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
