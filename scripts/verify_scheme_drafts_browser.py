"""Exercise saved settings, browser drafts and concurrent edits without running jobs."""
import argparse
import copy
import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:18902')
parser.add_argument('--source-report',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
report={'checks':[],'errors':[],'schemeWrites':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':768})
    page=context.new_page()
    page.on('pageerror',lambda error:report['errors'].append(str(error)))
    def record_write(response):
        if '/api/model/model-schemes' in response.url and response.request.method=='POST':
            report['schemeWrites'].append({'url':response.url,'status':response.status,'request':response.request.post_data_json})
    context.on('response',record_write)
    def api(path,body=None):
        response=context.request.post(args.url+'/api/model/'+path,data=body,timeout=60000) if body is not None else context.request.get(args.url+'/api/model/'+path,timeout=60000)
        assert response.ok,response.text()
        return response.json()
    def visit(target):
        target.goto(args.url+'/#/calibration?schemeId='+saved['id'])
        expect(target.get_by_role('button',name='编辑方案',exact=True)).to_be_visible(timeout=60000)
        target.wait_for_load_state('networkidle')
    def settings(target):
        target.get_by_role('button',name='率定设置',exact=True).click()
        expect(target.locator('#max-evals')).to_be_visible()
    def close_settings(target):
        target.locator('.el-dialog').filter(has=target.locator('#max-evals')).get_by_role('button',name='完成',exact=True).click()
    def set_budget(target,value,success=True):
        settings(target);target.locator('#max-evals').fill(str(value));target.locator('#max-evals').press('Tab');close_settings(target)
        expect(target.locator('.scheme-heading')).to_contain_text('修改已暂存' if success else '另一页面',timeout=30000)
    def check(name):
        report['checks'].append(name);print('PASS',name,flush=True)
    try:
        source_id=json.loads(Path(args.source_report).read_text(encoding='utf-8'))['schemeId']
        source=api('model-schemes/'+source_id)
        saved=api('model-schemes',{'name':'草稿及冲突验收 '+time.strftime('%H%M%S'),'definition':copy.deepcopy(source['definition']),
                                 'calculationDraft':copy.deepcopy(source['calculationDraft']),'editor':copy.deepcopy(source['savedEditor'])})
        report['schemeId']=saved['id'];visit(page)
        before=api('model-schemes/'+saved['id'])
        page.get_by_role('button',name='编辑方案',exact=True).click();set_budget(page,8)
        assert api('model-schemes/'+saved['id'])['calculationDraft']==before['calculationDraft']
        # Normal navigation flushes the draft and never asks to save the scheme.
        page.goto(args.url+'/#/data');expect(page.locator('.data-workspace')).to_be_visible(timeout=30000)
        visit(page);settings(page);expect(page.locator('#max-evals')).to_have_value('4');close_settings(page)
        page.get_by_role('button',name='恢复上次草稿',exact=True).click()
        settings(page);expect(page.locator('#max-evals')).to_have_value('8');close_settings(page)
        expect(page.get_by_role('button',name='保存设置并开始率定',exact=True)).to_be_visible()
        page.get_by_role('button',name='放弃修改',exact=True).click()
        expect(page.get_by_role('button',name='编辑方案',exact=True)).to_be_visible()
        assert api('model-schemes/'+saved['id'])['calculationDraft']==before['calculationDraft']
        check('draft-navigation-refresh-resume-and-discard-preserve-saved-settings')
        page.get_by_role('button',name='编辑方案',exact=True).click();settings(page)
        page.get_by_role('button',name='使用模型默认值',exact=True).click()
        expect(page.get_by_role('button',name='使用模型默认值',exact=True)).to_be_enabled(timeout=30000)
        assert page.locator('.el-message-box:visible').count()==0
        assert page.locator('.el-dialog:visible').count()==1
        close_settings(page);page.get_by_role('button',name='放弃修改',exact=True).click()
        expect(page.get_by_role('button',name='编辑方案',exact=True)).to_be_visible()
        check('default-parameter-load-does-not-open-a-second-hidden-dialog')
        page2=context.new_page();page2.on('pageerror',lambda error:report['errors'].append(str(error)))
        visit(page2)
        page.get_by_role('button',name='编辑方案',exact=True).click()
        page2.get_by_role('button',name='编辑方案',exact=True).click()
        set_budget(page,12);set_budget(page2,16,success=False)
        assert api('model-schemes/'+saved['id'])['editor']['maxEvaluations']==12
        page.get_by_role('button',name='保存修改',exact=True).click()
        expect(page.get_by_role('button',name='编辑方案',exact=True)).to_be_visible(timeout=30000)
        committed=api('model-schemes/'+saved['id'])
        assert committed['calculationDraft']['calibration']['optimizer']['maxEvaluations']==12
        assert committed['revision']==before['revision']+1
        page2.get_by_role('button',name='保存修改',exact=True).click()
        expect(page2.locator('.scheme-heading')).to_contain_text('另一页面',timeout=30000)
        assert api('model-schemes/'+saved['id'])['revision']==committed['revision']
        assert api('model-schemes/'+saved['id'])['calculationDraft']==committed['calculationDraft']
        page2.screenshot(path=str(out/'concurrent-conflict.png'),full_page=True)
        page2.close()
        check('two-pages-report-conflict-once-and-never-overwrite-the-first-save')
        visit(page);page.get_by_role('button',name='人工试调',exact=True).click()
        field=page.locator('#parameter-SM');field.fill('37');field.press('Tab')
        page.reload();expect(page.get_by_role('button',name='人工试调',exact=True)).to_be_visible(timeout=60000)
        page.get_by_role('button',name='人工试调',exact=True).click()
        expect(page.locator('#parameter-SM')).to_have_value('37')
        assert api('model-schemes/'+saved['id'])['calculationDraft']==committed['calculationDraft']
        check('temporary-manual-values-survive-refresh-without-changing-scheme')
        assert not report['errors'],report['errors']
        report['status']='passed'
    except Exception as error:
        report['failure']=str(error);page.screenshot(path=str(out/'failure.png'),full_page=True)
        print(page.locator('body').inner_text(),flush=True)
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
