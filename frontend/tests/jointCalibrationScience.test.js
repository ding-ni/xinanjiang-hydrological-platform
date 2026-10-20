import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { buildCalibrationRequest, physicalFloodGroups, stratifiedRandomEventSplit } from '../src/services/calibrationSubmissionService.js'
import { routingSearchCoordinates, selectedSearchParameters } from '../src/services/calibrationSpaceService.js'
import { normalizeBackendTask } from '../src/services/modelBackendService.js'
import { trialConditions } from '../src/services/manualCalibrationService.js'
import { registerCalibrationResult, parameterLibraryFor } from '../src/services/parameterLibraryService.js'
import { resolveDomainContext } from '../src/data/domainContext.js'

const context = resolveDomainContext('nananxi-xufan', { modelScale: '1km' })
const app = { basinId: context.basinId, modelScale: context.modelScale, modelConfigId: context.configId,
  basinName: '南安溪', selectedRoutingVersionId: 'ROUTING-start', projectData: { backend: {} } }
const events = [
  { id: 'A', start: '2024-01-01T00:00', end: '2024-01-01T03:00', observedPeak: 20, purpose: '率定' },
  { id: 'B', start: '2024-01-01T03:00', end: '2024-01-01T06:00', observedPeak: 30, purpose: '率定' },
  { id: 'C', start: '2024-01-01T05:00', end: '2024-01-01T08:00', observedPeak: 22, purpose: '率定' },
  { id: 'D', start: '2024-01-02T00:00', end: '2024-01-02T04:00', observedPeak: 10, purpose: '验证' }
]
const calibration = { contextKey: context.contextKey, calibrationMode: 'event', calibrationTarget: 'joint',
  jointProfile: 'reference-16', statePolicy: 'continuous-origin', warmupDays: 7,
  params: { KC: .89, C: .37, SM: 20, KI: .02, KG: .01 }, criteria: {},
  searchParameters: { KC: { minimum: .4, maximum: 1.5 }, SM: { minimum: 8, maximum: 60 } },
  routingSearchParameters: { RS_k_hours: { minimum: .05, maximum: 3 }, muskingum_x: { minimum: .05, maximum: .45 } },
  searchStrategy: 'staged', maxEvaluations: 40, populationSize: 8 }

test('transitive shared-hour overlaps form one physical group with unique hours', () => {
  const saved = structuredClone(events)
  const physical = physicalFloodGroups(events)
  assert.equal(physical.groupCount, 2)
  assert.deepEqual(physical.groups[0].memberEventIds, ['A', 'B', 'C'])
  assert.equal(physical.uniqueHours, 14)
  assert.equal(physical.groups[0].uniqueHours, 9)
  assert.deepEqual(physical.counts, { calibration: 1, validation: 1, independentTest: 0 })
  assert.deepEqual(events, saved)
})

test('random split allocates complete physical groups and is seed reproducible', () => {
  const extra = Array.from({ length: 12 }, (_, i) => ({ id: `G${i}`, start: `2024-02-${String(i+1).padStart(2, '0')}T00:00`,
    end: `2024-02-${String(i+1).padStart(2, '0')}T01:00`, observedPeak: i+1, purpose: '未使用' }))
  const source = [...events, ...extra]
  const assigned = stratifiedRandomEventSplit(source, '42')
  assert.deepEqual(assigned, stratifiedRandomEventSplit(source, '42'))
  assert.equal(new Set(assigned.filter(row => ['A', 'B', 'C'].includes(row.id)).map(row => row.purpose)).size, 1)
  assert.equal(assigned.length, source.length)
  assert.equal(physicalFloodGroups(source.map(row => ({ ...row, purpose: assigned.find(a => a.id === row.id).purpose }))).conflicts.length, 0)
})

test('manual conflicting purposes block submission without silently rewriting assignments', () => {
  const conflicting = structuredClone(events); conflicting[2].purpose = '验证'
  const original = structuredClone(conflicting)
  assert.equal(physicalFloodGroups(conflicting).conflicts.length, 1)
  assert.throws(() => buildCalibrationRequest({ app, calibration, operationMode: 'calibration', events: conflicting }), /重叠洪水不能跨用途/)
  assert.deepEqual(conflicting, original)
})

test('joint submission freezes both search groups, scientific version, state policy and budget', () => {
  const settings = structuredClone(calibration)
  settings.scienceVersionId = 'SCIENCE-registered'
  settings.scienceDescriptor = { scienceVersionId: settings.scienceVersionId,
    modelImplementationSHA256: 'model-hash', scienceConfigurationSHA256: 'science-hash', petConfigurationSHA256: 'pet-hash' }
  const request = buildCalibrationRequest({ app, calibration: settings, operationMode: 'calibration', events })
  assert.equal(request.calibration.target, 'joint')
  assert.equal(request.calibration.jointProfile, 'reference-16')
  assert.equal(request.calibration.optimizer.strategy, 'joint')
  assert.equal(request.calibration.statePolicy, 'independent-warmup')
  assert.equal(request.calibration.continuousOrigin, undefined)
  assert.equal(request.calibration.optimizer.maxEvaluations, 40)
  assert.equal(request.science.versionId, 'SCIENCE-registered')
  assert.equal(request.inputs.evaporation.mode, 'science-version')
  assert.equal(request.lineage.modelImplementationSHA256, 'model-hash')
  assert.equal(request.period.eventSet.physicalGroupPreview.uniqueHours, 14)
  settings.routingSearchParameters.RS_k_hours.maximum = 12
  assert.equal(request.calibration.routingSearchParameters.RS_k_hours.maximum, 3)
  const recovered = normalizeBackendTask({ taskId: 'JOB-joint', runConfig: request }).runConfig
  assert.equal(recovered.jointProfile, 'reference-16')
  assert.equal(recovered.statePolicy, 'independent-warmup')
  assert.equal(recovered.scienceVersionId, 'SCIENCE-registered')
  assert.deepEqual(recovered.routingSearchParameters, request.calibration.routingSearchParameters)
})

test('heterogeneous Muskingum X stays undefined and cannot be invented by default selection', () => {
  const payload = { default_slope_k_hours: { RS: 2, RI: 8, RG: 24 }, reach_muskingum: { A: { x: .1 }, B: { x: .3 } } }
  assert.equal(routingSearchCoordinates(payload).muskingum_x, null)
  assert.equal(routingSearchCoordinates(payload).muskingum_k_scale, 1)
  const definitions = [{ key: 'muskingum_k_scale', minimum: .5, maximum: 2 },
    { key: 'muskingum_x', minimum: .05, maximum: .45, unavailable: 'heterogeneous' }]
  assert.deepEqual(Object.keys(selectedSearchParameters(definitions)), ['muskingum_k_scale'])
})

test('trial comparability isolates three scientific hashes, science ID and actual routing candidate', () => {
  const request = buildCalibrationRequest({ app, calibration, operationMode: 'calibration', events })
  const result = { science: { versionId: 'SCIENCE-v1' }, statePolicy: 'continuous-origin', petMode: 'MONTHLY',
    lineage: { modelImplementationSHA256: 'm', scienceConfigurationSHA256: 's', petConfigurationSHA256: 'p' },
    routingCandidate: { routingVersionId: 'ROUTING-candidate' }, bestRoutingParameters: { x: .2 } }
  const key = trialConditions(request, result)
  for (const mutate of [r => r.science.versionId = 'v2', r => r.lineage.modelImplementationSHA256 = 'm2',
    r => r.lineage.scienceConfigurationSHA256 = 's2', r => r.lineage.petConfigurationSHA256 = 'p2',
    r => r.statePolicy = 'independent-warmup', r => r.petMode = 'ZERO', r => r.routingCandidate.routingVersionId = 'r2',
    r => r.bestRoutingParameters.x = .3]) {
    const next = structuredClone(result); mutate(next)
    assert.notEqual(trialConditions(request, next), key)
  }
})

test('candidate library retains the complete runoff-routing-science selection', () => {
  const saved = globalThis.localStorage, values = new Map()
  globalThis.localStorage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }
  try {
    const request = buildCalibrationRequest({ app, calibration, operationMode: 'calibration', events })
    const result = { taskId: 'JOB-joint-library', resultSource: 'model-backend', context: request.context,
      parameterVersionId: 'PARAM-joint', bestParameters: calibration.params, calibrationTarget: 'joint',
      routingCandidate: { routingVersionId: 'ROUTING-best' }, science: { versionId: 'SCIENCE-v1' },
      scienceSnapshot: { manifest: { scienceVersionId: 'SCIENCE-v1', statePolicy: { strategy: 'continuous-origin' } } },
      lineage: { scienceConfigurationSHA256: 'hash' }, statePolicy: 'continuous-origin', petMode: 'MONTHLY' }
    const version = registerCalibrationResult({ projectId: app.basinId, resolutionId: app.modelScale, request, result })
    assert.equal(version.routingCandidate.routingVersionId, 'ROUTING-best')
    assert.equal(version.science.versionId, 'SCIENCE-v1')
    assert.equal(version.statePolicy, 'continuous-origin')
    assert.equal(version.calibrationTarget, 'joint')
    assert.equal(parameterLibraryFor({}, app.basinId, app.modelScale).simulationRateLibrary.versions[0].lineage.scienceConfigurationSHA256, 'hash')
  } finally { globalThis.localStorage = saved }
})

test('candidate store action adopts routing and science alongside runoff', async () => {
  const source = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const action = source.slice(source.indexOf('    async loadVersion(version) {'), source.indexOf('    setSection(section) {'))
    .trim().replace(/,$/, '')
  const candidate = { id: 'PARAM-joint', parameters: { SM: 25 }, calibrationTarget: 'joint',
    routingCandidate: { routingVersionId: 'ROUTING-best' }, science: { versionId: 'SCIENCE-best' },
    statePolicy: 'continuous-origin', petMode: 'MONTHLY', lineage: { scienceConfigurationSHA256: 'hash' } }
  const selected = []
  const actions = new Function('useAppStore', 'parameterLibraryFor', 'clonePlain', 'VERSION_PRESETS', 'DEFAULT_PARAMS',
    `return { ${action} };`)(() => ({ updateWorkContext: row => selected.push(row) }),
    () => ({ simulationRateLibrary: { versions: [candidate] } }), structuredClone, {}, {})
  const state = { backendContractVersion: '2.0', markNeedsRun() {}, saveCurrentWorkspace() {} }
  await actions.loadVersion.call(state, candidate.id)
  assert.deepEqual(state.params, { SM: 25 })
  assert.deepEqual(selected, [{ selectedRoutingVersionId: 'ROUTING-best' }])
  assert.equal(state.scienceVersionId, 'SCIENCE-best')
  assert.equal(state.statePolicy, 'continuous-origin')
  assert.equal(state.calibrationTarget, 'joint')
  assert.equal(state.warmupDays, 7)
})

test('continuous origin and input requirements survive continuous requests and task recovery', () => {
  const settings = { ...calibration, calibrationMode: 'continuous', continuousOrigin: '2020-12-25T00:00:00', requiredInputBundleVersionId: 'INPUT-17-MDB',
    simulationStart: '2024-04-01T00:00', simulationEnd: '2024-04-02T00:00' }
  const selectedApp = { ...app, selectedInputBundleVersionId: 'INPUT-17-MDB', usesModelBackend: true }
  const request = buildCalibrationRequest({ app: selectedApp, calibration: settings, operationMode: 'calibration', events })
  assert.equal(request.calibration.continuousOrigin, '2020-12-25T00:00:00')
  assert.equal(request.inputs.bundleVersionId, 'INPUT-17-MDB')
  const recovered = normalizeBackendTask({ taskId: 'JOB-origin', runConfig: request }).runConfig
  assert.equal(recovered.continuousOrigin, '2020-12-25T00:00:00')
  assert.equal(recovered.requiredInputBundleVersionId, 'INPUT-17-MDB')
  const simulation = buildCalibrationRequest({ app: selectedApp, calibration: settings, operationMode: 'simulation' })
  assert.equal(simulation.period.continuousOrigin, '2020-12-25T00:00:00')
  assert.equal(normalizeBackendTask({ taskId: 'JOB-origin-sim', runConfig: simulation }).runConfig.statePolicy, 'continuous-origin')
  const independent = buildCalibrationRequest({ app: selectedApp, calibration: { ...settings, statePolicy: 'independent-warmup' }, operationMode: 'calibration', events })
  assert.equal(independent.calibration.continuousOrigin, undefined)
  assert.throws(() => buildCalibrationRequest({ app: { ...selectedApp, selectedInputBundleVersionId: 'old-226' }, calibration: settings,
    operationMode: 'simulation' }), /要求资料 INPUT-17-MDB/)
})

test('candidate loading verifies required registered input before changing any parameter or routing', async () => {
  const source = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const action = source.slice(source.indexOf('    async loadVersion(version) {'), source.indexOf('    setSection(section) {'))
    .trim().replace(/,$/, '')
  const candidate = { id: 'PARAM-origin', parameters: { SM: 25 }, calibrationTarget: 'joint',
    routingCandidate: { routingVersionId: 'ROUTING-best' }, science: { versionId: 'SCIENCE-best' },
    statePolicy: 'continuous-origin', continuousOrigin: '2020-12-25T00:00:00', requiredInputBundleVersionId: 'INPUT-17-MDB',
    scienceDescriptor: { statePolicy: { strategy: 'continuous-origin', warmupHours: 240 } } }
  const calls = []
  let fail = true
  const mockApp = { async selectInputBundleVersion(value) { calls.push(['verify-input', value]); if (fail) throw Error('未登记') },
    updateWorkContext(value) { calls.push(['routing', value]) } }
  const actions = new Function('useAppStore', 'parameterLibraryFor', 'clonePlain', 'VERSION_PRESETS', 'DEFAULT_PARAMS',
    `return { ${action} };`)(() => mockApp, () => ({ simulationRateLibrary: { versions: [candidate] } }), structuredClone, {}, {})
  const state = { contextKey: 'same-workspace', backendContractVersion: '2.0', params: { SM: 15 }, paramVersion: 'PARAM-before',
    markNeedsRun() {}, saveCurrentWorkspace() {} }
  assert.equal(await actions.loadVersion.call(state, candidate.id), false)
  assert.deepEqual(state.params, { SM: 15 })
  assert.equal(state.paramVersion, 'PARAM-before')
  assert.equal(calls.filter(row => row[0] === 'routing').length, 0)
  assert.match(state.runMessage, /要求资料 INPUT-17-MDB/)
  fail = false
  assert.equal(await actions.loadVersion.call(state, candidate.id), true)
  assert.deepEqual(state.params, { SM: 25 })
  assert.equal(state.requiredInputBundleVersionId, 'INPUT-17-MDB')
  assert.equal(state.continuousOrigin, '2020-12-25T00:00:00')
  assert.equal(state.scienceDescriptor.resolvedContinuousOrigin, '2020-12-25T00:00:00')
  assert.equal(state.warmupDays, 10)
  assert.deepEqual(calls.at(-1), ['routing', { selectedRoutingVersionId: 'ROUTING-best' }])
})

test('registered science selection adopts actual warmup and guards subsequent nominal edits', () => {
  const source = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const select = source.slice(source.indexOf('    setScienceVersion(descriptor) {'), source.indexOf('    setScienceField(key, value) {'))
    .trim().replace(/,$/, '')
  const warmup = source.slice(source.indexOf('    setWarmupDays(value) {'), source.indexOf('    setObservationRange(range = {}) {'))
    .trim().replace(/,$/, '')
  // The selected scientific warmup is checked by the real action, not inferred from a label.
  const action = new Function('clonePlain', `return { ${select}, ${warmup} };`)(structuredClone)
  const state = { warmupDays: 90, markNeedsRun() {}, saveCurrentWorkspace() {}, detachOriginalPlan() {} }
  action.setScienceVersion.call(state, { scienceVersionId: 'SCIENCE-actual', statePolicy: { strategy: 'continuous-origin', warmupHours: 360 } })
  assert.equal(state.warmupDays, 15)
  assert.equal(state.statePolicy, 'continuous-origin')
  action.setWarmupDays.call(state, 90)
  assert.equal(state.warmupDays, 15)
  action.setScienceVersion.call(state, null)
  action.setWarmupDays.call(state, 90)
  assert.equal(state.warmupDays, 90)
})

test('candidate actual warmup survives scientific registry refresh without changing hashed preset policy', async () => {
  const source = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const load = source.slice(source.indexOf('    async loadVersion(version) {'), source.indexOf('    setSection(section) {')).trim().replace(/,$/, '')
  const select = source.slice(source.indexOf('    setScienceVersion(descriptor) {'), source.indexOf('    setScienceField(key, value) {')).trim().replace(/,$/, '')
  const registry = { scienceVersionId: 'SCIENCE-same', statePolicy: { strategy: 'independent-warmup', warmupHours: 168 }, scienceConfigurationSHA256: 'preset-hash' }
  const candidate = { id: 'PARAM-1day', parameters: calibration.params, actualWarmupHours: 24,
    science: { versionId: 'SCIENCE-same' }, scienceDescriptor: registry, statePolicy: 'independent-warmup' }
  const actions = new Function('useAppStore', 'parameterLibraryFor', 'clonePlain', 'VERSION_PRESETS', 'DEFAULT_PARAMS',
    `return { ${load}, ${select} };`)(() => ({ updateWorkContext() {} }),
    () => ({ simulationRateLibrary: { versions: [candidate] } }), structuredClone, {}, {})
  const state = { backendContractVersion: '2.0', markNeedsRun() {}, saveCurrentWorkspace() {} }
  assert.equal(await actions.loadVersion.call(state, candidate.id), true)
  assert.equal(state.warmupDays, 1)
  assert.equal(state.candidateWarmupHours, 24)
  actions.setScienceVersion.call(state, registry)
  assert.equal(state.warmupDays, 1)
  assert.equal(state.scienceDescriptor.statePolicy.warmupHours, 168)
  assert.equal(state.scienceDescriptor.scienceConfigurationSHA256, 'preset-hash')
  const request = buildCalibrationRequest({ app, calibration: { ...calibration, ...state }, operationMode: 'calibration', events })
  assert.equal(request.period.warmupDays, 1)
  const recovered = normalizeBackendTask({ taskId: 'JOB-1day', runConfig: request }).runConfig
  assert.equal(recovered.actualWarmupHours, 24)
})
