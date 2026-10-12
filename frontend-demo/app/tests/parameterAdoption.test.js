import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('manual trial waits for complete asynchronous candidate adoption before freezing its routing', async () => {
  const source = readFileSync(new URL('../src/views/CalibrationView.vue', import.meta.url), 'utf8')
  const selectionAction = source.slice(source.indexOf('async function applyParameterVersion('), source.indexOf('async function restoreOriginalPlan('))
  const eligibility = source.slice(source.indexOf('const manualCanRun ='), source.indexOf('const calMode ='))
  const trialAction = source.slice(source.indexOf('async function startManualTrial()'), source.indexOf('async function prepareBackendTask('))
  let finishSelection
  const verification = new Promise(resolve => { finishSelection = resolve })
  const submissions = [], parameterLoading = { value: false }
  const app = { backendCase: { manualCalibration: true }, selectedRoutingVersionId: 'ROUTING-old' }
  const cal = { running: false, sourcePlan: null, calibrationMode: 'event', scienceVersionId: null,
    executionAvailability: { ready: true }, validateCalibration: () => ({ errors: [] }),
    async loadVersion() { await verification; app.selectedRoutingVersionId = 'ROUTING-candidate'; return true } }
  const empty = { value: '' }
  const actions = new Function('computed', 'app', 'cal', 'parameterLoading', 'routingReferenceWarning',
    'parameterConstraint', 'physicalConflict', 'scienceError', 'scienceLoading', 'scienceVersions', 'ElMessage',
    'prepareBackendTask', `${selectionAction}\n${eligibility}\n${trialAction}\nreturn { applyParameterVersion, startManualTrial, manualCanRun }`)(
    fn => ({ get value() { return fn() } }), app, cal, parameterLoading, empty, empty,
    { value: false }, empty, { value: false }, { value: [] }, { warning() {} },
    async () => submissions.push(app.selectedRoutingVersionId))
  assert.equal(actions.manualCanRun.value, true)
  const adopting = actions.applyParameterVersion('PARAM-candidate')
  assert.equal(actions.manualCanRun.value, false)
  await actions.startManualTrial()
  assert.deepEqual(submissions, [])
  finishSelection()
  await adopting
  assert.equal(actions.manualCanRun.value, true)
  await actions.startManualTrial()
  assert.deepEqual(submissions, ['ROUTING-candidate'])
})
