import test from 'node:test'
import assert from 'node:assert/strict'
import { isEditableDiagnosticSource } from '../src/services/diagnosticSourceService.js'

test('ordinary automatic schemes can vary diagnostic seeds while original seeded plans remain unchanged and unavailable', () => {
  const normal = { taskId: 'JOB-normal', runConfig: { runType: 'calibration', calibration: { target: 'joint', evaluationMode: 'automatic', optimizer: { seed: 42 } } } }
  const source = structuredClone(normal)
  source.taskId = 'JOB-source'
  source.runConfig.sourcePlan = { planId: 'SOURCE-original', operation: 'search' }
  source.runConfig.calibration.optimizer.initializationPolicy = 'SOURCE_INITIAL_CANDIDATES'
  const original = structuredClone(source)
  assert.equal(isEditableDiagnosticSource(normal), true)
  assert.equal(isEditableDiagnosticSource(source), false)
  const partial = structuredClone(source); delete partial.runConfig.sourcePlan
  assert.equal(isEditableDiagnosticSource(partial), false)
  const onlyMarker = structuredClone(source); delete onlyMarker.runConfig.calibration.optimizer.initializationPolicy
  assert.equal(isEditableDiagnosticSource(onlyMarker), false)
  assert.deepEqual(source, original)
  assert.deepEqual([source, normal].filter(isEditableDiagnosticSource).map(row => row.taskId), ['JOB-normal'])
})

test('manual, routing-only and noncalibration tasks cannot become seed-search sources', () => {
  for (const request of [{ runType: 'simulation' }, { runType: 'calibration', calibration: { evaluationMode: 'manual' } },
    { runType: 'calibration', calibration: { target: 'routing' } }]) {
    assert.equal(isEditableDiagnosticSource({ runConfig: request }), false)
  }
})
