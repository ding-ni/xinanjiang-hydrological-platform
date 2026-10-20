import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { normalizeBackendTask } from '../src/services/modelBackendService.js'
import { buildCalibrationRequest } from '../src/services/calibrationSubmissionService.js'

const context = { contextKey: 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km', projectId: 'nananxi-xufan', resolutionId: '1km', modelConfigId: 'nananxi-xufan-1km' }
const events = [
  { id: 'A', purpose: '率定', start: '2021-08-11T00:00:00', end: '2021-08-12T00:00:00', status: 'active' },
  { id: 'B', purpose: '验证', start: '2022-03-17T00:00:00', end: '2022-03-18T00:00:00', status: 'active' },
  { id: 'C', purpose: '独立检验', start: '2024-06-25T00:00:00', end: '2024-06-26T00:00:00', status: 'active' }
]
const source = { context, runType: 'calibration', inputs: { bundleVersionId: 'INPUT-sourceQC', evaporation: { mode: 'science-version' } },
  routing: { versionId: 'ROUTING-source-baseline' }, science: { versionId: 'SCIENCE-source' },
  sourcePlan: { parameterVersionId: 'PARAM-source-best', planId: 'SOURCE-PLAN-original', planSHA256: '1'.repeat(64), operation: 'search' },
  period: { mode: 'event', events, warmupDays: 7, eventSet: { selectedEventIds: ['A', 'B', 'C'] },
    eventAssignment: { method: 'RANDOM_OVERLAP_GROUP_SPLIT', seed: 42, ratios: { calibration: .667, validation: .201 } } },
  calibration: { target: 'joint', jointProfile: 'reference-16', statePolicy: 'continuous-origin', continuousOrigin: '2020-12-25T00:00:00',
    initialParameters: { KC: .89, C: .37, SM: 18, KI: .02, KG: .03 },
    searchParameters: { KC: { minimum: .4, maximum: 1.5 }, SM: { minimum: 8, maximum: 60 } },
    routingSearchParameters: { RS_k_hours: { minimum: .05, maximum: 3 } },
    optimizer: { algorithm: 'DIFFERENTIAL_EVOLUTION', strategy: 'joint', maxEvaluations: 960, populationSize: 48, seed: 20260820,
      differentialWeight: .8, crossoverProbability: .7, generations: 19, initializationPolicy: 'SOURCE_INITIAL_CANDIDATES',
      initialCandidates: [], effectiveInitialCandidates: [{ source: 'CURRENT_ACTIVE_PARAMETERS', parameters: { 'runoff.KC': .89, 'runoff.C': .37, 'runoff.SM': 18, 'routing.RS_k_hours': 2 } }] },
    searchParameterOrder: ['runoff.KC', 'runoff.C', 'runoff.SM', 'routing.RS_k_hours'],
    objectives: { metrics: { nse: true, kge: true, volume: true, peak: true, time: true },
      weights: { nse: .3, kge: .2, volume: .2, peak: .2, time: .1 }, waterBalanceMetric: 'PBIAS' } }, lineage: {} }
const plan = { sourceTaskId: 'CAL-source', sourceSplitSeed: 42, sourceSearchSeed: 20260820, actualWarmupHours: 168 }

function probe({ rejectInput = false } = {}) {
  const text = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const restore = text.slice(text.indexOf('    async restoreOriginalPlan('), text.indexOf('    setSection(')).trim().replace(/,$/, '')
  const load = text.slice(text.indexOf('    loadFromTaskConfig('), text.indexOf('    markNeedsRun(')).trim().replace(/,$/, '')
  const calls = []
  const app = { usesModelBackend: true, basinId: context.projectId, modelScale: context.resolutionId, modelConfigId: context.modelConfigId, projectData: { backend: {} },
    async selectInputBundleVersion(value) { calls.push(['input', value]); if (rejectInput) throw Error('资料未登记'); this.selectedInputBundleVersionId = value },
    updateWorkContext(value) { calls.push(['routing', value]); this.selectedRoutingVersionId = value.selectedRoutingVersionId } }
  const actions = new Function('useAppStore', 'modelApi', 'normalizeBackendTask', 'clonePlain', 'normalizedCriteria', 'DEFAULT_PARAMS', 'SECTION_MAP', 'normalizeFloodEvent',
    `return { ${restore}, ${load} }`)(() => app, async () => ({ plan: structuredClone(plan), request: structuredClone(source) }), normalizeBackendTask,
    structuredClone, value => ({ tolerances: { peakPercent: 10, peakTimeHours: 2 }, ...structuredClone(value) }), {}, {}, row => row)
  const workspace = { contextKey: context.contextKey, backendContractVersion: '2.0', params: { SM: 29 },
    events: [...structuredClone(events), { id: 'OLD', purpose: '率定' }], markNeedsRun(value) { calls.push(['prepared', value]) },
    saveCurrentWorkspace() {}, loadFromTaskConfig: actions.loadFromTaskConfig, setEvent() {} }
  return { app, workspace, calls, restore: () => actions.restoreOriginalPlan.call(workspace, 'PARAM-source-best') }
}

test('original settings restore source baseline, split, scientific conditions and optimizer without submitting a job', async () => {
  const p = probe()
  assert.equal(await p.restore(), true)
  assert.deepEqual(p.workspace.params, source.calibration.initialParameters)
  assert.equal(p.workspace.params.SM, 18)
  assert.equal(p.workspace.maxEvaluations, 960)
  assert.equal(p.workspace.populationSize, 48)
  assert.equal(p.workspace.searchSeed, 20260820)
  assert.equal(p.workspace.eventSplitSeed, 42)
  assert.equal(p.workspace.scienceVersionId, 'SCIENCE-source')
  assert.equal(p.workspace.continuousOrigin, '2020-12-25T00:00:00')
  assert.equal(p.workspace.warmupDays, 7)
  assert.deepEqual(p.workspace.criteria.weights, source.calibration.objectives.weights)
  assert.deepEqual(p.workspace.events.map(row => row.purpose), ['率定', '验证', '独立检验', '未使用'])
  assert.deepEqual(p.calls.map(row => row[0]), ['input', 'routing', 'prepared'])
  assert.equal(p.workspace.running, false)
  assert.equal(p.workspace.activeTaskId, null)
})

test('restored original settings round-trip KGE/PBIAS, DE settings, original variable order and plan provenance', async () => {
  const p = probe(); await p.restore()
  const request = buildCalibrationRequest({ app: p.app, calibration: p.workspace, operationMode: 'calibration', events: p.workspace.events })
  assert.deepEqual(request.sourcePlan, source.sourcePlan)
  assert.deepEqual(request.context, source.context)
  assert.deepEqual(request.inputs.evaporation, source.inputs.evaporation)
  assert.deepEqual(request.calibration.objectives, source.calibration.objectives)
  assert.deepEqual(request.calibration.optimizer, source.calibration.optimizer)
  assert.deepEqual(request.calibration.searchParameterOrder, source.calibration.searchParameterOrder)
  assert.deepEqual(request.calibration.searchParameters, source.calibration.searchParameters)
  assert.deepEqual(request.calibration.routingSearchParameters, source.calibration.routingSearchParameters)
  assert.equal(request.calibration.objectives.metrics.kge, true)
  assert.equal(request.calibration.objectives.weights.kge, .2)
  assert.equal(request.calibration.objectives.waterBalanceMetric, 'PBIAS')
  assert.deepEqual(request.period.eventAssignment, source.period.eventAssignment)
  assert.equal(request.routing.versionId, 'ROUTING-source-baseline')
  p.workspace.optimizerSettings.initialCandidates.push({ SM: 20 })
  assert.deepEqual(request.calibration.optimizer.initialCandidates, [])
})

test('an unavailable original input prevents restoring parameter and routing settings', async () => {
  const p = probe({ rejectInput: true })
  assert.equal(await p.restore(), false)
  assert.deepEqual(p.workspace.params, { SM: 29 })
  assert.deepEqual(p.calls.map(row => row[0]), ['input'])
  assert.match(p.workspace.runMessage, /未恢复原方案/)
})

test('changing the original objective removes its source identity and keeps the edited value', () => {
  const sourceText = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const detach = sourceText.slice(sourceText.indexOf('    detachOriginalPlan('), sourceText.indexOf('    setParam(')).trim().replace(/,$/, '')
  const objective = sourceText.slice(sourceText.indexOf('    setObjectiveWeight('), sourceText.indexOf('    setObjectiveTolerance(')).trim().replace(/,$/, '')
  const actions = new Function(`return { ${detach}, ${objective} }`)()
  const workspace = { sourcePlan: structuredClone(source.sourcePlan), optimizerSettings: { differentialWeight: .8 },
    searchParameterOrder: ['runoff.KC'], eventAssignment: { seed: 42 }, criteria: { weights: { kge: .2 } },
    detachOriginalPlan: actions.detachOriginalPlan, markNeedsRun() {} }
  actions.setObjectiveWeight.call(workspace, 'kge', .3)
  assert.equal(workspace.sourcePlan, null)
  assert.equal(workspace.originalPlanChanged, true)
  assert.equal(workspace.criteria.weights.kge, .3)
  assert.deepEqual(workspace.searchParameterOrder, ['runoff.KC'])
  assert.deepEqual(workspace.optimizerSettings, { differentialWeight: .8 })
})
