"""Real browser acceptance: organize results, compare curves, import and continue state."""
import argparse
import csv
import io
import json
import time
from datetime import datetime, timedelta
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
    context = browser.new_context(viewport={'width': 1440, 'height': 1050})
    page = context.new_page()
    page.on('pageerror', lambda error: report['errors'].append(str(error)))
    page.on('response', lambda r: report['failedResources'].append(r.url) if r.status >= 400 else None)

    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, response.text()
        return response.json()

    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)

    def download(button, filename):
        with page.expect_download() as pending:
            page.get_by_role('button', name=button, exact=True).click()
        path = output / filename
        pending.value.save_as(path)
        return path

    def open_task(task_id):
        page.locator('.task-table tr').filter(has=page.locator(f'[title="{task_id}"]')).get_by_role('button', name='查看成果', exact=True).click()
        expect(page.locator('.result-workbench')).to_be_visible()

    try:
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-archive')).to_be_visible()
        (output / 'initial-ui.txt').write_text(page.locator('.view-archive').inner_text(), encoding='utf-8')
        jobs = api('tasks')['tasks']
        trials = [row for row in jobs if row.get('runConfig', {}).get('calibration', {}).get('evaluationMode') == 'manual' and row['status'] in {'completed', 'archived'}]
        assert len(trials) >= 2, 'Run manual-trials browser acceptance first'
        current, previous = trials[:2]
        before = api('tasks/' + current['taskId'] + '/result')
        open_task(current['taskId'])
        assert page.get_by_label('成果时段', exact=True).locator('option').count() == 3
        for period in ['CALIBRATION', 'VALIDATION', 'INDEPENDENT']:
            page.get_by_label('成果时段', exact=True).select_option(period)
            expect(page.locator('.result-chart canvas').first).to_be_visible()
        page.get_by_label('成果时段', exact=True).select_option('CALIBRATION')
        page.get_by_label('对比成果', exact=True).select_option(previous['taskId'])
        expect(page.locator('.parameter-diff')).to_contain_text('SM 20 → 60')
        expect(page.locator('.comparison-toolbar')).to_contain_text('同条件过程对比')
        check('calibration-validation-independent-periods-and-real-parameter-comparison')
        page.get_by_label('成果名称', exact=True).fill('六月洪水 · SM60 试调')
        page.get_by_label('成果分组', exact=True).fill('六月参数比选')
        page.get_by_label('成果备注', exact=True).fill('保留 SM20 对照过程，检查自由水容量对流量过程的影响。')
        page.get_by_label('收藏成果', exact=True).check()
        save = page.get_by_role('button', name='保存整理', exact=True)
        if save.is_enabled():
            with page.expect_response(lambda r: '/result-metadata/' in r.url and r.request.method == 'POST') as pending:
                save.click()
            assert pending.value.ok
        assert api('result-catalog')['metadata'][current['taskId']]['collection'] == '六月参数比选'
        assert api('tasks/' + current['taskId'] + '/result') == before
        process = download('逐时过程 CSV', 'process.csv')
        records = list(csv.reader(io.StringIO(process.read_text(encoding='utf-8-sig'))))
        assert len(records) == len(before['hydrographs']['CALIBRATION']['forecast']) + 1
        assert len(records[0]) == 5
        assert float(records[1][3]) == before['hydrographs']['CALIBRATION']['forecast'][0]['value']
        download('评价汇总 CSV', 'evaluation.csv')
        download('参数表 CSV', 'parameters.csv')
        download('完整结果 JSON', 'result.json')
        download('报告包 ZIP', 'reports.zip')
        page.get_by_label('成果分组筛选', exact=True).select_option('六月参数比选')
        summary = download('导出筛选汇总', 'collection.csv')
        assert '六月洪水 · SM60 试调' in summary.read_text(encoding='utf-8-sig')
        expect(page.get_by_label('对比成果', exact=True)).to_have_value(previous['taskId'])
        expect(page.locator('.parameter-diff')).to_contain_text('SM 20 → 60')
        page.locator('.result-workbench').scroll_into_view_if_needed()
        page.screenshot(path=str(output / 'results-desktop.png'), full_page=True)
        page.set_viewport_size({'width': 900, 'height': 1000})
        page.wait_for_timeout(700)
        layout = page.locator('.result-workbench').evaluate("el => ({width:el.clientWidth,scroll:el.scrollWidth,children:[...el.querySelectorAll('*')].filter(e => e.scrollWidth > e.clientWidth+2).map(e=>({tag:e.tagName,cls:e.className,w:e.clientWidth,s:e.scrollWidth})).slice(0,30)})")
        (output / 'compact-layout.json').write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding='utf-8')
        page.screenshot(path=str(output / 'results-compact.png'), full_page=True)
        assert page.locator('.result-workbench').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
        page.screenshot(path=str(output / 'results-compact.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1050})
        check('SQL-result-organization-and-five-exports-preserve-numerical-results')
        # Close the first browser context before restoring shared workspace drafts.
        page.wait_for_timeout(1800)
        context.close()
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.on('response', lambda r: report['failedResources'].append(r.url) if r.status >= 400 else None)
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        open_task(current['taskId'])
        expect(page.get_by_label('成果名称', exact=True)).to_have_value('六月洪水 · SM60 试调')
        expect(page.get_by_label('成果备注', exact=True)).to_have_value('保留 SM20 对照过程，检查自由水容量对流量过程的影响。')
        check('new-browser-restores-names-groups-notes-and-favorites-from-SQL')
        states = api('states')['states']
        original = next(row for row in states if not row.get('importProvenance'))
        source = output / 'original-state.json'
        source.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
        page.goto(args.url + '/#/state')
        page.wait_for_load_state('networkidle')
        (output / 'state-initial-ui.txt').write_text(page.locator('.view-state').inner_text(), encoding='utf-8')
        page.locator('.state-library input[type=file]').set_input_files(source)
        expect(page.locator('.import-review')).to_be_visible()
        page.get_by_label('导入状态名称', exact=True).fill('移机续算状态')
        with page.expect_response(lambda r: r.url.endswith('/states/import') and r.request.method == 'POST') as pending:
            page.get_by_role('button', name='确认导入', exact=True).click()
        assert pending.value.ok, pending.value.text()
        imported = pending.value.json()['state']
        report['importedStateId'] = imported['stateVersionId']
        expect(page.get_by_label('状态名称', exact=True)).to_have_value('移机续算状态', timeout=30000)
        expect(page.locator('.time-title').first).to_contain_text('导入状态快照')
        for key in ['land', 'routing', 'parameters', 'gridIds', 'gridGeometry', 'time']:
            assert original[key] == imported[key]
        exported = download('状态文件', 'imported-state.json')
        assert json.loads(exported.read_text(encoding='utf-8')) == imported
        count = len(api('tasks')['tasks'])
        page.locator('.state-library input[type=file]').set_input_files(source)
        page.get_by_role('button', name='确认导入', exact=True).click()
        expect(page.locator('.state-library [role=status]')).to_contain_text('此状态已在库中')
        assert len(api('tasks')['tasks']) == count
        page.screenshot(path=str(output / 'imported-state.png'), full_page=True)
        check('checkpoint-upload-validation-selection-export-and-idempotent-import')
        page.get_by_role('button', name='用于续算', exact=True).click()
        target = (datetime.fromisoformat(imported['time']) + timedelta(hours=2)).isoformat(timespec='minutes')
        page.locator('#state-target-time').fill(target)
        with page.expect_response(lambda r: r.url.endswith('/tasks') and r.request.method == 'POST') as pending:
            page.get_by_role('button', name='开始续算', exact=True).click()
        assert pending.value.status == 202, pending.value.text()
        task = pending.value.json()
        report['continuationTaskId'] = task['taskId']
        assert task['runConfig']['period']['initialStateVersionId'] == imported['stateVersionId']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = api('tasks/' + task['taskId'])
            if status['status'] not in {'queued', 'running'}:
                assert status['status'] == 'completed', status
                break
            page.wait_for_timeout(500)
        else:
            raise AssertionError('Continuation timed out')
        result = api('tasks/' + task['taskId'] + '/result')
        assert result['stateTime'] == target + ':00'
        assert len(result['hydrograph']['forecast']) == 2
        check('imported-checkpoint-continues-through-real-model-to-next-state')
        assert not report['errors'], report['errors']
        assert not report['failedResources'], report['failedResources']
        report['status'] = 'passed'
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
