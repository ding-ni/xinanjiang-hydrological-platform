<template>
  <div class="basin-map" ref="rootEl">
    <div class="map-canvas" ref="mapEl" />
    <div v-if="!hasSpatialResources" class="spatial-pending">{{ spatialPendingText }}</div>
    <div v-else-if="legendMode === 'rain' && (props.layers.qpfGrid || smoothRainEnabled) && !hasGridRainfall(props.qpfGridCells) && !app.isSelinco" class="rain-empty" role="status">此时刻网格雨量缺测；实测过程可在下方查看</div>
    <div v-if="terrainBadge" class="terrain-badge">
      <b>{{ terrainBadge.label }}</b>
      <span v-if="terrainBadge.cellCount">{{ terrainBadge.cellCount.toLocaleString() }} 有效网格</span>
      <span>{{ terrainBadge.elevationMin }}–{{ terrainBadge.elevationMax }} m</span>
    </div>
    <div class="map-tools" v-if="showTools">
      <button type="button" @click="zoomBy(0.6)">放大</button>
      <button type="button" @click="zoomBy(-0.6)">缩小</button>
      <button type="button" @click="resetView">复位</button>
    </div>
    <div class="map-legend" v-if="legendMode === 'rain'">
      <p class="legend-title">{{ app.isSelinco ? '降水强度 (mm/h)' : '空间图层' }}</p>
      <div v-if="app.isSelinco" class="legend-row" v-for="item in rainLegend" :key="item.label">
        <span class="swatch" :style="{ background: item.color }" />
        <span>{{ item.label }}</span>
      </div>
      <div v-if="!app.isSelinco" class="legend-symbols">
        <div><i class="sym rain" />雨量站</div>
        <div><i class="sym hydro" />水文站</div>
        <div><i class="sym water" />水位站</div>
        <div><i class="sym section" />控制断面</div>
      </div>
      <div v-if="!app.isSelinco && smoothRainEnabled" class="smooth-rain-legend">
        <span class="smooth-rain-gradient" /><div><span>0</span><span>10</span><span>25</span><span>50+</span></div>
        <small>雨量分布（mm/h）</small>
      </div>
      <div v-if="!app.isSelinco && props.layers.qpfGrid" class="smooth-rain-legend">
        <span class="smooth-rain-gradient" /><div><span>0</span><span>10</span><span>25</span><span>50+</span></div>
        <small>雨量分布（mm/h）</small>
      </div>
      <div v-if="app.isSelinco" class="legend-symbols">
        <div><i class="sym rain" />降水站</div>
        <div><i class="sym water" />湖位站</div>
        <div><i class="sym section" />入湖断面</div>
        <div><i class="sym river" />Strahler 分级河网</div>
        <div><i class="sym lake" />湖泊</div>
        <div><i class="sym glacier" />冰川</div>
      </div>
    </div>
    <div class="map-legend" v-else-if="stateLegend">
      <p class="legend-title">{{ stateLegend.title }}</p>
      <small class="legend-scale-note">连续插值色标 · 数值刻度</small>
      <div class="legend-row" v-for="item in stateLegend.items" :key="item.label">
        <span class="swatch" :style="{ background: item.color }" />
        <span>{{ item.label }}</span>
      </div>
      <div class="grid-legend">
        <div v-if="app.isSelinco"><i class="grid-symbol model" />当前计算网格 · {{ app.activeModelScheme.label }}</div>
        <template v-else>
          <div><i class="grid-symbol model" />{{ app.activeScaleMeta.label }} 模型网格</div>
          <div v-if="props.layers.qpfGrid && props.qpfGridCells?.length"><i class="grid-symbol qpf" />{{ forbidsStationInterpolation ? '整编降雨网格' : '降雨输入网格' }}</div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch, shallowRef } from 'vue'
import maplibregl from 'maplibre-gl'
import { installLocalMapGlyphs } from '@/services/localMapGlyphs'
import { useAppStore } from '@/stores/app'
import { selincoProfile } from '@/data/selinco'
import { projectDataForContext } from '@/services/caseAvailabilityService'
import {
  stationsToGeoJSON,
  gridToGeoJSON
} from '@/data/basin'
import { buildSmoothRainfallDataURL, geoJSONBounds } from '@/services/rainfallInterpolationService'
import { buildSmoothGridRainfallDataURL, gridRainfallDisplaySignature, hasGridRainfall, gridRainfallColorExpression } from '@/services/gridRainfallDisplayService'
import { modelBackendEnabled } from '@/services/modelBackendService'
import { actualSubbasinGeometry, applyBasinBoundaryVisibility, SUBBASIN_FILL_COLOR } from '@/services/basinBoundaryDisplayService'

const props = defineProps({
  stations: { type: Array, default: () => [] },
  layers: {
    type: Object,
    default: () => ({
      rain: true,
      hydro: true,
      water: true,
      section: true,
      river: true,
      subbasin: true,
      rainField: true,
      smoothRain: false,
      dem: true,
      grid: false
    })
  },
  selectedId: { type: String, default: null },
  showTools: { type: Boolean, default: true },
  legendMode: { type: String, default: 'rain' }, // rain | runoff | soil | snow | frost | melt | none
  interactiveGrid: { type: Boolean, default: false },
  /** 外部栅格单元（参数/状态联动时传入） */
  gridCells: { type: Array, default: null },
  /** 当前时次的降雨网格；仅用于地图显示。 */
  qpfGridCells: { type: Array, default: null },
  smoothRainSource: { type: String, default: 'station-idw' },
  /** 栅格填色字段：runoff | soil | wu | wl | wd | freeWater | surface | snowWater | snowDepth | snowLiquid | frozenIndex | glacierMelt */
  gridPaintField: { type: String, default: 'runoff' },
  center: { type: Array, default: () => [0, 0] },
  zoom: { type: Number, default: 5 },
  /** 过程回放时降低展示层插值频率，暂停后立即刷新当前时刻。 */
  playing: { type: Boolean, default: false }
})

const emit = defineEmits([
  'select-station',
  'hover-station',
  'select-grid',
  'toggle-layers',
  'map-ready'
])

const app = useAppStore()
const forbidsStationInterpolation = computed(() => modelBackendEnabled() && app.basinId !== 'selinco')
const usesGridRainSmoothing = computed(() => props.smoothRainSource === 'model-grid' && app.basinId === 'nananxi-xufan' && app.modelScale === '1km')
const smoothRainEnabled = computed(() => Boolean(props.layers.smoothRain))
const EMPTY_GEOJSON = { type: 'FeatureCollection', features: [] }
// Keep the placeholder image valid because MapLibre decodes every image source,
// even when the corresponding layer is hidden.
const TRANSPARENT_PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQYV2NgAAIAAAUAAarVyFEAAAAASUVORK5CYII='
const externalProfile = shallowRef(null)
const caseData = computed(() => projectDataForContext(app.activeContext, app.projectData, { loading: app.projectDataLoading, error: app.projectDataError }))
const hasSpatialResources = computed(() => {
  const basin = caseData.value?.spatialAssets?.basin
  return Boolean(basin?.features?.some((feature) => feature.geometry) || (basin?.type === 'Feature' && basin.geometry))
})
const spatialPendingText = computed(() => app.projectDataLoading ? '正在读取当前案例空间资料…' : app.projectDataError ? '空间资料加载失败' : '当前案例空间资料未接入')

const mapEl = ref(null)
const rootEl = ref(null)
const mapRef = shallowRef(null)
let popup = null
let resizeObs = null
let resizeTimer = null
let smoothRainFrame = null
let smoothRainFrameKind = 'timeout'
let smoothRainPending = null
let smoothRainThrottleTimer = null
let smoothRainLastRenderAt = 0
let smoothRainContextKey = ''
let smoothImageUpdateTimer = null
let smoothImageUpdatePending = null
let smoothRainHasImage = false
let qpfGridFrame = null
let qpfGridPending = null
let qpfGridSignature = ''
const smoothRainCache = new Map()
const terrainImageSignatures = new Map()
let mapDestroyed = false

function scheduleSmoothRain(callback) {
  if (typeof requestIdleCallback === 'function') {
    smoothRainFrameKind = 'idle'
    return requestIdleCallback(callback, { timeout: 220 })
  }
  smoothRainFrameKind = 'timeout'
  return setTimeout(callback, 32)
}

function cancelSmoothRain() {
  if (smoothRainFrame == null) return
  if (smoothRainFrameKind === 'idle' && typeof cancelIdleCallback === 'function') cancelIdleCallback(smoothRainFrame)
  else clearTimeout(smoothRainFrame)
  smoothRainFrame = null
}

function scheduleSmoothRainThrottle(map) {
  if (smoothRainThrottleTimer != null) return
  const elapsed = Date.now() - smoothRainLastRenderAt
  const delay = Math.max(0, (usesGridRainSmoothing.value ? 160 : 760) - elapsed)
  smoothRainThrottleTimer = setTimeout(() => {
    smoothRainThrottleTimer = null
    updateSmoothRainfall(map, { force: true })
  }, delay)
}

const rainLegend = [
  { label: '0 - 1', color: '#dceeff' },
  { label: '1 - 5', color: '#9ed0ff' },
  { label: '5 - 10', color: '#5aaef0' },
  { label: '10 - 25', color: '#f0d35a' },
  { label: '25 - 50', color: '#f2a03a' },
  { label: '> 50', color: '#e24b3b' }
]

// Shared numeric stops drive both continuous map paint and legend ticks.
const physicalStateColors = [
  'rgba(174,217,255,0.12)', 'rgba(120,200,160,0.28)',
  'rgba(255,216,106,0.38)', 'rgba(242,160,58,0.50)', 'rgba(226,75,59,0.60)'
]
const physicalStateFields = {
  RS: { title: '地表径流 RS (mm)', stops: [0, 1, 2, 5, 10] },
  RI: { title: '壤中径流 RI (mm)', stops: [0, 0.2, 0.5, 1, 2] },
  RG: { title: '地下径流 RG (mm)', stops: [0, 0.1, 0.2, 0.5, 1] },
  wu: { title: '上层张力水 WU (mm)', stops: [0, 5, 10, 20, 40] },
  wl: { title: '下层张力水 WL (mm)', stops: [0, 20, 40, 80, 150] },
  wd: { title: '深层张力水 WD (mm)', stops: [0, 30, 60, 100, 200] },
  freeWater: { title: '自由水 S (mm)', stops: [5, 15, 30, 45, 60] },
  soil: { title: '张力水相对蓄量 W/WM', stops: [0.2, 0.4, 0.6, 0.8, 0.95], colors: [
    'rgba(174,217,255,0.15)', 'rgba(120,200,160,0.30)', 'rgba(255,216,106,0.40)',
    'rgba(242,160,58,0.52)', 'rgba(226,75,59,0.62)'
  ] },
  runoff: { title: '时段产流深 R (mm)', stops: [0, 1, 2, 4, 7], colors: [
    'rgba(174,217,255,0.08)', 'rgba(120,200,160,0.28)', 'rgba(255,216,106,0.38)',
    'rgba(242,160,58,0.48)', 'rgba(226,75,59,0.58)'
  ] },
  surface: { title: '地表指标 surface', stops: [0, 20, 40, 70, 100], colors: [
    'rgba(174,217,255,0.08)', 'rgba(120,200,160,0.28)', 'rgba(255,216,106,0.38)',
    'rgba(242,160,58,0.48)', 'rgba(226,75,59,0.58)'
  ] },
  snowWater: { title: '积雪水当量 SWE (mm)', stops: [0, 10, 25, 50, 90], colors: [
    'rgba(232,245,250,0.12)', 'rgba(198,224,236,0.32)', 'rgba(135,190,218,0.46)',
    'rgba(70,145,190,0.60)', 'rgba(0,85,145,0.72)'
  ] },
  snowDepth: { title: '积雪深度 (cm)', stops: [0, 8, 20, 40, 70], colors: [
    'rgba(232,245,250,0.12)', 'rgba(198,224,236,0.32)', 'rgba(135,190,218,0.46)',
    'rgba(70,145,190,0.60)', 'rgba(0,85,145,0.72)'
  ] },
  snowLiquid: { title: '雪层液态水 (mm)', stops: [0, 0.8, 2, 5, 9], colors: [
    'rgba(232,245,250,0.12)', 'rgba(198,224,236,0.32)', 'rgba(135,190,218,0.46)',
    'rgba(70,145,190,0.60)', 'rgba(0,85,145,0.72)'
  ] },
  frozenIndex: { title: '冻土冻结程度 (%)', stops: [0, 25, 50, 75, 100], colors: [
    'rgba(240,244,246,0.12)', 'rgba(178,205,219,0.34)', 'rgba(103,155,183,0.50)',
    'rgba(53,116,150,0.62)', 'rgba(20,70,100,0.74)'
  ] },
  glacierMelt: { title: '冰川消融量 (mm/h)', stops: [0, 0.1, 0.3, 0.6, 1.2], colors: [
    'rgba(231,248,246,0.10)', 'rgba(178,226,216,0.32)', 'rgba(102,194,178,0.48)',
    'rgba(44,162,145,0.62)', 'rgba(0,109,119,0.74)'
  ] }
}

const stateLegend = computed(() => {
  if (['rain', 'none'].includes(props.legendMode)) return null
  const physical = physicalStateFields[props.gridPaintField] || physicalStateFields.runoff
  return {
    title: physical.title,
    items: physical.stops.map((value, index) => ({
      label: `${index === 0 ? '≤ ' : index === physical.stops.length - 1 ? '≥ ' : ''}${value}`,
      color: (physical.colors || physicalStateColors)[index]
    }))
  }
})

const terrainBadge = computed(() => {
  if (!hasSpatialResources.value || props.layers.dem === false) return null
  if (app.isSelinco) {
    return {
      label: `${app.activeScaleMeta.label} DEM`,
      cellCount: app.activeScaleMeta.cellCount,
      elevationMin: app.activeScaleMeta.terrain.elevationMin,
      elevationMax: app.activeScaleMeta.terrain.elevationMax
    }
  }
  const terrain = caseData.value?.spatial?.terrain
  if (!terrain) return null
  return {
    label: `${app.activeScaleMeta.label} 网格高程`,
    cellCount: terrain.cellCount,
    elevationMin: Math.round(terrain.basinElevationMin ?? terrain.elevationMin),
    elevationMax: Math.round(terrain.basinElevationMax ?? terrain.elevationMax)
  }
})

function makeDemStyle() {
  // 无外网底图：用纯色 + hillshade 模拟灰色 DEM 氛围
  return {
    version: 8,
    glyphs: 'hydro-font://local/{fontstack}/{range}.pbf',
    sources: {},
    layers: [
      {
        id: 'background',
        type: 'background',
        paint: {
          'background-color': '#d9e0e6'
        }
      }
    ]
  }
}

function terrainSourceDefinition(scale) {
  const terrain = terrainForScale(scale)
  return {
    type: 'image',
    url: terrain.url,
    coordinates: terrain.coordinates
  }
}

async function preloadTerrainImages() {
  if (!app.isSelinco || typeof Image === 'undefined') return
  const urls = ['1km', '0p1deg'].map((scale) => terrainForScale(scale)?.url).filter(Boolean)
  await Promise.all(urls.map((url) => new Promise((resolve) => {
    const image = new Image()
    image.onload = resolve
    image.onerror = resolve
    image.src = url
  })))
}

// 南安溪 DEM 直接按已对齐的有效格网显示，避免把模型输入退化成平滑图片。
function terrainElevationColorExpr(scale = app.modelScale) {
  const terrain = activeProfile()?.terrain?.[scale] || {}
  const min = Number(terrain.basinElevationMin ?? terrain.elevationMin ?? 0)
  const max = Number(terrain.basinElevationMax ?? terrain.elevationMax ?? min + 1)
  const span = Math.max(1, max - min)
  const stops = [
    [min, 'rgba(236, 241, 235, 0.86)'],
    [min + span * 0.18, 'rgba(211, 222, 204, 0.88)'],
    [min + span * 0.38, 'rgba(181, 198, 164, 0.90)'],
    [min + span * 0.58, 'rgba(171, 157, 119, 0.90)'],
    [min + span * 0.78, 'rgba(137, 116, 89, 0.90)'],
    [max, 'rgba(96, 82, 72, 0.92)']
  ]
  return ['interpolate', ['linear'], ['coalesce', ['get', 'elevation'], min], ...stops.flat()]
}

function terrainForScale(scale) {
  if (app.isSelinco) {
    return { url: selincoProfile.terrainUrls[scale], coordinates: selincoProfile.imageCoordinates }
  }
  return activeProfile()?.terrain?.[scale] || { url: TRANSPARENT_PIXEL, coordinates: smoothRainCoordinates() }
}

function hasTerrain(scale = app.modelScale) {
  return hasSpatialResources.value && (app.isSelinco || Boolean(activeProfile()?.terrain?.[scale]))
}

function syncTerrainCoordinates(map) {
  if (!map || mapRef.value !== map || mapDestroyed || !map.getContainer?.()?.isConnected) return
  for (const [id, scale] of [['terrain-dem-1km', '1km'], ['terrain-dem-0p1deg', '0p1deg']]) {
    const source = map.getSource(id)
    const terrain = terrainForScale(scale)
    if (!hasTerrain(scale) || !source) continue
    const signature = `${terrain.url}|${JSON.stringify(terrain.coordinates || [])}`
    if (terrainImageSignatures.get(id) === signature) continue
    terrainImageSignatures.set(id, signature)
    try {
      const update = typeof source.updateImage === 'function'
        ? source.updateImage(terrain)
        : typeof source.setCoordinates === 'function'
          ? source.setCoordinates(terrain.coordinates)
          : null
      if (update && typeof update.catch === 'function') update.catch(() => {})
    } catch {
      // Route transitions can remove an image source while a profile update is pending.
    }
  }
}

function activeProfile() {
  if (!hasSpatialResources.value) return null
  if (app.isSelinco) return selincoProfile
  return externalProfile.value
}

async function loadExternalProfile() {
  if (app.isSelinco) {
    externalProfile.value = null
    return selincoProfile
  }
  const spatial = caseData.value?.spatial
  const assets = caseData.value?.spatialAssets
  const metadata = caseData.value?.spatialMetadata
  if (!spatial?.basin || !assets?.basin) {
    externalProfile.value = null
    return null
  }
  externalProfile.value = {
    basin: assets.basin,
    subbasins: actualSubbasinGeometry(assets.subbasins),
    rivers: assets.rivers,
    bounds: metadata?.bounds || geoJSONBounds(assets.basin),
    displayBounds: metadata?.bounds || geoJSONBounds(assets.basin),
    center: metadata?.center,
    outsideMask: EMPTY_GEOJSON,
    lake: EMPTY_GEOJSON,
    glaciers: EMPTY_GEOJSON,
    gridUrls: spatial.terrain?.gridUrl ? { [app.modelScale]: spatial.terrain.gridUrl } : {},
    terrain: spatial.terrain ? { [app.modelScale]: spatial.terrain } : {}
  }
  return externalProfile.value
}

function ensureSources(map) {
  const profile = activeProfile()
  if (!map.getSource('basin')) {
    map.addSource('basin', { type: 'geojson', data: profile?.basin || EMPTY_GEOJSON })
  }
  if (!map.getSource('outside-mask')) {
    map.addSource('outside-mask', { type: 'geojson', data: profile?.outsideMask || EMPTY_GEOJSON })
  }
  if (!map.getSource('subbasins')) {
    map.addSource('subbasins', { type: 'geojson', data: profile?.subbasins || EMPTY_GEOJSON })
  }
  if (!map.getSource('rivers')) {
    map.addSource('rivers', { type: 'geojson', data: profile?.rivers || EMPTY_GEOJSON })
  }
  if (!map.getSource('lakes')) {
    map.addSource('lakes', { type: 'geojson', data: profile?.lake || EMPTY_GEOJSON })
  }
  if (!map.getSource('glaciers')) {
    map.addSource('glaciers', { type: 'geojson', data: profile?.glaciers || EMPTY_GEOJSON })
  }
  // External projects render their aligned DEM cells from `real-grid`; only
  // Selinco still uses the legacy image preview source.
  if (app.isSelinco) {
    if (!map.getSource('terrain-dem-1km')) {
      const terrain = terrainSourceDefinition('1km')
      map.addSource('terrain-dem-1km', terrain)
      terrainImageSignatures.set('terrain-dem-1km', `${terrain.url}|${JSON.stringify(terrain.coordinates || [])}`)
    }
    if (!map.getSource('terrain-dem-0p1deg')) {
      const terrain = terrainSourceDefinition('0p1deg')
      map.addSource('terrain-dem-0p1deg', terrain)
      terrainImageSignatures.set('terrain-dem-0p1deg', `${terrain.url}|${JSON.stringify(terrain.coordinates || [])}`)
    }
  }
  if (!map.getSource('real-grid')) {
    map.addSource('real-grid', { type: 'geojson', data: profile?.gridUrls?.[app.modelScale] || EMPTY_GEOJSON })
  }
  if (!map.getSource('stations')) {
    map.addSource('stations', { type: 'geojson', data: stationsToGeoJSON(props.stations) })
  }
  if (!map.getSource('grid')) {
    const cells = hasSpatialResources.value ? props.gridCells || [] : []
    map.addSource('grid', { type: 'geojson', data: gridToGeoJSON(cells) })
  }
  if (!map.getSource('qpf-grid')) {
    map.addSource('qpf-grid', { type: 'geojson', data: gridToGeoJSON(props.qpfGridCells || []) })
  }
  if (!map.getSource('rain-heat')) {
    // 用站点点做热力模拟降雨场
    const heat = {
      type: 'FeatureCollection',
      features: props.stations
        .filter((s) => s.type === 'rain')
        .map((s) => ({
          type: 'Feature',
          properties: { weight: s.rain1h || 1 },
          geometry: { type: 'Point', coordinates: [s.lng, s.lat] }
        }))
    }
    map.addSource('rain-heat', { type: 'geojson', data: heat })
  }
  if (!map.getSource('smooth-rain')) {
    map.addSource('smooth-rain', {
      type: 'image',
      url: TRANSPARENT_PIXEL,
      coordinates: smoothRainCoordinates()
    })
  }
}

function ensureLayers(map) {
  const layers = [
    {
      id: 'terrain-dem-1km',
      type: 'raster',
      source: 'terrain-dem-1km',
      layout: { visibility: app.isSelinco && hasTerrain('1km') && app.modelScale === '1km' ? 'visible' : 'none' },
      paint: { 'raster-opacity': 0.96, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 }
    },
    {
      id: 'terrain-dem-0p1deg',
      type: 'raster',
      source: 'terrain-dem-0p1deg',
      layout: { visibility: app.isSelinco && hasTerrain('0p1deg') && app.modelScale === '0p1deg' ? 'visible' : 'none' },
      paint: { 'raster-opacity': 0.96, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 }
    },
    {
      id: 'outside-mask-fill',
      type: 'fill',
      source: 'outside-mask',
      paint: { 'fill-color': '#172b35', 'fill-opacity': 0.32 }
    },
    {
      id: 'subbasin-fill',
      type: 'fill',
      source: 'subbasins',
      paint: {
        'fill-color': SUBBASIN_FILL_COLOR,
        'fill-opacity': 1
      }
    },
    {
      id: 'subbasin-line',
      type: 'line',
      source: 'subbasins',
      paint: {
        'line-color': ['match', ['get', 'basinIndex'], 1, '#b88c29', 2, '#287e9f', 3, '#4d8c4b', 4, '#98548d', '#6e8793'],
        'line-width': 1.6,
        'line-opacity': 0.95
      }
    },
    {
      id: 'subbasin-label',
      type: 'symbol',
      source: 'subbasins',
      layout: {
        'text-field': ['get', 'name'],
        'text-size': 11,
        'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular']
      },
      paint: { 'text-color': '#3f5961', 'text-halo-color': 'rgba(255,255,255,0.9)', 'text-halo-width': 1.4 }
    },
    {
      id: 'lake-fill',
      type: 'fill',
      source: 'lakes',
      paint: { 'fill-color': '#8fc9dc', 'fill-opacity': 0.9 }
    },
    {
      id: 'lake-line',
      type: 'line',
      source: 'lakes',
      paint: { 'line-color': '#327f9e', 'line-width': 1.8 }
    },
    {
      id: 'lake-label',
      type: 'symbol',
      source: 'lakes',
      layout: { 'text-field': ['get', 'name'], 'text-size': 14, 'text-font': ['Open Sans Semibold', 'Arial Unicode MS Regular'] },
      paint: { 'text-color': '#165d7a', 'text-halo-color': 'rgba(255,255,255,0.9)', 'text-halo-width': 1.6 }
    },
    {
      id: 'glacier-fill',
      type: 'fill',
      source: 'glaciers',
      paint: { 'fill-color': '#dff5fa', 'fill-opacity': 0.92, 'fill-outline-color': '#75b8ca' }
    },
    {
      id: 'terrain-grid-fill',
      type: 'fill',
      source: 'real-grid',
      layout: { visibility: !app.isSelinco && props.layers.dem !== false ? 'visible' : 'none' },
      paint: {
        'fill-color': terrainElevationColorExpr(),
        'fill-outline-color': 'rgba(76, 89, 82, 0.16)'
      }
    },
    {
      id: 'real-grid-line',
      type: 'line',
      source: 'real-grid',
      minzoom: app.modelScale === '1km' ? 8 : 5,
      paint: {
        'line-color': app.modelScale === '1km' ? '#476e72' : '#c85f2a',
        'line-width': app.modelScale === '1km' ? 0.45 : 1.1,
        'line-opacity': app.modelScale === '1km' ? 0.5 : 0.78
      }
    },
    {
      id: 'basin-line',
      type: 'line',
      source: 'basin',
      paint: {
        'line-color': '#3d6f9e',
        'line-width': 2.2
      }
    },
    {
      id: 'rain-heat',
      type: 'heatmap',
      source: 'rain-heat',
      maxzoom: 12,
      paint: {
        'heatmap-weight': ['interpolate', ['linear'], ['get', 'weight'], 0, 0, 20, 1],
        'heatmap-intensity': 1.1,
        'heatmap-radius': 42,
        'heatmap-opacity': 0.72,
        'heatmap-color': [
          'interpolate',
          ['linear'],
          ['heatmap-density'],
          0, 'rgba(0,0,0,0)',
          0.2, 'rgba(158,208,255,0.55)',
          0.4, 'rgba(90,174,240,0.65)',
          0.6, 'rgba(240,211,90,0.7)',
          0.8, 'rgba(242,160,58,0.75)',
          1, 'rgba(226,75,59,0.8)'
        ]
      }
    },
    {
      id: 'smooth-rain-fill',
      type: 'raster',
      source: 'smooth-rain',
      layout: { visibility: 'none' },
      paint: {
        'raster-opacity': 0.78,
        'raster-resampling': 'linear',
        'raster-fade-duration': 0
      }
    },
    {
      id: 'qpf-grid-fill',
      type: 'fill',
      source: 'qpf-grid',
      layout: { visibility: 'none' },
      paint: {
        'fill-color': gridRainfallColorExpression()
      }
    },
    {
      id: 'qpf-grid-line',
      type: 'line',
      source: 'qpf-grid',
      layout: { visibility: 'none' },
      paint: {
        'line-color': '#f7fbff',
        'line-width': 0.45,
        'line-opacity': 0.32
      }
    },
    {
      id: 'grid-fill',
      type: 'fill',
      source: 'grid',
      layout: { visibility: 'none' },
      paint: {
        'fill-color': gridColorExpr(props.gridPaintField),
        'fill-outline-color': 'rgba(70,90,110,0.18)'
      }
    },
    {
      id: 'grid-line',
      type: 'line',
      source: 'grid',
      layout: { visibility: 'none' },
      paint: {
        'line-color': '#16877d',
        'line-width': 0.55,
        'line-opacity': 0.46
      }
    },
    {
      id: 'rivers',
      type: 'line',
      source: 'rivers',
      paint: {
        'line-color': '#145f9e',
        'line-width': [
          'match',
          ['get', 'order'],
          1, 1.05,
          2, 1.7,
          3, 2.4,
          4, 3.1,
          5, 4,
          1
        ],
        'line-opacity': [
          'interpolate', ['linear'], ['zoom'],
          6, ['match', ['get', 'order'], 1, 0.05, 2, 0.35, 0.94],
          8, ['match', ['get', 'order'], 1, 0.68, 2, 0.82, 0.94]
        ]
      }
    },
    {
      id: 'stations-circle',
      type: 'circle',
      source: 'stations',
      paint: {
        'circle-radius': [
          'case',
          ['==', ['get', 'id'], props.selectedId || ''],
          9,
          [
            'match',
            ['get', 'type'],
            'section', 8,
            'hydro', 7,
            'water', 6.5,
            6
          ]
        ],
        'circle-color': [
          'match',
          ['get', 'type'],
          'rain', '#1f9d55',
          'hydro', '#0b57c7',
          'water', '#1aa3c8',
          'section', '#f26c18',
          '#666'
        ],
        'circle-stroke-color': '#fff',
        'circle-stroke-width': 2
      }
    },
    {
      id: 'stations-label',
      type: 'symbol',
      source: 'stations',
      layout: {
        'text-field': ['case', ['all', ['==', ['get', 'type'], 'rain'], ['has', 'rain1h'], ['>=', ['coalesce', ['get', 'rain1h'], -1], 0]], ['concat', ['get', 'name'], '\n', ['to-string', ['get', 'rain1h']], ' mm'], ['get', 'name']],
        'text-size': 14,
        'text-offset': [0, 1.25],
        'text-anchor': 'top',
        'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular']
      },
      paint: {
        'text-color': '#1e3558',
        'text-halo-color': 'rgba(255,255,255,0.92)',
        'text-halo-width': 1.4
      }
    },
    {
      id: 'stations-priority-circle',
      type: 'circle',
      source: 'stations',
      filter: ['==', ['get', 'id'], '701AAJ02'],
      paint: {
        'circle-radius': 6,
        'circle-color': '#1f9d55',
        'circle-stroke-color': '#fff',
        'circle-stroke-width': 2
      }
    },
    {
      id: 'stations-priority-label',
      type: 'symbol',
      source: 'stations',
      filter: ['==', ['get', 'id'], '701AAJ02'],
      layout: {
        'text-field': ['case', ['all', ['has', 'rain1h'], ['>=', ['coalesce', ['get', 'rain1h'], -1], 0]], ['concat', ['get', 'name'], '\n', ['to-string', ['get', 'rain1h']], ' mm'], ['get', 'name']],
        'text-size': 14,
        'text-anchor': 'top',
        'text-offset': [0, 2],
        'text-allow-overlap': true,
        'text-ignore-placement': true,
        'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular']
      },
      paint: {
        'text-color': '#176b3a',
        'text-halo-color': 'rgba(255,255,255,0.96)',
        'text-halo-width': 1.8
      }
    }
  ]

  layers
    .filter((layer) => app.isSelinco || !layer.id.startsWith('terrain-dem-'))
    .forEach((layer) => {
    if (!map.getLayer(layer.id)) map.addLayer(layer)
    })
}

function applyLayerVisibility(map) {
  if (!map) return
  const vis = (on) => (on ? 'visible' : 'none')
  applyBasinBoundaryVisibility(map, {
    isSelinco: app.isSelinco,
    subbasins: activeProfile()?.subbasins,
    enabled: props.layers.subbasin
  })
  if (map.getLayer('rivers')) {
    map.setLayoutProperty('rivers', 'visibility', vis(props.layers.river))
  }
  if (map.getLayer('rain-heat')) {
    map.setLayoutProperty('rain-heat', 'visibility', vis(app.isSelinco && props.layers.rainField))
  }
  if (map.getLayer('smooth-rain-fill')) {
    map.setLayoutProperty('smooth-rain-fill', 'visibility', vis(smoothRainEnabled.value && smoothRainHasImage))
    map.setPaintProperty('smooth-rain-fill', 'raster-opacity', usesGridRainSmoothing.value ? 1 : 0.78)
  }
  if (map.getLayer('grid-fill')) {
    map.setLayoutProperty('grid-fill', 'visibility', vis(props.layers.grid || props.interactiveGrid))
    map.setLayoutProperty('grid-line', 'visibility', vis(props.layers.grid || props.interactiveGrid))
  }
  if (map.getLayer('qpf-grid-fill')) {
    map.setLayoutProperty('qpf-grid-fill', 'visibility', vis(props.layers.qpfGrid))
    map.setLayoutProperty('qpf-grid-line', 'visibility', vis(props.layers.qpfGrid))
  }
  if (map.getLayer('terrain-dem-1km') || map.getLayer('terrain-grid-fill')) {
    if (map.getLayer('outside-mask-fill')) map.setLayoutProperty('outside-mask-fill', 'visibility', vis(app.isSelinco))
    if (map.getLayer('terrain-dem-1km')) map.setLayoutProperty('terrain-dem-1km', 'visibility', vis(app.isSelinco && hasTerrain('1km') && app.modelScale === '1km' && props.layers.dem !== false))
    if (map.getLayer('terrain-dem-0p1deg')) map.setLayoutProperty('terrain-dem-0p1deg', 'visibility', vis(app.isSelinco && hasTerrain('0p1deg') && app.modelScale === '0p1deg' && props.layers.dem !== false))
    if (map.getLayer('terrain-grid-fill')) {
      map.setLayoutProperty('terrain-grid-fill', 'visibility', vis(!app.isSelinco && props.layers.dem !== false))
      map.setPaintProperty('terrain-grid-fill', 'fill-color', terrainElevationColorExpr())
    }
    map.setLayoutProperty('lake-fill', 'visibility', vis(app.isSelinco && props.layers.lake !== false))
    map.setLayoutProperty('lake-line', 'visibility', vis(app.isSelinco && props.layers.lake !== false))
    map.setLayoutProperty('lake-label', 'visibility', vis(app.isSelinco && props.layers.lake !== false))
    map.setLayoutProperty('glacier-fill', 'visibility', vis(app.isSelinco && props.layers.glacier !== false))
    map.setLayoutProperty('real-grid-line', 'visibility', vis(
      app.isSelinco ? props.layers.realGrid !== false : props.layers.grid !== false
    ))
  }
  // 站点按类型过滤：通过 filter
  if (map.getLayer('stations-circle')) {
    const allowed = []
    if (props.layers.rain) allowed.push('rain')
    if (props.layers.hydro) allowed.push('hydro')
    if (props.layers.water) allowed.push('water')
    if (props.layers.section) allowed.push('section')
    const filter = [
      'all',
      ['in', ['get', 'type'], ['literal', allowed]],
      ['!=', ['get', 'id'], '701AAJ02']
    ]
    map.setFilter('stations-circle', filter)
    map.setFilter('stations-label', filter)
    map.setLayoutProperty('stations-priority-circle', 'visibility', vis(props.layers.rain))
    map.setLayoutProperty('stations-priority-label', 'visibility', vis(props.layers.rain))
  }
}

function activeBounds() {
  return activeProfile()?.bounds || null
}

function smoothRainGeometry() {
  return activeProfile()?.basin || EMPTY_GEOJSON
}

function smoothRainBounds() {
  return geoJSONBounds(smoothRainGeometry()) || activeBounds()
}

function smoothRainCoordinates() {
  const bounds = smoothRainBounds() || [[-0.01, -0.01], [0.01, 0.01]]
  return [
    [bounds[0][0], bounds[1][1]],
    [bounds[1][0], bounds[1][1]],
    [bounds[1][0], bounds[0][1]],
    [bounds[0][0], bounds[0][1]]
  ]
}

function smoothRainStationSignature(stations = []) {
  return stations
    .filter((station) => station.type === 'rain' && station.rain1h != null && station.rain1h !== '' && Number.isFinite(Number(station.lng)) && Number.isFinite(Number(station.lat)) && Number.isFinite(Number(station.rain1h)))
    .map((station) => `${station.id || station.code || ''}:${Number(station.lng).toFixed(6)}:${Number(station.lat).toFixed(6)}:${Number(station.rain1h).toFixed(3)}`)
    .join('|')
}

function navigationBounds() {
  // 外部项目的流域通常狭长，宽屏容器配合 maxBounds 会抬高最小缩放级别，
  // 导致南北边界无论如何缩放都无法同时进入视口。
  if (!app.isSelinco) return null
  const profile = activeProfile()
  const bounds = profile?.displayBounds || profile?.bounds
  if (!bounds) return null
  const lngSpan = Math.max(0.08, bounds[1][0] - bounds[0][0])
  const latSpan = Math.max(0.08, bounds[1][1] - bounds[0][1])
  return [[bounds[0][0] - lngSpan * 0.35, bounds[0][1] - latSpan * 0.35], [bounds[1][0] + lngSpan * 0.35, bounds[1][1] + latSpan * 0.35]]
}

function fittedBounds() {
  const bounds = activeBounds()
  if (!bounds) return null
  const lngSpan = bounds[1][0] - bounds[0][0]
  const latSpan = bounds[1][1] - bounds[0][1]
  return [
    [bounds[0][0] - lngSpan * 0.05, bounds[0][1] - latSpan * 0.09],
    [bounds[1][0] + lngSpan * 0.05, bounds[1][1] + latSpan * 0.09]
  ]
}

function fitActiveExtent(duration = 0) {
  const map = mapRef.value
  if (!map) return
  map.resize()
  const width = rootEl.value?.clientWidth || 800
  const height = rootEl.value?.clientHeight || 500
  const padding = width < 520 || height < 360 ? 18 : width < 900 ? 32 : 46
  const bounds = fittedBounds()
  if (!bounds) {
    map.easeTo({ center: [0, 0], zoom: 3, duration })
    return
  }
  const camera = map.cameraForBounds(bounds, { padding })
  if (!camera) return
  const maxZoom = app.isSelinco ? 7.2 : 13
  map.easeTo({
    ...camera,
    zoom: Math.min(camera.zoom, maxZoom) - (app.isSelinco ? 0.15 : 0.22),
    duration
  })
}

function updateBasinProfile(map, fit = true) {
  if (!map?.getSource('basin')) return
  ensureSources(map)
  ensureLayers(map)
  syncTerrainCoordinates(map)
  const profile = activeProfile()
  map.getSource('basin').setData(profile?.basin || EMPTY_GEOJSON)
  map.getSource('outside-mask').setData(profile?.outsideMask || EMPTY_GEOJSON)
  map.getSource('subbasins').setData(profile?.subbasins || EMPTY_GEOJSON)
  map.getSource('rivers').setData(profile?.rivers || EMPTY_GEOJSON)
  map.getSource('lakes').setData(profile?.lake || EMPTY_GEOJSON)
  map.getSource('glaciers').setData(profile?.glaciers || EMPTY_GEOJSON)
  map.getSource('real-grid').setData(profile?.gridUrls?.[app.modelScale] || EMPTY_GEOJSON)
  updateGrid()
  updateQpfGrid()
  updateSmoothRainfall(map)

  const show = app.isSelinco ? 'visible' : 'none'
  for (const id of ['outside-mask-fill', 'lake-fill', 'lake-line', 'lake-label', 'glacier-fill']) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', show)
  }
  if (map.getLayer('real-grid-line')) {
    map.setLayoutProperty('real-grid-line', 'visibility', app.isSelinco
      ? (props.layers.realGrid === false ? 'none' : 'visible')
      : (props.layers.grid === false ? 'none' : 'visible'))
  }
  if (map.getLayer('real-grid-line')) {
    map.setLayerZoomRange('real-grid-line', app.modelScale === '1km' ? 8 : 5, 24)
    map.setPaintProperty('real-grid-line', 'line-color', app.modelScale === '1km' ? '#476e72' : '#c85f2a')
    map.setPaintProperty('real-grid-line', 'line-width', app.modelScale === '1km' ? 0.45 : 1.1)
  }
  map.setMaxBounds(navigationBounds())
  if (fit) fitActiveExtent(500)
  applyLayerVisibility(map)
}

function stationPopupHtml(p) {
  const code = p.code || p.id || '待登记'
  const external = p.sourceId === 'verified-spatial-source' || p.source === 'verified-spatial-source' || p.source === '已核实空间资料'
  const value = (raw, unit = '') => raw == null || raw === '' ? '待接入' : `${raw}${unit ? ` ${unit}` : ''}`
  if (p.type === 'rain') {
    return `
      <div class="sp">
        <strong>${p.name}</strong>
        <div class="sp-row"><span>站码</span><b>${code}</b></div>
        <div class="sp-row"><span>最近时间</span><b>${p.lastTime || '待接入'}</b></div>
        <div class="sp-row"><span>近 1 小时雨量</span><b>${value(p.rain1h, 'mm')}</b></div>
        <div class="sp-row"><span>累计雨量</span><b>${value(p.rainTotal, 'mm')}</b></div>
        <div class="sp-row"><span>质量标识</span><b class="ok">${p.quality || (external ? '仅空间登记' : '正常')}</b></div>
      </div>`
  }
  if (p.type === 'hydro') {
    return `
      <div class="sp">
        <strong>${p.name}</strong>
        <div class="sp-row"><span>站码</span><b>${code}</b></div>
        <div class="sp-row"><span>流量</span><b>${value(p.flow, 'm³/s')}</b></div>
        <div class="sp-row"><span>水位</span><b>${value(p.level, 'm')}</b></div>
        <div class="sp-row"><span>近 3 小时变化</span><b>${value(p.delta3h, 'm³/s')}</b></div>
      </div>`
  }
  if (p.type === 'water') {
    return `
      <div class="sp">
        <strong>${p.name}</strong>
        <div class="sp-row"><span>站码</span><b>${code}</b></div>
        <div class="sp-row"><span>水位</span><b>${value(p.level, 'm')}</b></div>
        <div class="sp-row"><span>警戒水位</span><b>${value(p.levelWarn, 'm')}</b></div>
      </div>`
  }
  return `
    <div class="sp">
      <strong>${p.name}</strong>
      <div class="sp-row"><span>断面编码</span><b>${code}</b></div>
      <div class="sp-row"><span>当前流量</span><b>${value(p.flow, 'm³/s')}</b></div>
      <div class="sp-row"><span>源登记集水面积</span><b>${value(p.registeredCatchmentAreaKm2, 'km²')}</b></div>
      <div class="sp-row"><span>类型</span><b>${p.typeLabel || '控制断面'} · ${external ? '空间登记' : '实况'}</b></div>
    </div>`
}

function bindEvents(map) {
  for (const layerId of ['stations-circle', 'stations-priority-circle']) {
    map.on('mouseenter', layerId, (e) => {
      map.getCanvas().style.cursor = 'pointer'
      const f = e.features?.[0]
      if (!f) return
      const p = f.properties
      emit('hover-station', p.id)
      if (!popup) {
        popup = new maplibregl.Popup({
          closeButton: false,
          closeOnClick: false,
          offset: 14,
          maxWidth: '260px',
          className: 'station-hover-popup'
        })
      }
      popup.setLngLat(f.geometry.coordinates).setHTML(stationPopupHtml(p)).addTo(map)
    })

    map.on('mouseleave', layerId, () => {
      map.getCanvas().style.cursor = ''
      emit('hover-station', null)
      popup?.remove()
    })

    map.on('click', layerId, (e) => {
      const f = e.features?.[0]
      if (!f) return
      emit('select-station', f.properties.id)
    })
  }

  map.on('click', 'grid-fill', (e) => {
    if (!props.interactiveGrid && !props.layers.grid) return
    const f = e.features?.[0]
    if (!f) return
    // 仅抛事件，由业务页弹窗展示，避免地图上大浮层遮挡
    emit('select-grid', f.properties.id, f.properties)
  })
}

function gridColorExpr(field = 'runoff') {
  const property = physicalStateFields[field] ? field : 'runoff'
  const definition = physicalStateFields[property]
  return ['case', ['==', ['typeof', ['get', property]], 'number'],
    ['interpolate', ['linear'], ['get', property], ...definition.stops
      .flatMap((value, index) => [value, (definition.colors || physicalStateColors)[index]])],
    'rgba(0,0,0,0)']
}

function updateGrid() {
  const map = mapRef.value
  if (!map?.getSource('grid')) return
  const cells = hasSpatialResources.value ? props.gridCells || [] : []
  map.getSource('grid').setData(gridToGeoJSON(cells))
  if (map.getLayer('grid-fill')) {
    map.setPaintProperty('grid-fill', 'fill-color', gridColorExpr(props.gridPaintField))
  }
  applyLayerVisibility(map)
}

function updateQpfGrid() {
  const map = mapRef.value
  if (!map?.getSource('qpf-grid')) return
  const cells = hasSpatialResources.value ? props.qpfGridCells || [] : []
  const signature = gridRainfallDisplaySignature(cells)
  if (signature === qpfGridSignature) return
  qpfGridSignature = signature
  qpfGridPending = { map, cells }
  if (qpfGridFrame != null) return
  const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (callback) => setTimeout(callback, 0)
  qpfGridFrame = schedule(() => {
    qpfGridFrame = null
    const pending = qpfGridPending
    qpfGridPending = null
    if (!pending || mapRef.value !== pending.map || !pending.map.getSource('qpf-grid')) return
    pending.map.getSource('qpf-grid').setData(gridToGeoJSON(pending.cells))
    applyLayerVisibility(pending.map)
  })
}

function updateSmoothImage(map, url, coordinates) {
  smoothImageUpdatePending = { map, url, coordinates }
  if (smoothImageUpdateTimer != null) return
  const flush = () => {
    smoothImageUpdateTimer = null
    const pending = smoothImageUpdatePending
    const source = pending?.map === mapRef.value ? pending.map.getSource('smooth-rain') : null
    if (!source || mapDestroyed) { smoothImageUpdatePending = null; return }
    if (!source.loaded()) { smoothImageUpdateTimer = setTimeout(flush, 16); return }
    const current = source.serialize()
    if (current.url === pending.url) {
      smoothImageUpdatePending = null
      if (JSON.stringify(source.coordinates) !== JSON.stringify(pending.coordinates)) source.setCoordinates(pending.coordinates)
      smoothRainHasImage = pending.url !== TRANSPARENT_PIXEL
      applyLayerVisibility(pending.map)
      return
    }
    // ImageSource.updateImage aborts an unfinished decode. Keep only the latest
    // requested image and wait for the current image to finish instead.
    source.updateImage({ url: pending.url, coordinates: pending.coordinates })
    smoothRainHasImage = false
    applyLayerVisibility(pending.map)
    smoothImageUpdateTimer = setTimeout(flush, 16)
  }
  flush()
}

function clearSmoothRainfall(map = mapRef.value) {
  smoothRainPending = null
  cancelSmoothRain()
  clearTimeout(smoothRainThrottleTimer)
  smoothRainThrottleTimer = null
  smoothRainLastRenderAt = 0
  smoothRainHasImage = false
  if (map?.getSource('smooth-rain')) updateSmoothImage(map, TRANSPARENT_PIXEL, smoothRainCoordinates())
  applyLayerVisibility(map)
}

function updateSmoothRainfall(map = mapRef.value, { force = false } = {}) {
  if (!map?.getSource('smooth-rain')) return
  if (force && smoothRainThrottleTimer != null) {
    clearTimeout(smoothRainThrottleTimer)
    smoothRainThrottleTimer = null
  }
  if (!smoothRainEnabled.value) {
    clearSmoothRainfall(map)
    return
  }
  const geometry = smoothRainGeometry()
  const bounds = smoothRainBounds()
  const gridSource = usesGridRainSmoothing.value
  if (!bounds || !hasSpatialResources.value || (gridSource && !hasGridRainfall(props.qpfGridCells || []))) {
    clearSmoothRainfall(map)
    return
  }
  const coordinates = smoothRainCoordinates()
  const contextKey = `${app.activeContext.contextKey}|${gridSource ? app.inputSelectionKey : ''}|${props.smoothRainSource}|${JSON.stringify(geometry)}`
  if (smoothRainContextKey !== contextKey) {
    clearSmoothRainfall(map)
    smoothRainCache.clear()
    smoothRainContextKey = contextKey
  }
  const cells = gridSource ? (props.qpfGridCells || []).map(cell => ({ ...cell, bounds: cell.bounds?.map(point => [...point]) })) : []
  const stations = gridSource ? [] : props.stations.map(station => ({ ...station }))
  const signature = `${contextKey}|${gridSource ? gridRainfallDisplaySignature(cells) : smoothRainStationSignature(stations)}`
  const pending = { map, geometry, bounds, coordinates, signature, gridSource, cells, stations }
  if (props.playing && !force && Date.now() - smoothRainLastRenderAt < (gridSource ? 160 : 760)) {
    smoothRainPending = pending
    cancelSmoothRain()
    scheduleSmoothRainThrottle(map)
    return
  }
  const source = map.getSource('smooth-rain')
  const cached = smoothRainCache.get(signature)
  if (cached && typeof source.updateImage === 'function') {
    smoothRainPending = null
    cancelSmoothRain()
    clearTimeout(smoothRainThrottleTimer)
    smoothRainThrottleTimer = null
    updateSmoothImage(map, cached, coordinates)
    smoothRainLastRenderAt = Date.now()
    applyLayerVisibility(map)
    return
  }
  applyLayerVisibility(map)
  smoothRainPending = pending
  if (smoothRainFrame != null) return
  smoothRainFrame = scheduleSmoothRain(() => {
    smoothRainFrame = null
    const pending = smoothRainPending
    smoothRainPending = null
    if (!pending || mapRef.value !== pending.map || !pending.map.getSource('smooth-rain')) return
    // 这是展示层，不是模型输入；降低显示分辨率并在空闲时生成，避免回放抢占地图重绘。
    const url = (pending.gridSource ? buildSmoothGridRainfallDataURL({
      cells: pending.cells, basin: pending.geometry, bounds: pending.bounds, size: 192
    }) : buildSmoothRainfallDataURL({
      stations: pending.stations, basin: pending.geometry, bounds: pending.bounds, size: 128
    })) || TRANSPARENT_PIXEL
    smoothRainCache.set(pending.signature, url)
    while (smoothRainCache.size > 12) smoothRainCache.delete(smoothRainCache.keys().next().value)
    const pendingSource = pending.map.getSource('smooth-rain')
    if (typeof pendingSource.updateImage === 'function') updateSmoothImage(pending.map, url, pending.coordinates)
    smoothRainLastRenderAt = Date.now()
    applyLayerVisibility(pending.map)
  })
}

function zoomBy(delta) {
  const map = mapRef.value
  if (!map) return
  map.zoomTo(map.getZoom() + delta, { duration: 250 })
}

function resetView() {
  fitActiveExtent(400)
}

function updateStations() {
  const map = mapRef.value
  if (!map?.getSource('stations')) return
  map.getSource('stations').setData(stationsToGeoJSON(props.stations))
  const heat = {
    type: 'FeatureCollection',
    features: props.stations
      .filter((s) => s.type === 'rain')
      .map((s) => ({
        type: 'Feature',
        properties: { weight: s.rain1h || 1 },
        geometry: { type: 'Point', coordinates: [s.lng, s.lat] }
      }))
  }
  map.getSource('rain-heat')?.setData(heat)
  updateSmoothRainfall(map)
  applyLayerVisibility(map)
  if (map.getLayer('stations-circle')) {
    map.setPaintProperty('stations-circle', 'circle-radius', [
      'case',
      ['==', ['get', 'id'], props.selectedId || ''],
      9,
      ['match', ['get', 'type'], 'section', 8, 'hydro', 7, 'water', 6.5, 6]
    ])
  }
}

onMounted(async () => {
  mapDestroyed = false
  await loadExternalProfile()
  if (mapDestroyed || !mapEl.value?.isConnected) return
  await preloadTerrainImages()
  // Case/route changes may unmount this view while images are still loading.
  // Do not create a map against the removed component's null container.
  if (mapDestroyed || !mapEl.value?.isConnected) return
  const mapOptions = {
    container: mapEl.value,
    style: makeDemStyle(),
    center: activeProfile()?.center || props.center || [0, 0],
    zoom: props.zoom,
    attributionControl: false,
    minZoom: 3,
    maxZoom: 16,
    scrollZoom: true,
    boxZoom: true,
    doubleClickZoom: true,
    dragPan: true,
    touchZoomRotate: true,
    keyboard: true
  }
  const bounds = navigationBounds()
  if (bounds) mapOptions.maxBounds = bounds
  installLocalMapGlyphs(maplibregl)
  const map = new maplibregl.Map(mapOptions)
  mapRef.value = map
  Object.defineProperty(mapEl.value, '__hydroMap', { value: map, configurable: true })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

  map.on('load', () => {
    if (mapDestroyed || mapRef.value !== map || !mapEl.value?.isConnected) return
    ensureSources(map)
    ensureLayers(map)
    applyLayerVisibility(map)
    bindEvents(map)
    updateBasinProfile(map, false)
    updateSmoothRainfall(map)
    fitActiveExtent(0)
    emit('map-ready', map)
  })

  resizeObs = new ResizeObserver(() => {
    if (mapDestroyed || mapRef.value !== map || !mapEl.value?.isConnected) return
    map.resize()
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => fitActiveExtent(180), 120)
  })
  if (rootEl.value) resizeObs.observe(rootEl.value)
})

onBeforeUnmount(() => {
  mapDestroyed = true
  resizeObs?.disconnect()
  clearTimeout(resizeTimer)
  cancelSmoothRain()
  clearTimeout(smoothRainThrottleTimer)
  smoothRainThrottleTimer = null
  if (qpfGridFrame != null) {
    if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(qpfGridFrame)
    else clearTimeout(qpfGridFrame)
  }
  smoothRainFrame = null
  smoothRainPending = null
  clearTimeout(smoothImageUpdateTimer)
  smoothImageUpdateTimer = null
  smoothImageUpdatePending = null
  smoothRainHasImage = false
  qpfGridFrame = null
  qpfGridPending = null
  qpfGridSignature = ''
  smoothRainCache.clear()
  smoothRainContextKey = ''
  terrainImageSignatures.clear()
  popup?.remove()
  mapRef.value?.remove()
  if (mapEl.value) delete mapEl.value.__hydroMap
  mapRef.value = null
})

watch(
  () => [props.stations, props.selectedId, props.interactiveGrid],
  () => updateStations(),
  { deep: true }
)

watch(
  () => props.layers,
  () => {
    applyLayerVisibility(mapRef.value)
    updateSmoothRainfall(mapRef.value)
  },
  { deep: true }
)

watch(
  () => props.playing,
  (playing) => {
    if (!playing) updateSmoothRainfall(mapRef.value, { force: true })
  }
)

watch(
  () => props.selectedId,
  () => {
    if (!mapRef.value || !app.isSelinco) return
    requestAnimationFrame(() => {
      fitActiveExtent(250)
    })
  }
)

watch(
  () => [app.basinId, app.modelScale, app.projectData, app.projectDataLoading, app.projectDataError],
  async () => {
    await loadExternalProfile()
    updateBasinProfile(mapRef.value)
    updateSmoothRainfall(mapRef.value)
  },
  { deep: true }
)

watch(
  () => [props.gridCells, props.gridPaintField],
  () => updateGrid(),
  { deep: true }
)


watch(
  () => [props.qpfGridCells, props.smoothRainSource],
  () => { updateQpfGrid(); updateSmoothRainfall(mapRef.value) },
  { deep: true }
)

watch(
  () => props.center,
  (c) => {
    if (mapRef.value && c) mapRef.value.easeTo({ center: c, duration: 500 })
  }
)

watch(
  () => props.zoom,
  (z) => {
    if (mapRef.value && z) mapRef.value.easeTo({ zoom: z, duration: 400 })
  }
)

defineExpose({ resetView, zoomBy, getMap: () => mapRef.value })
</script>

<style scoped>
.basin-map {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  background: #d5dde4;
  overflow: hidden;
}

.map-canvas {
  width: 100%;
  height: 100%;
}
.spatial-pending {
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 2;
  padding: 12px 18px;
  border: 1px dashed #8ca4b4;
  border-radius: 4px;
  background: rgba(255,255,255,0.94);
  color: #4a6577;
  transform: translate(-50%, -50%);
  pointer-events: none;
}
.terrain-badge {
  position: absolute;
  top: 12px;
  right: 50px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid rgba(117, 146, 151, 0.55);
  border-radius: 4px;
  background: rgba(255,255,255,0.92);
  color: #4c6268;
  font-size: 10px;
  box-shadow: var(--shadow-soft);
}
.terrain-badge b { color: #17343e; font-size: 11px; }

.map-tools {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 2;
  display: flex;
  gap: 6px;
  padding: 6px;
  border: 1px solid #c7d8ee;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: var(--shadow-soft);
}

.map-tools button {
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid #c8d8ec;
  border-radius: 6px;
  background: linear-gradient(180deg, #fff, #f5f9ff);
  color: #20406d;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.map-tools button:hover {
  border-color: var(--blue);
  color: var(--blue);
  background: var(--blue-soft);
}

.map-legend {
  position: absolute;
  right: 12px;
  bottom: 12px;
  z-index: 2;
  min-width: 148px;
  padding: 10px 12px;
  border: 1px solid #c7d8ee;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: var(--shadow-soft);
  color: #33445f;
  font-size: 12px;
}

.legend-title {
  margin: 0 0 8px;
  color: #173052;
  font-weight: 800;
}
.legend-scale-note { display: block; margin: 0 0 5px; color: #6a7c95; font-size: 9px; }

.legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 18px;
}

.swatch {
  width: 14px;
  height: 10px;
  border-radius: 2px;
  border: 1px solid rgba(0, 0, 0, 0.06);
}

.legend-symbols {
  display: grid;
  gap: 4px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #d4e0ef;
}

.legend-symbols div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.smooth-rain-legend { display: grid; gap: 3px; margin-top: 8px; padding-top: 8px; border-top: 1px dashed #d4e0ef; color: #61777f; font-size: 9px; }
.smooth-rain-gradient { display: block; height: 8px; border-radius: 3px; background: linear-gradient(90deg, #deefff 0%, #5aaef0 25%, #297fca 42%, #f0d35a 62%, #f2a03a 80%, #e24b3b 100%); }
.smooth-rain-legend div { display: flex; justify-content: space-between; }
.smooth-rain-legend small { color: #71858a; font-size: 9px; }

.grid-legend {
  display: grid;
  gap: 5px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #cbd8df;
}

.grid-legend div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.grid-symbol {
  display: inline-block;
  width: 22px;
  height: 10px;
  background: transparent;
}

.grid-symbol.model { border: 1px solid rgba(28, 76, 92, 0.78); }
.grid-symbol.qpf { border: 2px dashed rgba(210, 91, 28, 0.9); }

.sym {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08);
}

.sym.rain { background: #1f9d55; }
.sym.hydro { background: #0b57c7; border-radius: 2px; }
.sym.water { background: #1aa3c8; }
.sym.section { background: #f26c18; border-radius: 2px; transform: rotate(45deg); }
.sym.river { width: 14px; height: 3px; border: 0; border-radius: 2px; background: #3e93d8; box-shadow: none; }
.sym.lake { background: #8fc9dc; border-radius: 2px; }
.sym.glacier { background: #e8fbff; border-color: #75b8ca; border-radius: 2px; }
.rain-empty{position:absolute;left:50%;top:12px;transform:translateX(-50%);padding:8px 14px;background:#fffffff0;border:1px solid #dce7e2;border-radius:4px;color:#667d72;font-size:14px;pointer-events:none}
</style>

<style>
/* popup 全局样式（maplibre 挂到 body） */
.station-hover-popup .maplibregl-popup-content,
.maplibregl-popup-content {
  border-radius: 10px;
}

.sp {
  min-width: 200px;
  padding: 10px 12px 12px;
  color: #33445f;
  font-size: 12px;
}

.sp strong {
  display: block;
  margin-bottom: 8px;
  color: #122c55;
  font-size: 14px;
  font-weight: 800;
}

.sp-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  min-height: 20px;
}

.sp-row span {
  color: #6a7c95;
}

.sp-row b {
  color: #173052;
  font-weight: 800;
}

.sp-row b.ok { color: #15924d; }
.sp-row b.hot { color: #f26c18; }
</style>
