"""Exercise cleanup in a disposable seeded workspace, using the real browser and Recycle Bin."""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import time
from pathlib import Path


OWNED = 'JOB-cleanup-smoke-owned'
MAP = 'JOB-cleanup-smoke-map'
ACTIVE = 'JOB-cleanup-smoke-active'
STALE = 'OP-cleanup-smoke-stale'


def write_document(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare(workspace, seed):
    if workspace.exists():
        raise FileExistsError('Smoke workspace must be new: ' + str(workspace))
    shutil.copytree(seed, workspace)
    write_document(workspace / 'cleanup-smoke-fixture.json', {'purpose': 'disposable-cleanup-acceptance'})
    for identifier, kind in ((OWNED, 'simulation'), (MAP, 'spatialExport')):
        status = {'taskId': identifier, 'createdAt': '2026-10-21T00:00:00Z',
                  'status': 'completed', 'runType': kind, 'runConfig': {'runType': kind}}
        if identifier == OWNED:
            status.update(operationId='OP-cleanup-smoke-missing', internal=True)
        write_document(workspace / 'tasks' / identifier / 'status.json', status)
        write_document(workspace / 'tasks' / identifier / 'result.json', {'taskId': identifier})
    write_document(workspace / 'states' / ('STATE-' + OWNED + '.json'),
                   {'stateVersionId': 'STATE-' + OWNED, 'sourceTaskId': OWNED})
    write_document(workspace / 'operations' / STALE / 'status.json',
                   {'taskId': STALE, 'operation': 'calibration', 'status': 'completed',
                    'createdAt': '2026-10-21T00:00:00Z', 'runConfig': {},
                    'name': '旧页面删除记录验收'})
    write_document(workspace / 'operations' / STALE / 'result.json', {'taskId': STALE})
    marker = workspace / 'inputs' / 'cleanup-smoke-protected.txt'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('registered input files must remain untouched', encoding='utf-8')
    print(json.dumps({'prepared': str(workspace)}, ensure_ascii=False))


def verify(args):
    from playwright.sync_api import sync_playwright, expect
    workspace = args.workspace.resolve()
    fixture = workspace / 'cleanup-smoke-fixture.json'
    if not fixture.is_file() or json.loads(fixture.read_text(encoding='utf-8')).get('purpose') != 'disposable-cleanup-acceptance':
        raise RuntimeError('Refusing to operate on a non-fixture workspace')
    errors, report = [], {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(args.url.rstrip('/') + '/#/archive')
            page.wait_for_load_state('networkidle', timeout=60000)
            expect(page.get_by_role('heading', name='计算成果', exact=True)).to_be_visible()
            if args.standalone:
                expect(page.locator('.topbar .meta-select')).to_have_count(0)
                expect(page.locator('.fixed-context')).to_contain_text(['南安溪-徐畈', '1 km'])
                expect(page.get_by_role('button', name='退出平台', exact=True)).to_be_visible()
            page.get_by_role('button', name='回收站', exact=True).click()
            page.get_by_role('button', name='检查旧版残留文件', exact=True).click()
            dialog = page.locator('.el-dialog:visible')
            expect(dialog.get_by_text('tasks/' + OWNED, exact=True)).to_be_visible(timeout=60000)
            expect(dialog.get_by_text('states/STATE-' + OWNED + '.json', exact=True)).to_be_visible()
            expect(dialog.get_by_text('tasks/' + MAP, exact=True)).to_have_count(0)
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshot), full_page=True)
            with page.expect_response(lambda response: response.url.endswith('/workspace/artifact-cleanup')
                                      and response.request.method == 'POST', timeout=60000) as event:
                dialog.get_by_role('button', name='确认移走上列残留文件', exact=True).click()
            response = event.value
            assert response.ok, response.text()
            result = response.json()
            assert len(result['movedArtifacts']) == 2, result
            assert not (workspace / 'tasks' / OWNED).exists()
            assert not (workspace / 'states' / ('STATE-' + OWNED + '.json')).exists()
            assert (workspace / 'tasks' / MAP / 'status.json').is_file()
            assert (workspace / 'inputs' / 'cleanup-smoke-protected.txt').is_file()
            assert (workspace / 'database' / 'hydrology.sqlite3').is_file()
            journal = list((workspace / 'cleanup-journal').glob('*.json'))
            assert len(journal) == 1 and json.loads(journal[0].read_text(encoding='utf-8'))['status'] == 'completed'
            expect(dialog.get_by_role('status')).to_contain_text('已将 2 项送入 Windows 回收站')
            dialog.get_by_role('button', name='重新检查', exact=True).click()
            expect(dialog.get_by_role('button', name='确认移走上列残留文件', exact=True)).to_be_disabled()
            dialog.get_by_role('button', name='关闭', exact=True).click()
            page.reload()
            page.wait_for_load_state('networkidle', timeout=60000)
            report.update(cleanup=True, ownedStateRemoved=True, gisPreserved=True, inputsPreserved=True,
                          movedPaths=[row['relativePath'] for row in result['movedArtifacts']], pageErrors=errors)
            # Simulate a different tab deleting the calculation while this detail remains open.
            page.goto(args.url.rstrip('/') + '/#/archive?result=' + STALE)
            page.wait_for_load_state('networkidle', timeout=60000)
            expect(page.locator('.result-detail')).to_be_visible(timeout=60000)
            endpoint = args.url.rstrip('/') + '/api/model/result-retention/' + STALE
            retained = page.request.get(endpoint)
            assert retained.ok, retained.text()
            discarded = page.request.post(endpoint + '/discard', data={'revision': retained.json()['revision']})
            assert discarded.ok, discarded.text()
            preview = page.request.get(endpoint + '/deletion-preview')
            assert preview.ok, preview.text()
            plan = preview.json()
            assert not plan['protected'], plan
            purged = page.request.post(endpoint + '/purge', data={
                'revision': plan['revision'], 'previewSHA256': plan['previewSHA256']})
            assert purged.ok, purged.text()
            assert not (workspace / 'operations' / STALE).exists()
            page.get_by_role('button', name='刷新', exact=True).click()
            expect(page.locator('.result-detail')).to_have_count(0, timeout=60000)
            for suffix in ('operations/' + STALE, 'result-retention/' + STALE):
                missing = page.request.get(args.url.rstrip('/') + '/api/model/' + suffix)
                assert missing.status == 404, missing.text()
                assert missing.json()['code'] == 'RESULT_NOT_FOUND', missing.text()
                assert 'status.json' not in missing.text() and 'Errno' not in missing.text()
            page.goto(args.url.rstrip('/') + '/#/simulation?operation=' + STALE)
            page.wait_for_load_state('networkidle', timeout=60000)
            expect(page.get_by_role('alert')).to_contain_text('该计算记录已移除或文件缺失', timeout=60000)
            expect(page.locator('body')).not_to_contain_text('Errno 2')
            expect(page.locator('body')).not_to_contain_text('status.json')
            selections = page.evaluate("""() => Object.keys(localStorage)
                .filter(key => key.startsWith('hydrology.daily.') && key.endsWith('.simulation'))
                .map(key => JSON.parse(localStorage.getItem(key)))""")
            assert selections and all(row.get('operationId') != STALE for row in selections), selections
            report.update(staleDetailCleared=True, typedMissingResult=True, staleSimulationOpened=True)
            assert not errors, errors
            if args.standalone:
                active_file = workspace / 'tasks' / ACTIVE / 'status.json'
                active_status = {'taskId': ACTIVE, 'createdAt': '2026-10-21T00:00:00Z',
                                 'status': 'running', 'runType': 'simulation', 'runConfig': {}}
                write_document(active_file, active_status)
                blocked = page.request.post(args.url.rstrip('/') + '/api/model/exit', data={},
                                            headers={'Origin': args.url.rstrip('/')})
                assert blocked.status == 409, blocked.text()
                active_status['status'] = 'completed'
                write_document(active_file, active_status)
                page.get_by_role('button', name='退出平台', exact=True).click()
                page.locator('.el-message-box').get_by_role('button', name='退出平台', exact=True).click()
                expect(page.get_by_role('heading', name='平台已退出', exact=True)).to_be_visible(timeout=30000)
                port = int(args.url.rstrip('/').rsplit(':', 1)[1])
                for _ in range(100):
                    with socket.socket() as probe:
                        if probe.connect_ex(('127.0.0.1', port)) != 0:
                            break
                    time.sleep(0.1)
                else:
                    raise AssertionError('Platform port still listening after exit')
                report.update(activeExitBlocked=True, idleExitReleasedPort=True)
        finally:
            browser.close()
    if args.report:
        write_document(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--seed', type=Path)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--url', default='http://127.0.0.1:18879')
    parser.add_argument('--standalone', action='store_true')
    parser.add_argument('--restart-check', action='store_true')
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.prepare:
        if not args.seed:
            parser.error('--prepare requires --seed')
        prepare(args.workspace.resolve(), args.seed.resolve())
    elif args.restart_check:
        import urllib.request
        with urllib.request.urlopen(args.url.rstrip('/') + '/api/model/workspace/artifact-cleanup') as response:
            plan = json.load(response)
        assert plan['taskCount'] == 0 and plan['stateCount'] == 0, plan
        assert not (args.workspace / 'tasks' / OWNED).exists()
        assert (args.workspace / 'tasks' / MAP / 'status.json').is_file()
        endpoint = args.url.rstrip('/') + '/api/model/exit'
        request = urllib.request.Request(endpoint, data=b'{}',
            headers={'Content-Type': 'application/json', 'Origin': args.url.rstrip('/')})
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
        print(json.dumps({'restartRetainsCleanup': True, 'gisPreserved': True}))
    else:
        verify(args)


if __name__ == '__main__':
    main()
