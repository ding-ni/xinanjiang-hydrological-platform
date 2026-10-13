"""Check real grid/smooth rainfall display without submitting numerical jobs.

Run against an already running unified service and an isolated restored workspace.
The only UI changes are display, time, scale and existing input-version selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


READ_STATE = """() => {
  const pinia = document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia;
  const app = pinia?._s.get('app'), monitor = pinia?._s.get('monitor');
  const canvas = document.querySelector('.map-canvas'), map = canvas?.__hydroMap;
  const visibility = id => map?.getLayer(id) ? map.getLayoutProperty(id, 'visibility') || 'visible' : null;
  return {
    basin: app?.basinId, scale: app?.modelScale, selectedVersion: app?.selectedInputBundleVersionId,
    backendError: app?.backendError, mode: monitor?.rainDisplayMode, playing: monitor?.playing,
    loading: monitor?.historicalLoading, error: monitor?.historicalError,
    timeIndex: monitor?.timeIndex, timeLabel: monitor?.currentTimeLabel,
    replayEventId: monitor?.historicalReplayEventId,
    preview: monitor?.historicalPreview, product: monitor?.modelInputProduct,
    summary: monitor?.modelInputSummary, provenance: monitor?.modelInputProvenance,
    rainSeries: monitor?.selectedRainSeries,
    forcingNote: document.querySelector('.project-forcing .forcing-note')?.textContent?.trim() || '',
    legendNotes: Array.from(document.querySelectorAll('.map-legend small')).map(element => element.textContent.trim()),
    qpf: (map?.getSource('qpf-grid')?._data?.features || []).map(row => row.properties),
    qpfVisible: visibility('qpf-grid-fill'), smoothVisible: visibility('smooth-rain-fill'),
    smoothUrl: map?.getSource('smooth-rain')?.serialize()?.url || null,
    stationInterpolationVisible: visibility('rain-heat'),
    styleLoaded: Boolean(map?.isStyleLoaded())
  };
}"""

IMAGE_STATS = """async () => {
  const source = document.querySelector('.map-canvas')?.__hydroMap?.getSource('smooth-rain');
  const url = source?.serialize()?.url;
  if (!url) return {width: 0, height: 0, nontransparentPixels: 0};
  const image = new Image();
  await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error('smooth image decode failed')); image.src = url; });
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
  const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
  const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
  let count = 0;
  for (let i = 3; i < data.length; i += 4) if (data[i] > 0) count++;
  return {width: canvas.width, height: canvas.height, nontransparentPixels: count};
}"""

READY = """() => {
  const p = document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia;
  const m = p?._s.get('monitor'), map = document.querySelector('.map-canvas')?.__hydroMap;
  return !m?.historicalLoading && m?.historicalPreview?.sourceType === 'model-qc'
    && m?.modelInputProduct?.cells?.length === 110 && map?.isStyleLoaded()
    && map?.getSource('qpf-grid')?._data?.features?.length === 110;
}"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def normalize_hour(value):
    return str(value).replace('T', ' ')[:16]


def values_at(state, index=None):
    product = state['product']
    if index is None:
        index = next(i for i, stamp in enumerate(product['times'])
                     if normalize_hour(stamp) == normalize_hour(state['timeLabel']))
    return {cell['id']: cell['values'][index] for cell in product['cells']}


def assert_original_values(state):
    expected = values_at(state)
    actual = {row['id']: row.get('qpf') for row in state['qpf']}
    assert len(actual) == len(expected) == 110, 'Expected the 110 real registered cells'
    assert actual == expected, 'Map qpf values differ from the original model product'
    assert state['product']['bundleVersionId'] == state['selectedVersion'], 'Model product is from a different input version'
    # Production Vue intentionally omits DOM component debugging properties.
    # Confirm the rendered source description alongside the exact MapLibre
    # values and the separately checked immutable backend input product.
    assert not any('站点插值' in note for note in state['legendNotes']), 'Station interpolation legend appeared in the model-input workspace'
    expected_note = '网格雨量平滑显示' if state['mode'] == 'smooth' else '模型计算网格降雨'
    assert expected_note in state['forcingNote'], 'Rendered rainfall source note disagrees with the selected display mode: ' + state['forcingNote']
    if state['smoothVisible'] == 'visible':
        assert any('网格雨量平滑显示' in note for note in state['legendNotes']), 'Smooth image is not identified as a model-grid display'
    elif state['qpfVisible'] == 'visible':
        assert any('后端整编格点雨量' in note for note in state['legendNotes']), 'Grid layer is not identified as registered backend rainfall'


def unchanged_inputs(before, after):
    for key in ('product', 'summary', 'preview', 'provenance'):
        assert before[key] == after[key], 'Display modified ' + key
    assert before['timeIndex'] == after['timeIndex']
    assert before['timeLabel'] == after['timeLabel']
    assert_original_values(after)


def varying_indices(state):
    cells, times = state['product']['cells'], state['product']['times']
    signatures, indices = set(), []
    for i in range(len(times)):
        signature = tuple(cell['values'][i] for cell in cells)
        if signature not in signatures and any(value is not None and value > 0 for value in signature):
            signatures.add(signature)
            indices.append(i)
    return indices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    base = args.url.rstrip('/')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'checks': [], 'errors': [], 'failedResources': [],
              'expectedOfflineFailures': [], 'refreshLatencyMs': [], 'screenshots': []}
    phase = {'offline': False}
    aborted_urls = set()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            # Long-task measurements affect only this fresh browser context.
            context.add_init_script("""window.__rainfallLongTasks = [];
              if (typeof PerformanceObserver !== 'undefined') {
                try { new PerformanceObserver(list => {
                  for (const entry of list.getEntries()) window.__rainfallLongTasks.push({start: entry.startTime, duration: entry.duration});
                }).observe({type: 'longtask', buffered: true}); } catch (_) {}
              }""")
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))

            def request_failed(request):
                row = {'url': request.url, 'reason': request.failure}
                if request.url in aborted_urls:
                    report['expectedOfflineFailures'].append(row)
                else:
                    report['failedResources'].append(row)

            def response_received(response):
                if response.status >= 400:
                    report['failedResources'].append({'url': response.url, 'status': response.status})

            def console_received(message):
                if message.type != 'error':
                    return
                row = {'text': message.text, 'url': message.location.get('url', '')}
                expected = phase['offline'] and ('/api/model/' in row['url'] or
                           ('Failed to load resource' in row['text'] and 'net::ERR_FAILED' in row['text']))
                (report['expectedOfflineFailures'] if expected else report['errors']).append(row)

            page.on('requestfailed', request_failed)
            page.on('response', response_received)
            page.on('console', console_received)

            def api(resource, params=None):
                response = context.request.get(base + '/api/model/' + resource, params=params, timeout=60000)
                assert response.ok, (response.status, response.text())
                return response.json()

            def numerical_inventory():
                return {'modelTasks': sorted(row['taskId'] for row in api('tasks')['tasks']),
                        'inputTasks': sorted(row['taskId'] for row in api('input-tasks')['tasks']),
                        'inputVersions': sorted(row['bundleVersionId'] for row in api('input-products')['versions'])}

            def state():
                return page.evaluate(READ_STATE)

            def screenshot(name):
                page.screenshot(path=str(output / name), full_page=True)
                report['screenshots'].append(name)

            def check(name):
                report['checks'].append(name)

            def navigate(route):
                link = page.locator(f'a[href="#/{route}"]').first
                if link.count() and link.is_visible():
                    link.click()
                    page.wait_for_url(re.compile(r'#/' + route + r'$'))
                    page.wait_for_load_state('networkidle')
                else:
                    page.goto(base + '/#/' + route, wait_until='networkidle')

            def wait_grid():
                page.wait_for_function(READY, timeout=60000)
                page.wait_for_function("""() => {
                  const map = document.querySelector('.map-canvas')?.__hydroMap;
                  return map && !map.isMoving() && map.getCenter().lng > 100;
                }""", timeout=30000)

            def choose_input(text):
                navigate('config')
                page.get_by_role('button', name='水雨情资料', exact=True).click()
                table = page.get_by_role('table', name='已登记输入版本', exact=True)
                expect(table).to_be_visible(timeout=60000)
                row = table.locator('tbody tr').filter(has_text=text).first
                expect(row).to_be_visible(timeout=60000)
                version = row.locator('td').first.locator('code').first.inner_text()
                button = row.get_by_role('button', name='选用此版本', exact=True)
                if button.count():
                    button.click()
                expect(row.get_by_role('button', name='当前选用', exact=True)).to_be_visible(timeout=60000)
                navigate('realtime')
                wait_grid()
                assert state()['selectedVersion'] == version
                return version

            def registered_snapshot(snapshot):
                remote = api('inputs/window', params={'projectId': 'nananxi-xufan', 'resolutionId': '1km',
                             'bundleVersionId': snapshot['selectedVersion'],
                             'start': snapshot['preview']['start'], 'end': snapshot['preview']['end']})
                assert snapshot['product'] == remote['product']
                assert snapshot['summary'] == remote['summary']
                assert snapshot['preview']['river']['flows'] == remote['observationPreview']['river']['flows']
                return {'bundleVersionId': snapshot['selectedVersion'], 'productSHA256': digest(snapshot['product']),
                        'arealRainfallSHA256': digest(snapshot['summary']['arealRainfallMm']),
                        'hours': len(snapshot['product']['times']), 'gridCount': len(snapshot['product']['cells'])}

            def ensure_varying_window():
                snapshot = state()
                if len(varying_indices(snapshot)) >= 2:
                    return snapshot
                page.locator('.replay-mode select').select_option('event')
                event_select = page.locator('.replay-event select')
                expect(event_select).to_be_enabled(timeout=60000)
                options = event_select.locator('option').evaluate_all('rows => rows.filter(r => r.value && !r.disabled).map(r => r.value)')
                for event_id in options[:6]:
                    event_select.select_option(event_id)
                    page.wait_for_function("""id => {
                      const m = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('monitor');
                      return !m.historicalLoading && m.historicalReplayEventId === id && Boolean(m.modelInputProduct);
                    }""", arg=event_id, timeout=60000)
                    wait_grid()
                    snapshot = state()
                    if len(varying_indices(snapshot)) >= 2:
                        return snapshot
                raise AssertionError('No registered playback window contains two different wet hourly fields')

            def set_index(index, require_new_image=False):
                before = state()
                expected = values_at(before, index)
                started = time.monotonic()
                # Dispatch the native range input event; never write Pinia/model state.
                page.locator('.timeline input.range').evaluate("""(element, index) => {
                  element.value = String(index); element.dispatchEvent(new Event('input', {bubbles: true}));
                }""", index)
                page.wait_for_function("""expected => {
                  const p = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
                  const m = p._s.get('monitor'), map = document.querySelector('.map-canvas')?.__hydroMap;
                  const rows = map?.getSource('qpf-grid')?._data?.features || [];
                  return m.timeIndex === expected.index && rows.length === 110
                    && rows.every(row => row.properties.qpf === expected.values[row.properties.id]);
                }""", arg={'index': index, 'values': expected}, timeout=30000)
                if require_new_image:
                    page.wait_for_function("""previous => {
                      const map = document.querySelector('.map-canvas')?.__hydroMap;
                      const url = map?.getSource('smooth-rain')?.serialize()?.url;
                      return url && url !== previous && map.getLayoutProperty('smooth-rain-fill', 'visibility') === 'visible';
                    }""", arg=before['smoothUrl'], timeout=15000)
                    stats = page.evaluate(IMAGE_STATS)
                    assert stats['width'] > 1 and stats['height'] > 1
                    report['refreshLatencyMs'].append(round((time.monotonic() - started) * 1000, 2))
                assert_original_values(state())

            def wait_clear():
                page.wait_for_function("""() => {
                  const p = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
                  const m = p._s.get('monitor'), map = document.querySelector('.map-canvas')?.__hydroMap;
                  return !m.modelInputProduct && !m.historicalPreview
                    && (map?.getSource('qpf-grid')?._data?.features || []).length === 0;
                }""", timeout=45000)
                cleared = state()
                pixels = page.evaluate(IMAGE_STATS)
                assert cleared['smoothVisible'] == 'none' or pixels['nontransparentPixels'] == 0
                assert cleared['stationInterpolationVisible'] in (None, 'none')
                return pixels

            inventory = numerical_inventory()
            page.goto(base + '/#/realtime', wait_until='networkidle')
            page.locator('.basin-chip select').select_option('nananxi-xufan')
            page.locator('.scale-chip select').select_option('nananxi-xufan-1km')
            wait_grid()
            # Start from the historical registered baseline, then explicitly
            # exercise adoption of the colleague's separate 17-window product.
            report['baselineVersion'] = choose_input('NANANXI_QC_20260819_CONTROL_V2')
            ensure_varying_window()
            group = page.get_by_role('group', name='降雨显示方式', exact=True)
            expect(group.get_by_role('button')).to_have_count(2)
            grid_button = group.get_by_role('button', name='网格', exact=True)
            smooth_button = group.get_by_role('button', name='平滑', exact=True)
            rain_checkbox = page.get_by_role('checkbox', name='降雨场', exact=True)
            rain_checkbox.check()
            grid_button.click()
            set_index(varying_indices(state())[0])
            initial = state()
            assert_original_values(initial)
            report['baseline'] = registered_snapshot(initial)
            assert initial['qpfVisible'] == 'visible' and initial['smoothVisible'] == 'none'
            screenshot('desktop-grid.png')
            check('110-grid-values-and-areal-rainfall-match-registered-input')

            smooth_button.click()
            expect(smooth_button).to_have_attribute('aria-pressed', 'true')
            expect(grid_button).to_have_attribute('aria-pressed', 'false')
            page.wait_for_function("""() => {
              const map = document.querySelector('.map-canvas')?.__hydroMap;
              return map?.getLayoutProperty('smooth-rain-fill', 'visibility') === 'visible'
                && map.getLayoutProperty('qpf-grid-fill', 'visibility') === 'none'
                && (map.getSource('smooth-rain')?.serialize()?.url || '').length > 500;
            }""", timeout=30000)
            smoothed = state()
            unchanged_inputs(initial, smoothed)
            image = page.evaluate(IMAGE_STATS)
            assert image['width'] > 1 and image['height'] > 1 and image['nontransparentPixels'] > 100
            report['smoothImage'] = {**image, 'urlSHA256': digest(smoothed['smoothUrl'])}
            screenshot('desktop-smooth.png')
            check('exclusive-display-modes-preserve-complete-model-inputs')

            for index in varying_indices(smoothed)[1:9]:
                set_index(index, require_new_image=True)
            playback_state = state()
            cells, times = playback_state['product']['cells'], playback_state['product']['times']
            pair = next(i for i in range(len(times) - 1)
                        if any(cell['values'][i] != cell['values'][i + 1] for cell in cells))
            set_index(pair)
            before_playback = state()
            page.locator('.play-speed select').select_option('4')
            page.get_by_role('button', name='开始回放', exact=True).click()
            page.wait_for_function("""values => {
              const m = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('monitor');
              return m.playing && m.modelInputProduct.cells.some(cell => cell.values[m.timeIndex] !== values[cell.id]);
            }""", arg=values_at(before_playback), timeout=15000)
            page.get_by_role('button', name='暂停回放', exact=True).click()
            expect(page.get_by_role('button', name='开始回放', exact=True)).to_be_enabled()
            paused = state()
            set_index(paused['timeIndex'])
            page.wait_for_function("""previous => {
              const source = document.querySelector('.map-canvas')?.__hydroMap?.getSource('smooth-rain');
              return source?.serialize()?.url && source.serialize().url !== previous;
            }""", arg=before_playback['smoothUrl'], timeout=15000)
            assert state()['playing'] is False
            report['playback'] = {'before': before_playback['timeLabel'], 'paused': paused['timeLabel'],
                                  'pausedImage': page.evaluate(IMAGE_STATS)}
            screenshot('desktop-playback-paused.png')
            check('time-slider-and-playback-refresh-current-image-after-pause')

            before_hidden = state()
            rain_checkbox.uncheck()
            page.wait_for_function("""() => {
              const map = document.querySelector('.map-canvas')?.__hydroMap;
              return map?.getLayoutProperty('smooth-rain-fill', 'visibility') === 'none'
                && map.getLayoutProperty('qpf-grid-fill', 'visibility') === 'none';
            }""")
            hidden = state()
            unchanged_inputs(before_hidden, hidden)
            report['hiddenImage'] = page.evaluate(IMAGE_STATS)
            assert report['hiddenImage']['nontransparentPixels'] == 0
            rain_checkbox.check()
            page.wait_for_function("""() => {
              const map = document.querySelector('.map-canvas')?.__hydroMap;
              return map?.getLayoutProperty('smooth-rain-fill', 'visibility') === 'visible'
                && (map.getSource('smooth-rain')?.serialize()?.url || '').length > 500;
            }""")
            unchanged_inputs(before_hidden, state())
            check('hide-and-restore-rain-layer-clear-image-without-changing-input')

            report['colleagueVersion'] = choose_input(re.compile(r'同事.*来源.*17.*窗口'))
            assert report['colleagueVersion'] != report['baselineVersion']
            adopted = ensure_varying_window()
            set_index(varying_indices(adopted)[0])
            group.get_by_role('button', name='平滑', exact=True).click()
            page.wait_for_function("""() => {
              const map = document.querySelector('.map-canvas')?.__hydroMap;
              return map?.getLayoutProperty('smooth-rain-fill', 'visibility') === 'visible'
                && (map.getSource('smooth-rain')?.serialize()?.url || '').length > 500;
            }""", timeout=30000)
            adopted = state()
            assert_original_values(adopted)
            report['colleague'] = registered_snapshot(adopted)
            assert report['colleague']['productSHA256'] != report['baseline']['productSHA256']
            assert page.evaluate(IMAGE_STATS)['nontransparentPixels'] > 100
            screenshot('desktop-colleague-input.png')
            check('colleague-17-window-adoption-replaces-grid-and-smooth-data-source')

            page.set_viewport_size({'width': 390, 'height': 844})
            expect(group).to_be_visible()
            page.wait_for_function('document.documentElement.scrollWidth <= innerWidth + 1', timeout=15000)
            assert_original_values(state())
            screenshot('mobile-smooth.png')
            grid_button.click()
            expect(grid_button).to_have_attribute('aria-pressed', 'true')
            screenshot('mobile-grid.png')
            smooth_button.click()
            page.set_viewport_size({'width': 1440, 'height': 1000})
            check('1440-and-390-layouts-and-display-controls')

            page.locator('.scale-chip select').select_option('nananxi-xufan-0p1deg')
            page.wait_for_function("""() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('app').modelScale === '0p1deg'""")
            report['unsupportedScaleImage'] = wait_clear()
            screenshot('desktop-unsupported-scale.png')
            check('unsupported-scale-clears-old-rain-and-never-uses-station-interpolation')
            page.locator('.scale-chip select').select_option('nananxi-xufan-1km')
            wait_grid()
            restored = ensure_varying_window()
            set_index(varying_indices(restored)[0])
            smooth_button.click()
            page.wait_for_function("""() => (document.querySelector('.map-canvas')?.__hydroMap?.getSource('smooth-rain')?.serialize()?.url || '').length > 500""")

            def abort_model(route):
                aborted_urls.add(route.request.url)
                route.abort()

            phase['offline'] = True
            page.route('**/api/model/**', abort_model)
            page.locator('.replay-mode select').select_option('latest')
            refresh = page.get_by_role('button', name='刷新并载入最新共同覆盖时段', exact=True)
            if refresh.is_enabled():
                refresh.click()
            report['offlineImage'] = wait_clear()
            assert state()['error'] or state()['backendError']
            expect(page.get_by_role('button', name='开始回放', exact=True)).to_be_disabled()
            screenshot('desktop-offline.png')
            page.unroute('**/api/model/**', abort_model)
            check('backend-offline-invalidates-model-input-and-clears-smooth-image')

            assert numerical_inventory() == inventory, 'Display acceptance changed numerical jobs or registered products'
            report['numericalInventory'] = {key: len(value) for key, value in inventory.items()}
            report['longTasks'] = page.evaluate('window.__rainfallLongTasks || []')
            samples = sorted(report['refreshLatencyMs'])
            report['refreshPerformance'] = {'samples': len(samples),
                'p50Ms': samples[math.ceil(len(samples) * .5) - 1] if samples else None,
                'p95Ms': samples[math.ceil(len(samples) * .95) - 1] if samples else None,
                'maximumMs': max(samples) if samples else None}
            assert samples, 'No measured image refresh'
            assert not report['errors'], report['errors']
            assert not report['failedResources'], report['failedResources']
            check('no-model-computation-registration-or-unintentional-browser-errors')
            report['status'] = 'passed'
            print(json.dumps({'status': report['status'], 'checks': len(report['checks']),
                              'gridCount': 110, 'p95RefreshMs': report['refreshPerformance']['p95Ms']}, ensure_ascii=False))
        except Exception as error:
            report['failure'] = type(error).__name__ + ': ' + str(error)
            try:
                failed_state = state()
                report['failureContext'] = {
                    'completedChecks': report['checks'], 'selectedVersion': failed_state['selectedVersion'],
                    'timeLabel': failed_state['timeLabel'], 'mode': failed_state['mode'],
                    'modelGridCount': len((failed_state['product'] or {}).get('cells', [])),
                    'mapGridCount': len(failed_state['qpf']), 'forcingNote': failed_state['forcingNote'],
                    'legendNotes': failed_state['legendNotes'], 'productSHA256': digest(failed_state['product']),
                    'mapValuesSHA256': digest({row['id']: row.get('qpf') for row in failed_state['qpf']}),
                    'inputError': failed_state['error'], 'backendError': failed_state['backendError']}
                if failed_state['product']:
                    expected = values_at(failed_state)
                    actual = {row['id']: row.get('qpf') for row in failed_state['qpf']}
                    report['failureContext']['expectedValuesSHA256'] = digest(expected)
                    report['failureContext']['differentGridIds'] = [key for key in sorted(set(expected) | set(actual))
                        if key not in expected or key not in actual or expected[key] != actual[key]]
            except Exception as diagnostic_error:
                report['failureContextError'] = type(diagnostic_error).__name__ + ': ' + str(diagnostic_error)
            raise
        finally:
            (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            browser.close()


if __name__ == '__main__':
    main()
