"""Exercise the SQLite-to-model workflow against an isolated local service."""
import argparse
import csv
import json
import re
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18887')
parser.add_argument('--runtime', required=True)
parser.add_argument('--probe', action='store_true')
args = parser.parse_args()
output = Path(args.runtime).resolve() / 'browser'
output.mkdir(parents=True, exist_ok=True)
summary = {'status': 'failed', 'checks': [], 'errors': [], 'tasks': {}}


def check(name):
    summary['checks'].append(name)
    print('PASS', name, flush=True)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda error: summary['errors'].append(str(error)))

    def api(resource):
        response = context.request.get(args.url + '/api/model/' + resource)
        assert response.status == 200, (response.status, response.text())
        return response.json()

    def wait_task(task_id, kind='tasks'):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            task = api(kind + '/' + task_id)
            if task['status'] not in {'queued', 'running', 'cancelling'}:
                assert task['status'] in {'completed', 'archived'}, task
                return api(kind + '/' + task_id + '/result')
            time.sleep(0.5)
        raise AssertionError('Timed out: ' + task_id)

    def nonblank(locator, filename):
        from PIL import Image
        expect(locator).to_be_visible(timeout=60000)
        locator.screenshot(path=str(output / filename))
        pixels = Image.open(output / filename).convert('RGB')
        assert len(pixels.getcolors(pixels.width * pixels.height) or []) > 100

    try:
        page.goto(args.url + '/#/data')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('heading', name='水文资料库', exact=True)).to_be_visible(timeout=60000)
        expect(page.locator('.hydrograph canvas')).to_be_visible(timeout=60000)
        page.screenshot(path=str(output / 'desktop-observations.png'), full_page=True)
        if args.probe:
            print(page.locator('body').inner_text()[:18000], flush=True)
            print('LABELS', page.locator('label').all_text_contents(), flush=True)
            database = api('database/summary')
            assert database['events'] == 226 and database['sourceFiles'] == 128
            assert database['stations'] == 12
            nonblank(page.locator('.hydrograph canvas'), 'rain-canvas.png')
            summary['existingTasks'] = [row['taskId'] for row in api('tasks')['tasks']]
            check('readonly-daily-sql-and-observation-chart')
            diagnostic = context.new_page()
            diagnostic.on('pageerror', lambda error: summary['errors'].append(str(error)))
            diagnostic.goto(args.url + '/#/diagnostics')
            expect(diagnostic.get_by_role('heading', name='模型诊断', exact=True)).to_be_visible(timeout=60000)
            expect(diagnostic.get_by_label('RS 坡面 K（h）')).to_be_enabled(timeout=60000)
            expect(diagnostic.locator('.topology-map canvas')).to_be_visible(timeout=60000)
            diagnostic.screenshot(path=str(output / 'daily-diagnostic-desktop.png'), full_page=True)
            diagnostic.set_viewport_size({'width': 390, 'height': 844})
            diagnostic.wait_for_timeout(1000)
            assert diagnostic.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            diagnostic.screenshot(path=str(output / 'daily-diagnostic-mobile.png'), full_page=True)
            diagnostic.close()
            check('readonly-daily-new-workbench-with-legacy-tasks')
            state_page = context.new_page()
            state_page.on('pageerror', lambda error: summary['errors'].append(str(error)))
            state_page.goto(args.url + '/#/state')
            state_page.wait_for_load_state('networkidle')
            expect(state_page.get_by_label('状态名称', exact=True)).to_be_visible(timeout=30000)
            expect(state_page.locator('.node-item.active')).to_have_count(1)
            state_id = state_page.locator('.node-item.active').get_attribute('title')
            assert isinstance(api('state-catalog')['metadata'], dict)
            with state_page.expect_download() as state_download:
                state_page.get_by_role('button', name='状态文件', exact=True).click()
            checkpoint_path = output / 'existing-checkpoint.json'
            state_download.value.save_as(str(checkpoint_path))
            assert json.loads(checkpoint_path.read_text(encoding='utf-8')) == api('states/' + state_id + '/checkpoint')
            state_page.get_by_role('button', name='生成初始状态', exact=True).click()
            state_page.get_by_label('初始化方式', exact=True).select_option('OBSERVED_FLOW_ANCHORED')
            expect(state_page.get_by_label('初始化方式', exact=True)).to_have_value('OBSERVED_FLOW_ANCHORED')
            state_page.screenshot(path=str(output / 'daily-state-desktop.png'), full_page=True)
            state_page.set_viewport_size({'width': 390, 'height': 844})
            state_page.wait_for_timeout(500)
            assert state_page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            state_page.screenshot(path=str(output / 'daily-state-mobile.png'), full_page=True)
            state_page.close()
            assert summary['existingTasks'] == [row['taskId'] for row in api('tasks')['tasks']]
            check('readonly-daily-state-library-export-and-initialization-controls')
            assert not summary['errors'], summary['errors']
            summary['status'] = 'probe'
        else:
            database = api('database/summary')
            assert database['events'] == 226
            assert database['sourceFiles'] == 128
            nonblank(page.locator('.hydrograph canvas'), 'rain-canvas.png')
            check('fresh-unified-entry-uses-real-database')
            with page.expect_download() as download:
                page.get_by_role('button', name='导出逐时资料', exact=True).click()
            download.value.save_as(str(output / 'rain-observations.csv'))
            with (output / 'rain-observations.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            actual = api('database/window?stationId=70149600&variable=rain&start=2017-06-22T00:00&end=2017-06-28T23:00')
            assert len(rows) == actual['expected'] == 168
            assert [float(row['value']) if row['value'] else None for row in rows] == [row['value'] for row in actual['series']]
            check('ui-csv-matches-sql-hourly-values')
            for label, heading, filename in [('水位', re.compile(r'^徐畈(?:（二）)? · 水位过程$'), 'level-canvas.png'), ('流量', re.compile(r'^徐畈(?:（二）)? · 流量过程$'), 'flow-canvas.png')]:
                page.get_by_role('button', name=label, exact=True).click()
                expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible(timeout=60000)
                nonblank(page.locator('.hydrograph canvas'), filename)
            check('ui-queries-real-level-and-flow')
            page.get_by_role('button', name='洪水场次', exact=True).click()
            expect(page.locator('.event-section tbody tr')).to_have_count(226)
            page.get_by_role('button', name='查看实测过程', exact=True).first.click()
            expect(page.get_by_role('heading', name=re.compile(r'^徐畈(?:（二）)? · 流量过程$'))).to_be_visible(timeout=60000)
            check('event-catalog-opens-observed-hydrograph')

            page.get_by_role('button', name='降雨整编与空间化', exact=True).click()
            name = '库内雨量验收 ' + str(time.time_ns())
            page.locator('.source-form').get_by_label('资料名称', exact=True).fill(name)
            page.locator('.source-form').get_by_label('开始', exact=True).fill('2017-06-22T00:00')
            page.locator('.source-form').get_by_label('结束', exact=True).fill('2017-06-28T23:00')
            with page.expect_response(lambda r: r.url.endswith('/database/rain-source') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='登记为加工资料', exact=True).click()
            assert response.value.status == 200, response.value.text()
            source = response.value.json()
            assert source['stationCount'] == 5
            expect(page.get_by_label('已登记站点雨量', exact=True)).to_have_value(source['sourceId'])
            page.get_by_label('新产品名称', exact=True).fill(name + ' 网格降雨')
            with page.expect_response(lambda r: r.url.endswith('/input-tasks') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='生成降雨整编产品', exact=True).click()
            assert response.value.status == 202, response.value.text()
            task = response.value.json()
            summary['tasks']['stationRainProcessing'] = task['taskId']
            wait_task(task['taskId'], 'input-tasks')
            processing_row = page.locator('table[aria-label="资料加工任务"] tbody tr').filter(has_text=task['taskId'])
            with page.expect_response(lambda r: r.url.endswith('/' + task['taskId'] + '/register') and r.request.method == 'POST') as response:
                processing_row.get_by_role('button', name='校验并登记结果', exact=True).click(timeout=60000)
            assert response.value.status in {200, 201}, response.value.text()
            version = response.value.json()
            summary['inputBundleVersionId'] = version['bundleVersionId']
            version_row = page.locator('table[aria-label="已登记输入版本"] tbody tr').filter(has_text=version['bundleVersionId'])
            version_row.get_by_role('button', name='选用此版本', exact=True).click(timeout=60000)
            expect(version_row.get_by_role('button', name='当前选用', exact=True)).to_be_visible(timeout=60000)
            page.screenshot(path=str(output / 'desktop-rain-processing.png'), full_page=True)
            check('sql-source-backend-spatialization-register-select')

            page.goto(args.url + '/#/calibration')
            page.get_by_role('button', name='参数率定', exact=False).click()
            # This uses the new rain product, rather than replaying a previous
            # candidate that is bound to its original immutable input bundle.
            page.locator('#cal-version').select_option('NANANXI_XAJ_REFERENCE_V1')
            expect(page.locator('#cal-version')).to_be_enabled(timeout=60000)
            expect(page.locator('#cal-version')).to_have_value('NANANXI_XAJ_REFERENCE_V1')
            page.locator('#science-version').select_option('')
            page.get_by_role('button', name='连续模拟', exact=False).first.click()
            expect(page.get_by_label('预热期（天）', exact=True)).to_be_enabled()
            page.get_by_label('开始时间', exact=True).fill('2017-06-23T00:00')
            page.get_by_label('结束时间', exact=True).fill('2017-06-24T23:00')
            page.get_by_label('预热期（天）', exact=True).fill('1')
            page.get_by_label('初始状态版本', exact=True).fill('')
            page.get_by_label('结束时间', exact=True).blur()
            with page.expect_response(lambda r: r.url.endswith('/tasks') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='确认模拟任务', exact=True).click(timeout=60000)
            assert response.value.status == 202, response.value.text()
            simulation = response.value.json()
            result = wait_task(simulation['taskId'])
            summary['tasks']['simulation'] = simulation['taskId']
            assert result['lineage']['inputBundleVersionId'] == version['bundleVersionId']
            assert result['productionEligible'] is False
            expect(page.get_by_role('button', name='保存为起算状态', exact=True)).to_be_visible(timeout=60000)
            nonblank(page.locator('.hydro canvas').first, 'simulation-canvas.png')
            page.screenshot(path=str(output / 'desktop-simulation.png'), full_page=True)
            expect(page.locator('.result-summary b').first).not_to_have_text('')
            page.set_viewport_size({'width': 390, 'height': 844})
            nonblank(page.locator('.hydro canvas').first, 'mobile-simulation-canvas.png')
            page.screenshot(path=str(output / 'mobile-simulation.png'), full_page=True)
            assert page.locator('.view-cal').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            check('ui-simulation-consumes-selected-sql-rain-product')

            page.goto(args.url + '/#/data')
            page.get_by_role('button', name='计算方案', exact=True).click()
            page.get_by_label('已完成的计算', exact=True).select_option(simulation['taskId'], timeout=60000)
            scheme_name = '两日模拟验收 ' + str(time.time_ns())
            page.get_by_label('方案名称', exact=True).fill(scheme_name)
            with page.expect_response(lambda r: r.url.endswith('/database/schemes') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='保存方案', exact=True).click()
            assert response.value.status == 200, response.value.text()
            scheme = response.value.json()
            summary['schemeId'] = scheme['id']
            scheme_row = page.locator('.scheme-section tbody tr').filter(has_text=scheme_name)
            scheme_row.get_by_role('button', name='按方案计算', exact=True).click(timeout=60000)
            with page.expect_response(lambda r: r.url.endswith('/schemes/' + scheme['id'] + '/run') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='开始计算', exact=True).click()
            assert response.value.status == 200, response.value.text()
            rerun = response.value.json()
            repeated = wait_task(rerun['taskId'])
            assert repeated['lineage']['inputBundleVersionId'] == version['bundleVersionId']
            summary['tasks']['schemeRerun'] = rerun['taskId']
            page.screenshot(path=str(output / 'desktop-schemes.png'), full_page=True)
            check('ui-save-and-rerun-frozen-scheme')

            page.goto(args.url + '/#/realtime')
            picker = page.get_by_label('测站过程', exact=True)
            expect(picker).to_be_visible(timeout=60000)
            expect(page.locator('.time-picker input')).to_be_enabled(timeout=60000)
            page.locator('.time-picker input').fill('2017-06-24T23:00')
            page.locator('.time-picker input').blur()
            options = picker.locator('option').evaluate_all('(rows) => rows.map(row => ({value: row.value, label: row.textContent}))')
            outlet = next(row for row in options if '徐畈' in row['label'] and ('水文' in row['label'] or '断面' in row['label']))
            picker.select_option(outlet['value'])
            expect(page.locator('.station-detail').get_by_role('heading', name='水文过程', exact=True)).to_be_visible(timeout=60000)
            expect(page.locator('.station-detail canvas')).to_have_count(2, timeout=60000)
            for index in range(2):
                nonblank(page.locator('.station-detail canvas').nth(index), 'station-' + str(index) + '-canvas.png')
            page.screenshot(path=str(output / 'desktop-rain-water-monitor.png'), full_page=True)
            page.get_by_role('link', name='查询该站完整过程', exact=True).click()
            expect(page.get_by_role('heading', name=re.compile(r'^徐畈(?:（二）)? · 流量过程$'))).to_be_visible(timeout=60000)
            check('monitor-real-flow-level-and-station-query-link')

            page.set_viewport_size({'width': 390, 'height': 844})
            nonblank(page.locator('.hydrograph canvas'), 'mobile-observation-canvas.png')
            page.screenshot(path=str(output / 'mobile-observations.png'), full_page=True)
            assert page.locator('.data-workspace').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            page.get_by_role('button', name='降雨整编与空间化', exact=True).click()
            page.screenshot(path=str(output / 'mobile-processing.png'), full_page=True)
            assert page.locator('.data-workspace').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            check('mobile-observations-processing-contained')
            assert not summary['errors'], summary['errors']
            summary['status'] = 'passed'
    except Exception as error:
        summary['failure'] = str(error)
        page.screenshot(path=str(output / 'failure.png'), full_page=True)
        print(page.locator('body').inner_text()[:18000], flush=True)
        raise
    finally:
        (output / 'database-browser.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        context.close()
        browser.close()
