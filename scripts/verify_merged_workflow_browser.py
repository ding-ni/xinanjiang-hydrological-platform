"""Browser acceptance for historical observations and delivered schemes."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', default='http://127.0.0.1:18898')
parser.add_argument('--output', default='开发产物/merged-browser-screenshots')
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    base = args.url + '/api/model/'
    def get(path):
        response = context.request.get(base + path, timeout=60000)
        assert response.ok, response.text()
        return response.json()
    page.goto(args.url + '/#/calibration')
    page.wait_for_load_state('networkidle')
    rows = get('model-schemes')['schemes']
    delivered = next(saved for row in rows if row['name'].startswith('同事优选洪水')
                     for saved in [get('model-schemes/' + row['id'])] if saved['definition']['inputs'].get('dataMode') != 'historical-database')
    identifier = delivered['id']
    page.locator('[data-scheme-id="' + identifier + '"]').click()
    expect(page.locator('.revision-label')).to_contain_text('查看')
    assert page.locator('.purpose-select').first.is_disabled()
    page.locator('.flood-process').last.click()
    assert get('model-schemes/' + identifier)['draftRevision'] == delivered['draftRevision']
    page.screenshot(path=str(output / 'delivered-1366.png'), full_page=True)
    page.get_by_role('button', name='编辑方案', exact=True).click()
    page.locator('.purpose-select').first.select_option('未使用')
    page.wait_for_timeout(700)
    page.get_by_role('button', name='放弃修改', exact=True).click()
    expect(page.locator('.revision-label')).to_contain_text('查看')
    assert get('model-schemes/' + identifier)['calculationDraft'] == delivered['calculationDraft']
    page.get_by_role('button', name='编辑方案', exact=True).click()
    page.get_by_role('button', name='全部洪水', exact=True).click()
    page.get_by_label('洪水年份', exact=True).select_option('2007')
    year_count = len([row for row in get('database/catalog')['events'] if row['start'].startswith('2007')])
    expect(page.locator('.flood-row')).to_have_count(year_count)
    page.locator('.flood-row input[type=checkbox]').first.check()
    page.get_by_role('button', name='保存新版本', exact=True).click()
    expect(page.get_by_text('重新准备方案输入', exact=True)).to_be_visible()
    page.locator('.el-message-box').get_by_role('button', name='保存新版本', exact=True).click()
    expect(page.locator('.revision-label')).to_contain_text('查看', timeout=30000)
    changed = get('model-schemes/' + identifier)
    assert changed['revision'] == delivered['revision'] + 1
    assert changed['definition']['inputs']['dataMode'] == 'historical-database'
    assert any(row['start'].startswith('2007') for row in changed['calculationDraft']['period']['events'])
    assert get('model-schemes/' + identifier + '?revision=' + str(delivered['revision']))['calculationDraft'] == delivered['calculationDraft']
    page.reload()
    page.wait_for_load_state('networkidle')
    if page.locator('[data-scheme-id="' + identifier + '"]').count():
        page.locator('[data-scheme-id="' + identifier + '"]').click()
    page.get_by_role('button', name='全部洪水', exact=True).click()
    page.get_by_label('洪水年份', exact=True).select_option('2007')
    page.locator('.flood-process').first.click()
    expect(page.locator('.process-status').filter(has_text='读取雨流过程')).to_have_count(0, timeout=60000)
    expect(page.locator('.process-panel .hydrology-figure')).to_be_visible()
    assert not page.locator('.process-status.field-error').count()
    expect(page.locator('.flood-panel .panel-heading')).to_contain_text('已选专有时段 13 场')
    expect(page.locator('.flood-panel .panel-heading')).to_contain_text('原排除时段 1 场')
    page.get_by_role('button', name='编辑方案', exact=True).click()
    page.get_by_role('button', name='率定设置', exact=True).click()
    page.get_by_role('button', name='采用原方案最佳参数作为初值', exact=True).click()
    expect(page.get_by_role('button', name='采用原方案最佳参数作为初值', exact=True)).to_be_enabled(timeout=60000)
    page.get_by_role('button', name='完成', exact=True).click()
    assert not page.locator('.calculation-status [role=alert]').count(), page.locator('.calculation-status').inner_text() if page.locator('.calculation-status').count() else ''
    page.get_by_role('button', name='保存新版本', exact=True).click()
    expect(page.locator('.revision-label')).to_contain_text('查看', timeout=30000)
    adopted = get('model-schemes/' + identifier)
    assert adopted['calculationDraft']['routing']['versionId'] != delivered['calculationDraft']['routing']['versionId']
    assert adopted['calculationDraft']['period'] == changed['calculationDraft']['period']
    assert adopted['calculationDraft']['calibration']['searchParameters'] == changed['calculationDraft']['calibration']['searchParameters']
    assert adopted['calculationDraft']['calibration']['optimizer']['maxEvaluations'] == changed['calculationDraft']['calibration']['optimizer']['maxEvaluations']
    assert adopted['definition']['inputs'] == changed['definition']['inputs']
    expect(page.get_by_role('button', name='开始率定', exact=True)).to_be_disabled()
    expect(page.get_by_text('连续演算开始时间须早于最早评价时刻', exact=False)).to_be_visible()
    page.set_viewport_size({'width': 1920, 'height': 1080})
    page.screenshot(path=str(output / 'version-1920.png'), full_page=True)
    page.goto(args.url + '/#/realtime')
    page.wait_for_load_state('networkidle')
    page.locator('.replay-mode select').select_option('event')
    page.wait_for_function("!document.querySelector('.event-list').disabled")
    page.locator('.replay-controls label').filter(has_text='年份').locator('select').select_option('2007')
    expect(page.locator('.event-list option')).to_have_count(year_count + 1)
    page.locator('.event-list').select_option('xf-31070314')
    page.wait_for_function("!document.querySelector('.event-list').disabled")
    expect(page.locator('.replay-window')).to_contain_text('2007-03-14')
    assert not page.get_by_text('所选年份无固定版本 QC 降雨：2007', exact=True).count()
    page.screenshot(path=str(output / 'observations2007-1920.png'), full_page=True)
    page.set_viewport_size({'width': 1366, 'height': 768})
    page.screenshot(path=str(output / 'observations2007-1366.png'), full_page=True)
    page.goto(args.url + '/#/data?tab=inputs')
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('heading', name='历史库入库记录')).to_be_visible()
    expect(page.locator('.source-records tbody tr')).not_to_have_count(4)
    page.goto(args.url + '/#/config')
    page.wait_for_load_state('networkidle')
    expect(page.locator('.workspace-input-settings')).to_contain_text('使用水文资料库')
    expect(page.locator('.workspace-input-settings')).to_contain_text('徐畈（二）')
    assert not page.locator('#workspace-station-source').count()
    page.screenshot(path=str(output / 'network-1366.png'), full_page=True)
    assert not errors, errors
    (output / 'report.json').write_text(json.dumps({'errors': errors, 'schemeId': identifier,
        'oldRevision': delivered['revision'], 'newRevision': changed['revision'], 'bestParameterRevision': adopted['revision'], 'publicEvents': 226}, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
print('Merged workflow browser acceptance passed')
