import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { scientificRequestSelection, stateScienceBlockReason } from '../src/services/scientificSelectionService.js'
import { buildForecastRequest } from '../src/services/forecastSubmissionService.js'

const checkpoint = { scienceVersionId: 'SCIENCE-parent', modelImplementationSHA256: '1'.repeat(64),
  scienceConfigurationSHA256: '2'.repeat(64), petConfigurationSHA256: '3'.repeat(64), routingVersionId: 'routing-1' }
const parent = { id: 'STATE-JOB-parent', resultSource: 'model-backend', time: '2024-01-01T00:00:00',
  sourceTaskId: 'JOB-parent', parameters: { SM: 21 }, parameterVersionId: 'PARAM-parent', inputBundleVersionId: 'bundle-1', checkpoint }
const selected = { contextKey: 'same', backendContractVersion: '2.0', scienceVersionId: 'SCIENCE-selected',
  statePolicy: 'continuous-origin', continuousOrigin: '2020-12-25T00:00:00', warmupDays: 7, params: { SM: 29 } }

test('continuation follows parent science and never substitutes the selected calibration configuration', () => {
  assert.deepEqual(scientificRequestSelection({ parentState: parent, selected }), {
    science: { versionId: 'SCIENCE-parent' }, evaporation: { mode: 'science-version' }
  })
  for (const key of ['scienceVersionId', 'modelImplementationSHA256', 'scienceConfigurationSHA256', 'petConfigurationSHA256']) {
    const old = structuredClone(parent); delete old.checkpoint[key]
    assert.match(stateScienceBlockReason(old), /重新生成初始状态/)
    assert.throws(() => scientificRequestSelection({ parentState: old, selected }), /重新生成初始状态/)
  }
  assert.match(stateScienceBlockReason(parent, '0'.repeat(64)), /升级前模型/)
  assert.throws(() => scientificRequestSelection({ parentState: parent, modelImplementationSHA256: '0'.repeat(64) }), /重新初始化/)
  assert.equal(stateScienceBlockReason({...parent,implementationReplayable:true},'0'.repeat(64)), '')
  assert.equal(scientificRequestSelection({parentState:{...parent,implementationReplayable:true},modelImplementationSHA256:'0'.repeat(64)}).science.versionId,'SCIENCE-parent')
})

test('continuous candidate initialization inherits its scientific version while an explicit independent mode uses independent conditions', () => {
  assert.equal(scientificRequestSelection({ selected, initializationStrategy: 'CONTINUOUS' }).science.versionId, selected.scienceVersionId)
  assert.deepEqual(scientificRequestSelection({ selected, initializationStrategy: 'FIXED_WARMUP' }), { evaporation: { mode: 'monthly-climatology-assumption' } })
  assert.equal(scientificRequestSelection({ selected: { ...selected, statePolicy: 'independent-warmup' }, initializationStrategy: 'FIXED_WARMUP' }).science.versionId, selected.scienceVersionId)
})

function stateAction(calibration = selected) {
  const source = readFileSync(new URL('../src/stores/state.js', import.meta.url), 'utf8')
  const action = source.slice(source.indexOf('    async runBackendStateTask('), source.indexOf('    setDomain(')).trim().replace(/,$/, '')
  const requests = []
  const app = { inputSelectionKey: 'input-1', selectedRoutingVersionId: 'routing-1', selectedInputBundleVersionId: 'bundle-1',
    backendCase: { parameters: { SM: 18 }, parameterVersionId: 'PARAM-default' }, setStateVersion() {} }
  const tasks = { async submitTask(type, request) { requests.push(request); return { taskId: 'JOB-new' } },
    async waitForTask() { return { status: 'completed' } }, async getResult() { return { stateVersionId: 'STATE-JOB-new' } } }
  const actions = new Function('useAppStore', 'useCalibrationStore', 'selectedInputBundleVersionId', 'frozenInputBundleVersionId',
    'stateScienceBlockReason', 'scientificRequestSelection', 'uniqueRequestId', 'useTasksStore', 'modelApi', `return { ${action} }`)(
    () => app, () => calibration, a => a.selectedInputBundleVersionId, state => state.inputBundleVersionId || parent.inputBundleVersionId,
    stateScienceBlockReason, scientificRequestSelection, () => 'STATE-REQ-test', () => tasks, async () => ({ states: [] }))
  const state = { contextKey: 'same', activeInitialState: parent, activeDomainId: 'nananxi-xufan', activeScale: '1km',
    activeModelConfigId: 'nananxi-xufan-1km', hydrateBackendStates() {}, loadInitialState() { return { id: 'STATE-JOB-new' } } }
  return { requests, run: (mode, options = {}) => actions.runBackendStateTask.call(state, mode, '2024-01-01T02:00:00', options) }
}

test('actual state store request preserves parent science through continuation and pre-forecast catchup', async () => {
  const probe = stateAction()
  assert.equal((await probe.run('continuation')).ok, true)
  const request = probe.requests[0]
  assert.equal(request.science.versionId, 'SCIENCE-parent')
  assert.equal(request.inputs.evaporation.mode, 'science-version')
  assert.deepEqual(request.parameters, parent.parameters)
  assert.equal(request.period.startTime, '2024-01-01T01:00')
  assert.equal(request.period.initialStateVersionId, parent.id)
  assert.equal(request.period.continuousOrigin, undefined)
})

test('continuation accepts the initialization selector retained by the actual state page and uses its parent', async () => {
  const probe = stateAction({ ...selected, continuousOrigin: null })
  assert.equal((await probe.run('continuation', { strategy: 'CONTINUOUS' })).ok, true)
  const request = probe.requests[0]
  assert.equal(request.science.versionId, 'SCIENCE-parent')
  assert.deepEqual(request.parameters, parent.parameters)
  assert.equal(request.period.initialStateVersionId, parent.id)
  assert.equal(request.period.continuousOrigin, undefined)
  assert.equal(request.initialization, undefined)
})

test('actual state initialization request carries original continuous origin and selected scientific conditions', async () => {
  const probe = stateAction()
  assert.equal((await probe.run('initialization', { strategy: 'CONTINUOUS' })).ok, true)
  const request = probe.requests[0]
  assert.equal(request.science.versionId, selected.scienceVersionId)
  assert.equal(request.initialization.strategy, 'CONTINUOUS')
  assert.equal(request.period.continuousOrigin, selected.continuousOrigin)
  assert.deepEqual(request.parameters, selected.params)
  assert.equal(request.period.initialStateVersionId, null)
})

test('forecast builder carries parent scientific conditions and rejects an old state before submission', () => {
  const input = { app: { usesModelBackend: true, basinId: 'nananxi-xufan', selectedRoutingVersionId: 'routing-1', selectedInputBundleVersionId: 'bundle-1' },
    forecast: { startTime: parent.time, leadHours: 2, stateVersion: parent.id, effectiveParams: parent.parameters }, parentState: parent }
  const request = buildForecastRequest(input)
  assert.equal(request.science.versionId, 'SCIENCE-parent')
  assert.equal(request.inputs.evaporation.mode, 'science-version')
  assert.throws(() => buildForecastRequest({ ...input, parentState: { ...parent, checkpoint: {} } }), /重新生成初始状态/)
})
