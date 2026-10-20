/**
 * 新安江流域基础地理与业务数据。
 * 坐标范围经简化，用于界面中的流域空间表达。
 */

export const BASIN_BOUNDS = [
  [118.55, 29.05],
  [119.55, 29.72]
]

export const BASIN_CENTER = [119.05, 29.38]
export const DEFAULT_ZOOM = 9.4

/** 流域边界多边形（简化） */
export const basinBoundary = {
  type: 'Feature',
  properties: { name: '新安江流域', area: 845 },
  geometry: {
    type: 'Polygon',
    coordinates: [[
      [118.62, 29.52], [118.72, 29.62], [118.90, 29.68], [119.12, 29.70],
      [119.32, 29.66], [119.48, 29.55], [119.52, 29.40], [119.48, 29.25],
      [119.38, 29.12], [119.18, 29.08], [118.95, 29.10], [118.75, 29.18],
      [118.62, 29.32], [118.58, 29.42], [118.62, 29.52]
    ]]
  }
}

/** 子流域（简化面） */
export const subBasins = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { id: 'B01', name: '昌溪子流域', area: 112, wetness: 0.55, contribution: 0.11 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[118.72, 29.52], [118.90, 29.62], [119.05, 29.58], [118.95, 29.48], [118.78, 29.46], [118.72, 29.52]]]
      }
    },
    {
      type: 'Feature',
      properties: { id: 'B02', name: '榉桐子流域', area: 98, wetness: 0.62, contribution: 0.24 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[119.05, 29.58], [119.22, 29.64], [119.32, 29.52], [119.18, 29.46], [119.05, 29.48], [119.05, 29.58]]]
      }
    },
    {
      type: 'Feature',
      properties: { id: 'B03', name: '兰江子流域', area: 156, wetness: 0.48, contribution: 0.15 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[118.62, 29.42], [118.78, 29.48], [118.95, 29.42], [118.90, 29.28], [118.72, 29.26], [118.62, 29.32], [118.62, 29.42]]]
      }
    },
    {
      type: 'Feature',
      properties: { id: 'B04', name: '新叶子流域', area: 88, wetness: 0.58, contribution: 0.10 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[119.12, 29.38], [119.28, 29.42], [119.38, 29.30], [119.28, 29.22], [119.12, 29.28], [119.12, 29.38]]]
      }
    },
    {
      type: 'Feature',
      properties: { id: 'B05', name: '寿昌子流域', area: 186, wetness: 0.78, contribution: 0.32 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[119.08, 29.48], [119.28, 29.52], [119.42, 29.42], [119.38, 29.30], [119.18, 29.32], [119.08, 29.40], [119.08, 29.48]]]
      }
    },
    {
      type: 'Feature',
      properties: { id: 'B06', name: '大洋子流域', area: 205, wetness: 0.42, contribution: 0.08 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[118.72, 29.28], [118.95, 29.30], [119.12, 29.22], [119.05, 29.12], [118.82, 29.12], [118.68, 29.20], [118.72, 29.28]]]
      }
    }
  ]
}

/** 河网（简化线） */
export const riverNetwork = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { name: '新安江干流', order: 1 },
      geometry: {
        type: 'LineString',
        coordinates: [
          [118.85, 29.55], [118.95, 29.48], [119.05, 29.42], [119.15, 29.36],
          [119.25, 29.32], [119.35, 29.30], [119.42, 29.28]
        ]
      }
    },
    {
      type: 'Feature',
      properties: { name: '寿昌支流', order: 2 },
      geometry: {
        type: 'LineString',
        coordinates: [[119.22, 29.48], [119.20, 29.42], [119.18, 29.36], [119.22, 29.32]]
      }
    },
    {
      type: 'Feature',
      properties: { name: '兰江支流', order: 2 },
      geometry: {
        type: 'LineString',
        coordinates: [[118.78, 29.38], [118.90, 29.36], [119.02, 29.38], [119.12, 29.36]]
      }
    }
  ]
}

/**
 * 站点：雨量站 / 水文站 / 水位站 / 控制断面
 * detail 字段用于右侧详情（与悬停浮窗字段刻意区分）
 */
export const stations = [
  {
    id: 'R-33068110',
    name: '梅城雨量站',
    type: 'rain',
    typeLabel: '雨量站',
    lng: 118.97,
    lat: 29.485,
    code: '33068110',
    basin: '梅城子流域',
    elev: 65,
    builtAt: '2008-05-12',
    source: '自动接入',
    rain1h: 12.4,
    rain3h: 34.6,
    rainTotal: 86.7,
    quality: '正常',
    reportStatus: '准时',
    lastTime: '2026-06-02 14:00',
    dataStart: '2008-06-01',
    sensor: '翻斗式雨量计',
    interval: '10 min'
  },
  {
    id: 'R-33068118',
    name: '寿昌雨量站',
    type: 'rain',
    typeLabel: '雨量站',
    lng: 119.134,
    lat: 29.357,
    code: '33068118',
    basin: '寿昌子流域',
    elev: 72,
    builtAt: '2010-03-20',
    source: '自动接入',
    rain1h: 9.6,
    rain3h: 28.4,
    rainTotal: 72.1,
    quality: '正常',
    reportStatus: '准时',
    lastTime: '2026-06-02 14:00',
    dataStart: '2010-04-01',
    sensor: '翻斗式雨量计',
    interval: '10 min'
  },
  {
    id: 'R-33068122',
    name: '深渡雨量站',
    type: 'rain',
    typeLabel: '雨量站',
    lng: 118.88,
    lat: 29.28,
    code: '33068122',
    basin: '深渡子流域',
    elev: 98,
    builtAt: '2012-08-08',
    source: '自动接入',
    rain1h: 18.2,
    rain3h: 41.8,
    rainTotal: 104.3,
    quality: '正常',
    reportStatus: '准时',
    lastTime: '2026-06-02 14:00',
    dataStart: '2012-09-01',
    sensor: '翻斗式雨量计',
    interval: '10 min'
  },
  {
    id: 'R-33068125',
    name: '建德雨量站',
    type: 'rain',
    typeLabel: '雨量站',
    lng: 119.28,
    lat: 29.25,
    code: '33068125',
    basin: '建德子流域',
    elev: 58,
    builtAt: '2009-11-15',
    source: '自动接入',
    rain1h: 7.8,
    rain3h: 22.1,
    rainTotal: 61.4,
    quality: '正常',
    reportStatus: '准时',
    lastTime: '2026-06-02 13:50',
    dataStart: '2009-12-01',
    sensor: '翻斗式雨量计',
    interval: '10 min'
  },
  {
    id: 'H-33000210',
    name: '兰江站',
    type: 'hydro',
    typeLabel: '水文站',
    lng: 118.95,
    lat: 29.36,
    code: 'ST-H-33000210',
    basin: '兰江子流域',
    elev: 52,
    builtAt: '1998-04-10',
    source: '自动接入',
    flow: 1040,
    level: 28.6,
    delta3h: 126,
    quality: '正常',
    reportStatus: '准时',
    lastTime: '2026-06-02 14:00',
    dataStart: '1998-05-01',
    sensor: '雷达水位 + ADCP',
    interval: '10 min',
    sectionId: 'SEC-LANJIANG'
  },
  {
    id: 'H-33000220',
    name: '寿昌站',
    type: 'hydro',
    typeLabel: '水文站',
    lng: 119.18,
    lat: 29.34,
    code: 'ST-H-33000220',
    basin: '寿昌子流域',
    elev: 48,
    builtAt: '2001-07-22',
    source: '自动接入',
    flow: 860,
    level: 26.2,
    delta3h: 94,
    quality: '正常',
    reportStatus: '准时',
    lastTime: '2026-06-02 14:00',
    dataStart: '2001-08-01',
    sensor: '浮子水位计',
    interval: '10 min',
    sectionId: 'SEC-SHOUCHANG'
  },
  {
    id: 'W-33000205',
    name: '新安江水位站',
    type: 'water',
    typeLabel: '水位站',
    lng: 119.22,
    lat: 29.40,
    code: 'ST-W-33000205',
    basin: '下游控制区',
    elev: 44,
    builtAt: '2005-01-18',
    source: '自动接入',
    level: 31.8,
    levelWarn: 34.5,
    quality: '正常',
    reportStatus: '准时',
    lastTime: '2026-06-02 14:00',
    dataStart: '2005-02-01',
    sensor: '压力式水位计',
    interval: '10 min'
  },
  {
    id: 'SEC-33000200',
    name: '新安江大桥',
    type: 'section',
    typeLabel: '控制断面',
    lng: 119.31,
    lat: 29.29,
    code: 'SEC-33000200',
    basin: '下游控制区',
    elev: 46,
    builtAt: '1995-09-01',
    source: '业务断面',
    flow: 1230,
    peakForecast: 2850,
    peakTime: '2026-06-03 02:00',
    controlArea: 845,
    upstreamBasins: 5,
    quality: '关注',
    reportStatus: '预报中',
    lastTime: '2026-06-02 14:00',
    dataStart: '1995-10-01',
    sensor: '断面水位—流量关系',
    interval: '1 h',
    river: '新安江',
    canForecast: true
  }
]

export const sections = [
  {
    id: 'SEC-33000200',
    name: '新安江大桥',
    river: '新安江',
    controlArea: 845,
    upstreamBasins: 5,
    flow: 1230,
    peakForecast: 2850,
    peakTime: '2026-06-03 02:00',
    leadDefault: 24
  },
  {
    id: 'SEC-LANJIANG',
    name: '兰江口',
    river: '兰江',
    controlArea: 612,
    upstreamBasins: 3,
    flow: 1040,
    peakForecast: 1980,
    peakTime: '2026-06-03 04:00',
    leadDefault: 24
  },
  {
    id: 'SEC-SHOUCHANG',
    name: '寿昌控制断面',
    river: '寿昌溪',
    controlArea: 186,
    upstreamBasins: 1,
    flow: 860,
    peakForecast: 1420,
    peakTime: '2026-06-02 22:00',
    leadDefault: 12
  }
]

/** 顶部/指标条全局摘要 */
export const basinSummary = {
  basinName: '新安江流域',
  targetSection: '新安江大桥',
  currentFlow: 1230,
  peakForecast: 2850,
  peakTime: '2026-06-03 02:00',
  leadHours: 24,
  reportRate: 92,
  normalStations: 36,
  lateStations: 1,
  missingStations: 2,
  abnormal: 1,
  lastDataTime: '14:00',
  runId: 'RUN-20260602-1400',
  paramVersion: 'XAJ-ZJ-v3.2',
  stateVersion: 'STATE-20260602-1400',
  dataStatus: '基本正常'
}

/**
 * 生成 10 分钟步长的界面时序雨量。
 * 从 08:00 到 14:00，共 37 个点
 */
export function buildRainTimeline(stationId = 'R-33068110') {
  const base = {
    'R-33068110': 1.8,
    'R-33068118': 1.4,
    'R-33068122': 2.4,
    'R-33068125': 1.1
  }[stationId] || 1.5

  const start = new Date('2026-06-02T08:00:00')
  const points = []
  let cum = 0
  for (let i = 0; i <= 36; i++) {
    const t = new Date(start.getTime() + i * 10 * 60 * 1000)
    // 中午前后加强
    const hour = t.getHours() + t.getMinutes() / 60
    const envelope = Math.max(0, Math.sin(((hour - 8) / 8) * Math.PI)) * 3.2
    const noise = Math.sin(i * 1.7 + stationId.length) * 0.6 + 0.8
    const rain = Math.max(0, +(base * envelope * noise).toFixed(1))
    cum = +(cum + rain).toFixed(1)
    points.push({
      time: formatTime(t),
      label: pad(t.getHours()) + ':' + pad(t.getMinutes()),
      rain,
      cum
    })
  }
  return points
}

/**
 * 断面过程线 — 委托参数响应引擎（兼容旧调用）
 * 完整调参见 @/models/xajParamEngine
 */
/** 分布式栅格点，用于查看产流与状态空间分布。 */
export function buildGridCells(cols = 32, rows = 22) {
  const [minLng, minLat] = BASIN_BOUNDS[0]
  const [maxLng, maxLat] = BASIN_BOUNDS[1]
  const dLng = (maxLng - minLng) / cols
  const dLat = (maxLat - minLat) / rows
  const cells = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const lng0 = minLng + c * dLng
      const lat0 = minLat + r * dLat
      const lng1 = lng0 + dLng
      const lat1 = lat0 + dLat
      const cx = (lng0 + lng1) / 2
      const cy = (lat0 + lat1) / 2
      // 寿昌一带偏湿、产流高
      const dist = Math.hypot(cx - 119.2, cy - 29.4)
      const runoff = Math.max(0, +(8 - dist * 18 + Math.sin(c * r) * 1.2).toFixed(2))
      const soil = Math.min(1, Math.max(0.2, +(0.85 - dist * 0.9 + Math.cos(c) * 0.08).toFixed(2)))
      cells.push({
        id: `G-${r}-${c}`,
        row: r,
        col: c,
        lng: cx,
        lat: cy,
        bounds: [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]],
        runoff,
        soil,
        freeWater: +(soil * 48).toFixed(1),
        surface: Math.round(runoff * 42)
      })
    }
  }
  return cells
}

/** 0.1° 数值预报降雨原始网格，用于展示降尺度前的空间分辨率。 */
export function buildQpfGridCells(hour = 0, product = 'ecmwf') {
  const [minLng, minLat] = BASIN_BOUNDS[0]
  const [maxLng, maxLat] = BASIN_BOUNDS[1]
  const step = 0.1
  const startLng = Math.floor(minLng / step) * step
  const startLat = Math.floor(minLat / step) * step
  const endLng = Math.ceil(maxLng / step) * step
  const endLat = Math.ceil(maxLat / step) * step
  const cells = []
  let row = 0
  for (let lat = startLat; lat < endLat; lat += step) {
    let col = 0
    for (let lng = startLng; lng < endLng; lng += step) {
      const cx = lng + step / 2
      const cy = lat + step / 2
      const storm = Math.exp(-Math.pow((cx - 119.18) * 3.2, 2) - Math.pow((cy - 29.42) * 4.2, 2))
      const phase = 0.8 + Math.sin((hour + 6) / 18) * 0.35
      const productFactor = product === 'cma' ? 0.86 : product === 'no-rain' ? 0 : 1
      const qpf = +(Math.max(0, (2 + storm * 22 + Math.sin(row + col * 0.7) * 2) * phase * productFactor)).toFixed(1)
      cells.push({
        id: `QPF-${row}-${col}`,
        row,
        col,
        lng: cx,
        lat: cy,
        qpf,
        bounds: [[lng, lat], [lng + step, lat], [lng + step, lat + step], [lng, lat + step], [lng, lat]]
      })
      col += 1
    }
    row += 1
  }
  return cells
}

export function stationsToGeoJSON(list = stations) {
  return {
    type: 'FeatureCollection',
    features: list.map((s) => ({
      type: 'Feature',
      properties: { ...s },
      geometry: { type: 'Point', coordinates: [s.lng, s.lat] }
    }))
  }
}

export function gridToGeoJSON(cells = buildGridCells()) {
  return {
    type: 'FeatureCollection',
    features: cells.map((cell) => ({
      type: 'Feature',
      properties: {
        id: cell.id,
        runoff: cell.runoff,
        soil: cell.soil,
        freeWater: cell.freeWater,
        surface: cell.surface,
        qpf: cell.qpf,
        wu: cell.wu,
        wl: cell.wl,
        wd: cell.wd,
        RS: cell.RS,
        RI: cell.RI,
        RG: cell.RG
      },
      geometry: {
        type: 'Polygon',
        coordinates: [cell.bounds]
      }
    }))
  }
}

function pad(n) {
  return String(n).padStart(2, '0')
}

function formatTime(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
