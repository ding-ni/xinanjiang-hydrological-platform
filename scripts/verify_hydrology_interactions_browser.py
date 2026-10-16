"""Verify rain layout, catalogue, independent sections, GIS and states on an isolated server."""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:18902')
parser.add_argument('--source-report',required=True,help='Report from verify_trial_workspace_browser.py')
parser.add_argument('--scope-report',required=True,help='Report from verify_trial_scope_equivalence.py')
parser.add_argument('--output',required=True)
args=parser.parse_args()
source_report=json.loads(Path(args.source_report).read_text(encoding='utf-8'))
scope_report=json.loads(Path(args.scope_report).read_text(encoding='utf-8'))
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
report={'checks':[],'errors':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':768})
    page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
    def api(path,body=None):
        response=context.request.post(args.url+'/api/model/'+path,data=body,timeout=60000) if body is not None else context.request.get(args.url+'/api/model/'+path,timeout=60000)
        assert response.ok,(path,response.text())
        return response.json()
    def check(name):
        report['checks'].append(name);print('PASS',name,flush=True)
    def visit(path):
        page.goto(args.url+'/#'+path);page.wait_for_load_state('networkidle')
    def screenshots(name):
        for width,height in ((1366,768),(1920,1080)):
            page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(400)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(out/f'{name}-{width}.png'),full_page=True)
            if name=='rain-details':
                page.locator('.observed-process').screenshot(path=str(out/f'rain-process-{width}.png'))
                page.locator('.right-detail').screenshot(path=str(out/f'station-process-{width}.png'))
    try:
        visit('/realtime')
        page.locator('.replay-mode select').select_option('event')
        page.locator('.replay-controls label').filter(has_text='年份').locator('select').select_option('1988')
        expect(page.locator('.event-list')).to_be_enabled(timeout=60000)
        page.locator('.event-list').select_option('xf-31880808')
        expect(page.locator('.replay-window')).to_contain_text('1988-08-08',timeout=60000)
        page.get_by_label('降雨过程测站',exact=True).select_option('70115800')
        expect(page.locator('.rain-total')).to_contain_text('39.3 mm')
        page.locator('.time-picker input').fill('1988-08-08T07:00')
        page.locator('.time-picker input').dispatch_event('change')
        expect(page.locator('.metric').filter(has_text='本小时流域面雨量')).to_contain_text('3.5 mm')
        page.locator('#monitor-station').select_option('70115800')
        expect(page.locator('.right-detail')).to_be_visible(timeout=60000)
        screenshots('rain-details')
        for width in (1366,1920):
            page.set_viewport_size({'width':width,'height':768});page.wait_for_timeout(400)
            assert page.locator('.map-card').bounding_box()['width']>=450
        # Read the rendered chart, then verify zoom survives a time-only update.
        chart=page.locator('.observed-process .chart-host')
        manifest=context.request.get(args.url+'/.vite/manifest.json').json()
        chart_module=next(row['file'] for row in manifest.values() if row.get('name')=='echarts')
        chart.evaluate("async(el,path)=>{const module=await import('/'+path);window.acceptChart=Object.values(module)[0](el);window.acceptChart.dispatchAction({type:'dataZoom',start:20,end:80})}",chart_module)
        page.locator('.time-picker input').fill('1988-08-08T08:00');page.locator('.time-picker input').dispatch_event('change')
        page.wait_for_timeout(300)
        assert page.evaluate('Math.abs(window.acceptChart.getOption().dataZoom[0].start-20)<0.01')
        check('1988-rain-preserved-responsive-detail-map-and-chart-zoom')
        visit('/data');page.get_by_role('button',name='洪水目录',exact=True).click()
        page.get_by_role('button',name='洪峰流量（m³/s）排序',exact=True).click()
        peaks=[float(value) for value in page.locator('.event-section tbody .numeric').all_text_contents() if value.strip()!='—']
        assert peaks==sorted(peaks)
        page.get_by_role('button',name='洪峰流量（m³/s）排序',exact=True).click()
        peaks=[float(value) for value in page.locator('.event-section tbody .numeric').all_text_contents() if value.strip()!='—']
        assert peaks==sorted(peaks,reverse=True)
        screenshots('catalogue')
        check('flood-catalogue-numeric-sort-before-pagination')
        visit('/config?tab=routing')
        page.get_by_role('button',name='我的断面',exact=True).click()
        expect(page.locator('.topology-map canvas')).to_be_visible(timeout=60000)
        # A real map click defines the position, with no result or parameter selection.
        page.wait_for_function("document.querySelector('.topology-map')?.__hydroMap?.isStyleLoaded()")
        position=page.locator('.topology-map').evaluate("el=>{const p=el.__hydroMap.project([119.45537819524547,30.143731102817043]);return {x:p.x,y:p.y}}")
        page.locator('.topology-map canvas').click(position=position)
        expect(page.get_by_role('button',name='保存新断面',exact=True)).to_be_visible(timeout=60000)
        name='验收断面 '+time.strftime('%H%M%S')
        page.locator('.fields label').filter(has_text='断面名称').locator('input').fill(name)
        with page.expect_response(lambda r:r.url.endswith('/api/model/river-sections') and r.request.method=='POST') as saving:
            page.get_by_role('button',name='保存新断面',exact=True).click()
        assert saving.value.ok,saving.value.text();section=saving.value.json();report['sectionId']=section['id']
        page.reload();page.wait_for_load_state('networkidle');page.get_by_role('button',name='我的断面',exact=True).click()
        expect(page.get_by_label('我的断面',exact=True).locator('option').filter(has_text=name)).to_have_count(1)
        page.get_by_label('我的断面',exact=True).select_option(section['id'])
        page.get_by_role('button',name='修改名称',exact=True).click()
        page.locator('.el-message-box__input input').fill(name+' 改名')
        page.locator('.el-message-box').get_by_role('button',name='确定',exact=True).click()
        expect(page.get_by_label('我的断面',exact=True).locator('option:checked')).to_contain_text('改名')
        screenshots('sections')
        check('section-position-save-reload-rename-without-any-result')
        page.get_by_role('button',name='GIS图件',exact=True).click()
        page.get_by_role('button',name='生成地形河网与汇流分区图',exact=True).click()
        expect(page.locator('.spatial-results')).to_be_visible(timeout=180000)
        for label in ('地形与河网','汇流单元分区'):
            page.locator('.figure-tools').get_by_role('button',name=label,exact=True).click()
            image=page.locator('.spatial-figure img')
            expect(image).to_be_visible()
            page.wait_for_function("document.querySelector('.spatial-figure img')?.naturalWidth>0")
            response=context.request.get(urljoin(args.url,image.get_attribute('src')));assert response.ok and response.body().startswith(b'\x89PNG')
        package=context.request.get(urljoin(args.url,page.get_by_role('link',name='下载 GIS 成果包',exact=True).get_attribute('href')),timeout=60000)
        assert package.ok and package.body().startswith(b'PK')
        screenshots('gis')
        check('gis-two-pngs-and-package-generated-and-readable')
        all_id=scope_report['allOperation']
        processes=api('result-catalog/'+all_id+'/processes')['processes']
        visit('/results/'+all_id+'/sections?process=CALIBRATION&section=SECTION-XUFAN')
        for process in processes:
            page.get_by_label('断面查询洪水',exact=True).select_option(process['id'])
            expect(page.get_by_label('已保存断面',exact=True).locator('option').filter(has_text='徐畈（二）')).to_have_count(1,timeout=60000)
            page.get_by_label('已保存断面',exact=True).select_option('SECTION-XUFAN')
            with page.expect_response(lambda r:r.url.endswith('/api/model/result-section-queries') and r.request.method=='POST') as pending:
                page.get_by_role('button',name='查看断面流量',exact=True).click()
            data=pending.value.json()['result']['pointQuery']['series']
            expected=process['hydrograph']['forecast']
            assert data['time']==[row['time'] for row in expected]
            assert data['flow_cms']==[row['value'] for row in expected]
            expect(page.locator('.section-answer')).to_contain_text(process['name'],timeout=30000)
        page.get_by_label('已保存断面',exact=True).select_option(section['id'])
        page.get_by_role('button',name='查看断面流量',exact=True).click()
        expect(page.locator('.section-answer')).to_be_visible(timeout=180000)
        expect(page.locator('.section-answer')).to_contain_text('改名')
        page.get_by_role('button',name='查看断面流量',exact=True).click()
        expect(page.locator('.section-answer')).to_contain_text('已读取保存的断面过程',timeout=30000)
        with page.expect_download() as download:
            page.get_by_role('button',name='导出流量过程',exact=True).click()
        assert download.value.suggested_filename.endswith('.csv')
        screenshots('section-query')
        check('outlet-equals-each-selected-process-and-custom-section-reuses-exportable-query')
        visit('/state?operation='+all_id)
        expect(page.get_by_label('状态回放时刻',exact=True)).to_be_visible(timeout=60000)
        expect(page.get_by_role('button',name='查看完整末状态',exact=True)).to_be_visible(timeout=30000)
        assert page.get_by_role('button',name='命名保存末状态',exact=True).count()==0
        page.get_by_role('button',name='查看完整末状态',exact=True).click()
        expect(page.get_by_role('button',name='命名保存末状态',exact=True)).to_be_visible(timeout=60000)
        expect(page.get_by_label('状态回放时刻',exact=True).locator('option:checked')).to_contain_text('2017-06-21')
        check('continuous-first-window-never-masquerades-as-shared-full-terminal-state')
        calculations=[row for row in api('operations')['operations'] if row['status']=='completed' and row['operation']=='manualTrial']
        operation=next(row for row in calculations if row['taskId']==source_report['resultIds'][-1])
        visit('/state?operation='+operation['taskId'])
        expect(page.locator('.state-layout canvas').first).to_be_visible(timeout=60000)
        expect(page.get_by_label('状态回放时刻',exact=True)).to_be_visible(timeout=60000)
        times=page.get_by_label('状态回放时刻',exact=True).locator('option').evaluate_all('(rows)=>rows.map(row=>row.value)')
        canvas=page.locator('.state-layout canvas').first
        canvas.scroll_into_view_if_needed()
        host=page.locator('.state-layout .maplibregl-map')
        page.wait_for_function("document.querySelector('.state-layout .maplibregl-map')?.__hydroMap?.getLayer('grid-fill')")
        position=host.evaluate("el=>{const map=el.__hydroMap;for(const f of map.querySourceFeatures('grid')){const ring=f.geometry.coordinates[0];const xy=ring.reduce((a,p)=>[a[0]+p[0]/ring.length,a[1]+p[1]/ring.length],[0,0]);const p=map.project(xy);if(p.x>20&&p.y>20&&p.x<el.clientWidth-20&&p.y<el.clientHeight-20&&map.queryRenderedFeatures(p,{layers:['grid-fill']}).length)return {x:p.x,y:p.y}}throw Error('no visible grid')}")
        box=canvas.bounding_box();page.mouse.move(box['x']+position['x'],box['y']+position['y'])
        page.wait_for_timeout(150)
        assert host.evaluate("el=>el.__hydroMap.getFilter('grid-hover')[2]")!=''
        assert canvas.evaluate("el=>el.style.cursor")=='default'
        canvas.click(position=position)
        selected=host.evaluate("el=>el.__hydroMap.getFilter('grid-selected')[2]")
        assert selected
        page.locator('.variables>button').filter(has_text='上层张力水').click()
        assert host.evaluate("el=>el.__hydroMap.getFilter('grid-selected')[2]")==selected
        page.get_by_label('状态回放时刻',exact=True).select_option(times[0])
        expect(page.locator('.grid-id')).to_contain_text(selected)
        assert page.get_by_role('button',name='命名保存末状态',exact=True).count()==0
        page.get_by_label('状态回放时刻',exact=True).select_option(times[-1])
        expect(page.get_by_role('button',name='命名保存末状态',exact=True)).to_be_visible(timeout=30000)
        page.get_by_role('button',name='命名保存末状态',exact=True).click()
        state_name='命名末状态 '+time.strftime('%H%M%S')
        page.locator('.el-message-box__input input').fill(state_name)
        page.locator('.el-message-box').get_by_role('button',name='确定',exact=True).click()
        expect(page.locator('.el-message-box')).not_to_be_visible(timeout=30000)
        screenshots('process-state')
        page.locator('.state-layout').screenshot(path=str(out/'state-grid-interaction.png'))
        check('state-grid-hover-arrow-selection-and-time-variable-persistence')
        page.get_by_role('button',name='状态库',exact=True).first.click()
        expect(page.locator('.node-item').filter(has_text=state_name)).to_be_visible(timeout=60000)
        page.locator('.node-item').filter(has_text=state_name).click()
        with page.expect_download() as download:
            page.get_by_role('button',name='状态文件',exact=True).click()
        assert download.value.suggested_filename.endswith('.json')
        page.get_by_role('button',name='用于续算',exact=True).click()
        expect(page.locator('.simulation-view')).to_be_visible(timeout=30000)
        screenshots('continuation')
        check('state-process-final-only-naming-library-export-and-continuation')
        assert not report['errors'],report['errors']
        report['status']='passed'
    except Exception as failure:
        report['failure']=str(failure);page.screenshot(path=str(out/'failure.png'),full_page=True)
        print(page.locator('body').inner_text(),flush=True)
        raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
