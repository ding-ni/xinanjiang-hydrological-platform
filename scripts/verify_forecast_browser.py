"""Real rainfall-scenario forecast UI acceptance in an isolated workspace."""
import argparse
import json
import time
import zipfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True)
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

    def api(path, body=None):
        url = args.url + '/api/model/' + path
        response = context.request.get(url) if body is None else context.request.post(url, data=body)
        assert response.ok, response.text()
        return response.json()

    def complete(task):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = api('tasks/' + task['taskId'])
            if current['status'] not in {'queued', 'running'}:
                assert current['status'] == 'completed', current
                return api('tasks/' + task['taskId'] + '/result')
            page.wait_for_timeout(300)
        raise AssertionError('Forecast timed out')

    def fill(selector, value):
        page.locator(selector).fill(value)
        page.locator(selector).press('Tab')

    def save_rain(name, rainfall):
        editor = page.locator('.rain-editor')
        if editor.get_attribute('open') is None:
            editor.locator('summary').click()
        fill('#forecast-rain-name', name)
        fill('#forecast-hourly-rainfall', rainfall)
        with page.expect_response(lambda r: r.url.endswith('/forecast-rain') and r.request.method == 'POST') as saved:
            page.get_by_role('button', name='保存并采用情景', exact=True).click()
        assert saved.value.ok, saved.value.text()
        row = saved.value.json()
        expect(page.locator('#forecast-rain-product')).to_have_value(row['versionId'])
        return row

    def run_forecast():
        expect(page.locator('.run-button')).to_be_enabled(timeout=30000)
        page.locator('.run-button').click()
        with page.expect_response(lambda r: r.url.endswith('/forecast-tasks') and r.request.method == 'POST') as submitted:
            page.get_by_role('button', name='确认提交', exact=True).click()
        assert submitted.value.status == 202, submitted.value.text()
        task = submitted.value.json()
        result = complete(task)
        expect(page.locator('.backend-forecast-chart canvas')).to_be_visible(timeout=30000)
        expect(page.locator('#forecast-history')).to_have_value(task['taskId'], timeout=30000)
        expect(page.locator('.run-button')).to_be_enabled(timeout=30000)
        expect(page.locator('.forecast-results .condition-note')).to_have_count(0)
        return task, result

    try:
        # The port is reserved before the first workspace imports its observations.
        ready = context.request.get(args.url + '/api/model/health', timeout=180000)
        assert ready.ok, ready.text()
        state = complete(api('tasks', {
            'schemaVersion': '2.0', 'requestId': 'BROWSER-INIT-' + str(time.time_ns()), 'runType': 'stateInitialization',
            'context': {'contextKey': 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km',
                        'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'modelConfigId': 'nananxi-xufan-1km'},
            'period': {'startTime': '2017-06-19T11:00', 'endTime': '2017-06-19T11:00', 'warmupDays': 1},
            'parameters': {}, 'inputs': {'evaporation': {'mode': 'monthly-climatology-assumption'}}, 'lineage': {}}))
        before_states = api('states')
        page.goto(args.url + '/#/forecast')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-forecast')).to_be_visible()
        (output / 'initial-ui.txt').write_text(page.locator('.view-forecast').inner_text(), encoding='utf-8')
        fill('#forecast-issue-time', '2017-06-19T11:00')
        page.locator('#forecast-lead').select_option('6')
        page.locator('#forecast-state').select_option(state['stateVersionId'])
        expect(page.locator('#manual-parameter-version')).to_have_value('__STATE__')
        rain = save_rain('六小时降雨过程', '0 1 5 10 2 0')
        wet_task, wet = run_forecast()
        assert wet['lineage']['forecastRainVersionId'] == rain['versionId']
        expect(page.locator('.forecast-summary')).to_contain_text('18.0')
        page.locator('.forecast-ledger summary').click()
        expect(page.locator('.forecast-ledger tbody tr')).to_have_count(6)
        expect(page.locator('.forecast-ledger tbody tr').first).to_contain_text('06-19 12:00')
        expect(page.locator('.forecast-ledger tbody tr').last).to_contain_text('06-19 17:00')
        expect(page.locator('.backend-forecast-chart .figure-legend')).not_to_contain_text('实测流量')
        check('real-hour-ending-forecast-chart-six-hour-table-and-summary')
        page.get_by_role('button', name='查看预报单', exact=True).click()
        sheet = page.locator('#forecast-report-print')
        expect(sheet).to_be_visible()
        expect(sheet).to_contain_text(rain['versionId'])
        expect(sheet).to_contain_text(state['stateVersionId'])
        expect(sheet).to_contain_text('18.0 mm')
        expect(sheet).not_to_contain_text('峰水位（估）')
        sheet.screenshot(path=str(output / 'forecast-report.png'))
        sheet.get_by_role('button', name='关闭', exact=True).click()
        check('report-uses-frozen-rain-state-and-volume-without-demo-water-level')
        save_rain('无雨退水情景', '0 0 0 0 0 0')
        expect(page.locator('.forecast-results')).to_contain_text('当前保留上次成果')
        expect(page.locator('.forecast-summary')).to_contain_text('18.0')
        dry_task, dry = run_forecast()
        assert wet['hydrograph']['forecast'] != dry['hydrograph']['forecast']
        assert api('tasks/' + wet_task['taskId'] + '/result') == wet
        assert api('states') == before_states
        report['taskIds'] = [wet_task['taskId'], dry_task['taskId']]
        check('change-rainfall-recalculate-without-overwriting-old-result-or-observed-state')
        page.get_by_role('button', name='归档本次预报', exact=True).click()
        expect(page.locator('.forecast-results .latest-badge')).to_have_text('已归档')
        assert api('tasks/' + dry_task['taskId'])['status'] == 'archived'
        page.reload()
        page.wait_for_load_state('networkidle')
        page.locator('#forecast-history').select_option(wet_task['taskId'])
        expect(page.locator('.forecast-summary')).to_contain_text('18.0', timeout=30000)
        expect(page.locator('.forecast-results .condition-note')).to_have_count(0, timeout=30000)
        expect(page.locator('#forecast-rain-product')).to_have_value(rain['versionId'])
        expect(page.locator('#forecast-state')).to_have_value(state['stateVersionId'])
        check('archive-and-reload-frozen-forecast-without-new-calculation')
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.wait_for_timeout(500)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
            page.screenshot(path=str(output / f'forecast-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1000})
        check('desktop-mobile-layout-without-horizontal-overflow')
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        row = page.locator('.task-table tbody tr').filter(has=page.locator(f'td[title="{dry_task["taskId"]}"]'))
        row.get_by_role('button', name='查看成果', exact=True).click()
        workbench = page.locator('.result-workbench')
        expect(workbench.locator('.hydro-metrics')).to_contain_text('预报洪峰')
        expect(workbench.locator('.hydro-metrics')).not_to_contain_text('纳什')
        with page.expect_download() as download:
            workbench.get_by_role('button', name='逐时过程 CSV', exact=True).click()
        download.value.save_as(str(output / 'forecast-process.csv'))
        exported = (output / 'forecast-process.csv').read_text(encoding='utf-8-sig')
        assert '预报流量' in exported and '实测流量' not in exported
        assert len(exported.splitlines()) == 7
        with page.expect_download() as download:
            workbench.get_by_role('button', name='预报摘要 CSV', exact=True).click()
        download.value.save_as(str(output / 'forecast-summary.csv'))
        assert dry['lineage']['forecastRainVersionId'] in (output / 'forecast-summary.csv').read_text(encoding='utf-8-sig')
        with page.expect_download() as download:
            workbench.get_by_role('button', name='报告包 ZIP', exact=True).click()
        download.value.save_as(str(output / 'forecast-reports.zip'))
        with zipfile.ZipFile(output / 'forecast-reports.zip') as archive:
            assert {'forecast-rain.json', 'forecast-end-state.json', 'request.json', 'result.json'}.issubset(archive.namelist())
            assert json.loads(archive.read('result.json')) == dry
        count = len(api('tasks')['tasks'])
        row.get_by_role('button', name='按原条件打开', exact=True).click()
        expect(page.locator('#forecast-history')).to_have_value(dry_task['taskId'], timeout=30000)
        expect(page.locator('#forecast-rain-product')).to_have_value(dry['lineage']['forecastRainVersionId'])
        expect(page.locator('.forecast-results .condition-note')).to_have_count(0, timeout=30000)
        assert len(api('tasks')['tasks']) == count
        check('archive-forecast-csv-report-package-and-reopen-exact-conditions')
        page.get_by_role('button', name='复制为下一次作业', exact=True).click()
        expect(page.locator('#forecast-issue-time')).to_have_value('2017-06-19T17:00')
        expect(page.locator('.run-button')).to_be_disabled()
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as submitted:
            page.get_by_role('button', name='补算至起报时刻', exact=True).click()
        assert submitted.value.status == 202, submitted.value.text()
        catchup = complete(submitted.value.json())
        assert catchup['parentStateVersionId'] == state['stateVersionId']
        assert catchup['stateTime'] == '2017-06-19T17:00:00'
        expect(page.locator('#forecast-state')).to_have_value(catchup['stateVersionId'], timeout=30000)
        expect(page.locator('.run-button')).to_be_disabled()
        check('next-issue-continues-observed-state-and-requires-new-rainfall-scenario')
        assert not report['errors'], report['errors']
        assert not report['failedResources'], report['failedResources']
        report['status'] = 'passed'
    except Exception as error:
        report['failure'] = str(error)
        (output / 'failure-ui.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        page.screenshot(path=str(output / 'failure.png'), full_page=True)
        raise
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
