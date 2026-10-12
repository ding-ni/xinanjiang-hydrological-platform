"""Exercise the routing workbench against isolated real model evidence."""
import argparse
import json
import time
import zipfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18888')
parser.add_argument('--runtime', required=True)
parser.add_argument('--output')
args = parser.parse_args()
runtime = Path(args.runtime).resolve()
output = Path(args.output).resolve() if args.output else runtime / 'browser'
output.mkdir(parents=True, exist_ok=True)
evidence = json.loads((runtime / 'unit-routing-acceptance.json').read_text(encoding='utf-8'))
report = {'status': 'failed', 'checks': [], 'errors': [], 'tasks': {}}


def checked(name):
    report['checks'].append(name)
    print('PASS', name, flush=True)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))

    def api(resource):
        response = context.request.get(args.url + '/api/model/' + resource)
        assert response.ok, (response.status, response.text())
        return response.json()

    def download(button, filename):
        with page.expect_download() as result:
            button.click()
        result.value.save_as(str(output / filename))
        return json.loads((output / filename).read_text(encoding='utf-8'))

    def map_pixels(filename):
        canvas = page.locator('.topology-map canvas')
        expect(canvas).to_be_visible()
        page.wait_for_timeout(1000)
        canvas.screenshot(path=str(output / filename))
        pixels = Image.open(output / filename).convert('RGB')
        colors = pixels.getcolors(pixels.width * pixels.height) or []
        assert len(colors) > 100, len(colors)
        thematic = [(x, y) for y in range(pixels.height) for x in range(pixels.width)
                    if not (x >= pixels.width - 112 and y >= pixels.height - 32)
                    if ((lambda r, g, b: (g - r > 20 and g - b > 10) or (b - r > 35 and b - g > 5))(*pixels.getpixel((x, y))))]
        assert thematic, 'No river/basin pixels'
        assert min(x for x, _ in thematic) >= 20 and max(x for x, _ in thematic) < pixels.width - 20
        assert min(y for _, y in thematic) >= 20 and max(y for _, y in thematic) < pixels.height - 20

    def contained():
        dimensions = page.evaluate('({width:innerWidth, body:document.body.scrollWidth, root:document.documentElement.scrollWidth})')
        assert max(dimensions['body'], dimensions['root']) <= dimensions['width'] + 1, dimensions
        assert page.locator('.diagnostic-view').evaluate('(el)=>el.scrollWidth <= el.clientWidth + 1')

    try:
        page.goto(args.url + '/#/diagnostics')
        expect(page.get_by_role('heading', name='模型诊断', exact=True)).to_be_visible(timeout=30000)
        expect(page.get_by_label('RS 坡面 K（h）')).to_be_enabled(timeout=30000)
        product = api('spatial-products')
        assert len(product['geometry']['features']) == 110
        assert len(product['topology']['reaches']) == 32
        map_pixels('desktop-map.png')
        contained()
        page.screenshot(path=str(output / 'desktop-workbench.png'), full_page=True)
        checked('desktop-real-map-and-contained-layout')

        base = page.get_by_label('已登记汇流版本').input_value()
        page.get_by_role('tab', name='汇流单元', exact=True).click()
        unit_id = page.get_by_label('选择汇流单元').input_value()
        source_k = page.get_by_label(unit_id + ' RS K', exact=True).input_value()
        page.get_by_label('地表径流独立 K', exact=True).check()
        page.get_by_label(unit_id + ' RS K', exact=True).fill('8')
        expect(page.get_by_role('button', name='选用此版本', exact=True)).to_be_disabled()
        draft = download(page.get_by_role('button', name='导出汇流参数 JSON', exact=True), 'draft.json')
        assert draft['draft'] is True and draft['parameters']['unit_component_slope_k_hours'][unit_id]['RS'] == 8
        page.get_by_label('壤中径流独立 K', exact=True).check()
        page.get_by_label('地表径流独立 K', exact=True).uncheck()
        expect(page.get_by_label(unit_id + ' RS K', exact=True)).to_be_disabled()
        assert page.get_by_label(unit_id + ' RS K', exact=True).input_value() == source_k
        page.get_by_role('button', name='单元恢复全域参数', exact=True).click()
        expect(page.get_by_label('壤中径流独立 K', exact=True)).not_to_be_checked()
        page.get_by_label('检索汇流单元').fill('not-a-unit')
        expect(page.get_by_text('没有匹配的汇流单元', exact=True)).to_be_visible()
        page.get_by_label('检索汇流单元').fill('R0020_UP')
        expect(page.get_by_label('选择汇流单元').locator('option')).to_have_count(1)
        page.get_by_label('检索汇流单元').fill('')
        checked('unit-independent-K-fallback-reset-search-draft-export')

        page.get_by_role('tab', name='全域默认', exact=True).click()
        page.get_by_label('RS 坡面 K（h）').fill('1.7')
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.get_by_label('已登记汇流版本').select_option(evidence['routingVersionId'])
        assert page.get_by_label('已登记汇流版本').input_value() == base
        assert page.get_by_label('RS 坡面 K（h）').input_value() == '1.7'
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_label('已登记汇流版本').select_option(evidence['routingVersionId'])
        assert page.get_by_label('已登记汇流版本').input_value() == evidence['routingVersionId']
        checked('dirty-version-switch-cancel-and-confirm')

        page.get_by_label('新版本名称').fill('Browser unit K ' + str(time.time_ns()))
        page.get_by_role('tab', name='汇流单元', exact=True).click()
        unit_id = page.get_by_label('选择汇流单元').input_value()
        page.get_by_label('地表径流独立 K', exact=True).check()
        page.get_by_label(unit_id + ' RS K', exact=True).fill('8.25')
        with page.expect_response(lambda response: '/routing-versions' in response.url and response.request.method == 'POST') as response:
            page.get_by_role('button', name='登记新版本', exact=True).click()
        assert response.value.status == 201, response.value.text()
        registered = response.value.json()
        expect(page.locator('.message')).to_contain_text('尚未选用')
        saved = download(page.get_by_role('button', name='导出汇流参数 JSON', exact=True), 'registered.json')
        assert saved['routingVersionId'] == registered['routingVersionId']
        assert saved['parameters']['unit_component_slope_k_hours'][unit_id]['RS'] == 8.25
        page.get_by_role('button', name='选用此版本', exact=True).click()
        page.reload()
        expect(page.get_by_label('已登记汇流版本')).to_have_value(registered['routingVersionId'], timeout=30000)
        checked('register-select-export-reload-unit-version')

        exported = download(page.get_by_role('button', name='导出空间产品 JSON', exact=True), 'spatial.json')
        assert len(exported['geometry']['features']) == 110 and len(exported['topology']['reaches']) == 32
        page.get_by_role('tab', name='河段参数', exact=True).click()
        expect(page.locator('table[aria-label="河段汇流参数"] tbody tr')).to_have_count(32)
        page.get_by_label('检索河段').fill('R0020')
        assert page.locator('table[aria-label="河段汇流参数"] tbody tr').count() < 32
        page.get_by_label('检索河段').fill('')
        page.get_by_label('R0001 K', exact=True).fill('5')
        page.get_by_label('R0001 X', exact=True).fill('0.3')
        expect(page.locator('.stability-warning')).to_be_visible()
        page.get_by_label('新版本名称').fill('Invalid hourly stability')
        with page.expect_response(lambda response: '/routing-versions' in response.url and response.request.method == 'POST') as response:
            page.get_by_role('button', name='登记新版本', exact=True).click()
        assert response.value.status == 400
        expect(page.get_by_role('alert')).to_be_visible()
        page.get_by_role('button', name='恢复当前版本参数', exact=True).click()
        checked('spatial-download-reach-search-unstable-parameters-rejected')

        page.get_by_role('button', name='R0001', exact=True).click()
        endpoint = next(row for row in product['topology']['reaches'] if row['reach_id'] == 'R0001')['endLonLat']
        expect(page.locator('.point-coordinate')).to_contain_text(f'{endpoint[0]:.6f}')
        canvas = page.locator('.topology-map canvas')
        box = canvas.bounding_box()
        canvas.click(position={'x': box['width'] * 0.48, 'y': box['height'] * 0.45})
        expect(page.locator('.point-coordinate')).to_be_visible()
        coordinate = page.locator('.point-coordinate').inner_text()
        page.get_by_role('button', name='查询选定点', exact=True).click()
        expect(page.get_by_label('经度', exact=True)).to_be_visible()
        assert f"{float(page.get_by_label('经度', exact=True).input_value()):.6f}" in coordinate
        page.get_by_label('来源连续模拟').select_option(evidence['tasks']['custom-whole'])
        point = evidence['points']['river-interior']['point']
        page.get_by_label('经度', exact=True).fill(str(point['lon']))
        page.get_by_label('纬度', exact=True).fill(str(point['lat']))
        with page.expect_response(lambda response: response.url.endswith('/tasks') and response.request.method == 'POST') as response:
            page.get_by_role('button', name='计算指定位置流量', exact=True).click()
        assert response.value.status == 202, response.value.text()
        task_id = response.value.json()['taskId']
        report['tasks']['pointQuery'] = task_id
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            task = api('tasks/' + task_id)
            if task['status'] in {'completed', 'archived'}:
                break
            assert task['status'] in {'queued', 'running'}, task
            time.sleep(0.5)
        else:
            raise AssertionError('Point query timed out')
        page.get_by_role('button', name='刷新', exact=True).click()
        expect(page.locator('.chart')).to_be_visible(timeout=30000)
        assert api('tasks/' + task_id + '/result')['productionEligible'] is False
        page.screenshot(path=str(output / 'desktop-point-query.png'), full_page=True)
        checked('map-selection-ui-submit-real-point-query-chart')

        workflow_id = evidence['tasks']['custom-workflow']
        page.goto(args.url + '/#/diagnostics?task=' + workflow_id)
        expect(page.locator('table[aria-label="验收检查"] tbody tr')).to_have_count(12, timeout=30000)
        gates = page.locator('table[aria-label="验收检查"]')
        expect(gates).to_contain_text('逐月假设蒸散发')
        assert 'MONTHLY_CLIMATOLOGY' not in gates.inner_text()
        assert 'REVIEW_REQUIRED' not in gates.inner_text()
        assert 'OBSERVED_FLOW_ANCHORED' not in page.locator('.result').inner_text().split('计算请求与完整结果')[0]
        expect(page.locator('table[aria-label="年度水量平衡"] tbody tr')).to_have_count(3)
        workflow = download(page.get_by_role('button', name='导出结果 JSON', exact=True), 'workflow.json')
        assert workflow['result']['productionEligible'] is False
        with page.expect_download() as result:
            page.get_by_role('link', name='下载报告包', exact=True).click()
        result.value.save_as(str(output / 'reports.zip'))
        with zipfile.ZipFile(output / 'reports.zip') as archive:
            assert 'continuous-balance/continuous_hourly_flow.csv' in archive.namelist()
        page.get_by_role('button', name='采用候选至率定工作区', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('不能直接采用')
        page.get_by_role('tab', name='汇流与拓扑', exact=True).click()
        page.get_by_label('已登记汇流版本').select_option(evidence['routingVersionId'])
        page.get_by_role('button', name='选用此版本', exact=True).click()
        page.get_by_role('button', name='采用候选至率定工作区', exact=True).click()
        expect(page.locator('.message')).to_contain_text('候选参数已载入率定工作区')
        checked('archived-workflow-12-gates-3-years-downloads-lineage-guard')

        task_table = page.get_by_role('table', name='诊断任务', exact=True)
        page.get_by_label('检索诊断任务').fill(evidence['tasks']['custom-workflow'])
        expect(task_table.locator('tbody tr')).to_have_count(1)
        expect(task_table).to_contain_text(evidence['tasks']['custom-workflow'][-8:])
        workflow_status = api('tasks/' + evidence['tasks']['custom-workflow'])['status']
        page.get_by_label('诊断任务状态').select_option(workflow_status)
        expect(task_table).to_contain_text(evidence['tasks']['custom-workflow'][-8:])
        page.get_by_label('诊断任务状态').select_option('failed')
        expect(task_table).to_contain_text('没有匹配的任务')
        page.get_by_role('button', name='清除任务筛选', exact=True).click()
        expect(page.get_by_label('检索诊断任务')).to_have_value('')
        expect(page.get_by_label('诊断任务状态')).to_have_value('')
        assert task_table.locator('tbody tr').count() > 1
        checked('task-search-status-composition-and-reset')

        for width in (390, 360):
            page.set_viewport_size({'width': width, 'height': 844})
            page.get_by_role('tab', name='汇流与拓扑', exact=True).click()
            page.get_by_role('tab', name='全域默认', exact=True).click()
            map_pixels(f'mobile-map-{width}.png')
            contained()
            page.screenshot(path=str(output / f'mobile-workbench-{width}.png'), full_page=True)
            page.get_by_role('tab', name='河段参数', exact=True).click()
            contained()
            page.get_by_role('tab', name='模型综合检验', exact=True).click()
            contained()
            page.screenshot(path=str(output / f'mobile-workflow-{width}.png'), full_page=True)
        checked('390-and-360-mobile-canvas-forms-tables-contained')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.get_by_role('tab', name='汇流与拓扑', exact=True).click()
        map_pixels('desktop-resized-map.png')
        checked('map-resizes-after-view-and-viewport-switch')
        assert not report['errors'], report['errors']
        report['status'] = 'passed'
    finally:
        page.screenshot(path=str(output / 'last-state.png'), full_page=True)
        (output / 'browser-acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        context.close()
        browser.close()
