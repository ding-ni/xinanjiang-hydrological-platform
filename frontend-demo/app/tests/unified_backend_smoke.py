"""Browser acceptance: actual UI submissions, durable results and case isolation."""
import json
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("XAJ_TEST_URL", "http://127.0.0.1:18878")
ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(os.environ.get('XAJ_TEST_OUTPUT', str(ROOT / "runtime" / "browser-verification")))
OUTPUT.mkdir(parents=True, exist_ok=True)
READ_CHART = """async el => {
  if (!el.getAttribute('_echarts_instance_')) throw new Error('chart is not mounted');
  const url = performance.getEntriesByType('resource').map(row => row.name).find(url => /\/echarts-[^/]+\.js$/.test(url));
  const {i: init} = await import(url);
  // ECharts init returns the already-mounted instance for this exact DOM.
  return init(el).getOption();
}"""


def submit_from_ui(page, button_name):
    with page.expect_response(lambda response: response.request.method == 'POST' and response.url == BASE + '/api/model/tasks') as posted:
        page.get_by_role('button', name=button_name, exact=True).click()
    response = posted.value
    assert response.status == 202, response.text()
    task_id = response.json()['taskId']
    page.wait_for_function("taskId => document.querySelector('.task-status .task-head b')?.textContent.includes(taskId)", arg=task_id)
    return task_id


def wait_for_task(page, task_id, timeout=120000):
    page.wait_for_function("taskId => document.querySelector('.task-status .task-head b')?.textContent.includes(taskId) && document.querySelector('.task-status .task-state')?.textContent === '已完成'", arg=task_id, timeout=timeout)


def assert_actual_hydrograph_chart(page, hydrograph):
    """Use the frozen result's label, and compare the rendered numbers exactly."""
    label = hydrograph['forcing']['sourceLabel']
    assert label and label in page.locator('.figure-legend').inner_text()
    chart = page.locator('.hydrology-chart').evaluate(READ_CHART)
    times = [row['time'] for row in hydrograph['forecast']]
    assert chart['xAxis'][0]['data'] == times
    series = {row['name']: row for row in chart['series']}
    assert series['面平均降雨']['data'] == hydrograph['forcing']['precipitation']
    assert series['模拟流量']['data'] == [row['value'] for row in hydrograph['forecast']]
    observed = {row['time']: row['value'] for row in hydrograph['obs']}
    assert series['实测流量']['data'] == [observed.get(time) for time in times]
    assert series['实测流量']['connectNulls'] is False
    assert series['模拟流量']['connectNulls'] is False


def assert_frozen_input_lineage(page, task_id, result):
    request = page.request.get(BASE + f'/api/model/tasks/{task_id}').json()['runConfig']
    assert result['lineage']['inputBundleVersionId'] == request['inputs']['bundleVersionId']
    assert result['lineage']['inputBundleManifestSHA256'] == request['inputs']['bundleManifestSHA256']
    assert result['lineage']['forcingVersion'] == request['lineage']['forcingVersion']
    assert result['lineage']['observationVersion'] == request['lineage']['observationVersion']
    assert result['lineage']['routingManifestSHA256'] == request['lineage']['routingManifestSHA256']
    assert result['hydrograph']['forcing']['bundleVersionId'] == request['inputs']['bundleVersionId']


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 1100})
    page.add_init_script("""(() => {
      const key = 'hydro_parameter_library_overrides_v1';
      if (localStorage.getItem(key)) return;
      const legacy = {id: 'LEGACY-DISPLAY-CANDIDATE', source: 'frontend-fixture', sourceTaskId: 'SIM-old',
        parameters: {K: 1}, metrics: {calibration: {nse: 0.999}},
        independentTest: {passed: true, nse: 0.999}, review: {status: '审核通过'}, status: 'approved'};
      localStorage.setItem(key, JSON.stringify({'nananxi-xufan:1km': {
        simulationRateLibrary: {versions: [legacy]},
        automaticForecastLibrary: {productionVersion: 'AUTO-LEGACY-DISPLAY-CANDIDATE',
          versions: [{...legacy, id: 'AUTO-LEGACY-DISPLAY-CANDIDATE', status: 'production'}]}
      }}));
    })()""")
    errors, failed_local = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", lambda response: failed_local.append(f"{response.status} {response.url}: {response.text()}") if response.url.startswith(BASE) and response.status >= 400 else None)
    page.goto(BASE + "/#/calibration")
    page.wait_for_load_state("networkidle")
    page.locator('.basin-chip select').select_option('nananxi-xufan')
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.body.innerText.includes('服务正常')")
    assert 'LEGACY-DISPLAY-CANDIDATE' not in page.locator('#main-workspace').inner_text()
    # Reconnaissance before interaction: inspect the actual rendered controls.
    print(json.dumps(page.locator("button").all_text_contents(), ensure_ascii=False), flush=True)
    assert page.locator("#algorithm option").all_text_contents() == ["差分进化法", "随机搜索", "网格搜索"]
    assert page.locator(".param-meta label").count() == 11
    # Before a model run, an observed event is not a simulated event.
    page.locator('#cal-event').select_option('xf-31170619')
    page.wait_for_function("document.querySelector('.figure-legend')?.innerText.includes('后端 QC')", timeout=60000)
    assert '模拟流量' not in page.locator('.figure-legend').inner_text()
    assert '站均过程雨量' not in page.locator('.result-metrics').inner_text()
    prepared = page.request.get(BASE + '/api/model/inputs/events/xf-31170619?warmupHours=168').json()
    observed_chart = page.locator('.hydrology-chart').evaluate(READ_CHART)
    assert observed_chart['xAxis'][0]['data'] == prepared['product']['times']
    assert next(row for row in observed_chart['series'] if row['name'] == '实测流量')['data'] == prepared['observationPreview']['river']['flows']
    assert next(row for row in observed_chart['series'] if row['name'] == '面平均降雨')['data'] == prepared['summary']['arealRainfallMm']
    page.get_by_role("button", name="连续模拟", exact=False).first.click()
    page.locator(".field").filter(has=page.locator("label", has_text="开始时间")).locator("input").fill("2017-06-19T11:00")
    page.locator(".field").filter(has=page.locator("label", has_text="结束时间")).locator("input").fill("2017-06-20T11:00")
    task_id = submit_from_ui(page, '确认模拟任务')
    # Editing while a frozen task is running must not relabel its old result
    # as the newly edited scheme. The worker still computes the original 25h.
    end_input = page.locator(".field").filter(has=page.locator("label", has_text="结束时间")).locator("input")
    end_input.fill("2017-06-20T12:00")
    end_input.dispatch_event("change")
    page.wait_for_selector(".simulation-result-card", timeout=120000)
    assert "不是当前方案" in page.locator(".simulation-result-card").inner_text()
    assert '2017-06-20 11:00' in page.locator('.process-subtitle').inner_text()
    assert '2017-06-20 12:00' not in page.locator('.process-subtitle').inner_text()
    assert '面平均气温' not in page.locator('.figure-legend').inner_text()
    result = page.request.get(BASE + f"/api/model/tasks/{task_id}/result").json()
    assert_actual_hydrograph_chart(page, result['hydrograph'])
    assert_frozen_input_lineage(page, task_id, result)
    page.screenshot(path=str(OUTPUT / "real-simulation.png"), full_page=True)
    assert result["resultSource"] == "model-backend" and result["productionEligible"] is False
    assert len(result["hydrograph"]["forecast"]) == 25
    assert result["gridCount"] == 110 and 0 <= result["means"]["wu"] <= 20
    assert result["means"]["wl"] > 0
    assert len(result['hydrograph']['forcing']['precipitation']) == 25
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".simulation-result-card", timeout=20000)
    assert result["endStateVersionId"] in page.locator(".simulation-result-card").inner_text()
    page.get_by_role("button", name="保存为起算状态", exact=True).click()
    page.wait_for_url("**/#/state")
    page.wait_for_load_state("networkidle")
    expect(page.locator('.node-item.active')).to_have_attribute('title', result['endStateVersionId'])
    page.locator('.state-input-details > summary').click()
    assert "逐月给定蒸散发" in page.locator(".state-input-status").inner_text()
    assert result['lineage']['parameterVersionId'] in page.locator('.state-input-status').inner_text()
    page.get_by_role("button", name="用于续算", exact=True).click()
    assert page.get_by_role("button", name="开始续算", exact=True).is_enabled()
    source_period = page.request.get(BASE + f'/api/model/tasks/{task_id}').json()['runConfig']['period']
    assert page.get_by_label('计算至', exact=True).input_value() > source_period['endTime']
    page.get_by_label('状态回放时刻').wait_for(timeout=20000)
    history_catalog = page.request.get(BASE + f'/api/model/tasks/{task_id}/grid-state/catalog').json()
    assert len(history_catalog['times']) == 25 and len(history_catalog['gridIds']) == 110
    first_time = history_catalog['times'][0]
    page.get_by_label('状态回放时刻').select_option(first_time)
    page.wait_for_function("time => document.querySelector('.map-head')?.innerText.includes(time.replace('T', ' '))", arg=first_time)
    first_frame = page.request.get(BASE + f'/api/model/tasks/{task_id}/grid-state?time={first_time}').json()
    page.wait_for_function("expected => { const map = document.querySelector('.map-canvas')?.__hydroMap; const cells = map?.getSource('grid')?._data?.features || []; return cells.length === expected.length && cells.every((cell,index) => cell.properties.wu === expected[index].wu); }", arg=first_frame['cells'])
    page.wait_for_selector('.right .mini-chart', timeout=15000)
    first_series = page.request.get(BASE + f'/api/model/tasks/{task_id}/grid-state?unitId={history_catalog["gridIds"][0]}').json()
    trend = page.locator('.right .mini-chart .chart-host').evaluate(READ_CHART)
    assert trend['xAxis'][0]['data'] == [time.replace('T', ' ') for time in first_series['times']]
    assert all(row.get('smooth') is False and row.get('connectNulls') is False for row in trend['series'])
    page.get_by_role("button", name="续算状态", exact=True).click()
    page.locator(".compact-field input").fill("2017-06-20T17:00")
    continuation_id = submit_from_ui(page, '开始续算')
    wait_for_task(page, continuation_id)
    continuation_result = page.request.get(BASE + f'/api/model/tasks/{continuation_id}/result').json()
    assert_frozen_input_lineage(page, continuation_id, continuation_result)
    assert continuation_result['parentStateVersionId'] == result['stateVersionId']
    assert continuation_result['lineage']['inputBundleVersionId'] == result['lineage']['inputBundleVersionId']
    page.wait_for_function("document.querySelector('.metric-strip')?.innerText.includes('2017-06-20 17:00')", timeout=15000)
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.body.innerText.includes('服务正常')")
    assert "2017-06-20 17:00" in page.locator(".metric-strip").inner_text()
    assert result['stateVersionId'] in page.locator('.left').inner_text()
    assert '历史 QC 逐小时降雨 · 非预报' in page.locator('.left').inner_text()
    assert '相对起报' not in page.locator('.left').inner_text()
    assert '真实已计算时刻' in page.locator('.map-head').inner_text()
    assert '预见期' not in page.locator('.map-head').inner_text()
    page.screenshot(path=str(OUTPUT / "real-state.png"), full_page=True)
    page.get_by_role("button", name="生成初始状态", exact=True).click()
    page.locator(".compact-field input").fill("2017-06-21T00:00")
    initialization_id = submit_from_ui(page, '开始初始化')
    wait_for_task(page, initialization_id)
    initialized = page.request.get(BASE + f"/api/model/tasks/{initialization_id}/result").json()
    assert_frozen_input_lineage(page, initialization_id, initialized)
    assert initialized['parentStateVersionId'] is None
    assert initialized['stateTime'] == '2017-06-21T00:00:00'
    assert initialized['parameters']['WM'] == result['parameters']['WM']
    assert initialized['gridCount'] == 110

    # UI calibration with a tiny budget, preserving exactly the chosen groups.
    page.goto(BASE + "/#/calibration")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="参数率定", exact=False).first.click()
    page.get_by_role("button", name="清空", exact=True).click()
    page.locator("#max-evals").fill("4")
    page.locator("#max-evals").dispatch_event("change")
    page.locator("#population-size").fill("4")
    page.locator("#population-size").dispatch_event("change")
    # Catalog filter reveals the three 2017 events and their role selectors.
    selects = page.locator(".event-toolbar select")
    print('event toolbar:', page.locator('.event-table').inner_text()[:1000], flush=True)
    # Role assignment uses rendered ledger controls, not a replacement model.
    year_select = page.locator("select").filter(has=page.locator("option[value='2017']"))
    year_select.first.select_option("2017")
    rows = page.locator(".event-table .event-table-row").filter(has=page.locator("select"))
    print('2017 rows:', rows.count(), flush=True)
    assert rows.count() == 3
    for row, purpose in zip(rows.all(), ["率定", "验证", "独立检验"]):
        row.locator("select").select_option(purpose)
    calibration_id = submit_from_ui(page, '确认率定任务')
    wait_for_task(page, calibration_id, timeout=180000)
    page.wait_for_function("taskId => document.querySelector('.parameter-result-card')?.innerText.includes('PARAM-' + taskId)", arg=calibration_id, timeout=15000)
    assert "待生产验收" in page.locator(".parameter-result-card").inner_text()
    assert page.get_by_role("button", name="发布到自动预报参数库").count() == 0
    assert '2017' in page.locator('.process-subtitle').inner_text()
    assert '模拟流量' in page.locator('.figure-legend').inner_text()
    page.wait_for_function("document.querySelector('.result-metrics')?.innerText.includes('纳什效率系数（NSE）')", timeout=15000)
    metric_text = page.locator('.result-metrics').inner_text()
    assert '1964' not in metric_text
    assert '站均过程雨量' not in metric_text
    assert '当前场次拟合指标' in page.locator('.result-panel .panel-title').inner_text()
    calibration_result = page.request.get(BASE + f'/api/model/tasks/{calibration_id}/result').json()
    selected_event = page.locator('#cal-event').input_value()
    assert selected_event in calibration_result['hydrographs']
    assert_actual_hydrograph_chart(page, calibration_result['hydrographs'][selected_event])
    assert_frozen_input_lineage(page, calibration_id, calibration_result)
    expected_nse = calibration_result['hydrographs'][selected_event]['metrics']['nse']
    assert f'{expected_nse:.3f}' in metric_text
    page.screenshot(path=str(OUTPUT / "real-calibration.png"), full_page=True)
    # Config must see the server-owned parameter list, and loading a native
    # candidate must bring its values to simulation, not a fixture forecast.
    page.goto(BASE + '/#/config')
    page.wait_for_load_state('networkidle')
    page.get_by_role('button', name='参数与状态', exact=True).click()
    candidate_id = calibration_result['parameterVersionId']
    candidate_row = page.locator('.parameter-library-table tbody tr').filter(has_text=candidate_id)
    candidate_row.wait_for(timeout=15000)
    assert 'LEGACY-DISPLAY-CANDIDATE' not in page.locator('#main-workspace').inner_text()
    assert '暂无发布版本' in page.locator('.hint').filter(has_text='发布版本').inner_text()
    candidate_row.get_by_role('button', name='载入试算', exact=True).click()
    page.wait_for_url('**/#/calibration')
    page.wait_for_load_state('networkidle')
    loaded = page.evaluate("""() => {
      const cal = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('calibration');
      return {version: cal.paramVersion, params: {...cal.params}, mode: cal.operationMode};
    }""")
    assert loaded['version'] == candidate_id and loaded['mode'] == 'simulation'
    assert loaded['params'] == calibration_result['bestParameters']
    page.get_by_role('button', name='参数率定', exact=False).first.click()
    page.goto(BASE + "/#/archive")
    page.wait_for_load_state("networkidle")
    ledger = page.locator(".task-table tbody tr").filter(has_text=task_id)
    ledger.get_by_role("button", name="查看条件", exact=True).click()
    page.wait_for_selector('.backend-result-inspector h3')
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='导出结果与冻结条件 JSON', exact=True).click()
    downloaded.value.save_as(str(OUTPUT / 'exported-result.json'))
    exported = json.loads((OUTPUT / 'exported-result.json').read_text(encoding='utf-8'))
    assert exported['result']['taskId'] == task_id
    assert exported['request']['context']['projectId'] == 'nananxi-xufan'
    assert exported['result'] == result
    assert exported['request'] == page.request.get(BASE + f'/api/model/tasks/{task_id}').json()['runConfig']
    with page.expect_response(lambda response: response.request.method == 'POST' and response.url == BASE + f'/api/model/tasks/{task_id}/archive') as archived:
        ledger.get_by_role("button", name="归档", exact=True).click()
    assert archived.value.status == 200 and archived.value.json()['status'] == 'archived'
    assert page.request.get(BASE + f'/api/model/tasks/{task_id}').json()['status'] == 'archived'

    checked = []
    for basin, configurations in {"nananxi-xufan": ["nananxi-xufan-1km", "nananxi-xufan-0p1deg"], "llpq": ["llpq-1km", "llpq-0p1deg"], "selinco": ["selinco-1km", "selinco-0p1deg"]}.items():
        page.locator(".basin-chip select").select_option(basin)
        for config in configurations:
            page.locator(".scale-chip select").select_option(config)
            for route in ["realtime", "forecast", "state", "calibration", "archive", "config"]:
                page.goto(BASE + "/#/" + route)
                page.wait_for_load_state("networkidle")
                if basin != "nananxi-xufan" or config.endswith("0p1deg"):
                    assert "RESULT-JOB-" not in page.locator("#main-workspace").inner_text()
                    if route == "archive":
                        assert task_id not in page.locator(".task-table").inner_text()
                if route == "calibration" and (basin == "llpq" or config == "nananxi-xufan-0p1deg"):
                    button = page.get_by_role("button", name="确认率定任务" if basin == "nananxi-xufan" else "开始率定", exact=True)
                    assert button.is_disabled()
                checked.append(f"{config}/{route}")
    assert not errors, errors
    assert not failed_local, failed_local
    page.locator('.basin-chip select').select_option('nananxi-xufan')
    page.locator('.scale-chip select').select_option('nananxi-xufan-1km')
    page.goto(BASE + '/#/calibration')
    page.wait_for_load_state('networkidle')
    page.route('**/api/model/**', lambda route: route.abort())
    page.wait_for_function("document.body.innerText.includes('服务离线')", timeout=15000)
    assert page.get_by_role('button', name='确认率定任务', exact=True).is_disabled()
    assert '真实模型服务离线' in page.locator('#main-workspace').inner_text()
    assert 'LEGACY-DISPLAY-CANDIDATE' not in page.locator('#main-workspace').inner_text()
    legacy_retained = page.evaluate("""() => {
      const old = JSON.parse(localStorage.getItem('hydro_parameter_library_overrides_v1'))['nananxi-xufan:1km'];
      return old.simulationRateLibrary.versions.some(row => row.id === 'LEGACY-DISPLAY-CANDIDATE')
        && old.automaticForecastLibrary.versions.some(row => row.id === 'AUTO-LEGACY-DISPLAY-CANDIDATE');
    }""")
    assert legacy_retained
    page.unroute('**/api/model/**')
    page.wait_for_function("document.body.innerText.includes('服务正常')", timeout=20000)
    report = {"simulationTaskId": task_id, "stateVersionId": result["stateVersionId"], 'continuationTaskId': continuation_id, 'initializationTaskId': initialization_id, 'calibrationTaskId': calibration_id, "casePagesChecked": checked, "pageErrors": errors, "localRequestFailures": failed_local, 'offlineRecovery': 'passed', 'resultExport': 'passed', 'editedDuringRun': 'passed', 'realForcingAndStateLineage': 'passed', 'stateSelectionRecovery': 'passed', 'actualEventMetrics': 'passed', 'observedOnlyBeforeRun': 'passed', 'gridStateReplay': 'passed', 'nativeCandidateLoad': 'passed', 'legacyParametersQuarantinedAndRetained': 'passed', 'renderedChartEqualsFrozenResult': 'passed', 'inputBundleAndRoutingDigestsFrozen': 'passed', 'selectedCalibrationEventId': selected_event, 'selectedCalibrationEventNSE': expected_nse}
    (OUTPUT / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    browser.close()
