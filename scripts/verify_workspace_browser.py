"""Workspace UI, browser-independent drafts and offline map acceptance."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18888')
parser.add_argument('--output', required=True)
parser.add_argument('--probe', action='store_true')
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'errors': [], 'external': []}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    def route(request):
        if request.request.url.startswith(args.url): request.continue_()
        else:
            report['external'].append(request.request.url)
            request.abort()
    context.route('http**/*', route)
    page = context.new_page()
    page.on('pageerror', lambda e: report['errors'].append(str(e)))
    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, response.text()
        return response.json()
    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)
    try:
        page.goto(args.url + '/#/system')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('heading', name='工作区与系统', exact=True)).to_be_visible()
        print('SYSTEM DOM', page.locator('body').inner_text(), flush=True)
        page.screenshot(path=str(output / 'system-desktop.png'), full_page=True)
        if args.probe:
            page.goto(args.url + '/#/calibration')
            page.wait_for_load_state('networkidle')
            print('CALIBRATION DOM', page.locator('body').inner_text()[:18000], flush=True)
            print('INPUTS', page.locator('input,textarea,select').evaluate_all('(els)=>els.map(e=>({tag:e.tagName,type:e.type,label:e.getAttribute("aria-label"),placeholder:e.placeholder,value:e.value}))'), flush=True)
            page.screenshot(path=str(output / 'calibration-desktop.png'), full_page=True)
            page.goto(args.url + '/#/realtime')
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(1500)
            page.screenshot(path=str(output / 'map-offline.png'), full_page=True)
            print('MAP DOM', page.locator('body').inner_text()[:7000], flush=True)
            report['status'] = 'probe'
        else:
            info = api('workspace')
            assert any(row['path'] in page.locator('body').inner_text() for row in info['directories'])
            check('工作区显示实际目录')
            before = {row['name'] for row in api('workspace/backups')['backups']}
            page.get_by_role('button', name='备份工作区', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('备份完成', timeout=600000)
            saved = api('workspace/backups')['backups']
            assert len({row['name'] for row in saved} - before) == 1
            report['backup'] = next(row for row in saved if row['name'] not in before)
            check('界面实际备份工作区')
            page.goto(args.url + '/#/calibration')
            page.reload()
            expect(page.locator('.view-cal')).to_be_visible(timeout=30000)
            page.get_by_role('button', name='参数率定').click()
            field = page.locator('#max-evals')
            expect(field).to_be_visible()
            original = field.input_value()
            changed = '7' if original != '7' else '8'
            field.fill(changed)
            field.press('Tab')
            page.wait_for_timeout(1500)
            shared = api('workspace/drafts')['drafts']['hydro_calibration_workspaces_v1']
            assert any(row.get('maxEvaluations') == int(changed) for row in shared['value']['workspaces'].values())
            check('率定条件保存到资料库')
            second = browser.new_context(viewport={'width': 1440, 'height': 1000})
            second.route('http**/*', route)
            fresh = second.new_page()
            fresh.on('pageerror', lambda e: report['errors'].append(str(e)))
            fresh.goto(args.url + '/#/calibration')
            expect(fresh.locator('.view-cal')).to_be_visible(timeout=30000)
            fresh.get_by_role('button', name='参数率定').click()
            expect(fresh.locator('#max-evals')).to_have_value(changed)
            check('新浏览器恢复率定草稿')
            # Restore the test edit through the same UI and shared revision.
            fresh.locator('#max-evals').fill(original)
            fresh.locator('#max-evals').press('Tab')
            fresh.wait_for_timeout(1500)
            fresh.screenshot(path=str(output / 'calibration-desktop.png'), full_page=True)
            second.close()
            page.goto(args.url + '/#/realtime')
            page.reload()
            expect(page.locator('canvas.maplibregl-canvas').first).to_be_visible(timeout=30000)
            page.wait_for_load_state('networkidle')
            page.screenshot(path=str(output / 'map-offline.png'), full_page=True)
            assert not report['external'], report['external']
            check('地图不请求外部网络资源')
            page.goto(args.url + '/#/system')
            page.reload()
            expect(page.get_by_role('heading', name='工作区与系统', exact=True)).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(output / 'system-mobile.png'), full_page=True)
            check('工作区手机布局无横向溢出')
            assert not report['errors'], report['errors']
            report['status'] = 'passed'
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
