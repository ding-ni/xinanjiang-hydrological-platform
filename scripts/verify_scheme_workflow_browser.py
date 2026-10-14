"""Exercise the actual named continuous-calibration workflow in an isolated server."""
import argparse
import json
import math
import time
from pathlib import Path
from urllib.parse import unquote
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:18893')
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
report = {'status': 'failed', 'checks': [], 'pageErrors': []}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: report['pageErrors'].append(str(error)))
    def check(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)
    def api(path):
        response = context.request.get(args.url + '/api/model/' + path)
        assert response.ok, response.text()
        return response.json()
    def fill(selector, value):
        page.locator(selector).fill(value)
        page.locator(selector).press('Tab')
    def select_operation(operation, period):
        page.locator('.operation-tabs').get_by_role('button', name=operation, exact=True).click()
        page.locator('.setting-panel .mode-tabs').get_by_role('button', name=period, exact=True).click()
    def open_scheme(scheme_id):
        page.get_by_role('button', name='打开方案', exact=True).click()
        modal = page.locator('#scheme-selector-modal')
        expect(modal).to_be_visible()
        modal.locator(f'.scheme-choice[data-scheme-id="{scheme_id}"]').click()
        expect(modal).not_to_be_visible()
    def show_initial_conditions():
        details = page.locator('.setting-panel details').filter(has=page.locator('summary').filter(has_text='起算条件'))
        if details.get_attribute('open') is None:
            details.locator('summary').click()
        return details
    try:
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.scheme-bar')).to_be_visible()
        output.joinpath('initial-ui.txt').write_text(page.locator('.view-cal').inner_text(), encoding='utf-8')
        page.screenshot(path=str(output / 'initial.png'), full_page=True)
        expect(page.locator('.operation-tabs button')).to_have_count(2)
        expect(page.locator('.setting-panel .mode-tabs button')).to_have_count(2)
        assert page.locator('.workflow-steps').count() == 0
        assert page.locator('.source-settings').count() == 0
        check('One-page workbench keeps flood selection, calculation settings and results together')
        schemes = api('model-schemes')['schemes']
        assert all(row['name'].startswith(('浏览器', '南安溪-徐畈 · 连续')) for row in schemes)
        check('Workspace contains no pre-generated colleague scheme')
        page.goto(args.url + '/#/config')
        page.wait_for_load_state('networkidle')
        page.locator('summary').filter(has_text='资料设置').click()
        page.locator('#workspace-rainfall-method').select_option('reuse')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='重新选洪水', exact=True).click()
        page.locator('.scheme-bar input[placeholder="方案名称（可选）"]').fill('浏览器连续率定验收')
        select_operation('参数率定', '连续时段')
        initial_conditions = show_initial_conditions()
        page.locator('#science-version').select_option('')
        page.locator('#cal-state-policy').select_option('continuous-origin')
        initial_conditions.locator('summary').click()
        expect(page.locator('#continuous-origin')).to_be_visible()
        for selector, value in [('#cal-start', '2017-06-19T00:00'), ('#cal-end', '2017-06-19T23:00'),
                                ('#val-start', '2017-06-20T00:00'), ('#val-end', '2017-06-20T23:00'),
                                ('#ind-start', '2017-06-21T00:00'), ('#ind-end', '2017-06-21T23:00'),
                                ('#continuous-origin', '2017-06-18T00:00')]:
            fill(selector, value)
        page.locator('#algorithm').select_option('DIFFERENTIAL_EVOLUTION')
        page.locator('#search-strategy').select_option('joint')
        fill('#max-evals', '4')
        fill('#population-size', '4')
        prior_tasks = {row['taskId'] for row in api('tasks')['tasks']}
        page.get_by_role('button', name='开始自动调参', exact=True).click()
        deadline = time.monotonic() + 300
        result = None
        while time.monotonic() < deadline:
            tasks = api('tasks')['tasks']
            calibration = next((row for row in tasks if row['runType'] == 'continuousCalibration' and row['taskId'] not in prior_tasks), None)
            if calibration and calibration['status'] in {'failed', 'cancelled'}:
                raise AssertionError(calibration.get('errorMessage'))
            if calibration and calibration['status'] == 'completed':
                result = api('tasks/' + calibration['taskId'] + '/result')
                assert not result.get('stateCalculationError'), result.get('stateCalculationError')
                child = next((row for row in tasks if row['taskId'] == result.get('stateCalculationTaskId')), None)
                if child and child['status'] == 'failed':
                    raise AssertionError(child.get('errorMessage'))
                if child and child['status'] == 'completed':
                    break
            page.wait_for_timeout(500)
        assert result and child['status'] == 'completed', page.locator('.view-cal').inner_text()
        assert result['optimizer']['evaluations'] == 4
        assert result['optimizer']['terminationReason'] == 'BUDGET_EXHAUSTED'
        assert result['optimizationGroups'] == ['CALIBRATION']
        assert result['statePolicy'] == 'continuous-origin'
        check('Named scheme automatically prepares inputs and performs a real continuous search')
        expect(page.get_by_role('button', name='查看最佳参数模拟状态')).to_be_visible(timeout=20000)
        expect(page.locator('.view-cal')).to_contain_text('评价预算已用完')
        page.screenshot(path=str(output / 'continuous-result.png'), full_page=True)
        page.get_by_role('button', name='查看最佳参数模拟状态').click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-state')).to_be_visible()
        expect(page.locator('.view-state')).to_contain_text('最佳参数连续模拟')
        page.screenshot(path=str(output / 'state.png'), full_page=True)
        check('Best-parameter checkpoint opens the simulation-state page')
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        page.get_by_label('按方案查看成果').select_option(calibration['lineage']['schemeId'])
        expect(page.locator('.task-table')).to_contain_text('浏览器连续率定验收')
        check('Archive lists calculations by scheme and keeps their revision')
        page.goto(args.url + '/#/data')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_label('资料起始时间')).to_be_visible()
        assert page.get_by_role('button', name='资料整编', exact=True).count() == 0
        assert page.locator('#scheme-task').count() == 0
        check('Observation library no longer asks users to process or register each flood')
        page.goto(args.url + '/#/config')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='参考参数', exact=True).click()
        expect(page.get_by_text('参考参数与状态目录', exact=True)).to_be_visible()
        check('Reference-parameter tab renders')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='重新选洪水', exact=True).click()
        page.locator('.scheme-bar input[placeholder="方案名称（可选）"]').fill('浏览器场次率定验收')
        select_operation('参数率定', '洪水场次')
        initial_conditions = show_initial_conditions()
        page.locator('#cal-state-policy').select_option('independent-warmup')
        initial_conditions.locator('summary').click()
        fill('input[aria-label="预热期天数"]', '1')
        events = [row for row in api('catalog')['cases']['nananxi-xufan:1km']['events'] if row['start'].startswith('2017')]
        page.locator('.event-window-editor summary').click()
        for index, (event, purpose) in enumerate(zip(events, ['率定', '验证', '独立检验'])):
            identifier = 'BrowserWindow_' + str(index)
            fields = page.locator('.event-window-editor input')
            fields.nth(0).fill(identifier)
            fields.nth(1).fill(event['start'][:16])
            fields.nth(2).fill(event['end'][:16])
            page.get_by_role('button', name='保存到本方案', exact=True).click()
            page.locator('.event-search input').fill(identifier)
            row = page.locator('.event-table-row[role="row"]').filter(has_text=identifier)
            expect(row).to_be_visible()
            row.locator('select').select_option(label=purpose)
        page.locator('.event-search input').fill('BrowserWindow_')
        check('User flood windows remain selectable before automatic registration')
        first_window = page.locator('.event-table-row[role="row"]').filter(has_text='BrowserWindow_0')
        with page.expect_response(lambda response: '/api/model/inputs/window?' in response.url
                                 and ('start=' + events[0]['start'][:16]) in unquote(response.url)) as preview_response:
            first_window.locator('.event-code').click()
        assert preview_response.value.ok, preview_response.value.text()
        preview = preview_response.value.json()
        preview_times = preview['product']['times']
        preview_flow = preview['observationPreview']['river']['flows']
        assert len(preview_flow) == len(preview_times) and any(value is not None and math.isfinite(value) for value in preview_flow)
        assert any(value is not None and math.isfinite(value) for value in preview['summary']['arealRainfallMm'])
        expect(page.locator('.hydrology-chart canvas').first).to_be_visible()
        expect(page.locator('.input-quality-note')).not_to_be_visible()
        expect(page.locator('.view-cal')).not_to_contain_text('此场次尚未登记到计算资料目录')
        check('A newly selected flood previews actual rain and observed flow before input preparation')
        prior_tasks = {row['taskId'] for row in api('tasks')['tasks']}
        page.get_by_role('button', name='开始自动调参', exact=True).click()
        deadline = time.monotonic() + 180
        event_task = None
        while time.monotonic() < deadline:
            event_task = next((row for row in api('tasks')['tasks'] if row['taskId'] not in prior_tasks and row.get('lineage', {}).get('schemeName') == '浏览器场次率定验收'), None)
            if event_task and event_task['status'] in {'failed', 'cancelled'}:
                raise AssertionError(event_task.get('errorMessage'))
            if event_task and event_task['status'] == 'completed':
                break
            page.wait_for_timeout(500)
        assert event_task and event_task['status'] == 'completed', page.locator('.view-cal').inner_text()
        event_result = api('tasks/' + event_task['taskId'] + '/result')
        assert event_result['optimizer']['evaluations'] == 4
        assert set(event_result['hydrographs']) == {'BrowserWindow_0', 'BrowserWindow_1', 'BrowserWindow_2'}
        expect(page.get_by_role('button', name='重新选洪水', exact=True)).to_be_enabled(timeout=20000)
        page.screenshot(path=str(output / 'event-result.png'), full_page=True)
        check('Named event scheme automatically registers custom windows and performs real search')
        saved_ids = {row['taskId'] for row in api('tasks')['tasks']}
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        page.get_by_label('按方案查看成果').select_option(event_task['lineage']['schemeId'])
        row = page.locator('.task-table tbody tr').filter(has=page.locator(f'td[title="{event_task["taskId"]}"]'))
        row.get_by_role('button', name='按原条件打开', exact=True).click()
        expect(page.locator('.view-cal')).to_be_visible(timeout=20000)
        page.locator('.event-search input').fill('BrowserWindow_')
        expect(page.locator('.event-table .event-code')).to_have_count(3)
        assert {row['taskId'] for row in api('tasks')['tasks']} == saved_ids
        check('Archive restores custom windows and original conditions as a draft without submitting')
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        row = page.locator('.task-table tbody tr').filter(has=page.locator(f'td[title="{event_task["taskId"]}"]'))
        row.get_by_role('button', name='查看成果', exact=True).click()
        page.get_by_role('button', name='采用最佳参数进行模拟', exact=True).click()
        expect(page.locator('.view-cal')).to_be_visible()
        expect(page.locator('.operation-tabs button.active')).to_contain_text('模型模拟')
        expect(page.locator('.setting-panel .mode-tabs button.active')).to_contain_text('洪水场次')
        assert {row['taskId'] for row in api('tasks')['tasks']} == saved_ids
        check('Best-parameter action opens a fixed simulation draft without starting a search')
        page.get_by_role('button', name='保存方案', exact=True).click()
        scheme_id = event_task['lineage']['schemeId']
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            fixed_draft = api('model-schemes/' + scheme_id)
            if fixed_draft['calculationDraft'].get('calibration', {}).get('simulationOnly'):
                break
            page.wait_for_timeout(200)
        assert fixed_draft['calculationDraft']['calibration']['simulationOnly']
        assert fixed_draft['revision'] == event_task['lineage']['schemeRevision']
        open_scheme(scheme_id)
        expect(page.locator('.operation-tabs button.active')).to_contain_text('模型模拟')
        expect(page.locator('.setting-panel .mode-tabs button.active')).to_contain_text('洪水场次')
        page.get_by_role('button', name='开始模拟', exact=True).click()
        deadline = time.monotonic() + 180
        fixed_task = None
        while time.monotonic() < deadline:
            fixed_task = next((row for row in api('tasks')['tasks'] if row['taskId'] not in saved_ids and row['runType'] == 'eventSimulation'), None)
            if fixed_task and fixed_task['status'] in {'failed', 'cancelled'}:
                raise AssertionError(fixed_task.get('errorMessage'))
            if fixed_task and fixed_task['status'] == 'completed':
                break
            page.wait_for_timeout(500)
        assert fixed_task and fixed_task['status'] == 'completed', page.locator('.view-cal').inner_text()
        fixed_result = api('tasks/' + fixed_task['taskId'] + '/result')
        assert fixed_result['optimizer']['evaluations'] == 1
        assert fixed_result['optimizer']['terminationReason'] == 'FIXED_PARAMETERS_EVALUATED'
        assert fixed_task['lineage']['schemeId'] == scheme_id
        assert fixed_result['bestParameters'] == fixed_task['runConfig']['calibration']['initialParameters']
        assert fixed_result['bestParameters'] == event_result['bestParameters']
        check('Same scheme restores fixed simulation and evaluates specified parameters exactly once')

        # A second search stays within the scheme; the first calculation remains frozen.
        before_second = {row['taskId'] for row in api('tasks')['tasks']}
        select_operation('参数率定', '洪水场次')
        page.locator('#algorithm').select_option('RANDOM_SEARCH')
        page.get_by_role('button', name='开始自动调参', exact=True).click()
        deadline = time.monotonic() + 180
        second_task = None
        while time.monotonic() < deadline:
            second_task = next((row for row in api('tasks')['tasks'] if row['taskId'] not in before_second and row['runType'] == 'eventCalibration'), None)
            if second_task and second_task['status'] in {'failed', 'cancelled'}:
                raise AssertionError(second_task.get('errorMessage'))
            if second_task and second_task['status'] == 'completed':
                break
            page.wait_for_timeout(500)
        assert second_task and second_task['status'] == 'completed'
        assert second_task['lineage']['schemeId'] == scheme_id
        assert second_task['lineage']['schemeRevision'] == event_task['lineage']['schemeRevision']
        assert api('tasks/' + event_task['taskId'] + '/result') == event_result
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        row = page.locator('.task-table tbody tr').filter(has=page.locator(f'td[title="{second_task["taskId"]}"]'))
        row.get_by_role('button', name='查看成果', exact=True).click()
        page.get_by_label('对比成果').select_option(fixed_task['taskId'])
        expect(page.locator('.comparison-metrics')).to_be_visible()
        page.get_by_label('对比成果').select_option(event_task['taskId'])
        expect(page.locator('.comparison-metrics')).to_be_visible()
        check('Second automatic search keeps the same scheme and compares both original search and fixed simulation')

        # Diagnostic source names and out-of-period simulation handoff.
        page.goto(args.url + '/#/archive')
        page.wait_for_load_state('networkidle')
        row = page.locator('.task-table tbody tr').filter(has=page.locator(f'td[title="{calibration["taskId"]}"]'))
        row.get_by_role('button', name='查看成果', exact=True).click()
        page.get_by_role('button', name='查询河道断面流量', exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('.diagnostic-view form select:visible').first).to_have_value(child['taskId'])
        page.locator('.diagnostic-view input[type="datetime-local"]').first.fill('2017-06-17T00:00')
        page.locator('.diagnostic-view input[type="datetime-local"]').first.press('Tab')
        expect(page.get_by_role('button', name='模拟所选时段', exact=True)).to_be_visible()
        page.get_by_role('button', name='模拟所选时段', exact=True).click()
        expect(page.locator('.operation-tabs button.active')).to_contain_text('模型模拟')
        expect(page.locator('.setting-panel .mode-tabs button.active')).to_contain_text('连续时段')
        check('Continuous optimum opens river query and uncovered times open an independent simulation draft')
        # A worker can begin directly with a chosen period, without naming first.
        page.get_by_role('button', name='重新选洪水', exact=True).click()
        select_operation('模型模拟', '连续时段')
        expect(page.locator('.scheme-bar input[placeholder="方案名称（可选）"]')).to_have_value('')
        fill('#simulation-start', '2017-06-19T00:00')
        fill('#simulation-end', '2017-06-19T01:00')
        before_unnamed = {row['taskId'] for row in api('tasks')['tasks']}
        page.get_by_role('button', name='开始模拟', exact=True).click()
        deadline = time.monotonic() + 180
        unnamed_task = None
        while time.monotonic() < deadline:
            unnamed_task = next((row for row in api('tasks')['tasks'] if row['taskId'] not in before_unnamed and row['runType'] == 'continuousSimulation'), None)
            if unnamed_task and unnamed_task['status'] in {'failed', 'cancelled'}:
                raise AssertionError(unnamed_task.get('errorMessage'))
            if unnamed_task and unnamed_task['status'] == 'completed':
                break
            page.wait_for_timeout(500)
        assert unnamed_task and unnamed_task['status'] == 'completed', page.locator('.view-cal').inner_text()
        assert unnamed_task['lineage']['schemeName'].strip()
        expect(page.locator('.scheme-bar input[placeholder="方案名称（可选）"]')).to_have_value(unnamed_task['lineage']['schemeName'])
        unnamed_result = api('tasks/' + unnamed_task['taskId'] + '/result')
        assert len(unnamed_result['hydrograph']['forecast']) == 2
        expect(page.get_by_role('button', name='重新选洪水', exact=True)).to_be_enabled(timeout=20000)
        check('An unnamed two-hour simulation automatically saves a scheme and runs directly')
        # Real local observations, automatic station selection and IDW through the UI.
        page.goto(args.url + '/#/config')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='流域与站网', exact=True).click()
        page.locator('summary').filter(has_text='资料设置').click()
        page.locator('#workspace-rainfall-method').select_option('stationIdw')
        page.locator('#workspace-station-source').select_option('LOCAL_OBSERVATIONS')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='重新选洪水', exact=True).click()
        page.locator('.scheme-bar input[placeholder="方案名称（可选）"]').fill('浏览器站点自动模拟验收')
        select_operation('模型模拟', '连续时段')
        # Select a period covered by the actual station archive, including warmup.
        page.locator('#simulation-start').fill('2017-06-19T00:00')
        page.locator('#simulation-start').press('Tab')
        page.locator('#simulation-end').fill('2017-06-19T23:00')
        page.locator('#simulation-end').press('Tab')
        prior_station = {row['taskId'] for row in api('tasks')['tasks']}
        expect(page.locator('#simulation-parameters')).to_be_visible()
        expect(page.locator('.parameter-panel')).to_contain_text('模拟参数')
        page.get_by_role('button', name='开始模拟', exact=True).click()
        deadline = time.monotonic() + 180
        station_task = None
        while time.monotonic() < deadline:
            station_task = next((row for row in api('tasks')['tasks'] if row['taskId'] not in prior_station and row.get('lineage', {}).get('schemeName') == '浏览器站点自动模拟验收'), None)
            if station_task and station_task['status'] in {'failed', 'cancelled'}:
                raise AssertionError(station_task.get('errorMessage'))
            if station_task and station_task['status'] == 'completed':
                break
            page.wait_for_timeout(500)
        assert station_task and station_task['status'] == 'completed', page.locator('.view-cal').inner_text()
        assert station_task['runConfig']['inputs']['stationSourceId'] == 'LOCAL_OBSERVATIONS'
        assert station_task['runConfig']['inputs']['stationNetwork']['stations']
        check('Actual local station archive automatically prepares IDW rain and completes simulation')
        page.goto(args.url + '/#/realtime')
        page.wait_for_load_state('networkidle')
        expect(page.locator('.view-realtime .map-body canvas').first).to_be_visible(timeout=30000)
        text = page.locator('.view-realtime').inner_text()
        for removed in ['当前计算降雨', '资料编号与提取时段', '地图高程按当前产流网格显示', '未接入实时更新']:
            assert removed not in text, removed
        page.get_by_role('button', name='平滑', exact=True).click()
        expect(page.get_by_role('button', name='平滑', exact=True)).to_have_attribute('aria-pressed', 'true')
        page.get_by_role('button', name='网格', exact=True).click()
        expect(page.get_by_role('button', name='网格', exact=True)).to_have_attribute('aria-pressed', 'true')
        page.screenshot(path=str(output / 'realtime.png'), full_page=True)
        check('Rainfall map keeps grid and smooth controls without repeated source explanations')
        page.goto(args.url + '/#/calibration')
        page.wait_for_load_state('networkidle')
        assert report['pageErrors'] == [], report['pageErrors']
        report.update(status='passed', calibrationTaskId=calibration['taskId'], stateTaskId=child['taskId'], eventTaskId=event_task['taskId'], fixedSimulationTaskId=fixed_task['taskId'], secondSearchTaskId=second_task['taskId'], automaticNameSimulationTaskId=unnamed_task['taskId'], stationSimulationTaskId=station_task['taskId'])
    except Exception:
        page.screenshot(path=str(output / 'failure.png'), full_page=True)
        output.joinpath('failure-ui.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
        raise
    finally:
        output.joinpath('browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
