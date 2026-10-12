import test from 'node:test'
import assert from 'node:assert/strict'
import { buildForecastRequest, forecastCommit, parseHourlyRainfall } from '../src/services/forecastSubmissionService.js'
import { resultPeriods, hydrographCsv } from '../src/services/resultPresentation.js'

const contextKey = 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km'
function fixture() {
  const request = buildForecastRequest({
    app: { usesModelBackend: true, basinId: 'nananxi-xufan', modelScale: '1km', selectedRoutingVersionId: 'routing-1', selectedInputBundleVersionId: 'bundle-1' },
    forecast: { contextKey, startTime: '2017-06-19 11:00', leadHours: 6, forecastMode: 'manual', targetSectionId: '70115800',
      modelConfigId: 'nananxi-xufan-1km', stateVersion: 'STATE-JOB-parent', effectiveParams: { SM: 20 }, paramVersion: 'parameter-1' },
    rainProduct: { versionId: 'rain-1', manifestSHA256: 'digest-1' }
  })
  const result = { taskId: 'JOB-forecast', runType: 'forecast', resultSource: 'model-backend', resultVersionId: 'result-1',
    context: { ...request.context }, contextKey, issue: { ...request.issue }, parentStateVersionId: request.period.initialStateVersionId,
    lineage: { inputBundleVersionId: 'bundle-1', forecastRainVersionId: 'rain-1', forecastRainManifestSHA256: 'digest-1', routingVersionId: 'routing-1', parameterVersionId: 'parameter-1' },
    hydrograph: { forecast: [1, 2, 3, 4, 5, 6].map((value, index) => ({ time: `2017-06-19T${12 + index}:00:00`, value })),
      peak: 6, peakTime: '2017-06-19T17:00:00', volume: 21 * 3600, obs: [],
      forcing: { precipitation: [0, 1, 5, 10, 2, 0], versionId: 'rain-1' } }
  }
  return { request, result }
}

test('rainfall accepts hourly decimals and Chinese separators', () => {
  assert.deepEqual(parseHourlyRainfall('0，1 5;10；2\n.5', 6), [0, 1, 5, 10, 2, 0.5])
})

test('rainfall rejects missing hours, negative/nonfinite/exponential values', () => {
  for (const text of ['', '1 2', '-1', 'NaN', 'Infinity', '1e2', '0xff', '1001', 'true']) {
    assert.throws(() => parseHourlyRainfall(text, 1))
  }
})

test('forecast freezes issue/state/rain/bundle and starts next hour', () => {
  const { request } = fixture()
  assert.equal(request.schemaVersion, '2.0')
  assert.equal(request.period.startTime, '2017-06-19T12:00:00')
  assert.equal(request.period.endTime, '2017-06-19T17:00:00')
  assert.equal(request.period.initialStateVersionId, 'STATE-JOB-parent')
  assert.equal(request.inputs.bundleVersionId, 'bundle-1')
  assert.equal(request.inputs.evaporation.mode, 'monthly-climatology-assumption')
  assert.equal(request.inputs.forecastPrecipitation.manifestSHA256, 'digest-1')
  assert.notEqual(request.requestId, fixture().request.requestId)
})

test('commit preserves original numerical process and frozen request', () => {
  const { request, result } = fixture()
  const commit = forecastCommit(result, request, result.taskId)
  assert.equal(commit.hydrograph, result.hydrograph)
  assert.equal(commit.request, request)
  assert.equal(commit.summary.volume, 75600)
  assert.equal(commit.outputs[0].sectionId, '70115800')
})

test('forecast exports use forecast terminology and preserve all hourly values', () => {
  const { result } = fixture()
  assert.equal(resultPeriods(result)[0].id, 'FORECAST')
  assert.equal(resultPeriods(result)[0].label, '预见期')
  const csv = hydrographCsv(result.hydrograph, [], 'forecast')
  assert.match(csv, /预报流量/)
  assert.doesNotMatch(csv, /实测流量|模拟流量/)
  assert.equal(csv.split('\r\n').length, 7)
  assert.match(csv, /2017-06-19T15:00:00",10,4/)
})

test('commit refuses stale context, issue, rainfall, routing, state and parameters', () => {
  for (const change of [r => r.taskId = 'other', r => r.context.resolutionId = 'other', r => r.contextKey = 'other',
    r => r.issue.issueTime = '2017-06-19T12:00:00', r => r.issue.leadHours = 12,
    r => r.lineage.forecastRainManifestSHA256 = 'other', r => r.lineage.forecastRainVersionId = 'other',
    r => r.lineage.inputBundleVersionId = 'other', r => r.lineage.routingVersionId = 'other',
    r => r.lineage.parameterVersionId = 'other', r => r.parentStateVersionId = 'other']) {
    const { request, result } = fixture()
    change(result)
    assert.throws(() => forecastCommit(result, request, 'JOB-forecast'))
  }
})

test('commit rejects incomplete process or inconsistent peak/volume', () => {
  for (const change of [r => r.hydrograph.forecast.pop(), r => r.hydrograph.forecast[0].time = r.issue.issueTime,
    r => r.hydrograph.forecast[0].value = NaN, r => r.hydrograph.forecast[0].value = -1,
    r => r.hydrograph.forcing.precipitation = [], r => r.hydrograph.forcing.versionId = 'other',
    r => r.hydrograph.peak = 99, r => r.hydrograph.volume = 99, r => r.hydrograph.peakTime = r.issue.issueTime]) {
    const { request, result } = fixture()
    change(result)
    assert.throws(() => forecastCommit(result, request, result.taskId))
  }
})
