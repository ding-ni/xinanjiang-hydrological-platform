"""Run continuous calibration through the UI, recover, export and reuse it."""
import argparse
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

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    page.on('response', lambda response: report['failedResources'].append(response.url) if response.status >= 400 else None)

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def api(path):
        answer = context.request.get(args.url + '/api/model/' + path)
        assert answer.ok, answer.text()
        return answer.json()

    def fill(selector, value):
        field = page.locator(selector)
        field.fill(value)
        field.press('Tab')

    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-cal')).to_be_visible()
        (output / 'initial-ui.txt').write_text(page.locator('.view-cal').inner_text(), encoding='utf-8')
        page.get_by_role('button', name='连续时段', exact=True).click()
        expect(page.locator('#cal-start')).to_be_visible()
        expect(page.locator('.events-panel')).to_have_count(0)
        expect(page.locator('.data-list')).to_contain_text('逐月给定值')
        for selector, value in [('#cal-start', '2017-06-19T00:00'), ('#cal-end', '2017-06-19T23:00'),
                                ('#val-start', '2017-06-20T00:00'), ('#val-end', '2017-06-20T23:00'),
                                ('#ind-start', '2017-06-21T00:00'), ('#ind-end', '2017-06-21T23:00')]:
            fill(selector, value)
        page.get_by_label('预热期天数', exact=True).fill('1')
        page.get_by_label('预热期天数', exact=True).press('Tab')
        page.locator('#search-strategy').select_option('staged')
        page.locator('#algorithm').select_option('DIFFERENTIAL_EVOLUTION')
        fill('#search-seed', '17')
        fill('#max-evals', '10')
        fill('#population-size', '4')
        submit = page.get_by_role('button', name='确认率定任务', exact=True)
        expect(submit).to_be_enabled()
        fill('#val-start', '2017-06-19T23:00')
        expect(submit).to_be_disabled()
        expect(page.locator('.run-message.warn').first).to_contain_text('不能重叠')
        fill('#val-start', '2017-06-20T00:00')
        expect(submit).to_be_enabled()
        check('continuous-periods-pet-and-overlap-validation')

        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as submitted:
            submit.click()
        assert submitted.value.status == 202, submitted.value.text()
        task = submitted.value.json()
        report['taskId'] = task['taskId']
        assert task['runType'] == 'continuousCalibration'
        assert task['runConfig']['calibration']['optimizer']['strategy'] == 'staged'
        assert 'events' not in task['runConfig']['period']
        page.reload()
        page.wait_for_load_state('networkidle')
        expect(page.locator('#search-strategy')).to_have_value('staged')
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            task = api('tasks/' + task['taskId'])
            if task['status'] not in {'queued', 'running'}:
                break
            page.wait_for_timeout(500)
        assert task['status'] == 'completed', task
        result = api('tasks/' + task['taskId'] + '/result')
        assert result['optimizer']['evaluations'] == 10
        expect(page.locator('.search-stage-results')).to_contain_text('水源划分', timeout=30000)
        expect(page.locator('.parameter-result-card')).to_contain_text(result['parameterVersionId'])
        expect(page.locator('.continuous-process-tabs button')).to_have_count(3)
        for name, date in [('率定期', '2017-06-19'), ('验证期', '2017-06-20'), ('独立检验期', '2017-06-21')]:
            page.locator('.continuous-process-tabs').get_by_role('button', name=name, exact=True).click()
            expect(page.locator('.process-subtitle')).to_contain_text(date)
            expect(page.locator('.hydro canvas').first).to_be_visible()
        check('real-staged-task-refresh-recovery-and-three-process-lines')

        for width in (1440, 390, 360):
            page.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
            page.wait_for_timeout(700)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'Overflow at {width}'
            assert page.locator('.process-panel').evaluate("el => el.querySelector('canvas').getBoundingClientRect().bottom <= el.getBoundingClientRect().bottom + 1"), 'Process chart is clipped'
            page.screenshot(path=str(output / f'continuous-{width}.png'), full_page=True)
        check('desktop-and-mobile-layouts')
        page.set_viewport_size({'width': 1440, 'height': 1000})

        # A fresh browser obtains the persisted SQL workspace, not the old tab's storage.
        other_context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        other_page = other_context.new_page()
        other_page.goto(args.url + '/#/calibration')
        other_page.wait_for_load_state('networkidle')
        expect(other_page.locator('#search-strategy')).to_have_value('staged', timeout=30000)
        expect(other_page.locator('#ind-end')).to_have_value('2017-06-21T23:00')
        expect(other_page.locator('.search-stage-results')).to_contain_text('水源划分')
        other_context.close()
        check('fresh-browser-restores-sql-periods-search-and-result')

        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        row = page.locator('.task-table tr', has_text=task['taskId'])
        row.get_by_role('button', name='查看条件', exact=True).click()
        expect(page.get_by_role('button', name='导出结果与冻结条件 JSON')).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('button', name='导出结果与冻结条件 JSON').click()
        download.value.save_as(str(output / 'continuous-result.json'))
        saved = json.loads((output / 'continuous-result.json').read_text(encoding='utf-8'))
        assert saved['result'] == result
        assert saved['request']['period'] == task['runConfig']['period']
        row.get_by_role('button', name='按原条件打开', exact=True).click()
        expect(page.locator('#search-strategy')).to_have_value('staged')
        expect(page.locator('#search-seed')).to_have_value('17')
        expect(page.locator('#ind-end')).to_have_value('2017-06-21T23:00')
        check('archive-export-and-original-conditions-handoff')
        # Reopen the completed result after restoring the request, then adopt its
        # registered parameters without starting a new calculation implicitly.
        page.reload()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button', name='采用该参数', exact=True)).to_be_visible(timeout=30000)
        count_before = len(api('tasks')['tasks'])
        page.get_by_role('button', name='采用该参数', exact=True).click()
        expect(page.locator('#cal-version')).to_have_value(result['parameterVersionId'])
        assert len(api('tasks')['tasks']) == count_before
        check('adopts-registered-parameters-without-implicit-run')
        page.goto(args.url + '/#/diagnostics')
        page.wait_for_load_state('networkidle')
        page.get_by_role('tab', name='参数稳定性检验', exact=True).click()
        page.get_by_label('率定方案').select_option(task['taskId'])
        expect(page.get_by_label('率定方案')).to_contain_text('连续时段')
        expect(page.get_by_label('最大参数评价次数', exact=True)).to_have_value('10')
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as submitted:
            page.get_by_role('button', name='开始参数检验', exact=True).click()
        assert submitted.value.status == 202, submitted.value.text()
        diagnostic = submitted.value.json()
        report['diagnosticTaskId'] = diagnostic['taskId']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            diagnostic = api('tasks/' + diagnostic['taskId'])
            if diagnostic['status'] not in {'queued', 'running'}:
                break
            page.wait_for_timeout(500)
        assert diagnostic['status'] == 'completed', diagnostic
        experiment = api('tasks/' + diagnostic['taskId'] + '/result')
        assert len(experiment['experiment']['runs']) == 3
        expect(page.locator('.diagnostic-view')).to_contain_text('采用候选至率定工作区', timeout=30000)
        check('continuous-calibration-three-seed-diagnostic-completes')
        assert not report['errors'], report['errors']
        assert not report['failedResources'], report['failedResources']
        report['status'] = 'passed'
    finally:
        page.screenshot(path=str(output / 'last-ui.png'), full_page=True)
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
