"""Exercise joint search, scientific adoption and replay in the real Vue UI."""
import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', default='http://127.0.0.1:18891')
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'errors': [], 'failedResources': []}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    page.on('response', lambda response: report['failedResources'].append(response.url) if response.status >= 400 else None)

    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, response.text()
        return response.json()

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def fill(locator, value):
        locator.fill(value)
        locator.press('Tab')

    def submit(button):
        with page.expect_response(lambda response: response.url.endswith('/api/model/tasks') and response.request.method == 'POST') as response:
            button.click()
        assert response.value.status == 202, response.value.text()
        return response.value.json()

    def completed(task):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = api('tasks/' + task['taskId'])
            if status['status'] not in {'queued', 'running'}:
                assert status['status'] == 'completed', status
                return api('tasks/' + task['taskId'] + '/result')
            page.wait_for_timeout(500)
        raise AssertionError('Task timed out')

    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-cal')).to_be_visible()
        (output / 'initial-dom.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        page.get_by_role('button', name='参数率定', exact=False).click()
        page.get_by_role('button', name='连续时段', exact=True).click()
        for selector, value in [('#cal-start', '2017-06-19T00:00'), ('#cal-end', '2017-06-19T23:00'),
                                ('#val-start', '2017-06-20T00:00'), ('#val-end', '2017-06-20T23:00'),
                                ('#ind-start', '2017-06-21T00:00'), ('#ind-end', '2017-06-21T23:00')]:
            fill(page.locator(selector), value)
        page.locator('#science-version').select_option('')
        fill(page.get_by_label('预热期天数', exact=True), '1')
        page.locator('#calibration-target').select_option('joint')
        page.locator('#joint-profile').select_option('reference-16')
        expect(page.locator('#search-strategy')).to_have_count(0)
        editors = page.locator('.search-editor')
        expect(editors).to_have_count(2)
        for index in range(2):
            editor = editors.nth(index)
            if not editor.evaluate('element => element.open'):
                editor.locator('summary').click()
            editor.get_by_role('button', name='全部固定', exact=True).click()
        automatic = page.get_by_role('button', name='确认率定任务', exact=True)
        expect(automatic).to_be_disabled()
        page.get_by_label('SM 参与率定', exact=True).check()
        page.get_by_label('RS_k_hours 参与率定', exact=True).check()
        fill(page.get_by_label('RS_k_hours 搜索下限', exact=True), '0.05')
        fill(page.get_by_label('RS_k_hours 搜索上限', exact=True), '3')
        page.locator('#algorithm').select_option('DIFFERENTIAL_EVOLUTION')
        fill(page.locator('#max-evals'), '4')
        fill(page.locator('#population-size'), '4')
        fill(page.locator('#search-seed'), '17')
        preview = page.locator('.submission-plan')
        if not preview.evaluate('element => element.open'):
            preview.locator('summary').click()
        expect(preview).to_contain_text('当前方案重新评价计入预算')
        expect(preview).to_contain_text('模型摘要')
        expect(automatic).to_be_enabled()
        check('joint-two-search-editors-current-values-and-frozen-preview')
        task = submit(automatic)
        report['taskId'] = task['taskId']
        frozen = task['runConfig']
        assert frozen['calibration']['target'] == 'joint'
        assert set(frozen['calibration']['searchParameters']) == {'SM'}
        assert set(frozen['calibration']['routingSearchParameters']) == {'RS_k_hours'}
        for key in ('modelImplementationSHA256', 'scienceConfigurationSHA256', 'petConfigurationSHA256'):
            assert len(frozen['lineage'][key]) == 64
        result = completed(task)
        assert result['optimizer']['baselineReevaluated']
        assert result['optimizer']['evaluations'] == 4
        assert result['optimizer']['best_loss'] <= result['optimizer']['stages'][0]['initialLoss']
        assert result['evaluationPolicy'] == 'EQUAL_PHYSICAL_GROUP_UNIQUE_HOURS'
        expect(page.locator('.search-plan')).to_be_visible(timeout=30000)
        (output / 'frozen-result.json').write_text(json.dumps({'request': frozen, 'result': result}, ensure_ascii=False, indent=2), encoding='utf-8')
        check('real-joint-run-persistent-complete-candidate-and-budget-evidence')
        page.get_by_role('button', name='采用产汇流方案', exact=True).click()
        expect(page.locator('#science-version')).to_have_value(result['science']['versionId'])
        expect(page.get_by_label('预热期天数', exact=True)).to_be_disabled()
        science = next(row for row in api('science-versions')['versions'] if row['scienceVersionId'] == result['science']['versionId'])
        # A task's 1-day warm-up differs from the registered default. Explicit
        # candidate adoption must preserve the task's actual warm-up to replay.
        report['adoptedWarmupDays'] = page.get_by_label('预热期天数', exact=True).input_value()
        manual = submit(page.get_by_role('button', name='试算并叠加', exact=True))
        assert manual['runConfig']['routing']['versionId'] == result['routingCandidate']['routingVersionId']
        assert manual['runConfig']['science']['versionId'] == result['science']['versionId']
        assert manual['runConfig']['calibration']['initialParameters'] == result['bestParameters']
        replay = completed(manual)
        report['replayTaskId'] = manual['taskId']
        assert replay['hydrographs'] == result['hydrographs'], 'Adoption changed the candidate warm-up/trajectory'
        check('adopt-runoff-routing-science-and-exact-manual-replay')
        for width in (1440, 390, 360):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
            page.screenshot(path=str(output / f'joint-{width}.png'), full_page=True)
        check('desktop-and-mobile-layout')
        fresh = browser.new_context(viewport={'width': 1440, 'height': 1000})
        refreshed = fresh.new_page()
        refreshed.goto(args.url + '/#/calibration')
        refreshed.wait_for_load_state('networkidle')
        expect(refreshed.locator('#science-version')).to_have_value(result['science']['versionId'])
        fresh.close()
        check('new-browser-restores-scientific-selection')
        assert not report['errors'], report['errors']
        assert not report['failedResources'], report['failedResources']
        report['status'] = 'passed'
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            page.screenshot(path=str(output / 'last-ui.png'), full_page=True, timeout=5000)
        except Exception as screenshot_error:
            report['screenshotError'] = str(screenshot_error)
            (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            browser.close()
