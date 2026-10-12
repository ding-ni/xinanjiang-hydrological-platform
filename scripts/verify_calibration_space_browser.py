"""Verify selected automatic calibration against real results, SQL and exports."""
import argparse
import csv
import json
import time
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18888')
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'errors': [], 'failedResources': []}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    page.on('response', lambda r: report['failedResources'].append(r.url) if r.status >= 400 else None)

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, response.text()
        return response.json()

    def fill(field, value):
        field.fill(value)
        field.press('Tab')

    def complete(task):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = api('tasks/' + task['taskId'])
            if state['status'] not in {'queued', 'running'}:
                assert state['status'] == 'completed', state
                return api('tasks/' + task['taskId'] + '/result')
            page.wait_for_timeout(500)
        raise AssertionError('Calculation timed out')

    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-cal')).to_be_visible()
        (output / 'initial-ui.txt').write_text(page.locator('.view-cal').inner_text(), encoding='utf-8')
        page.get_by_role('button', name='参数率定', exact=False).click()
        page.get_by_role('button', name='连续时段', exact=True).click()
        for selector, value in [('#cal-start', '2017-06-19T00:00'), ('#cal-end', '2017-06-19T23:00'),
                                ('#val-start', '2017-06-20T00:00'), ('#val-end', '2017-06-20T23:00'),
                                ('#ind-start', '2017-06-21T00:00'), ('#ind-end', '2017-06-21T23:00')]:
            fill(page.locator(selector), value)
        fill(page.get_by_label('预热期天数', exact=True), '1')
        fill(page.locator('#parameter-SM'), '20')
        page.locator('#search-strategy').select_option('joint')
        page.locator('#algorithm').select_option('DIFFERENTIAL_EVOLUTION')
        fill(page.locator('#search-seed'), '17')
        fill(page.locator('#max-evals'), '4')
        fill(page.locator('#population-size'), '4')
        editor = page.locator('.search-editor')
        if not editor.evaluate('el => el.open'):
            editor.locator('summary').click()
        editor.get_by_role('button', name='全部固定', exact=True).click()
        automatic = page.get_by_role('button', name='确认率定任务', exact=True)
        manual = page.get_by_role('button', name='试算并叠加', exact=True)
        expect(automatic).to_be_disabled()
        expect(manual).to_be_enabled()
        page.get_by_label('SM 参与率定', exact=True).check()
        fill(page.get_by_label('SM 搜索下限', exact=True), '10')
        fill(page.get_by_label('SM 搜索上限', exact=True), '40')
        expect(automatic).to_be_enabled()
        fill(page.get_by_label('SM 搜索下限', exact=True), '30')
        expect(automatic).to_be_disabled()
        expect(editor).to_contain_text('当前参数须在搜索范围内')
        expect(manual).to_be_enabled()
        fill(page.get_by_label('SM 搜索下限', exact=True), '10')
        page.locator('#search-strategy').select_option('staged')
        expect(automatic).to_be_disabled()
        expect(editor).to_contain_text('单组参数')
        page.locator('#search-strategy').select_option('joint')
        check('single-parameter-bounds-and-manual-independent-controls')

        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as submitted:
            automatic.click()
        assert submitted.value.status == 202, submitted.value.text()
        task = submitted.value.json()
        report['taskId'] = task['taskId']
        assert task['runConfig']['calibration']['searchParameters'] == {'SM': {'minimum': 10, 'maximum': 40}}
        initial = task['runConfig']['calibration']['initialParameters']
        result = complete(task)
        assert 10 <= result['bestParameters']['SM'] <= 40
        assert result['bestParameters']['SM'] != initial['SM']
        for key, value in initial.items():
            if key != 'SM':
                assert result['bestParameters'][key] == value, key
        assert [row['key'] for row in result['searchPlan']['rows'] if row['active']] == ['SM']
        expect(page.locator('.search-plan')).to_be_visible(timeout=30000)
        page.locator('.search-plan summary').click()
        expect(page.get_by_role('table', name='本次搜索参数')).to_contain_text('自由水蓄水容量')
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='搜索参数表 CSV', exact=True).click()
        downloaded.value.save_as(str(output / 'search-parameters.csv'))
        with (output / 'search-parameters.csv').open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.reader(handle))
        sm_row = next(row for row in rows if row[1] == 'SM')
        assert float(sm_row[-1]) == result['bestParameters']['SM']
        assert len(rows) == 12
        check('real-SM-search-fixed-values-and-full-precision-CSV')

        page.wait_for_timeout(1800)
        other = browser.new_context(viewport={'width': 1440, 'height': 1000})
        other_page = other.new_page()
        other_page.goto(args.url + '/#/calibration')
        other_page.wait_for_load_state('networkidle')
        expect(other_page.get_by_label('SM 搜索下限', exact=True)).to_have_value('10')
        expect(other_page.get_by_label('SM 搜索上限', exact=True)).to_have_value('40')
        expect(other_page.get_by_label('WM 参与率定', exact=True)).not_to_be_checked()
        expect(other_page.locator('.search-plan')).to_be_visible()
        other.close()
        check('fresh-browser-recovers-selection-and-results-from-SQL')

        for width in (1440, 390, 360):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.wait_for_timeout(500)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
            assert page.locator('.process-panel').evaluate("el => el.querySelector('canvas').getBoundingClientRect().bottom <= el.getBoundingClientRect().bottom + 1")
            page.screenshot(path=str(output / f'search-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1000})
        check('desktop-and-mobile-search-result-layout')

        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        page.get_by_label('检索成果', exact=True).fill(task['taskId'])
        row = page.locator('.task-table tbody tr').filter(has=page.locator(f'td[title="{task["taskId"]}"]'))
        row.get_by_role('button', name='查看成果', exact=True).click()
        expect(page.locator('.search-plan')).to_be_visible()
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='完整结果 JSON', exact=True).click()
        downloaded.value.save_as(str(output / 'frozen-result.json'))
        saved = json.loads((output / 'frozen-result.json').read_text(encoding='utf-8'))
        assert saved['result'] == result
        assert saved['request']['calibration']['searchParameters'] == {'SM': {'minimum': 10, 'maximum': 40}}
        row.get_by_role('button', name='按原条件打开', exact=True).click()
        expect(page.get_by_label('SM 搜索下限', exact=True)).to_have_value('10')
        expect(page.get_by_label('WM 参与率定', exact=True)).not_to_be_checked()
        expect(page.locator('#parameter-SM')).to_have_value('20')
        check('archive-plan-export-and-original-selection-handoff')

        # Custom scopes remain valid for repeat-seed experiments.
        page.goto(args.url + '/#/diagnostics')
        page.wait_for_load_state('networkidle')
        page.get_by_role('tab', name='参数稳定性检验', exact=True).click()
        page.get_by_label('率定方案').select_option(task['taskId'])
        expect(page.get_by_label('实验方式').locator('option[value="wlmBoundary"]')).to_have_attribute('disabled', '')
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as submitted:
            page.get_by_role('button', name='开始参数检验', exact=True).click()
        assert submitted.value.status == 202, submitted.value.text()
        diagnostic = submitted.value.json()
        experiment = complete(diagnostic)
        report['diagnosticTaskId'] = diagnostic['taskId']
        assert len(experiment['experiment']['runs']) == 3
        for run in experiment['experiment']['runs']:
            assert 10 <= run['bestParameters']['SM'] <= 40
            assert run['optimizer']['evaluations'] == 4
            for key, value in initial.items():
                if key != 'SM':
                    assert run['bestParameters'][key] == value, key
        check('custom-SM-three-seed-stability-experiment')

        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        page.locator('.search-editor').get_by_role('button', name='全部固定', exact=True).click()
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as submitted:
            page.get_by_role('button', name='试算并叠加', exact=True).click()
        assert submitted.value.status == 202, submitted.value.text()
        manual_task = submitted.value.json()
        assert 'searchParameters' not in manual_task['runConfig']['calibration']
        manual_result = complete(manual_task)
        assert manual_result['bestParameters'] == manual_task['runConfig']['calibration']['initialParameters']
        assert manual_result['searchPlan']['mode'] == 'manual'
        assert not any(row['active'] for row in manual_result['searchPlan']['rows'])
        expect(page.get_by_role('button', name='试算并叠加', exact=True)).to_be_enabled(timeout=30000)
        page.locator('.search-editor').get_by_role('button', name='预设范围', exact=True).click()
        check('all-fixed-manual-trial-and-default-reset')
        assert not report['errors'], report['errors']
        assert not report['failedResources'], report['failedResources']
        report['status'] = 'passed'
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        page.screenshot(path=str(output / 'last-ui.png'), full_page=True)
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
