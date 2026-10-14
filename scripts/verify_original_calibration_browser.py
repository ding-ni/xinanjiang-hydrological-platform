"""Verify a retained original calibration opens as a named scheme without running."""
import argparse
import json
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
    context = browser.new_context(viewport={'width': 1440, 'height': 1050})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))

    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, (path, response.text())
        return response.json()

    def check(text):
        report['checks'].append(text)
        print('PASS', text, flush=True)

    try:
        versions = api('parameter-versions')['versions']
        assert len(versions) == 2
        original = next(row for row in versions if row['candidateKind'] == 'frozen-calibration')
        expected = api('parameter-versions/' + original['parameterVersionId'] + '/original-plan?operation=search')
        task_count = len(api('tasks')['tasks'])
        operation_count = len(api('operations')['operations'])
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.original-records')).to_be_visible()
        page.locator('.original-records summary').click()
        expect(page.locator('.original-records .scheme-choice')).to_have_count(1)
        check('Two original parameter records appear as one retained calibration plan')
        page.locator('.original-records .scheme-choice').click()
        expect(page.locator('.scheme-heading h1')).to_have_text(original['name'])
        expect(page.locator('.flood-panel .panel-heading')).to_contain_text('226 场')
        expect(page.get_by_role('button', name='开始率定', exact=True)).to_be_enabled()
        page.wait_for_timeout(1000)
        rows = api('model-schemes')['schemes']
        assert len(rows) == 1
        saved = api('model-schemes/' + rows[0]['id'])
        editor = saved['editor']
        assert editor['schemeSourceBundleVersionId'] == expected['request']['inputs']['bundleVersionId']
        assert editor['schemeRoutingVersionId'] == expected['request']['routing']['versionId']
        assert editor['scienceVersionId'] == expected['request']['science']['versionId']
        assert editor['statePolicy'] == expected['request']['calibration']['statePolicy']
        assert editor['continuousOrigin'] == expected['request']['calibration']['continuousOrigin']
        assert editor['params'] == expected['request']['calibration']['initialParameters']
        for key, value in expected['request']['calibration']['objectives'].items():
            assert editor['criteria'][key] == value
        assert editor['maxEvaluations'] == expected['request']['calibration']['optimizer']['maxEvaluations']
        assert editor['searchSeed'] == expected['request']['calibration']['optimizer']['seed']
        actual = {row['id']: (row['start'], row['end'], row['purpose']) for row in saved['calculationDraft']['period']['events']}
        requested = {row['id']: (row['start'], row['end'], row['purpose']) for row in expected['request']['period']['events']}
        assert actual == requested
        assert len(actual) == 16
        assert len(api('flood-catalog')['events']) == 226
        assert len(editor['events']) == 226
        assert len(editor['schemeWindows']) == 13
        assert editor['eventId'] in actual
        assert len(editor['originalCalibrationPlan']['sourceEventWindows']) == 17
        assert len(editor['originalCalibrationPlan']['sourceExcludedEventIds']) == 1
        assigned = {row['id']: row['purpose'] for row in editor['events'] + editor['schemeWindows']}
        for identifier in editor['originalCalibrationPlan']['sourceExcludedEventIds']:
            assert assigned.get(identifier, '未使用') == '未使用'
        check('The full catalogue retains 226 floods; the source sixteen accepted floods and one excluded window remain exact')
        check('Source rainfall, parameter ranges, routing, evaporation, warmup and purpose assignments are restored together')

        # The fixed display context is independent from the imported scheme.
        assert 'INPUT-MDB-4b0ebc43b1144d46a2a85b850e9a0528' not in page.locator('.topbar').inner_text()
        page.get_by_role('button', name='← 方案列表', exact=True).click()
        page.locator('.original-records summary').click()
        page.locator('.original-records .scheme-choice').click()
        expect(page.locator('.scheme-heading h1')).to_have_text(original['name'])
        assert len(api('model-schemes')['schemes']) == 1
        assert len(api('tasks')['tasks']) == task_count
        assert len(api('operations')['operations']) == operation_count
        check('Opening the retained record again reuses its named scheme and starts no calculation')
        page.screenshot(path=str(output / 'original-calibration.png'), full_page=True)
        assert not report['pageErrors'], report['pageErrors']
        report['schemeId'] = rows[0]['id']
        report['status'] = 'passed'
    finally:
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['status'] != 'passed':
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            output.joinpath('failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        browser.close()
