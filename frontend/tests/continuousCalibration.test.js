import { it } from 'node:test'
import assert from 'node:assert/strict'
import { buildCalibrationRequest } from '../src/services/calibrationSubmissionService.js'
import { normalizeBackendTask } from '../src/services/modelBackendService.js'

function setup() {
  return {
    app: { basinId: 'nananxi-xufan', modelScale: '1km', modelConfigId: 'nananxi-xufan-1km', projectData: { backend: {} } },
    calibration: { contextKey: 'CASE', calibrationMode: 'continuous', params: { KC: 0.89 }, warmupDays: 7,
      calibrationStart: '2017-06-01T00:00', calibrationEnd: '2017-06-15T23:00',
      validationStart: '2017-06-16T00:00', validationEnd: '2017-06-30T23:00',
      independentStart: '2017-07-01T00:00', independentEnd: '2017-07-15T23:00',
      algorithm: 'DIFFERENTIAL_EVOLUTION', maxEvaluations: 120, populationSize: 12,
      searchStrategy: 'staged', searchSeed: 0, criteria: {}, eventSetVersion: 'OLD-EVENTS' },
    operationMode: 'calibration', events: [{ id: 'old', purpose: '率定' }]
  }
}

it('continuous calibration freezes periods and search, excluding unrelated event selections', () => {
  const input = setup()
  const request = buildCalibrationRequest(input)
  assert.equal(request.runType, 'calibration')
  assert.equal(request.period.mode, 'continuous')
  assert.equal(request.period.independentStart, input.calibration.independentStart)
  assert.equal(request.inputs.evaporation.mode, 'monthly-climatology-assumption')
  assert.equal(request.period.events, undefined)
  assert.equal(request.period.eventSet, undefined)
  assert.equal(request.lineage.eventSetVersionId, null)
  assert.equal(request.calibration.optimizer.strategy, 'staged')
  assert.equal(request.calibration.optimizer.seed, 0)
  input.calibration.searchSeed = 123
  assert.equal(request.calibration.optimizer.seed, 0)
})

it('continuous task recovery preserves independent windows and stage settings', () => {
  const request = buildCalibrationRequest(setup())
  const task = normalizeBackendTask({ taskId: 'JOB-continuous', runType: 'continuousCalibration', runConfig: request })
  assert.equal(task.runType, 'continuousCalibration')
  assert.equal(task.runConfig.period.independentEnd, request.period.independentEnd)
  assert.equal(task.runConfig.searchStrategy, 'staged')
  assert.equal(task.runConfig.searchSeed, 0)
})

it('event calibration keeps its existing frozen group and zero evaporation contract', () => {
  const input = setup()
  input.calibration.calibrationMode = 'event'
  delete input.calibration.searchStrategy
  delete input.calibration.searchSeed
  const request = buildCalibrationRequest(input)
  assert.equal(request.period.events.length, 1)
  assert.equal(request.period.eventSet.version, 'OLD-EVENTS')
  assert.equal(request.inputs.evaporation.mode, 'constant-zero')
  assert.equal(request.calibration.optimizer.strategy, 'joint')
  assert.equal(request.calibration.optimizer.seed, 42)
})
