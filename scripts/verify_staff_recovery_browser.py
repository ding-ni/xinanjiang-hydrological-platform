"""Verify saved staff work across restart and a restored workspace."""
import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', default='http://127.0.0.1:18894')
parser.add_argument('--output', required=True)
parser.add_argument('--snapshot', required=True)
parser.add_argument('--capture', action='store_true')
parser.add_argument('--continue-simulation', action='store_true')
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
snapshot_path = Path(args.snapshot).resolve()
report = {'status': 'failed', 'checks': [], 'pageErrors': []}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1366, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda failure: report['pageErrors'].append(str(failure)))

    def api(path, body=None):
        address = args.url + '/api/model/' + path
        response = context.request.get(address, timeout=120000) if body is None else context.request.post(address, data=body, timeout=120000)
        assert response.ok, (path, response.status, response.text())
        return response.json()

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def saved_work():
        operations = api('operations')['operations']
        assert all(row['status'] not in {'queued', 'running', 'cancelling'} for row in operations)
        schemes = {row['id']: api('model-schemes/' + row['id']) for row in api('model-schemes')['schemes']}
        parameters = api('parameter-schemes')['schemes']
        return {'schemes': schemes, 'parameters': parameters,
                'operations': operations, 'states': api('states')['states'],
                'results': {row['operationId']: digest(api('operations/' + row['operationId'] + '/result'))
                            for row in operations if row['status'] == 'completed'},
                'catalog': api('flood-catalog')}

    try:
        saved = saved_work()
        assert saved['catalog']['count'] == 226
        assert saved['parameters'] and saved['schemes'] and saved['states']
        if args.capture:
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
            report['backup'] = api('workspace/backups', {})
            check('Backup includes named schemes, saved parameters, calculations and states')
        else:
            expected = json.loads(snapshot_path.read_text(encoding='utf-8'))
            for kind in ('schemes', 'parameters', 'operations', 'states', 'results', 'catalog'):
                assert digest(saved[kind]) == digest(expected[kind]), kind
            check('Saved schemes, parameters, all calculation results and states match before restart')

            scheme_counts = {}
            for row in saved['operations']:
                if row['status'] == 'completed':
                    identifier = row.get('lineage', {}).get('schemeId')
                    if identifier:
                        scheme_counts[identifier] = scheme_counts.get(identifier, 0) + 1
            scheme = max((row for row in saved['schemes'].values()
                          if row['name'].startswith('浏览器率定作业-') and row['revision'] > 0),
                         key=lambda row: scheme_counts.get(row['id'], 0))
            page.goto(args.url + '/#/calibration')
            page.wait_for_load_state('networkidle')
            expect(page.locator('.workbench-toolbar')).to_have_count(0)
            page.locator('.scheme-choice[data-scheme-id="' + scheme['id'] + '"]').click()
            expect(page.locator('.scheme-heading h1')).to_have_text(scheme['name'])
            expect(page.locator('.calculation-history').filter(has_text='历次计算').first).to_be_visible()
            selected = [row for row in scheme['editor']['events'] if row.get('purpose') != '未使用']
            expect(page.locator('.selection-counts')).to_contain_text(str(sum(row['purpose'] == '率定' for row in selected)))
            check('A fresh browser reopens the named scheme and its selected floods without running')

            simulation = next(row for row in saved['operations']
                              if row['operation'] == 'continuousSimulation' and row['status'] == 'completed')
            page.goto(args.url + '/#/archive?operation=' + simulation['operationId'])
            page.wait_for_load_state('networkidle')
            expect(page.locator('.daily-result canvas').first).to_be_visible(timeout=30000)
            page.locator('.daily-result').get_by_role('button', name='模拟状态', exact=True).click()
            expect(page.get_by_label('状态回放时刻')).to_be_visible(timeout=30000)
            expect(page.locator('canvas.maplibregl-canvas')).to_be_visible(timeout=60000)
            page.screenshot(path=str(output / 'restored-state.png'), full_page=True)
            check('The saved result opens its hourly state in a fresh browser')

            if args.continue_simulation:
                result = api('operations/' + simulation['operationId'] + '/result')
                end = datetime.fromisoformat(result['hydrograph']['forecast'][-1]['time']) + timedelta(hours=1)
                page.get_by_role('button', name='继续模拟', exact=True).click()
                expect(page.locator('.continuation-source')).to_contain_text(
                    '当前末状态 ' + (end - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M'))
                page.locator('#simulation-end').fill(end.isoformat(timespec='minutes'))
                page.locator('#simulation-end').press('Tab')
                before = {row['operationId'] for row in saved['operations']}
                expect(page.get_by_role('button', name='开始续算', exact=True)).to_be_enabled()
                page.get_by_role('button', name='开始续算', exact=True).click()
                deadline = time.monotonic() + 240
                completed = None
                while time.monotonic() < deadline:
                    row = next((row for row in api('operations')['operations'] if row['operationId'] not in before), None)
                    if row:
                        assert row['status'] not in {'failed', 'cancelled'}, row
                        if row['status'] == 'completed':
                            completed = api('operations/' + row['operationId'] + '/result')
                            break
                    page.wait_for_timeout(350)
                assert completed, 'Restored continuation did not complete'
                assert completed['parentStateVersionId'] == result['endStateVersionId']
                assert len(completed['hydrograph']['forecast']) == 1
                check('A restored state resumes the saved simulation for the next hour')
            else:
                assert len(api('operations')['operations']) == len(saved['operations'])
            assert not report['pageErrors'], report['pageErrors']
        report['status'] = 'passed'
    finally:
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['status'] != 'passed':
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            output.joinpath('failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        browser.close()
