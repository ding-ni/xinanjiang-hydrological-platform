import { defineStore } from 'pinia'
import {
  stations,
  basinSummary,
  buildRainTimeline,
  BASIN_CENTER,
  DEFAULT_ZOOM
} from '@/data/basin'
import { useConfigStore } from '@/stores/config'
import { useAppStore } from '@/stores/app'
import { normalizeBasinId } from '@/data/domainContext'
import {
  loadNananxiFloodEvents,
  loadNananxiLatestPreview,
  loadNananxiLatestWindowPreview,
  loadNananxiObservationCatalog,
  loadNananxiWindowPreview,
  clearNananxiObservationCache
} from '@/services/historicalObservationService'
import {
  finiteInputValue,
  loadModelInputCatalog,
  loadModelInputWindow,
  loadModelInputEvent,
  sliceModelInputWindow,
  usesModelInputService
} from '@/services/modelInputService'

let historicalWindowRequestId = 0
const PLAYBACK_STEP_MS = 420
const PLAYBACK_SPEEDS = Object.freeze([0.5, 1, 2, 4])

function historicalEventKey(event) {
  return String(event?.eventId || event?.id || event?.sourceFloodNo || '')
}

function mergeHistoricalEventCatalog(current = [], loaded = []) {
  const currentRows = (Array.isArray(current) ? current : [])
    .filter((event) => !event?.basinProjectId || event.basinProjectId === 'nananxi-xufan')
  const currentById = new Map(currentRows.map((event) => [historicalEventKey(event), event]))
  const loadedKeys = new Set()
  const merged = (Array.isArray(loaded) ? loaded : []).map((event) => {
    const key = historicalEventKey(event)
    loadedKeys.add(key)
    return currentById.has(key) ? { ...event, ...currentById.get(key) } : event
  })
  currentRows.forEach((event) => {
    const key = historicalEventKey(event)
    if (key && !loadedKeys.has(key)) merged.push(event)
  })
  return merged
}

const defaultLayers = {
  rain: true,
  hydro: true,
  water: true,
  section: true,
  river: true,
  subbasin: true,
  rainField: true,
  smoothRain: true,
  dem: true,
  grid: true
}

const issueSeed = [
  { id: 'R-33068125', name: '建德雨量站', type: 'rain', issue: '迟报', detail: '最近到报 13:50，滞后 10 min', severity: 'warn', handle: '待处理' },
  { id: 'W-33000205', name: '新安江水位站', type: 'water', issue: '异常', detail: '水位跳变，待复核', severity: 'bad', handle: '待处理' },
  { id: 'R-33068122', name: '深渡雨量站', type: 'rain', issue: '缺测', detail: '12:00–12:30 时段缺测', severity: 'bad', handle: '待处理' }
]

/** 雨量站 → 子流域（面雨量归属） */
const RAIN_STATION_BASIN = {
  'R-33068110': '昌溪',
  'R-33068118': '寿昌',
  'R-33068122': '昌溪',
  'R-33068125': '寿昌'
}

const baseHydro = {
  'H-33000210': { flow: 1040, level: 28.6 },
  'H-33000220': { flow: 860, level: 26.2 },
  'W-33000205': { level: 31.8 },
  'SEC-33000200': { flow: 1230, level: 31.8 }
}

const SELINCO_QUALITY = Object.freeze({
  issues: [],
  excluded: [],
  qualityRecordId: 'QC-SEL-20260602-1400',
  source: 'selinco_hourly_observation_qc',
  status: 'ok'
})

function activeBasinId(fallback = 'xinanjiang') {
  try {
    return normalizeBasinId(useAppStore().basinId)
  } catch {
    return normalizeBasinId(fallback)
  }
}

function activeObservationSource() {
  try {
    return useAppStore().activeContext
  } catch {
    return null
  }
}

function requiresModelInput() {
  return usesModelInputService(activeObservationSource())
}

const trustedObservation = preview => ['model-qc', 'database-observed'].includes(preview?.sourceType)

function localHour(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:00`
}

function buildQualityPayload(state, basinId) {
  if (basinId === 'selinco') {
    return {
      ...SELINCO_QUALITY,
      issues: [...SELINCO_QUALITY.issues],
      excluded: [...SELINCO_QUALITY.excluded],
      basinId,
      // Do not reuse the XinAnJiang rain interpolation label or water-level issue.
      arealNote: `\u8272\u6797\u9519\u9010\u5c0f\u65f6\u964d\u6c34\u573a \u00b7 ${state.currentTimeLabel}`
    }
  }

  if (basinId === 'nananxi-xufan') {
    if (requiresModelInput()) {
      const ready = trustedObservation(state.historicalPreview) && Boolean(state.modelInputProduct)
      const index = Math.min(state.timeIndex, (state.historicalPreview?.times?.length || 1) - 1)
      const rain = finiteInputValue(state.modelInputSummary?.arealRainfallMm?.[index])
      const flow = finiteInputValue(state.historicalPreview?.river?.flows?.[index])
      const missing = !ready || rain == null || flow == null
      return {
        basinId,
        issues: missing ? [{ id: 'MODEL-QC-INPUT', issue: '缺测', severity: 'bad', detail: state.historicalError || '当前时次缺少整编降雨或出口流量' }] : [],
        excluded: [],
        arealNote: `徐畈控制区面雨量 · ${state.currentTimeLabel}`,
        qualityRecordId: state.modelInputProvenance?.forcingVersion || null,
        source: state.historicalPreview?.sourceType === 'database-observed' ? 'historical-database' : 'model-backend-qc-inputs',
        status: missing ? 'missing' : state.historicalPreview?.sourceType === 'database-observed' ? 'observed-ready' : 'qc-ready',
        productionEligible: false
      }
    }
    return {
      basinId,
      issues: [],
      excluded: [],
      arealNote: `南安溪离线站点资料预览 · ${state.currentTimeLabel}`,
      qualityRecordId: 'XF-HISTORICAL-OBSERVATION-CATALOG',
      source: 'xufan_mdb_observed_data',
      status: 'observed-ready'
    }
  }

  return {
    basinId,
    issues: state.dataIssues,
    excluded: [...state.excludedRainStations],
    arealNote: `\u9762\u96e8\u91cf ${state.currentTimeLabel} \u00b7 ${state.rainWindow}h`,
    qualityRecordId: 'QC-XAJ-20260602-1400',
    source: `${basinId}_station_observation_qc`,
    status: state.dataIssues.some((item) => item.severity === 'bad') ? 'warn' : 'ok'
  }
}

export const useMonitorStore = defineStore('monitor', {
  state: () => ({
    // Xinanjiang station playback remains in this store; the active payload is
    // resolved from app.basinId so it cannot leak into another basin.
    activeDomainId: 'xinanjiang',
    stations: stations.map((s) => ({ ...s })),
    summary: {
      basinName: basinSummary.basinName,
      targetSection: basinSummary.targetSection,
      currentFlow: basinSummary.currentFlow,
      currentLevel: 31.8,
      reportRate: basinSummary.reportRate,
      normalStations: basinSummary.normalStations,
      lateStations: basinSummary.lateStations,
      missingStations: basinSummary.missingStations,
      abnormal: basinSummary.abnormal,
      lastDataTime: basinSummary.lastDataTime,
      rain1hBasin: 11.2,
      rain3hBasin: 28.6,
      rain24hBasin: 86.4
    },
    layers: {
      rain: true,
      hydro: true,
      water: true,
      section: true,
      river: true,
      subbasin: true,
      rainField: true,
      smoothRain: false,
      qpfGrid: true,
      dem: true,
      grid: true
    },
    rainDisplayMode: 'grid',
    selectedStationId: null,
    detailOpen: false,
    mapCenter: [...BASIN_CENTER],
    mapZoom: DEFAULT_ZOOM,
    timeIndex: 36,
    timeMin: 0,
    timeMax: 36,
    timelineStart: '2026-06-02 08:00',
    timelineEnd: '2026-06-02 14:00',
    timelineStepMinutes: 10,
    timelineLabel: '10 分钟',
    playing: false,
    playbackSpeed: 1,
    playTimer: null,
    playLastAt: 0,
    rainWindow: 3,
    dataIssues: issueSeed.map((x) => ({ ...x })),
    highlightedIssueId: null,
    subbasinRain: [
      { id: 'B01', name: '昌溪', rain1h: 9.2, rain3h: 24.1, rain6h: 38.0, rain24h: 72.5, inAreal: true },
      { id: 'B02', name: '榉桐', rain1h: 12.5, rain3h: 31.8, rain6h: 48.2, rain24h: 91.0, inAreal: true },
      { id: 'B03', name: '兰江', rain1h: 7.8, rain3h: 19.6, rain6h: 30.5, rain24h: 64.2, inAreal: true },
      { id: 'B04', name: '新叶', rain1h: 10.1, rain3h: 26.4, rain6h: 41.0, rain24h: 80.3, inAreal: true },
      { id: 'B05', name: '寿昌', rain1h: 15.6, rain3h: 42.0, rain6h: 61.5, rain24h: 112.8, inAreal: true },
      { id: 'B06', name: '大洋', rain1h: 6.4, rain3h: 16.2, rain6h: 25.8, rain24h: 55.0, inAreal: true }
    ],
    excludedRainStations: [],
    historicalCatalog: null,
    historicalEvents: [],
    historicalPreview: null,
    historicalLoading: false,
    historicalError: '',
    historicalDataRevision: 0,
    historicalReplayMode: 'latest',
    historicalReplayEventId: null,
    historicalReplayLabel: '最新共同覆盖时段',
    modelInputProduct: null,
    modelInputSummary: null,
    modelInputProvenance: null,
    modelInputEventQuality: null
  }),
  getters: {
    selectedStation(state) {
      return state.stations.find((s) => s.id === state.selectedStationId) || null
    },
    currentTimeLabel(state) {
      if (requiresModelInput() && !state.historicalPreview) return '—'
      // Registered QC timestamps already include seconds; appending another
      // ':00' would make the time invalid and detach map playback from inputs.
      const start = new Date(state.timelineStart.replace(' ', 'T'))
      const t = new Date(start.getTime() + state.timeIndex * state.timelineStepMinutes * 60 * 1000)
      const y = t.getFullYear()
      const mo = String(t.getMonth() + 1).padStart(2, '0')
      const d = String(t.getDate()).padStart(2, '0')
      const hh = String(t.getHours()).padStart(2, '0')
      const mm = String(t.getMinutes()).padStart(2, '0')
      return `${y}-${mo}-${d} ${hh}:${mm}`
    },
    selectedRainSeries(state) {
      if (requiresModelInput() && !trustedObservation(state.historicalPreview)) return []
      if (state.activeDomainId === 'nananxi-xufan' && state.historicalPreview) {
        const station = state.stations.find((item) => item.id === state.selectedStationId)
        return this.historicalRainSeries(station?.code || station?.id)
      }
      const id = state.selectedStationId || 'R-33068110'
      return buildRainTimeline(id).slice(0, state.timeIndex + 1)
    },
    hasHistoricalObservations: (state) => Boolean(state.historicalCatalog && state.historicalPreview
      && (!requiresModelInput() || (trustedObservation(state.historicalPreview) && state.modelInputProduct))),
    historicalStationSnapshot(state) {
      return (spatialCode) => {
        if (!state.historicalPreview || !state.historicalCatalog) return null
        if (requiresModelInput() && !trustedObservation(state.historicalPreview)) return null
        const station = state.historicalCatalog.rain.stations.find((item) => item.spatialCode === spatialCode)
        const index = Math.min(state.timeIndex, state.historicalPreview.times.length - 1)
        const rainValues = station ? state.historicalPreview.rain[station.legacyCode] : null
        const rain = finiteInputValue(rainValues?.[index])
        const knownRain = rainValues?.slice(0, index + 1).map(finiteInputValue)
        const rainComplete = Boolean(knownRain?.length) && knownRain.every((value) => value != null)
        const rainTotal = rainComplete ? knownRain.reduce((sum, value) => sum + value, 0) : null
        const isRiver = state.historicalPreview.river.spatialCode === spatialCode
        const modelQC = state.historicalPreview.sourceType === 'model-qc'
        const flow = isRiver ? finiteInputValue(state.historicalPreview.river.flows?.[index]) : null
        return {
          observationReady: Boolean(rainValues?.some((value) => finiteInputValue(value) != null) || (isRiver && state.historicalPreview.river.flows?.some((value) => finiteInputValue(value) != null))),
          hasRainObservation: Boolean(rainValues),
          hasRiverObservation: isRiver,
          rain1h: rain,
          rainTotal: rainTotal == null ? null : +rainTotal.toFixed(1),
          rainDataComplete: rainComplete,
          level: isRiver ? finiteInputValue(state.historicalPreview.river.levels?.[index]) : null,
          flow,
          lastTime: state.historicalPreview.times[index]?.replace('T', ' '),
          quality: modelQC ? isRiver ? (flow == null ? '流量缺测' : '整编实测流量') : '站点实测请查询水文资料库' : '历史实测记录',
          sourceLabel: modelQC ? isRiver ? '徐畈控制断面 · 整编逐时流量' : '模型计算资料为网格降雨' : '水文资料库 · 原始实测记录',
          sourceType: state.historicalPreview.sourceType,
          interval: '逐小时'
        }
      }
    },
    historicalRainSeries(state) {
      return (spatialCode) => {
        if (!state.historicalPreview || !state.historicalCatalog) return []
        if (requiresModelInput() && !trustedObservation(state.historicalPreview)) return []
        const station = state.historicalCatalog.rain.stations.find((item) => item.spatialCode === spatialCode)
        const values = station ? state.historicalPreview.rain[station.legacyCode] : null
        if (!values) return []
        let cumulative = 0
        let complete = true
        return state.historicalPreview.times.slice(0, state.timeIndex + 1).map((time, index) => {
          const rain = finiteInputValue(values[index])
          if (rain == null) complete = false
          else cumulative += rain
          return { time, label: time.slice(5).replace('T', ' '), rain, cum: complete ? +cumulative.toFixed(1) : null, complete }
        })
      }
    },
    historicalFlowSeries(state) {
      if (!state.historicalPreview || (requiresModelInput() && !trustedObservation(state.historicalPreview))) return []
      return state.historicalPreview.times.slice(0, state.timeIndex + 1).map((time, index) => ({
        time, label: time.slice(5).replace('T', ' '), flow: finiteInputValue(state.historicalPreview.river?.flows?.[index])
      }))
    },
    subbasinRainRows(state) {
      const key = `rain${state.rainWindow}h`
      const blockedBasins = new Set()
      state.excludedRainStations.forEach((id) => {
        const b = RAIN_STATION_BASIN[id]
        if (b) blockedBasins.add(b)
      })
      return state.subbasinRain.map((b) => {
        let rain = b[key] ?? b.rain3h
        const blocked = blockedBasins.has(b.name)
        if (blocked) rain = +(rain * 0.75).toFixed(1)
        else if (state.excludedRainStations.length) rain = +(rain * 0.95).toFixed(1)
        return {
          ...b,
          rain,
          inAreal: b.inAreal && !blocked
        }
      })
    },
    qualityStats(state) {
      const late = state.dataIssues.filter((i) => i.issue === '迟报').length
      const missing = state.dataIssues.filter((i) => i.issue === '缺测').length
      const abnormal = state.dataIssues.filter((i) => i.issue === '异常' || i.severity === 'bad').length
      const total = state.stations.length || 40
      const problem = new Set(state.dataIssues.map((i) => i.id)).size
      const normal = Math.max(0, total - problem)
      const reportRate = Math.max(70, Math.round(100 - late * 2 - missing * 3 - state.excludedRainStations.length * 1.5))
      return { late, missing, abnormal, normal, reportRate, problem }
    },
    /** 推送给预报的质控摘要 */
    qualityPayload(state) {
      return buildQualityPayload(state, activeBasinId(state.activeDomainId))
    },
    /** Returns a quality record for a selected basin. */
    qualityPayloadByBasin(state) {
      return (basinId) => buildQualityPayload(state, normalizeBasinId(basinId))
    },
    /** Semantic alias used by forecast workflows. */
    qualityPayloadFor(state) {
      return (basinId) => buildQualityPayload(state, normalizeBasinId(basinId))
    },
    basinRainValue(state) {
      if (requiresModelInput()) return finiteInputValue(state.modelInputSummary?.arealRainfallMm?.[state.timeIndex])
      const s = state.summary
      let v =
        state.rainWindow === 1
          ? s.rain1hBasin
          : state.rainWindow === 3
            ? s.rain3hBasin
            : state.rainWindow === 6
              ? s.rain3hBasin * 1.6
              : s.rain24hBasin
      if (state.excludedRainStations.length) v = +(Number(v) * 0.94).toFixed(1)
      return v
    }
  },
  actions: {
    setTimelineContext(domainId) {
      if (this.activeDomainId !== normalizeBasinId(domainId)) historicalWindowRequestId += 1
      this.stopPlay()
      this.activeDomainId = normalizeBasinId(domainId)
      if (this.activeDomainId === 'selinco') {
        this.timelineStart = '2026-06-01 00:00'
        this.timelineEnd = '2026-06-06 00:00'
        this.timelineStepMinutes = 60
        this.timelineLabel = '逐小时'
        this.timeMin = 0
        this.timeMax = 120
        this.timeIndex = Math.min(120, Math.max(0, this.timeIndex === 36 ? 38 : this.timeIndex))
      } else if (this.activeDomainId === 'nananxi-xufan' && this.historicalPreview) {
        this.applyHistoricalTimeline()
      } else {
        this.timelineStart = '2026-06-02 08:00'
        this.timelineEnd = '2026-06-02 14:00'
        this.timelineStepMinutes = 10
        this.timelineLabel = '10 分钟'
        this.timeMin = 0
        this.timeMax = 36
        this.timeIndex = Math.min(36, this.timeIndex)
      }
    },
    async loadHistoricalPreview({ syncDataTime = true } = {}) {
      if (requiresModelInput()) return this.loadBackendInputWindow({ mode: 'latest', label: '雨量与流量共同覆盖时段', syncDataTime })
      if (this.historicalPreview && this.historicalCatalog && this.historicalEvents.length) {
        if (this.activeDomainId === 'nananxi-xufan') this.applyHistoricalTimeline()
        return
      }
      this.historicalLoading = true
      this.historicalError = ''
      try {
        const source = activeObservationSource()
        const [catalog, eventsCatalog] = await Promise.all([
          loadNananxiObservationCatalog({ source }),
          loadNananxiFloodEvents({ source })
        ])
        this.historicalCatalog = catalog
        const loadedEvents = Array.isArray(eventsCatalog?.events) ? eventsCatalog.events : []
        this.historicalEvents = mergeHistoricalEventCatalog(this.historicalEvents, loadedEvents)
        try {
          this.historicalPreview = await loadNananxiLatestWindowPreview({ catalog, source })
        } catch {
          this.historicalPreview = await loadNananxiLatestPreview({ source })
        }
        this.historicalReplayMode = 'latest'
        this.historicalReplayEventId = null
        this.historicalReplayLabel = '最新共同覆盖时段'
        if (this.activeDomainId === 'nananxi-xufan') this.applyHistoricalTimeline()
      } catch (error) {
        this.historicalError = error instanceof Error ? error.message : String(error)
      } finally {
        this.historicalLoading = false
      }
    },
    async loadHistoricalWindow(start, end, options = {}) {
      if (requiresModelInput()) return this.loadBackendInputWindow({ start, end, ...options })
      const requestId = ++historicalWindowRequestId
      this.stopPlay()
      this.historicalLoading = true
      this.historicalError = ''
      try {
        const preview = await loadNananxiWindowPreview(start, end, {
          catalog: this.historicalCatalog,
          source: activeObservationSource()
        })
        if (requestId !== historicalWindowRequestId) return preview
        this.historicalPreview = preview
        this.historicalReplayMode = options.mode || 'range'
        this.historicalReplayEventId = options.eventId || null
        this.historicalReplayLabel = options.label || '指定资料时段'
        this.applyHistoricalTimeline({ reset: true })
        return preview
      } catch (error) {
        if (requestId === historicalWindowRequestId) this.historicalError = error instanceof Error ? error.message : String(error)
        throw error
      } finally {
        if (requestId === historicalWindowRequestId) this.historicalLoading = false
      }
    },
    async loadLatestHistoricalWindow(hours = 72, { refresh = false } = {}) {
      if (requiresModelInput()) return this.loadBackendInputWindow({ hours, refresh, mode: 'latest', label: '雨量与流量共同覆盖时段' })
      const requestId = ++historicalWindowRequestId
      this.stopPlay()
      this.historicalLoading = true
      this.historicalError = ''
      try {
        const source = activeObservationSource()
        if (refresh) {
          clearNananxiObservationCache()
          const [catalog, eventsCatalog] = await Promise.all([
            loadNananxiObservationCatalog({ refresh: true, source }),
            loadNananxiFloodEvents({ refresh: true, source })
          ])
          this.historicalCatalog = catalog
          const loadedEvents = Array.isArray(eventsCatalog?.events) ? eventsCatalog.events : []
          this.historicalEvents = mergeHistoricalEventCatalog(this.historicalEvents, loadedEvents)
          this.historicalDataRevision += 1
        } else if (!this.historicalCatalog || !this.historicalEvents.length) {
          await this.loadHistoricalPreview()
        }
        let preview
        try {
          preview = await loadNananxiLatestWindowPreview({ hours, catalog: this.historicalCatalog, refresh, source })
        } catch {
          preview = await loadNananxiLatestPreview({ refresh, source })
        }
        if (requestId !== historicalWindowRequestId) return preview
        this.historicalPreview = preview
        this.historicalReplayMode = 'latest'
        this.historicalReplayEventId = null
        this.historicalReplayLabel = '最新共同覆盖时段'
        this.applyHistoricalTimeline({ reset: true })
        return preview
      } catch (error) {
        if (requestId === historicalWindowRequestId) this.historicalError = error instanceof Error ? error.message : String(error)
        throw error
      } finally {
        if (requestId === historicalWindowRequestId) this.historicalLoading = false
      }
    },
    async loadHistoricalEvent(eventId) {
      if (requiresModelInput()) {
        return this.loadBackendInputWindow({ eventId, mode: 'event' })
      }
      if (!this.historicalEvents.length) await this.loadHistoricalPreview()
      const event = this.historicalEvents.find((item) => String(item.eventId || item.id) === String(eventId))
      if (!event) throw new Error('未找到对应洪水场次')
      const sourceFloodNo = event.sourceFloodNo || event.floodCode || event.eventId || event.id
      const observedPeak = finiteInputValue(event.observedPeak)
      const peak = observedPeak != null ? ` · Qmax ${observedPeak.toLocaleString()} m³/s` : ''
      return this.loadHistoricalWindow(event.start, event.end, {
        mode: 'event',
        eventId: event.eventId || event.id,
        label: `${sourceFloodNo}${peak}`
      })
    },
    syncHistoricalEvents(events = []) {
      if (!Array.isArray(events) || !events.length) return
      this.historicalEvents = events.map((event) => ({ ...event }))
    },
    clearModelInputWindow(message = '') {
      this.historicalPreview = null
      this.modelInputProduct = null
      this.modelInputSummary = null
      this.modelInputProvenance = null
      this.modelInputEventQuality = null
      this.historicalError = message
      this.stopPlay()
    },
    invalidateBackendInput(message = '', { clearCatalog = false } = {}) {
      historicalWindowRequestId += 1
      this.clearModelInputWindow(message)
      this.historicalLoading = false
      if (clearCatalog) {
        this.historicalCatalog = null
        this.historicalEvents = []
        this.historicalReplayEventId = null
        this.historicalReplayLabel = ''
        this.historicalDataRevision += 1
      }
    },
    async loadBackendInputWindow({ start = '', end = '', hours = 72, refresh = false, mode = 'range', eventId = null, label = '', syncDataTime = true } = {}) {
      const requestId = ++historicalWindowRequestId
      const source = activeObservationSource()
      const selectionKey = source?.inputSelectionKey
      const stillSelected = () => requestId === historicalWindowRequestId && selectionKey === activeObservationSource()?.inputSelectionKey
      this.stopPlay()
      this.clearModelInputWindow()
      this.historicalLoading = true
      try {
        const { loadHistoricalDatabaseCatalog, loadHistoricalDatabaseProcess } = await import('@/services/modelInputService')
        const catalog = !refresh && this.historicalCatalog?.sourceType === 'database-observed'
          ? this.historicalCatalog : await loadHistoricalDatabaseCatalog({ source })
        if (!stillSelected()) return null
        this.historicalCatalog = catalog
        // Registered statistics are authoritative for this version; merging a
        // previous bundle over them would silently restore obsolete rainfall.
        this.historicalEvents = catalog.events.map((event) => ({ ...event }))
        let input
        let replayLabel = label
        if (eventId) {
          const event = catalog.events.find((item) => String(item.eventId || item.id) === String(eventId))
          if (!event) throw new Error('选定计算资料不包含该洪水场次')
          input = await loadHistoricalDatabaseProcess('', '', { source, eventId })
          replayLabel = `${event.sourceFloodNo || event.eventId || event.id} · 历史库洪水过程`
        } else {
          if (!start && !end) {
            const common = catalog.coverage?.common || catalog.latestPeriod || {}
            const last = new Date(common.end || catalog.latestPeriod?.end)
            const earliest = new Date(common.start || catalog.river.start)
            if (!Number.isFinite(last.getTime()) || !Number.isFinite(earliest.getTime())) throw new Error('选定计算资料的共同覆盖时段无效')
            const first = new Date(Math.max(earliest.getTime(), last.getTime() - (Math.max(1, Number(hours) || 72) - 1) * 3600000))
            start = localHour(first)
            end = localHour(last)
          }
          input = await loadHistoricalDatabaseProcess(start, end, { source })
        }
        if (!stillSelected() || !requiresModelInput()) return null
        this.historicalPreview = input.observationPreview
        this.modelInputProduct = input.product
        this.modelInputSummary = input.summary
        this.modelInputProvenance = input.provenance
        this.modelInputEventQuality = input.quality || null
        this.historicalReplayMode = mode
        this.historicalReplayEventId = eventId
        this.historicalReplayLabel = replayLabel || '历史库指定时段'
        this.historicalDataRevision += 1
        this.applyHistoricalTimeline({ reset: true })
        if (syncDataTime) useAppStore().setDataTime(this.currentTimeLabel)
        return this.historicalPreview
      } catch (error) {
        if (stillSelected()) this.clearModelInputWindow(error instanceof Error ? error.message : String(error))
        throw error
      } finally {
        if (stillSelected()) this.historicalLoading = false
      }
    },
    applyHistoricalTimeline({ reset = false } = {}) {
      const preview = this.historicalPreview
      if (!preview?.times?.length) return
      this.timelineStart = preview.start.replace('T', ' ')
      this.timelineEnd = preview.end.replace('T', ' ')
      this.timelineStepMinutes = Number(preview.stepHours || 1) * 60
      this.timelineLabel = preview.sourceType === 'model-qc' ? '整编逐时资料' : preview.sourceType === 'database-observed' ? '历史库逐时实测' : '逐小时离线资料'
      this.timeMin = 0
      this.timeMax = preview.times.length - 1
      this.timeIndex = reset ? 0 : Math.min(this.timeMax, Math.max(this.timeMin, this.timeIndex))
    },
    selectStation(id, openDetail = true) {
      this.selectedStationId = id
      if (openDetail) this.detailOpen = true
    },
    closeDetail() {
      this.detailOpen = false
    },
    setLayer(key, value) {
      if (key in this.layers) this.layers[key] = value
    },
    setRainDisplayMode(mode) {
      if (!['grid', 'smooth'].includes(mode)) return
      this.rainDisplayMode = mode
      this.layers.qpfGrid = true
    },
    setRainWindow(h) {
      this.rainWindow = h
      if (requiresModelInput()) return
      // 热力权重：按时段缩放 rain 显示
      const scale = h === 1 ? 0.4 : h === 3 ? 1 : h === 6 ? 1.5 : 2.8
      this.stations = this.stations.map((s) => {
        if (s.type !== 'rain') return s
        const series = buildRainTimeline(s.id)
        const point = series[this.timeIndex] || series[series.length - 1]
        return {
          ...s,
          rain1h: +(point.rain * scale).toFixed(1),
          rainTotal: point.cum,
          lastTime: this.currentTimeLabel
        }
      })
    },
    setTimeIndex(index) {
      this.timeIndex = Math.min(this.timeMax, Math.max(this.timeMin, Number(index)))
      if (requiresModelInput() && !this.historicalPreview) return
      if (this.activeDomainId === 'nananxi-xufan' && this.historicalPreview) {
        // 拖动或播放时只更新内存中的当前时刻，结束交互后再写入工作上下文，
        // 避免每个小时都触发一次本地存储序列化。
        useAppStore().setDataTime(this.currentTimeLabel, { persist: false })
        return
      }
      const progress = this.timeIndex / this.timeMax
      const scale = this.rainWindow === 1 ? 0.4 : this.rainWindow === 3 ? 1 : this.rainWindow === 6 ? 1.5 : 2.8
      this.stations = this.stations.map((s) => {
        if (s.type === 'rain') {
          const series = buildRainTimeline(s.id)
          const point = series[this.timeIndex] || series[series.length - 1]
          return {
            ...s,
            rain1h: +(point.rain * scale).toFixed(1),
            rainTotal: point.cum,
            lastTime: this.currentTimeLabel
          }
        }
        // 水文/水位/断面随回放上涨
        const base = baseHydro[s.id]
        if (base) {
          if (s.type === 'hydro' || s.type === 'section') {
            return {
              ...s,
              flow: Math.round((base.flow || 800) * (0.75 + progress * 0.55)),
              level: +((base.level || 30) + progress * 1.8).toFixed(2),
              lastTime: this.currentTimeLabel
            }
          }
          if (s.type === 'water') {
            return {
              ...s,
              level: +((base.level || 31) + progress * 1.5).toFixed(2),
              lastTime: this.currentTimeLabel
            }
          }
        }
        return { ...s, lastTime: this.currentTimeLabel }
      })
      // 摘要出口
      this.summary.currentFlow = Math.round(1230 * (0.75 + progress * 0.55))
      this.summary.currentLevel = +(31.2 + progress * 1.6).toFixed(2)
      this.summary.rain1hBasin = +(8 + progress * 8).toFixed(1)
      this.summary.rain3hBasin = +(18 + progress * 20).toFixed(1)
      this.summary.rain24hBasin = +(60 + progress * 40).toFixed(1)
    },
    setPlaybackSpeed(value) {
      const numeric = Number(value)
      this.playbackSpeed = PLAYBACK_SPEEDS.includes(numeric) ? numeric : 1
    },
    togglePlay() {
      if (requiresModelInput() && !this.hasHistoricalObservations) return
      if (this.playing) {
        this.stopPlay()
      } else {
        this.playing = true
        const tick = () => {
          if (!this.playing) return
          const interval = Math.max(100, Math.round(PLAYBACK_STEP_MS / this.playbackSpeed))
          this.setTimeIndex(this.timeIndex >= this.timeMax ? this.timeMin : this.timeIndex + 1)
          this.playTimer = setTimeout(tick, interval)
        }
        this.playTimer = setTimeout(tick, Math.max(100, Math.round(PLAYBACK_STEP_MS / this.playbackSpeed)))
      }
    },
    stopPlay() {
      this.playing = false
      if (this.playTimer) {
        clearTimeout(this.playTimer)
        this.playTimer = null
      }
      this.playLastAt = 0
    },
    flyToStation(id) {
      const s = this.stations.find((x) => x.id === id)
      if (!s) return
      this.mapCenter = [s.lng, s.lat]
      this.mapZoom = 11
      this.highlightedIssueId = id
    },
    locateIssue(issue) {
      this.flyToStation(issue.id)
      this.selectStation(issue.id, true)
    },
    toggleExcludeRainStation(id) {
      const i = this.excludedRainStations.indexOf(id)
      if (i >= 0) this.excludedRainStations.splice(i, 1)
      else this.excludedRainStations.push(id)
      // 同步质控处理结论
      const iss = this.dataIssues.find((x) => x.id === id)
      if (iss) {
        iss.handle = this.excludedRainStations.includes(id) ? '已屏蔽' : '待处理'
      }
      const qs = this.qualityStats
      this.summary.reportRate = qs.reportRate
      this.summary.lateStations = qs.late
      this.summary.missingStations = qs.missing
      this.summary.abnormal = qs.abnormal
      this.summary.normalStations = qs.normal
    },
    markIssueHandled(id, handle = '已确认可用') {
      const iss = this.dataIssues.find((x) => x.id === id)
      if (iss) iss.handle = handle
    },
    syncQualitySummary() {
      const qs = this.qualityStats
      this.summary.reportRate = qs.reportRate
      this.summary.lateStations = qs.late
      this.summary.missingStations = qs.missing
      this.summary.abnormal = qs.abnormal
      this.summary.normalStations = qs.normal
    },
    isRainExcluded(id) {
      return this.excludedRainStations.includes(id)
    },
    /** 按配置权重估算流域面平均雨量。 */
    recomputeArealFromConfig() {
      if (requiresModelInput()) return
      try {
        const cfg = useConfigStore()
        const active = cfg.stationWeights.filter((w) => w.use && w.weight != null)
        if (!active.length) return
        const sumW = active.reduce((a, w) => a + Number(w.weight), 0) || 1
        // 用站上当前 rain1h 加权（没有则用默认 10）
        let sum = 0
        active.forEach((w) => {
          const st = this.stations.find((s) => s.code === w.code || s.id.includes(w.code.slice(-4)))
          const rain = st?.rain1h ?? st?.rain3h ?? 10
          sum += Number(w.weight) * Number(rain)
        })
        const avg = sum / sumW
        this.summary.rain1hBasin = +avg.toFixed(1)
        this.summary.rain3hBasin = +(avg * 2.4).toFixed(1)
        this.summary.rain24hBasin = +(avg * 7.5).toFixed(1)
      } catch {
        /* ignore */
      }
    }
  }
})
