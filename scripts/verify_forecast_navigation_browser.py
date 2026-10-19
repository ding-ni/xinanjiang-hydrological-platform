"""Read-only forecast/state navigation acceptance against an isolated workspace."""
import argparse
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
output=Path(args.output).resolve()
output.mkdir(parents=True,exist_ok=True)
report={'status':'failed','checks':[],'pageErrors':[]}
with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1366,'height':768})
    page.on('pageerror',lambda error:report['pageErrors'].append(str(error)))
    try:
        page.goto(args.url+'/#/forecast')
        page.wait_for_load_state('networkidle')
        # Inspect the rendered page before choosing controls.
        (output/'navigation-initial-ui.txt').write_text(page.locator('body').inner_text(),encoding='utf-8')
        expect(page.locator('.schedule-state')).to_contain_text('补做完成',timeout=30000)
        expect(page.locator('#auto-record option')).to_have_count(4)
        expect(page.get_by_role('button',name='补做预报',exact=True)).to_be_disabled()
        expect(page.get_by_role('button',name='关闭自动预报',exact=True)).to_have_count(0)
        expect(page.get_by_role('button',name='格式示例',exact=True)).not_to_be_visible()
        expect(page.locator('.daily-result canvas')).to_be_visible()
        selected=page.locator('#auto-record').input_value()
        result=page.request.get(args.url+'/api/model/operations/'+selected+'/result').json()
        page.get_by_role('button',name='状态分布',exact=True).click()
        expect(page).to_have_url(re.compile(r'/#/state\?'),timeout=30000)
        assert 'operation='+selected in page.url and 'source='+result['primaryTaskId'] in page.url,page.url
        expect(page.get_by_role('dialog')).not_to_be_visible()
        report['checks'].append('Forecast state view opens the selected issue without an unsaved-result prompt')
        page.go_back();page.wait_for_load_state('networkidle')
        expect(page.locator('.daily-result canvas')).to_be_visible(timeout=30000)
        page.get_by_role('button',name='河道流量',exact=True).click()
        expect(page).to_have_url(re.compile('/#/results/'+selected+'/sections'),timeout=30000)
        expect(page.get_by_role('dialog')).not_to_be_visible()
        report['checks'].append('Return to forecast and open its own river sections without leaving-workspace prompts')
        page.screenshot(path=str(output/'forecast-sections-1366x768.png'),full_page=True)
        assert not report['pageErrors'],report['pageErrors']
        report['status']='passed'
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        (output/'navigation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
