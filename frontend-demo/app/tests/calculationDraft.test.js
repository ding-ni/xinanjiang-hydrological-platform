import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { calibrationEditor, restoreCalibrationEditor, saveModelScheme } from '../src/services/modelSchemeService.js'
import { buildCalibrationRequest } from '../src/services/calibrationSubmissionService.js'
import { normalizeBackendTask } from '../src/services/modelBackendService.js'
import { cloneWorkspaceValue } from '../src/services/workspaceSnapshotService.js'

const contextKey = 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km'

function originFixture(overrides = {}) {
  const source = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end)).trim().replace(/,$/, '')
  const fieldsAndCopy = slice('const WORKSPACE_FIELDS = [', 'function applyWorkspace(')
  const save = slice('    saveCurrentWorkspace() {', '    setContext(')
  const staleAndDetach = slice('    markNeedsRun(', '    setParam(')
  const science = slice('    setScienceField(', '    setPeriod(')
  const selectEvent = slice('    setEvent(id, start = null, end = null) {', '    setEventPurpose(')
  const operation = slice('    setOperationMode(mode) {', '    setSimulationField(')
  const persisted = []
  const actions = new Function('clonePlain', 'backendWaits', 'persistWorkspaceRecords',
    `${fieldsAndCopy}\nreturn { ${save}, ${staleAndDetach}, ${science}, ${selectEvent}, ${operation} }`)(
    cloneWorkspaceValue, new Map(), workspaces => persisted.push(structuredClone(workspaces)))
  const state = {
    ...actions,
    contextKey, domainId: 'nananxi-xufan', modelScale: '1km', modelConfigId: 'nananxi-xufan-1km',
    operationMode: 'simulation', calibrationMode: 'continuous', statePolicy: 'continuous-origin',
    continuousOrigin: '2017-05-25T00:00', simulationStart: '2017-06-01T00:00', simulationEnd: '2017-06-30T23:00',
    calibrationStart: '2017-06-01T00:00', calibrationEnd: '2017-06-15T23:00',
    validationStart: '2017-06-16T00:00', validationEnd: '2017-06-30T23:00',
    params: { KC: .89, SM: 29 }, paramVersion: 'PARAM-current', warmupDays: 7,
    criteria: {}, algorithm: 'DIFFERENTIAL_EVOLUTION', maxEvaluations: 120, populationSize: 12,
    scienceVersionId: null, evaporationMode: 'monthly-climatology-assumption', sourcePlan: null,
    workspaces: {}, activeTaskId: null, running: false, dirty: false, runStatus: 'complete',
    runResult: { taskId: 'JOB-calibration', isCurrent: true },
    simulationResult: { taskId: 'JOB-simulation', isCurrent: true },
    ...overrides
  }
  const app = { basinId: state.domainId, basinName: '南安溪·徐畈', modelScale: '1km',
    modelConfigId: state.modelConfigId, projectData: { backend: {} }, usesModelBackend: true,
    selectedInputBundleVersionId: 'INPUT-source', selectedRoutingVersionId: 'ROUTING-current' }
  return { state, app, persisted }
}

test('editing a continuous origin saves the new start and leaves previous results tied to their own conditions', () => {
  const { state, persisted } = originFixture()
  const previousResult = state.simulationResult
  state.setScienceField('continuousOrigin', '2017-05-20T00:00')
  assert.equal(state.continuousOrigin, '2017-05-20T00:00')
  assert.equal(state.dirty, true)
  assert.equal(state.runStatus, 'needs-run')
  assert.equal(state.runResult.isCurrent, false)
  assert.equal(state.simulationResult.isCurrent, false)
  assert.equal(previousResult.isCurrent, true)
  assert.equal(persisted.length, 1)
  assert.equal(persisted[0][contextKey].continuousOrigin, '2017-05-20T00:00')
  assert.equal(persisted[0][contextKey].dirty, true)
  assert.equal(persisted[0][contextKey].simulationResult.isCurrent, false)
  assert.deepEqual(persisted[0][contextKey].params, { KC: .89, SM: 29 })
  state.continuousOrigin = '2017-05-18T00:00'
  assert.equal(persisted[0][contextKey].continuousOrigin, '2017-05-20T00:00')
})

test('a registered scientific configuration permits origin changes and preserves its evaporation and state policy', () => {
  const descriptor = { scienceVersionId: 'SCIENCE-current', statePolicy: { strategy: 'continuous-origin', warmupHours: 168 },
    resolvedContinuousOrigin: '2017-05-25T00:00', petConfigurationSHA256: 'preset-pet' }
  const { state, app, persisted } = originFixture({ scienceVersionId: descriptor.scienceVersionId,
    scienceDescriptor: structuredClone(descriptor), evaporationMode: 'science-version', candidateWarmupHours: 24 })
  state.setScienceField('continuousOrigin', '2017-05-19T00:00')
  assert.equal(state.scienceVersionId, descriptor.scienceVersionId)
  assert.deepEqual(state.scienceDescriptor, descriptor)
  assert.equal(state.evaporationMode, 'science-version')
  assert.equal(state.statePolicy, 'continuous-origin')
  assert.equal(state.candidateWarmupHours, 24)
  assert.equal(persisted[0][contextKey].continuousOrigin, '2017-05-19T00:00')
  for (const operationMode of ['simulation', 'calibration']) {
    const request = buildCalibrationRequest({ app, calibration: state, operationMode })
    const submittedOrigin = operationMode === 'simulation' ? request.period.continuousOrigin : request.calibration.continuousOrigin
    assert.equal(submittedOrigin, '2017-05-19T00:00')
    assert.equal(request.science.versionId, descriptor.scienceVersionId)
    assert.equal(request.inputs.evaporation.mode, 'science-version')
    const recovered = normalizeBackendTask({ runConfig: request }).runConfig
    assert.equal(recovered.continuousOrigin, '2017-05-19T00:00')
  }
})

test('registered scientific settings and an origin belonging to a running calculation cannot be changed', () => {
  const locked = originFixture({ scienceVersionId: 'SCIENCE-current', evaporationMode: 'science-version' })
  locked.state.setScienceField('statePolicy', 'independent-warmup')
  locked.state.setScienceField('evaporationMode', 'constant-zero')
  assert.equal(locked.state.statePolicy, 'continuous-origin')
  assert.equal(locked.state.evaporationMode, 'science-version')
  assert.equal(locked.state.dirty, false)
  assert.equal(locked.persisted.length, 0)
  const running = originFixture({ running: true })
  running.state.setScienceField('continuousOrigin', '2017-05-01T00:00')
  assert.equal(running.state.continuousOrigin, '2017-05-25T00:00')
  assert.equal(running.state.running, true)
  assert.equal(running.persisted.length, 0)
})

test('changing the original continuous origin prepares a fresh request without carrying the original search identity', () => {
  const { state, persisted } = originFixture({ sourcePlan: { planId: 'original', operation: 'search' },
    sourcePlanRequestTemplate: { original: true }, searchParameterOrder: ['runoff.SM'],
    optimizerSettings: { generations: 19 }, eventAssignment: { seed: 42 } })
  state.setScienceField('continuousOrigin', '2017-05-19T00:00')
  assert.equal(state.sourcePlan, null)
  assert.equal(state.sourcePlanRequestTemplate, null)
  assert.equal(state.originalPlanChanged, true)
  assert.equal(state.searchParameterOrder, null)
  assert.deepEqual(state.optimizerSettings, {})
  assert.equal(persisted[0][contextKey].sourcePlan, null)
  assert.equal(persisted[0][contextKey].continuousOrigin, '2017-05-19T00:00')
})

test('selecting a new scheme flood switches and saves its process without changing the original flood catalogue', () => {
  const oldEvent = { id: 'OLD', purpose: '率定', status: 'active', runtimeRegistered: true,
    start: '2017-06-01T00:00', end: '2017-06-02T23:00' }
  const newEvent = { id: 'NEW', purpose: '验证', status: 'active', runtimeRegistered: false,
    sourceBundleVersionId: 'INPUT-source', start: '2017-06-20T00:00', end: '2017-06-21T23:00' }
  const { state, persisted } = originFixture({
    operationMode: 'calibration', calibrationMode: 'event', events: [structuredClone(oldEvent)],
    schemeWindows: [structuredClone(newEvent)], schemeEvents: [structuredClone(oldEvent), structuredClone(newEvent)],
    eventId: oldEvent.id, eventStart: oldEvent.start, eventEnd: oldEvent.end
  })
  const oldCatalogue = structuredClone(state.events)
  const priorResult = state.runResult
  state.setEvent(newEvent.id)
  assert.equal(state.eventId, newEvent.id)
  assert.equal(state.eventStart, newEvent.start)
  assert.equal(state.eventEnd, newEvent.end)
  assert.equal(state.dirty, true)
  assert.equal(state.runStatus, 'needs-run')
  assert.equal(state.runResult.isCurrent, false)
  assert.equal(priorResult.isCurrent, true)
  assert.deepEqual(state.events, oldCatalogue)
  assert.deepEqual(state.schemeWindows, [newEvent])
  assert.ok(persisted.length > 0)
  const saved = persisted.at(-1)[contextKey]
  assert.equal(saved.eventId, newEvent.id)
  assert.equal(saved.eventStart, newEvent.start)
  assert.equal(saved.eventEnd, newEvent.end)
  assert.equal(saved.runResult.isCurrent, false)
  assert.deepEqual(saved.events, oldCatalogue)
  assert.deepEqual(saved.schemeWindows, [newEvent])
})

test('switching between simulation and calibration saves changed conditions without retaining a completed result or adding guidance', () => {
  const { state, persisted } = originFixture({ runMessage: '连续模拟完成' })
  state.setOperationMode('calibration')
  assert.equal(state.operationMode, 'calibration')
  assert.equal(state.dirty, true)
  assert.equal(state.runStatus, 'needs-run')
  assert.equal(state.runResult.isCurrent, false)
  assert.equal(state.simulationResult.isCurrent, false)
  assert.equal(state.runMessage, '')
  assert.equal(persisted.length, 1)
  const saved = persisted[0][contextKey]
  assert.equal(saved.operationMode, 'calibration')
  assert.equal(saved.dirty, true)
  assert.equal(saved.runStatus, 'needs-run')
  assert.equal(saved.runResult.isCurrent, false)
  assert.equal(saved.simulationResult.isCurrent, false)
  assert.equal(saved.runMessage, '')
  state.setOperationMode('calibration')
  assert.equal(persisted.length, 1)
})

test('workspace preparation defaults apply to new schemes without changing an opened original scheme', async () => {
  const { state, persisted } = originFixture({
    sourcePlan: { planId: 'source-original', operation: 'search' },
    sourcePlanRequestTemplate: { inputs: { bundleVersionId: 'INPUT-original' } },
    optimizerSettings: { initializationPolicy: 'SOURCE_INITIAL_CANDIDATES', generations: 19 },
    searchParameterOrder: ['runoff.SM'], eventAssignment: { seed: 42 },
    rainfallMethod: 'stationIdw', stationSourceId: 'LOCAL_OBSERVATIONS'
  })
  const source = readFileSync(new URL('../src/views/ConfigView.vue', import.meta.url), 'utf8')
  const action = source.slice(source.indexOf('async function savePreparationDefaults()'), source.indexOf('const tabs ='))
  const before = structuredClone({ sourcePlan: state.sourcePlan, optimizerSettings: state.optimizerSettings, params: state.params })
  const defaults = { value: { idwPower: 3, minimumStations: 2 } }
  const api = async (path, options) => {
    assert.equal(path, 'database/preparation-defaults')
    assert.deepEqual(options.body, defaults.value)
    return options.body
  }
  const save = new Function('calibration', 'preparationDefaults', 'modelApi', 'ElMessage', `${action}\nreturn savePreparationDefaults`)(state, defaults, api, { success() {}, error(message) { throw new Error(message) } })
  await save()
  assert.deepEqual({ sourcePlan: state.sourcePlan, optimizerSettings: state.optimizerSettings, params: state.params }, before)
  assert.equal(persisted.length, 0)
})

test('a named new scheme uses the configured local source and clears selections without altering the current display source', () => {
  const source = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const action = source.slice(source.indexOf('    resetCalibrationScheme('), source.indexOf('    async runOperationRequest(')).trim().replace(/,$/, '')
  const app = { selectedInputBundleVersionId: 'INPUT-display', selectedRoutingVersionId: 'ROUTING-existing' }
  const defaults = { rainfallMethod: 'stationIdw', stationSourceId: 'LOCAL_OBSERVATIONS',
    sourceBundleVersionId: 'INPUT-local-base', idwPower: 3, minimumStations: 2 }
  const saved = []
  const state = {
    ...new Function('useAppStore', 'criteriaTemplate', 'return { ' + action + ' }')(() => app, () => ({})),
    events: [{ id: 'OLD', purpose: '率定' }, { id: 'OTHER', purpose: '验证' }],
    baseline: { SM: 29 }, params: { SM: 45 }, modelSchemeId: 'SCHEME-old',
    runResult: { taskId: 'JOB-history' }, trialRecords: [{ taskId: 'JOB-history' }],
    saveCurrentWorkspace() { saved.push(structuredClone({ events: this.events, source: this.schemeSourceBundleVersionId })) }
  }
  const oldResult = structuredClone(state.runResult)
  state.resetCalibrationScheme(defaults, { parameters: { SM: 29 }, parameterVersionId: 'PARAM-default',
    defaultWarmupDays: 7, algorithms: [{ id: 'DE' }], defaultMaxEvaluations: 20 })
  assert.equal(state.modelSchemeId, null)
  assert.ok(state.events.every(row => row.purpose === '未使用'))
  assert.deepEqual(state.params, { SM: 29 })
  assert.equal(state.warmupDays, 7)
  assert.equal(state.schemeSourceBundleVersionId, 'INPUT-local-base')
  assert.equal(state.stationSourceId, 'LOCAL_OBSERVATIONS')
  assert.equal(state.idwPower, 3)
  assert.equal(state.minimumStations, 2)
  assert.equal(app.selectedInputBundleVersionId, 'INPUT-display')
  assert.deepEqual(oldResult, { taskId: 'JOB-history' })
  assert.equal(saved.length, 1)
})

test('a calculation cannot create an anonymous scheme', async () => {
  await assert.rejects(saveModelScheme({ modelSchemeName: '' }, {}), /方案名称/)
})

test('an incomplete named editor restores choices and periods independently of calculated conditions', () => {
  const source = {
    contextKey, domainId: 'nananxi-xufan', modelScale: '1km', modelConfigId: 'nananxi-xufan-1km',
    events: [{ id: 'A', start: '2024-06-20T00:00', end: '2024-06-21T00:00', purpose: '率定', status: 'active' }],
    eventId: 'A', calibrationMode: 'continuous', validationStart: '', validationEnd: '',
    params: { SM: 29 }, baseline: { SM: 29 }, schemeSourceBundleVersionId: 'INPUT-fixed',
    schemeRoutingVersionId: 'ROUTING-fixed', statePolicy: 'continuous-origin', activeOperationId: null
  }
  const editor = calibrationEditor(source)
  source.params.SM = 40
  source.events[0].purpose = '验证'
  const restored = { events: [{ id: 'A', purpose: '未使用' }, { id: 'B', purpose: '率定' }],
    runResult: { taskId: 'JOB-old' }, simulationResult: {}, trialRecords: [{}], running: false }
  restoreCalibrationEditor(restored, editor)
  assert.deepEqual(restored.params, { SM: 29 })
  assert.equal(restored.events[0].purpose, '率定')
  assert.equal(restored.events[1].purpose, '未使用')
  assert.equal(restored.validationStart, '')
  assert.equal(restored.schemeSourceBundleVersionId, 'INPUT-fixed')
  assert.equal(restored.schemeRoutingVersionId, 'ROUTING-fixed')
  assert.equal(restored.runResult, null)
  assert.equal(restored.running, false)
  assert.equal(restored.requiredInputBundleVersionId, null)
})
