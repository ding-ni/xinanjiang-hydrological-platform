import { defineStore } from 'pinia'
import { trialConditions } from '@/services/manualCalibrationService'
import { recoverableCalculation } from '@/services/taskPresentation'
import { physicalFloodGroups } from '@/services/calibrationSubmissionService'
import {
  DEFAULT_PARAMS,
  VERSION_PRESETS,
  PARAM_DEFS,
  simulateHydrograph,
  formatMetric,
  sectionBasePeak
} from '@/models/xajParamEngine'
import { resolveDomainContext } from '@/data/domainContext'
import { useTasksStore } from '@/stores/tasks'
import { useAppStore } from '@/stores/app'
import { parameterLibraryFor } from '@/services/parameterLibraryService'
import { useStateStore } from '@/stores/state'
import { modelApi, normalizeBackendTask } from '@/services/modelBackendService'
import { saveBusinessDraft } from '@/services/workspaceService'
import { mergeBackendFloodEvents, registeredBackendEventSelection, currentInputFloodEvents } from '@/services/backendEventCatalogService'
import { cloneWorkspaceValue, restoreWorkspaceEventRecords } from '@/services/workspaceSnapshotService'
import { DEFAULT_INPUT_BUNDLE_VERSION_ID, frozenInputBundleVersionId, inputResultUsesSelection } from '@/services/inputProductService'
import { visibleWorkspaceResult } from '@/services/savedModelResultService'
import { caseExecutionAvailability, isCaseFixtureContext, projectDataForContext } from '@/services/caseAvailabilityService'
import floodEventCatalog from '@/fixtures/catalogs/flood-events.json'
import {
  FLOOD_EVENT_PURPOSES,
  FLOOD_EVENT_SCHEMA_VERSION,
  buildEventSetVersion,
  mergeFloodEventPackages,
  normalizeFloodEvent,
  stableFloodEventId
} from '@/services/floodEventService'

const SECTION_MAP = {
  新安江大桥: 'SEC-33000200',
  兰江口: 'SEC-LANJIANG',
  寿昌控制断面: 'SEC-SHOUCHANG',
  扎加藏布入湖口: 'SEL-SEC-001',
  扎根藏布入湖口: 'SEL-SEC-002',
  波曲藏布入湖口: 'SEL-SEC-003',
  阿里藏布入湖口: 'SEL-SEC-004',
  色林错全湖: 'SEL-LAKE-ALL',
  '徐畈（二）': '70115800'
}

function validateFloodWindow(domainId, start, end, sectionId) {
  if (!sectionId) return '请填写控制断面。'
  if (domainId === 'nananxi-xufan') {
    if (sectionId !== '70115800') return '南安溪当前仅登记徐畈出口断面（70115800）。'
  }
  return null
}

function observationCoverageWarning(range, start, end) {
  if (!range?.start || !range?.end) return null
  if (start < range.start || end > range.end) return '场次超出当前出口水文资料覆盖范围，已保留并标记为待核查；补充资料后可重新计算统计值。'
  return null
}

const EVENT_SEED = {
  'xaj-2019-06': 0.9,
  'xaj-2020-06': 1,
  'xaj-2021-07': 1.15,
  'xaj-2023-04': 0.85,
  'xaj-2024-06': 0.95,
  'sel-2022-07': 0.9,
  'sel-2023-08': 1.05,
  'sel-2024-07': 1.14,
  'sel-2025-07': 0.98
}

const SELINCO_BASE_PEAK = {
  'SEL-LAKE-ALL': 675,
  'SEL-SEC-001': 264,
  'SEL-SEC-002': 171,
  'SEL-SEC-003': 132,
  'SEL-SEC-004': 108
}

function criteriaTemplate() {
  return {
    metrics: {
      nse: true,
      kge: false,
      peak: true,
      time: true,
      volume: false
    },
    weights: {
      nse: 0.5,
      kge: 0.2,
      peak: 0.25,
      time: 0.25,
      volume: 0.1
    },
    tolerances: {
      peakPercent: 10,
      peakTimeHours: 2
    }
  }
}

function normalizedCriteria(value = {}) {
  const defaults = criteriaTemplate()
  return { ...defaults, ...clonePlain(value), metrics: { ...defaults.metrics, ...value.metrics },
    weights: { ...defaults.weights, ...value.weights }, tolerances: { ...defaults.tolerances, ...value.tolerances } }
}

const EVENT_OVERRIDE_STORAGE = 'hydro_flood_event_overrides_v1:'
const WORKSPACE_STORAGE = 'hydro_calibration_workspaces_v1'
const backendWaits = new Map()

function readWorkspaceRecords() {
  if (typeof localStorage === 'undefined') return {}
  try {
    const saved = JSON.parse(localStorage.getItem(WORKSPACE_STORAGE) || '{}')
    return saved?.workspaces && typeof saved.workspaces === 'object' ? saved.workspaces : {}
  } catch {
    return {}
  }
}

function persistWorkspaceRecords(workspaces) {
  if (typeof localStorage === 'undefined') return
  try {
    saveBusinessDraft(WORKSPACE_STORAGE, JSON.stringify({ schemaVersion: 1, workspaces }))
  } catch {
    // 工作区过大或浏览器禁用存储时，当前页面状态仍然有效。
  }
}

function readEventOverrides(domainId) {
  if (typeof localStorage === 'undefined') return { updates: {}, added: [] }
  try {
    const saved = JSON.parse(localStorage.getItem(`${EVENT_OVERRIDE_STORAGE}${domainId}`) || '{}')
    return {
      updates: saved?.updates && typeof saved.updates === 'object' ? saved.updates : {},
      added: Array.isArray(saved?.added) ? saved.added : []
    }
  } catch {
    return { updates: {}, added: [] }
  }
}

function createFloodEvents(domainId) {
  const base = floodEventCatalog.events
    .filter((event) => event.basinProjectId === domainId)
    .map((event) => normalizeFloodEvent(event))
  const { updates, added } = readEventOverrides(domainId)
  const merged = base.map((event) => normalizeFloodEvent({ ...event, ...(updates[event.eventId] || {}) }))
  const existing = new Set(merged.map((event) => event.eventId))
  const extra = added
    .filter((event) => event.basinProjectId === domainId && !existing.has(event.eventId || event.id))
    .map((event) => normalizeFloodEvent(event))
  return [...merged, ...extra]
}

function clonePlain(value) {
  return cloneWorkspaceValue(value)
}

function eventOverrideFields(event) {
  return {
    name: event.name,
    start: event.start,
    end: event.end,
    sectionId: event.sectionId,
    observedPeak: event.observedPeak,
    peakTime: event.peakTime,
    volume: event.volume,
    volumeM3: event.volumeM3,
    rainCompleteness: event.rainCompleteness,
    flowCompleteness: event.flowCompleteness,
    riverCompleteness: event.riverCompleteness,
    sourceFiles: event.sourceFiles,
    status: event.status,
    purpose: event.purpose,
    note: event.note,
    revision: event.revision,
    lineage: event.lineage,
    history: event.history,
    localDraft: event.localDraft,
    runtimeRegistered: event.runtimeRegistered,
    registeredStatus: event.registeredStatus
  }
}

function persistEventOverrides(domainId, events) {
  if (typeof localStorage === 'undefined') return
  const baseIds = new Set(
    floodEventCatalog.events
      .filter((event) => event.basinProjectId === domainId)
      .map((event) => stableFloodEventId(event))
  )
  const updates = {}
  const added = []
  events.forEach((event) => {
    if (baseIds.has(event.eventId)) updates[event.eventId] = eventOverrideFields(event)
    else added.push(clonePlain(event))
  })
  try {
    saveBusinessDraft(`${EVENT_OVERRIDE_STORAGE}${domainId}`, JSON.stringify({ schemaVersion: 1, updates, added }))
  } catch {
    // 浏览器存储空间不足时，当前页面状态仍然有效。
  }
}

function buildHydrograph(state) {
  const context = resolveDomainContext(state.domainId, { configId: state.modelConfigId, modelScale: state.modelScale })
  if (!isCaseFixtureContext(context)) return { forecast: [], obs: [], prev: [], peak: null, peakTime: '—', volume: null, metrics: null }
  const sectionId = SECTION_MAP[state.section]
  if (!sectionId?.startsWith('SEL-')) return { forecast: [], obs: [], prev: [], peak: null, peakTime: '—', volume: null, metrics: null }
  const isSelinco = state.domainId === 'selinco'
  const basePeak = isSelinco
    ? (SELINCO_BASE_PEAK[sectionId] || SELINCO_BASE_PEAK['SEL-SEC-001'])
    : sectionBasePeak(sectionId)
  const wholeLake = sectionId === 'SEL-LAKE-ALL'

  return simulateHydrograph(state.params, {
    basePeak,
    domainId: state.domainId,
    targetPeak: isSelinco,
    baseFlow: isSelinco ? (wholeLake ? 451 : 186) : undefined,
    issueFlow: isSelinco ? (wholeLake ? 451 : 186) : undefined,
    seed: EVENT_SEED[state.eventId] ?? 1,
    hours: 72,
    obsHours: 36,
    start: `${state.eventStart || state.calibrationStart}:00`
  })
}

function metricScore(metrics, criteria) {
  const scores = {
    nse: Math.max(0, Math.min(1, Number(metrics.nse) || 0)),
    peak: Math.max(0, 1 - Math.abs(Number(metrics.peakErr) || 0) / 40),
    time: Math.max(0, 1 - Math.abs(Number(metrics.timeErrH) || 0) / 6),
    volume: Math.max(0, 1 - Math.abs(Number(metrics.volErr) || 0) / 40)
  }
  const selected = Object.keys(criteria.metrics).filter((key) => criteria.metrics[key])
  const total = selected.reduce((sum, key) => sum + Number(criteria.weights[key] || 0), 0)
  if (!selected.length || !total) return 0
  const score = selected.reduce((sum, key) => sum + scores[key] * Number(criteria.weights[key] || 0), 0) / total
  return +(score * 100).toFixed(1)
}

function resultGrade(score) {
  if (score >= 85) return '拟合良好'
  if (score >= 70) return '拟合可用'
  return '建议复核'
}

function formatRunTime(date = new Date()) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function createWorkspace(context) {
  const isSelinco = context.basinId === 'selinco'
  const isNananxi = context.basinId === 'nananxi-xufan'
  const paramVersion = context.modelConfig.paramVersion
  const params = (isSelinco || isNananxi) ? { ...(VERSION_PRESETS[paramVersion] || DEFAULT_PARAMS) } : {}
  const events = createFloodEvents(context.basinId)
  const selected = events.find((event) => event.id === 'sel-2024-07') || events[0] || null

  return {
    domainId: context.basinId,
    modelScale: context.modelScale,
    modelConfigId: context.configId,
    contextKey: context.contextKey,
    section: isSelinco ? '扎加藏布入湖口' : (isNananxi ? '徐畈（二）' : ''),
    paramVersion,
    calibrationMode: 'event',
    targetMode: isSelinco ? 'section-flow' : 'section-flow',
    eventId: selected?.id || null,
    eventStart: selected?.start || null,
    eventEnd: selected?.end || null,
    events,
    quarantinedEvents: [],
    eventCatalogSchemaVersion: FLOOD_EVENT_SCHEMA_VERSION,
    eventSetVersion: buildEventSetVersion(events, floodEventCatalog.version || 'catalog'),
    calibrationStart: isSelinco ? '2021-06-01T00:00' : isNananxi ? '2017-06-01T00:00' : '',
    calibrationEnd: isSelinco ? '2023-09-30T23:00' : isNananxi ? '2017-06-15T23:00' : '',
    validationStart: isSelinco ? '2024-06-01T00:00' : isNananxi ? '2017-06-16T00:00' : '',
    validationEnd: isSelinco ? '2024-09-30T23:00' : isNananxi ? '2017-06-30T23:00' : '',
    independentStart: isSelinco ? '2025-06-01T00:00' : '',
    independentEnd: isSelinco ? '2025-09-30T23:00' : '',
    criteria: criteriaTemplate(),
    algorithm: 'SCE-UA',
    maxEvaluations: 3000,
    populationSize: 48,
    searchStrategy: 'joint',
    searchParameters: null,
    searchRangeOverrides: {},
    calibrationTarget: 'runoff',
    routingSearchParameters: null,
    jointProfile: 'reference-16', statePolicy: 'independent-warmup',
    scienceVersionId: null, scienceDescriptor: null, evaporationMode: 'constant-zero', scienceConditionsDraft: null,
    continuousOrigin: null, requiredInputBundleVersionId: null,
    candidateWarmupHours: null,
    searchSeed: 42,
    optimizerSettings: {}, sourcePlan: null, sourcePlanRequestTemplate: null, originalCalibrationPlan: null, originalPlanChanged: false,
    deliverySelection: [], configurationPending: false, deliveryProcessing: null,
    searchParameterOrder: null, eventSplitSeed: 42, eventAssignment: null,
    params,
    baseline: { ...params },
    operationMode: 'calibration',
    modelSchemeId: null, modelSchemeRevision: 0, modelSchemeLatestRevision: 0, modelSchemeName: '', schemeWindows: [], rainfallMethod: 'reuse', fixedEventSimulation: false, idwPower: null, minimumStations: null, stationSourceId: null,
    schemeSourceBundleVersionId: null, schemeRoutingVersionId: null, schemeDataMode: 'frozen-input', schemeStationNetwork: null, fullFloodCatalogLoaded: false,
    activeOperationId: null, activeOperationRequest: null, operationProgress: null,
    simulationStart: isSelinco ? '2026-06-01T00:00' : isNananxi ? '2024-01-01T00:00' : '',
    simulationEnd: isSelinco ? '2026-06-06T00:00' : isNananxi ? '2024-12-31T23:00' : '',
    warmupDays: isSelinco ? 30 : 90,
    // 资料范围由项目目录加载后写入。
    observationRange: null,
    initialStateVersionId: isSelinco ? context.modelConfig.statePrefix : null,
    outputStepHours: 1,
    simulationResult: null,
    dirty: false,
    running: false,
    runStatus: 'ready',
    activeTaskId: null,
    runMessage: isSelinco ? '当前为前端试算演示，选择洪水场次和评价指标。' : '计算服务尚未接入，可整理当前案例的资料和计算条件。',
    runResult: null,
    trialRecords: [],
    modelSchemeDraftRevision: null, editorTrialMode: 'automatic', manualRoutingPayload: null,
    runCount: 0
    ,backendContractVersion: null, backendDefinitions: [], backendMigration: null, backendInputBundleVersionId: null
  }
}

const WORKSPACE_FIELDS = [
  'modelSchemeId', 'modelSchemeRevision', 'modelSchemeLatestRevision', 'modelSchemeName', 'schemeWindows', 'rainfallMethod', 'fixedEventSimulation', 'idwPower', 'minimumStations', 'stationSourceId',
  'schemeSourceBundleVersionId', 'schemeRoutingVersionId', 'schemeDataMode', 'schemeStationNetwork', 'fullFloodCatalogLoaded', 'activeOperationId', 'activeOperationRequest', 'operationProgress',
  'domainId',
  'modelScale',
  'modelConfigId',
  'contextKey',
  'section',
  'paramVersion',
  'calibrationMode',
  'targetMode',
  'eventId',
  'eventStart',
  'eventEnd',
  'events',
  'quarantinedEvents',
  'eventCatalogSchemaVersion',
  'eventSetVersion',
  'calibrationStart',
  'calibrationEnd',
  'validationStart',
  'validationEnd',
  'independentStart',
  'independentEnd',
  'criteria',
  'algorithm',
  'maxEvaluations',
  'populationSize',
  'searchStrategy',
  'searchParameters',
  'calibrationTarget',
  'routingSearchParameters',
  'jointProfile', 'statePolicy', 'scienceVersionId', 'scienceDescriptor', 'evaporationMode', 'scienceConditionsDraft',
  'continuousOrigin', 'requiredInputBundleVersionId',
  'candidateWarmupHours',
  'searchSeed',
  'optimizerSettings', 'sourcePlan', 'sourcePlanRequestTemplate', 'originalCalibrationPlan', 'originalPlanChanged', 'searchParameterOrder', 'eventSplitSeed', 'eventAssignment', 'deliverySelection', 'configurationPending', 'deliveryProcessing',
  'params',
  'baseline',
  'operationMode',
  'simulationStart',
  'simulationEnd',
  'warmupDays',
  'observationRange',
  'initialStateVersionId',
  'outputStepHours',
  'simulationResult',
  'dirty',
  'running',
  'runStatus',
  'activeTaskId',
  'runMessage',
  'runResult',
  'trialRecords', 'modelSchemeDraftRevision', 'editorTrialMode', 'manualRoutingPayload', 'searchRangeOverrides',
  'runCount'
  , 'backendContractVersion', 'backendDefinitions', 'backendMigration', 'backendInputBundleVersionId'
]

function copyWorkspace(source) {
  return WORKSPACE_FIELDS.reduce((workspace, key) => {
    workspace[key] = clonePlain(source[key])
    return workspace
  }, {})
}

function applyWorkspace(target, workspace) {
  WORKSPACE_FIELDS.forEach((key) => {
    target[key] = clonePlain(workspace[key])
  })
}

function restoreWorkspace(base, saved) {
  if (!saved || typeof saved !== 'object') return base
  const restored = { ...base }
  WORKSPACE_FIELDS.forEach((key) => {
    if (saved[key] !== undefined) restored[key] = clonePlain(saved[key])
  })
  Object.assign(restored, restoreWorkspaceEventRecords(base.events, saved.events, base, saved, saved.quarantinedEvents))
  restored.criteria = normalizedCriteria(restored.criteria)
  if (!['selinco', 'nananxi-xufan'].includes(base.domainId) && ['新安江大桥', '兰江口', '寿昌控制断面'].includes(restored.section)) {
    restored.section = ''
    restored.params = { ...base.params }
    restored.baseline = { ...base.baseline }
    restored.initialStateVersionId = null
    restored.runResult = null
    restored.simulationResult = null
  }
  restored.eventSetVersion = buildEventSetVersion(restored.events, floodEventCatalog.version || 'catalog')
  return restored
}

function initialWorkspace() {
  return createWorkspace(resolveDomainContext('xinanjiang', { configId: 'xaj-1km' }))
}

export const useCalibrationStore = defineStore('calibration', {
  state: () => {
    const baseWorkspace = initialWorkspace()
    const savedWorkspaces = readWorkspaceRecords()
    const workspace = restoreWorkspace(baseWorkspace, savedWorkspaces[baseWorkspace.contextKey])
    return {
      ...workspace,
      workspaces: { ...savedWorkspaces, [workspace.contextKey]: copyWorkspace(workspace) }
    }
  },

  getters: {
    sectionId(state) {
      const app = useAppStore()
      const context = resolveDomainContext(state.domainId, { configId: state.modelConfigId, modelScale: state.modelScale })
      const data = projectDataForContext(context, app.projectData, { loading: app.projectDataLoading, error: app.projectDataError })
      const section = data?.sections?.sections?.find((item) => item.name === state.section || item.id === state.section)
      if (section) return section.id
      return isCaseFixtureContext(context) && SECTION_MAP[state.section]?.startsWith('SEL-') ? SECTION_MAP[state.section] : null
    },
    executionAvailability(state) {
      const app = useAppStore()
      const context = resolveDomainContext(state.domainId, { configId: state.modelConfigId, modelScale: state.modelScale })
      const operation = state.operationMode === 'simulation' ? 'continuousSimulation' : state.calibrationMode === 'continuous' ? 'continuousCalibration' : 'eventCalibration'
      if (app.contextKey !== state.contextKey) return { ready: false, reason: '率定工作区与当前案例不一致。' }
      if (app.usesModelBackend && state.domainId === 'nananxi-xufan' && (app.inputCatalogError || app.inputCatalogLoading)) return { ready: false, reason: app.inputCatalogError || '正在校验选定输入版本。' }
      return caseExecutionAvailability(context, app.projectData, operation, { loading: app.projectDataLoading, error: app.projectDataError })
    },
    paramDefs() {
      return this.backendContractVersion ? this.backendDefinitions : PARAM_DEFS
    },
    visibleSimulationResult(state) {
      return visibleWorkspaceResult(state.simulationResult, resolveDomainContext(state.domainId, { configId: state.modelConfigId, modelScale: state.modelScale }))
    },
    visibleRunResult(state) {
      return visibleWorkspaceResult(state.runResult, resolveDomainContext(state.domainId, { configId: state.modelConfigId, modelScale: state.modelScale }))
    },
    hydrograph(state) {
      const empty = { forecast: [], obs: [], prev: [], peak: null, peakTime: '—', volume: null, metrics: null }
      if (state.operationMode === 'simulation' && this.visibleSimulationResult?.hydrograph) return this.visibleSimulationResult.hydrograph
      if (this.visibleRunResult?.resultSource === 'model-backend' && state.operationMode !== 'simulation') {
        return this.visibleRunResult.hydrographs?.[state.eventId] || empty
      }
      if (!this.executionAvailability.ready || this.executionAvailability.mode !== 'frontend-fixture') return empty
      return state.operationMode === 'simulation' && this.visibleSimulationResult?.hydrograph
        ? this.visibleSimulationResult.hydrograph
        : buildHydrograph(state)
    },
    liveMetrics() {
      return formatMetric(this.hydrograph.metrics)
    },
    peakSummary() {
      const hydrograph = this.hydrograph
      return {
        peak: hydrograph.peak,
        peakTime: hydrograph.peakTime,
        nse: hydrograph.metrics?.nse ?? null,
        peakErr: hydrograph.metrics?.peakErr ?? null,
        timeErrH: hydrograph.metrics?.timeErrH ?? null,
        volErr: hydrograph.metrics?.volErr ?? null
      }
    },
    eventCounts(state) {
      return this.schemeEvents.filter((event) => event.status === 'active').reduce((counts, event) => {
        if (event.purpose === '率定') counts.calibration += 1
        if (event.purpose === '验证') counts.validation += 1
        if (event.purpose === '独立检验') counts.independent += 1
        return counts
      }, { calibration: 0, validation: 0, independent: 0 })
    },
    schemeEvents(state) {
      if (state.fullFloodCatalogLoaded) {
        const windows = state.schemeWindows || []
        const overrides = new Set(windows.map(row => row.id))
        return [...state.events.filter(row => row.status !== 'inactive' && !overrides.has(row.id)), ...windows.filter(row => row.status !== 'inactive')]
      }
      return currentInputFloodEvents(state.events, { registeredOnly: Boolean(state.backendContractVersion),
        selectedBundleVersionId: useAppStore().selectedInputBundleVersionId, registeredBundleVersionId: state.backendInputBundleVersionId,
        schemeWindows: state.schemeWindows || [] })
    },
    activeEventCount(state) {
      return state.events.filter((event) => event.status !== 'inactive').length
    },
    calibrationStatusLabel(state) {
      if (state.operationMode === 'simulation') {
        return state.running ? '模拟中' : this.visibleSimulationResult ? '模拟完成' : '待模拟'
      }
      if (state.fixedEventSimulation) return state.running ? '模拟中' : this.visibleRunResult?.simulationOnly ? '模拟完成' : state.dirty ? '设置已调整' : '待模拟'
      if (state.runStatus === 'complete' && !this.visibleRunResult) return '历史示例已隔离'
      return {
        ready: '待率定',
        running: '率定中',
        complete: '率定完成',
        'needs-run': '设置已调整',
        invalid: '设置待完善'
      }[state.runStatus] || '待率定'
    },
    contrib(state) {
      if (state.domainId === 'selinco' && this.executionAvailability.ready) {
        const rows = [
          { name: '扎加藏布', w: 0.41, note: '主要入湖贡献' },
          { name: '扎根藏布', w: 0.33, note: '主要入湖贡献' },
          { name: '阿里藏布', w: 0.17, note: '次要入湖贡献' },
          { name: '波曲藏布', w: 0.09, note: '响应较快' }
        ]
        return rows.map((row, index) => ({
          ...row,
          w: row.w.toFixed(2),
          err: `${index % 2 ? '+' : '-'}${(0.2 + index * 0.1).toFixed(1)} h`,
          cls: 'ok'
        }))
      }
      return []
    }
  },

  actions: {
    markInputSelectionChanged() {
      if (this.modelSchemeId && this.schemeSourceBundleVersionId) return
      this.dirty = true
      if (this.runResult) this.runResult.isCurrent = false
      if (this.simulationResult) this.simulationResult.isCurrent = false
      if (!this.running) this.runStatus = 'needs-run'
      this.runMessage = '输入版本已切换。旧任务、结果、参数和状态文件保持原血缘；请用新版本重新计算。'
      this.saveCurrentWorkspace()
    },
    configureBackend(backend) {
      if (backend.basinId !== this.domainId || backend.resolutionId !== this.modelScale) return
      this.backendDefinitions = backend.parameterDefinitions
      const firstMigration = !this.backendContractVersion
      const bundle = backend.inputBundleVersionId || DEFAULT_INPUT_BUNDLE_VERSION_ID
      if (this.backendInputBundleVersionId && this.backendInputBundleVersionId !== bundle) this.markInputSelectionChanged()
      this.backendInputBundleVersionId = bundle
      if (!this.fullFloodCatalogLoaded) this.events = mergeBackendFloodEvents(this.events, backend.events)
      if (!firstMigration) {
        Object.assign(this, registeredBackendEventSelection(this.events, this.eventId))
        this.syncEventCatalog()
        return
      }
      this.backendMigration = { migratedAt: new Date().toISOString(), legacyParameters: { ...this.params }, legacyAlgorithm: this.algorithm, legacyWarmupDays: this.warmupDays,
        note: '示意参数不等同于真实内核参数；保留原方案，启用后端原生参数。用户场次用途与人工修订保留。' }
      this.params = { ...backend.parameters }
      this.baseline = { ...backend.parameters }
      this.paramVersion = backend.parameterVersionId
      this.algorithm = backend.algorithms[0].id
      this.maxEvaluations = backend.defaultMaxEvaluations
      this.populationSize = 12
      if (this.warmupDays === 90) this.warmupDays = backend.defaultWarmupDays
      if (this.simulationStart === '2024-01-01T00:00' && this.simulationEnd === '2024-12-31T23:00') {
        this.simulationStart = backend.defaultSimulationPeriod.start
        this.simulationEnd = backend.defaultSimulationPeriod.end
      }
      Object.assign(this, registeredBackendEventSelection(this.events, this.eventId))
      this.backendContractVersion = '2.0'
      this.initialStateVersionId = this.initialStateVersionId?.startsWith('STATE-JOB-') ? this.initialStateVersionId : null
      this.runResult = null
      this.simulationResult = null
      this.outputStepHours = 1
      this.runMessage = '已启用真实产汇流内核与原生参数；原示意方案保留在迁移记录。结果仅供诊断。'
      this.syncEventCatalog()
    },
    applyBackendResult(request, result, task, projectData) {
      const key = request.context.contextKey
      const current = this.contextKey === key
      const workspace = current ? this : this.workspaces[key]
      if (!workspace) return
      workspace.activeTaskId = task.taskId
      workspace.running = false
      const schemeResult = request.scheme?.id && request.scheme.id === workspace.modelSchemeId
      const matchesInput = current && (schemeResult || inputResultUsesSelection(request, useAppStore()))
      const matchesRouting = current && (schemeResult || (request.routing?.versionId || 'control-split-v2') === useAppStore().selectedRoutingVersionId)
      const isCurrent = !workspace.dirty && matchesInput && matchesRouting
      workspace.runStatus = isCurrent ? 'complete' : 'needs-run'
      if (request.runType === 'simulation') {
        workspace.simulationResult = { ...result, frozenRequest: JSON.parse(JSON.stringify(request)), inputBundleVersionId: frozenInputBundleVersionId(request), completedAt: task.completedAt, startTime: request.period.startTime, endTime: request.period.endTime, isCurrent }
        workspace.runMessage = '连续模拟完成，流量与状态已保存。'
      } else if (request.runType === 'calibration') {
        if (result.calibrationTarget !== 'routing' && !workspace.trialRecords.some(row => row.taskId === task.taskId)) {
          workspace.trialRecords.push({ taskId: result.primaryTaskId || result.taskId || task.taskId, conditions: trialConditions(request, result),
            parameterVersionId: result.parameterVersionId, parameters: { ...result.bestParameters },
            completedAt: task.completedAt, manual: result.evaluationMode === 'manual', visible: true })
        }
        workspace.runCount += 1
        workspace.runResult = { ...result, taskId: result.primaryTaskId || result.taskId || task.taskId, frozenRequest: JSON.parse(JSON.stringify(request)), inputBundleVersionId: frozenInputBundleVersionId(request), metrics: result.hydrograph?.metrics, peak: result.hydrograph?.peak, peakTime: result.hydrograph?.peakTime, score: 0, grade: result.simulationOnly ? '场次模拟完成' : result.evaluationMode === 'manual' ? '人工试调完成' : '率定完成', completedAt: task.completedAt || result.completedAt || new Date().toISOString(), isCurrent }
        if (request.period.mode === 'event' && !result.hydrographs?.[workspace.eventId]) {
          const selected = request.period.events.find((event) => result.hydrographs?.[event.id])
          if (selected) {
            workspace.eventId = selected.id
            workspace.eventStart = selected.start
            workspace.eventEnd = selected.end
          }
        }
        workspace.runMessage = result.simulationOnly ? '场次模拟完成。' : result.evaluationMode === 'manual' ? '试调完成。' : '率定完成。'
      }
      if (current) this.saveCurrentWorkspace()
      else persistWorkspaceRecords(this.workspaces)
    },
    async awaitBackendTask(task, request, projectData) {
      if (backendWaits.has(task.taskId)) return backendWaits.get(task.taskId)
      const key = request.context.contextKey
      const pending = (async () => {
        try {
          const tasks = useTasksStore()
          const completed = await tasks.waitForTask(task.taskId)
          if (!['completed', 'archived'].includes(completed?.status)) throw new Error(completed?.errorMessage || '模型任务未完成。')
          const result = await tasks.getResult(task.taskId)
          const [{ states }, { versions }] = await Promise.all([modelApi('states'), modelApi('parameter-versions')])
          useStateStore().hydrateBackendStates(states)
          if (useAppStore().contextKey === key) useAppStore().projectData.backendParameterVersions = versions
          this.applyBackendResult(request, result, completed, projectData)
          if (result.stateCalculationTaskId) {
            const workspace = this.contextKey === key ? this : this.workspaces[key]
            workspace.running = true; workspace.runMessage = '参数搜索已完成，正在保存最佳参数对应的连续状态。'
            try {
              const stateTask = await tasks.waitForTask(result.stateCalculationTaskId)
              if (!['completed', 'archived'].includes(stateTask?.status)) throw new Error(stateTask?.errorMessage || '状态保存未完成')
              const stateResult = await tasks.getResult(stateTask.taskId)
              useStateStore().hydrateBackendStates((await modelApi('states')).states)
              if (workspace.runResult?.taskId === task.taskId) workspace.runResult.stateCalculation = stateResult
              workspace.runMessage = '率定完成，最佳参数对应的流量与状态已保存。'
            } catch (failure) {
              if (workspace.runResult?.taskId === task.taskId) workspace.runResult.stateCalculationError = failure.message
              workspace.runMessage = `率定计算已完成；状态保存失败：${failure.message}。请按最佳参数独立模拟。`
            } finally { workspace.running = false; if (this.contextKey === key) this.saveCurrentWorkspace() }
          }
          return { ok: true, result, task: completed }
        } catch (error) {
          const workspace = this.contextKey === key ? this : this.workspaces[key]
          if (workspace) { workspace.running = false; workspace.runStatus = 'failed'; workspace.runMessage = error.message }
          if (this.contextKey === key) this.saveCurrentWorkspace()
          return { ok: false, errors: [error.message] }
        } finally {
          backendWaits.delete(task.taskId)
        }
      })()
      backendWaits.set(task.taskId, pending)
      return pending
    },
    async runBackendRequest(request) {
      const projectData = useAppStore().projectData
      this.running = true
      this.runStatus = 'running'
      this.dirty = false
      this.runMessage = '提交真实模型任务，等待计算结果。'
      try {
        const task = await useTasksStore().submitTask(request.runType === 'simulation' ? 'continuousSimulation' : request.period.mode === 'continuous' ? 'continuousCalibration' : 'eventCalibration', request, `model:${request.context.contextKey}`)
        const workspace = this.contextKey === request.context.contextKey ? this : this.workspaces[request.context.contextKey]
        workspace.activeTaskId = task.taskId
        if (this.contextKey === request.context.contextKey) this.saveCurrentWorkspace()
        else persistWorkspaceRecords(this.workspaces)
        return await this.awaitBackendTask(task, task.request?.context ? task.request : request, projectData)
      } catch (error) {
        if (this.contextKey === request.context.contextKey) { this.running = false; this.runStatus = 'failed'; this.runMessage = error.message; this.saveCurrentWorkspace() }
        return { ok: false, errors: [error.message] }
      }
    },
    async recoverBackendTasks() {
      if (this.activeOperationId) {
        return this.awaitOperation(this.activeOperationId, this.activeOperationRequest)
      }
      const tasks = useTasksStore()
      await tasks.refresh()
      const rows = tasks.tasksForContext(this.contextKey).filter((row) => row.resultSource === 'model-backend' && ['eventCalibration', 'eventSimulation', 'continuousCalibration', 'continuousSimulation'].includes(row.runType))
      const task = recoverableCalculation(rows, this)
      if (task && backendWaits.has(task.taskId)) return
      if (!task || this.runResult?.taskId === task.taskId || this.simulationResult?.taskId === task.taskId) {
        if (!task || !['queued', 'running', 'cancelling'].includes(task.status)) { this.running = false; this.saveCurrentWorkspace() }
        return
      }
      if (['queued', 'running', 'cancelling'].includes(task.status)) {
        this.activeTaskId = task.taskId
        this.running = true
        this.awaitBackendTask(task, task.request, useAppStore().projectData)
      } else if (['completed', 'archived'].includes(task.status)) {
        this.applyBackendResult(task.request, await tasks.getResult(task.taskId), task, useAppStore().projectData)
      } else {
        this.running = false
        this.runStatus = task.status
        this.runMessage = task.errorMessage || task.stageLabel
      }
    },
    syncEventCatalog() {
      this.eventCatalogSchemaVersion = FLOOD_EVENT_SCHEMA_VERSION
      this.eventSetVersion = buildEventSetVersion(this.events, this.backendInputBundleVersionId || floodEventCatalog.version || 'catalog')
      persistEventOverrides(this.domainId, this.events)
      this.saveCurrentWorkspace()
    },
    saveCurrentWorkspace() {
      if (!this.contextKey) return
      this.workspaces[this.contextKey] = copyWorkspace(this)
      persistWorkspaceRecords(this.workspaces)
    },
    loadFullFloodCatalog(events = []) {
      const current = new Map(this.events.map(row => [row.id, row]))
      this.events = events.map(row => normalizeFloodEvent({ ...row,
        basinProjectId: this.domainId, purpose: current.get(row.id)?.purpose || '未使用',
        runtimeRegistered: true, registeredStatus: 'active', status: row.status === 'inactive' ? 'inactive' : 'active' }))
      this.fullFloodCatalogLoaded = true
      if (!this.events.some(row => row.id === this.eventId)) {
        const first = this.events[0]
        this.eventId = first?.id || null; this.eventStart = first?.start || null; this.eventEnd = first?.end || null
      }
      this.saveCurrentWorkspace()
    },
    resetCalibrationScheme(defaults = {}, backend = {}) {
      this.modelSchemeId = null; this.modelSchemeRevision = 0; this.modelSchemeLatestRevision = 0; this.modelSchemeName = ''
      this.modelSchemeDraftRevision = null; this.editorTrialMode = 'automatic'; this.manualRoutingPayload = null
      this.schemeWindows = []; this.runResult = null; this.simulationResult = null; this.activeTaskId = null
      this.activeOperationId = null; this.activeOperationRequest = null; this.operationProgress = null
      this.events = this.events.map(row => ({ ...row, purpose: '未使用' }))
      this.operationMode = 'calibration'; this.calibrationMode = 'event'; this.fixedEventSimulation = false
      this.params = { ...(backend?.parameters || this.baseline) }; this.baseline = { ...this.params }
      this.paramVersion = backend?.parameterVersionId || null
      this.algorithm = backend?.algorithms?.[0]?.id || this.algorithm
      this.maxEvaluations = 120
      this.populationSize = 12; this.searchStrategy = 'joint'; this.searchSeed = 42
      this.searchParameters = Object.fromEntries((backend?.searchParameterDefinitions || []).map(row=>[row.key,{minimum:row.minimum,maximum:row.maximum}]))
      this.routingSearchParameters = {}; this.searchRangeOverrides = {}; this.calibrationTarget = 'joint'; this.jointProfile='reference-16'
      this.optimizerSettings = {}; this.searchParameterOrder = null; this.criteria = criteriaTemplate()
      this.requiredInputBundleVersionId = null; this.eventAssignment = null; this.trialRecords = []
      this.scienceVersionId = null; this.scienceDescriptor = null; this.candidateWarmupHours = null
      this.scienceConditionsDraft = null
      this.statePolicy = 'independent-warmup'; this.continuousOrigin = null; this.initialStateVersionId = null
      this.sourcePlan = null; this.sourcePlanRequestTemplate = null; this.originalCalibrationPlan = null
      this.originalPlanChanged = false
      this.deliverySelection = []; this.configurationPending = false; this.deliveryProcessing = null
      this.evaporationMode = 'monthly-climatology-assumption'; this.warmupDays = backend?.defaultWarmupDays ?? 7
      this.rainfallMethod = defaults.rainfallMethod || 'stationIdw'; this.stationSourceId = Object.hasOwn(defaults,'stationSourceId') ? defaults.stationSourceId : 'LOCAL_OBSERVATIONS'
      this.schemeSourceBundleVersionId = defaults.sourceBundleVersionId || 'HISTORICAL_SPATIAL_V1'
      this.schemeDataMode = 'historical-database'; this.schemeStationNetwork = null
      this.schemeRoutingVersionId = defaults.routingVersionId || 'control-split-v2'
      this.idwPower = defaults.idwPower ?? 2; this.minimumStations = defaults.minimumStations ?? 1
      this.dirty = true; this.running = false; this.runStatus = 'needs-run'; this.runMessage = ''
      this.saveCurrentWorkspace()
    },
    async runOperationRequest(request, operation = 'calibration', options = {}) {
      this.running = true; if(operation!=='manualTrial')this.dirty = false; this.runStatus = 'running'; this.runMessage = ''
      try {
        const task = await modelApi('operations', { method: 'POST', body: { operation, requestId: request.requestId, request, rainfallMethod: this.rainfallMethod, ...options }, timeoutMs: 60000 })
        this.activeOperationId = task.operationId || task.taskId || task.id
        this.activeOperationRequest = clonePlain(request); this.operationProgress = task
        this.saveCurrentWorkspace()
        return await this.awaitOperation(this.activeOperationId, request)
      } catch (error) {
        this.running = false; this.runStatus = 'failed'; this.runMessage = error.message; this.saveCurrentWorkspace()
        return { ok: false, errors: [error.message] }
      }
    },
    async awaitOperation(operationId, request) {
      if (backendWaits.has(operationId)) return backendWaits.get(operationId)
      const key = request?.context?.contextKey || this.contextKey
      const workspace = () => this.contextKey === key ? this : this.workspaces[key]
      let terminal = false
      const pending = (async () => {
        try {
          let task = await modelApi(`operations/${operationId}`)
          while (['queued', 'running', 'cancelling'].includes(task.status)) {
            const current = workspace()
            if (current) { current.operationProgress = task; current.running = true }
            await new Promise(resolve => setTimeout(resolve, 1000))
            task = await modelApi(`operations/${operationId}`)
          }
          if (!['completed', 'archived'].includes(task.status)) {
            terminal = true
            const failure = new Error(task.errorMessage || task.error || (task.status === 'cancelled' ? '计算已停止' : '计算未完成'))
            failure.issues = task.issues || task.result?.issues || []
            throw failure
          }
          const result = await modelApi(`operations/${operationId}/result`)
          const executedRequest = result.frozenRequest || task.runConfig?.request || request
          const primaryId = result.primaryTaskId || result.taskId || task.primaryTaskId || operationId
          this.applyBackendResult(executedRequest, result, { ...task, taskId: primaryId }, useAppStore().projectData)
          terminal = true
          const current = workspace()
          if (current) {
            current.activeOperationId = null; current.activeOperationRequest = null; current.operationProgress = task
            // Completed curves own their parameters. Adopting a named set or
            // replacing automatic initial values is always an explicit action.
          }
          if (this.contextKey === key) this.saveCurrentWorkspace()
          return { ok: true, result, task }
        } catch (error) {
          const current = workspace()
          if (current) { current.running = false; current.runStatus = error.message === '计算已停止' ? 'cancelled' : 'failed'; current.runMessage = error.message;
            current.operationProgress = { ...current.operationProgress, status: current.runStatus, issues: error.issues || [] };
            if (terminal) { current.activeOperationId = null; current.activeOperationRequest = null }
            else current.operationProgress.status = 'unavailable' }
          if (this.contextKey === key) this.saveCurrentWorkspace()
          return { ok: false, errors: [error.message], issues: error.issues || [] }
        } finally { backendWaits.delete(operationId) }
      })()
      backendWaits.set(operationId, pending)
      return pending
    },
    setContext(domainId, modelScale, modelConfigId) {
      const context = resolveDomainContext(domainId, { configId: modelConfigId, modelScale })
      if (this.contextKey === context.contextKey) return context

      this.saveCurrentWorkspace()
      if (!this.workspaces[context.contextKey]) {
        const saved = readWorkspaceRecords()[context.contextKey]
        this.workspaces[context.contextKey] = copyWorkspace(restoreWorkspace(createWorkspace(context), saved))
      }
      applyWorkspace(this, this.workspaces[context.contextKey])
      return context
    },
    enrichEvents(packages = []) {
      if (this.backendContractVersion) return
      if (!Array.isArray(packages) || !packages.length) return
      const next = mergeFloodEventPackages(this.events, packages)
      const changed = JSON.stringify(next) !== JSON.stringify(this.events)
      if (!changed) return
      this.events = next
      this.syncEventCatalog()
    },
    syncEventCatalogEvents(catalogEvents = []) {
      if (this.backendContractVersion) return
      if (!Array.isArray(catalogEvents) || !catalogEvents.length) return
      const current = new Map(this.events.map((event) => [event.eventId || event.id, event]))
      const loaded = catalogEvents
        .filter((event) => event.basinProjectId === this.domainId)
        .map((event) => normalizeFloodEvent({ ...event, ...(current.get(event.eventId || event.id) || {}) }))
      const loadedIds = new Set(loaded.map((event) => event.eventId || event.id))
      const manual = this.events.filter((event) => event.basinProjectId === this.domainId && !loadedIds.has(event.eventId || event.id))
      const next = [...loaded, ...manual]
      if (JSON.stringify(next) === JSON.stringify(this.events)) return
      this.events = next
      this.syncEventCatalog()
    },
    setDomain(domainId) {
      const context = resolveDomainContext(domainId)
      return this.setContext(context.basinId, context.modelScale, context.configId)
    },
    setModelScale(modelScale) {
      const context = resolveDomainContext(this.domainId, { modelScale })
      return this.setContext(context.basinId, context.modelScale, context.configId)
    },
    setOperationMode(mode) {
      const operation = mode === 'simulation' ? 'simulation' : 'calibration'
      if (this.operationMode === operation) return
      this.operationMode = operation
      this.markNeedsRun('')
      this.saveCurrentWorkspace()
    },
    setSimulationField(key, value) {
      if (!['simulationStart', 'simulationEnd', 'warmupDays', 'initialStateVersionId', 'outputStepHours'].includes(key)) return
      if (key === 'warmupDays') {
        this.setWarmupDays(value)
        return
      }
      this[key] = key === 'outputStepHours' ? Math.max(1, Math.round(Number(value) || 1)) : value
      this.simulationResult = null
      this.runStatus = 'needs-run'
      this.runMessage = this.operationMode === 'simulation'
        ? '连续模拟条件已调整，请重新运行。'
        : '模型工作区条件已调整，请重新率定或模拟。'
      this.dirty = true
      this.saveCurrentWorkspace()
    },
    setWarmupDays(value) {
      if (this.scienceVersionId) return
      const days = Math.max(1, Math.round(Number(value) || 1))
      if (this.warmupDays === days) return
      this.warmupDays = days
      this.simulationResult = null
      this.runStatus = 'needs-run'
      this.runMessage = this.operationMode === 'simulation'
        ? '连续模拟预热期已调整，请重新运行。'
        : '预热期已调整，请重新率定或模拟。'
      this.dirty = true
      this.saveCurrentWorkspace()
    },
    setObservationRange(range = {}) {
      if (this.domainId !== 'nananxi-xufan') return
      const start = String(range.start || '').replace(' ', 'T').slice(0, 16)
      const end = String(range.end || '').replace(' ', 'T').slice(0, 16)
      if (!start || !end || start >= end) return
      this.observationRange = { start, end }
      this.saveCurrentWorkspace()
    },
    loadFromTaskConfig(runType, config = {}) {
      this.stationSourceId = config.inputs?.stationSourceId || null
      this.fixedEventSimulation = runType === 'eventSimulation' || Boolean(config.calibration?.simulationOnly)
      if (config.criteria || config.calibration?.objectives) this.criteria = clonePlain(config.criteria || config.calibration.objectives)
      this.paramVersion = config.calibration?.initialParameterVersionId || config.parameterVersionId || this.paramVersion
      this.optimizerSettings = clonePlain(config.optimizerSettings || {})
      this.sourcePlan = clonePlain(config.sourcePlan || null)
      this.sourcePlanRequestTemplate = this.sourcePlan ? clonePlain(config) : null
      this.searchParameterOrder = clonePlain(config.searchParameterOrder || null)
      this.originalPlanChanged = false
      this.eventAssignment = clonePlain(config.eventAssignment || null)
      this.jointProfile = config.jointProfile || config.calibration?.jointProfile || 'reference-16'
      this.statePolicy = config.statePolicy || config.calibration?.statePolicy || 'independent-warmup'
      this.scienceVersionId = config.scienceVersionId || config.science?.versionId || null
      this.scienceDescriptor = clonePlain(config.scienceDescriptor || (this.scienceVersionId ? { ...config.lineage, scienceVersionId: this.scienceVersionId } : null))
      this.evaporationMode = config.evaporationMode || config.inputs?.evaporation?.mode || 'constant-zero'
      this.continuousOrigin = config.continuousOrigin || config.calibration?.continuousOrigin || config.period?.continuousOrigin || config.scienceDescriptor?.resolvedContinuousOrigin || null
      this.requiredInputBundleVersionId = config.requiredInputBundleVersionId || config.inputs?.bundleVersionId || null
      this.candidateWarmupHours = Number.isFinite(config.actualWarmupHours) ? config.actualWarmupHours
        : Number.isFinite(config.warmupDays) && this.scienceVersionId ? config.warmupDays * 24 : null
      if (config.params) {
        this.params = { ...(this.backendContractVersion ? {} : DEFAULT_PARAMS), ...clonePlain(config.params) }
        this.baseline = { ...this.params }
      }
      const sectionName = Object.entries(SECTION_MAP).find(([, id]) => id === config.sectionId)?.[0]
      if (sectionName) this.section = sectionName

      if (runType === 'continuousSimulation') {
        this.operationMode = 'simulation'
        this.simulationStart = String(config.startTime || this.simulationStart).replace(' ', 'T')
        this.simulationEnd = String(config.endTime || this.simulationEnd).replace(' ', 'T')
        if (Number.isFinite(config.warmupDays)) this.warmupDays = config.warmupDays
        this.initialStateVersionId = config.stateVersionId || null
        this.outputStepHours = Number(config.outputStepHours) || this.outputStepHours
        this.simulationResult = null
        this.runMessage = '已从运行档案带入连续模拟条件，请复核后执行。'
      } else {
        this.operationMode = 'calibration'
        this.calibrationMode = runType === 'continuousCalibration' ? 'continuous' : 'event'
        if (config.algorithm) this.algorithm = config.algorithm
        if (config.maxEvaluations) this.maxEvaluations = config.maxEvaluations
        if (config.populationSize) this.populationSize = config.populationSize
        this.searchStrategy = config.searchStrategy || 'joint'
        this.searchParameters = clonePlain(config.searchParameters ?? config.calibration?.searchParameters ?? null)
        this.calibrationTarget = config.calibrationTarget || config.calibration?.target || 'runoff'
        this.routingSearchParameters = clonePlain(config.routingSearchParameters ?? config.calibration?.routingSearchParameters ?? null)
        this.searchSeed = config.searchSeed ?? 42
        if (Number.isFinite(config.warmupDays)) this.warmupDays = config.warmupDays
        if (Array.isArray(config.events)) {
          const selected = new Map(config.events.map((row) => [row.id, row]))
          for (const row of config.events) if (!this.events.some(event => event.id === row.id)) {
            if (this.fullFloodCatalogLoaded) {
              this.schemeWindows ||= []
              if (!this.schemeWindows.some(event => event.id === row.id)) this.schemeWindows.push({ ...row,
                runtimeRegistered: false, status: 'active', sourceBundleVersionId: config.inputs?.sourceBundleVersionId || config.inputs?.bundleVersionId })
            } else this.events.push(normalizeFloodEvent({ ...row, runtimeRegistered: false, status: 'active' }))
          }
          this.events = this.events.map((row) => normalizeFloodEvent({ ...row, ...(selected.get(row.id) || {}),
            purpose: selected.get(row.id)?.purpose || '未使用', status: row.status, runtimeRegistered: row.runtimeRegistered,
            registeredStatus: row.registeredStatus }))
        }
        if (config.criteria) this.criteria = normalizedCriteria(config.criteria)
        if (config.eventId) this.setEvent(config.eventId)
        if (config.period) {
          for (const key of ['calibrationStart', 'calibrationEnd', 'validationStart', 'validationEnd', 'independentStart', 'independentEnd']) {
            if (config.period[key] !== undefined) this[key] = config.period[key]
          }
        }
        this.runResult = null
        this.runMessage = '已从运行档案带入率定条件，请复核后执行。'
      }
      this.activeTaskId = null
      this.running = false
      this.runStatus = 'needs-run'
      this.dirty = true
      this.saveCurrentWorkspace()
    },
    markNeedsRun(message = '计算条件已更新。') {
      if (!backendWaits.has(this.activeTaskId)) this.running = false
      this.runStatus = 'needs-run'
      this.runMessage = this.originalPlanChanged ? `原方案已调整。${message}` : message
      this.dirty = true
      if (this.runResult) this.runResult = { ...this.runResult, isCurrent: false }
      if (this.simulationResult) this.simulationResult = { ...this.simulationResult, isCurrent: false }
    },
    detachOriginalPlan() {
      if (!this.sourcePlan) return
      this.sourcePlan = null
      this.sourcePlanRequestTemplate = null
      this.originalPlanChanged = true
      // Editing detaches delivery identity, not its numerical choices.
      if (this.optimizerSettings) {
        const { initializationPolicy, initialCandidates, effectiveInitialCandidates, ...settings } = this.optimizerSettings
        this.optimizerSettings = settings
      }
    },
    setParam(key, value) {
      if (!(key in this.params)) return
      const definition = this.paramDefs.find((item) => item.key === key)
      let normalized = Number(value)
      if (!Number.isFinite(normalized)) return
      if (definition) normalized = Math.min(definition.max, Math.max(definition.min, normalized))
      this.detachOriginalPlan()
      this.params[key] = definition?.step < 1 ? +normalized.toFixed(this.backendContractVersion ? 8 : 3) : +normalized.toFixed(1)
      this.markNeedsRun('参数已调整')
      this.saveCurrentWorkspace()
    },
    resetParams() {
      this.params = { ...this.baseline }
      this.markNeedsRun('已恢复参数方案')
    },
    async loadVersion(version) {
      if (this.backendContractVersion) {
        const app = useAppStore()
        const row = parameterLibraryFor(app.projectData, this.domainId, this.modelScale, app.activeContext).simulationRateLibrary?.versions?.find((item) => item.id === version)
        const parameters = row?.parameters || (version === app.backendCase?.parameterVersionId ? app.backendCase.parameters : null)
        if (!parameters) { this.runMessage = '参数方案尚未登记，请刷新后重新选择。'; return false }
        if (row?.calibrationTarget === 'joint' && !row.routingCandidate?.routingVersionId) {
          this.runMessage = '联合候选缺少汇流版本，不能仅采用产流参数。'
          return false
        }
        const requiredInput = row?.requiredInputBundleVersionId || row?.inputBundleVersionId || row?.lineage?.inputBundleVersionId || null
        if (requiredInput) {
          const requestedContext = this.contextKey
          try {
            await app.selectInputBundleVersion(requiredInput)
            if (this.contextKey !== requestedContext) throw new Error('工作区已切换，请在原案例重新选择该候选')
          } catch (error) {
            this.runMessage = `该候选要求资料 ${requiredInput}；未载入候选：${error.message}`
            return false
          }
        }
        this.params = { ...parameters }; this.baseline = { ...parameters }; this.paramVersion = version
        this.sourcePlan = null; this.sourcePlanRequestTemplate = null; this.originalCalibrationPlan = clonePlain(row?.originalCalibrationPlan || null)
        this.originalPlanChanged = false; this.searchParameterOrder = null; this.optimizerSettings = {}
        this.requiredInputBundleVersionId = requiredInput
        this.candidateWarmupHours = Number.isFinite(row?.actualWarmupHours) ? row.actualWarmupHours : null
        if (row?.routingCandidate?.routingVersionId) app.updateWorkContext({ selectedRoutingVersionId: row.routingCandidate.routingVersionId })
        if (row?.science?.versionId) {
          this.scienceVersionId = row.science.versionId
          this.scienceDescriptor = clonePlain({ ...row.scienceSnapshot?.manifest, ...row.lineage,
            scienceVersionId: row.science.versionId, petMode: row.petMode,
            statePolicy: row.scienceSnapshot?.manifest?.statePolicy || row.scienceDescriptor?.statePolicy || { strategy: row.statePolicy },
            resolvedContinuousOrigin: row.continuousOrigin || row.scienceDescriptor?.resolvedContinuousOrigin || null })
          this.statePolicy = row.statePolicy || this.scienceDescriptor.statePolicy?.strategy || 'independent-warmup'
          this.warmupDays = (this.candidateWarmupHours ?? (Number(this.scienceDescriptor.statePolicy?.warmupHours) || 168)) / 24
          this.evaporationMode = 'science-version'
        } else {
          this.scienceVersionId = null; this.scienceDescriptor = null
          this.statePolicy = 'independent-warmup'; this.evaporationMode = 'monthly-climatology-assumption'
        }
        this.continuousOrigin = row?.continuousOrigin || row?.scienceDescriptor?.resolvedContinuousOrigin || null
        if (row?.calibrationTarget === 'joint') {
          this.calibrationTarget = 'joint'; this.searchStrategy = 'joint'
          this.jointProfile = row.jointProfile || 'reference-16'
          this.searchParameters = null; this.routingSearchParameters = null
        }
        this.markNeedsRun(row?.science?.versionId ? '已采用产流、汇流与科学条件，请复核后重新计算。' : '已选择真实参数版本，请重新计算。')
        this.saveCurrentWorkspace()
        return true
      }
      const base = String(version).replace(/-tune.*$/, '')
      const preset = ['selinco', 'nananxi-xufan'].includes(this.domainId) ? VERSION_PRESETS[base] || DEFAULT_PARAMS : {}
      this.paramVersion = base
      this.params = { ...preset }
      this.baseline = { ...preset }
      this.markNeedsRun('已切换参数方案')
    },
    async restoreOriginalPlan(version, operation = 'search') {
      if (this.running) return false
      const app = useAppStore()
      const context = this.contextKey
      try {
        const { plan, request } = await modelApi(`parameter-versions/${encodeURIComponent(version)}/original-plan?operation=${operation}`)
        if (!plan || !request || request.context?.contextKey !== context) throw new Error('原方案与当前流域不一致')
        await app.selectInputBundleVersion(request.inputs.bundleVersionId)
        if (this.contextKey !== context) throw new Error('工作区已切换，请在原流域恢复方案')
        app.updateWorkContext({ selectedRoutingVersionId: request.routing.versionId })
        this.loadFromTaskConfig('eventCalibration', normalizeBackendTask({ runConfig: request }).runConfig)
        this.originalCalibrationPlan = clonePlain(plan)
        this.sourcePlan = clonePlain(request.sourcePlan)
        this.sourcePlanRequestTemplate = clonePlain(request)
        this.paramVersion = version
        this.eventSplitSeed = plan.sourceSplitSeed ?? 42
        this.eventAssignment = clonePlain(request.period.eventAssignment || null)
        this.candidateWarmupHours = plan.actualWarmupHours ?? request.period.warmupDays * 24
        this.initialStateVersionId = null
        this.markNeedsRun('已恢复原方案的起始参数、洪水划分和搜索设置，请复核后确认率定。')
        this.saveCurrentWorkspace()
        return operation === 'fixed-replay' ? request : true
      } catch (error) {
        this.runMessage = `未恢复原方案：${error.message}`
        return false
      }
    },
    setSection(section) {
      if (this.section === section) return
      this.section = String(section || '')
      this.markNeedsRun('率定对象已调整，请重新率定。')
      this.saveCurrentWorkspace()
    },
    setTargetMode(mode) {
      if (!mode || this.targetMode === mode) return
      this.targetMode = mode
      if (this.domainId === 'selinco' && mode !== 'section-flow') this.section = '色林错全湖'
      this.markNeedsRun('拟合依据已调整，请重新率定。')
    },
    setCalibrationMode(mode) {
      const normalized = mode === 'continuous' ? 'continuous' : 'event'
      if (this.calibrationMode === normalized) return
      this.calibrationMode = normalized
      if (normalized === 'continuous' && this.operationMode !== 'simulation' && !this.scienceVersionId && !this.sourcePlan) {
        this.statePolicy = 'continuous-origin'
        if (!this.continuousOrigin && this.calibrationStart) {
          this.continuousOrigin = new Date(Date.parse(`${this.calibrationStart.replace(' ', 'T')}Z`) - Number(this.warmupDays) * 86400000).toISOString().slice(0, 16)
        }
      }
      this.markNeedsRun('率定方式已调整，请重新率定。')
    },
    setEvent(id, start = null, end = null) {
      const event = this.schemeEvents.find((item) => item.id === id) || this.events.find((item) => item.id === id)
      if (!event) return
      const nextStart = start || event.start
      const nextEnd = end || event.end
      const changed = this.eventId !== event.id || this.eventStart !== nextStart || this.eventEnd !== nextEnd
      this.eventId = event.id
      this.eventStart = nextStart
      this.eventEnd = nextEnd
      if (changed) {
        const computedEvent = this.runResult?.resultSource === 'model-backend' && this.runResult.hydrographs?.[id]
        if (computedEvent && nextStart === event.start && nextEnd === event.end) this.saveCurrentWorkspace()
        else this.markNeedsRun('洪水场次已切换，请复核率定条件。')
        this.saveCurrentWorkspace()
      }
    },
    setEventPurpose(id, purpose) {
      const event = this.schemeEvents.find((item) => item.id === id)
      if (!event || event.status === 'inactive' || !FLOOD_EVENT_PURPOSES.includes(purpose)) return
      if (event.purpose === purpose) return
      this.detachOriginalPlan()
      event.purpose = purpose
      this.markNeedsRun('洪水场次用途已调整，请重新率定。')
      this.syncEventCatalog()
    },
    setEventPurposes(assignments = []) {
      const byId = new Map(assignments.map((item) => [item.id, item.purpose]))
      let changed = 0
      this.schemeEvents.forEach((event) => {
        const purpose = byId.get(event.id)
        if (event.status === 'inactive' || !purpose || !FLOOD_EVENT_PURPOSES.includes(purpose) || event.purpose === purpose) return
        event.purpose = purpose
        changed += 1
      })
      if (changed) {
        this.detachOriginalPlan()
        this.markNeedsRun('洪水场次用途已调整，请重新率定。')
        this.syncEventCatalog()
      }
      return changed
    },
    setEventStatus(id, status) {
      const event = this.events.find((item) => item.id === id)
      if (!event || !['active', 'draft', 'pending', 'inactive'].includes(status) || event.status === status) return false
      if (this.backendContractVersion && status !== 'inactive') {
        status = event.runtimeRegistered !== true ? 'draft' : event.registeredStatus || 'pending'
      }
      event.status = status
      if (status === 'inactive') event.purpose = '未使用'
      this.markNeedsRun(status === 'inactive' ? '洪水场次已停用，当前率定不会使用。' : '洪水场次状态已更新，请复核率定样本。')
      this.syncEventCatalog()
      return true
    },
    updateEvent(id, updates = {}) {
      const event = this.events.find((item) => item.id === id)
      if (!event) return { ok: false, error: '未找到洪水场次。' }
      const start = updates.start || event.start
      const end = updates.end || event.end
      if (!start || !end || start >= end) return { ok: false, error: '结束时间应晚于开始时间。' }
      const sectionError = validateFloodWindow(this.domainId, start, end, updates.sectionId || event.sectionId)
      if (sectionError) return { ok: false, error: sectionError }
      const overlap = this.events.find((item) => item.id !== id && item.status !== 'inactive' && start <= item.end && end >= item.start)
      const timeChanged = start !== event.start || end !== event.end
      const derived = updates.derivedStats || null
      const history = [...(event.history || []), {
        revision: event.revision,
        changedAt: new Date().toISOString(),
        reason: updates.changeReason || '场次信息更新',
        snapshot: clonePlain(event)
      }]
      const next = normalizeFloodEvent({
        ...event,
        ...updates,
        ...(derived ? {
          observedPeak: derived.observedPeakFlowM3s,
          peakTime: derived.observedPeakTime,
          volume: derived.volumeM3,
          volumeM3: derived.volumeM3,
          rainCompleteness: derived.rainCompleteness,
          flowCompleteness: derived.flowCompleteness,
          riverCompleteness: derived.riverCompleteness
        } : {}),
        ...(timeChanged && !derived ? { observedPeak: null, peakTime: null, volume: null, volumeM3: null, status: 'pending' } : {}),
        id: event.id,
        eventId: event.eventId,
        basinProjectId: event.basinProjectId,
        sourceFloodNo: event.sourceFloodNo,
        revision: (Number(event.revision) || 1) + 1,
        history
      })
      const index = this.events.findIndex((item) => item.id === id)
      this.events.splice(index, 1, next)
      if (this.eventId === id) this.setEvent(id, next.start, next.end)
      this.markNeedsRun('洪水场次资料已修改，请复核率定样本。')
      this.syncEventCatalog()
      return {
        ok: true,
        event: next,
        warnings: [
          overlap ? `与${overlap.name || overlap.eventId}存在时间重叠，请核查是否需要拆分或合并。` : null,
          observationCoverageWarning(this.observationRange, start, end)
        ].filter(Boolean)
      }
    },
    addEvent({ start, end, name, sectionId, note, derivedStats = null }) {
      if (!start || !end || start >= end) return { ok: false, error: '结束时间应晚于开始时间。' }
      const sectionError = validateFloodWindow(this.domainId, start, end, sectionId)
      if (sectionError) return { ok: false, error: sectionError }
      const overlap = this.events.find((item) => item.status !== 'inactive' && start <= item.end && end >= item.start)
      const prefix = this.domainId === 'selinco' ? 'sel' : this.domainId === 'nananxi-xufan' ? 'xf' : 'xaj'
      const stamp = start.replace(/[-:T]/g, '').slice(0, 12)
      let id = `${prefix}-manual-${stamp || Date.now()}`
      let index = 2
      while (this.events.some((event) => event.id === id)) {
        id = `${prefix}-manual-${stamp || Date.now()}-${index}`
        index += 1
      }
      const event = normalizeFloodEvent({
        id,
        eventId: id,
        name: name?.trim() || `${start.slice(0, 10).replaceAll('-', '年').replace(/年(\d{2})$/, '月')}洪水`,
        basinProjectId: this.domainId,
        sourceFloodNo: `MANUAL-${stamp || Date.now()}`,
        sectionId,
        purpose: '未使用',
        status: derivedStats?.observedPeakFlowM3s != null ? 'draft' : 'pending',
        start,
        end,
        observedPeak: derivedStats?.observedPeakFlowM3s ?? null,
        peakTime: derivedStats?.observedPeakTime || null,
        volume: derivedStats?.volumeM3 ?? null,
        volumeM3: derivedStats?.volumeM3 ?? null,
        rainCompleteness: derivedStats?.rainCompleteness ?? null,
        flowCompleteness: derivedStats?.flowCompleteness ?? null,
        riverCompleteness: derivedStats?.riverCompleteness ?? null,
        note: note?.trim() || '人工新增，待资料复核',
        source: 'frontend-manual',
        sourceFiles: { rain: '本地逐小时雨量资料', river: '本地出口水文资料' },
        history: []
      })
      this.events.unshift(event)
      this.setEvent(event.id, event.start, event.end)
      this.markNeedsRun('已新增洪水场次，当前为待复核状态。')
      this.syncEventCatalog()
      return {
        ok: true,
        event,
        warnings: [
          overlap ? `与${overlap.name || overlap.eventId}存在时间重叠，请核查是否需要拆分或合并。` : null,
          observationCoverageWarning(this.observationRange, start, end)
        ].filter(Boolean)
      }
    },
    splitEvent(id, splitAt, rightDerivedStats = null, leftDerivedStats = null) {
      const source = this.events.find((item) => item.id === id)
      if (!source || !splitAt || splitAt <= source.start || splitAt >= source.end) return { ok: false, error: '拆分时刻必须位于场次起止时间之间。' }
      const left = this.addEvent({
        start: source.start,
        end: splitAt,
        name: `${source.name}（前段）`,
        sectionId: source.sectionId,
        note: `由${source.eventId}拆分生成`,
        derivedStats: leftDerivedStats
      })
      const right = this.addEvent({
        start: splitAt,
        end: source.end,
        name: `${source.name}（后段）`,
        sectionId: source.sectionId,
        note: `由${source.eventId}拆分生成`,
        derivedStats: rightDerivedStats
      })
      if (!left.ok || !right.ok) return { ok: false, error: left.error || right.error || '拆分失败。' }
      const sourceIndex = this.events.findIndex((item) => item.id === id)
      this.events[sourceIndex] = normalizeFloodEvent({
        ...source,
        status: 'inactive',
        purpose: '未使用',
        revision: (Number(source.revision) || 1) + 1,
        lineage: { ...(source.lineage || {}), supersededBy: [left.event.eventId, right.event.eventId], relation: 'split' }
      })
      this.syncEventCatalog()
      return { ok: true, source: this.events[sourceIndex], events: [left.event, right.event] }
    },
    mergeEvents(ids = [], name = '') {
      const sources = ids.map((item) => this.events.find((event) => event.id === item)).filter(Boolean)
      if (sources.length < 2) return { ok: false, error: '合并至少需要两场洪水。' }
      const start = sources.reduce((value, event) => event.start < value ? event.start : value, sources[0].start)
      const end = sources.reduce((value, event) => event.end > value ? event.end : value, sources[0].end)
      const created = this.addEvent({
        start,
        end,
        name: name || `${sources[0].name}合并场次`,
        sectionId: sources[0].sectionId,
        note: `由${sources.map((event) => event.eventId).join('、')}合并生成`
      })
      if (!created.ok) return created
      const sourceIds = sources.map((event) => event.eventId)
      sources.forEach((source) => {
        const index = this.events.findIndex((event) => event.id === source.id)
        if (index >= 0) this.events[index] = normalizeFloodEvent({
          ...source,
          status: 'inactive',
          purpose: '未使用',
          revision: (Number(source.revision) || 1) + 1,
          lineage: { ...(source.lineage || {}), supersededBy: [created.event.eventId], relation: 'merge', mergedInto: created.event.eventId }
        })
      })
      const createdIndex = this.events.findIndex((event) => event.id === created.event.id)
      if (createdIndex >= 0) this.events[createdIndex] = normalizeFloodEvent({
        ...this.events[createdIndex],
        lineage: { ...(this.events[createdIndex].lineage || {}), sourceEventIds: sourceIds, relation: 'merge' }
      })
      this.syncEventCatalog()
      return { ok: true, event: this.events[createdIndex], sources }
    },
    setObjectiveMetric(key, enabled) {
      if (!(key in this.criteria.metrics)) return
      this.detachOriginalPlan()
      this.criteria.metrics[key] = Boolean(enabled)
      this.markNeedsRun('评价指标已调整，请重新率定。')
    },
    setObjectiveWeight(key, value) {
      if (!(key in this.criteria.weights)) return
      this.detachOriginalPlan()
      const normalized = Math.max(0, Math.min(1, Number(value) || 0))
      this.criteria.weights[key] = +normalized.toFixed(2)
      this.markNeedsRun('评价指标权重已调整，请重新率定。')
    },
    setObjectiveTolerance(key, value) {
      if (!(key in this.criteria.tolerances)) return
      this.detachOriginalPlan()
      this.criteria.tolerances[key] = Math.max(0, Number(value) || 0)
      this.markNeedsRun('参考阈值已调整，请重新率定。')
    },
    setOptimizerConfig(key, value) {
      if (!['algorithm', 'maxEvaluations', 'populationSize', 'searchStrategy', 'searchSeed'].includes(key)) return
      this.detachOriginalPlan()
      if (key === 'algorithm' || key === 'searchStrategy') {
        this[key] = String(value)
      } else if (key === 'searchSeed') {
        this.searchSeed = Math.min(4294967295, Math.max(0, Math.round(Number(value) || 0)))
      } else {
        const minimum = key === 'maxEvaluations' ? (this.backendContractVersion ? 1 : 200) : (this.backendContractVersion ? 4 : 8)
        this[key] = Math.max(minimum, Math.round(Number(value) || minimum))
      }
      this.markNeedsRun('搜索设置已调整，请重新率定。')
      this.saveCurrentWorkspace()
    },
    setSearchParameters(value) {
      this.detachOriginalPlan()
      if (JSON.stringify(Object.keys(value || {}).sort()) !== JSON.stringify(Object.keys(this.searchParameters || {}).sort())) this.searchParameterOrder = null
      this.searchParameters = clonePlain(value)
      this.markNeedsRun('率定参数范围已调整。')
      this.saveCurrentWorkspace()
    },
    setCalibrationTarget(value) {
      if (!['runoff', 'routing', 'joint'].includes(value) || this.running) return
      this.detachOriginalPlan()
      if (value !== this.calibrationTarget) {
        this.searchParameterOrder = null
      }
      this.calibrationTarget = value
      if (value === 'runoff' && this.searchParameters) {
        const allowed=new Set((useAppStore().backendCase?.searchParameterDefinitions || []).map(row=>row.key))
        if(allowed.size) this.searchParameters=Object.fromEntries(Object.entries(this.searchParameters).filter(([key])=>allowed.has(key)))
      }
      if (value !== 'runoff') this.searchStrategy = 'joint'
      this.markNeedsRun('率定参数组已调整。')
      this.saveCurrentWorkspace()
    },
    setRoutingSearchParameters(value) {
      this.detachOriginalPlan()
      if (JSON.stringify(Object.keys(value || {}).sort()) !== JSON.stringify(Object.keys(this.routingSearchParameters || {}).sort())) this.searchParameterOrder = null
      this.routingSearchParameters = clonePlain(value)
      this.markNeedsRun('坡面汇流搜索范围已调整。')
      this.saveCurrentWorkspace()
    },
    setJointProfile(value) {
      if (!['reference-16', 'fixed-kc-c-14', 'all-physical-16'].includes(value) || this.running) return
      this.detachOriginalPlan()
      if (value !== this.jointProfile) { this.searchParameters = null; this.routingSearchParameters = null; this.sourcePlan = null; this.optimizerSettings = {} }
      this.jointProfile = value
      if (this.searchParameters && value === 'fixed-kc-c-14') {
        const { KC, C, ...remaining } = this.searchParameters
        this.searchParameters = remaining
      }
      this.markNeedsRun('联合率定预设已调整，请复核当前搜索参数。')
      this.saveCurrentWorkspace()
    },
    setScienceVersion(descriptor) {
      const previous = this.scienceVersionId
      if (previous !== (descriptor?.scienceVersionId || null)) this.detachOriginalPlan()
      this.scienceVersionId = descriptor?.scienceVersionId || null
      if (!descriptor || previous !== this.scienceVersionId) this.candidateWarmupHours = null
      this.scienceDescriptor = clonePlain(descriptor || null)
      if (descriptor) {
        this.statePolicy = descriptor.statePolicy?.strategy || 'independent-warmup'
        this.warmupDays = (this.candidateWarmupHours ?? (Number(descriptor.statePolicy?.warmupHours) || 168)) / 24
        this.evaporationMode = 'science-version'
        this.continuousOrigin = descriptor.resolvedContinuousOrigin || (previous === this.scienceVersionId ? this.continuousOrigin : null)
      } else { this.evaporationMode = 'constant-zero'; this.continuousOrigin = null }
      this.markNeedsRun('科学条件已调整，请重新计算。')
      this.saveCurrentWorkspace()
    },
    setScienceField(key, value) {
      if (!['statePolicy', 'evaporationMode', 'continuousOrigin'].includes(key) || this.running) return
      if (this.scienceVersionId && key !== 'continuousOrigin') return
      this.detachOriginalPlan()
      this[key] = value
      if (key === 'statePolicy' && value !== 'continuous-origin') this.continuousOrigin = null
      this.markNeedsRun('起算条件已调整')
      this.saveCurrentWorkspace()
    },
    setPeriod(key, value) {
      if (!['calibrationStart', 'calibrationEnd', 'validationStart', 'validationEnd', 'independentStart', 'independentEnd'].includes(key)) return
      this.detachOriginalPlan()
      this[key] = value
      this.markNeedsRun('率定时段已调整，请重新率定。')
    },
    validateCalibration({ manual = false } = {}) {
      const errors = []
      const selected = Object.keys(this.criteria.metrics).filter((key) => this.criteria.metrics[key])
      const totalWeight = selected.reduce((sum, key) => sum + Number(this.criteria.weights[key] || 0), 0)
      if (!selected.length) errors.push('至少选择一项评价指标')
      if (Math.abs(totalWeight - 1) > 0.001) errors.push('已选指标的权重合计应为 1.00')

      if (this.calibrationMode === 'event') {
        const counts = this.eventCounts
        if (!counts.calibration) errors.push('至少选择一场率定洪水')
        if (!manual && !counts.validation) errors.push('至少选择一场验证洪水')
        const physical = physicalFloodGroups(this.schemeEvents.filter(row => row.status === 'active'))
        if (physical.conflicts.length) errors.push(`重叠洪水不能跨用途：${physical.conflicts.map(group => group.memberEventIds.join('、')).join('；')}`)
        if (physical.errors.length) errors.push(physical.errors[0])
      } else {
        if (!this.calibrationStart || !this.calibrationEnd || this.calibrationStart >= this.calibrationEnd) errors.push('率定时段不完整')
        if (!this.validationStart || !this.validationEnd || this.validationStart >= this.validationEnd) errors.push('验证时段不完整')
        const windows = [['率定', this.calibrationStart, this.calibrationEnd], ['验证', this.validationStart, this.validationEnd]]
        if (this.independentStart || this.independentEnd) windows.push(['独立检验', this.independentStart, this.independentEnd])
        for (const [label, start, end] of windows) {
          if (!start || !end || !Number.isFinite(Date.parse(start)) || !Number.isFinite(Date.parse(end)) || start >= end) errors.push(`${label}期开始须早于结束`)
          if (this.backendContractVersion && ([start, end].some(value => !/T\d{2}:00(?::00)?$/.test(value || '')))) errors.push(`${label}期请选择整点`)
        }
        const ordered = [...windows].sort((a, b) => a[1].localeCompare(b[1]))
        if (ordered.some((row, index) => index > 0 && ordered[index - 1][2] >= row[1])) errors.push('率定、验证与独立检验时段不能重叠')
        if (windows.some((row, index) => index > 0 && windows[index - 1][2] >= row[1])) errors.push('各评价期须按率定、验证、独立检验的时间顺序排列')
      }
      if (this.backendContractVersion) {
        const minimum = this.searchStrategy === 'staged' ? (this.algorithm === 'DIFFERENTIAL_EVOLUTION' ? 10 : 4) : (this.algorithm === 'DIFFERENTIAL_EVOLUTION' ? 4 : 1)
        if (this.maxEvaluations < minimum) errors.push(`当前搜索方式至少需要 ${minimum} 次评价`)
      }
      return { ok: !errors.length, errors }
    },
    async runCalibration() {
      if (!this.executionAvailability.ready || !this.sectionId) {
        this.runStatus = 'invalid'
        this.runMessage = this.executionAvailability.reason || '当前案例未登记率定断面。'
        return { ok: false, errors: [this.runMessage] }
      }
      const validation = this.validateCalibration()
      if (!validation.ok) {
        this.runStatus = 'invalid'
        this.runMessage = validation.errors.join('；')
        return validation
      }
      const requestedContextKey = this.contextKey

      this.running = true
      this.runStatus = 'running'
      this.runMessage = '试算任务已提交，正在排队。'
      const tasks = useTasksStore()
      const sectionId = this.sectionId
      const isSelinco = this.domainId === 'selinco'
      const task = await tasks.submitTask(
        this.calibrationMode === 'event' ? 'eventCalibration' : 'continuousCalibration',
        {
          contextKey: this.contextKey,
          domainId: this.domainId,
          modelScale: this.modelScale,
          modelConfigId: this.modelConfigId,
          sectionId,
          sectionName: this.section,
          params: this.params,
          basePeak: isSelinco ? (SELINCO_BASE_PEAK[sectionId] || SELINCO_BASE_PEAK['SEL-SEC-001']) : sectionBasePeak(sectionId),
          currentFlow: isSelinco ? (sectionId === 'SEL-LAKE-ALL' ? 451 : 186) : undefined,
          seed: EVENT_SEED[this.eventId] ?? 1,
          startTime: this.eventStart || this.calibrationStart,
          leadHours: 72,
          obsHours: 36,
          algorithm: this.algorithm,
          criteria: clonePlain(this.criteria),
          eventId: this.calibrationMode === 'event' ? this.eventId : null,
          period: this.calibrationMode === 'continuous' ? {
            calibrationStart: this.calibrationStart,
            calibrationEnd: this.calibrationEnd,
            validationStart: this.validationStart,
            validationEnd: this.validationEnd
          } : null
        },
        `calibration:${this.contextKey}`
      )
      if (this.contextKey === requestedContextKey) this.activeTaskId = task.taskId
      const completed = await tasks.waitForTask(task.taskId)
      if (this.contextKey !== requestedContextKey) {
        const workspace = this.workspaces[requestedContextKey]
        if (workspace) {
          workspace.activeTaskId = task.taskId
          workspace.running = false
          workspace.runMessage = '任务记录保留在原工作区，结果未写入当前案例。'
        }
        return { ok: false, errors: ['案例已切换，率定结果未写入当前案例。'], task: completed }
      }
      if (completed?.status !== 'completed') {
        this.running = false
        this.runStatus = completed?.status || 'failed'
        this.runMessage = completed?.errorMessage || (completed?.status === 'cancelled' ? '任务已停止。' : '任务未完成。')
        return { ok: false, errors: [this.runMessage], task: completed }
      }
      const fixtureResult = await tasks.getResult(task.taskId)
      if (this.contextKey !== requestedContextKey) return { ok: false, errors: ['案例已切换，率定结果未写入当前案例。'], task: completed }
      const hydrograph = fixtureResult?.hydrograph || buildHydrograph(this)
      const score = metricScore(hydrograph.metrics, this.criteria)
      this.runCount += 1
      this.runResult = {
        score,
        grade: resultGrade(score),
        metrics: { ...hydrograph.metrics },
        peak: hydrograph.peak,
        peakTime: hydrograph.peakTime,
        completedAt: formatRunTime(),
        taskId: task.taskId,
        resultVersionId: completed.resultVersionId,
        resultSource: completed.resultSource,
        isCurrent: true,
        runCount: this.runCount
      }
      this.running = false
      this.runStatus = 'complete'
      this.runMessage = `率定完成：综合评分 ${score.toFixed(1)} 分，${this.runResult.grade}。`
      this.dirty = false
      return { ok: true, result: this.runResult, task: completed }
    },
    async runContinuousSimulation() {
      if (!this.executionAvailability.ready || !this.sectionId) {
        this.runStatus = 'invalid'
        this.runMessage = this.executionAvailability.reason || '当前案例未登记模拟断面。'
        return { ok: false, errors: [this.runMessage] }
      }
      if (!this.simulationStart || !this.simulationEnd || this.simulationStart >= this.simulationEnd) {
        this.runStatus = 'invalid'
        this.runMessage = '连续模拟开始和结束时间不完整。'
        return { ok: false, errors: [this.runMessage] }
      }
      const requestedContextKey = this.contextKey
      this.running = true
      this.runStatus = 'running'
      this.runMessage = '连续模拟任务已提交。'
      const tasks = useTasksStore()
      const task = await tasks.submitTask('continuousSimulation', {
        contextKey: this.contextKey,
        domainId: this.domainId,
        modelScale: this.modelScale,
        modelConfigId: this.modelConfigId,
        sectionId: this.sectionId,
        sectionName: this.section,
        params: this.params,
        basePeak: this.domainId === 'selinco' ? (SELINCO_BASE_PEAK[this.sectionId] || 264) : sectionBasePeak(this.sectionId),
        currentFlow: this.domainId === 'selinco' ? 186 : undefined,
        startTime: this.simulationStart,
        endTime: this.simulationEnd,
        warmupDays: this.warmupDays,
        stateVersionId: this.initialStateVersionId,
        outputStepHours: this.outputStepHours,
        hours: Math.min(240, Math.max(24, Math.round((new Date(this.simulationEnd) - new Date(this.simulationStart)) / 36e5)))
      }, `simulation:${this.contextKey}`)
      if (this.contextKey === requestedContextKey) this.activeTaskId = task.taskId
      const completed = await tasks.waitForTask(task.taskId)
      if (this.contextKey !== requestedContextKey) {
        const workspace = this.workspaces[requestedContextKey]
        if (workspace) {
          workspace.activeTaskId = task.taskId
          workspace.running = false
          workspace.runMessage = '任务记录保留在原工作区，结果未写入当前案例。'
        }
        return { ok: false, errors: ['案例已切换，模拟结果未写入当前案例。'], task: completed }
      }
      this.running = false
      if (completed?.status !== 'completed') {
        this.runStatus = completed?.status || 'failed'
        this.runMessage = completed?.errorMessage || '连续模拟未完成。'
        return { ok: false, errors: [this.runMessage], task: completed }
      }
      const result = await tasks.getResult(task.taskId)
      if (this.contextKey !== requestedContextKey) return { ok: false, errors: ['案例已切换，模拟结果未写入当前案例。'], task: completed }
      this.simulationResult = {
        ...result,
        taskId: task.taskId,
        resultVersionId: completed.resultVersionId,
        completedAt: formatRunTime()
      }
      this.runStatus = 'complete'
      this.runMessage = '连续模拟完成，流量与状态已保存。'
      return { ok: true, result: this.simulationResult, task: completed }
    },
    async cancelCalibration() {
      if (this.activeOperationId) {
        const task = await modelApi(`operations/${this.activeOperationId}/cancel`, { method: 'POST', body: {} })
        this.operationProgress = task; this.runMessage = '正在停止'; this.saveCurrentWorkspace()
        return task
      }
      if (!this.activeTaskId) return null
      const requestedContextKey = this.contextKey
      const task = await useTasksStore().cancelTask(this.activeTaskId)
      if (this.contextKey !== requestedContextKey) return task
      this.running = false
      this.runStatus = task?.status || 'cancelled'
      this.runMessage = '任务已停止。'
      return task
    }
  }
})
