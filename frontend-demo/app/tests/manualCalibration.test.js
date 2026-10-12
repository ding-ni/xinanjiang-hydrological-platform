import { test } from 'node:test'
import assert from 'node:assert/strict'
import { trialConditions, changedParameters, alignedTrialSeries } from '../src/services/manualCalibrationService.js'
import { catchupWindow, compatibleCatchupState } from '../src/services/stateCatchupService.js'

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

test('state catchup covers the missing hours, never reruns the checkpoint hour', () => {
  assert.deepEqual(catchupWindow('2017-06-19 23:00', '2017-06-20T02:00'), { hours: 3, ready: true, reason: '', endTime: '2017-06-20T02:00' })
  assert.equal(catchupWindow('2017-06-20 02:00', '2017-06-20T02:00').ready, false)
  assert.equal(catchupWindow('2017-06-20 03:00', '2017-06-20T02:00').ready, false)
  assert.equal(catchupWindow('2017-06-20 01:30', '2017-06-20T02:00').ready, false)
  assert.equal(catchupWindow('2017-06-20 01:00', '2019-06-20T02:00').ready, false)
})

test('catchup source must be a model checkpoint with matching input and routing', () => {
  const node = { resultSource: 'model-backend', inputBundleVersionId: 'v1', checkpoint: { routingVersionId: 'r1' } }
  assert.equal(compatibleCatchupState(node, 'v1', 'r1'), true)
  assert.equal(compatibleCatchupState(node, 'v2', 'r1'), false)
  assert.equal(compatibleCatchupState(node, 'v1', 'r2'), false)
  assert.equal(compatibleCatchupState({ ...node, resultSource: 'fixture' }, 'v1', 'r1'), false)
})
