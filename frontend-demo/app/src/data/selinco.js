import basin from '@/data/generated/selinco/basin.json'
import subbasins from '@/data/generated/selinco/subbasins.json'
import lake from '@/data/generated/selinco/lake.json'
import glaciers from '@/data/generated/selinco/glaciers.json'
import metadata from '@/data/generated/selinco/metadata.json'
import rivers from '@/data/generated/selinco/rivers.json'
import outsideMask from '@/data/generated/selinco/outside-mask.json'
import { resourceUrl } from '@/services/resourceUrl'

export const selincoProfile = {
  ...metadata,
  // Bundled metadata uses app-relative resources; never expose root URLs.
  forcing: Object.fromEntries(Object.entries(metadata.forcing || {}).map(([scale, product]) => [
    scale, { ...product, file: resourceUrl(product.file) }
  ])),
  name: '色林错入湖流域群',
  basin,
  subbasins,
  lake,
  glaciers,
  terrainUrls: {
    '1km': resourceUrl('/data/selinco/dem-1km.png'),
    '0p1deg': resourceUrl('/data/selinco/dem-0p1deg.png')
  },
  gridUrls: {
    '1km': resourceUrl('/data/selinco/grid-1km.geojson'),
    '0p1deg': resourceUrl('/data/selinco/grid-0p1deg.geojson')
  },
  riverUrl: resourceUrl('/data/selinco/rivers.geojson'),
  rivers,
  outsideMask
}

const distributedCellCache = new Map()
let precipitationPreviewRequest = null

export async function loadSelincoGridCells(scale = '1km') {
  const normalized = scale === '0p1deg' ? '0p1deg' : '1km'
  if (distributedCellCache.has(normalized)) return distributedCellCache.get(normalized)
  const request = fetch(resourceUrl(`/data/selinco/cells-${normalized}.json`)).then((response) => {
    if (!response.ok) throw new Error(`色林错 ${normalized} 计算单元加载失败`)
    return response.json()
  })
  distributedCellCache.set(normalized, request)
  return request
}

export async function loadSelincoPrecipitationPreview() {
  if (!precipitationPreviewRequest) {
    precipitationPreviewRequest = fetch(resourceUrl('/data/selinco/forcing/precip-hourly-preview.json')).then((response) => {
      if (!response.ok) throw new Error('色林错逐小时降水场加载失败')
      return response.json()
    })
  }
  return precipitationPreviewRequest
}

export const selincoStations = [
  { id: 'SEL-R-001', name: '巴勒切', type: 'rain', typeLabel: '降水站', lng: 91.877766, lat: 32.691672, code: 'SEL-R-001', basin: '扎加藏布入湖流域', elev: 4868, rain1h: 1.8, rain3h: 4.6, rainTotal: 18.7, quality: '正常', reportStatus: '准时', lastTime: '2026-06-02 14:00', interval: '1 h', source: '自动接入' },
  { id: 'SEL-R-002', name: '115道班', type: 'rain', typeLabel: '降水站', lng: 91.83399, lat: 32.516819, code: 'SEL-R-002', basin: '扎加藏布入湖流域', elev: 4782, rain1h: 1.2, rain3h: 3.9, rainTotal: 15.2, quality: '正常', reportStatus: '准时', lastTime: '2026-06-02 14:00', interval: '1 h', source: '自动接入' },
  { id: 'SEL-R-003', name: '杜加2', type: 'rain', typeLabel: '降水站', lng: 89.159979, lat: 32.030571, code: 'SEL-R-003', basin: '扎加藏布入湖流域', elev: 4615, rain1h: 0.6, rain3h: 2.4, rainTotal: 10.8, quality: '正常', reportStatus: '准时', lastTime: '2026-06-02 14:00', interval: '1 h', source: '自动接入' },
  { id: 'SEL-R-004', name: '色林错', type: 'rain', typeLabel: '降水站', lng: 89.357022, lat: 31.843296, code: 'SEL-R-004', basin: '色林错湖区', elev: 4532, rain1h: 0.4, rain3h: 1.7, rainTotal: 8.4, quality: '正常', reportStatus: '准时', lastTime: '2026-06-02 14:00', interval: '1 h', source: '自动接入' },
  { id: 'SEL-W-001', name: '色林错湖位', type: 'water', typeLabel: '湖泊水位站', lng: 88.95, lat: 31.82, code: 'SEL-W-001', basin: '色林错湖区', elev: 4530, level: 4530.24, levelWarn: 4531.1, quality: '正常', reportStatus: '准时', lastTime: '2026-06-02 14:00', interval: '1 h', source: '卫星与岸基融合' },
  ...metadata.outlets.map((outlet, index) => ({
    id: `SEL-SEC-${String(index + 1).padStart(3, '0')}`,
    name: outlet.name,
    type: 'section',
    typeLabel: '入湖控制断面',
    lng: outlet.coordinate[0],
    lat: outlet.coordinate[1],
    code: `SEL-SEC-${String(index + 1).padStart(3, '0')}`,
    basin: outlet.name.replace('入湖口', '入湖流域'),
    elev: 4540 + index * 3,
    flow: [186, 112, 84, 69][index],
    peakForecast: [264, 171, 132, 108][index],
    peakTime: ['2026-06-03 08:00', '2026-06-03 10:00', '2026-06-03 06:00', '2026-06-03 12:00'][index],
    quality: '正常',
    reportStatus: '预报中',
    lastTime: '2026-06-02 14:00',
    interval: '1 h',
    canForecast: true
  }))
]

export const selincoSummary = {
  basinName: '色林错入湖流域群',
  targetSection: '色林错全湖',
  currentFlow: 451,
  currentLevel: 4530.24,
  peakForecast: 675,
  peakTime: '2026-06-03 10:00',
  leadHours: 48,
  reportRate: 100,
  dataStatus: '基本正常'
}

const inflowSections = selincoStations
  .filter((station) => station.type === 'section')
  .map((station) => ({
    id: station.id,
    name: station.name,
    river: station.name.replace('入湖口', ''),
    basePeak: station.peakForecast,
    currentFlow: station.flow,
    leadDefault: 24,
    warnFlow: Math.round(station.peakForecast * 1.35),
    ensureFlow: Math.round(station.peakForecast * 1.65),
    basinId: 'selinco'
  }))

export const selincoSections = [
  {
    id: 'SEL-LAKE-ALL',
    name: '色林错全湖',
    river: '四个入湖流域',
    basePeak: inflowSections.reduce((sum, section) => sum + section.basePeak, 0),
    currentFlow: inflowSections.reduce((sum, section) => sum + section.currentFlow, 0),
    leadDefault: 24,
    warnFlow: 900,
    ensureFlow: 1100,
    basinId: 'selinco',
    wholeLake: true
  },
  ...inflowSections
]
