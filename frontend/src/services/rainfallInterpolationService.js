import { colorForRainfall } from './gridRainfallDisplayService.js'
const DEFAULT_POWER = 2
const DEFAULT_SIZE = 256

function finite(value) {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function validStations(stations = []) {
  return stations
    .map((station) => ({
      lng: finite(station.lng),
      lat: finite(station.lat),
      type: station.type,
      value: finite(station.rain1h),
      ...projectRainfallPoint(finite(station.lng), finite(station.lat))
    }))
    .filter((station) => station.lng != null && station.lat != null && station.value != null && station.value >= 0 && Number.isFinite(station.x + station.y) && (!station.type || station.type === 'rain'))
}

// WGS84 / UTM 50N, the same metric projection as the local rainfall preparation.
export function projectRainfallPoint(lng, lat) {
  if (lng == null || lat == null || !Number.isFinite(lng + lat) || Math.abs(lat) >= 90) return { x: NaN, y: NaN }
  const a = 6378137, e2 = 0.0066943799901413165, ep2 = e2 / (1 - e2), k = 0.9996
  const phi = lat * Math.PI / 180, lambda = (lng - 117) * Math.PI / 180
  const sin = Math.sin(phi), cos = Math.cos(phi), tan = Math.tan(phi)
  const n = a / Math.sqrt(1 - e2 * sin * sin), t = tan * tan, c = ep2 * cos * cos, v = cos * lambda
  const m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
    - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * Math.sin(2 * phi)
    + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * Math.sin(4 * phi)
    - 35 * e2 ** 3 / 3072 * Math.sin(6 * phi))
  return { x: 500000 + k * n * (v + (1 - t + c) * v ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * v ** 5 / 120),
    y: k * (m + n * tan * (v * v / 2 + (5 - t + 9 * c + 4 * c * c) * v ** 4 / 24 + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * v ** 6 / 720)) }
}

function ringCoordinates(geometry) {
  if (!geometry) return []
  if (geometry.type === 'Polygon') return geometry.coordinates || []
  if (geometry.type === 'MultiPolygon') return (geometry.coordinates || []).flat()
  return []
}

function pointInRing(point, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i] || []
    const [xj, yj] = ring[j] || []
    if (!Number.isFinite(xi) || !Number.isFinite(yi) || !Number.isFinite(xj) || !Number.isFinite(yj)) continue
    const intersects = ((yi > point[1]) !== (yj > point[1]))
      && point[0] < ((xj - xi) * (point[1] - yi)) / ((yj - yi) || Number.EPSILON) + xi
    if (intersects) inside = !inside
  }
  return inside
}

export function pointInGeoJSON(point, geojson) {
  const features = geojson?.type === 'FeatureCollection'
    ? geojson.features || []
    : geojson?.type === 'Feature'
      ? [geojson]
      : [{ geometry: geojson }]
  return features.some((feature) => {
    const geometry = feature?.geometry
    const polygons = geometry?.type === 'MultiPolygon' ? geometry.coordinates : geometry?.type === 'Polygon' ? [geometry.coordinates] : []
    return polygons.some(rings => rings.length && pointInRing(point, rings[0]) && !rings.slice(1).some(hole => pointInRing(point, hole)))
  })
}

export function geoJSONBounds(geojson) {
  const coordinates = []
  const visit = (value) => {
    if (!Array.isArray(value)) return
    if (value.length >= 2 && Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))) {
      coordinates.push([Number(value[0]), Number(value[1])])
      return
    }
    value.forEach(visit)
  }
  const features = geojson?.type === 'FeatureCollection' ? geojson.features || [] : [geojson]
  features.forEach((feature) => visit(feature?.geometry?.coordinates || feature?.coordinates))
  if (!coordinates.length) return null
  const lngs = coordinates.map(([lng]) => lng)
  const lats = coordinates.map(([, lat]) => lat)
  return [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]]
}

export function interpolateRainfallAt(lng, lat, stations = [], power = DEFAULT_POWER) {
  return interpolatePreparedRainfallAt(lng, lat, validStations(stations), power)
}

function interpolatePreparedRainfallAt(lng, lat, valid, power = DEFAULT_POWER) {
  if (!valid.length) return null
  const point = projectRainfallPoint(lng, lat)
  if (!Number.isFinite(point.x + point.y)) return null

  let weighted = 0
  let totalWeight = 0
  for (const station of valid) {
    const distance = Math.hypot(point.x - station.x, point.y - station.y)
    if (distance < 0.001) return station.value
    const weight = 1 / Math.pow(distance, power)
    weighted += station.value * weight
    totalWeight += weight
  }
  return totalWeight ? weighted / totalWeight : null
}

export function stationRainfallGrid(cells = [], stations = []) {
  const valid = validStations(stations)
  return cells.map(cell => {
    const bounds = cell.bounds || []
    if (!bounds.length) return { ...cell, qpf: null }
    const lng = (Math.min(...bounds.map(point => point[0])) + Math.max(...bounds.map(point => point[0]))) / 2
    const lat = (Math.min(...bounds.map(point => point[1])) + Math.max(...bounds.map(point => point[1]))) / 2
    return { ...cell, qpf: interpolatePreparedRainfallAt(lng, lat, valid) }
  })
}

export function buildSmoothRainfallDataURL({ stations = [], basin, bounds = geoJSONBounds(basin), size = DEFAULT_SIZE, power = DEFAULT_POWER } = {}) {
  if (typeof document === 'undefined' || !bounds) return null
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const context = canvas.getContext('2d')
  if (!context) return null
  const valid = validStations(stations)
  if (!valid.length) return null
  const image = context.createImageData(size, size)
  const [min, max] = bounds
  const lngSpan = Math.max(Number.EPSILON, max[0] - min[0])
  const mercator = lat => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360))
  const north = mercator(max[1]), south = mercator(min[1])
  const latSpan = Math.max(Number.EPSILON, north - south)
  const path = typeof context.beginPath === 'function' ? context : null
  if (path) {
    context.beginPath()
    const features = basin?.type === 'FeatureCollection' ? basin.features || [] : basin?.type === 'Feature' ? [basin] : [{ geometry: basin }]
    features.forEach((feature) => ringCoordinates(feature?.geometry).forEach((ring) => {
      ring.forEach(([lng, lat], index) => {
        const x = ((lng - min[0]) / lngSpan) * size
        const y = ((north - mercator(lat)) / latSpan) * size
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      context.closePath()
    }))
  }

  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      const lng = min[0] + ((col + 0.5) / size) * lngSpan
      const lat = (2 * Math.atan(Math.exp(north - ((row + 0.5) / size) * latSpan)) - Math.PI / 2) * 180 / Math.PI
      const offset = (row * size + col) * 4
      if (path ? !context.isPointInPath(col + 0.5, row + 0.5, 'evenodd') : !pointInGeoJSON([lng, lat], basin)) continue
      const color = colorForRainfall(interpolatePreparedRainfallAt(lng, lat, valid, power))
      image.data[offset] = color[0]
      image.data[offset + 1] = color[1]
      image.data[offset + 2] = color[2]
      image.data[offset + 3] = color[3]
    }
  }
  context.putImageData(image, 0, 0)
  return canvas.toDataURL('image/png')
}

export const rainfallInterpolationDefaults = Object.freeze({ power: DEFAULT_POWER, size: DEFAULT_SIZE })
