"""Verify the two hydrological data pages in an isolated restored workspace.

No model, forecast or rainfall-processing task is submitted. One small station
source may be saved to verify its form handoff, only in the test workspace.
"""
import argparse
import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import expect, sync_playwright


STATE = """() => {
  const pinia = document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia;
  const app = pinia?._s.get('app'), monitor = pinia?._s.get('monitor');
  const map = document.querySelector('.map-canvas')?.__hydroMap;
  const visibility = id => map?.getLayer(id) ? map.getLayoutProperty(id, 'visibility') || 'visible' : null;
  return { selected: app?.selectedInputBundleVersionId, time: monitor?.currentTimeLabel,
    loaded: !!monitor?.hasHistoricalObservations, gridCount: monitor?.modelInputProduct?.cells?.length || 0,
    boundary: visibility('basin-line'), subbasin: visibility('subbasin-fill'),
    subbasinFeatures: map?.getSource('subbasins')?.serialize()?.data?.features?.length || 0,
    qpf: visibility('qpf-grid-fill'), smooth: visibility('smooth-rain-fill') };
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    base = args.url.rstrip('/')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'checks': [], 'errors': [], 'failedResources': [], 'screenshots': []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.on('requestfailed', lambda request: report['failedResources'].append({'url': request.url, 'reason': request.failure}))

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
                'inputVersions': sorted(row['bundleVersionId'] for row in api('input-products')['versions'])
            }

        try:
            workspace = api('workspace')
            assert workspace['version'] == '1.11.4'
            workspace_text = json.dumps(workspace, ensure_ascii=False)
            assert '开发产物' in workspace_text and '恢复工作区' in workspace_text, 'Use an isolated restored workspace'
            before = inventory()
            source_count_before = len(api('input-sources')['sources'])
            page.goto(base + '/#/realtime', wait_until='networkidle')
            page.locator('.basin-chip select').select_option('nananxi-xufan')
            page.locator('.scale-chip select').select_option('nananxi-xufan-1km')
            page.wait_for_function("""() => {
              const p = document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia;
              const map = document.querySelector('.map-canvas')?.__hydroMap;
              return p?._s.get('app')?.backendCase && p?._s.get('monitor')?.hasHistoricalObservations
                && map?.getLayer('basin-line');
            }""", timeout=60000)
            expect(page.locator('.clock-chip span')).to_have_text('回放时刻')
            assert page.locator('.clock-chip b').inner_text() == page.evaluate(STATE)['time']
            expect(page.get_by_role('checkbox', name='流域边界', exact=True)).to_be_visible()
            expect(page.get_by_role('checkbox', name='1 km 网格高程', exact=True)).to_be_visible()
            expect(page.get_by_role('checkbox', name='子流域', exact=True)).to_have_count(0)
            page.get_by_role('checkbox', name='流域边界', exact=True).check()
            page.wait_for_function("""() => document.querySelector('.map-canvas').__hydroMap.getLayoutProperty('basin-line','visibility') === 'visible'""")
            state = page.evaluate(STATE)
            assert state['subbasin'] == 'none' and state['subbasinFeatures'] == 0, state
            page.get_by_role('checkbox', name='流域边界', exact=True).uncheck()
            page.wait_for_function("""() => document.querySelector('.map-canvas').__hydroMap.getLayoutProperty('basin-line','visibility') === 'none'""")
            page.get_by_role('checkbox', name='流域边界', exact=True).check()
            expect(page.locator('.project-readiness')).to_contain_text('30 m 地形及汇流分区')
            report['checks'].append('historical-clock-and-grid-dem-with-correct-boundary-no-black-subbasin')
            snapshot('desktop-rainfall.png')

            page.get_by_role('link', name='查看或选用计算资料 →', exact=True).click()
            expect(page.locator('.processing-section .current-input strong')).to_be_visible(timeout=60000)
            table = page.get_by_role('table', name='已登记计算资料', exact=True)
            expect(table.locator('tbody tr')).to_have_count(4)
            for name in ('历史交付整编资料 · 20260819', '终结版MDB独立整编对照', '原插值方法整编对照', '同事终结方案配套资料 · 17个洪水窗口'):
                expect(table).to_contain_text(name)
            assert not page.locator('.optional-processing').evaluate('(el) => el.open')
            history = page.locator('.input-products > details.product-section').filter(has=page.locator('summary', has_text='降雨整编记录'))
            assert not history.evaluate('(el) => el.open')
            baseline = page.evaluate(STATE)['selected']
            source_row = table.locator('tbody tr').filter(has_text='同事终结方案配套资料 · 17个洪水窗口')
            source_row.get_by_role('button', name='查看来源', exact=True).click()
            expect(page.locator('.product-details .view-state')).to_have_text('本资料尚未选用；查看来源不会切换当前计算资料。')
            assert page.evaluate(STATE)['selected'] == baseline
            expect(source_row).to_contain_text('2020-12-25 00:00 → 2024-06-30 15:00')
            expect(source_row).not_to_contain_text('2024-12-31')
            report['checks'].append('four-input-purposes-real-extraction-range-and-view-does-not-select')
            snapshot('desktop-calculation-inputs.png')

            source_row.get_by_role('button', name='选用此资料', exact=True).click()
            expect(page.locator('.current-input strong')).to_have_text('同事终结方案配套资料 · 17个洪水窗口', timeout=60000)
            selected = page.evaluate(STATE)['selected']
            assert selected != baseline
            expect(page.locator('.product-details .view-state')).to_have_text('本资料正在使用。')
            page.get_by_role('link', name='雨水情', exact=True).click()
            page.wait_for_function("""() => {
              const m = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('monitor');
              return m.hasHistoricalObservations && m.modelInputProduct?.cells?.length === 110;
            }""", timeout=60000)
            state = page.evaluate(STATE)
            assert state['selected'] == selected and state['gridCount'] == 110
            assert page.locator('.clock-chip b').inner_text() == state['time']
            assert datetime.fromisoformat(state['time']) <= datetime.fromisoformat('2024-06-30 15:00')
            expect(page.locator('.project-forcing')).to_contain_text('同事终结方案配套资料')
            page.get_by_role('button', name='平滑', exact=True).click()
            page.wait_for_function("""() => {
              const map = document.querySelector('.map-canvas')?.__hydroMap;
              return map?.getLayoutProperty('smooth-rain-fill','visibility') === 'visible';
            }""", timeout=30000)
            assert page.evaluate(STATE)['qpf'] == 'none'
            report['checks'].append('explicit-input-selection-updates-rainfall-source-clock-and-smooth-display')
            snapshot('desktop-colleague-rainfall.png')

            page.get_by_role('link', name='水文资料库', exact=True).click()
            expect(page.get_by_role('button', name='查询', exact=True)).to_be_enabled(timeout=60000)
            dates = {'start': '2017-01-01T00:00', 'end': '2019-01-01T00:00'}
            page.get_by_label('资料起始时间', exact=True).fill(dates['start'])
            page.get_by_label('资料结束时间', exact=True).fill(dates['end'])
            page.get_by_role('button', name='查询', exact=True).click()
            expect(page.locator('.series-content')).to_be_visible(timeout=60000)
            expect(page.get_by_role('button', name='下一时段', exact=True)).to_be_enabled()
            query = {'stationId': '70149600', 'variable': 'rain', **dates}
            first = api('database/window?' + urlencode(query))
            assert first['expected'] > 8784 and len(first['series']) == 8784
            expect(page.locator('.series-summary')).to_contain_text(str(first['expected']))
            page.get_by_role('button', name='下一时段', exact=True).click()
            expect(page.get_by_role('button', name='上一时段', exact=True)).to_be_enabled()
            second = api('database/window?' + urlencode({**query, 'offsetHours': 8784}))
            expect(page.locator('.display-toolbar')).to_contain_text(second['display']['start'].replace('T', ' '))
            assert first['valid'] == second['valid'] and first['total'] == second['total']
            assert datetime.fromisoformat(first['series'][-1]['time']) < datetime.fromisoformat(second['series'][0]['time'])
            with page.expect_download(timeout=60000) as download:
                page.get_by_role('button', name='导出全部逐时资料', exact=True).click()
            path = output / '跨年完整逐时资料.csv'
            download.value.save_as(str(path))
            rows = list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            assert len(rows) == first['expected'] and rows[0]['time'] == dates['start'] and rows[-1]['time'] == dates['end']
            report['query'] = {**dates, 'expected': first['expected'], 'valid': first['valid'], 'missing': first['missing'], 'firstDisplayed': len(first['series']), 'exportRows': len(rows), 'exportSHA256': hashlib.sha256(path.read_bytes()).hexdigest()}
            report['checks'].append('multi-year-query-statistics-display-segments-and-full-hourly-csv')
            snapshot('desktop-multiyear-observations.png')

            page.get_by_role('button', name='计算降雨资料', exact=True).click()
            optional = page.locator('.optional-processing')
            assert not optional.evaluate('(el) => el.open')
            optional.locator(':scope > summary').click()
            expect(page.get_by_role('button', name='保存站点雨量资料', exact=True)).to_be_disabled()
            assert page.locator('.source-form input[type=checkbox]:checked').count() == 0
            assert '2017 年汛期' not in page.locator('.source-form').inner_text()
            page.get_by_label('站点雨量开始', exact=True).fill('2017-06-22T00:00')
            page.get_by_label('站点雨量结束', exact=True).fill('2017-06-22T23:00')
            page.locator('.source-form input[maxlength="120"]').fill('页面核验站点雨量 · 24小时')
            page.locator('.source-form fieldset label').filter(has_text='杉树湾').get_by_role('checkbox').check()
            page.get_by_role('button', name='保存站点雨量资料', exact=True).click()
            expect(page.locator('.source-receipt')).to_contain_text('页面核验站点雨量 · 24小时', timeout=60000)
            assert page.locator('#input-source').input_value()
            expect(page.locator('#input-source option:checked')).to_contain_text('页面核验站点雨量 · 24小时')
            expect(page.locator('#input-window-start')).to_have_value('2017-06-22T00:00')
            expect(page.locator('#input-window-end')).to_have_value('2017-06-22T23:00')
            page.get_by_role('button', name='刷新资料与记录', exact=True).click()
            expect(page.locator('#input-source option:checked')).to_contain_text('页面核验站点雨量 · 24小时')
            assert page.evaluate(STATE)['selected'] == selected
            report['checks'].append('optional-real-station-selection-saves-source-and-preserves-choice-through-refresh')
            snapshot('desktop-optional-rainfall-preparation.png')

            optional.locator(':scope > summary').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
            expect(page.locator('.current-input strong')).to_be_visible()
            snapshot('mobile-calculation-inputs.png')
            page.get_by_role('link', name='雨水情', exact=True).click()
            expect(page.get_by_role('button', name='平滑', exact=True)).to_be_visible(timeout=30000)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
            snapshot('mobile-rainfall.png')
            report['checks'].append('390-pixel-layout-and-existing-rainfall-controls')

            after = inventory()
            assert before == after
            assert len(api('input-sources')['sources']) == source_count_before + 1
            assert not report['errors'] and not report['failedResources'], report
            report['numericalInventory'] = {key: len(value) for key, value in after.items()}
            report['testStationSourcesAdded'] = 1
            report['checks'].append('no-model-forecast-rainfall-processing-or-input-version-created')
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
