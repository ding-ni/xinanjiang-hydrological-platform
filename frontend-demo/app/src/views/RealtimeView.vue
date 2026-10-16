<template>
  <section class="view-realtime" :class="{ 'has-product': app.hasLakeBalance }">
    <div class="metric-strip">
      <div class="metric"><span>当前流域</span><b>{{ displaySummary.basinName }}</b></div>
      <div class="metric"><span>{{ app.hasLakeBalance ? '入湖控制断面' : '出口断面' }}</span><b>{{ displaySummary.targetSection }}</b></div>
      <div class="metric"><span>{{ usesModelInputs ? '实测流量' : '当前流量' }}</span><b>{{ metricValue(displaySummary.currentFlow, 'm³/s') }}</b></div>
      <div class="metric"><span>{{ app.hasLakeBalance ? '湖面水位' : usesModelInputs ? '实测水位' : '当前水位' }}</span><b>{{ metricValue(displaySummary.currentLevel, 'm') }}</b></div>
      <div class="metric"><span>{{ usesModelInputs ? '本小时流域面雨量（插值）' : '流域面平均雨量' }}</span><b>{{ hasNananxiObservation ? `${basinRain} mm` : (app.hasLakeBalance ? `近 ${monitor.rainWindow} 小时 ${basinRain} mm` : '—') }}</b></div>
      <div class="metric"><span>{{ usesModelInputs ? '回放时刻' : '资料时间' }}</span><b>{{ (app.hasLakeBalance || hasNananxiObservation) ? monitor.currentTimeLabel : '待接入' }}</b></div>
    </div>

    <div v-if="app.hasLakeBalance" class="product-bar">
      <div class="prod-left">
        <span class="pill">离线示意预报产品</span>
        <span>{{ product.section }}</span>
        <span class="sep">·</span>
        <b class="hot">洪峰 {{ product.peak.toLocaleString() }} m³/s</b>
        <span class="sep">·</span>
        <span>峰现 {{ product.peakTime }}</span>
        <span class="sep">·</span>
        <span class="tag ok">{{ product.status }}</span>
        <span class="muted">{{ product.runId }} · {{ product.publishedAt }}</span>
      </div>
      <div class="product-actions">
        <button class="btn sm" type="button" @click="bringToState">准备起算状态</button>
        <button class="btn primary sm" type="button" @click="bringToForecast">带入洪水预报</button>
      </div>
    </div>
    <div class="main-row" :class="{ 'detail-open': detailOpen }">
      <div class="left-stack">
        <div v-if="app.hasLakeBalance" class="panel panel-pad domain-card">
          <h3 class="panel-title"><span>流域概况</span><span class="cold-tag">真实资料</span></h3>
          <div class="domain-stats">
            <div><span>入湖流域</span><b>{{ selincoProfile.subbasinCount }} 个</b></div>
            <div><span>冰川</span><b>{{ selincoProfile.glacierCount }} 条</b></div>
            <div><span>有效计算栅格</span><b>{{ app.activeScaleMeta.cellCount.toLocaleString() }} 个</b></div>
            <div><span>高程范围</span><b>{{ app.activeScaleMeta.terrain.elevationMin }}–{{ app.activeScaleMeta.terrain.elevationMax }} m</b></div>
            <div><span>空间离散</span><b>网格步长 {{ app.activeScaleMeta.step }}°</b></div>
            <div><span>河网分级</span><b>Strahler 1–{{ selincoProfile.hydrology.maxOrder }} 级</b></div>
          </div>
          <p class="domain-note">河网依据地形汇水关系提取，并按 Strahler 河级显示。四个入湖流域分别计算，最终汇入色林错进行湖泊水量平衡。</p>
        </div>
        <div class="panel panel-pad">
          <h3 class="panel-title"><span>监视要素</span></h3>
          <div class="check-list two-col">
            <label v-for="item in layerItems" :key="item.key">
              <input
                type="checkbox"
                :checked="activeLayers[item.key]"
                @change="setActiveLayer(item.key, $event.target.checked)"
              />
              {{ item.label }}
            </label>
          </div>
          <template v-if="usesModelInputs && app.modelScale === '1km'">
            <div class="form-group-title">降雨显示</div>
            <div class="seg" role="group" aria-label="降雨显示方式">
              <button type="button" :class="{ on: monitor.rainDisplayMode !== 'smooth' }" :aria-pressed="monitor.rainDisplayMode !== 'smooth'" @click="monitor.setRainDisplayMode('grid')">网格</button>
              <button type="button" :class="{ on: monitor.rainDisplayMode === 'smooth' }" :aria-pressed="monitor.rainDisplayMode === 'smooth'" @click="monitor.setRainDisplayMode('smooth')">雨量分布</button>
            </div>
          </template>
          <div v-if="app.hasLakeBalance" class="form-group-title">累计雨量时段</div>
          <div v-if="app.hasLakeBalance" class="seg">
            <button
              v-for="h in [1, 3, 6, 24]"
              :key="h"
              type="button"
              :class="{ on: monitor.rainWindow === h }"
              @click="monitor.setRainWindow(h)"
            >
              {{ h }}h
            </button>
          </div>
        </div>

        <div v-if="!app.hasLakeBalance" class="panel panel-pad project-readiness">
          <h3 class="panel-title"><span>流域与站网</span></h3>
          <div class="domain-stats">
            <div><span>河流</span><b>{{ projectCounts.rivers }} 条</b></div>
            <div><span>站点登记</span><b>{{ projectCounts.stations }} 个</b></div>
            <div><span>出口断面</span><b>{{ projectCounts.sections }} 个</b></div>
          </div>
          <RouterLink v-if="usesModelInputs" class="btn" to="/data">查询站点雨量、水位与流量</RouterLink>
          <div v-if="usesModelInputs" class="station-picker">
            <label for="monitor-station">测站过程</label>
            <select id="monitor-station" class="input" :value="selectedProjectId || ''" @change="onSelect($event.target.value)">
              <option value="" disabled>选择测站</option>
              <option v-for="station in projectStations" :key="station.id" :value="station.id">{{ station.name }} · {{ station.typeLabel || (station.type === 'rain' ? '雨量站' : '水文站') }}</option>
            </select>
          </div>
        </div>
      </div>

      <article class="panel map-card">
        <div class="map-head">
          <span>{{ app.hasLakeBalance || hasNananxiObservation ? '流域雨水情' : '流域空间资料' }}</span>
          <div v-if="!app.hasLakeBalance" class="product-actions">
            <RouterLink class="btn sm" to="/simulation">洪水模拟</RouterLink>
            <button class="btn primary sm" type="button" @click="bringToForecast">洪水预报</button>
          </div>
        </div>
        <div class="map-body">
          <BasinMap
            :stations="activeStations"
            :layers="activeLayers"
            :selected-id="app.hasLakeBalance ? selectedSelincoId : selectedProjectId"
            :center="activeCenter"
            :zoom="activeZoom"
            :playing="monitor.playing"
            :qpf-grid-cells="displayRainCells"
            smooth-rain-source="model-grid"
            legend-mode="rain"
            @select-station="onSelect"
          />
        </div>
        <div v-if="app.hasLakeBalance" class="forcing-strip">
          <div><span>当前降水文件</span><b>{{ activePrecip?.source || app.activeModelScheme.forcing?.name }}</b></div>
          <div><span>图面时次</span><b>{{ monitor.currentTimeLabel }} · 第 {{ monitor.timeIndex + 1 }}/121 时次</b></div>
          <div><span>计算方案</span><b>{{ app.activeModelScheme.label }} · {{ currentPrecipCells.length.toLocaleString() }} 个展示网格</b></div>
          <span class="forcing-status" :class="{ loading: precipLoading }">{{ precipLoading ? '正在读取逐小时降水场…' : precipError || '离线逐小时降水场预览已载入' }}</span>
        </div>
        <div v-else-if="hasNananxiObservation || usesModelInputs" class="forcing-strip project-forcing">
          <span v-if="rainGridLoading || monitor.historicalLoading || rainGridError || monitor.historicalError" class="forcing-status" :class="{ loading: rainGridLoading || monitor.historicalLoading, error: Boolean(rainGridError || monitor.historicalError) }" role="status">{{ rainGridLoading || monitor.historicalLoading ? '正在读取降雨' : rainGridError || monitor.historicalError }}</span>
          <details><summary>降雨资料</summary><p>测站雨量 · 距离反比加权</p></details>
          <div><span>时刻</span><b>{{ usesModelInputs && !hasNananxiObservation ? '—' : monitor.currentTimeLabel }}</b></div>
          <div><span>降雨网格</span><b>{{ currentPrecipCells.length }} 个</b></div>
        </div>
      </article>

      <SelincoStationDetail v-if="app.hasLakeBalance && selectedSelincoStation" class="right-detail" :station="selectedSelincoStation" @close="selectedSelincoId = null" @forecast="enterForecast" />
      <StationDetail
        v-else-if="!app.hasLakeBalance && selectedProjectStation"
        class="right-detail"
        :station="selectedProjectStation"
        @close="selectedProjectId = null"
      />
    </div>

    <TimelineBar v-if="app.hasLakeBalance || hasNananxiObservation || usesModelInputs" />
    <div v-else class="timeline-pending">当前项目没有可回放的逐小时资料</div>
    <ObservationProcess v-if="usesModelInputs && monitor.historicalPreview" :preview="monitor.historicalPreview" :summary="monitor.modelInputSummary" :stations="projectStations" :time-index="monitor.timeIndex" @select-time="monitor.setTimeIndex($event)" />
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMonitorStore } from '@/stores/monitor'
import { useAppStore } from '@/stores/app'
import { useForecastStore } from '@/stores/forecast'
import { loadSelincoPrecipitationPreview, selincoProfile, selincoStations, selincoSummary } from '@/data/selinco'
import BasinMap from '@/components/map/BasinMap.vue'
import StationDetail from '@/components/monitor/StationDetail.vue'
import SelincoStationDetail from '@/components/monitor/SelincoStationDetail.vue'
import TimelineBar from '@/components/monitor/TimelineBar.vue'
import ObservationProcess from '@/components/monitor/ObservationProcess.vue'
import { windowStation } from '@/services/observationProcessService'
import { gridCellsAt, gridCellsAtTime, loadNananxiRainfallGrid } from '@/services/rainfallGridService'
import { projectDataForContext } from '@/services/caseAvailabilityService'
import { usesModelInputService, finiteInputValue } from '@/services/modelInputService'

const monitor = useMonitorStore()
const app = useAppStore()
const forecast = useForecastStore()
const router = useRouter()
const selectedSelincoId = ref(null)
const selectedProjectId = ref(null)
const precipitationPreview = ref(null)
const nananxiRainGridProduct = shallowRef(null)
const rainGridLoading = ref(false)
const rainGridError = ref('')
const precipLoading = ref(false)
const precipError = ref('')
const precipCellCache = new Map()
let observationRequestId = 0

const product = computed(() => app.latestForecastProduct)
const caseData = computed(() => projectDataForContext(app.activeContext, app.projectData, { loading: app.projectDataLoading, error: app.projectDataError }))
const usesModelInputs = computed(() => usesModelInputService(app.activeContext))
const hasNananxiObservation = computed(() => Boolean(caseData.value) && app.hasObservationCatalog && monitor.hasHistoricalObservations
  && (!usesModelInputs.value || (app.modelScale === '1km' && !app.backendError && ['model-qc','database-observed'].includes(monitor.historicalPreview?.sourceType))))
const projectStations = computed(() => {
  const rows = [
    ...(caseData.value?.stations?.stations || []),
    ...(caseData.value?.sections?.sections || [])
  ]
  return [...new Map(rows.map((row) => [row.id, row])).values()].map((row) => ({
    ...row,
    ...(usesModelInputs.value ? windowStation(monitor.historicalPreview, String(row.code || row.id), monitor.timeIndex) || {
      observationReady: false, hasRainObservation: false, hasRiverObservation: false,
      rain1h: null, rainTotal: null, level: null, flow: null, sourceType: 'database-observed',
      quality: monitor.historicalPreview ? '此时段无记录' : '正在读取',
      sourceLabel: '水文资料库 · 原始实测记录', rainSeries: [], flowSeries: [], levelSeries: []
    } : hasNananxiObservation.value ? monitor.historicalStationSnapshot(row.code || row.id) : null)
  }))
})
const displaySummary = computed(() => {
  if (app.hasLakeBalance) return selincoSummary
  const outlet = usesModelInputs.value ? windowStation(monitor.historicalPreview, '70115800', monitor.timeIndex) : hasNananxiObservation.value ? monitor.historicalStationSnapshot('70115800') : null
  return {
    basinName: app.basinName,
    targetSection: projectStations.value.find((station) => station.type === 'section')?.name || '待登记',
    currentFlow: outlet?.flow ?? null,
    currentLevel: outlet?.level ?? null,
    dataStatus: app.dataStatus
  }
})
const projectCounts = computed(() => {
  const metadata = caseData.value?.spatialMetadata || {}
  const counts = metadata.featureCounts || {}
  return {
    rivers: counts.rivers ?? 0,
    stations: projectStations.value.length,
    sections: counts.sections ?? 0,
    cells: metadata.terrain?.[app.modelScale]?.cellCount ?? '待生成'
  }
})
const activeStations = computed(() => caseData.value ? app.hasLakeBalance ? selincoStations : projectStations.value : [])
const selectedProjectStation = computed(() => projectStations.value.find((station) => station.id === selectedProjectId.value) || null)
const activeCenter = computed(() => caseData.value ? app.hasLakeBalance ? selincoProfile.center : caseData.value.spatialMetadata?.center || [0, 0] : [0, 0])
const activeZoom = computed(() => app.hasLakeBalance ? selincoProfile.zoom : (app.basinId === 'llpq' ? 8 : 10))
const selincoLayers = reactive({ rain: true, hydro: false, water: true, section: true, river: true, subbasin: true, rainField: false, smoothRain: false, qpfGrid: true, lake: true, glacier: true, realGrid: false, dem: true })
const activeLayers = computed(() => app.hasLakeBalance ? selincoLayers : usesModelInputs.value ? {
  ...monitor.layers,
  rainDisplay: monitor.layers.qpfGrid,
  qpfGrid: monitor.layers.qpfGrid && monitor.rainDisplayMode !== 'smooth',
  smoothRain: monitor.layers.qpfGrid && monitor.rainDisplayMode === 'smooth' && app.modelScale === '1km',
  rainField: false
} : monitor.layers)
const selectedSelincoStation = computed(() => selincoStations.find((station) => station.id === selectedSelincoId.value) || null)
const detailOpen = computed(() => app.hasLakeBalance
  ? Boolean(selectedSelincoStation.value)
  : Boolean(selectedProjectStation.value))

const activePrecip = computed(() => precipitationPreview.value?.[app.modelScale] || null)
const currentPrecipCells = computed(() => {
  if (!caseData.value) return []
  if (hasNananxiObservation.value && nananxiRainGridProduct.value && monitor.historicalPreview) {
    const product = nananxiRainGridProduct.value
    const index = Math.min(product.times.length - 1, Math.max(0, monitor.timeIndex))
    const key = `${product.source || product.sourceType || 'grid'}:${product.resolution}:${product.times[0] || ''}:${product.times[product.times.length - 1] || ''}:${index}`
    const cached = precipCellCache.get(key)
    if (cached) return cached
    const cells = ['model-qc','database-derived-grid'].includes(product.sourceType) ? gridCellsAtTime(product, monitor.currentTimeLabel) : gridCellsAt(product, index)
    precipCellCache.set(key, cells)
    while (precipCellCache.size > 24) precipCellCache.delete(precipCellCache.keys().next().value)
    return cells
  }
  if (!app.hasLakeBalance) return []
  const product = activePrecip.value
  if (!product?.cells) return []
  const index = Math.min(product.times.length - 1, monitor.timeIndex)
  return product.cells.map((cell) => ({ id: cell.id, bounds: cell.bounds, qpf: cell.values[index] == null ? null : Number(cell.values[index]) }))
})
const displayRainCells = computed(() => currentPrecipCells.value)
const basinRain = computed(() => {
  if (usesModelInputs.value) {
    const value = finiteInputValue(monitor.modelInputSummary?.arealRainfallMm?.[monitor.timeIndex])
    return value == null ? '—' : value.toFixed(1)
  }
  if (hasNananxiObservation.value) {
    const values = projectStations.value.map((station) => station.rain1h).filter((value) => value != null)
    return values.length ? (values.reduce((sum, value) => sum + Number(value), 0) / values.length).toFixed(1) : '—'
  }
  if (!app.hasLakeBalance || !currentPrecipCells.value.length) return '—'
  const values = currentPrecipCells.value.map((cell) => cell.qpf).filter((value) => Number.isFinite(value))
  if (!values.length) return '—'
  return (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1)
})

function metricValue(value, unit) {
  if (value == null || value === '') return '—'
  return `${Number(value).toLocaleString()} ${unit}`
}

const xajLayerItems = [
  { key: 'rain', label: '雨量站' },
  { key: 'hydro', label: '水文站' },
  { key: 'water', label: '水位站' },
  { key: 'section', label: '控制断面' },
  { key: 'river', label: '河网' },
  { key: 'subbasin', label: '流域边界' },
  { key: 'dem', label: '网格高程' },
  { key: 'grid', label: '产流网格线' },
  { key: 'qpfGrid', label: '1公里实测降雨栅格' },
  { key: 'smoothRain', label: '平滑雨量场（站点插值）' }
]
const selincoLayerItems = [
  { key: 'rain', label: '降水站' },
  { key: 'water', label: '湖位站' },
  { key: 'section', label: '入湖断面' },
  { key: 'river', label: '河网' },
  { key: 'subbasin', label: '入湖流域' },
  { key: 'lake', label: '湖泊' },
  { key: 'glacier', label: '冰川' },
  { key: 'qpfGrid', label: '逐小时降水场' },
  { key: 'realGrid', label: '计算格网' },
  { key: 'dem', label: 'DEM' }
]
const layerItems = computed(() => app.hasLakeBalance ? selincoLayerItems : usesModelInputs.value
  ? xajLayerItems.filter((item) => item.key !== 'smoothRain').map((item) => item.key === 'qpfGrid' ? { key: 'rainDisplay', label: app.modelScale === '1km' ? '降雨场' : '降雨场（该尺度未接入）' } : item.key === 'dem' ? { ...item, label: `${app.activeScaleMeta.label} 网格高程` } : item)
  : xajLayerItems)

async function loadPrecipitation() {
  if (!app.hasLakeBalance || precipitationPreview.value) return
  precipLoading.value = true
  precipError.value = ''
  try {
    precipitationPreview.value = await loadSelincoPrecipitationPreview()
  } catch (error) {
    precipError.value = '降水预览读取失败'
  } finally {
    precipLoading.value = false
  }
}

let rainGridRequestId = 0
let rainGridRequestKey = ''
let rainGridLoadedKey = ''
let rainGridRevision = -1

async function loadRainfallGridWindow(requestId = observationRequestId) {
  const preview = monitor.historicalPreview
  if (!preview || requestId !== observationRequestId) return
  if (usesModelInputs.value) {
    if (app.modelScale !== '1km' || !['model-qc','database-observed'].includes(preview.sourceType) || !monitor.modelInputProduct) {
      nananxiRainGridProduct.value = null
      rainGridError.value = '当前尺度没有降雨资料'
      return
    }
    nananxiRainGridProduct.value = monitor.modelInputProduct
    rainGridError.value = monitor.modelInputEventQuality?.gridError || ''
    rainGridLoading.value = false
    precipCellCache.clear()
    return
  }
  const revision = Number(monitor.historicalDataRevision || 0)
  const key = `${app.modelScale}:${preview.start}:${preview.end}:r${revision}`
  if (rainGridLoadedKey === key && nananxiRainGridProduct.value) return
  if (rainGridLoading.value && rainGridRequestKey === key) return
  rainGridRequestKey = key
  const gridRequest = ++rainGridRequestId
  rainGridLoading.value = true
  rainGridError.value = ''
  try {
    const product = await loadNananxiRainfallGrid({
      resolution: app.modelScale,
      start: preview.start,
      end: preview.end,
      refresh: rainGridRevision >= 0 && rainGridRevision !== revision,
      catalog: monitor.historicalCatalog,
      source: app.activeContext
    })
    if (requestId !== observationRequestId || gridRequest !== rainGridRequestId) return
    nananxiRainGridProduct.value = product
    rainGridLoadedKey = key
    rainGridRevision = revision
  } catch (error) {
    if (requestId === observationRequestId && gridRequest === rainGridRequestId) {
      nananxiRainGridProduct.value = null
      rainGridError.value = error instanceof Error ? error.message : String(error)
    }
  } finally {
    if (gridRequest === rainGridRequestId) rainGridLoading.value = false
  }
}

async function loadDomainObservations({ preserveDataTime = false } = {}) {
  const requestId = ++observationRequestId
  monitor.setTimelineContext(app.basinId)
  if (app.hasObservationCatalog) {
    nananxiRainGridProduct.value = null
    precipCellCache.clear()
    rainGridLoadedKey = ''
    rainGridRevision = -1
    try {
      await monitor.loadHistoricalPreview({ syncDataTime: !preserveDataTime })
    } catch (error) {
      if (requestId !== observationRequestId) return
      rainGridError.value = error instanceof Error ? error.message : String(error)
      nananxiRainGridProduct.value = null
      return
    }
    if (requestId !== observationRequestId) return
    await loadRainfallGridWindow(requestId)
    if (requestId !== observationRequestId) return
    if (monitor.hasHistoricalObservations) {
      monitor.applyHistoricalTimeline()
      if (!preserveDataTime) app.setDataTime(monitor.currentTimeLabel)
    }
  } else {
    nananxiRainGridProduct.value = null
    rainGridLoading.value = false
    rainGridError.value = ''
  }
}

onMounted(() => {
  loadPrecipitation()
  loadDomainObservations()
})
watch(() => app.basinId, () => {
  selectedProjectId.value = null
  loadPrecipitation()
  loadDomainObservations()
})
watch(() => [app.modelScale, app.projectData?.spatial?.terrain?.gridUrl], () => {
  if (!app.hasObservationCatalog) return
  loadDomainObservations()
})
watch(() => app.inputSelectionKey, () => {
  if (!usesModelInputs.value) return
  observationRequestId += 1
  rainGridRequestId += 1
  nananxiRainGridProduct.value = null
  rainGridLoadedKey = ''
  precipCellCache.clear()
  rainGridLoading.value = false
  rainGridError.value = ''
  loadDomainObservations({ preserveDataTime: true })
})
watch(() => [monitor.historicalPreview?.start, monitor.historicalPreview?.end, monitor.historicalDataRevision], () => {
  if (!app.hasObservationCatalog || !monitor.historicalPreview) return
  loadRainfallGridWindow()
})
watch(() => app.backendError, (message, previous) => {
  if (!usesModelInputs.value) return
  if (message) {
    observationRequestId += 1
    monitor.invalidateBackendInput(message)
    nananxiRainGridProduct.value = null
    precipCellCache.clear()
    rainGridLoading.value = false
    rainGridError.value = message
  } else if (previous) {
    loadDomainObservations()
  }
})

function setActiveLayer(key, value) {
  if (app.hasLakeBalance) selincoLayers[key] = value
  else monitor.setLayer(usesModelInputs.value && key === 'rainDisplay' ? 'qpfGrid' : key, value)
}

function onSelect(id) {
  if (app.hasLakeBalance) {
    return
  }
  if (projectStations.value.some((station) => station.id === id)) {
    selectedProjectId.value = id
    return
  }
}

function enterForecast(station) {
  forecast.setDomain(app.basinId)
  forecast.prepareFromStation(station, monitor.currentTimeLabel)
  prepareMonitorHandoff('forecast', station?.id)
  router.push('/forecast')
}

function monitorBundleId() {
  if (usesModelInputs.value) return monitor.modelInputProvenance?.inputBundleVersionId || app.selectedInputBundleVersionId
  return caseData.value?.datasets?.datasets?.[0]?.id || null
}

function defaultSection() {
  return selectedProjectStation.value || projectStations.value.find((item) => item.type === 'section') || null
}

function prepareMonitorHandoff(targetPage, sectionId = null) {
  const bundleId = monitorBundleId()
  app.prepareHandoff(targetPage, {
    sourcePage: 'realtime',
    sourceLabel: '雨水情监视',
    issueTime: app.hasLakeBalance ? monitor.currentTimeLabel : app.dataTime,
    dataSourceId: bundleId,
    inputBundleVersionId: bundleId,
    sectionId: sectionId || defaultSection()?.id || app.workContext.sectionId,
    sourceTaskId: null
  })
}

function bringToState() {
  prepareMonitorHandoff('state')
  router.push('/state')
}

function bringToForecast() {
  const section = defaultSection()
  if (section) forecast.prepareFromStation(section, app.hasLakeBalance ? monitor.currentTimeLabel : app.dataTime)
  forecast.syncFromMonitor(typeof monitor.qualityPayloadFor === 'function' ? monitor.qualityPayloadFor(app.basinId) : monitor.qualityPayload)
  prepareMonitorHandoff('forecast', section?.id)
  router.push('/forecast')
}
</script>

<style scoped>
.station-picker { display: grid; gap: 6px; margin-top: 14px; font-size: 12px; color: #65777e; }
.station-picker .input { width: 100%; min-width: 0; }
.view-realtime {
  display: grid;
  grid-template-rows: auto minmax(460px, 1fr) auto auto;
  gap: 10px;
  width: 100%;
  height: auto;
  min-height:100%;
  min-height: 0;
}
.view-realtime.has-product { grid-template-rows: 46px auto minmax(0, 1fr) auto; }

.product-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 14px;
  border: 1px solid #d4e0ef;
  border-radius: var(--radius);
  background: linear-gradient(90deg, #f3f8ff, #fff);
  box-shadow: var(--shadow-soft);
}

.product-actions { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }

.prod-left {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: #33455f;
  font-size: 13px;
}

.pill {
  padding: 2px 8px;
  border-radius: 999px;
  background: #e8f1ff;
  color: var(--blue);
  font-size: 11px;
  font-weight: 800;
}

.sep {
  color: #b0bfd2;
}

.muted {
  color: #7a8ba3;
  font-size: 12px;
}

.tag.ok {
  background: #e6f7ed;
  color: #14833e;
}

.btn.sm {
  min-height: 32px;
  padding: 0 12px;
  font-size: 12px;
}

.main-row {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 10px;
  min-height: 0;
}

.main-row.detail-open {
  grid-template-columns: 240px minmax(0, 1fr) clamp(340px, 24vw, 400px);
}

.left-stack {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 10px;
  min-height: 0;
  overflow: auto;
}
.domain-card { align-self: start; }
.cold-tag { color: var(--teal); font-size: 10px; font-weight: 800; }
.domain-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; border: 1px solid var(--line); background: var(--line); }
.domain-stats div { display: grid; gap: 3px; padding: 9px; background: #fff; }
.domain-stats span { color: var(--muted); font-size: 10px; }
.domain-stats b { overflow-wrap: anywhere; color: var(--ink); font-size: 12px; font-variant-numeric: tabular-nums; }
.domain-note { margin: 10px 0 0; color: var(--muted); font-size: 11px; line-height: 1.5; }

.seg {
  display: flex;
  gap: 6px;
}

.seg button {
  flex: 1;
  height: 28px;
  border: 1px solid #c8d8ec;
  border-radius: 6px;
  background: #fff;
  color: #3a4d68;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.seg button.on {
  border-color: var(--blue);
  background: var(--blue-soft);
  color: var(--blue);
}

.issues {
  min-height: 0;
  overflow: auto;
}

.project-readiness {
  min-height: 0;
  overflow: auto;
}

.timeline-pending {
  display: grid;
  place-items: center;
  border: 1px dashed #c9d6df;
  border-radius: var(--radius);
  background: #f8fafb;
  color: #647789;
  font-size: 12px;
}

.stat-box.compact {
  margin-bottom: 10px;
}

.issue-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.issue-list li {
  padding: 8px 10px;
  border: 1px solid #e0eaf6;
  border-left: 3px solid #c8d8ec;
  border-radius: 8px;
  background: #fbfdff;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.issue-list li:hover {
  background: #f3f8ff;
  border-color: #b8cce6;
}
.issue-list li:focus-visible {
  outline: 3px solid rgba(11, 98, 140, 0.2);
  outline-offset: 2px;
}

.issue-list li.warn {
  border-left-color: var(--orange);
}

.issue-list li.bad {
  border-left-color: var(--red);
}

.iss-top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  color: #173052;
  font-size: 12px;
}

.iss-top span {
  color: #6a7c95;
  font-weight: 700;
}

.issue-list p {
  margin: 0;
  color: #5a6d88;
  font-size: 11px;
  line-height: 1.4;
}

.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.mini-table th,
.mini-table td {
  padding: 5px 4px;
  border-bottom: 1px solid #e8f0fa;
  text-align: left;
  color: #3a4d68;
}

.mini-table th {
  color: #6a7c95;
  font-weight: 700;
}

.mini-table b {
  color: #0b57c7;
}

.mini-table .bad {
  color: var(--red);
  font-weight: 700;
}

.time-hint {
  margin: 10px 0 0;
  color: #5a6d88;
  font-size: 11px;
  line-height: 1.4;
}

.map-card {
  display: grid;
  grid-template-rows: 42px minmax(0, 1fr) auto;
  min-height: 0;
}

.map-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 14px;
  border-bottom: 1px solid var(--line);
  color: #173052;
  font-weight: 800;
  background: linear-gradient(180deg, #fff, #f8fbff);
}

.map-body {
  min-height: 0;
}

.forcing-strip {
  display: grid;
  grid-template-columns: minmax(210px, 1.25fr) minmax(190px, 1fr) minmax(180px, 0.9fr) auto;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  border-top: 1px solid var(--line);
  background: #f8fbff;
}
.forcing-strip > div { display: grid; gap: 2px; min-width: 0; }
.forcing-strip span { color: var(--muted); font-size: 10px; }
.forcing-strip b { overflow-wrap: anywhere; color: var(--ink); font-size: 11px; font-variant-numeric: tabular-nums; }
.forcing-status { color: var(--green) !important; font-weight: 800; white-space: nowrap; }
.forcing-status.loading { color: var(--blue) !important; }
.forcing-status.error { color: var(--red) !important; white-space: normal; }
.project-forcing { grid-template-columns: minmax(0, 2fr) minmax(160px, 1fr) 100px; }
.project-forcing .forcing-status { grid-column: 1 / -1; }

@media (max-width: 1200px) {
  .view-realtime,
  .view-realtime.has-product {
    height: auto;
    min-height: 100%;
    grid-template-rows: auto auto auto auto;
  }
  .main-row,
  .main-row.detail-open {
    grid-template-columns: 260px minmax(0, 1fr);
  }
  .right-detail {
    grid-column: 1 / -1;
  }
  .left-stack { overflow: visible; }
  .map-card { min-height: 560px; }
}

@media (max-width: 760px) {
  .product-bar { align-items: stretch; flex-direction: column; }
  .main-row,
  .main-row.detail-open { grid-template-columns: 1fr; }
  .right-detail { grid-column: 1; }
  .map-card { min-height: 480px; }
  .forcing-strip { grid-template-columns: 1fr; }
}
</style>
