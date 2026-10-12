"""Exercise queued spatial generation, figure downloads and archive integration."""
import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18888')
parser.add_argument('--output', required=True)
parser.add_argument('--inspect', action='store_true')
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'errors': [], 'failedResources': []}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1050}, accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    page.on('response', lambda response: report['failedResources'].append(response.url) if response.status >= 400 else None)

    def api(resource):
        response = context.request.get(args.url + '/api/model/' + resource)
        assert response.ok, response.text()
        return response.json()

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def download(link, filename):
        with page.expect_download() as pending:
            page.get_by_role('link', name=link, exact=True).click()
        path = output / filename
        pending.value.save_as(path)
        return path

    def contained():
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert page.locator('.diagnostic-view').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')

    def loaded_figure():
        expect(page.locator('.spatial-figure img')).to_be_visible(timeout=60000)
        page.wait_for_function("document.querySelector('.spatial-figure img')?.naturalWidth > 1500")

    try:
        page.goto(args.url + '/#/diagnostics')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button', name='生成空间成果', exact=True)).to_be_enabled(timeout=30000)
        (output / 'initial-ui.txt').write_text(page.locator('.diagnostic-view').inner_text(), encoding='utf-8')
        print(page.locator('.spatial-toolbar').inner_text(), flush=True)
        page.screenshot(path=str(output / 'initial.png'), full_page=True)
        if args.inspect:
            report['status'] = 'inspected'
        else:
            existing = {row['taskId'] for row in api('tasks')['tasks']}
            page.get_by_role('button', name='生成空间成果', exact=True).click()
            expect(page.locator('.message')).to_contain_text('空间成果制作已提交', timeout=30000)
            jobs = [row for row in api('tasks')['tasks'] if row['taskId'] not in existing and row['runType'] == 'spatialExport']
            assert len(jobs) == 1
            task_id = report['taskId'] = jobs[0]['taskId']
            loaded_figure()
            result = api('tasks/' + task_id + '/result')
            assert result['spatial']['summary']['assignedCells'] == 79500
            assert result['spatial']['summary']['unassignedCells'] == 130
            contained()
            check('button-to-real-worker-with-original-d8-and-control-split')
            page.locator('.spatial-results').scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'desktop-terrain.png'), full_page=True)
            png = download('下载当前图 PNG', 'terrain-network.png')
            hashes = {row['name']: row['sha256'] for row in result['spatial']['files']}
            assert hashlib.sha256(png.read_bytes()).hexdigest() == hashes[png.name]
            assert Image.open(png).size == (1888, 1536)
            page.get_by_role('button', name='汇流单元分区', exact=True).click()
            loaded_figure()
            png = download('下载当前图 PNG', 'routing-units.png')
            assert hashlib.sha256(png.read_bytes()).hexdigest() == hashes[png.name]
            package = download('下载 GIS 成果包', 'spatial.zip')
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                assert len(archive.namelist()) == len(hashes) + 1
                for row in manifest['files']:
                    assert hashlib.sha256(archive.read(row['name'])).hexdigest() == row['sha256']
                assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive.namelist())
            check('two-png-and-gis-package-downloads-match-server-hashes')
            page.reload()
            page.wait_for_load_state('networkidle')
            page.get_by_role('button', name='查看最近成果', exact=True).click()
            loaded_figure()
            check('refresh-restores-most-recent-spatial-result')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('.spatial-results').scroll_into_view_if_needed()
            contained()
            page.screenshot(path=str(output / 'mobile-spatial.png'), full_page=True)
            check('mobile-390px-contained-product-layout')
            page.set_viewport_size({'width': 1440, 'height': 1050})
            page.goto(args.url + '/#/archive')
            page.wait_for_load_state('networkidle')
            row = page.locator('.task-table tr').filter(has=page.locator(f'[title="{task_id}"]'))
            row.get_by_role('button', name='查看成果', exact=True).click()
            loaded_figure()
            page.get_by_label('成果名称', exact=True).fill('南安溪 · 地形河网与汇流分区')
            page.get_by_label('成果分组', exact=True).fill('流域空间成果')
            page.get_by_role('button', name='保存整理', exact=True).click()
            expect(page.get_by_role('button', name='保存整理', exact=True)).to_be_disabled(timeout=10000)
            assert api('result-catalog')['metadata'][task_id]['collection'] == '流域空间成果'
            page.screenshot(path=str(output / 'archive-spatial.png'), full_page=True)
            check('shared-result-workbench-preview-and-sql-grouping')
            page.goto(args.url + '/#/diagnostics?task=' + task_id)
            page.wait_for_load_state('networkidle')
            loaded_figure()
            row = page.get_by_role('table', name='诊断任务', exact=True).locator('tr').filter(has=page.locator(f'code[title="{task_id}"]'))
            row.get_by_role('button', name='归档', exact=True).click()
            expect(row).to_contain_text('已归档', timeout=20000)
            loaded_figure()
            check('archived-result-remains-previewable-and-downloadable')
            # A second job tests cancellation through the same UI, not an artificial status.
            existing = {row['taskId'] for row in api('tasks')['tasks']}
            page.get_by_role('button', name='生成空间成果', exact=True).click()
            expect(page.locator('.message')).to_contain_text('空间成果制作已提交', timeout=30000)
            job = next(row for row in api('tasks')['tasks'] if row['taskId'] not in existing and row['runType'] == 'spatialExport')
            row = page.get_by_role('table', name='诊断任务', exact=True).locator('tr').filter(has=page.locator(f'code[title="{job["taskId"]}"]'))
            row.get_by_role('button', name='取消', exact=True).click()
            expect(row).to_contain_text('已取消', timeout=30000)
            assert api('tasks/' + job['taskId'])['status'] == 'cancelled'
            report['cancelledTaskId'] = job['taskId']
            check('real-spatial-worker-cancellation')
            assert not report['errors'], report['errors']
            assert not report['failedResources'], report['failedResources']
            report['status'] = 'passed'
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
