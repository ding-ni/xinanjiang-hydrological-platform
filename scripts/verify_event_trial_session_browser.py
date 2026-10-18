"""Check workspace-scoped drafts and explicit legacy parameter reuse without calculating."""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:18917')
parser.add_argument('--scheme',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
report={'checks':[],'errors':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':768})
    page=context.new_page();expect.set_options(timeout=45000)
    page.on('pageerror',lambda error:report['errors'].append(str(error)))
    def api(path):
        response=context.request.get(args.url+'/api/model/'+path,timeout=120000)
        assert response.ok,response.text()
        return response.json()
    def goto(scheme):
        page.goto(args.url+'/#/calibration?schemeId='+scheme);page.reload();page.wait_for_load_state('networkidle')
    try:
        legacy='SCHEME-8a5eea9752cf4cf79ffec638f98cf086'
        original=api('model-schemes/'+legacy)
        goto(legacy)
        expect(page.get_by_role('button',name='沿用所选计算参数另建场次方案',exact=True)).to_be_visible()
        operations=[row for row in api('operations')['operations'] if row['status']=='completed' and
                    (row.get('runConfig',{}).get('request',{}).get('scheme',{}).get('id')==legacy or row.get('lineage',{}).get('schemeId')==legacy)]
        selected=max((row for row in operations if row['operation']=='calibration'),key=lambda row:row['createdAt'])
        result=api('operations/'+selected['taskId']+'/result')
        page.get_by_role('button',name='沿用所选计算参数另建场次方案',exact=True).click()
        box=page.locator('.el-message-box')
        name='旧参数数值复用验收 '+time.strftime('%H%M%S')
        box.locator('input').fill(name);box.get_by_role('button',name='建立方案',exact=True).click()
        expect(page.locator('.scheme-heading')).to_contain_text(name)
        created=next(row for row in api('model-schemes')['schemes'] if row['name']==name)
        editor=api('model-schemes/'+created['id'])['editor']
        assert editor['params']==result['bestParameters']
        version=result.get('routingCandidate',{}).get('routingVersionId') or result['frozenRequest']['routing']['versionId']
        expected=result.get('bestRoutingParameters') or next(row['parameters'] for row in api('routing-versions')['versions'] if row['routingVersionId']==version)
        assert editor['manualRoutingPayload']==expected
        assert editor['statePolicy']=='independent-warmup' and not editor.get('continuousOrigin') and not editor.get('scienceVersionId')
        assert editor['schemeWindows']==[] and all(row['purpose']=='未使用' for row in editor['events'])
        assert api('model-schemes/'+legacy)['calculationDraft']==original['calculationDraft']
        report['checks'].append('explicit-reuse-copies-exact-runoff-and-routing-values-without-origin-science-or-windows')

        goto(args.scheme)
        before=len(api('operations')['operations'])
        page.get_by_role('button',name='人工试调',exact=True).click()
        expect(page.locator('.curve-chip')).to_have_count(1)
        expect(page.locator('.calculation-record-info')).not_to_contain_text('人工试调')
        field=page.locator('#parameter-SM');field.fill('51');field.press('Tab')
        workspace=api('workspace')['workspace']['id']
        key=f"hydrology-manual-trial:{workspace}:nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km:{args.scheme}"
        page.wait_for_function("key=>JSON.parse(sessionStorage.getItem(key)||'null')?.params.SM===51",arg=key)
        page.reload();page.wait_for_load_state('networkidle')
        page.get_by_role('button',name='人工试调',exact=True).click()
        expect(page.locator('#parameter-SM')).to_have_value('51')
        expect(page.locator('.curve-chip')).to_have_count(1)
        expect(page.locator('.calculation-record-info')).not_to_contain_text('人工试调')
        assert len(api('operations')['operations'])==before
        report['checks'].append('actual-workspace-key-persists-draft-and-does-not-import-historical-trial-curves')
        for width,height in ((1366,768),(1920,1080)):
            page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            expect(page.locator('.selected-calculation')).to_contain_text('模拟峰值')
            page.locator('.process-panel').screenshot(path=str(out/f'event-manual-{width}.png'))
        page.get_by_role('button',name=re.compile('^计算记录（')).click()
        history=page.locator('.history-row').filter(has_text='人工试调').first
        history.locator('button').first.click()
        expect(page.locator('.curve-controls')).to_contain_text('历史人工试调')
        expect(page.locator('#parameter-SM')).not_to_be_visible()
        page.get_by_role('button',name='人工参数',exact=True).click()
        expect(page.locator('#parameter-SM')).to_have_value('51')
        report['checks'].append('explicit-historical-selection-shows-its-own-curve-without-changing-the-draft')
        assert not report['errors'],report['errors']
        report['status']='passed';print(json.dumps(report,ensure_ascii=False),flush=True)
    except Exception as failure:
        report['failure']=str(failure);page.screenshot(path=str(out/'failure.png'),full_page=True);raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
