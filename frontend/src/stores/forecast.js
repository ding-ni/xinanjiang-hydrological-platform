import { defineStore } from 'pinia'
import { selincoSections } from '@/data/selinco'
import dayjs from 'dayjs'
import {
  DEFAULT_PARAMS,
  VERSION_PRESETS,
  PARAM_DEFS,
  simulateHydrograph,
  sectionBasePeak,
  warnLevelFromPeak
} from '@/models/xajParamEngine'
import { useConfigStore } from '@/stores/config'
import { resolveDomainContext } from '@/data/domainContext'
import { useTasksStore } from '@/stores/tasks'
import { useAppStore } from '@/stores/app'
import { caseExecutionAvailability, isCaseFixtureContext } from '@/services/caseAvailabilityService'

function normalizeScale(domainId, scale) {
  return scale === '0p1deg' ? '0p1deg' : '1km'
}

function contextMeta(domainId, scale = '1km', configId = null) {
  const resolved = resolveDomainContext(domainId, {
    modelScale: normalizeScale(domainId, scale),
    configId: configId || undefined
  })
  const modelScale = resolved.modelScale

  if (resolved.basinId === 'selinco') {
    const coarse = modelScale === '0p1deg'
    return {
      domainId: resolved.basinId,
      modelScale,
      modelProjectId: resolved.projectId,
      modelConfigId: resolved.configId,
      contextKey: resolved.contextKey,
      paramVersion: coarse ? 'XAJ-SEL-0P1-v1.0' : 'XAJ-SEL-v1.0',
      stateVersion: coarse ? 'STATE-SEL-0P1-20260602-0800' : 'STATE-SEL-20260602-0800',
      dataVersion: coarse ? 'SEL-0P1-NC-20260602-1400' : 'SEL-1KM-NC-20260602-1400',
      rainQpfSource: coarse ? 'selinco_precip_hourly_0p1deg.nc' : 'selinco_precip_hourly_1km.nc',
      observationDataId: 'OBS-SEL-20260602-1400-QC1',
      forecastPrecipitationId: coarse ? 'FORCING-SEL-0P1-20260602-1400' : 'FORCING-SEL-1KM-20260602-1400',
      wetness: coarse ? 0.49 : 0.47
    }
  }

  if (resolved.basinId === 'nananxi-xufan') {
    return {
      domainId: resolved.basinId,
      modelScale,
      modelProjectId: resolved.projectId,
      modelConfigId: resolved.configId,
      contextKey: resolved.contextKey,
      paramVersion: resolved.modelConfig.paramVersion,
      stateVersion: null,
      dataVersion: 'XF-OBS-RAIN-HOURLY',
      rainQpfSource: '预报降雨未接入',
      observationDataId: 'XF-OBS-RAIN-HOURLY',
      forecastPrecipitationId: null,
      wetness: null,
      defaultIssueTime: '2024-07-06 08:00'
    }
  }

  return {
    domainId: resolved.basinId,
    modelScale,
    modelProjectId: resolved.projectId,
    modelConfigId: resolved.configId,
    contextKey: resolved.contextKey,
    paramVersion: resolved.modelConfig.paramVersion,
    stateVersion: null,
    dataVersion: null,
    rainQpfSource: '预报降雨未接入',
    observationDataId: null,
    forecastPrecipitationId: null,
    wetness: null,
    defaultIssueTime: ''
  }
}

function qualityFor(domainId) {
  if (domainId === 'selinco') {
    return {
      rain: { status: 'ok', text: '4 站正常', block: false },
      hydro: { status: 'ok', text: '4 个入湖口正常', block: false },
      areal: { status: 'ok', text: '降水场已就绪', block: false },
      state: { status: 'ok', text: '起算状态已就绪', block: false },
      level: { status: 'ok', text: '湖位正常', block: false },
      late: { status: 'ok', text: '无迟报', block: false }
    }
  }

  if (domainId === 'llpq') {
    return {
      rain: { status: 'bad', text: '小时降水未接入', block: true },
      hydro: { status: 'bad', text: '出口流量未接入', block: true },
      areal: { status: 'bad', text: '降水场未生成', block: true },
      state: { status: 'bad', text: '无可用状态版本', block: true },
      level: { status: 'warn', text: '仅空间资料', block: false },
      late: { status: 'warn', text: '任务暂不可执行', block: false }
    }
  }


  if (domainId === 'nananxi-xufan') {
    return {
      rain: { status: 'ok', text: '11 站实测雨量已接入', block: false },
      hydro: { status: 'ok', text: '徐畈水位流量已接入', block: false },
      areal: { status: 'ok', text: '1 km 实测降雨场已就绪', block: false },
      state: { status: 'bad', text: '起算状态待生成', block: true },
      level: { status: 'ok', text: '实测水位资料已接入', block: false },
      late: { status: 'ok', text: '资料窗口不判迟报', block: false }
    }
  }

  return {
    rain: { status: 'bad', text: '小时降水未接入', block: true },
    hydro: { status: 'bad', text: '出口流量未接入', block: true },
    areal: { status: 'bad', text: '降水场未接入', block: true },
    state: { status: 'bad', text: '无可用状态版本', block: true },
    level: { status: 'warn', text: '水位资料未接入', block: false },
    late: { status: 'warn', text: '未登记资料，不判迟报', block: false }
  }
}

function defaultLeadHours(section) {
  const suggested = Number(section?.leadDefault)
  return suggested >= 6 && suggested <= 72 ? suggested : 24
}

function createWorkspace(domainId = 'xinanjiang', scale = '1km', configId = null) {
  const meta = contextMeta(domainId, scale, configId)
  const context = resolveDomainContext(meta.domainId, { configId: meta.modelConfigId })
  const demoEnabled = isCaseFixtureContext(context)
  const domainSections = (demoEnabled ? selincoSections : []).map((section) => ({ ...section }))
  const issueTime = meta.defaultIssueTime ?? (demoEnabled ? '2026-06-02 14:00' : '')

  return {
    ...meta,
    contextKey: meta.contextKey,
    sections: domainSections,
    targetSectionId: domainSections[0]?.id || '',
    outputScope: 'full-domain',
    startTime: issueTime,
    leadHours: defaultLeadHours(domainSections[0]),
    rainMode: 'blend_auto',
    rainObsEnd: issueTime,
    arealRainProduct: meta.domainId === 'selinco' ? '色林错逐小时格点降水' : meta.domainId === 'nananxi-xufan' ? '南安溪实测降雨场' : '当前案例降雨场未接入',
    forecastMode: meta.domainId === 'nananxi-xufan' ? 'manual' : 'automatic',
    parameterLibrary: meta.domainId === 'nananxi-xufan' ? 'simulation-rate' : 'automatic-forecast',
    parameterVersionStatus: demoEnabled ? '采用已登记试算参数' : meta.domainId === 'nananxi-xufan' ? '沿用起报状态参数' : '生产参数待发布',
    baseParams: demoEnabled ? { ...(VERSION_PRESETS[meta.paramVersion] || DEFAULT_PARAMS) } : {},
    paramTweaks: {},
    stateSource: 'continuation',
    running: false,
    activeTaskId: null,
    resultStatus: '待预报',
    selectedGridId: null,
    committed: null,
    dirtySinceCommit: false,
    qualityOverride: { requested: false, reason: '' },
    dataCheck: qualityFor(meta.domainId),
    resultBySection: {}
  }
}

const WORKSPACE_KEYS = Object.keys(createWorkspace())

function cloneValue(value) {
  if (Array.isArray(value)) return value.map((item) => cloneValue(item))
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneValue(item)]))
  }
  return value
}

function captureWorkspace(store) {
  return Object.fromEntries(WORKSPACE_KEYS.map((key) => [key, cloneValue(store[key])]))
}

function applyWorkspace(store, workspace) {
  WORKSPACE_KEYS.forEach((key) => {
    store[key] = cloneValue(workspace[key])
  })
}

function compactId(value) {
  return String(value || '').replace(/[^A-Za-z0-9]/g, '').slice(-12) || '0000'
}

function createForecastRunId(state, nonce = null) {
  const issue = String(state.startTime || '').replace(/[-:\s]/g, '').slice(0, 12) || 'PENDING'
  const basin = state.domainId === 'selinco' ? 'SEL' : 'XAJ'
  const scale = state.modelScale === '0p1deg' ? '0P1' : '1KM'
  const serial = compactId(nonce || `${Date.now()}${Math.floor(Math.random() * 1e4)}`).slice(-8)
  return `FCST-${basin}-${scale}-${issue}-${serial}`
}

function buildSummary(h, state, thresholds) {
  const peak = h.peak
  const lakeMode = state.domainId === 'selinco'
  const warnLevel = lakeMode ? 4531.1 : (thresholds?.warnLevel ?? 34.5)
  const ensureLevel = lakeMode ? 4531.5 : (thresholds?.ensureLevel ?? 36)
  const warnFlow = thresholds?.warnFlow ?? 2800
  const peakLevel = lakeMode
    ? +(4530.24 + Math.min(0.95, h.volume / 4.8e9)).toFixed(2)
    : +(warnLevel - 0.8 + peak / (warnFlow * 1.1)).toFixed(2)
  const level = lakeMode
    ? (peakLevel >= ensureLevel ? '高水位' : peakLevel >= warnLevel ? '关注' : '平稳')
    : warnLevelFromPeak(peak, peakLevel, thresholds)

  return {
    key: 'main',
    name: '常规预报',
    peak,
    peakTime: dayjs(state.startTime).add(h.peakHour || 18, 'hour').format('YYYY-MM-DD HH:mm'),
    level,
    peakLevel,
    warnLevel,
    ensureLevel,
    warnFlow,
    volume: h.volume,
    duration: Math.max(12, Math.round(18)),
    deltaPeakPrev: Math.round(peak * 0.05),
    deltaTimePrev: 0,
    wetness: h.wetness
  }
}

export const useForecastStore = defineStore('forecast', {
  state: () => ({
    ...createWorkspace(),
    workspaces: {}
  }),

  getters: {
    targetSection(state) {
      return state.sections.find((section) => section.id === state.targetSectionId) || state.sections[0] || { id: '', name: '断面未登记', river: '—' }
    },
    executionAvailability(state) {
      const app = useAppStore()
      const context = resolveDomainContext(state.domainId, { configId: state.modelConfigId, modelScale: state.modelScale })
      if (app.contextKey !== state.contextKey) return { ready: false, reason: '预报工作区与当前案例不一致。' }
      return caseExecutionAvailability(context, app.projectData, 'forecast', { loading: app.projectDataLoading, error: app.projectDataError })
    },
    paramDefs() {
      return PARAM_DEFS
    },
    effectiveParams(state) {
      return { ...state.baseParams, ...state.paramTweaks }
    },
    simOptions(state) {
      const selinco = state.domainId === 'selinco'
      return {
        basePeak: this.targetSection?.basePeak ?? null,
        domainId: state.domainId,
        targetPeak: selinco,
        baseFlow: selinco ? (this.targetSection?.currentFlow || 186) : undefined,
        issueFlow: selinco ? (this.targetSection?.currentFlow || 186) : undefined,
        hours: Math.min(72, Math.max(6, state.leadHours)),
        obsHours: 48,
        observedBeforeStart: true,
        start: state.startTime.replace(' ', 'T') + (state.startTime.length <= 16 ? ':00' : ''),
        seed: 1,
        wetness: state.wetness
      }
    },
    previewHydrograph() {
      if (!this.executionAvailability.ready || this.executionAvailability.mode !== 'frontend-fixture') return { forecast: [], obs: [], prev: [], startMark: this.startTime }
      return simulateHydrograph(this.effectiveParams, this.simOptions)
    },
    displayHydrograph(state) {
      if (state.committed?.hydrograph && !state.dirtySinceCommit) return state.committed.hydrograph
      return this.previewHydrograph
    },
    hydrograph() {
      return this.displayHydrograph
    },
    gridCells() {
      // The view loads grids from the active case; no global demo grid fallback.
      return []
    },
    previewScheme(state) {
      if (!this.executionAvailability.ready || this.executionAvailability.mode !== 'frontend-fixture') {
        return { name: '待模型计算', peak: null, peakTime: '—', peakLevel: null, level: '—', warnLevel: null, ensureLevel: null, volume: null }
      }
      let thresholds = null
      try {
        thresholds = useConfigStore().thresholds[state.targetSectionId]
      } catch {
        thresholds = null
      }
      return buildSummary(this.previewHydrograph, state, thresholds)
    },
    currentScheme(state) {
      if (state.committed?.summary && !state.dirtySinceCommit) {
        return { ...state.committed.summary, name: state.committed.summary.name || '常规预报' }
      }
      return this.previewScheme
    },
    sectionPreviews(state) {
      if (!this.executionAvailability.ready || this.executionAvailability.mode !== 'frontend-fixture') return []
      let thresholds = {}
      try {
        thresholds = useConfigStore().thresholds
      } catch {
        thresholds = {}
      }
      const selinco = state.domainId === 'selinco'
      return state.sections.map((section) => {
        const hydrograph = simulateHydrograph(this.effectiveParams, {
          ...this.simOptions,
          basePeak: section.basePeak || sectionBasePeak(section.id),
          baseFlow: selinco ? section.currentFlow : undefined,
          issueFlow: selinco ? section.currentFlow : undefined
        })
        return {
          sectionId: section.id,
          sectionName: section.name,
          hydrograph,
          summary: buildSummary(hydrograph, state, thresholds[section.id])
        }
      })
    },
    selectedGrid(state) {
      return this.gridCells.find((grid) => grid.id === state.selectedGridId) || null
    },
    peakCountdown() {
      const peak = dayjs(this.currentScheme.peakTime)
      const start = dayjs(this.startTime)
      return `${Math.max(0, peak.diff(start, 'hour'))} h`
    },
    hasBlockingIssue(state) {
      return Object.values(state.dataCheck).some((item) => item.block && item.status === 'bad')
    },
    canRun(state) {
      const hasApprovedOverride = state.qualityOverride?.requested && Boolean(state.qualityOverride?.reason)
      return this.executionAvailability.ready && !state.running && Boolean(state.targetSectionId && state.startTime && state.leadHours && state.stateVersion) && (!this.hasBlockingIssue || hasApprovedOverride)
    },
    forecastEndTime(state) {
      return dayjs(state.startTime).add(state.leadHours, 'hour').format('YYYY-MM-DD HH:mm')
    },
    isPreviewMode(state) {
      return !state.committed || state.dirtySinceCommit
    },
    modeLabel(state) {
      if (!state.committed) return '待预报'
      if (state.dirtySinceCommit) return '条件已更新'
      return '计算完成'
    },
    parameterSourceLabel(state) {
      if (state.domainId === 'nananxi-xufan' && state.forecastMode === 'manual') return state.parameterVersionStatus
      return state.forecastMode === 'automatic'
        ? `自动预报参数库 · ${state.parameterVersionStatus}`
        : `人工预报 · 洪水模拟与率定参数库 · ${state.parameterVersionStatus}`
    },
    outputSections(state) {
      const previews = Object.fromEntries(this.sectionPreviews.map((item) => [item.sectionId, item]))
      return state.sections.map((section) => ({
        ...section,
        result: (!state.dirtySinceCommit && state.resultBySection[section.id]) || previews[section.id] || null
      }))
    }
  },

  actions: {
    setForecastMode(mode) {
      const normalized = mode === 'manual' ? 'manual' : 'automatic'
      if (this.forecastMode === normalized) return
      this.forecastMode = normalized
      this.parameterLibrary = normalized === 'automatic' ? 'automatic-forecast' : 'simulation-rate'
      this.parameterVersionStatus = normalized === 'automatic' ? '生产参数待发布' : '可选择率定候选参数'
      this.markDirty()
    },
    setProjectSections(rows = []) {
      if (this.domainId === 'selinco') return
      const nextSections = rows.map((row) => ({
        id: row.id,
        name: row.name,
        river: row.sourceProperties?.河流 || row.sourceProperties?.O_Name || this.domainId,
        basePeak: Number(row.basePeak || 0),
        currentFlow: Number(row.currentFlow || 0),
        leadDefault: Number(row.leadDefault || 24),
        warnFlow: row.warnFlow == null ? null : Number(row.warnFlow),
        ensureFlow: row.ensureFlow == null ? null : Number(row.ensureFlow),
        basinId: this.domainId
      }))
      const previousTarget = this.targetSectionId
      this.sections = nextSections
      this.targetSectionId = nextSections.some((section) => section.id === previousTarget)
        ? previousTarget
        : nextSections[0]?.id || ''
      this.workspaces[this.contextKey] = captureWorkspace(this)
    },
    setContext(domainId, scale = '1km', modelConfigId = null) {
      const meta = contextMeta(domainId, scale, modelConfigId)
      const nextKey = meta.contextKey
      if (this.contextKey === nextKey) return
      if (this.contextKey) this.workspaces[this.contextKey] = captureWorkspace(this)
      const saved = this.workspaces[nextKey]
      applyWorkspace(this, saved || createWorkspace(meta.domainId, meta.modelScale, meta.modelConfigId))
    },
    setDomain(domainId) {
      this.setContext(domainId, this.modelScale || '1km')
    },
    setModelScale(scale) {
      this.setContext(this.domainId, scale)
    },
    markDirty() {
      if (this.committed) {
        this.dirtySinceCommit = true
        this.resultStatus = '条件已更新'
      }
    },
    prepareFromStation(station, dataTime) {
      if (!station) return
      const sectionId = station.type === 'section' ? station.id : station.sectionId || this.targetSectionId
      const section = this.sections.find((item) => item.id === sectionId)
      if (section) this.targetSectionId = section.id
      const time = dataTime || dayjs().format('YYYY-MM-DD HH:mm')
      this.startTime = time
      this.rainObsEnd = time
      this.leadHours = defaultLeadHours(section)
      this.resultStatus = '待预报'
      this.committed = null
      this.dirtySinceCommit = false
    },
    setTarget(id) {
      this.targetSectionId = id
      const section = this.sections.find((item) => item.id === id)
      if (section) this.leadHours = defaultLeadHours(section)
      this.markDirty()
    },
    setTweak(key, value) {
      const definition = PARAM_DEFS.find((item) => item.key === key)
      let nextValue = Number(value)
      if (definition) nextValue = Math.min(definition.max, Math.max(definition.min, nextValue))
      this.paramTweaks = {
        ...this.paramTweaks,
        [key]: definition?.step < 1 ? +nextValue.toFixed(3) : +nextValue.toFixed(1)
      }
      this.markDirty()
    },
    resetTweaks() {
      this.paramTweaks = {}
      this.markDirty()
    },
    loadParamVersion(version) {
      this.paramVersion = version
      this.baseParams = this.domainId === 'selinco' ? { ...(VERSION_PRESETS[version] || DEFAULT_PARAMS) } : {}
      this.paramTweaks = {}
      this.markDirty()
    },
    loadParameterVersion(version) {
      if (!version) return
      const id = typeof version === 'string' ? version : version.id
      if (!id) return
      this.paramVersion = id
      this.baseParams = {
        ...(this.domainId === 'selinco' ? { ...DEFAULT_PARAMS, ...(VERSION_PRESETS[id] || {}) } : {}),
        ...(typeof version === 'object' && version.parameters ? version.parameters : {})
      }
      this.paramTweaks = {}
      this.parameterVersionStatus = typeof version === 'object'
        ? (version.status === 'production' ? '已发布生产版本' : version.review?.status || '率定候选版本')
        : this.parameterVersionStatus
      this.markDirty()
    },
    setWetness(wetness, source) {
      this.wetness = wetness
      if (source) this.stateSource = source
      this.markDirty()
    },
    selectGrid(id) {
      this.selectedGridId = id
    },
    clearGrid() {
      this.selectedGridId = null
    },
    setStateVersion(version, source = 'continuation') {
      this.stateVersion = version
      this.stateSource = source
      this.markDirty()
    },
    setQualityOverride(reason = '') {
      this.qualityOverride = { requested: Boolean(String(reason).trim()), reason: String(reason).trim() }
      this.markDirty()
    },
    syncFromMonitor(payload) {
      if (!payload || (payload.domainId && payload.domainId !== this.domainId)) return
      if (!['selinco', 'nananxi-xufan'].includes(this.domainId)) return
      if (this.domainId === 'nananxi-xufan' && ['observed-ready', 'historical-ready'].includes(payload.status)) {
        this.dataCheck.rain = { status: 'ok', text: '11 站实测雨量已接入', block: false }
        this.dataCheck.hydro = { status: 'ok', text: '徐畈水位流量已接入', block: false }
        this.dataCheck.areal = { status: 'ok', text: '1 km 实测降雨场已就绪', block: false }
        this.dataCheck.level = { status: 'ok', text: '实测水位资料已接入', block: false }
        this.dataCheck.late = { status: 'ok', text: '资料窗口不判迟报', block: false }
        this.observationDataId = payload.qualityRecordId || this.observationDataId
        this.arealRainProduct = payload.arealNote || this.arealRainProduct
        this.markDirty()
        return
      }
      const { issues = [], excluded = [], arealNote } = payload
      const late = issues.filter((item) => item.issue === '迟报').length
      const missing = issues.filter((item) => item.issue === '缺测').length
      const abnormal = issues.filter((item) => item.issue === '异常' || item.severity === 'bad')

      this.dataCheck.late = { status: late ? 'warn' : 'ok', text: late ? `${late} 站迟报` : '无迟报', block: false }
      this.dataCheck.rain = {
        status: missing || excluded.length ? 'warn' : 'ok',
        text: missing ? `${missing} 站缺测` : excluded.length ? `${excluded.length} 站已屏蔽` : '正常',
        block: false
      }
      const levelIssue = abnormal.find((item) => item.type === 'water')
      this.dataCheck.level = levelIssue
        ? { status: 'bad', text: levelIssue.detail || '水位异常', block: true }
        : { status: 'ok', text: '正常', block: false }
      if (arealNote) this.arealRainProduct = arealNote
      if (payload.qualityRecordId) this.observationDataId = payload.qualityRecordId
      if (payload.forecastPrecipitationId) this.forecastPrecipitationId = payload.forecastPrecipitationId
      this.markDirty()
    },
    loadFromArchive(record) {
      if (!record) return
      const section = this.sections.find((item) => item.name === record.section)
      if (section) this.targetSectionId = section.id
      this.startTime = record.start
      this.rainObsEnd = record.start
      this.paramVersion = record.param || this.paramVersion
      if (record.params) {
        this.baseParams = { ...DEFAULT_PARAMS, ...record.params }
        this.paramTweaks = {}
      } else {
        this.loadParamVersion(String(record.param || this.paramVersion).replace(/-tune.*$/, ''))
      }
      this.stateVersion = record.state || this.stateVersion
      this.stateSource = 'archive'
      this.dataVersion = record.data || this.dataVersion
      this.observationDataId = record.inputSources?.observation?.id || record.data || this.observationDataId
      this.forecastPrecipitationId = record.inputSources?.precipitation?.id || record.data || this.forecastPrecipitationId
      this.modelProjectId = record.modelProjectId || this.modelProjectId
      this.modelConfigId = record.modelConfigId || this.modelConfigId
      this.outputScope = record.outputScope || this.outputScope
      this.leadHours = Math.min(72, Math.max(6, Number(record.leadHours) || defaultLeadHours(section)))
      this.wetness = record.wetness ?? this.wetness
      this.forecastMode = record.forecastMode === 'manual' ? 'manual' : 'automatic'
      this.parameterLibrary = record.parameterLibrary || (this.forecastMode === 'manual' ? 'simulation-rate' : 'automatic-forecast')
      this.resultStatus = '待预报'
      this.committed = null
      this.dirtySinceCommit = false
    },
    prepareNextRun(hours = 6) {
      const previousTaskId = this.committed?.taskId || this.activeTaskId || null
      const nextStart = dayjs(this.startTime).add(hours, 'hour').format('YYYY-MM-DD HH:mm')
      this.startTime = nextStart
      this.rainObsEnd = nextStart
      this.committed = null
      this.resultBySection = {}
      this.resultStatus = '待预报'
      this.activeTaskId = null
      this.dirtySinceCommit = false
      return { nextStart, sourceTaskId: previousTaskId }
    },
    async runForecast() {
      if (!this.canRun) return false
      if (this.executionAvailability.mode !== 'frontend-fixture') {
        this.resultStatus = '正式预报服务尚未接入，不使用演示结果替代'
        return false
      }
      const requestedContextKey = this.contextKey
      this.running = true
      try {
        let thresholds = {}
        try {
          thresholds = useConfigStore().thresholds
        } catch {
          thresholds = {}
        }

        const tasks = useTasksStore()
        const submitted = await tasks.submitTask('forecast', {
          contextKey: this.contextKey,
          domainId: this.domainId,
          modelScale: this.modelScale,
          modelConfigId: this.modelConfigId,
          sectionId: this.targetSectionId,
          sectionName: this.targetSection?.name,
          startTime: this.startTime,
          leadHours: this.leadHours,
          wetness: this.wetness,
          params: this.effectiveParams,
          sections: this.sections.map((section) => ({
            id: section.id,
            name: section.name,
            basePeak: section.basePeak,
            currentFlow: section.currentFlow
          })),
          thresholds,
          parameterVersionId: this.paramVersion,
          forecastMode: this.forecastMode,
          parameterLibrary: this.parameterLibrary,
          stateVersionId: this.stateVersion,
          observationDataId: this.observationDataId,
          forecastPrecipitationId: this.forecastPrecipitationId,
          issueTime: this.startTime
        }, `forecast:${this.contextKey}`)
        if (this.contextKey === requestedContextKey) this.activeTaskId = submitted.taskId
        const task = await tasks.waitForTask(submitted.taskId)
        if (this.contextKey !== requestedContextKey) {
          const workspace = this.workspaces[requestedContextKey]
          if (workspace) {
            workspace.activeTaskId = submitted.taskId
            workspace.running = false
            workspace.resultStatus = '任务记录保留在原工作区'
          }
          return false
        }
        if (task?.status !== 'completed') {
          this.resultStatus = task?.status === 'cancelled' ? '已停止' : '运行失败'
          return false
        }
        const fixtureResult = await tasks.getResult(task.taskId)
        if (this.contextKey !== requestedContextKey) return false
        if (task.resultSource !== 'frontend-fixture' || fixtureResult?.resultSource !== 'frontend-fixture'
          || !Array.isArray(fixtureResult.outputs) || !fixtureResult.outputs.length
          || fixtureResult.outputs.some((output) => !output.hydrograph?.forecast?.length || !output.summary)) {
          this.resultStatus = '结果缺失，不生成替代过程线'
          throw new Error('试算任务未返回完整结果，不能用前端生成曲线补齐。')
        }
        const outputs = fixtureResult.outputs
        const focused = outputs.find((item) => item.sectionId === this.targetSectionId) || outputs[0]
        if (!focused) return false

        this.resultBySection = Object.fromEntries(outputs.map((item) => [item.sectionId, item]))
        this.committed = {
          runId: task.taskId,
          taskId: task.taskId,
          resultVersionId: task.resultVersionId,
          resultSource: task.resultSource,
          outputs,
          focusSectionId: focused.sectionId,
          productId: focused.productId,
          hydrograph: focused.hydrograph,
          summary: { ...focused.summary }
        }
        this.dirtySinceCommit = false
        this.resultStatus = '待复核'
        return task.taskId
      } finally {
        if (this.contextKey === requestedContextKey) this.running = false
      }
    },
    async cancelForecast() {
      if (!this.activeTaskId) return null
      const requestedContextKey = this.contextKey
      const task = await useTasksStore().cancelTask(this.activeTaskId)
      if (this.contextKey !== requestedContextKey) return task
      this.running = false
      this.resultStatus = '已停止'
      return task
    },
    createArchiveRecord(runId) {
      if (!this.committed?.hydrograph || this.dirtySinceCommit || !['frontend-fixture', 'model-backend'].includes(this.committed.resultSource)) {
        throw new Error('尚无已完成且与当前条件一致的预报结果，不能归档预览曲线。')
      }
      const summary = this.currentScheme
      const hydrograph = this.displayHydrograph
      const resolvedRunId = this.committed?.runId || runId || createForecastRunId(this)
      const focused = this.resultBySection[this.targetSectionId]
      const productId = focused?.productId || `${resolvedRunId}::${this.targetSectionId}`

      return {
        id: productId,
        productId,
        runId: resolvedRunId,
        taskId: this.committed?.taskId || this.activeTaskId || resolvedRunId,
        runType: 'forecast',
        resultSource: this.committed.resultSource,
        resultVersionId: this.committed?.resultVersionId || null,
        section: this.targetSection.name,
        sectionId: this.targetSectionId,
        start: this.startTime,
        peak: summary.peak,
        peakTime: summary.peakTime,
        peakLevel: summary.peakLevel,
        level: summary.level,
        warnLevel: summary.warnLevel,
        ensureLevel: summary.ensureLevel,
        volume: summary.volume,
        param: this.paramVersion,
        state: this.stateVersion,
        data: this.dataVersion,
        rainQpfSource: this.rainQpfSource,
        routingVersionId: this.committed?.request?.routing?.versionId || null,
        lineage: this.committed?.lineage || null,
        request: this.committed?.request || null,
        inputSources: {
          state: { id: this.stateVersion, label: '已采用起报状态' },
          precipitation: { id: this.dataVersion, label: this.rainQpfSource },
          observation: { id: this.committed?.request?.inputs?.bundleVersionId || this.observationDataId, label: '起报实测资料' },
          parameter: { id: this.paramVersion, label: this.modelConfigId }
        },
        modelProjectId: this.modelProjectId,
        modelConfigId: this.modelConfigId,
        modelVersion: this.modelConfigId,
        domainId: this.domainId,
        modelScale: this.modelScale,
        outputScope: this.outputScope,
        status: this.resultStatus === '计算完成' ? '待复核' : this.resultStatus,
        leadHours: this.leadHours,
        params: { ...this.effectiveParams },
        forecastMode: this.forecastMode,
        parameterLibrary: this.parameterLibrary,
        wetness: this.wetness,
        hydrograph,
        outputSummaries: this.outputSections.map((item) => ({
          sectionId: item.id,
          section: item.name,
          ...(item.result?.summary || {})
        })),
        type: this.committed.resultSource === 'model-backend' ? '降雨情景预报' : '实时报',
        operator: '值班预报员',
        repro: true
      }
    },
  }
})
