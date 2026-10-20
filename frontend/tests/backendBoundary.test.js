import { it } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import dayjs from 'dayjs'
import { buildCalibrationRequest, selectCalibrationCandidates } from '../src/services/calibrationSubmissionService.js'
import { independentTestPassed, releaseReadiness, registerCalibrationResult, publishAutomaticParameter } from '../src/services/parameterLibraryService.js'
import { caseExecutionAvailability, isCaseFixtureContext } from '../src/services/caseAvailabilityService.js'
import { normalizeBackendTask, uniqueRequestId } from '../src/services/modelBackendService.js'
import { formatMetric, computeMetrics, simulateHydrograph, DEFAULT_PARAMS, VERSION_PRESETS, PARAM_DEFS, sectionBasePeak, warnLevelFromPeak } from '../src/models/xajParamEngine.js'
import { resolveDomainContext } from '../src/data/domainContext.js'
import { isFixtureRunConfig, isFixtureTask } from '../src/services/adapters/fixtureAdapter.js'
import { visibleWorkspaceResult } from '../src/services/savedModelResultService.js'

it('explicit failed independent test never becomes passed from a high NSE', () => {
  assert.equal(independentTestPassed({ passed: false, nse: 0.99, peakErrorPercent: 1 }), false)
  assert.equal(releaseReadiness({ productionEligible: false, independentTest: { passed: true }, review: { status: '审核通过' } }).canPublish, false)
})

it('diagnostic candidate cannot publish even after approval', () => {
  const storage = new Map()
  const previous = globalThis.localStorage
  globalThis.localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) }
  try {
    const context = resolveDomainContext('nananxi-xufan', { modelScale: '1km' })
    const args = { projectId: context.basinId, resolutionId: context.modelScale, projectData: {}, request: {
      context: { projectId: context.basinId, resolutionId: context.modelScale, modelConfigId: context.configId, contextKey: context.contextKey }
    } }
    registerCalibrationResult({ ...args, result: { parameterVersionId: 'P1', taskId: 'JOB-diagnostic', resultSource: 'model-backend', productionEligible: false, independentTest: { passed: true } } })
    assert.throws(() => publishAutomaticParameter({ ...args, versionId: 'P1' }), /生产验收/)
  } finally { globalThis.localStorage = previous }
})

it('server readiness is operation-specific and goes away when offline', () => {
  const context = { basinId: 'nananxi-xufan', modelScale: '1km' }
  const resolution = { status: 'partial', spatialManifest: './1km/spatial.json' }
  const data = { manifest: { id: context.basinId, resolutions: { '1km': resolution }, capabilities: { continuousSimulation: true, forecast: true } }, resolution,
    backend: { mode: 'model-backend', ready: true, operations: ['continuousSimulation'], unsupported: { forecast: '缺少正式预报降雨' } } }
  assert.equal(caseExecutionAvailability(context, data, 'continuousSimulation').mode, 'model-backend')
  assert.equal(caseExecutionAvailability(context, data, 'forecast').ready, false)
  data.backendError = '断开'
  assert.equal(caseExecutionAvailability(context, data, 'continuousSimulation').ready, false)
})

it('simulation request freezes native params and explicit assumed PET', () => {
  const app = { basinId: 'nananxi-xufan', modelScale: '1km', modelConfigId: 'nananxi-xufan-1km', projectData: { backend: {} } }
  const calibration = { contextKey: 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km', params: { KC: 0.89 }, simulationStart: '2017-06-19T11:00', simulationEnd: '2017-06-20T11:00', warmupDays: 7 }
  const a = buildCalibrationRequest({ app, calibration, operationMode: 'simulation' })
  const b = buildCalibrationRequest({ app, calibration, operationMode: 'simulation' })
  calibration.params.KC = 1
  assert.equal(a.parameters.KC, 0.89)
  assert.equal(a.inputs.evaporation.mode, 'monthly-climatology-assumption')
  assert.notEqual(a.requestId, b.requestId)
  assert.notEqual(uniqueRequestId(), uniqueRequestId())
})

it('normalizes durable backend jobs without dropping their frozen request', () => {
  const request = { context: { projectId: 'nananxi-xufan', resolutionId: '1km', contextKey: 'CASE' }, period: { endTime: '2017-06-20T11:00' }, parameters: { WM: 150 }, runType: 'stateContinuation' }
  const task = normalizeBackendTask({ taskId: 'JOB-test', runConfig: request })
  assert.equal(task.request, request)
  assert.equal(task.runConfig.domainId, 'nananxi-xufan')
  assert.equal(task.runConfig.targetTime, request.period.endTime)
})

it('simulation request freezes the selected routing identity without following later changes', () => {
  const app = { basinId: 'nananxi-xufan', modelScale: '1km', modelConfigId: 'nananxi-xufan-1km', selectedRoutingVersionId: 'ROUTING-submitted', projectData: { backend: {} } }
  const calibration = { contextKey: 'CASE', params: { KC: 0.89 }, simulationStart: '2017-06-19T11:00', simulationEnd: '2017-06-20T11:00', warmupDays: 7 }
  const request = buildCalibrationRequest({ app, calibration, operationMode: 'simulation' })
  app.selectedRoutingVersionId = 'ROUTING-later'
  assert.equal(request.routing.versionId, 'ROUTING-submitted')
})

it('new diagnostic task types retain their complete frozen requests', () => {
  for (const runType of ['pointQuery', 'diagnosticExperiment', 'productionWorkflow']) {
    const request = { runType, context: { projectId: 'nananxi-xufan', resolutionId: '1km', contextKey: 'CASE' }, routing: { versionId: 'ROUTING-original' }, baseRequest: { runType: 'calibration' } }
    const task = normalizeBackendTask({ taskId: 'JOB-diagnostic', runType, runConfig: request })
    assert.equal(task.request, request)
    assert.equal(task.runType, runType)
    assert.equal(task.runConfig.routing.versionId, 'ROUTING-original')
    assert.equal(task.runConfig.baseRequest, request.baseRequest)
  }
})

it('formats native backend metrics without requiring an invented correlation', () => {
  const rows = formatMetric({ nse: 0.262, peakErr: -61.2, timeErrH: 1, volErr: -30.4 })
  assert.equal(rows[0].value, '0.262')
  assert.equal(rows.length, 4)
  assert.equal(rows.some((row) => row.name === '相关系数 R'), false)
})

it('leaves undefined diagnostic metrics empty instead of reporting zero', () => {
  const rows = formatMetric({ nse: null, peakErr: null, timeErrH: null, volErr: null })
  assert.equal(rows.every((row) => row.value === '—'), true)
})

it('local draft and QC-pending events cannot re-enter candidates by changing their display status', () => {
  const rows = [
    { id: 'registered', observedPeak: 25, status: 'active', runtimeRegistered: true, registeredStatus: 'active' },
    { id: 'local-only', observedPeak: 999, status: 'active', runtimeRegistered: false, registeredStatus: null },
    { id: 'qc-pending', observedPeak: 999, status: 'active', runtimeRegistered: true, registeredStatus: 'pending' },
    { id: 'stale-registration', observedPeak: 999, status: 'active', runtimeRegistered: true },
    { id: 'offline-catalog', observedPeak: 30, status: 'active' }
  ]
  assert.deepEqual(selectCalibrationCandidates(rows).map((row) => row.id), ['registered', 'offline-catalog'])
  assert.deepEqual(selectCalibrationCandidates(rows, { method: 'top-n', topN: 1 }).map((row) => row.id), ['offline-catalog'])
  assert.deepEqual(selectCalibrationCandidates(rows, { method: 'manual', manualIds: ['local-only', 'qc-pending'] }), [])
})

it('missing and non-numeric observed peaks never become zero-valued calibration candidates', () => {
  const invalid = [null, undefined, '', '   ', false, true, [], {}, NaN, Infinity]
    .map((observedPeak, index) => ({ id: `invalid-${index}`, observedPeak, status: 'active' }))
  const genuineZero = { id: 'zero', observedPeak: 0, status: 'active' }
  const numericString = { id: 'numeric-string', observedPeak: '12.5', status: 'active' }
  assert.deepEqual(selectCalibrationCandidates([...invalid, genuineZero, numericString]).map((row) => row.id), ['zero', 'numeric-string'])
})

it('saved Nananxi fixture or unlabelled results never show as completed model results, including offline', () => {
  const context = resolveDomainContext('nananxi-xufan', { modelScale: '1km' })
  const old = { resultSource: 'frontend-fixture', taskId: 'SIM-old', hydrograph: { forecast: [{ time: 'fake', value: 999 }] }, metrics: { nse: 0.98 } }
  const snapshot = structuredClone(old)
  assert.equal(visibleWorkspaceResult(old, context), null)
  assert.equal(visibleWorkspaceResult({ taskId: 'SIM-unlabelled', metrics: { nse: 0.99 } }, context), null)
  assert.deepEqual(old, snapshot)
})

it('genuine saved model results remain visible offline only in their own exact workspace', () => {
  const context = resolveDomainContext('nananxi-xufan', { modelScale: '1km' })
  const result = { resultSource: 'model-backend', taskId: 'JOB-saved', contextKey: context.contextKey, metrics: { nse: 0.1 } }
  assert.equal(visibleWorkspaceResult(result, { ...context, backendError: 'offline' }), result)
  assert.equal(visibleWorkspaceResult(result, resolveDomainContext('nananxi-xufan', { modelScale: '0p1deg' })), null)
  assert.equal(visibleWorkspaceResult(result, resolveDomainContext('selinco', { modelScale: '1km' })), null)
  assert.equal(visibleWorkspaceResult({ ...result, taskId: 'SIM-forged' }, context), null)
  assert.equal(visibleWorkspaceResult({ ...result, contextKey: undefined }, context), null)
  assert.equal(visibleWorkspaceResult({ ...result, context: { contextKey: 'foreign' } }, context), null)
})

it('full frozen model result context is accepted only when its scope agrees with the workspace', () => {
  const context = resolveDomainContext('nananxi-xufan', { modelScale: '1km' })
  const result = { resultSource: 'model-backend', taskId: 'JOB-full-context', context: {
    contextKey: context.contextKey, projectId: context.basinId, resolutionId: context.modelScale, modelConfigId: context.configId
  } }
  assert.equal(visibleWorkspaceResult(result, context), result)
  for (const field of ['projectId', 'resolutionId', 'modelConfigId', 'contextKey']) {
    assert.equal(visibleWorkspaceResult({ ...result, context: { ...result.context, [field]: 'foreign' } }, context), null)
  }
})

it('both registered Selinco scales keep explicitly labelled demo results and reject incomplete or conflicting scope', () => {
  for (const modelScale of ['1km', '0p1deg']) {
    const context = resolveDomainContext('selinco', { modelScale })
    const result = { resultSource: 'frontend-fixture', taskId: 'SIM-selinco', metrics: { nse: 0.8 } }
    assert.equal(visibleWorkspaceResult(result, context), result)
    assert.equal(visibleWorkspaceResult({ ...result, contextKey: context.contextKey }, context)?.taskId, 'SIM-selinco')
    assert.equal(visibleWorkspaceResult({ ...result, contextKey: 'other-scale' }, context), null)
    assert.equal(visibleWorkspaceResult({ ...result, taskId: 'JOB-forged' }, context), null)
    assert.equal(visibleWorkspaceResult({ taskId: 'SIM-unlabelled' }, context), null)
    assert.equal(visibleWorkspaceResult(result, { basinId: 'selinco', status: 'frontend-fixture', contextKey: context.contextKey }), null)
    assert.equal(visibleWorkspaceResult(result, { ...context, modelScale: modelScale === '1km' ? '0p1deg' : '1km' }), null)
  }
  assert.equal(visibleWorkspaceResult(null, null), null)
})

function fixtureConfig(scale = '1km') {
  const context = resolveDomainContext('selinco', { modelScale: scale })
  return { domainId: 'selinco', modelScale: scale, modelConfigId: context.configId, contextKey: context.contextKey }
}

it('requires finite observed/model pairs and never invents short-sample NSE or R', () => {
  for (const count of [0, 1, 2]) {
    const series = Array.from({ length: count }, (_, index) => ({ time: `${index}`, value: index + 1 }))
    const metrics = computeMetrics(series, series)
    assert.equal(metrics.nse, null)
    assert.equal(metrics.r, null)
    assert.equal(formatMetric(metrics).every((row) => row.value === '—'), true)
  }
  const observations = [{ time: 'a', value: 1 }, { time: 'b', value: null }, { time: 'c', value: 3 }, { time: 'd', value: NaN }]
  const simulated = observations.map((point) => ({ ...point, value: 2 }))
  assert.equal(computeMetrics(observations, simulated).nse, null)
  const constant = [{ time: 'a', value: 4 }, { time: 'b', value: 4 }, { time: 'c', value: 4 }]
  assert.equal(computeMetrics(constant, constant).nse, null)
  assert.equal(computeMetrics(constant, constant).r, null)
})

it('whitelists only complete registered Selinco fixture contexts', () => {
  for (const scale of ['1km', '0p1deg']) assert.equal(isFixtureRunConfig(fixtureConfig(scale)), true)
  const valid = fixtureConfig()
  for (const field of ['domainId', 'modelScale', 'modelConfigId', 'contextKey']) {
    const incomplete = { ...valid }
    delete incomplete[field]
    assert.equal(isFixtureRunConfig(incomplete), false, `missing ${field}`)
  }
  assert.equal(isFixtureRunConfig({ ...valid, domainId: 'nananxi-xufan' }), false)
  assert.equal(isFixtureRunConfig({ ...valid, domainId: 'xinanjiang' }), false)
  assert.equal(isFixtureRunConfig({ ...valid, modelScale: '0p1deg' }), false)
  assert.equal(isFixtureRunConfig({ ...valid, modelConfigId: 'unknown' }), false)
  assert.equal(isFixtureRunConfig({ ...valid, context: { projectId: 'nananxi-xufan' } }), false)
  assert.equal(isFixtureRunConfig({ context: { projectId: 'selinco', resolutionId: '1km', modelConfigId: valid.modelConfigId, contextKey: valid.contextKey } }), true)
})

it('quarantines historical Nananxi mock tasks without restoring, cloning or deleting them', async () => {
  const storage = new Map()
  const previousStorage = globalThis.localStorage
  globalThis.localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) }
  const validConfig = fixtureConfig()
  const oldTask = (taskId, runConfig, status = 'queued') => ({
    taskId, runType: 'forecast', contextKey: runConfig.contextKey, runConfig,
    status, resultSource: 'frontend-fixture', createdAt: '2026-01-01 00:00:00'
  })
  const invalid = [
    oldTask('OLD-NANANXI', { ...validConfig, domainId: 'nananxi-xufan', contextKey: 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km' }),
    oldTask('OLD-XINANJIANG', { ...validConfig, domainId: 'xinanjiang' }, 'running'),
    oldTask('BROKEN-SELINCO', { ...validConfig, modelScale: '0p1deg' }),
    oldTask('JOB-forged-fixture', validConfig)
  ]
  storage.set('hydro_fixture_tasks_v1', JSON.stringify({
    tasks: [...invalid, oldTask('VALID-SELINCO-QUEUED', { ...validConfig, startTime: '2026-06-02 14:00', sectionId: 'SEL-SEC-001', basePeak: 264, currentFlow: 186 }), oldTask('MISSING-RESULT', validConfig, 'completed')],
    results: { 'OLD-NANANXI': { resultSource: 'frontend-fixture', hydrograph: { forecast: [{ time: 'fake', value: 99 }] } } }
  }))
  const { fixtureAdapter } = await import(`../src/services/adapters/fixtureAdapter.js?boundary=${Date.now()}`)
  try {
    const visible = await fixtureAdapter.listTasks()
    assert.deepEqual(visible.map((task) => task.taskId).sort(), ['MISSING-RESULT', 'VALID-SELINCO-QUEUED'])
    assert.equal(visible.every(isFixtureTask), true)
    for (const task of invalid) {
      assert.equal(await fixtureAdapter.getTask(task.taskId), null)
      assert.equal(await fixtureAdapter.getResult(task.taskId), null)
      assert.equal(await fixtureAdapter.cloneTask(task.taskId), null)
      assert.equal(await fixtureAdapter.cancelTask(task.taskId), null)
      assert.equal(await fixtureAdapter.archiveTask(task.taskId), null)
    }
    assert.equal(await fixtureAdapter.getResult('MISSING-RESULT'), null)
    await assert.rejects(fixtureAdapter.submitTask('forecast', { domainId: 'selinco' }), /上下文/)
    await assert.rejects(fixtureAdapter.submitTask('forecast', { ...validConfig, domainId: 'nananxi-xufan' }), /上下文/)
    await assert.rejects(fixtureAdapter.submitTask('unregistered-run', validConfig), /任务类型/)
    await new Promise((resolve) => setTimeout(resolve, 850))
    assert.equal((await fixtureAdapter.getTask('VALID-SELINCO-QUEUED')).status, 'completed')
    assert.equal((await fixtureAdapter.getResult('VALID-SELINCO-QUEUED')).resultSource, 'frontend-fixture')
    const clone = await fixtureAdapter.cloneTask('VALID-SELINCO-QUEUED')
    assert.equal(isFixtureTask(clone), true)
    await fixtureAdapter.cancelTask(clone.taskId)
    const persisted = JSON.parse(storage.get('hydro_fixture_tasks_v1'))
    for (const task of invalid) assert.deepEqual(persisted.tasks.find((row) => row.taskId === task.taskId), task)
    assert.equal(persisted.results['OLD-NANANXI'].hydrograph.forecast[0].value, 99)
  } finally { globalThis.localStorage = previousStorage }
})

it('the task service cannot revive or clone unsafe persisted fixture records', async () => {
  const storage = new Map()
  const previousStorage = globalThis.localStorage
  const config = { ...fixtureConfig(), domainId: 'nananxi-xufan' }
  storage.set('hydro_fixture_tasks_v1', JSON.stringify({ tasks: [{ taskId: 'LEGACY-MOCK', runType: 'forecast', contextKey: config.contextKey, runConfig: config, status: 'queued', resultSource: 'frontend-fixture' }] }))
  globalThis.localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) }
  try {
    const { taskService } = await import('../src/services/taskService.js')
    assert.deepEqual(await taskService.listTasks(), [])
    assert.equal(await taskService.getTask('LEGACY-MOCK'), null)
    assert.equal(await taskService.getResult('LEGACY-MOCK'), null)
    assert.equal(await taskService.cloneTask('LEGACY-MOCK'), null)
    assert.throws(() => taskService.submitTask('forecast', { domainId: 'selinco' }), /完整/)
  } finally { globalThis.localStorage = previousStorage }
})

function chartOption(overrides = {}) {
  const source = readFileSync(new URL('../src/components/charts/HydrographChart.vue', import.meta.url), 'utf8')
  const script = source.split('<script setup>')[1].split('</script>')[0].replace(/^import .+$/gm, '')
  const props = { hydrograph: { forecast: [], obs: [], prev: [] }, rainfall: null, showPrev: true, mode: 'calibration', overlay: null, showThresholds: false, warnFlow: null, ensureFlow: null, displayMode: 'combined', ...overrides }
  // Evaluate only the local chart's pure computed-option script; no DOM/ECharts
  // mount is needed to verify that absent inputs stay absent.
  return new Function('computed', 'defineProps', `${script}\nreturn { option, hasRainfall };`)(
    (getter) => ({ get value() { return getter() } }), () => props
  )
}

it('chart rainfall never synthesizes missing or short inputs from the hydrograph', () => {
  const hydrograph = { forecast: [{ time: 'a', value: 3 }, { time: 'b', value: 9 }, { time: 'c', value: 5 }], obs: [{ time: 'a', value: 2 }, { time: 'c', value: 4 }], prev: [] }
  const absent = chartOption({ hydrograph })
  assert.deepEqual(absent.option.value.series[0].data, [null, null, null])
  assert.equal(absent.hasRainfall.value, false)
  assert.deepEqual(chartOption({ hydrograph, rainfall: [0, 2] }).option.value.series[0].data, [0, 2, null])
  assert.deepEqual(chartOption({ hydrograph: { ...hydrograph, forcing: { precipitation: [1, null, 3] } } }).option.value.series[0].data, [1, null, 3])
  assert.deepEqual(absent.option.value.series.find((row) => row.name === '实测流量').data, [2, null, 4])
})

it('chart peak and comparison lines come from supplied timestamped data only', () => {
  const chart = chartOption({
    hydrograph: { forecast: [{ time: 'a', value: null }, { time: 'b', value: null }], obs: [], prev: [{ time: 'b', value: 8 }] },
    overlay: { forecast: [{ time: 'b', value: 9 }, { time: 'c', value: 10 }] }
  }).option.value
  assert.deepEqual(chart.series.find((row) => row.name === '模拟流量').markPoint.data, [])
  assert.deepEqual(chart.series.find((row) => row.name === '上一份预报').data, [null, 8])
  assert.deepEqual(chart.series.find((row) => row.name === '对比档案').data, [null, 9])
})

function forecastDefinition({ tasks = {}, simulate = simulateHydrograph } = {}) {
  const source = readFileSync(new URL('../src/stores/forecast.js', import.meta.url), 'utf8')
  const script = source.replace(/^import\s[\s\S]*?\sfrom\s['"][^'"]+['"]\s*\r?\n/gm, '').replace('export const useForecastStore', 'const useForecastStore')
  return new Function('defineStore', 'dayjs', 'selincoSections', 'DEFAULT_PARAMS', 'VERSION_PRESETS', 'PARAM_DEFS', 'simulateHydrograph', 'sectionBasePeak', 'warnLevelFromPeak', 'useConfigStore', 'resolveDomainContext', 'useTasksStore', 'useAppStore', 'caseExecutionAvailability', 'isCaseFixtureContext', `${script}\nreturn useForecastStore;`)(
    (_name, definition) => definition, dayjs, [], DEFAULT_PARAMS, VERSION_PRESETS, PARAM_DEFS,
    simulate, sectionBasePeak, warnLevelFromPeak, () => ({ thresholds: {} }), resolveDomainContext,
    () => tasks, () => ({}), caseExecutionAvailability, isCaseFixtureContext
  )
}

it('real-backend readiness cannot enable fixture forecast previews or task execution', async () => {
  let calls = 0
  const definition = forecastDefinition({ simulate: (...args) => { calls += 1; return simulateHydrograph(...args) } })
  const context = { executionAvailability: { ready: true, mode: 'model-backend' }, startTime: '2017-06-19 11:00', effectiveParams: DEFAULT_PARAMS, simOptions: {}, canRun: true }
  assert.deepEqual(definition.getters.previewHydrograph.call(context).forecast, [])
  assert.equal(definition.getters.previewScheme.call(context, {}).peak, null)
  assert.deepEqual(definition.getters.sectionPreviews.call(context, { sections: [{ id: '70115800' }] }), [])
  assert.equal(await definition.actions.runForecast.call(context), false)
  assert.equal(calls, 0)
  context.executionAvailability.mode = 'frontend-fixture'
  assert.ok(definition.getters.previewHydrograph.call(context).forecast.length > 0)
  assert.equal(calls, 1)
})

it('missing forecast task outputs fail instead of generating replacement flows', async () => {
  let calls = 0
  const definition = forecastDefinition({
    simulate: () => { calls += 1; throw new Error('unexpected synthesized flow') },
    tasks: {
      submitTask: async () => ({ taskId: 'FCST-valid' }),
      waitForTask: async () => ({ taskId: 'FCST-valid', status: 'completed', resultSource: 'frontend-fixture' }),
      getResult: async () => null
    }
  })
  const context = { ...definition.state(), ...fixtureConfig(), executionAvailability: { ready: true, mode: 'frontend-fixture' }, canRun: true,
    effectiveParams: DEFAULT_PARAMS, targetSection: { id: 'SEL-SEC-001' }, sections: [{ id: 'SEL-SEC-001' }] }
  await assert.rejects(definition.actions.runForecast.call(context), /未返回完整结果/)
  assert.equal(context.committed, null)
  assert.equal(context.running, false)
  assert.equal(calls, 0)
  assert.throws(() => definition.actions.createArchiveRecord.call(context), /尚无已完成/)
})
