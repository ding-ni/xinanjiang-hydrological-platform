"""Exercise the process-first workbench against actual saved 16-flood records."""
import argparse
import json
import re
import time
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:18904')
parser.add_argument('--output',required=True)
args=parser.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
expect.set_options(timeout=45000)
scheme='SCHEME-8a5eea9752cf4cf79ffec638f98cf086'
automatic='OP-c5ebbdd1a9db4c18bf503aeefe07b691'
report={'checks':[],'errors':[],'sizes':[],'requests':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':768})
    page=context.new_page();page.set_default_timeout(45000)
    page.on('pageerror',lambda error:report['errors'].append(str(error)))
    page.on('request',lambda request:report['requests'].append({'method':request.method,'url':request.url}) if '/api/model/' in request.url else None)
    def api(path,body=None):
        response=context.request.get(args.url+'/api/model/'+path,timeout=120000) if body is None else context.request.post(args.url+'/api/model/'+path,data=body,timeout=120000)
        assert response.ok,(path,response.status,response.text()[:1000])
        return response.json()
    def check(label):report['checks'].append(label);print('PASS',label,flush=True)
    def goto(path):page.goto(args.url+'/#'+path);page.wait_for_load_state('networkidle')
    def screenshot(name):page.screenshot(path=str(out/(name+'.png')),full_page=True)
    try:
        original=api('model-schemes/'+scheme)
        goto('/calibration?schemeId='+scheme)
        expect(page.locator('.scheme-heading')).to_contain_text('我的测试-16场洪水')
        expect(page.locator('.process-panel canvas')).to_be_visible()
        for width,height in [(1366,768),(1920,1080)]:
            page.set_viewport_size({'width':width,'height':height})
            page.get_by_role('button',name='人工试调',exact=True).click()
            page.wait_for_timeout(700)
            plot=page.locator('.process-panel .chart-host').bounding_box()
            assert plot['height']>=420,plot
            chart=page.locator('.process-panel .chart-host').evaluate("el=>{const app=document.querySelector('#app').__vue_app__;return {width:el.clientWidth,height:el.clientHeight}}")
            report['sizes'].append({'viewport':[width,height],'plot':plot,'chart':chart})
            screenshot('manual-'+str(width))
            page.locator('.process-panel').screenshot(path=str(out/('manual-chart-'+str(width)+'.png')))
            page.get_by_role('button',name='收起洪水列表',exact=True).click()
            assert page.locator('.process-panel .chart-host').bounding_box()['width']>=plot['width']
            page.get_by_role('button',name='展开洪水列表',exact=True).click()
            page.get_by_role('button',name='自动率定',exact=True).click()
        check('two viewport sizes retain a full plotting host in manual mode')
        page.get_by_role('button',name='人工试调',exact=True).click()
        page.locator('.flood-process').filter(has_text='31210811').first.click()
        page.get_by_role('button',name=re.compile(r'^计算记录（\d+）$')).click()
        manual_name=next(row['name'] for row in api('result-catalog')['results'] if row['type']=='manualTrial' and row['status']=='completed')
        page.locator('.history-row').filter(has_text=manual_name).first.locator('button').first.click()
        expect(page.locator('.curve-chip')).to_have_count(3)
        expect(page.locator('.curve-controls')).not_to_contain_text('条件不同')
        expect(page.locator('.curve-controls')).not_to_contain_text('未算此场')
        page.locator('.process-panel').screenshot(path=str(out/'three-curve-comparison.png'))
        check('one automatic reference and both successful manual curves share the same flood conditions')
        page.get_by_role('button',name=re.compile(r'^计算记录（\d+）$')).click()
        rows=page.locator('.history-row')
        assert rows.count()>=2
        # Select the original audited record even after replay/trial acceptance.
        original_name=next(row['name'] for row in api('result-catalog')['results'] if row['id']==automatic)
        rows.filter(has_text=original_name).first.locator('button').first.click()
        page.get_by_role('button',name='全场评价',exact=True).click()
        expect(page.locator('.evaluation')).to_contain_text('0.657')
        expect(page.locator('.evaluation')).to_contain_text('54.3%')
        page.get_by_text('搜索评价详情 · 合并窗口',exact=True).click()
        expect(page.locator('.evaluation')).to_contain_text('549.0 h')
        expect(page.locator('.evaluation')).to_contain_text('2022-03-21 21:00')
        expect(page.locator('.evaluation')).to_contain_text('2022-04-13 18:00')
        check('saved 16-flood result shows window summary while retaining the 549h merged metric')
        with page.expect_download() as downloading:
            page.get_by_role('button',name='导出逐场评价',exact=True).click()
        csv_path=out/'evaluation.csv';downloading.value.save_as(csv_path)
        exported=csv_path.read_text(encoding='utf-8-sig')
        assert '0.6571184380900811' in exported and '54.26144337361899' in exported
        check('evaluation CSV exports the same daily summary as the page')
        page.get_by_role('button',name='本组参数',exact=True).click()
        expect(page.locator('.parameter-results')).to_contain_text('仅看达到边界（7）')
        check('seven searched parameters at bounds are visible without modifying values')
        page.get_by_role('button',name='设置历史',exact=True).click()
        page.locator('.history-item').first.get_by_role('button',name=re.compile('^(命名|改名)$')).click()
        page.locator('.el-message-box__input input').fill('参数与洪水配置验收')
        page.locator('.el-message-box__btns button').last.click()
        expect(page.locator('.history-item').first).to_contain_text('参数与洪水配置验收')
        page.get_by_role('button',name='关闭',exact=True).last.click()
        after=api('model-schemes/'+scheme)
        assert original['revision']==after['revision'] and original['calculationDraft']==after['calculationDraft']
        check('history can be named without creating a scheme revision')
        goto('/archive?result='+automatic)
        expect(page.locator('.result-detail canvas')).to_be_visible()
        screenshot('results')
        before_tasks=len(api('tasks')['tasks'])
        page.locator('.result-save-bar').get_by_role('button',name='✎ 修改名称',exact=True).click()
        expect(page.get_by_role('textbox',name='计算记录名称',exact=True)).to_be_visible()
        page.get_by_role('textbox',name='计算记录名称',exact=True).fill('我的16场 · 过程工作台验收')
        page.get_by_role('button',name='保存整理',exact=True).click()
        expect(page.locator('.result-save-bar')).to_contain_text('我的16场 · 过程工作台验收')
        favorite=page.locator('.result-save-bar').get_by_role('button',name=re.compile('(收藏|已收藏)$'))
        if favorite.get_attribute('aria-pressed')!='true':favorite.click()
        expect(favorite).to_have_attribute('aria-pressed','true')
        assert api('result-metadata/'+automatic)['favorite']
        assert len(api('tasks')['tasks'])==before_tasks
        check('rename and immediate favorite update metadata without calculation')
        page.get_by_role('button',name='本次计算设置',exact=True).click()
        expect(page.locator('.conditions')).to_be_visible()
        assert '#/archive' in page.url
        check('calculation settings are read-only inside the result viewer')
        before_parameters=api('parameter-schemes')
        snapshot=api('results/'+automatic+'/parameter-snapshot')
        assert snapshot['sourceResultId']==automatic and snapshot['parameters']
        assert before_parameters==api('parameter-schemes')
        page.get_by_role('button',name='用这组参数模拟其他洪水',exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('#simulation-parameter')).to_have_value('@result')
        check('result parameters load into simulation without a parameter-library save')
        goto('/state?operation='+automatic+'&event=xf-31220317')
        expect(page.locator('.state-slider input')).to_be_visible()
        expect(page.locator('.map-panel canvas').first).to_be_visible()
        before_tasks=len(api('tasks')['tasks'])
        selector=page.get_by_role('combobox',name='状态回放时刻')
        values=selector.locator('option').evaluate_all('rows=>rows.map(row=>row.value)')
        assert len(values)>2
        selector.select_option(values[1]);page.wait_for_timeout(800)
        expect(page.locator('.grid-detail h3')).to_contain_text(values[1].replace('T',' ')[:16])
        # Delay a real response to expose the loading interval. No replacement
        # numerical values are supplied, and the next request must win.
        delayed_url='**/grid-state?time='+urllib.parse.quote(values[2],safe='')
        held=[]
        def delay_frame(route):held.append(route)
        page.route(delayed_url,delay_frame)
        selector.select_option(values[2])
        expect(page.locator('.state-reading')).to_contain_text(values[2].replace('T',' ')[:16])
        assert page.locator('.grid-detail').count()==0
        selector.select_option(values[1])
        expect(page.locator('.grid-detail h3')).to_contain_text(values[1].replace('T',' ')[:16])
        assert held,'Delayed frame request was not intercepted'
        held[0].continue_();page.wait_for_load_state('networkidle')
        expect(page.locator('.grid-detail h3')).to_contain_text(values[1].replace('T',' ')[:16])
        page.unroute(delayed_url,delay_frame)
        check('state loading never falls back to terminal data and late responses cannot replace the chosen time')
        assert len(api('tasks')['tasks'])==before_tasks
        assert page.get_by_role('button',name='查看完整末状态',exact=True).count()==0
        screenshot('state')
        check('browsing hourly states keeps one result/process context and creates no calculation')
        goto('/state?tab=library')
        assert page.get_by_text('高级操作',exact=True).count()==0
        assert page.get_by_text('全部计算状态',exact=True).count()==0
        check('daily state library excludes maintenance operations')
        goto('/results/'+automatic+'/sections?process=xf-31220317')
        expect(page.locator('.river-sections h1')).to_have_text('我的16场 · 过程工作台验收')
        expect(page.get_by_role('button',name='查看断面流量',exact=True)).to_be_enabled()
        started=time.monotonic();page.get_by_role('button',name='查看断面流量',exact=True).click()
        expect(page.locator('.section-answer')).to_be_visible()
        report['outletSeconds']=round(time.monotonic()-started,3)
        assert len(api('tasks')['tasks'])==before_tasks
        check('outlet reads its saved flood process without a routing task')
        assert not report['errors'],report['errors']
        report['status']='passed'
    except Exception:
        report['status']='failed';screenshot('failure');raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
