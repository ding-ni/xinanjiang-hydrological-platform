import { defineStore } from 'pinia'
import { scaleGridByParams, DEFAULT_PARAMS } from '@/models/xajParamEngine'
import { normalizeBasinId, resolveDomainContext } from '@/data/domainContext'
import { useTasksStore } from '@/stores/tasks'
import { useAppStore } from '@/stores/app'
import { modelApi, uniqueRequestId } from '@/services/modelBackendService'
import { useCalibrationStore } from '@/stores/calibration'
import { frozenInputBundleVersionId, selectedInputBundleVersionId } from '@/services/inputProductService'
import { caseExecutionAvailability } from '@/services/caseAvailabilityService'
import { scientificRequestSelection, stateScienceBlockReason } from '@/services/scientificSelectionService'
import stateVersionCatalog from '@/fixtures/catalogs/state-versions.json'

const SNOW_STATE_DEFAULTS = { swe: 38.6, snowDepth: 24.1, snowLiquid: 2.8, frost: 68, glacierMelt: 0.18 }

function enrichInitialState(initialState) {
  const context = resolveDomainContext(initialState.domainId || 'xinanjiang', { modelScale: initialState.modelScale || '1km' })
  return {
    ...initialState,
    domainId: context.basinId,
    domain: initialState.domain || context.basinName,
    modelScale: context.modelScale,
    modelProjectId: initialState.modelProjectId || context.projectId,
    modelConfigId: initialState.modelConfigId || context.configId,
    contextKey: context.contextKey,
    observationDataId: initialState.observationDataId || `OBS-${context.basinId.toUpperCase()}-${String(initialState.time).replace(/[-:\s]/g, '')}-QC1`,
    precipitationDataId: initialState.precipitationDataId || `FORCING-${context.configId}-${String(initialState.time).replace(/[-:\s]/g, '')}`,
    observationSource: initialState.observationSource || '站网雨水情资料',
    forcingSource: initialState.forcingSource || '预报降水资料',
    analysisStatus: initialState.analysisStatus || '预报起算',
    parentStateVersionId: initialState.parentStateVersionId || null,
    inputBundleVersionId: initialState.inputBundleVersionId || null,
    parameterVersionId: initialState.parameterVersionId || context.modelConfig.paramVersion,
    sourceTaskId: initialState.sourceTaskId || initialState.sourceRun || null,
    resultSource: initialState.resultSource || null
  }
}

function cloneStateValue(value) {
  if (Array.isArray(value)) return value.map((item) => cloneStateValue(item))
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneStateValue(item)]))
  return value
}

const STATE_WORKSPACE_KEYS = ['activeVar', 'mods', 'means', 'baselines', 'selectedId', 'dirty', 'adoptedVersion', 'readiness', 'activeTaskId', 'taskRunning', 'lastTaskStatus', 'pendingHandoff']

function emptyStateWorkspace() {
  const keys = ['wu', 'wl', 'wd', 'soil', 'free', 'runoff', 'rs', 'swe', 'snowDepth', 'snowLiquid', 'frost', 'glacierMelt']
  const means = Object.fromEntries(keys.map((key) => [key, 0]))
  return {
    activeVar: 'soil',
    mods: Object.fromEntries(keys.map((key) => [key, 1])),
    means,
    baselines: { ...means },
    selectedId: null,
    dirty: false,
    adoptedVersion: null,
    readiness: '不可起报 · 尚无状态版本',
    activeTaskId: null,
    taskRunning: false,
    lastTaskStatus: '',
    pendingHandoff: null
  }
}

function captureStateWorkspace(store) {
  return Object.fromEntries(STATE_WORKSPACE_KEYS.map((key) => [key, cloneStateValue(store[key])]))
}

function applyStateWorkspace(store, workspace) {
  STATE_WORKSPACE_KEYS.forEach((key) => {
    store[key] = cloneStateValue(workspace[key])
  })
}

function withSnowState(initialState) {
  if (!initialState) return initialState
  const defaults = initialState.domainId === 'selinco' ? SNOW_STATE_DEFAULTS : { swe: 0, snowDepth: 0, snowLiquid: 0, frost: 0, glacierMelt: 0 }
  return { ...initialState, means: { ...defaults, ...initialState.means } }
}

/**
 * 状态空间场与预报时效展示所需的状态数据。
 */
export const useStateStore = defineStore('state', {
  state: () => ({
    activeDomainId: 'xinanjiang',
    activeScale: '1km',
    activeModelProjectId: 'xaj-distributed',
    activeModelConfigId: 'xaj-1km',
    contextKey: 'xinanjiang:xaj-distributed:xaj-1km',
    ...emptyStateWorkspace(),
    initialStates: stateVersionCatalog.states
      .filter((stateVersion) => stateVersion.domainId === 'selinco')
      .map(enrichInitialState),
    /** 状态编辑记录按模型配置保存，不能跨流域带入。 */
    workspaces: {}
  }),
  getters: {
    cells() {
      const checkpoint = this.activeInitialState.checkpoint
      if (!checkpoint?.gridGeometry) return []
      return checkpoint.gridGeometry.map((cell, index) => ({
        ...cell,
        lng: cell.bounds.slice(0, 4).reduce((sum, point) => sum + point[0], 0) / 4,
        lat: cell.bounds.slice(0, 4).reduce((sum, point) => sum + point[1], 0) / 4,
        wu: checkpoint.land.WU[index], wl: checkpoint.land.WL[index], wd: checkpoint.land.WD[index],
        freeWater: checkpoint.land.S[index],
        soil: (checkpoint.land.WU[index] + checkpoint.land.WL[index] + checkpoint.land.WD[index]) / checkpoint.parameters['runoff.WM']
      }))
    },
    selected(state) {
      return null
    },
    activeInitialState(state) {
      const domainRows = state.initialStates.filter((item) => {
        if (normalizeBasinId(item.domainId || 'xinanjiang') !== state.activeDomainId) return false
        if ((item.modelScale || '1km') !== state.activeScale) return false
        return !item.modelConfigId || item.modelConfigId === state.activeModelConfigId
      })
      const found = domainRows.find((item) => item.id === state.adoptedVersion) || domainRows[0]
      if (found) return withSnowState(found)
      const context = resolveDomainContext(state.activeDomainId, { configId: state.activeModelConfigId, modelScale: state.activeScale })
      return withSnowState({
        id: '暂无可用状态',
        time: '待接入',
        domainId: context.basinId,
        modelScale: context.modelScale,
        domain: context.basinName,
        modelConfigId: context.configId,
        sourceSection: '—',
        sourceRun: null,
        source: '资料缺失',
        status: '资料缺失',
        analysisStatus: '不可起报',
        observationDataId: null,
        precipitationDataId: null,
        observationSource: '待接入',
        forcingSource: '待接入',
        means: emptyStateWorkspace().means
      })
    },
    domainInitialStates(state) {
      return state.initialStates.filter((item) => {
        if (normalizeBasinId(item.domainId || 'xinanjiang') !== state.activeDomainId) return false
        if ((item.modelScale || '1km') !== state.activeScale) return false
        return !item.modelConfigId || item.modelConfigId === state.activeModelConfigId
      }).map(withSnowState)
    }
  },
  actions: {
    hydrateBackendStates(states = []) {
      const rows = states.map((row) => enrichInitialState({
        id: row.stateVersionId, time: row.time.replace('T', ' '),
        domainId: row.context.projectId, modelScale: row.context.resolutionId,
        modelConfigId: row.context.modelConfigId, sourceSection: '徐畈（二）',
        source: row.importProvenance ? '导入状态' : row.initialization?.strategy === 'CONTINUOUS' ? '从连续起点补算' : row.initialization?.strategy === 'OBSERVED_FLOW_ANCHORED' ? '按实测流量初始化' : row.initialization ? '降雨预热' : '模型末状态', sourceTaskId: row.sourceTaskId, resultSource: 'model-backend',
        productionEligible: row.productionEligible, status: stateScienceBlockReason(row, useAppStore().backendCase?.modelImplementationSHA256) ? '需重新初始化' : '可用',
        analysisStatus: stateScienceBlockReason(row, useAppStore().backendCase?.modelImplementationSHA256) ? '仅供查看' : '可续算',
        parameters: Object.fromEntries(Object.entries(row.parameters).map(([key, value]) => [key.replace('runoff.', ''), value])),
        parameterVersionId: row.parameterVersionId,
        parentStateVersionId: row.parentStateVersionId,
        inputBundleVersionId: frozenInputBundleVersionId(row),
        inputBundleManifestSHA256: row.inputBundleManifestSHA256 || row.lineage?.inputBundleManifestSHA256 || null,
        observationDataId: row.observationVersion || 'xufan_outlet_hourly_qc_20260819',
        precipitationDataId: row.forcingVersion || 'precipitation_qc_20260819',
        observationSource: '徐畈整编逐时流量',
        forcingSource: '历史整编逐时降雨',
        checkpoint: row,
        means: {
          wu: row.land.WU.reduce((a, b) => a + b, 0) / row.gridIds.length,
          wl: row.land.WL.reduce((a, b) => a + b, 0) / row.gridIds.length,
          wd: row.land.WD.reduce((a, b) => a + b, 0) / row.gridIds.length,
          free: row.land.S.reduce((a, b) => a + b, 0) / row.gridIds.length,
          soil: (row.land.WU.reduce((a, b) => a + b, 0) + row.land.WL.reduce((a, b) => a + b, 0) + row.land.WD.reduce((a, b) => a + b, 0)) / row.gridIds.length / row.parameters['runoff.WM']
        }
      }))
      const ids = new Set(rows.map((row) => row.id))
      this.initialStates = [...rows.sort((a, b) => b.time.localeCompare(a.time)), ...this.initialStates.filter((row) => !ids.has(row.id))]
      const app = useAppStore()
      const savedVersion = app.contextKey === this.contextKey ? app.workContext.stateVersionId : null
      const available = this.domainInitialStates
      const selected = available.find((row) => row.id === this.adoptedVersion)
        || available.find((row) => row.id === savedVersion)
        || available[0]
      if (selected && this.adoptedVersion !== selected.id) this.loadInitialState(selected.id)
    },
    async runBackendStateTask(mode, targetTime, options = {}) {
      const app = useAppStore()
      const key = this.contextKey
      const inputKey = app.inputSelectionKey
      const routingVersionId = app.selectedRoutingVersionId
      const bundleVersionId = selectedInputBundleVersionId(app)
      const source = this.activeInitialState
      const target = new Date(targetTime)
      if (!Number.isFinite(target.getTime()) || target.getMinutes()) return { ok: false, error: '请选择有效的历史整点。' }
      if (mode !== 'initialization' && !source.id.startsWith('STATE-JOB-')) return { ok: false, error: '续算必须选择真实模型状态。' }
      if (mode !== 'initialization' && stateScienceBlockReason(source, app.backendCase?.modelImplementationSHA256)) return { ok: false, error: stateScienceBlockReason(source, app.backendCase?.modelImplementationSHA256) }
      if (mode !== 'initialization' && frozenInputBundleVersionId(source.checkpoint || source) !== bundleVersionId) return { ok: false, error: '父状态属于另一输入版本。跨资料版本必须重新初始化，不能无提示续算。' }
      if (mode !== 'initialization' && (source.checkpoint?.routingVersionId || source.checkpoint?.lineage?.routingVersionId || 'control-split-v2') !== app.selectedRoutingVersionId) return { ok: false, error: '父状态属于另一汇流版本。请重新初始化。' }
      if (app.inputCatalogError || app.inputCatalogLoading) return { ok: false, error: app.inputCatalogError || '选定输入版本仍在校验中。' }
      if (this.dirty) return { ok: false, error: '状态已修改，请重新初始化。' }
      const localIso = (value) => { const date = new Date(value); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) }
      const runType = mode === 'initialization' ? 'stateInitialization' : 'stateContinuation'
      const calibration = useCalibrationStore()
      const useCalibrationParameters = calibration.contextKey === key && calibration.backendContractVersion
      const parameters = mode === 'initialization' ? (useCalibrationParameters ? { ...calibration.params } : app.backendCase.parameters) : source.parameters
      const strategy = options.strategy || 'FIXED_WARMUP'
      const scientific = scientificRequestSelection({ parentState: mode === 'initialization' ? null : source,
        selected: useCalibrationParameters ? calibration : null, initializationStrategy: strategy })
      const continuousOrigin = mode === 'initialization' && strategy === 'CONTINUOUS' ? calibration.continuousOrigin : null
      if (mode === 'initialization' && strategy === 'CONTINUOUS' && (!continuousOrigin || !scientific.science)) return { ok: false, error: '请先采用含连续起点的参数方案。' }
      const request = {
        schemaVersion: '2.0', requestId: uniqueRequestId('STATE-REQ'), runType,
        context: { contextKey: key, projectId: this.activeDomainId, resolutionId: this.activeScale, modelConfigId: this.activeModelConfigId },
        parameters,
        ...(scientific.science ? { science: scientific.science } : {}),
        inputs: { bundleVersionId, evaporation: scientific.evaporation },
        routing: { versionId: app.selectedRoutingVersionId },
        ...(mode === 'initialization' ? { initialization: { strategy } } : {}),
        period: { startTime: mode === 'initialization' ? targetTime : localIso(new Date(source.time.replace(' ', 'T')).getTime() + 3600000), endTime: targetTime,
          warmupDays: strategy === 'CONTINUOUS' ? calibration.warmupDays : mode === 'initialization' ? (options.warmupDays ?? 7) : 7,
          initialStateVersionId: mode === 'initialization' ? null : source.id, ...(continuousOrigin ? { continuousOrigin } : {}) },
        lineage: { inputBundleVersionId: bundleVersionId, stateVersionId: mode === 'initialization' ? null : source.id, sourceTaskId: source.sourceTaskId,
          parameterVersionId: mode === 'initialization' ? (useCalibrationParameters ? calibration.paramVersion : app.backendCase.parameterVersionId) : source.parameterVersionId }
      }
      this.taskRunning = true
      this.lastTaskStatus = '提交真实状态计算'
      try {
        const tasks = useTasksStore()
        const submitted = await tasks.submitTask(runType, request, `state:${key}`)
        if (this.contextKey === key) this.activeTaskId = submitted.taskId
        const completed = await tasks.waitForTask(submitted.taskId)
        if (!['completed', 'archived'].includes(completed.status)) throw new Error(completed.errorMessage || '状态计算未完成。')
        const result = await tasks.getResult(submitted.taskId)
        if (this.contextKey !== key || app.inputSelectionKey !== inputKey || app.selectedRoutingVersionId !== routingVersionId) return { ok: false, error: '任务已保存原输入与汇流血缘，工作区已切换，未自动采用此状态。' }
        const response = await modelApi('states')
        if (this.contextKey !== key || app.inputSelectionKey !== inputKey || app.selectedRoutingVersionId !== routingVersionId) return { ok: false, error: '任务已完成，可在原工作区查看。' }
        this.hydrateBackendStates(response.states)
        const saved = this.loadInitialState(result.stateVersionId)
        app.setStateVersion(saved.id)
        this.lastTaskStatus = '状态计算完成'
        return { ok: true, state: saved, task: completed, result }
      } catch (error) {
        if (this.contextKey === key) this.lastTaskStatus = error.message
        return { ok: false, error: error.message }
      } finally {
        if (this.contextKey === key) this.taskRunning = false
        else if (this.workspaces[key]) this.workspaces[key].taskRunning = false
      }
    },
    setDomain(domainId) {
      this.setContext(domainId, this.activeScale)
    },
    setContext(domainId, scale = '1km', modelConfigId = null) {
      const context = resolveDomainContext(domainId, { modelScale: scale, configId: modelConfigId || undefined })
      if (context.contextKey === this.contextKey) return
      if (this.contextKey) this.workspaces[this.contextKey] = captureStateWorkspace(this)
      this.activeDomainId = context.basinId
      this.activeScale = context.modelScale
      this.activeModelProjectId = context.projectId
      this.activeModelConfigId = context.configId
      this.contextKey = context.contextKey
      const saved = this.workspaces[context.contextKey]
      if (saved) {
        applyStateWorkspace(this, saved)
        return
      }
      const first = this.domainInitialStates[0]
      applyStateWorkspace(this, emptyStateWorkspace())
      if (first) this.loadInitialState(first.id)
    },
    setActiveVar(key) {
      this.activeVar = key
    },
    setMean(key, value) {
      if (!this.domainInitialStates.length || !(key in this.means)) return
      const v = Number(value)
      this.means[key] = v
      // 修正系数 = 当前 / 基线
      const base = this.baselines[key] || 1
      this.mods[key] = base === 0 ? 1 : v / base
      this.dirty = true
      this.readiness = this.evaluateReadiness()
    },
    evaluateReadiness() {
      if (!this.domainInitialStates.length || !this.adoptedVersion) return '不可起报 · 尚无状态版本'
      const s = this.means.soil
      if (s > 0.9) return '偏湿 · 建议复核'
      if (s < 0.25) return '偏干 · 可起报'
      if (this.means.wu > 28) return '上层饱和偏高'
      return '可起报'
    },
    resetMeans() {
      this.means = { ...this.baselines }
      Object.keys(this.mods).forEach((k) => {
        this.mods[k] = 1
      })
      this.dirty = false
      this.readiness = this.evaluateReadiness()
    },
    adopt(version) {
      if (!this.domainInitialStates.some((item) => item.id === version)) return null
      this.adoptedVersion = version
      this.baselines = { ...this.means }
      this.dirty = false
      this.readiness = '可起报'
      return {
        version,
        means: { ...this.means },
        mods: { ...this.mods }
      }
    },
    loadInitialState(id) {
      const initialState = this.domainInitialStates.find((item) => item.id === id)
      if (!initialState) return null
      this.adoptedVersion = initialState.id
      const defaults = initialState.domainId === 'selinco'
        ? { wu: 0, wl: 0, wd: 0, soil: 0, free: 0, runoff: 0, rs: 0, ...SNOW_STATE_DEFAULTS }
        : { wu: 0, wl: 0, wd: 0, soil: 0, free: 0, runoff: 0, rs: 0, swe: 0, snowDepth: 0, snowLiquid: 0, frost: 0, glacierMelt: 0 }
      this.means = { ...defaults, ...initialState.means }
      this.baselines = { ...this.means }
      Object.keys(this.mods).forEach((key) => {
        this.mods[key] = 1
      })
      this.dirty = false
      this.readiness = initialState.status
      const app = useAppStore()
      if (initialState.resultSource === 'model-backend' && app.contextKey === this.contextKey) app.setStateVersion(initialState.id)
      return initialState
    },
    prepareFromWorkContext(workContext) {
      if (!workContext?.handoff || workContext.handoff.targetPage !== 'state') return null
      this.pendingHandoff = cloneStateValue({
        ...workContext.handoff,
        issueTime: workContext.issueTime,
        dataSourceId: workContext.dataSourceId,
        inputBundleVersionId: workContext.inputBundleVersionId,
        parameterVersionId: workContext.parameterVersionId,
        sourceTaskId: workContext.sourceTaskId
      })
      return this.pendingHandoff
    },
    saveInitialState(payload) {
      const compact = String(payload.time).replace(/[-:\s]/g, '').slice(0, 12)
      const context = resolveDomainContext(payload.domainId || this.activeDomainId, {
        modelScale: payload.modelScale || this.activeScale,
        configId: payload.modelConfigId || this.activeModelConfigId
      })
      const domainId = context.basinId
      const modelScale = context.modelScale
      const prefix = domainId === 'selinco' ? (modelScale === '0p1deg' ? 'STATE-SEL-0P1' : 'STATE-SEL') : 'STATE'
      const baseId = payload.id || `${prefix}-${compact.slice(0, 8)}-${compact.slice(8, 12)}-${context.configId}`
      const duplicateCount = this.initialStates.filter((item) => item.id === baseId || item.id.startsWith(`${baseId}-r`)).length
      if (payload.resultSource === 'model-backend' && this.initialStates.some((row) => row.id === baseId)) return this.loadInitialState(baseId)
      const id = duplicateCount ? `${baseId}-r${duplicateCount + 1}` : baseId
      const initialState = enrichInitialState({
        id,
        time: payload.time,
        domainId,
        modelScale,
        modelProjectId: payload.modelProjectId || context.projectId,
        modelConfigId: context.configId,
        domain: payload.domain || context.basinName,
        sourceSection: payload.sourceSection,
        sourceRun: payload.sourceRun,
        source: payload.source || '预报起算状态',
        status: '可用',
        analysisStatus: payload.analysisStatus || '预报起算',
        observationDataId: payload.observationDataId,
        precipitationDataId: payload.precipitationDataId,
        observationSource: payload.observationSource,
        forcingSource: payload.forcingSource,
        parentStateVersionId: payload.parentStateVersionId,
        inputBundleVersionId: payload.inputBundleVersionId,
        parameterVersionId: payload.parameterVersionId,
        sourceTaskId: payload.sourceTaskId,
        resultSource: payload.resultSource,
        parameters: payload.parameters,
        productionEligible: payload.productionEligible,
        means: { ...this.means, ...(payload.means || {}) }
      })
      this.initialStates.unshift(initialState)
      this.loadInitialState(id)
      return initialState
    },
    async runStateTask(mode, targetTime, options = {}) {
      const app = useAppStore()
      const context = resolveDomainContext(this.activeDomainId, { configId: this.activeModelConfigId, modelScale: this.activeScale })
      const availability = caseExecutionAvailability(context, app.projectData, mode === 'initialization' ? 'stateInitialization' : 'stateContinuation', {
        loading: app.projectDataLoading,
        error: app.projectDataError
      })
      if (!availability.ready || app.contextKey !== this.contextKey) return { ok: false, error: availability.reason || '状态工作区与当前案例不一致。' }
      if (availability.mode === 'model-backend') return this.runBackendStateTask(mode, targetTime, options)
      if (mode !== 'initialization' && !this.domainInitialStates.length) return { ok: false, error: '当前案例没有可续算的状态版本。' }
      const source = this.activeInitialState
      const requestedContextKey = this.contextKey
      const runType = mode === 'initialization' ? 'stateInitialization' : 'stateContinuation'
      const tasks = useTasksStore()
      this.taskRunning = true
      this.lastTaskStatus = '任务已提交'
      const parameterVersionId = resolveDomainContext(this.activeDomainId, { configId: this.activeModelConfigId }).modelConfig.paramVersion
      const submitted = await tasks.submitTask(runType, {
        contextKey: this.contextKey,
        domainId: this.activeDomainId,
        modelScale: this.activeScale,
        modelConfigId: this.activeModelConfigId,
        sourceStateVersionId: mode === 'initialization' ? null : source?.id,
        targetTime,
        issueTime: targetTime,
        parameterVersionId,
        inputBundleVersionId: this.pendingHandoff?.inputBundleVersionId || source?.inputBundleVersionId || null,
        dataSourceId: this.pendingHandoff?.dataSourceId || null,
        sourceTaskId: this.pendingHandoff?.sourceTaskId || source?.sourceTaskId || null,
        observationDataId: source?.observationDataId,
        variables: this.activeDomainId === 'selinco'
          ? ['WU', 'WL', 'WD', 'S', 'SWE', 'snowDepth', 'snowLiquid', 'frozenIndex', 'glacierMelt']
          : ['WU', 'WL', 'WD', 'S']
      }, `state:${this.contextKey}`)
      if (this.contextKey === requestedContextKey) this.activeTaskId = submitted.taskId
      const completed = await tasks.waitForTask(submitted.taskId)
      if (this.contextKey !== requestedContextKey) {
        const workspace = this.workspaces[requestedContextKey]
        if (workspace) {
          workspace.activeTaskId = submitted.taskId
          workspace.taskRunning = false
          workspace.lastTaskStatus = completed?.stageLabel || completed?.status || '任务结束'
        }
        return { ok: false, error: '案例已切换，任务记录保留在原工作区，结果未写入当前案例。', task: completed }
      }
      this.taskRunning = false
      this.lastTaskStatus = completed?.stageLabel || completed?.status || '任务结束'
      if (completed?.status !== 'completed') return { ok: false, error: completed?.errorMessage || this.lastTaskStatus, task: completed }
      const result = await tasks.getResult(submitted.taskId)
      if (this.contextKey !== requestedContextKey) return { ok: false, error: '案例已切换，状态结果未写入当前案例。', task: completed }
      const saved = this.saveInitialState({
        id: result?.stateVersionId,
        time: targetTime,
        domainId: this.activeDomainId,
        modelScale: this.activeScale,
        modelConfigId: this.activeModelConfigId,
        sourceSection: source?.sourceSection || '全流域栅格',
        sourceRun: submitted.taskId,
        source: mode === 'initialization' ? '试算状态初始化' : '试算状态续算',
        analysisStatus: '可用于预报',
        observationDataId: source?.observationDataId,
        precipitationDataId: source?.precipitationDataId,
        parentStateVersionId: result?.parentStateVersionId || (mode === 'continuation' ? source?.id : null),
        inputBundleVersionId: this.pendingHandoff?.inputBundleVersionId || source?.inputBundleVersionId || null,
        parameterVersionId,
        sourceTaskId: submitted.taskId,
        resultSource: completed.resultSource,
        means: { ...this.means }
      })
      return { ok: true, state: saved, task: completed, result }
    },
    async cancelStateTask() {
      if (!this.activeTaskId) return null
      const requestedContextKey = this.contextKey
      const task = await useTasksStore().cancelTask(this.activeTaskId)
      if (this.contextKey !== requestedContextKey) return task
      this.taskRunning = false
      this.lastTaskStatus = '任务已停止'
      return task
    },
    /** 应用修正到栅格单元 */
    applyMods(cells, params = DEFAULT_PARAMS) {
      if (this.activeInitialState.resultSource === 'model-backend' || this.activeDomainId !== 'selinco') return []
      const scaled = scaleGridByParams(cells, params)
      const m = this.mods
      return scaled.map((c) => ({
        ...c,
        soil: +Math.min(0.98, Math.max(0.1, c.soil * (m.soil || 1))).toFixed(2),
        freeWater: +(c.freeWater * (m.free || 1)).toFixed(1),
        runoff: +Math.max(0, c.runoff * (m.runoff || 1)).toFixed(2),
        surface: Math.round(Math.max(0, c.surface * (m.rs || 1))),
        wu: +((this.means.wu / 18) * c.soil * 30).toFixed(1),
        wl: +((this.means.wl / 42) * c.soil * 55).toFixed(1),
        wd: +((this.means.wd / 55) * c.soil * 70).toFixed(1)
      }))
    }
  }
})
