import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { originalCalibrationRecords, openOriginalCalibrationRecord, recoverActiveCalibrationOperation, calibrationEditor, restoreCalibrationEditor, partitionSchemeWindows } from '../src/services/modelSchemeService.js'
import { normalizeBackendTask } from '../src/services/modelBackendService.js'

const context = { contextKey: 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km', projectId: 'nananxi-xufan', resolutionId: '1km', modelConfigId: 'nananxi-xufan-1km' }
const plan = { planId: 'SOURCE-PLAN-' + 'a'.repeat(64), planSHA256: 'a'.repeat(64), sourceAcceptedEventIds: ['A', 'B'], sourceExcludedEventIds: ['C'], sourceSplitSeed: 42 }
const record = { parameterVersionId: 'PARAM-original', context, name: '原率定记录', candidateKind: 'frozen-calibration', originalCalibrationPlan: plan }
const events = [
  { id: 'A', purpose: '率定', start: '2021-08-11T00:00:00', end: '2021-08-12T00:00:00', status: 'active', runtimeRegistered: true },
  { id: 'B', purpose: '验证', start: '2022-03-17T00:00:00', end: '2022-03-18T00:00:00', status: 'active', runtimeRegistered: true },
  { id: 'C', purpose: '未使用', start: '2024-06-25T00:00:00', end: '2024-06-26T00:00:00', status: 'active', runtimeRegistered: true }
]
const source = { context, runType: 'calibration', inputs: { bundleVersionId: 'INPUT-original', evaporation: { mode: 'science-version' } },
  routing: { versionId: 'ROUTING-original' }, science: { versionId: 'SCIENCE-original' },
  sourcePlan: { parameterVersionId: record.parameterVersionId, planId: plan.planId, planSHA256: plan.planSHA256, operation: 'search' },
  period: { mode: 'event', warmupDays: 7, events: events.slice(0, 2) },
  calibration: { initialParameters: { SM: 18 }, target: 'joint', statePolicy: 'continuous-origin', continuousOrigin: '2020-12-25T00:00:00',
    optimizer: { algorithm: 'DIFFERENTIAL_EVOLUTION', seed: 20260820, maxEvaluations: 960, populationSize: 48 },
    objectives: { metrics: { nse: true }, weights: { nse: 1 } } } }

test('same flood number with an old boundary never replaces the public directory boundary', () => {
  const old={...events[0],start:'2021-08-01T00:00:00'}
  const result=partitionSchemeWindows(events,[old,{...events[1],start:events[1].start.slice(0,16)}])
  assert.equal(result.catalogue.length,3)
  assert.equal(result.catalogue[0].start,events[0].start)
  assert.equal(result.catalogue[0].purpose,'未使用')
  assert.equal(result.windows.length,1)
  assert.equal(result.windows[0].start,old.start)
  assert.equal(result.windows[0].catalogueBoundaryOverride,true)
})

test('only verified-context source plans appear, with repeated parameter records merged into one original plan entry', () => {
  const manual = { ...record, candidateKind: 'manual-edit', parameterVersionId: 'PARAM-manual' }
  const foreign = { ...record, context: { contextKey: 'another-basin' } }
  assert.deepEqual(originalCalibrationRecords([manual, record, foreign, { ...record, originalCalibrationPlan: null }], context.contextKey), [record])
  assert.deepEqual(originalCalibrationRecords([], context.contextKey), [])
})

test('opening an original record preserves complete catalogue, exact accepted uses and conditions without changing active display input or executing', async () => {
  const store = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const load = store.slice(store.indexOf('    loadFromTaskConfig('), store.indexOf('    markNeedsRun(')).trim().replace(/,$/, '')
  const actions = new Function('clonePlain', 'normalizedCriteria', 'DEFAULT_PARAMS', 'SECTION_MAP', 'normalizeFloodEvent',
    'return { ' + load + ' }')(structuredClone, value => structuredClone(value), {}, {}, row => row)
  const cal = { ...actions, contextKey: context.contextKey, domainId: context.projectId, modelScale: '1km', modelConfigId: context.modelConfigId,
    backendContractVersion: '2.0', events: structuredClone(events), schemeWindows: [], idwPower: 2, minimumStations: 1,
    saveCurrentWorkspace() {}, resetCalibrationScheme() {}, setEvent() {}, runResult: { taskId: 'JOB-old' } }
  const app = { contextKey: context.contextKey, selectedInputBundleVersionId: 'INPUT-display', selectedRoutingVersionId: 'ROUTING-display' }
  const display = structuredClone(app)
  const saved = new Map(), calls = []
  const api = async (path, options = {}) => {
    calls.push([path, options.method || 'GET'])
    if (path.includes('/original-plan?')) return { plan: structuredClone(plan), request: structuredClone(source) }
    if (path === 'model-schemes' && !options.body) return { schemes: [...saved.values()].map(row => ({ id: row.id, name: row.name })) }
    if (path === 'model-schemes' && options.body) {
      const body = options.body
      const row = { id: 'SCHEME-original', name: body.name, revision: 1, latestRevision: 1, definition: body.definition,
        calculationDraft: body.calculationDraft, savedEditor: body.editor, editor: {} }
      saved.set(row.id, structuredClone(row)); return structuredClone(row)
    }
    if (path.endsWith('/draft')) { const row = saved.get('SCHEME-original'); row.editor = structuredClone(options.body.editor); return structuredClone(row) }
    if (path === 'model-schemes/SCHEME-original') return structuredClone(saved.get('SCHEME-original'))
    throw new Error('Unexpected call: ' + path)
  }
  await openOriginalCalibrationRecord(cal, app, record, api)
  assert.equal(cal.modelSchemeName, record.name)
  assert.equal(cal.eventId, 'A')
  assert.equal(cal.events.length, 3)
  assert.deepEqual(cal.events.map(row => row.purpose), ['率定', '验证', '未使用'])
  assert.equal(cal.schemeSourceBundleVersionId, 'INPUT-original')
  assert.equal(cal.schemeRoutingVersionId, 'ROUTING-original')
  assert.equal(cal.scienceVersionId, 'SCIENCE-original')
  assert.equal(cal.evaporationMode, 'science-version')
  assert.equal(cal.statePolicy, 'continuous-origin')
  assert.equal(cal.continuousOrigin, source.calibration.continuousOrigin)
  assert.equal(cal.maxEvaluations, 960)
  assert.equal(cal.searchSeed, 20260820)
  assert.deepEqual(cal.params, { SM: 18 })
  assert.deepEqual(cal.originalCalibrationPlan.sourceExcludedEventIds, ['C'])
  assert.deepEqual(app, display)
  assert.equal(cal.running, false)
  await openOriginalCalibrationRecord(cal, app, record, api)
  assert.equal(calls.filter(([path, method]) => path === 'model-schemes' && method === 'POST').length, 1)
  assert.equal(saved.get('SCHEME-original').savedEditor.events.length, events.length)
  assert.equal(calls.some(([path]) => path.endsWith('/draft')), false)
  assert.equal(calls.some(([path]) => /operations|calibration-tasks|scheme-preparations/.test(path)), false)
})

test('failed or cancelled trial tasks leave the previous successful result and curve records untouched', async () => {
  const store = readFileSync(new URL('../src/stores/calibration.js', import.meta.url), 'utf8')
  const actionsSource = store.slice(store.indexOf('    async runOperationRequest('), store.indexOf('    setContext(')).trim().replace(/,$/, '')
  for (const status of ['failed', 'cancelled']) {
    let applied = 0
    const api = async path => path === 'operations' ? { operationId: 'OP-check', status: 'queued' } : { status, errorMessage: status === 'failed' ? '资料不足' : '', issues: [] }
    const actions = new Function('modelApi', 'clonePlain', 'backendWaits', 'useAppStore', 'return { ' + actionsSource + ' }')(api, structuredClone, new Map(), () => ({ projectData: {} }))
    const prior = { taskId: 'JOB-success', hydrographs: { A: { forecast: [{ time: 'a', value: 5 }] } } }
    const records = [{ taskId: 'JOB-previous' }, { taskId: 'JOB-success' }]
    const cal = { ...actions, contextKey: 'current', runResult: prior, trialRecords: records, workspaces: {}, rainfallMethod: 'reuse',
      saveCurrentWorkspace() {}, applyBackendResult() { applied++ } }
    const outcome = await cal.runOperationRequest({ context: { contextKey: 'current' }, requestId: 'repeat' }, 'manualTrial')
    assert.equal(outcome.ok, false)
    assert.equal(cal.runResult, prior)
    assert.equal(cal.trialRecords, records)
    assert.equal(applied, 0)
  }
})

test('only a genuinely active operation reopens the workbench after refresh', async () => {
  for (const status of ['failed', 'cancelled', 'completed', 'running', 'queued']) {
    let saved = 0
    const cal = { activeOperationId: 'OP-current', activeOperationRequest: {}, running: true, saveCurrentWorkspace() { saved++ } }
    const active = await recoverActiveCalibrationOperation(cal, async () => ({ status }))
    assert.equal(active, ['running', 'queued'].includes(status))
    assert.equal(cal.activeOperationId, active ? 'OP-current' : null)
    assert.equal(saved, active ? 0 : 1)
  }
})

test('restoring old unknown flood windows keeps the base catalogue separate and retains saved names across another restore', () => {
  const original = { events: [{ id: 'A', name: '原目录洪水', purpose: '未使用' }], schemeWindows: [], schemeSourceBundleVersionId: 'INPUT-original' }
  const editor = { events: [{ id: 'A', purpose: '率定', start: '2024-06-01T00:00', end: '2024-06-02T00:00' },
    { id: 'WINDOW-extra', purpose: '验证', start: '2024-06-05T00:00', end: '2024-06-06T00:00' }],
    schemeWindows: [{ id: 'WINDOW-extra', name: '补充洪水', purpose: '验证', start: '2024-06-05T00:00', end: '2024-06-06T00:00' }], eventId: 'WINDOW-extra' }
  restoreCalibrationEditor(original, editor)
  assert.equal(original.events.length, 1)
  assert.equal(original.schemeWindows.length, 1)
  assert.equal(original.schemeWindows[0].name, '补充洪水')
  assert.equal(original.eventId, 'WINDOW-extra')
  const saved = calibrationEditor(original)
  assert.equal(saved.events.length, 1)
  const another = { events: [{ id: 'A', name: '原目录洪水' }], schemeWindows: [] }
  restoreCalibrationEditor(another, saved)
  assert.equal(another.events.length, 1)
  assert.equal(another.schemeWindows[0].name, '补充洪水')
  assert.equal(another.schemeWindows[0].purpose, '验证')
})
