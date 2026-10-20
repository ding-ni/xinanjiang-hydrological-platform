import { test } from 'node:test'
import assert from 'node:assert/strict'
import { trialConditions, changedParameters, alignedTrialSeries, latestComparableTrial, resultProcessMatches, calibrationSummaryMetrics } from '../src/services/manualCalibrationService.js'
import { catchupWindow, compatibleCatchupState } from '../src/services/stateCatchupService.js'
import { buildCalibrationRequest } from '../src/services/calibrationSubmissionService.js'

test('saving a fixed event simulation draft keeps fixed parameters and cannot restore an optimizer run', () => {
  const draft = buildCalibrationRequest({ app: { basinId: 'nananxi-xufan', modelScale: '1km', projectData: { backend: {} } },
    calibration: { calibrationMode: 'event', fixedEventSimulation: true, calibrationTarget: 'joint', params: { SM: 20 }, criteria: {},
      searchParameters: { SM: { minimum: 10, maximum: 30 } }, routingSearchParameters: { RS_k_hours: { minimum: 1, maximum: 2 } } },
    operationMode: 'calibration', events: [] })
  assert.equal(draft.calibration.simulationOnly, true)
  assert.equal(draft.calibration.evaluationMode, 'manual')
  assert.deepEqual(draft.calibration.initialParameters, { SM: 20 })
  assert.equal(draft.calibration.searchParameters, undefined)
  assert.equal(draft.calibration.routingSearchParameters, undefined)
  assert.equal(draft.calibration.target, undefined)
})

const request = { context: { contextKey: 'basin:1km' }, inputs: { bundleVersionId: 'input-v1', evaporation: { mode: 'constant-zero' } },
  routing: { versionId: 'routing-v1' }, period: { mode: 'event', warmupDays: 7, events: [{ id: 'A', start: '2017-06-01', end: '2017-06-02', purpose: '率定', revision: 1 }] },
  calibration: { initialParameters: { SM: 20 }, objectives: { metrics: { nse: true } } } }

test('trial comparison ignores changed parameters but isolates forcing, routing, windows and warmup', () => {
  const next = structuredClone(request)
  next.calibration.initialParameters.SM = 30
  next.calibration.evaluationMode = 'manual'
  next.requestId = 'next-job'
  assert.equal(trialConditions(request), trialConditions(next))
  for (const mutate of [r => r.period.warmupDays++, r => r.inputs.bundleVersionId = 'v2', r => r.routing.versionId = 'v2', r => r.period.events[0].end = '2017-06-03']) {
    const other = structuredClone(next); mutate(other)
    assert.notEqual(trialConditions(request), trialConditions(other))
  }
  assert.equal(changedParameters({ SM: 20, KC: 1 }, { SM: 30, KC: 1 }), 'SM 20 → 30')
})

test('trial lines use exact timestamps and retain missing hours as gaps', () => {
  assert.deepEqual(alignedTrialSeries({ forecast: [{ time: 'a', value: 0 }, { time: 'c', value: 5 }] }, ['a', 'b', 'c']), [0, null, 5])
})

test('four successful trials only return the immediately previous matching process, and a foreign flood returns no overlay', () => {
  const result = taskId => ({ taskId, frozenRequest: structuredClone(request), hydrographs: { A: { forecast: [{ time: 'a', value: 5 }] } } })
  const records = ['one', 'two', 'three', 'four'].map(taskId => ({ taskId, conditions: trialConditions(request) }))
  assert.equal(latestComparableTrial(records, result('four'), 'A').taskId, 'three')
  assert.equal(latestComparableTrial(records, result('two'), 'A').taskId, 'one')
  assert.equal(latestComparableTrial(records, result('one'), 'A'), null)
  assert.equal(latestComparableTrial(records, result('four'), 'B'), null)
  const other = result('four'); other.frozenRequest.inputs.bundleVersionId = 'another-source'
  assert.equal(latestComparableTrial(records, other, 'A'), null)
})

test('editing parameters keeps the successful flow, while changed flood endpoints, input or origin cannot display an unrelated process', () => {
  const editor = { calibrationMode: 'event', statePolicy: 'independent-warmup', warmupDays: 7,
    schemeSourceBundleVersionId: 'input-v1', scienceVersionId: null, evaporationMode: 'constant-zero',
    events: structuredClone(request.period.events), schemeWindows: [], params: { SM: 20 } }
  const result = { frozenRequest: structuredClone(request), hydrographs: { A: {} } }
  assert.equal(resultProcessMatches(editor, result, 'A'), true)
  editor.params.SM = 34
  assert.equal(resultProcessMatches(editor, result, 'A'), true)
  editor.events[0].end = '2017-06-03'
  assert.equal(resultProcessMatches(editor, result, 'A'), false)
  editor.events[0].end = request.period.events[0].end
  editor.schemeSourceBundleVersionId = 'another-source'
  assert.equal(resultProcessMatches(editor, result, 'A'), false)
  editor.schemeSourceBundleVersionId = 'input-v1'
  editor.statePolicy = 'continuous-origin'
  assert.equal(resultProcessMatches(editor, result, 'A'), false)
})

test('editing continuous evaluation dates hides the old process but leaves the result object intact', () => {
  const period = { mode: 'continuous', warmupDays: 7, calibrationStart: '2017-06-01T00:00', calibrationEnd: '2017-06-02T23:00', validationStart: '2017-06-03T00:00', validationEnd: '2017-06-04T23:00' }
  const result = { frozenRequest: { ...structuredClone(request), period,
    calibration: { ...request.calibration, statePolicy: 'continuous-origin', continuousOrigin: '2017-05-25T00:00' } } }
  const editor = { ...period, calibrationMode: 'continuous', statePolicy: 'continuous-origin', continuousOrigin: '2017-05-25T00:00', schemeSourceBundleVersionId: 'input-v1' }
  assert.equal(resultProcessMatches(editor, result, 'CALIBRATION'), true)
  editor.validationEnd = '2017-06-05T23:00'
  assert.equal(resultProcessMatches(editor, result, 'CALIBRATION'), false)
  assert.equal(result.frozenRequest.period.validationEnd, '2017-06-04T23:00')
})

test('summary presentation maps the actual grouped metrics and preserves zero and unavailable values', () => {
  const summary = { mean_NSE: .73, mean_abs_peak_error_percent: 12.6, median_peak_time_error_hours: 0,
    mean_abs_volume_error_percent: 19.1 }
  assert.deepEqual(calibrationSummaryMetrics(summary), { nse: .73, peak: 12.6, time: 0, volume: 19.1 })
  assert.deepEqual(calibrationSummaryMetrics({ mean_abs_peak_time_error_hours: 2 }), { nse: undefined, peak: undefined, time: 2, volume: undefined })
  assert.deepEqual(summary, { mean_NSE: .73, mean_abs_peak_error_percent: 12.6, median_peak_time_error_hours: 0, mean_abs_volume_error_percent: 19.1 })
})

test('state catchup covers the missing hours, never reruns the checkpoint hour', () => {
  assert.deepEqual(catchupWindow('2017-06-19 23:00', '2017-06-20T02:00'), { hours: 3, ready: true, reason: '', endTime: '2017-06-20T02:00' })
  assert.equal(catchupWindow('2017-06-20 02:00', '2017-06-20T02:00').ready, false)
  assert.equal(catchupWindow('2017-06-20 03:00', '2017-06-20T02:00').ready, false)
  assert.equal(catchupWindow('2017-06-20 01:30', '2017-06-20T02:00').ready, false)
  assert.equal(catchupWindow('2017-06-20 01:00', '2019-06-20T02:00').ready, false)
})

test('catchup source must be a model checkpoint with matching input and routing', () => {
  const node = { resultSource: 'model-backend', inputBundleVersionId: 'v1', checkpoint: { routingVersionId: 'r1',
    scienceVersionId: 'SCIENCE-parent', modelImplementationSHA256: '1'.repeat(64), scienceConfigurationSHA256: '2'.repeat(64), petConfigurationSHA256: '3'.repeat(64) } }
  assert.equal(compatibleCatchupState(node, 'v1', 'r1'), true)
  assert.equal(compatibleCatchupState(node, 'v2', 'r1'), false)
  assert.equal(compatibleCatchupState(node, 'v1', 'r2'), false)
  assert.equal(compatibleCatchupState({ ...node, resultSource: 'fixture' }, 'v1', 'r1'), false)
  assert.equal(compatibleCatchupState({ ...node, checkpoint: { routingVersionId: 'r1' } }, 'v1', 'r1'), false)
})
