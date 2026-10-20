import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { buildCalibrationRequest } from '../src/services/calibrationSubmissionService.js'

const store = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
const definitions = store.slice(store.indexOf('function criteriaTemplate()'), store.indexOf('const EVENT_OVERRIDE_STORAGE'))
const { criteriaTemplate, normalizedCriteria } = new Function('clonePlain', `${definitions}\nreturn { criteriaTemplate, normalizedCriteria }`)(structuredClone)

test('new default includes water balance with a valid four-term weighted objective', () => {
  const criteria = criteriaTemplate()
  assert.deepEqual(Object.keys(criteria.metrics).filter(key => criteria.metrics[key]).sort(), ['nse', 'peak', 'time', 'volume'])
  assert.deepEqual(Object.fromEntries(Object.entries(criteria.weights).filter(([key]) => criteria.metrics[key])),
    { nse: .4, peak: .2, time: .1, volume: .3 })
  assert.equal(criteria.waterBalanceMetric, 'VOLUME_ERROR')
  assert.ok(Math.abs(Object.entries(criteria.weights).reduce((sum, [key, weight]) => sum + (criteria.metrics[key] ? weight : 0), 0) - 1) < 1e-12)
  criteria.weights.volume = .8
  assert.equal(criteriaTemplate().weights.volume, .3)
})

test('restoring sparse historical objectives does not silently enable water balance', () => {
  const saved = { metrics: { nse: true, peak: true, time: true },
    weights: { nse: .5, peak: .25, time: .25 }, tolerances: { peakPercent: 15 } }
  const before = structuredClone(saved)
  const restored = normalizedCriteria(saved)
  assert.equal(restored.metrics.volume, false)
  assert.equal(restored.metrics.kge, false)
  assert.deepEqual(Object.fromEntries(Object.entries(restored.weights).filter(([key]) => restored.metrics[key])), saved.weights)
  assert.equal(restored.tolerances.peakPercent, 15)
  assert.deepEqual(saved, before)
  const custom = { metrics: { kge: true, volume: true }, weights: { kge: .7, volume: .3 }, waterBalanceMetric: 'PBIAS' }
  const normalized = normalizedCriteria(custom)
  assert.equal(normalized.metrics.nse, false)
  assert.equal(normalized.waterBalanceMetric, 'PBIAS')
  assert.equal(normalized.weights.kge, .7)
})

test('default objectives reach event and continuous submissions unchanged', () => {
  for (const mode of ['event', 'continuous']) {
    const criteria = criteriaTemplate()
    const request = buildCalibrationRequest({
      app: { basinId: 'nananxi-xufan', modelScale: '1km', modelConfigId: 'nananxi-xufan-1km', projectData: { backend: {} } },
      calibration: { contextKey: 'CASE', calibrationMode: mode, criteria, params: { SM: 29 }, warmupDays: 14,
        algorithm: 'DIFFERENTIAL_EVOLUTION', maxEvaluations: 120, populationSize: 12 },
      operationMode: 'calibration', events: []
    })
    assert.deepEqual(request.calibration.objectives, criteria)
  }
})

test('explicit default adoption marks the draft for recalculation and leaves completed evidence intact', () => {
  const action = store.slice(store.indexOf('    resetObjectiveCriteria()'), store.indexOf('    setObjectiveMetric(')).trim().replace(/,$/, '')
  const actions = new Function('criteriaTemplate', `return { ${action} }`)(criteriaTemplate)
  const completed = { taskId: 'JOB-old', request: { calibration: { objectives: { metrics: { nse: true }, weights: { nse: 1 } } } } }
  const evidence = structuredClone(completed)
  let detached = 0, saved = 0
  const state = { ...actions, criteria: { metrics: { nse: true }, weights: { nse: 1 } }, runResult: completed,
    detachOriginalPlan() { detached++ }, markNeedsRun(message) { this.dirty = true; this.runMessage = message },
    saveCurrentWorkspace() { saved++ } }
  state.resetObjectiveCriteria()
  assert.deepEqual(state.criteria, criteriaTemplate())
  assert.equal(state.dirty, true)
  assert.equal(detached, 1)
  assert.equal(saved, 1)
  assert.deepEqual(completed, evidence)
  state.running = true
  state.criteria.weights.nse = .6
  state.resetObjectiveCriteria()
  assert.equal(state.criteria.weights.nse, .6)
  assert.equal(saved, 1)
})
