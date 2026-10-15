"""Actual numeric result -> named parameter -> state process -> named state UI."""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
url='http://127.0.0.1:18897'
out=Path('开发产物/scheme-ui-acceptance/screenshots').resolve()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':1366,'height':768})
    page=context.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(url+'/#/simulation');page.wait_for_load_state('networkidle')
    case=context.request.get(url+'/api/model/catalog').json()['cases']['nananxi-xufan:1km']
    body={'schemaVersion':'2.0','requestId':'UI-STATE-'+str(time.time_ns()),'runType':'simulation',
          'context':{'contextKey':'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km','projectId':'nananxi-xufan','resolutionId':'1km','modelConfigId':'nananxi-xufan-1km'},
          'parameters':case['parameters'],'inputs':{'evaporation':{'mode':'monthly-climatology-assumption'}},
          'period':{'startTime':'2017-06-19T11:00','endTime':'2017-06-20T11:00','warmupDays':1}}
    response=context.request.post(url+'/api/model/simulation-tasks',data=body)
    assert response.ok,response.text()
    task=response.json();identifier=task['taskId'];print('Numeric state acceptance task: '+identifier,flush=True)
    deadline=time.monotonic()+240
    while task['status'] in ('queued','running') and time.monotonic()<deadline:
        page.wait_for_timeout(600)
        task=context.request.get(url+'/api/model/tasks/'+identifier).json()
    assert task['status']=='completed',task
    result=context.request.get(url+'/api/model/tasks/'+identifier+'/result').json()
    response=context.request.post(url+'/api/model/parameter-schemes',data={'name':'界面验收参数','sourceTaskId':identifier})
    assert response.ok,response.text()
    parameter=response.json()
    page.goto(url+'/#/simulation?parameter='+parameter['id']);page.wait_for_load_state('networkidle')
    expect(page.locator('#simulation-parameter')).to_have_value(parameter['id'])
    page.get_by_title('修改参数名称').click()
    page.locator('.el-message-box__input input').fill('界面验收参数改名')
    page.locator('.el-message-box__btns button').last.click()
    expect(page.locator('#simulation-parameter')).to_contain_text('界面验收参数改名')
    renamed=context.request.get(url+'/api/model/parameter-schemes/'+parameter['id']).json()
    assert {k:v for k,v in renamed.items() if k not in ('name','updatedAt')}=={k:v for k,v in parameter.items() if k not in ('name','updatedAt')}
    page.goto(url+'/#/state?source='+identifier);page.wait_for_load_state('networkidle')
    expect(page.locator('.state-process canvas')).to_have_count(1,timeout=20000)
    assert page.locator('.state-process canvas').bounding_box()['height']>200
    slider=page.get_by_role('slider',name='拖动查看逐时状态')
    expect(slider).to_have_attribute('max','24')
    slider.fill('0');slider.dispatch_event('input')
    expect(page.get_by_role('button',name='命名保存末状态',exact=True)).to_have_count(0)
    slider.fill('24');slider.dispatch_event('input')
    page.get_by_role('button',name='命名保存末状态',exact=True).click()
    page.locator('.el-message-box__input input').fill('界面验收末状态')
    page.locator('.el-message-box__btns button').last.click()
    expect(page.locator('.el-message')).to_contain_text('完整末状态已保存到状态库')
    page.screenshot(path=str(out/'state-result-1366.png'),full_page=True)
    page.set_viewport_size({'width':1920,'height':1080})
    page.screenshot(path=str(out/'state-result-1920.png'),full_page=True)
    catalog=context.request.get(url+'/api/model/state-catalog').json()
    assert catalog['metadata'][result['endStateVersionId']]['name']=='界面验收末状态'
    page.get_by_role('link',name='状态库',exact=True).click();page.wait_for_load_state('networkidle')
    expect(page.locator('.state-library')).to_contain_text('界面验收末状态',timeout=20000)
    assert not errors,errors
    print(json.dumps({'checks':['real simulation result','parameter rename preserves values','state hydrograph visible','slider follows result25hours','named full final state in library'],'errors':errors}),flush=True)
    browser.close()
