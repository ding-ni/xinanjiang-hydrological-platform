/**
 * 轻量单测：node --test（Node 18+）
 * npm test
 */
import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  simulateHydrograph,
  warnLevelFromPeak,
  DEFAULT_PARAMS,
  computeMetrics
} from '../src/models/xajParamEngine.js'
import {
  buildCalibrationRequest,
  selectCalibrationCandidates,
  stratifiedRandomEventSplit
} from '../src/services/calibrationSubmissionService.js'
import { buildForecastRequest } from '../src/services/forecastSubmissionService.js'
import { normalizeFloodEvent } from '../src/services/floodEventService.js'
import { interpolateRainfallAt, pointInGeoJSON } from '../src/services/rainfallInterpolationService.js'
import { formatObservationCoverage, getObservationCoverage } from '../src/services/observationCoverageService.js'
import { selectObservedHydrologyDataset, selectObservedPrecipitationDataset } from '../src/services/datasetSelectionService.js'

describe('warnLevelFromPeak', () => {
  it('returns 正常 under thresholds', () => {
    assert.equal(warnLevelFromPeak(1000, 33, { warnLevel: 34.5, ensureLevel: 36, warnFlow: 2800 }), '正常')
  })
  it('returns 警戒 near warn', () => {
    assert.equal(warnLevelFromPeak(2900, 34.6, { warnLevel: 34.5, ensureLevel: 36, warnFlow: 2800 }), '警戒')
  })
  it('returns 保证 at ensure', () => {
    assert.equal(warnLevelFromPeak(3500, 36.1, { warnLevel: 34.5, ensureLevel: 36, warnFlow: 2800 }), '保证')
  })
})

describe('simulateHydrograph', () => {
  it('peaks higher when wetter', () => {
    const dry = simulateHydrograph(DEFAULT_PARAMS, { wetness: 0.3, basePeak: 2850, hours: 48 })
    const wet = simulateHydrograph(DEFAULT_PARAMS, { wetness: 0.9, basePeak: 2850, hours: 48 })
    assert.ok(wet.peak > dry.peak)
  })
  it('WM increase lowers peak roughly', () => {
    const a = simulateHydrograph({ ...DEFAULT_PARAMS, WM: 100 }, { basePeak: 2850 })
    const b = simulateHydrograph({ ...DEFAULT_PARAMS, WM: 140 }, { basePeak: 2850 })
    assert.ok(b.peak < a.peak)
  })
  it('has startMark and obs segment', () => {
    const h = simulateHydrograph(DEFAULT_PARAMS, { obsHours: 6, hours: 24 })
    assert.ok(h.startMark)
    assert.ok(h.obs.length > 0)
    assert.equal(h.forecast.length, 25)
  })
  it('places 48-hour observations before forecast start for duty runs', () => {
    const h = simulateHydrograph(DEFAULT_PARAMS, {
      obsHours: 48,
      hours: 72,
      observedBeforeStart: true,
      start: '2026-06-02T14:00:00'
    })
    assert.equal(h.obs.length, 49)
    assert.equal(h.obs.at(-1).time, h.startMark)
    assert.equal(h.forecast.find((d) => d.time === h.startMark).value != null, true)
    assert.equal(h.forecast[0].value, null)
  })

  it('joins observed and forecast discharge at issue time', () => {
    const h = simulateHydrograph(DEFAULT_PARAMS, {
      start: '2026-06-02T14:00:00',
      obsHours: 48,
      observedBeforeStart: true
    })
    const observedAtIssue = h.obs.find((point) => point.time === h.startMark)
    const forecastAtIssue = h.forecast.find((point) => point.time === h.startMark)
    assert.equal(forecastAtIssue.value, observedAtIssue.value)
  })
  it('keeps a Selinco inflow forecast continuous and within section scale', () => {
    const h = simulateHydrograph(DEFAULT_PARAMS, {
      domainId: 'selinco',
      targetPeak: true,
      basePeak: 264,
      baseFlow: 186,
      issueFlow: 186,
      start: '2026-06-02T14:00:00',
      obsHours: 48,
      observedBeforeStart: true,
      hours: 72
    })
    assert.equal(h.obs.at(-1).value, h.forecast.find((point) => point.time === h.startMark).value)
    assert.ok(h.peak >= 240 && h.peak <= 290, `unexpected Selinco peak ${h.peak}`)
    assert.ok(Math.abs(h.forecast.find((point) => point.time === h.startMark).value - h.forecast.find((point) => point.value != null && point.time !== h.startMark).value) < 40)
  })
  it('keeps calibration observations close enough for meaningful NSE', () => {
    const h = simulateHydrograph(DEFAULT_PARAMS, { obsHours: 36, hours: 72 })
    assert.ok(h.metrics.nse > 0.8)
  })
})

describe('computeMetrics', () => {
  it('perfect match NSE ~ 1', () => {
    const series = [
      { time: 'a', value: 1 },
      { time: 'b', value: 2 },
      { time: 'c', value: 3 }
    ]
    const m = computeMetrics(series, series)
    assert.ok(m.nse > 0.99)
  })
})

describe('南安溪洪水场次划分', () => {
  const events = Array.from({ length: 226 }, (_, index) => ({
    id: `XF-${index + 1}`,
    observedPeak: index + 1,
    purpose: '未使用'
  }))

  it('按洪峰分层随机划分为 158、45、23 场', () => {
    const result = stratifiedRandomEventSplit(events, '42')
    assert.equal(result.filter((item) => item.purpose === '率定').length, 158)
    assert.equal(result.filter((item) => item.purpose === '验证').length, 45)
    assert.equal(result.filter((item) => item.purpose === '独立检验').length, 23)
    assert.equal(new Set(result.map((item) => item.id)).size, 226)
  })

  it('相同种子可复现，不同种子产生不同名单', () => {
    const first = stratifiedRandomEventSplit(events, '42')
    const repeated = stratifiedRandomEventSplit(events, '42')
    const changed = stratifiedRandomEventSplit(events, '43')
    assert.deepEqual(first, repeated)
    assert.notDeepEqual(first, changed)
  })

  it('场次率定请求采用 0 mm/h 蒸散发', () => {
    const app = { basinId: 'nananxi-xufan', basinName: '南安溪-徐畈', modelScale: '1km', modelConfigId: 'xaj-1km' }
    const calibration = {
      contextKey: 'nananxi-xufan:1km', calibrationMode: 'event', warmupDays: 5,
      calibrationStart: '', calibrationEnd: '', validationStart: '', validationEnd: '',
      section: '徐畈（二）', paramVersion: '待率定', params: {}, criteria: {},
      algorithm: 'SCE-UA', maxEvaluations: 1000, populationSize: 20
    }
    const request = buildCalibrationRequest({ app, calibration, operationMode: 'calibration', events })
    assert.deepEqual(request.inputs.evaporation, {
      mode: 'constant-zero', valueMmPerHour: 0, scope: 'event-simulation'
    })
  })

  it('先按候选规则筛选，再进行率定划分', () => {
    const events = [
      normalizeFloodEvent({ id: 'xf-a', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'A', start: '2020-01-01T00:00', end: '2020-01-01T03:00', observedPeak: 12 }),
      normalizeFloodEvent({ id: 'xf-b', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'B', start: '2021-01-01T00:00', end: '2021-01-01T03:00', observedPeak: 55 }),
      normalizeFloodEvent({ id: 'xf-c', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'C', start: '2022-01-01T00:00', end: '2022-01-01T03:00', observedPeak: 31, status: 'inactive' }),
      normalizeFloodEvent({ id: 'xf-d', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'D', start: '2023-01-01T00:00', end: '2023-01-01T03:00', observedPeak: 80, status: 'draft' })
    ]
    assert.deepEqual(selectCalibrationCandidates(events, { method: 'top-n', topN: 1 }).map((event) => event.sourceFloodNo), ['B'])
    assert.deepEqual(selectCalibrationCandidates(events, { method: 'peak-threshold', peakThreshold: 30 }).map((event) => event.sourceFloodNo), ['B'])
    assert.deepEqual(selectCalibrationCandidates(events, { method: 'year', year: '2020' }).map((event) => event.sourceFloodNo), ['A'])
  })

  it('率定请求保留事件集版本和原始洪号', () => {
    const app = { basinId: 'nananxi-xufan', basinName: '南安溪-徐畈', modelScale: '1km', modelConfigId: 'xaj-1km' }
    const calibration = {
      contextKey: 'nananxi-xufan:1km', calibrationMode: 'event', warmupDays: 5,
      calibrationStart: '', calibrationEnd: '', validationStart: '', validationEnd: '',
      section: '徐畈（二）', paramVersion: '待率定', params: {}, criteria: {},
      algorithm: 'SCE-UA', maxEvaluations: 1000, populationSize: 20,
      eventSetVersion: '2026-08-03-226-r1', eventCatalogSchemaVersion: '1.1'
    }
    const event = normalizeFloodEvent({ id: 'xf-20240619', basinProjectId: 'nananxi-xufan', sourceFloodNo: '31240619', start: '2024-06-19T00:00', end: '2024-06-20T00:00', observedPeak: 166 })
    const request = buildCalibrationRequest({
      app,
      calibration,
      operationMode: 'calibration',
      events: [{ ...event, purpose: '率定' }],
      eventSelection: { method: 'top-n', topN: 1, selectedEventIds: [event.eventId] }
    })
    assert.equal(request.period.eventSet.version, '2026-08-03-226-r1')
    assert.equal(request.period.events[0].sourceFloodNo, '31240619')
    assert.equal(request.period.eventSet.selection.method, 'top-n')
  })

  it('filters complete flood events by date range', () => {
    const events = [
      normalizeFloodEvent({ id: 'xf-in', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'IN', start: '2020-06-01T00:00:00Z', end: '2020-06-02T00:00:00Z', observedPeak: 20 }),
      normalizeFloodEvent({ id: 'xf-before', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'BEFORE', start: '2019-06-01T00:00:00Z', end: '2019-06-02T00:00:00Z', observedPeak: 30 }),
      normalizeFloodEvent({ id: 'xf-overlap', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'OVERLAP', start: '2020-05-31T12:00:00Z', end: '2020-06-01T12:00:00Z', observedPeak: 40 }),
      normalizeFloodEvent({ id: 'xf-after', basinProjectId: 'nananxi-xufan', sourceFloodNo: 'AFTER', start: '2020-06-03T00:00:00Z', end: '2020-06-04T00:00:00Z', observedPeak: 50 })
    ]
    const selected = selectCalibrationCandidates(events, {
      method: 'date-range',
      startDate: '2020-06-01T00:00:00Z',
      endDate: '2020-06-03T00:00:00Z'
    })
    assert.deepEqual(selected.map((event) => event.sourceFloodNo), ['IN'])
  })

  it('recognizes points inside and outside the basin polygon', () => {
    const basin = {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'Polygon',
        coordinates: [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]
      }
    }
    assert.equal(pointInGeoJSON([1, 1], basin), true)
    assert.equal(pointInGeoJSON([3, 1], basin), false)
  })

  it('returns the station observation and a bounded IDW value', () => {
    const stations = [
      { lng: 0, lat: 0, rain1h: 10 },
      { lng: 2, lat: 0, rain1h: 30 }
    ]
    assert.equal(interpolateRainfallAt(0, 0, stations), 10)
    const middle = interpolateRainfallAt(1, 0, stations)
    assert.ok(middle > 10 && middle < 30, `unexpected IDW value ${middle}`)
  })

  it('ignores stations without a current observation', () => {
    const value = interpolateRainfallAt(1, 0, [
      { lng: 0, lat: 0, rain1h: 10 },
      { lng: 2, lat: 0, rain1h: null },
      { lng: 4, lat: 0, rain1h: 30 }
    ])
    assert.ok(value > 10 && value < 30)
  })

  it('uses the precipitation grid matching the selected model scale', () => {
    const baseApp = {
      basinId: 'nananxi-xufan', basinName: 'Nananxi Xufan', modelConfigId: 'xaj-1km',
      projectData: { datasets: { datasets: [
        { id: 'XF-OBS-RAIN-GRID-1KM-NC', type: 'observed-precipitation-grid-hourly', role: 'external-model-input' },
        { id: 'XF-OBS-RAIN-GRID-0P1DEG-NC', type: 'observed-precipitation-grid-hourly', role: 'external-model-input' }
      ] } }
    }
    const calibration = {
      contextKey: 'nananxi-xufan:1km', calibrationMode: 'event', warmupDays: 5,
      calibrationStart: '', calibrationEnd: '', validationStart: '', validationEnd: '',
      section: 'Xufan', paramVersion: 'pending', params: {}, criteria: {},
      algorithm: 'SCE-UA', maxEvaluations: 1000, populationSize: 20
    }
    const oneKm = buildCalibrationRequest({
      app: { ...baseApp, modelScale: '1km' }, calibration, operationMode: 'calibration'
    })
    const coarse = buildCalibrationRequest({
      app: { ...baseApp, modelScale: '0p1deg' }, calibration, operationMode: 'calibration'
    })
    assert.equal(oneKm.inputs.precipitationDatasetId, 'XF-OBS-RAIN-GRID-1KM-NC')
    assert.equal(coarse.inputs.precipitationDatasetId, 'XF-OBS-RAIN-GRID-0P1DEG-NC')
    assert.equal(coarse.lineage.dataVersionId, 'XF-OBS-RAIN-GRID-0P1DEG-NC')

    const forecast = buildForecastRequest({
      app: { ...baseApp, modelScale: '0p1deg' },
      forecast: {
        contextKey: 'nananxi-xufan:0p1deg', observationDataId: 'XF-OBS-RAIN-HOURLY',
        startTime: '2024-07-06 08:00', leadHours: 6, forecastEndTime: '2024-07-06 14:00',
        forecastMode: 'manual', targetSectionId: '70115800', paramVersion: 'candidate', stateVersion: null
      },
      rainProduct: null
    })
    assert.equal(forecast.inputs.observation.griddedPrecipitationDatasetId, 'XF-OBS-RAIN-GRID-0P1DEG-NC')
  })
})

describe('实测资料共同覆盖范围', () => {
  it('uses the latest common start and earliest common end', () => {
    const catalog = {
      rain: { start: '1959-05-02T20:00', end: '2025-08-12T23:00' },
      river: { start: '1964-04-04T00:00', end: '2024-07-06T08:00' }
    }
    const coverage = getObservationCoverage(catalog)
    assert.equal(coverage.startText, '1964-04-04 00:00')
    assert.equal(coverage.endText, '2024-07-06 08:00')
    assert.equal(formatObservationCoverage(catalog), '1964-04-04 00:00 至 2024-07-06 08:00')
  })

  it('moves the common end when a new outlet record is appended', () => {
    const catalog = {
      rain: { start: '1959-05-02T20:00', end: '2026-01-02T00:00' },
      river: { start: '1964-04-04T00:00', end: '2025-12-31T23:00' }
    }
    assert.equal(getObservationCoverage(catalog).endText, '2025-12-31 23:00')
  })

  it('marks a catalog with no overlap as invalid', () => {
    const coverage = getObservationCoverage({
      rain: { start: '2025-01-01T00:00', end: '2025-01-02T00:00' },
      river: { start: '2024-01-01T00:00', end: '2024-12-31T23:00' }
    })
    assert.equal(coverage.valid, false)
  })
})

describe('模型输入资料选择', () => {
  const projectData = {
    datasets: {
      datasets: [
        { id: 'XF-OBS-RAIN-HOURLY', type: 'observed-precipitation-hourly', role: 'observation' },
        { id: 'XF-OBS-RIVER-HOURLY', type: 'observed-river-hourly', role: 'observation' },
        { id: 'XF-OBS-RAIN-GRID-1KM-NC', type: 'observed-precipitation-grid-hourly', role: 'external-model-input' },
        { id: 'XF-OBS-RAIN-GRID-0P1DEG-NC', type: 'observed-precipitation-grid-hourly', role: 'external-model-input' },
        { id: 'XF-OBS-OUTLET-XLSX', type: 'observed-outlet-hydrology-hourly', role: 'external-model-input' }
      ]
    }
  }

  it('selects the grid matching the active model scale', () => {
    assert.equal(selectObservedPrecipitationDataset(projectData, '1km').id, 'XF-OBS-RAIN-GRID-1KM-NC')
    assert.equal(selectObservedPrecipitationDataset(projectData, '0p1deg').id, 'XF-OBS-RAIN-GRID-0P1DEG-NC')
  })

  it('selects the outlet hydrology package instead of station rainfall', () => {
    assert.equal(selectObservedHydrologyDataset(projectData).id, 'XF-OBS-OUTLET-XLSX')
  })
})
