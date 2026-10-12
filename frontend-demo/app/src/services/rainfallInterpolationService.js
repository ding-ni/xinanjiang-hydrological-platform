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
      value: finite(station.rain1h)
    }))
    .filter((station) => station.lng != null && station.lat != null && station.value != null)
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
    const rings = ringCoordinates(feature?.geometry)
    if (!rings.length || !pointInRing(point, rings[0])) return false
    return !rings.slice(1).some((hole) => pointInRing(point, hole))
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

  let weighted = 0
  let totalWeight = 0
  for (const station of valid) {
    const distance = Math.hypot((lng - station.lng) * Math.cos((lat * Math.PI) / 180), lat - station.lat)
    if (distance < 1e-7) return station.value
    const weight = 1 / Math.pow(distance, power)
    weighted += station.value * weight
    totalWeight += weight
  }
  return totalWeight ? weighted / totalWeight : null
}

function colorForRain(value, maxValue) {
  if (value == null) return [0, 0, 0, 0]
  const scale = Math.max(1, maxValue || 0, value)
  const stops = [
    [0, [222, 239, 255]],
    [1, [158, 208, 255]],
    [5, [90, 174, 240]],
    [10, [41, 128, 204]],
    [25, [240, 211, 90]],
    [50, [242, 160, 58]],
    [80, [226, 75, 59]]
  ]
  const bounded = Math.min(120, value)
  let left = stops[0]
  let right = stops[stops.length - 1]
  for (let i = 1; i < stops.length; i += 1) {
    if (bounded <= stops[i][0]) {
      right = stops[i]
      left = stops[i - 1]
      break
    }
  }
  const ratio = right[0] === left[0] ? 0 : (bounded - left[0]) / (right[0] - left[0])
  return [
    Math.round(left[1][0] + (right[1][0] - left[1][0]) * ratio),
    Math.round(left[1][1] + (right[1][1] - left[1][1]) * ratio),
    Math.round(left[1][2] + (right[1][2] - left[1][2]) * ratio),
    Math.round(70 + Math.min(165, 90 + (value / scale) * 75))
  ]
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
  const maxValue = Math.max(...valid.map((station) => Number(station.rain1h)))
  const image = context.createImageData(size, size)
  const [min, max] = bounds
  const lngSpan = Math.max(Number.EPSILON, max[0] - min[0])
  const latSpan = Math.max(Number.EPSILON, max[1] - min[1])
  const path = typeof context.beginPath === 'function' ? context : null
  if (path) {
    context.beginPath()
    const features = basin?.type === 'FeatureCollection' ? basin.features || [] : basin?.type === 'Feature' ? [basin] : [{ geometry: basin }]
    features.forEach((feature) => ringCoordinates(feature?.geometry).forEach((ring) => {
      ring.forEach(([lng, lat], index) => {
        const x = ((lng - min[0]) / lngSpan) * size
        const y = ((max[1] - lat) / latSpan) * size
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      context.closePath()
    }))
  }

  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      const lng = min[0] + ((col + 0.5) / size) * lngSpan
      const lat = max[1] - ((row + 0.5) / size) * latSpan
      const offset = (row * size + col) * 4
      if (path ? !context.isPointInPath(col + 0.5, row + 0.5, 'evenodd') : !pointInGeoJSON([lng, lat], basin)) continue
      const color = colorForRain(interpolatePreparedRainfallAt(lng, lat, valid, power), maxValue)
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
