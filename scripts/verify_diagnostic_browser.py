"""Browser acceptance against an isolated, real unified diagnostic service."""
import argparse
import json
import time
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18886')
parser.add_argument('--runtime', required=True)
parser.add_argument('--probe', action='store_true')
args = parser.parse_args()
runtime = Path(args.runtime).resolve()
output = runtime / 'browser'
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

    def api(resource, body=None, expected=200):
        response = (context.request.get if body is None else context.request.post)(
            args.url + '/api/model/' + resource, **({} if body is None else {'data': body}))
        assert response.status == expected, (response.status, response.text())
        return response.json()

    def wait_task(task_id):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            task = api('tasks/' + task_id)
            if task['status'] not in {'queued', 'running', 'cancelling'}:
                assert task['status'] in {'completed', 'archived'}, task
                return api('tasks/' + task_id + '/result')
            time.sleep(0.5)
        raise AssertionError('Timed out: ' + task_id)

    try:
        page.goto(args.url + '/#/diagnostics')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('heading', name='模型诊断', exact=True)).to_be_visible(timeout=30000)
        expect(page.locator('table[aria-label="河段汇流参数"] tbody tr')).to_have_count(32, timeout=30000)
        page.screenshot(path=str(output / 'desktop-topology.png'), full_page=True)
        if args.probe:
            print(page.locator('body').inner_text()[:16000], flush=True)
            print('LABELS', page.locator('label').all_text_contents(), flush=True)
            summary['status'] = 'probe'
        else:
            product = api('spatial-products')
            assert len(product['geometry']['features']) == 110
            check('110-grids-32-reaches-real-topology')
            page.wait_for_function("document.querySelector('.maplibregl-canvas')?.width > 0")
            # Capture the actual canvas, not only a blank container.
            canvas = page.locator('.topology-map canvas')
            canvas.screenshot(path=str(output / 'topology-canvas.png'))
            from PIL import Image
            pixels = Image.open(output / 'topology-canvas.png').convert('RGB')
            assert len(pixels.getcolors(pixels.width * pixels.height) or []) > 100
            check('map-canvas-nonblank')

            base_version = api('routing-versions')['versions'][0]
            page.get_by_label('新版本名称').fill('Browser routing ' + str(time.time_ns()))
            page.get_by_label('RS 坡面 K（h）').fill('1.55')
            with page.expect_response(lambda r: '/routing-versions' in r.url and r.request.method == 'POST') as response:
                page.get_by_role('button', name='登记新版本', exact=True).click()
            assert response.value.status == 201
            version = response.value.json()
            expect(page.get_by_role('status')).to_contain_text('尚未选用')
            page.get_by_role('button', name='选用此版本', exact=True).click()
            expect(page.locator('.identity').first).to_contain_text(version['routingVersionId'])
            page.reload()
            expect(page.locator('.identity').first).to_contain_text(version['routingVersionId'], timeout=30000)
            check('register-select-reload-routing-version')

            # Invalid hourly Muskingum stability is rejected, not silently clipped.
            page.get_by_label('R0001 K', exact=True).fill('5')
            page.get_by_label('R0001 X', exact=True).fill('0.3')
            page.get_by_label('新版本名称').fill('Invalid stability')
            with page.expect_response(lambda r: '/routing-versions' in r.url and r.request.method == 'POST') as response:
                page.get_by_role('button', name='登记新版本', exact=True).click()
            assert response.value.status == 400
            expect(page.get_by_role('alert')).to_be_visible()
            check('unstable-routing-rejected-in-ui')

            tasks = api('tasks')['tasks']
            source = next(task for task in tasks if task['runConfig']['runType'] == 'simulation' and task['status'] in {'completed', 'archived'})
            simulated = json.loads(json.dumps(source['runConfig']))
            simulated['requestId'] = 'BROWSER-SIM-' + str(time.time_ns())
            simulated['routing'] = {'versionId': version['routingVersionId']}
            for key in ['routingVersionId', 'routingManifestSHA256']:
                simulated['lineage'].pop(key, None)
            submitted = api('tasks', simulated, 202)
            custom = wait_task(submitted['taskId'])
            assert custom['lineage']['routingVersionId'] == version['routingVersionId']
            assert custom['productionEligible'] is False
            summary['tasks']['customRoutingSimulation'] = submitted['taskId']
            check('registered-routing-consumed-by-real-worker')

            # Seed one actual standalone calibration scheme for the selectors.
            nested = next(runtime.glob('tasks/JOB-*/JOB-boundary/JOB-*/request.json'))
            base = json.loads(nested.read_text(encoding='utf-8'))
            base['requestId'] = 'BROWSER-CAL-' + str(time.time_ns())
            calibration = api('tasks', base, 202)
            wait_task(calibration['taskId'])
            summary['tasks']['calibrationSource'] = calibration['taskId']
            page.get_by_role('button', name='刷新', exact=True).click()
            page.get_by_role('tab', name='指定位置流量', exact=True).click()
            page.get_by_label('来源连续模拟').select_option(submitted['taskId'])
            river_point = json.loads((runtime / 'diagnostic-acceptance.json').read_text(encoding='utf-8'))['river-interior']['point']
            page.get_by_label('经度', exact=True).fill(str(river_point['lon']))
            page.get_by_label('纬度', exact=True).fill(str(river_point['lat']))
            with page.expect_response(lambda r: r.url.endswith('/tasks') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='计算指定位置流量', exact=True).click()
            assert response.value.status == 202, response.value.text()
            point = response.value.json()
            point_result = wait_task(point['taskId'])
            summary['tasks']['pointQuery'] = point['taskId']
            assert point_result['pointQuery']['quality']['status'] == 'RERUN_COMPLETED'
            page.get_by_role('button', name='刷新', exact=True).click()
            expect(page.locator('.chart')).to_be_visible(timeout=30000)
            page.screenshot(path=str(output / 'desktop-point-result.png'), full_page=True)
            check('ui-submit-point-query-real-chart')

            page.get_by_role('tab', name='参数稳定性检验', exact=True).click()
            page.get_by_label('场次率定方案').select_option(calibration['taskId'])
            with page.expect_response(lambda r: r.url.endswith('/tasks') and r.request.method == 'POST') as response:
                page.get_by_role('button', name='开始参数检验', exact=True).click()
            assert response.value.status == 202, response.value.text()
            experiment = response.value.json()
            wait_task(experiment['taskId'])
            page.get_by_role('button', name='刷新', exact=True).click()
            expect(page.locator('table[aria-label="实验运行结果"] tbody tr')).to_have_count(3, timeout=30000)
            summary['tasks']['experiment'] = experiment['taskId']
            check('ui-submit-three-real-seed-runs')

            workflow_record = json.loads((runtime / 'workflow-acceptance.json').read_text(encoding='utf-8'))
            workflow_id = workflow_record['taskId']
            page.goto(args.url + '/#/diagnostics?task=' + workflow_id)
            expect(page.locator('table[aria-label="验收检查"] tbody tr')).to_have_count(12, timeout=30000)
            expect(page.locator('table[aria-label="年度水量平衡"] tbody tr')).to_have_count(7)
            check('archived-workflow-deeplink-12-checks-7-years')

            with page.expect_download() as download:
                page.get_by_role('button', name='导出结果 JSON', exact=True).click()
            download.value.save_as(str(output / 'workflow-result.json'))
            exported = json.loads((output / 'workflow-result.json').read_text(encoding='utf-8'))
            assert exported['result']['taskId'] == workflow_id
            assert exported['result']['productionEligible'] is False
            with page.expect_download() as download:
                page.get_by_role('link', name='下载报告包', exact=True).click()
            download.value.save_as(str(output / 'workflow-reports.zip'))
            with zipfile.ZipFile(output / 'workflow-reports.zip') as archive:
                assert 'continuous-balance/continuous_hourly_flow.csv' in archive.namelist()
                assert 'continuous-balance/annual_water_balance_metrics.csv' in archive.namelist()
                assert all('secrets' not in name for name in archive.namelist())
            check('json-and-zip-downloads-real-reports')

            page.get_by_role('button', name='采用候选至率定工作区', exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('不能直接采用')
            check('cross-routing-candidate-adoption-rejected')
            page.get_by_role('tab', name='汇流与拓扑', exact=True).click()
            page.get_by_label('已登记汇流版本').select_option('control-split-v2')
            page.get_by_role('button', name='选用此版本', exact=True).click()
            page.get_by_role('button', name='采用候选至率定工作区', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('未发布为生产参数')
            expect(page.get_by_role('alert')).to_have_count(0)
            check('matching-candidate-adoption-keeps-diagnostic-status')

            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_role('tab', name='模型综合检验', exact=True).click()
            page.screenshot(path=str(output / 'mobile-workflow.png'), full_page=True)
            assert page.locator('.diagnostic-view').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            page.get_by_role('tab', name='汇流与拓扑', exact=True).click()
            page.screenshot(path=str(output / 'mobile-topology.png'), full_page=True)
            assert page.locator('.diagnostic-view').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            check('mobile-forms-map-and-tables-contained')
            assert not summary['errors'], summary['errors']
            summary['status'] = 'passed'
    finally:
        (output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        context.close()
        browser.close()
