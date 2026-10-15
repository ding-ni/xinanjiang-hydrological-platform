"""Scheme view/edit/version acceptance against an isolated local workspace."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18897')
parser.add_argument('--output', default='开发产物/scheme-ui-acceptance/screenshots')
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width':1366,'height':768})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.url+'/#/calibration')
    page.wait_for_load_state('networkidle')
    schemes = context.request.get(args.url+'/api/model/model-schemes').json()['schemes']
    scheme = next(row for row in schemes if row['revision'] > 0)
    identifier = scheme['id']
    def saved(): return context.request.get(args.url+'/api/model/model-schemes/'+identifier).json()
    before = saved()
    page.locator('[data-scheme-id="'+identifier+'"]').click()
    expect(page.locator('.revision-label')).to_contain_text('查看')
    expect(page.locator('.flood-row')).to_have_count(min(10,scheme['summary']['selectedCount']))
    assert page.locator('.purpose-select').first.is_disabled()
    page.locator('[aria-label="搜索洪水"]').fill('2024')
    page.locator('[aria-label="搜索洪水"]').fill('')
    page.locator('.flood-process').last.click()
    assert saved()['draftRevision'] == before['draftRevision']
    assert saved()['calculationDraft'] == before['calculationDraft']
    page.get_by_role('button',name='率定设置',exact=True).click()
    assert page.locator('#max-evals').is_disabled()
    page.screenshot(path=str(output/'settings-readonly-1366.png'),full_page=True)
    page.get_by_role('button',name='完成',exact=True).click()
    page.get_by_role('button',name='编辑方案',exact=True).click()
    page.locator('.purpose-select').first.select_option('验证')
    page.wait_for_timeout(800)
    page.get_by_role('button',name='放弃修改',exact=True).click()
    expect(page.locator('.revision-label')).to_contain_text('查看')
    assert saved()['calculationDraft'] == before['calculationDraft']
    page.get_by_role('button',name='编辑方案',exact=True).click()
    page.get_by_role('button',name='率定设置',exact=True).click()
    budget=480 if before['calculationDraft']['calibration']['optimizer']['maxEvaluations']==120 else 120
    page.locator('#max-evals').fill(str(budget))
    page.locator('#max-evals').press('Tab')
    page.get_by_role('button',name='完成',exact=True).click()
    page.get_by_role('button',name='保存新版本',exact=True).click()
    expect(page.locator('.revision-label')).to_contain_text('查看',timeout=20000)
    after = saved()
    assert after['revision'] == before['revision']+1
    original = context.request.get(args.url+'/api/model/model-schemes/'+identifier+'?revision='+str(before['revision'])).json()
    assert original['calculationDraft'] == before['calculationDraft']
    assert original['editor'] == {}
    page.screenshot(path=str(output/'saved-1366.png'),full_page=True)
    page.set_viewport_size({'width':1920,'height':1080})
    page.screenshot(path=str(output/'saved-1920.png'),full_page=True)
    page.get_by_role('button',name='编辑方案',exact=True).click()
    page.get_by_role('button',name='率定设置',exact=True).click()
    page.get_by_role('button',name='仅产流',exact=True).click()
    bounds=page.get_by_role('spinbutton',name='SM 搜索上限',exact=True)
    bounds.fill('80');bounds.press('Tab')
    box=page.get_by_role('checkbox',name='SM 参与率定',exact=True)
    box.uncheck();box.check()
    expect(bounds).to_have_value('80')
    box.uncheck()
    page.get_by_role('button',name='完成',exact=True).click()
    page.get_by_role('button',name='保存新版本',exact=True).click()
    expect(page.locator('.revision-label')).to_contain_text('查看',timeout=20000)
    stored=saved()
    assert stored['savedEditor']['searchRangeOverrides']['SM']['maximum']==80
    page.reload();page.wait_for_load_state('networkidle')
    if page.locator('[data-scheme-id="'+identifier+'"]').count(): page.locator('[data-scheme-id="'+identifier+'"]').click()
    page.get_by_role('button',name='率定设置',exact=True).click()
    expect(page.get_by_role('spinbutton',name='SM 搜索上限',exact=True)).to_have_value('80')
    page.get_by_role('button',name='完成',exact=True).click()
    # An independent browser tab retains its own optimistic draft revision.
    second=context.new_page();second.on('pageerror',lambda error:errors.append(str(error)))
    second.goto(args.url+'/#/calibration?schemeId='+identifier);second.wait_for_load_state('networkidle')
    page.get_by_role('button',name='编辑方案',exact=True).click()
    second.get_by_role('button',name='编辑方案',exact=True).click()
    page.locator('.purpose-select').first.select_option('验证');page.wait_for_timeout(900)
    second.locator('.purpose-select').first.select_option('独立检验')
    expect(second.locator('.scheme-error')).to_contain_text('另一页面',timeout=10000)
    second.close()
    page.get_by_role('button',name='放弃修改',exact=True).click()
    expect(page.locator('.revision-label')).to_contain_text('查看',timeout=15000)
    # The copied SQL schemes reference the user's large MDB products, which are
    # deliberately absent from this isolated UI fixture. Test station editing
    # against its installed baseline and copied local observation tables.
    response=context.request.post(args.url+'/api/model/preparation-defaults',data={'sourceBundleVersionId':'NANANXI_QC_20260819_CONTROL_V2','stationSourceId':'LOCAL_OBSERVATIONS','rainfallMethod':'stationIdw','idwPower':2,'minimumStations':1})
    assert response.ok,response.text()
    page.goto(args.url+'/#/config');page.wait_for_load_state('networkidle')
    page.get_by_role('button',name='流域与站网',exact=True).click()
    expect(page.locator('.workspace-input-settings')).to_contain_text('徐畈（二）')
    page.screenshot(path=str(output/'station-names-1920.png'),full_page=True)
    page.goto(args.url+'/#/diagnostics');page.wait_for_load_state('networkidle')
    expect(page.get_by_role('tab')).to_have_count(2)
    page.goto(args.url+'/#/realtime');page.wait_for_load_state('networkidle')
    page.locator('.replay-mode select').select_option('event')
    expect(page.locator('.event-list option')).to_have_count(227)
    assert page.locator('.event-list').evaluate('(node)=>node.scrollHeight>node.clientHeight')
    page.locator('.event-list').evaluate('(node)=>node.scrollTop=node.scrollHeight')
    page.screenshot(path=str(output/'public-catalogue-1920.png'),full_page=True)
    page.set_viewport_size({'width':1366,'height':768})
    page.screenshot(path=str(output/'public-catalogue-1366.png'),full_page=True)
    assert not errors, errors
    print(json.dumps({'checks':['view does not mutate','readonly settings','discard restores','save adds version','old revision immutable','disabled ranges persist after refresh','two-page edit conflict','Chinese stations','two diagnostic tabs','complete scrollable catalogue226'],'errors':errors}),flush=True)
    browser.close()
