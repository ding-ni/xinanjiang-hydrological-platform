"""Verify real slope routing search, adoption, replay and durable UI results."""
import argparse
import csv
import json
import time
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
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
        answer = context.request.get(args.url + '/api/model/' + path)
        assert answer.ok, answer.text()
        return answer.json()

    def fill(field, value):
        field.fill(value)
        field.press('Tab')

    def submit(button):
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as answer:
            button.click()
        assert answer.value.status == 202, answer.value.text()
        return answer.value.json()

    def complete(task):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = api('tasks/' + task['taskId'])
            if status['status'] not in {'queued', 'running'}:
                assert status['status'] == 'completed', status
                return api('tasks/' + task['taskId'] + '/result')
            page.wait_for_timeout(500)
        raise AssertionError('Calculation timed out')

    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-cal')).to_be_visible()
        page.get_by_role('button', name='参数率定', exact=False).click()
        page.get_by_role('button', name='连续时段', exact=True).click()
        # Previous trials may have adopted a frozen scientific preset. This
        # separate search explicitly starts with editable current conditions.
        page.locator('#science-version').select_option('')
        expect(page.get_by_label('预热期天数', exact=True)).to_be_enabled()
        for selector, value in [('#cal-start', '2017-06-19T00:00'), ('#cal-end', '2017-06-19T23:00'),
                                ('#val-start', '2017-06-20T00:00'), ('#val-end', '2017-06-20T23:00'),
                                ('#ind-start', '2017-06-21T00:00'), ('#ind-end', '2017-06-21T23:00')]:
            fill(page.locator(selector), value)
        fill(page.get_by_label('预热期天数', exact=True), '1')
        page.locator('#calibration-target').select_option('routing')
        page.locator('#algorithm').select_option('DIFFERENTIAL_EVOLUTION')
        fill(page.locator('#search-seed'), '17')
        fill(page.locator('#max-evals'), '4')
        fill(page.locator('#population-size'), '4')
        expect(page.locator('#search-strategy')).to_have_count(0)
        editor = page.locator('.search-editor')
        if not editor.evaluate('el => el.open'):
            editor.locator('summary').click()
        expect(editor).to_contain_text('地表径流汇流时间')
        expect(editor).to_contain_text('壤中流汇流时间')
        expect(editor).to_contain_text('地下径流汇流时间')
        editor.get_by_role('button', name='全部固定', exact=True).click()
        automatic = page.get_by_role('button', name='确认率定任务', exact=True)
        expect(automatic).to_be_disabled()
        page.get_by_label('RS_k_hours 参与率定', exact=True).check()
        fill(page.get_by_label('RS_k_hours 搜索下限', exact=True), '0.5')
        fill(page.get_by_label('RS_k_hours 搜索上限', exact=True), '4')
        expect(automatic).to_be_enabled()
        (output / 'routing-controls.txt').write_text(editor.inner_text(), encoding='utf-8')
        versions = api('routing-versions')['versions']
        base = next(row for row in versions if row['routingVersionId'] == 'control-split-v2')
        check('existing-workbench-three-hydrological-parameters-and-single-selection')

        task = submit(automatic)
        report['taskId'] = task['taskId']
        frozen = task['runConfig']
        assert frozen['calibration']['target'] == 'routing'
        assert 'searchParameters' not in frozen['calibration']
        assert frozen['calibration']['routingSearchParameters'] == {'RS_k_hours': {'minimum': .5, 'maximum': 4}}
        result = complete(task)
        assert result['calibrationTarget'] == 'routing'
        assert result['bestParameters'] == frozen['calibration']['initialParameters']
        best = result['bestRoutingParameters']
        assert .5 <= best['default_slope_k_hours']['RS'] <= 4
        assert best['default_slope_k_hours']['RS'] != base['parameters']['default_slope_k_hours']['RS']
        for key in ('RI', 'RG'):
            assert best['default_slope_k_hours'][key] == base['parameters']['default_slope_k_hours'][key]
        for key in ('reach_muskingum', 'unit_component_slope_k_hours'):
            assert best[key] == base['parameters'][key]
        candidate = result['routingCandidate']
        report['routingVersionId'] = candidate['routingVersionId']
        versions = api('routing-versions')['versions']
        assert next(row for row in versions if row['routingVersionId'] == 'control-split-v2') == base
        assert next(row for row in versions if row['routingVersionId'] == candidate['routingVersionId']) == candidate
        assert [row['key'] for row in result['searchPlan']['rows'] if row['active']] == ['RS_k_hours']
        expect(page.locator('.search-plan')).to_be_visible(timeout=30000)
        page.locator('.search-plan summary').click()
        expect(page.get_by_role('table', name='本次搜索参数')).to_contain_text('地表径流汇流时间')
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='搜索参数表 CSV', exact=True).click()
        downloaded.value.save_as(str(output / 'search-parameters.csv'))
        with (output / 'search-parameters.csv').open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 4
        assert float(rows[1][-1]) == best['default_slope_k_hours']['RS']
        check('real-RS-search-fixed-runoff-channel-and-unit-parameters-new-version-CSV')

        page.wait_for_timeout(1800)
        other = browser.new_context(viewport={'width': 1440, 'height': 1000})
        other_page = other.new_page()
        other_page.goto(args.url + '/#/calibration')
        other_page.wait_for_load_state('networkidle')
        expect(other_page.locator('#calibration-target')).to_have_value('routing')
        expect(other_page.get_by_label('RS_k_hours 搜索下限', exact=True)).to_have_value('0.5')
        expect(other_page.get_by_label('RI_k_hours 参与率定', exact=True)).not_to_be_checked()
        expect(other_page.locator('.search-plan')).to_be_visible()
        other.close()
        check('fresh-browser-SQL-restores-routing-selection-and-results')

        for width in (1440, 390, 360):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.wait_for_timeout(500)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
            assert page.locator('.process-panel').evaluate("el => el.querySelector('canvas').getBoundingClientRect().bottom <= el.getBoundingClientRect().bottom + 1")
            page.screenshot(path=str(output / f'routing-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1000})
        check('desktop-390-and-360-layout-with-real-result')

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
        assert saved['request']['calibration']['target'] == 'routing'
        row.get_by_role('button', name='按原条件打开', exact=True).click()
        expect(page.locator('#calibration-target')).to_have_value('routing')
        expect(page.get_by_label('RS_k_hours 搜索下限', exact=True)).to_have_value('0.5')
        check('archive-result-JSON-and-original-routing-search-handoff')

        page.get_by_role('button', name='采用产汇流方案', exact=True).click()
        page.locator('#calibration-target').select_option('runoff')
        manual_task = submit(page.get_by_role('button', name='试算并叠加', exact=True))
        assert manual_task['runConfig']['routing']['versionId'] == candidate['routingVersionId']
        assert manual_task['runConfig']['calibration']['initialParameters'] == result['bestParameters']
        reproduced = complete(manual_task)
        for key in result['hydrographs']:
            assert reproduced['hydrographs'][key] == result['hydrographs'][key], key
        report['replayTaskId'] = manual_task['taskId']
        check('adopt-complete-scheme-and-exact-real-model-replay')
        page.goto(args.url + '/#/config')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='水雨情资料', exact=True).click()
        expect(page.get_by_role('button', name='生成降雨整编产品', exact=True)).to_be_visible()
        page.get_by_role('button', name='模型方案', exact=True).click()
        expect(page.locator('.scheme-note')).to_contain_text('面雨量：按控制区面积加权')
        expect(page.locator('input[value="三分量线性水库 · 全域与单元参数"]')).to_be_visible()
        check('hydrological-terms-across-input-processing-and-routing-scheme')
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
