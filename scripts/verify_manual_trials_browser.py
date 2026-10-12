"""Exercise actual manual parameter trials and checkpoint catchup in the UI."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

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

    def complete(task):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = api('tasks/' + task['taskId'])
            if status['status'] not in {'queued', 'running'}:
                assert status['status'] == 'completed', status
                return api('tasks/' + task['taskId'] + '/result')
            page.wait_for_timeout(500)
        raise AssertionError('Calculation timed out')

    def fill(selector, value):
        page.locator(selector).fill(value)
        page.locator(selector).press('Tab')

    def submit_trial(sm):
        fill('#parameter-SM', sm)
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as response:
            page.get_by_role('button', name='试算并叠加', exact=True).click()
        assert response.value.status == 202, response.value.text()
        task = response.value.json()
        result = complete(task)
        assert result['evaluationMode'] == 'manual'
        assert result['bestParameters']['SM'] == float(sm)
        assert result['optimizer']['evaluations'] == 1
        expect(page.get_by_role('button', name='试算并叠加', exact=True)).to_be_enabled(timeout=30000)
        return task, result

    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-cal')).to_be_visible()
        (output / 'initial-ui.txt').write_text(page.locator('.view-cal').inner_text(), encoding='utf-8')
        page.get_by_role('button', name='参数率定', exact=False).click()
        page.get_by_role('button', name='连续时段', exact=True).click()
        page.locator('#science-version').select_option('')
        expect(page.get_by_label('预热期天数', exact=True)).to_be_enabled()
        for selector, value in [('#cal-start', '2017-06-19T00:00'), ('#cal-end', '2017-06-19T23:00'),
                                ('#val-start', '2017-06-20T00:00'), ('#val-end', '2017-06-20T23:00'),
                                ('#ind-start', '2017-06-21T00:00'), ('#ind-end', '2017-06-21T23:00')]:
            fill(selector, value)
        page.get_by_label('预热期天数', exact=True).fill('1')
        page.get_by_label('预热期天数', exact=True).press('Tab')
        expect(page.locator('.param-row').filter(has=page.locator('#parameter-SM'))).to_contain_text('自由水蓄水容量')
        expect(page.locator('.param-row').filter(has=page.locator('#parameter-KC'))).to_contain_text('蒸散发折算系数')
        check('Chinese-parameter-names-symbols-and-units')
        task1, first = submit_trial('20')
        task2, second = submit_trial('60')
        report['trialTaskIds'] = [task1['taskId'], task2['taskId']]
        assert api('tasks/' + task1['taskId'] + '/result') == first
        assert first['hydrographs']['CALIBRATION']['forecast'] != second['hydrographs']['CALIBRATION']['forecast']
        expect(page.locator('.trial-ledger')).to_contain_text('SM 20 → 60')
        expect(page.locator('.figure-legend')).to_contain_text('基准曲线')
        expect(page.locator('.hydro canvas').first).to_be_visible()
        assert page.locator('.process-panel').evaluate("el => el.querySelector('.trial-ledger').getBoundingClientRect().bottom <= el.getBoundingClientRect().bottom + 1"), 'Trial controls clipped'
        page.locator('.process-panel').screenshot(path=str(output / 'overlaid-process.png'))
        check('two-real-SM-trials-retain-original-and-overlay-new-curve')
        # Reload while keeping SQL backed records; no new task is submitted.
        page.wait_for_timeout(1600)
        page.reload()
        page.wait_for_load_state('networkidle')
        expect(page.locator('.trial-ledger')).to_contain_text('SM 20 → 60', timeout=30000)
        expect(page.locator('.trial-score').last).not_to_have_text('NSE —', timeout=30000)
        check('trial-history-and-curves-recover-after-refresh')
        previous_count = len(api('tasks')['tasks'])
        page.locator('.trial-entry').last.get_by_role('button', name='采用参数').click()
        expect(page.locator('#parameter-SM')).to_have_value('60')
        assert len(api('tasks')['tasks']) == previous_count
        fill('#parameter-WM', '50')
        expect(page.get_by_role('button', name='试算并叠加', exact=True)).to_be_disabled()
        expect(page.locator('.manual-trial-actions')).to_contain_text('WM 须大于')
        page.locator('.trial-entry').last.get_by_role('button', name='采用参数').click()
        check('adopt-trial-without-running-and-show-physical-constraint')
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.wait_for_timeout(600)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
            page.screenshot(path=str(output / f'manual-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1000})

        page.wait_for_timeout(1600)
        context.close()
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.on('response', lambda r: report['failedResources'].append(r.url) if r.status >= 400 else None)
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.trial-ledger')).to_contain_text('SM 20 → 60', timeout=30000)
        check('fresh-browser-recovers-trial-ledger-from-SQL')

        # Generate a real checkpoint, then advance its exact missing three hours.
        page.goto(args.url + '/#/state')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='生成初始状态', exact=True).click()
        fill('#state-target-time', '2017-06-21T00:00')
        fill('#state-warmup-days', '1')
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as response:
            page.get_by_role('button', name='开始初始化', exact=True).click()
        assert response.value.status == 202, response.value.text()
        initial = complete(response.value.json())
        expect(page.get_by_role('button', name='开始初始化', exact=True)).to_be_visible(timeout=30000)
        page.goto(args.url + '/#/forecast')
        page.wait_for_load_state('networkidle')
        field = page.locator('input[type=datetime-local]').first
        field.fill('2017-06-21T03:00')
        field.press('Tab')
        state_select = page.locator('select').filter(has=page.locator(f'option[value="{initial["stateVersionId"]}"]'))
        state_select.select_option(initial['stateVersionId'])
        expect(page.get_by_role('button', name='补算至起报时刻', exact=True)).to_be_enabled()
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as response:
            page.get_by_role('button', name='补算至起报时刻', exact=True).click()
        assert response.value.status == 202, response.value.text()
        catchup_task = response.value.json()
        assert catchup_task['runConfig']['period']['startTime'] == '2017-06-21T01:00'
        assert catchup_task['runConfig']['period']['endTime'] == '2017-06-21T03:00'
        assert catchup_task['runConfig']['period']['initialStateVersionId'] == initial['stateVersionId']
        completed = complete(catchup_task)
        expect(page.get_by_role('button', name='补算至起报时刻', exact=True)).to_have_count(0, timeout=30000)
        expect(page.locator('.view-forecast')).to_contain_text('已衔接起报时刻')
        report['catchupTaskId'] = catchup_task['taskId']
        assert completed['parentStateVersionId'] == initial['stateVersionId']
        page.screenshot(path=str(output / 'catchup-forecast.png'), full_page=True)
        check('checkpoint-catchup-exact-hours-lineage-and-no-duplicate-action')
        assert not report['errors'], report['errors']
        assert not report['failedResources'], report['failedResources']
        report['status'] = 'passed'
    except Exception as error:
        report['failure'] = str(error)
        page.screenshot(path=str(output / 'failure.png'), full_page=True)
        raise
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
