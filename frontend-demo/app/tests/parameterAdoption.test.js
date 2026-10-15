import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('manual trial is blocked until a saved reference parameter set has been completely read, without changing other pages', async () => {
  const source = readFileSync(new URL('../src/views/CalibrationView.vue', import.meta.url), 'utf8')
  const selectionAction = source.slice(source.indexOf('async function applyReference('), source.indexOf('function openSaveParameters('))
  const eligibility = source.slice(source.indexOf('const canRun ='), source.indexOf('const calculationBlockReason ='))
  let finishSelection
  const verification = new Promise(resolve => { finishSelection = resolve })
  const parameterLoading = { value: false }, referenceParameterId = { value: '' }, calculationError = { value: '' }
  const app = { backendCase: { parameters: { SM: 29 } }, selectedRoutingVersionId: 'ROUTING-display' }
  const cal = { modelSchemeId: 'SCHEME-current', running: false, calibrationMode: 'event',
    params: { SM: 29 }, baseline: { SM: 29 }, paramVersion: 'PARAM-default',
    schemeRoutingVersionId: 'ROUTING-current', validateCalibration: () => ({ errors: [] }),
    detachOriginalPlan() {}, markNeedsRun() {}, saveCurrentWorkspace() {} }
  const empty = { value: '' }
  const actions = new Function('computed', 'app', 'cal', 'parameterLoading', 'parameterConstraint', 'physicalConflict',
    'physicalGrouping', 'trialMode', 'searchError', 'routingSearchError', 'objectiveWeightValid', 'routingError',
    'referenceParameterId', 'calculationError', 'modelApi', 'originError',
    selectionAction + '\n' + eligibility + '\nreturn { applyReference, canRun }')(
    fn => ({ get value() { return fn() } }), app, cal, parameterLoading, empty, empty,
    { value: { errors: [] } }, { value: 'manual' }, empty, empty, { value: true }, empty,
    referenceParameterId, calculationError, async () => { await verification; return {
      id: 'PARAMSET-reference', parameters: { SM: 41 }, routing: { versionId: 'ROUTING-reference' },
      science: { versionId: 'SCIENCE-reference' }, warmupDays: 7, statePolicy: 'independent-warmup'
    } }, empty)
  assert.equal(actions.canRun.value, true)
  const adopting = actions.applyReference('PARAMSET-reference')
  assert.equal(actions.canRun.value, false)
  assert.deepEqual(cal.params, { SM: 29 })
  assert.equal(cal.schemeRoutingVersionId, 'ROUTING-current')
  finishSelection()
  await adopting
  assert.equal(actions.canRun.value, true)
  assert.deepEqual(cal.params, { SM: 41 })
  assert.equal(cal.schemeRoutingVersionId, 'ROUTING-reference')
  assert.equal(cal.scienceVersionId, 'SCIENCE-reference')
  assert.equal(cal.requiredInputBundleVersionId, null)
  assert.equal(app.selectedRoutingVersionId, 'ROUTING-display')
})
