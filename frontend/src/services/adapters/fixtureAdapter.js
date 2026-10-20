import dayjs from 'dayjs'
import {
  DEFAULT_PARAMS,
  sectionBasePeak,
  simulateHydrograph,
  warnLevelFromPeak
} from '../../models/xajParamEngine.js'
import { resolveDomainContext } from '../../data/domainContext.js'

const STORAGE_KEY = 'hydro_fixture_tasks_v1'
const tasks = new Map()
const results = new Map()
const timers = new Map()
const listeners = new Set()
// Keep old records recoverable in browser storage, but never list or execute
// tasks from an unsupported basin or an incomplete/mismatched context.
const historicalTasks = new Map()
const historicalResults = new Map()
let hydrated = false
let taskSequence = 0

const RUN_LABELS = {
  continuousSimulation: '连续模拟',
  continuousCalibration: '连续时段率定',
  eventCalibration: '洪水事件率定',
  stateInitialization: '状态初始化',
  stateContinuation: '状态续算',
  forecast: '洪水预报'
}

function fixtureContext(config = {}) {
  if (!config || typeof config !== 'object') return null
  const nested = config.context || {}
  const domainId = config.domainId ?? nested.projectId
  const modelScale = config.modelScale ?? nested.resolutionId
  const modelConfigId = config.modelConfigId ?? nested.modelConfigId
  const contextKey = config.contextKey ?? nested.contextKey
  if (domainId !== 'selinco' || !['1km', '0p1deg'].includes(modelScale) || !modelConfigId || !contextKey) return null
  for (const [flat, embedded] of [
    [config.domainId, nested.projectId], [config.modelScale, nested.resolutionId],
    [config.modelConfigId, nested.modelConfigId], [config.contextKey, nested.contextKey]
  ]) {
    if (flat != null && embedded != null && flat !== embedded) return null
  }
  if (config.basinProjectId != null && config.basinProjectId !== domainId) return null
  const registered = resolveDomainContext(domainId, { modelScale, configId: modelConfigId })
  if (registered.status !== 'frontend-fixture' || registered.modelScale !== modelScale
    || registered.configId !== modelConfigId || registered.contextKey !== contextKey) return null
  if (config.modelProjectId != null && config.modelProjectId !== registered.projectId) return null
  return registered
}

export function isFixtureRunConfig(config) {
  return Boolean(fixtureContext(config))
}

export function isFixtureTask(task) {
  return Boolean(task && typeof task.taskId === 'string' && !task.taskId.startsWith('JOB-')
    && task.resultSource === 'frontend-fixture' && Object.hasOwn(RUN_LABELS, task.runType)
    && isFixtureRunConfig(task.runConfig)
    && task.contextKey === (task.runConfig.contextKey ?? task.runConfig.context?.contextKey))
}

function normalizedFixtureConfig(config) {
  const context = fixtureContext(config)
  if (!context) throw new Error('演示任务仅允许已登记、完整且一致的色林错试算上下文。')
  return {
    ...clone(config), domainId: context.basinId, modelScale: context.modelScale,
    modelProjectId: context.projectId, modelConfigId: context.configId, contextKey: context.contextKey
  }
}

function nowText() {
  return dayjs().format('YYYY-MM-DD HH:mm:ss')
}

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value))
}

function readStorage() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  } catch {
    return {}
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      tasks: [...historicalTasks.values(), ...tasks.values()],
      results: Object.fromEntries([...historicalResults, ...results])
    }))
  } catch {
    // Storage can be unavailable or full; the in-memory task remains usable.
  }
}

function notify(task) {
  persist()
  const snapshot = clone(task)
  listeners.forEach((listener) => listener(snapshot))
}

function updateTask(taskId, partial) {
  const current = tasks.get(taskId)
  if (!current) return null
  const next = { ...current, ...partial }
  tasks.set(taskId, next)
  notify(next)
  return next
}

function compact(value) {
  return String(value || '').replace(/[^A-Za-z0-9]/g, '').slice(-12) || 'TASK'
}

function createTaskId(runType, contextKey) {
  const prefix = {
    continuousSimulation: 'SIM',
    continuousCalibration: 'CCAL',
    eventCalibration: 'ECAL',
    stateInitialization: 'SINI',
    stateContinuation: 'SCON',
    forecast: 'FCST'
  }[runType] || 'TASK'
  return `${prefix}-${compact(contextKey).toUpperCase()}-${dayjs().format('YYYYMMDDHHmmss')}-${String(Date.now()).slice(-5)}-${++taskSequence}`
}

function hydroOptions(config = {}, section = {}) {
  const domainId = config.domainId
  const lakeMode = domainId === 'selinco'
  return {
    basePeak: section.basePeak || config.basePeak || sectionBasePeak(section.id || config.sectionId),
    domainId,
    targetPeak: lakeMode,
    baseFlow: lakeMode ? (section.currentFlow ?? config.currentFlow) : undefined,
    issueFlow: lakeMode ? (section.currentFlow ?? config.currentFlow) : undefined,
    seed: config.seed ?? 1,
    hours: Math.min(240, Math.max(6, Number(config.leadHours || config.hours) || 72)),
    obsHours: lakeMode ? 48 : (Number(config.obsHours) || 36),
    observedBeforeStart: Boolean(config.observedBeforeStart),
    start: String(config.startTime || config.start || '2026-06-02T14:00:00').replace(' ', 'T'),
    wetness: config.wetness
  }
}

function forecastSummary(hydrograph, config, thresholds = {}) {
  const lakeMode = config.domainId === 'selinco'
  const warnLevel = lakeMode ? 4531.1 : (thresholds.warnLevel ?? 34.5)
  const ensureLevel = lakeMode ? 4531.5 : (thresholds.ensureLevel ?? 36)
  const warnFlow = thresholds.warnFlow ?? 2800
  const peakLevel = lakeMode
    ? +(4530.24 + Math.min(0.95, hydrograph.volume / 4.8e9)).toFixed(2)
    : +(warnLevel - 0.8 + hydrograph.peak / (warnFlow * 1.1)).toFixed(2)
  return {
    key: 'main',
    name: '本地试算预报',
    peak: hydrograph.peak,
    peakTime: dayjs(config.startTime).add(hydrograph.peakHour || 18, 'hour').format('YYYY-MM-DD HH:mm'),
    level: lakeMode
      ? (peakLevel >= ensureLevel ? '高水位' : peakLevel >= warnLevel ? '关注' : '平稳')
      : warnLevelFromPeak(hydrograph.peak, peakLevel, thresholds),
    peakLevel,
    warnLevel,
    ensureLevel,
    warnFlow,
    volume: hydrograph.volume,
    duration: 18,
    deltaPeakPrev: Math.round(hydrograph.peak * 0.05),
    deltaTimePrev: 0,
    wetness: hydrograph.wetness
  }
}

function buildFixtureResult(task) {
  if (!isFixtureTask(task)) throw new Error('拒绝为非色林错试算任务生成演示结果。')
  const config = task.runConfig || {}
  const params = { ...DEFAULT_PARAMS, ...(config.params || {}) }

  if (task.runType === 'forecast') {
    const sections = config.sections?.length ? config.sections : [{
      id: config.sectionId || 'default',
      name: config.sectionName || '预报对象',
      basePeak: config.basePeak,
      currentFlow: config.currentFlow
    }]
    const outputs = sections.map((section) => {
      const hydrograph = simulateHydrograph(params, hydroOptions({
        ...config,
        observedBeforeStart: true,
        obsHours: config.domainId === 'selinco' ? 48 : 24
      }, section))
      return {
        productId: `${task.taskId}::${section.id}`,
        sectionId: section.id,
        sectionName: section.name,
        hydrograph,
        summary: forecastSummary(hydrograph, config, config.thresholds?.[section.id] || {})
      }
    })
    const focused = outputs.find((item) => item.sectionId === config.sectionId) || outputs[0]
    return {
      runId: task.taskId,
      outputs,
      focusSectionId: focused?.sectionId,
      productId: focused?.productId,
      hydrograph: focused?.hydrograph,
      summary: focused?.summary,
      resultSource: 'frontend-fixture'
    }
  }

  if (task.runType === 'continuousSimulation' || task.runType === 'continuousCalibration' || task.runType === 'eventCalibration') {
    const hydrograph = simulateHydrograph(params, hydroOptions(config))
    return {
      hydrograph,
      metrics: hydrograph.metrics,
      peak: hydrograph.peak,
      peakTime: hydrograph.peakTime,
      endStateVersionId: task.runType === 'continuousSimulation' ? `STATE-${compact(task.contextKey)}-${dayjs().format('YYYYMMDDHHmm')}` : null,
      resultSource: 'frontend-fixture'
    }
  }

  if (task.runType === 'stateInitialization' || task.runType === 'stateContinuation') {
    const stateVersionId = `STATE-${compact(task.contextKey)}-${dayjs(config.targetTime || config.issueTime).format('YYYYMMDDHHmm')}`
    return {
      stateVersionId,
      parentStateVersionId: config.sourceStateVersionId || null,
      targetTime: config.targetTime || config.issueTime,
      variables: config.variables || ['WU', 'WL', 'WD', 'S'],
      resultSource: 'frontend-fixture'
    }
  }

  return { resultSource: 'frontend-fixture', echo: clone(config) }
}

function clearTaskTimers(taskId) {
  ;(timers.get(taskId) || []).forEach((timer) => clearTimeout(timer))
  timers.delete(taskId)
}

function schedule(taskId, delay, callback) {
  const timer = setTimeout(callback, delay)
  timers.set(taskId, [...(timers.get(taskId) || []), timer])
}

function startLifecycle(taskId) {
  clearTaskTimers(taskId)
  if (!isFixtureTask(tasks.get(taskId))) return
  schedule(taskId, 80, () => {
    const task = tasks.get(taskId)
    if (!task || ['cancelled', 'failed', 'completed', 'archived'].includes(task.status)) return
    updateTask(taskId, {
      status: 'running', progress: 12, stageLabel: '校验任务条件', startedAt: task.startedAt || nowText()
    })
  })
  schedule(taskId, 260, () => {
    const task = tasks.get(taskId)
    if (task?.status !== 'running') return
    updateTask(taskId, { progress: 48, stageLabel: '读取试算输入' })
  })
  schedule(taskId, 520, () => {
    const task = tasks.get(taskId)
    if (task?.status !== 'running') return
    updateTask(taskId, { progress: 82, stageLabel: '生成试算结果' })
  })
  schedule(taskId, 780, () => {
    const task = tasks.get(taskId)
    if (task?.status !== 'running') return
    if (task.runConfig?.fixtureScenarioId === 'failed') {
      updateTask(taskId, {
        status: 'failed', progress: 100, stageLabel: '任务失败',
        errorMessage: task.runConfig.errorMessage || '前端失败样例：输入目录校验未通过', completedAt: nowText()
      })
      clearTaskTimers(taskId)
      return
    }
    try {
      const result = buildFixtureResult(task)
      results.set(taskId, result)
      updateTask(taskId, {
        status: 'completed', progress: 100, stageLabel: '试算完成',
        resultVersionId: `RESULT-${taskId}`,
        lineage: { ...(task.lineage || task.runConfig?.lineage || {}), resultVersionId: `RESULT-${taskId}` },
        completedAt: nowText()
      })
    } catch (error) {
      updateTask(taskId, {
        status: 'failed', progress: 100, stageLabel: '结果生成失败',
        errorMessage: error?.message || String(error), completedAt: nowText()
      })
    } finally {
      clearTaskTimers(taskId)
    }
  })
}

function hydrate() {
  if (hydrated) return
  hydrated = true
  const saved = readStorage()
  Object.values(saved.tasks || []).forEach((task) => {
    if (!isFixtureTask(task)) {
      if (task?.taskId) historicalTasks.set(task.taskId, task)
      return
    }
    const restored = ['queued', 'running'].includes(task.status)
      ? { ...task, runConfig: normalizedFixtureConfig(task.runConfig), status: 'queued', progress: 0, stageLabel: '刷新后恢复排队' }
      : { ...task, runConfig: normalizedFixtureConfig(task.runConfig) }
    tasks.set(restored.taskId, restored)
  })
  Object.entries(saved.results || {}).forEach(([taskId, result]) => {
    if (tasks.has(taskId) && result?.resultSource === 'frontend-fixture') results.set(taskId, result)
    else historicalResults.set(taskId, result)
  })
  ;[...tasks.values()].filter((task) => task.status === 'queued').forEach((task) => startLifecycle(task.taskId))
}

export const fixtureAdapter = {
  listOptions(context) {
    return Promise.resolve({
      contextKey: context?.contextKey,
      runTypes: isFixtureRunConfig({ domainId: context?.basinId, modelScale: context?.modelScale, modelConfigId: context?.configId, contextKey: context?.contextKey })
        ? Object.entries(RUN_LABELS).map(([id, label]) => ({ id, label })) : []
    })
  },
  async submitTask(runType, runConfig = {}) {
    if (!Object.hasOwn(RUN_LABELS, runType)) throw new Error('未登记的演示任务类型。')
    const config = normalizedFixtureConfig(runConfig)
    hydrate()
    const taskId = createTaskId(runType, config.contextKey)
    const task = {
      taskId,
      runType,
      runTypeLabel: RUN_LABELS[runType] || runType,
      contextKey: config.contextKey,
      runConfig: config,
      status: 'queued',
      progress: 0,
      stageLabel: '等待试算',
      resultSource: 'frontend-fixture',
      resultVersionId: null,
      errorMessage: '',
      createdAt: nowText(),
      startedAt: null,
      completedAt: null,
      archivedAt: null,
      sourceTaskId: config.sourceTaskId || null,
      lineage: clone(config.lineage || {})
    }
    tasks.set(taskId, task)
    notify(task)
    startLifecycle(taskId)
    return clone(task)
  },
  async getTask(taskId) {
    hydrate()
    const task = tasks.get(taskId)
    return isFixtureTask(task) ? clone(task) : null
  },
  async listTasks() {
    hydrate()
    return clone([...tasks.values()].filter(isFixtureTask).sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt))))
  },
  async cancelTask(taskId) {
    hydrate()
    const task = tasks.get(taskId)
    if (!isFixtureTask(task)) return null
    if (!['queued', 'running'].includes(task.status)) return clone(task)
    clearTaskTimers(taskId)
    return clone(updateTask(taskId, {
      status: 'cancelled', progress: task.progress, stageLabel: '已停止', completedAt: nowText()
    }))
  },
  async getResult(taskId) {
    hydrate()
    const task = tasks.get(taskId)
    if (!isFixtureTask(task) || !['completed', 'archived'].includes(task.status)) return null
    const result = results.get(taskId)
    return result?.resultSource === 'frontend-fixture' ? clone(result) : null
  },
  async archiveTask(taskId) {
    hydrate()
    const task = tasks.get(taskId)
    if (!isFixtureTask(task)) return null
    if (!['completed', 'archived'].includes(task.status)) return clone(task)
    return clone(updateTask(taskId, { status: 'archived', stageLabel: '已归档', archivedAt: task.archivedAt || nowText() }))
  },
  async cloneTask(taskId) {
    hydrate()
    const source = tasks.get(taskId)
    if (!isFixtureTask(source)) return null
    return this.submitTask(source.runType, { ...source.runConfig, sourceTaskId: source.taskId })
  },
  subscribe(listener) {
    listeners.add(listener)
    return () => listeners.delete(listener)
  }
}
