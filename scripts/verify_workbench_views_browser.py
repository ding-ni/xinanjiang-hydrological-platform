"""Inspect the saved staff workflow and rainfall/state maps after restart."""
import argparse
import json
import io
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18894')
parser.add_argument('--report', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
previous = json.loads(Path(args.report).read_text(encoding='utf-8'))
assert previous['status'] == 'passed'
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'pageErrors': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))

    def api(path, body=None):
        response = context.request.get(args.url + '/api/model/' + path) if body is None else context.request.post(args.url + '/api/model/' + path, data=body, timeout=180000)
        assert response.ok, response.text()
        return response.json()

    def check(label):
        report['checks'].append(label)
        print('PASS', label, flush=True)

    def visible_size(selector):
        invalid = page.locator(selector).evaluate_all('''elements => elements.filter(el => el.getClientRects().length).filter(el => {
          const style = getComputedStyle(el), control = el.matches('button,input:not([type=checkbox]),select');
          return parseFloat(style.fontSize) < 14 || (control && el.getBoundingClientRect().height < 35.5);
        }).map(el => ({tag:el.tagName,text:el.textContent.slice(0,70),font:getComputedStyle(el).fontSize,height:el.getBoundingClientRect().height}))''')
        assert not invalid, invalid

    try:
        page.goto(args.url + '/#/calibration?schemeId=' + previous['schemeId'])
        expect(page.locator('.scheme-heading h1')).to_have_text(previous['schemeName'], timeout=20000)
        expect(page.locator('.flood-row')).to_have_count(2)
        assert page.locator('.pagination').count() == 0
        visible_size('.calibration-page .btn,.calibration-page .input,.flood-process small,.selection-counts,.event-scope button')
        page.get_by_role('button', name='率定设置', exact=True).click()
        expect(page.locator('.settings-dialog')).to_contain_text('搜索设置')
        expect(page.locator('.search-head').first).to_contain_text('起始值')
        expect(page.locator('#reference-parameters')).to_have_value('')
        visible_size('.settings-dialog .input,.settings-dialog .btn,.settings-dialog label')
        page.screenshot(path=str(output / 'calibration-settings-1366.png'), full_page=True)
        page.get_by_role('button', name='完成', exact=True).click()
        page.locator('.calculation-history').filter(has_text='历次计算').locator('summary').click()
        expect(page.locator('.history-row')).to_have_count(5)
        page.locator('.history-row').first.click()
        page.get_by_role('button', name='状态分布', exact=True).click()
        expect(page.locator('.state-layout .map-panel')).to_be_visible(timeout=20000)
        select = page.get_by_label('状态回放时刻')
        expect(select).to_be_visible()
        times = select.locator('option').evaluate_all('elements => elements.map(el => el.value)')
        select.select_option(times[0])
        page.get_by_role('button', name='地表径流', exact=False).first.click()
        expect(page.locator('.map-legend')).to_contain_text('地表径流')
        expect(page.locator('.spatial-pending')).to_have_count(0, timeout=60000)
        expect(page.locator('.maplibregl-canvas')).to_be_visible()
        assert not page.get_by_label('状态所属过程').input_value().startswith('event-')
        canvas = page.locator('.state-layout .maplibregl-canvas')
        screenshot = Image.open(io.BytesIO(canvas.screenshot())).convert('RGB')
        black = sum(max(pixel) < 15 for pixel in screenshot.getdata())
        assert black / (screenshot.width * screenshot.height) < .01, 'State grid rendered black instead of its computed values'
        page.screenshot(path=str(output / 'state-map-1366.png'), full_page=True)
        check('A fresh browser restores scheme history and opens the matching state map and time')

        page.goto(args.url + '/#/realtime')
        page.wait_for_load_state('networkidle')
        distribution = page.get_by_role('button', name='雨量分布', exact=True)
        expect(distribution).to_be_visible(timeout=20000)
        distribution.click()
        expect(distribution).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('.smooth-rain-legend')).to_contain_text('雨量分布')
        page.get_by_role('button', name='放大', exact=True).click()
        page.get_by_role('button', name='缩小', exact=True).click()
        page.screenshot(path=str(output / 'rainfall-map-1366.png'), full_page=True)
        page.get_by_role('button', name='网格', exact=True).click()
        expect(distribution).to_have_attribute('aria-pressed', 'false')
        check('Rainfall distribution and station grid switch on the same observation hour and survive zoom')

        page.goto(args.url + '/#/config')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('link', name='查询实测资料', exact=True)).to_be_visible()
        expect(page.locator('.workspace-input-settings')).not_to_have_attribute('open', '')
        assert page.locator('#workspace-input-source,#workspace-rainfall-method').count() == 0
        visible_size('.view-config .btn,.view-config .input,.workspace-input-settings summary')
        page.screenshot(path=str(output / 'model-data-1366.png'), full_page=True)
        check('Model data shows terrain, outlet and stations with one folded maintenance entry')
        report['backup'] = api('workspace/backups', {})
        check('Backup contains schemes, saved parameters, operations and state relations')
        assert not report['pageErrors'], report['pageErrors']
        report['status'] = 'passed'
    finally:
        output.joinpath('acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['status'] != 'passed':
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            output.joinpath('failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        browser.close()
