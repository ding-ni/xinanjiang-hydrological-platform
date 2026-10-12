"""Exercise state initialization, SQL metadata, export and continuation in UI."""
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
report = {'status': 'failed', 'checks': [], 'tasks': {}, 'errors': []}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))

    def api(path):
        answer = context.request.get(args.url + '/api/model/' + path)
        assert answer.ok, answer.text()
        return answer.json()

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def submit(button, name):
        with page.expect_response(lambda r: r.url.endswith('/api/model/tasks') and r.request.method == 'POST') as response:
            page.get_by_role('button', name=button, exact=True).click()
        assert response.value.status == 202, response.value.text()
        task = response.value.json()
        report['tasks'][name] = task['taskId']
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            task = api('tasks/' + task['taskId'])
            if task['status'] not in {'queued', 'running', 'cancelling'}:
                break
            page.wait_for_timeout(500)
        assert task['status'] == 'completed', task
        result = api('tasks/' + task['taskId'] + '/result')
        expect(page.locator('.node-item.active')).to_have_attribute('title', result['stateVersionId'], timeout=20000)
        return task, result, api('states/' + result['stateVersionId'] + '/checkpoint')

    try:
        page.goto(args.url + '/#/state')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button', name='生成初始状态', exact=True)).to_be_visible()
        page.get_by_role('button', name='生成初始状态', exact=True).click()
        page.get_by_label('初始化方式', exact=True).select_option('OBSERVED_FLOW_ANCHORED')
        page.get_by_label('预热天数', exact=True).fill('1')
        page.get_by_label('计算至', exact=True).fill('2017-06-20T11:00')
        task, result, anchor = submit('开始初始化', 'anchored-initialization')
        assert task['runConfig']['initialization']['strategy'] == 'OBSERVED_FLOW_ANCHORED'
        assert task['runConfig']['period']['warmupDays'] == 1
        assert result['initialization']['anchor'] == anchor['initialization']['anchor']
        assert abs(result['initialization']['controlFlowCms'] - result['initialization']['anchor']['flowCms']) < 1e-9
        expect(page.locator('.selection-metrics')).to_contain_text('徐畈初始流量')
        check('observed-flow-initialization-submitted-and-displayed')

        state_id = result['stateVersionId']
        name = '六月洪水起算 · ' + state_id[-8:]
        page.get_by_label('状态名称', exact=True).fill(name)
        page.get_by_role('button', name='保存状态名称', exact=True).click()
        expect(page.locator('.node-item.active')).to_contain_text(name)
        page.get_by_role('button', name='收藏状态', exact=True).click()
        expect(page.get_by_role('button', name='取消收藏状态', exact=True)).to_be_visible()
        record = api('state-catalog')['metadata'][state_id]
        assert record['name'] == name and record['favorite'] is True
        assert api('states/' + state_id + '/checkpoint') == anchor
        page.reload()
        page.wait_for_load_state('networkidle')
        expect(page.get_by_label('状态名称', exact=True)).to_have_value(name, timeout=30000)
        expect(page.get_by_label('计算至', exact=True)).to_have_value('2017-06-20T12:00')
        page.get_by_label('检索状态', exact=True).fill(name)
        expect(page.locator('.node-item')).to_have_count(1)
        page.get_by_label('状态来源', exact=True).select_option('FIXED_WARMUP')
        expect(page.locator('.library-empty')).to_contain_text('没有匹配的状态')
        page.get_by_label('状态来源', exact=True).select_option('OBSERVED_FLOW_ANCHORED')
        expect(page.locator('.node-item')).to_have_count(1)
        page.get_by_role('button', name='收藏', exact=True).click()
        expect(page.locator('.node-item')).to_have_count(1)
        check('sql-name-favorite-reload-and-combined-search')

        with page.expect_download() as download:
            page.get_by_role('button', name='状态文件', exact=True).click()
        download.value.save_as(str(output / 'checkpoint.json'))
        assert json.loads((output / 'checkpoint.json').read_text(encoding='utf-8')) == anchor
        with page.expect_download() as download:
            page.get_by_role('button', name='网格 CSV', exact=True).click()
        download.value.save_as(str(output / 'grid-state.csv'))
        with (output / 'grid-state.csv').open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.DictReader(source))
        assert len(rows) == len(anchor['gridIds']) == 110
        for i, row in enumerate(rows):
            assert row['grid_id'] == anchor['gridIds'][i]
            for field, values in anchor['land'].items():
                assert float(row[field]) == values[i]
        check('checkpoint-json-and-110-grid-csv-match-model')
        page.screenshot(path=str(output / 'state-library-desktop.png'), full_page=True)

        page.get_by_label('检索状态', exact=True).fill('')
        page.get_by_label('状态来源', exact=True).select_option(label='全部来源')
        page.get_by_role('button', name='收藏', exact=True).click()
        page.get_by_role('button', name='用于续算', exact=True).click()
        page.get_by_label('计算至', exact=True).fill('2017-06-20T14:00')
        continued_task, continued_result, continued = submit('开始续算', 'continuation')
        assert continued_task['runConfig']['period']['initialStateVersionId'] == state_id
        assert continued['parentStateVersionId'] == state_id
        expect(page.get_by_label('计算至', exact=True)).to_have_value('2017-06-20T15:00')
        check('anchored-state-continuation-and-next-hour-ready')

        page.get_by_role('button', name='生成初始状态', exact=True).click()
        page.get_by_label('初始化方式', exact=True).select_option('FIXED_WARMUP')
        page.get_by_label('预热天数', exact=True).fill('1')
        page.get_by_label('计算至', exact=True).fill('2017-06-20T11:00')
        _, _, fixed = submit('开始初始化', 'rain-warmup')
        assert fixed['land'] == anchor['land']
        assert fixed['routing'] != anchor['routing']
        assert fixed['initialization']['strategy'] == 'FIXED_WARMUP'
        page.get_by_label('状态来源', exact=True).select_option('MODEL_END_STATE')
        expect(page.locator('.node-item', has_text='模型末状态').first).to_be_visible()
        page.get_by_label('状态来源', exact=True).select_option(label='全部来源')
        assert page.get_by_label('状态来源', exact=True).evaluate('el => el.selectedOptions[0].textContent') == '全部来源'
        expect(page.locator('.node-item.active')).to_be_in_viewport()
        check('rain-warmup-and-anchor-share-land-but-distinct-routing')

        for width in (1440, 390, 360):
            page.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
            page.wait_for_timeout(700)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            expect(page.locator('.map-body canvas').first).to_be_visible(timeout=30000)
            page.screenshot(path=str(output / f'state-{width}.png'), full_page=True)
        check('desktop-and-390-360-mobile-state-layouts')
        assert not report['errors'], report['errors']
        report['status'] = 'passed'
    finally:
        page.screenshot(path=str(output / 'last-state.png'), full_page=True)
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
