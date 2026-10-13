"""Verify the data library using actual registered workspace records.

Use an isolated restored workspace only. One small station-rainfall source is
saved to check the form handoff. No model, forecast or rainfall-processing task
is started and no model-input version or calculation condition is registered.
"""
import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

from playwright.sync_api import expect, sync_playwright


APP_STATE = """() => {
  const pinia = document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia;
  const app = pinia?._s.get('app');
  return { selected: app?.selectedInputBundleVersionId, backend: !!app?.backendCase };
}"""
EPOCH = datetime(1970, 1, 1)


def iso(value):
    return value.isoformat(timespec='minutes')


def count(*values):
    return next((value for value in values if type(value) is int and value >= 0), None)


def content_expectations(record, catalog, sources):
    """Use explicit evidence, never record names, to check displayed counts."""
    descriptor = record.get('contents') or {}
    summary = record.get('summary') or {}
    source = next((row for row in sources if row.get('sourceId') == record.get('sourceId')
                   and row.get('sourceId')), {})
    grid_count = count(descriptor.get('gridCount'), summary.get('gridCount'),
                       record.get('gridCount'), catalog.get('gridCount'))
    event_count = count(descriptor.get('eventCount'), summary.get('eventCount'),
                        record.get('eventCount'), len(catalog['events']) if isinstance(catalog.get('events'), list) else None)
    accepted_count = count(descriptor.get('acceptedEventCount'), summary.get('acceptedEventCount'))
    scope = record.get('recordQueryScope') or {}
    origin = record.get('sourceOrigin') or {}
    station_count = count(descriptor.get('rainStationCount'),
                          len(scope['rainStations']) if isinstance(scope.get('rainStations'), list) else None,
                          len(origin['stationIds']) if isinstance(origin.get('stationIds'), list) else None,
                          source.get('stationCount'))
    coverage = catalog.get('coverage') or {}
    has_rain = descriptor.get('hasGridRainfall') is True or bool(coverage.get('precipitation'))
    has_flow = descriptor.get('hasOutletFlow') is True or bool(coverage.get('observedFlow'))
    has_events = descriptor.get('hasEventCatalog') is True or isinstance(catalog.get('events'), list)
    return {
        'gridCount': grid_count, 'eventCount': event_count,
        'acceptedEventCount': accepted_count, 'rainStationCount': station_count,
        'rain': ('已整编' + (f' · {grid_count} 个网格' if grid_count is not None else ''))
                if has_rain else '资料组成待读取',
        'flow': '已包含' if has_flow else '资料组成待读取',
        'events': ((f'{event_count} 个登记窗口' if event_count is not None else '已包含')
                   + ' · ' + (f'按本版规则通过检查 {accepted_count} 个' if accepted_count is not None else '通过检查数量未统计'))
                  if has_events else '目录待读取',
        'stations': f'{station_count} 个来源站' if station_count is not None else '未登记站数',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--expected-version', default='', help='Optional exact packaged application version')
    args = parser.parse_args()
    parsed = urlparse(args.url)
    assert parsed.hostname in {'127.0.0.1', 'localhost', '::1'}, 'Use a local isolated test service'
    assert parsed.port != 18890, 'Never use the user active workspace on port 18890'
    base = args.url.rstrip('/')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'checks': [], 'errors': [], 'consoleErrors': [],
              'failedResources': [], 'httpErrors': [], 'screenshots': [], 'inspectedInputs': []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.on('console', lambda message: report['consoleErrors'].append(message.text) if message.type == 'error' else None)
        page.on('requestfailed', lambda request: report['failedResources'].append({'url': request.url, 'reason': request.failure}))
        page.on('response', lambda response: report['httpErrors'].append({'url': response.url, 'status': response.status})
                if response.status >= 400 else None)

        def api(resource):
            response = context.request.get(base + '/api/model/' + resource, timeout=60000)
            assert response.status == 200, (response.status, response.text()[:500])
            return response.json()

        def snapshot(name):
            page.screenshot(path=str(output / name), full_page=True)
            report['screenshots'].append(name)

        def inventory():
            return {
                'modelTasks': sorted(row['taskId'] for row in api('tasks')['tasks']),
                'inputTasks': sorted(row['taskId'] for row in api('input-tasks')['tasks']),
                'inputVersions': sorted(row['bundleVersionId'] for row in api('input-products')['versions']),
                'schemes': sorted(row['id'] for row in api('database/schemes')['schemes']),
            }

        def inspect_record(record, selected_id, sources):
            version_id = record['bundleVersionId']
            if version_id == selected_id:
                page.locator('[data-current-input]').get_by_role('button', name='查看资料内容', exact=True).click()
            else:
                others = page.locator('details.other-inputs')
                if not others.evaluate('(el) => el.open'):
                    others.locator(':scope > summary').click()
                row = others.locator('tbody tr[data-input-version=' + json.dumps(version_id) + ']')
                expect(row.locator('td').first.locator('b')).to_have_text(record['name'])
                row.get_by_role('button', name='查看资料内容', exact=True).click()
            detail = page.locator('[data-input-detail]')
            expect(detail).to_have_attribute('data-inspected-version', version_id)
            expect(detail.get_by_role('status')).to_have_count(0, timeout=60000)
            catalog = api('inputs/catalog?' + urlencode({'projectId': record['projectId'],
                          'resolutionId': record['resolutionId'], 'bundleVersionId': version_id}))
            assert catalog['bundleVersionId'] == version_id
            values = content_expectations(record, catalog, sources)
            table = detail.get_by_role('table', name='资料组成', exact=True)
            for key in ('rain', 'flow', 'events', 'stations'):
                expect(table.locator(f'[data-content-key="{key}"] td').first).to_contain_text(values[key])
            scope = record.get('recordQueryScope') or {}
            if scope.get('start') and scope.get('end'):
                expect(detail).to_contain_text(scope['start'].replace('T', ' ')[:16])
                expect(detail).to_contain_text(scope['end'].replace('T', ' ')[:16])
            assert page.evaluate(APP_STATE)['selected'] == selected_id
            expect(page.locator('[data-current-input]')).to_have_attribute('data-input-version', selected_id)
            if version_id != selected_id:
                expect(detail.locator('.view-state')).to_have_text('仅查看资料内容，当前计算资料未改变。')
            windows = detail.locator('.detail-group').filter(has=page.locator('summary', has_text='洪水窗口清单'))
            if not windows.evaluate('(el) => el.open'):
                windows.locator(':scope > summary').click()
            rows = windows.get_by_role('table', name='本资料洪水窗口', exact=True).locator('tbody tr')
            expect(rows).to_have_count(max(1, len(catalog.get('events') or [])))
            report['inspectedInputs'].append({'bundleVersionId': version_id, 'registeredName': record['name'],
                                             'catalogEventCount': len(catalog.get('events') or []), **values})
            return detail

        try:
            workspace = api('workspace')
            if args.expected_version:
                assert workspace['version'] == args.expected_version, workspace['version']
            workspace_text = json.dumps(workspace, ensure_ascii=False)
            assert '开发产物' in workspace_text and '恢复工作区' in workspace_text, 'Use an isolated restored workspace'
            report['workspaceVersion'] = workspace['version']
            before = inventory()
            sources_before = api('input-sources')['sources']
            versions_before = api('input-products')['versions']
            versions = {row['bundleVersionId']: row for row in versions_before}
            assert versions, 'Acceptance needs at least one registered input'

            page.goto(base + '/#/data?tab=inputs', wait_until='networkidle')
            page.locator('.basin-chip select').select_option('nananxi-xufan')
            page.locator('.scale-chip select').select_option('nananxi-xufan-1km')
            expect(page.locator('[data-current-input] strong')).to_be_visible(timeout=60000)
            expect(page.locator('.data-tabs button')).to_have_text(['实测资料', '洪水目录', '模型计算资料'])
            saved = page.locator('details.saved-conditions')
            expect(saved.locator(':scope > summary')).to_contain_text('已保存的计算条件')
            assert not saved.evaluate('(el) => el.open')
            assert page.locator('[data-input-processing]').count() == 0
            assert not page.locator('details.other-inputs').evaluate('(el) => el.open')
            baseline = page.evaluate(APP_STATE)['selected']
            assert baseline in versions
            expect(page.locator('[data-current-input] strong')).to_have_text(versions[baseline]['name'])
            baseline_catalog = api('inputs/catalog?' + urlencode({
                'projectId': versions[baseline]['projectId'], 'resolutionId': versions[baseline]['resolutionId'],
                'bundleVersionId': baseline}))
            baseline_values = content_expectations(versions[baseline], baseline_catalog, sources_before)
            current_values = page.locator('[data-current-input] .current-composition dd')
            for index, key in enumerate(('rain', 'flow', 'events')):
                expect(current_values.nth(index)).to_contain_text(baseline_values[key], timeout=60000)
            saved.locator(':scope > summary').click()
            expect(saved.get_by_role('button', name='保存计算条件', exact=True)).to_be_visible()
            reusable = [row for row in api('tasks')['tasks'] if row.get('status') in {'completed', 'archived'}
                        and row.get('runConfig', {}).get('runType') in
                        {'simulation', 'calibration', 'stateInitialization', 'stateContinuation'}]
            expect(saved.locator('#scheme-task option')).to_have_count(len(reusable) + 1)
            expect(saved).to_contain_text('率定类条件重新计算时会重新搜索参数')
            saved.locator(':scope > summary').click()
            report['checks'].append('three-primary-data-tabs-and-secondary-saved-conditions')
            snapshot('desktop-current-model-input.png')

            for index, record in enumerate(versions_before, start=1):
                inspect_record(record, baseline, sources_before)
                if report['inspectedInputs'][-1]['rainStationCount'] is None:
                    snapshot(f'desktop-input-with-unregistered-source-count-{index:02}.png')
            assert api('input-products')['versions'] == versions_before
            report['checks'].append('registered-names-actual-composition-and-window-catalog-with-read-only-inspection')
            snapshot('desktop-model-input-detail.png')

            alternative = next((row for row in versions_before if row['bundleVersionId'] != baseline), None)
            selected = baseline
            if alternative:
                detail = inspect_record(alternative, baseline, sources_before)
                detail.get_by_role('button', name='选用此资料', exact=True).click()
                selected = alternative['bundleVersionId']
                expect(page.locator('[data-current-input]')).to_have_attribute('data-input-version', selected, timeout=60000)
                expect(page.locator('[data-current-input] strong')).to_have_text(alternative['name'])
                expect(detail.locator('.view-state')).to_have_text('本资料正在使用。')
                assert page.evaluate(APP_STATE)['selected'] == selected
            report['checks'].append('only-explicit-adoption-changes-selected-input')

            page.get_by_role('button', name='实测资料', exact=True).click()
            expect(page.get_by_role('button', name='查询', exact=True)).to_be_enabled(timeout=60000)
            stations = api('database/stations')['stations']
            candidates = [row for row in stations if row.get('variable') == 'rain'
                          and row.get('lon') is not None and row.get('lat') is not None
                          and type(row.get('endHour')) is int and type(row.get('startHour')) is int
                          and row['endHour'] - row['startHour'] >= 17544]
            assert candidates, 'Acceptance needs a real rain station with multi-year coverage'
            station = max(candidates, key=lambda row: row.get('valid') or 0)
            dates = {'start': iso(EPOCH + timedelta(hours=station['startHour'])),
                     'end': iso(EPOCH + timedelta(hours=station['startHour'] + 17544))}
            page.get_by_label('查找测站', exact=True).fill(station['id'])
            page.locator('.station-list button').filter(has_text=station['id']).click()
            expect(page.get_by_role('button', name='查询', exact=True)).to_be_enabled(timeout=60000)
            page.get_by_label('资料起始时间', exact=True).fill(dates['start'])
            page.get_by_label('资料结束时间', exact=True).fill(dates['end'])
            page.get_by_role('button', name='查询', exact=True).click()
            expect(page.locator('.series-content')).to_be_visible(timeout=60000)
            expect(page.get_by_role('button', name='下一时段', exact=True)).to_be_enabled()
            query = {'stationId': station['id'], 'variable': 'rain', **dates}
            first = api('database/window?' + urlencode(query))
            limit = first['display']['limitHours']
            assert first['expected'] > limit and len(first['series']) == limit
            expect(page.locator('.series-summary')).to_contain_text(str(first['expected']))
            page.get_by_role('button', name='下一时段', exact=True).click()
            expect(page.get_by_role('button', name='上一时段', exact=True)).to_be_enabled()
            second = api('database/window?' + urlencode({**query, 'offsetHours': limit}))
            expect(page.locator('.display-toolbar')).to_contain_text(second['display']['start'].replace('T', ' '))
            assert first['valid'] == second['valid'] and first['total'] == second['total']
            assert first['series'][-1]['time'] < second['series'][0]['time']
            with page.expect_download(timeout=60000) as download:
                page.get_by_role('button', name='导出全部逐时资料', exact=True).click()
            path = output / '跨年完整逐时资料.csv'
            download.value.save_as(str(path))
            rows = list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            assert len(rows) == first['expected'] and rows[0]['time'] == dates['start'] and rows[-1]['time'] == dates['end']
            report['query'] = {**query, 'expected': first['expected'], 'valid': first['valid'], 'missing': first['missing'],
                               'displayLimitHours': limit, 'exportRows': len(rows),
                               'exportSHA256': hashlib.sha256(path.read_bytes()).hexdigest()}
            report['checks'].append('multi-year-query-full-statistics-segmented-hours-and-complete-csv')
            snapshot('desktop-multiyear-observations.png')

            page.get_by_role('button', name='洪水目录', exact=True).click()
            expect(page.locator('.event-section')).to_contain_text('目录登记洪水编号和起止时段')
            expect(page.locator('.event-section')).not_to_contain_text('同事方案')
            first_event = page.locator('.event-section tbody tr').first
            event_dates = first_event.locator('td').all_text_contents()[1:3]
            first_event.get_by_role('button', name='查看实测过程', exact=True).click()
            expect(page.locator('.series-content')).to_be_visible(timeout=60000)
            expect(page.get_by_label('资料起始时间', exact=True)).to_have_value(event_dates[0].replace(' ', 'T')[:16])
            expect(page.get_by_label('资料结束时间', exact=True)).to_have_value(event_dates[1].replace(' ', 'T')[:16])
            report['checks'].append('historical-flood-directory-opens-original-flow-query')

            page.get_by_role('button', name='模型计算资料', exact=True).click()
            expect(page.locator('[data-current-input]')).to_be_visible(timeout=60000)
            page.locator('.product-heading').get_by_role('button', name='资料整编', exact=True).click()
            processing = page.locator('[data-input-processing]')
            expect(processing).to_be_visible()
            for step in ('1．选取站点雨量', '2．生成网格降雨', '3．核对并采用生成资料'):
                expect(processing.get_by_role('heading', name=step, exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='保存站点雨量资料', exact=True)).to_be_disabled()
            assert page.locator('.source-form input[type=checkbox]:checked').count() == 0
            expect(page.locator('#input-operation')).to_have_value('stationIdw')
            expect(processing).to_contain_text('这一步只准备来源，尚未生成网格降雨')
            # Raw queries may start before the processing service's supported
            # years. Prepare the small source inside the selected model-input
            # range, independent of the earlier long archive query.
            selected_record = versions[selected]
            selected_catalog = api('inputs/catalog?' + urlencode({
                'projectId': selected_record['projectId'],
                'resolutionId': selected_record['resolutionId'], 'bundleVersionId': selected}))
            selected_coverage = selected_catalog.get('coverage') or {}
            source_ranges = [selected_record.get('recordQueryScope'), selected_catalog.get('latestPeriod'),
                             selected_coverage.get('common'), selected_coverage.get('precipitation')]
            source_station = source_start = source_end = source_window = None
            rain_stations = [station] + [row for row in stations if row['id'] != station['id']
                            and row.get('variable') == 'rain' and row.get('lon') is not None
                            and row.get('lat') is not None and type(row.get('startHour')) is int
                            and type(row.get('endHour')) is int]
            for source_range in source_ranges:
                if not source_range or not source_range.get('start') or not source_range.get('end'):
                    continue
                range_start = datetime.fromisoformat(source_range['start'])
                range_end = datetime.fromisoformat(source_range['end'])
                for source_candidate in rain_stations:
                    intersection_start = max(range_start, EPOCH + timedelta(hours=source_candidate['startHour']))
                    candidate_end = min(range_end, EPOCH + timedelta(hours=source_candidate['endHour']))
                    candidate_start = candidate_end - timedelta(hours=23)
                    if candidate_start < intersection_start:
                        continue
                    probe = api('database/window?' + urlencode({'stationId': source_candidate['id'],
                                'variable': 'rain', 'start': iso(candidate_start), 'end': iso(candidate_end)}))
                    if probe['valid'] > 0:
                        source_station, source_start, source_end, source_window = source_candidate, candidate_start, candidate_end, probe
                        break
                if source_station:
                    break
            assert source_station, 'Need real station rain in the currently selected model-input range'
            source_name = '页面核验站点雨量 · ' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            page.get_by_label('站点雨量开始', exact=True).fill(iso(source_start))
            page.get_by_label('站点雨量结束', exact=True).fill(iso(source_end))
            page.locator('.source-form input[maxlength="120"]').fill(source_name)
            page.locator('.source-form input[type=checkbox][value=' + json.dumps(source_station['id']) + ']').check()
            page.get_by_role('button', name='保存站点雨量资料', exact=True).click()
            expect(page.locator('.source-receipt')).to_contain_text(source_name, timeout=60000)
            source_id = page.locator('#input-source').input_value()
            assert source_id
            expect(page.locator('#input-source option:checked')).to_contain_text(source_name)
            expect(page.locator('#input-window-start')).to_have_value(iso(source_start))
            expect(page.locator('#input-window-end')).to_have_value(iso(source_end))
            page.get_by_role('button', name='刷新资料', exact=True).click()
            expect(page.get_by_role('button', name='刷新资料', exact=True)).to_be_enabled(timeout=60000)
            expect(page.locator('#input-source')).to_have_value(source_id)
            expect(page.locator('#input-source option:checked')).to_contain_text(source_name)
            assert page.evaluate(APP_STATE)['selected'] == selected
            assert before == inventory()
            report['savedTestSource'] = {'sourceId': source_id, 'name': source_name, 'stationId': source_station['id'],
                                         'start': iso(source_start), 'end': iso(source_end), 'validHours': source_window['valid']}
            report['checks'].append('point-rainfall-source-save-and-grid-form-handoff-without-input-generation')
            snapshot('desktop-three-step-rainfall-preparation.png')

            page.goto(base + '/#/config', wait_until='networkidle')
            page.get_by_role('button', name='水雨情资料', exact=True).click()
            summary = page.get_by_label('当前模型计算资料摘要', exact=True)
            expect(summary).to_be_visible(timeout=60000)
            expect(summary.locator('b')).to_have_text(versions[selected]['name'])
            expect(page.locator('.input-products')).to_have_count(0)
            link = summary.get_by_role('link', name='进入模型计算资料 →', exact=True)
            assert link.get_attribute('href').endswith('/data?tab=inputs')
            snapshot('desktop-config-data-summary.png')
            link.click()
            expect(page.locator('[data-current-input]')).to_have_attribute('data-input-version', selected, timeout=60000)
            expect(page.locator('.data-tabs .active')).to_have_text('模型计算资料')
            report['checks'].append('config-summary-links-to-single-input-management-entry')

            page.set_viewport_size({'width': 390, 'height': 844})
            for tab_name, filename in (('模型计算资料', 'mobile-model-inputs.png'),
                                       ('洪水目录', 'mobile-flood-directory.png'),
                                       ('实测资料', 'mobile-observations.png')):
                page.get_by_role('button', name=tab_name, exact=True).click()
                expect(page.locator('.data-tabs .active')).to_have_text(tab_name)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2'), tab_name
                snapshot(filename)
            page.get_by_role('button', name='模型计算资料', exact=True).click()
            inspect_record(versions[selected], selected, api('input-sources')['sources'])
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
            snapshot('mobile-input-composition.png')
            page.locator('.product-heading').get_by_role('button', name='资料整编', exact=True).click()
            expect(page.locator('[data-input-processing]')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
            snapshot('mobile-rainfall-preparation.png')
            report['checks'].append('390-pixel-tabs-composition-and-preparation-without-page-overflow')

            after = inventory()
            assert before == after
            assert api('input-products')['versions'] == versions_before
            assert len(api('input-sources')['sources']) == len(sources_before) + 1
            assert not report['errors'] and not report['consoleErrors'] and not report['failedResources'] and not report['httpErrors'], report
            report['numericalInventory'] = {key: len(value) for key, value in after.items()}
            report['testStationSourcesAdded'] = 1
            report['checks'].append('no-model-input-task-version-condition-created-and-no-browser-errors')
            report['status'] = 'passed'
        except Exception as failure:
            report['status'] = 'failed'
            report['failure'] = repr(failure)
            snapshot('failure.png')
            raise
        finally:
            (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            browser.close()
    print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'errors': report['errors']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
