// Display the registered grid rainfall as a continuous field. Nothing in this
// module creates model forcing or fills a missing rainfall observation.
const DEFAULT_SIZE = 192
const MAX_SIZE = 192
const RAIN_COLORS = [
  [0, [255, 255, 255, 0]],
  [1, [190, 222, 252, 0.34]],
  [5, [88, 166, 232, 0.50]],
  [10, [38, 119, 196, 0.56]],
  [25, [240, 201, 64, 0.62]],
  [50, [238, 132, 39, 0.68]],
  [80, [211, 64, 51, 0.74]]
]

function finiteNumber(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null
  if (typeof value === 'string' && !value.trim()) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function rainfallValue(value) {
  const number = finiteNumber(value)
  return number != null && number >= 0 ? number : null
}

export function hasGridRainfall(cells = []) {
  return Array.isArray(cells) && cells.some((cell) => rainfallValue(cell?.qpf) != null)
}

export function gridRainfallDisplaySignature(cells = []) {
  // Keep full precision. A changed polygon or a small rainfall change must not
  // reuse an image from the preceding input window.
  return JSON.stringify((Array.isArray(cells) ? cells : []).map((cell) => [
    cell?.id ?? null, cell?.row ?? null, cell?.col ?? null,
    cell?.bounds ?? null, cell?.qpf ?? null
  ]))
}

export function gridRainfallColorExpression() {
  return [
    'interpolate', ['linear'], ['coalesce', ['get', 'qpf'], 0],
    ...RAIN_COLORS.flatMap(([value, color]) => [value, `rgba(${color.join(',')})`])
  ]
}

function prepareRectangle(cell) {
  if (!Array.isArray(cell?.bounds) || cell.bounds.length < 4) return null
  const points = cell.bounds.map((point) => [finiteNumber(point?.[0]), finiteNumber(point?.[1])])
  if (points.some((point) => point.some((value) => value == null))) return null
  const minLng = Math.min(...points.map(([lng]) => lng))
  const maxLng = Math.max(...points.map(([lng]) => lng))
  const minLat = Math.min(...points.map(([, lat]) => lat))
  const maxLat = Math.max(...points.map(([, lat]) => lat))
  const width = maxLng - minLng
  const height = maxLat - minLat
  if (!(width > 0 && height > 0)) return null
  const tolerance = Math.max(width, height) * 1e-7
  const corners = new Set()
  for (const [lng, lat] of points) {
    const x = Math.abs(lng - minLng) <= tolerance ? 0 : Math.abs(lng - maxLng) <= tolerance ? 1 : null
    const y = Math.abs(lat - minLat) <= tolerance ? 0 : Math.abs(lat - maxLat) <= tolerance ? 1 : null
    if (x == null || y == null) return null
    corners.add(`${x}:${y}`)
  }
  if (corners.size !== 4) return null
  return {
    minLng, maxLng, minLat, maxLat, width, height,
    lng: (minLng + maxLng) / 2, lat: (minLat + maxLat) / 2,
    value: rainfallValue(cell.qpf)
  }
}

function regularLattice(rectangles) {
  if (!rectangles.length) return null
  const width = rectangles[0].width
  const height = rectangles[0].height
  const minLng = Math.min(...rectangles.map((cell) => cell.minLng))
  const minLat = Math.min(...rectangles.map((cell) => cell.minLat))
  const tolerance = Math.max(width, height) * 1e-7
  const cells = new Map()
  for (const cell of rectangles) {
    const col = Math.round((cell.minLng - minLng) / width)
    const row = Math.round((cell.minLat - minLat) / height)
    if (Math.abs(cell.width - width) > tolerance || Math.abs(cell.height - height) > tolerance
      || Math.abs(cell.minLng - (minLng + col * width)) > tolerance
      || Math.abs(cell.minLat - (minLat + row * height)) > tolerance
      || cells.has(`${col}:${row}`)) return null
    cells.set(`${col}:${row}`, { ...cell, col, row })
  }
  // Each quadrant has at most four contributing centres. A diagonal neighbour
  // must have a valid edge-connected path, so two missing neighbours cannot
  // connect otherwise separate parts of the rainfall field.
  for (const cell of cells.values()) {
    cell.quadrants = new Map()
    for (const dx of [-1, 1]) {
      for (const dy of [-1, 1]) {
        const horizontal = cells.get(`${cell.col + dx}:${cell.row}`)
        const vertical = cells.get(`${cell.col}:${cell.row + dy}`)
        const diagonal = cells.get(`${cell.col + dx}:${cell.row + dy}`)
        cell.quadrants.set(`${dx}:${dy}`, [
          cell, horizontal, vertical,
          horizontal?.value != null || vertical?.value != null ? diagonal : null
        ])
      }
    }
  }
  return { cells, minLng, minLat, width, height }
}

export function createGridRainfallSampler(cells = []) {
  const rectangles = (Array.isArray(cells) ? cells : []).map(prepareRectangle).filter(Boolean)
  const lattice = regularLattice(rectangles)
  return (lng, lat) => {
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null
    // Never extrapolate into an absent or missing containing cell. Geometry,
    // not row/col from another data array, determines the containing cell.
    if (!lattice) {
      // Unregistered irregular geometry can still display its own value;
      // smoothing is reserved for a verified regular grid.
      const owner = rectangles.find((cell) => lng >= cell.minLng && lng < cell.maxLng
        && lat >= cell.minLat && lat < cell.maxLat)
      return owner?.value ?? null
    }
    const col = Math.floor((lng - lattice.minLng) / lattice.width)
    const row = Math.floor((lat - lattice.minLat) / lattice.height)
    const owner = lattice.cells.get(`${col}:${row}`)
    if (owner?.value == null) return null
    const offsetX = (lng - owner.lng) / lattice.width
    const offsetY = (lat - owner.lat) / lattice.height
    const x = Math.min(1, Math.abs(offsetX))
    const y = Math.min(1, Math.abs(offsetY))
    const neighbours = owner.quadrants.get(`${offsetX < 0 ? -1 : 1}:${offsetY < 0 ? -1 : 1}`)
    const weights = [(1 - x) * (1 - y), x * (1 - y), (1 - x) * y, x * y]
    let weighted = 0
    let totalWeight = 0
    let minValue = owner.value
    let maxValue = owner.value
    neighbours.forEach((cell, index) => {
      if (cell?.value == null || weights[index] <= 0) return
      weighted += cell.value * weights[index]
      totalWeight += weights[index]
      minValue = Math.min(minValue, cell.value)
      maxValue = Math.max(maxValue, cell.value)
    })
    return totalWeight > 0 ? Math.max(minValue, Math.min(maxValue, weighted / totalWeight)) : null
  }
}

function polygonParts(geojson) {
  if (!geojson) return []
  if (geojson.type === 'FeatureCollection') return (geojson.features || []).flatMap(polygonParts)
  if (geojson.type === 'Feature') return polygonParts(geojson.geometry)
  if (geojson.type === 'GeometryCollection') return (geojson.geometries || []).flatMap(polygonParts)
  const polygons = geojson.type === 'Polygon' ? [geojson.coordinates]
    : geojson.type === 'MultiPolygon' ? geojson.coordinates : []
  return (polygons || []).map((polygon) => (polygon || []).map((ring) => (ring || []).map((point) => [
    finiteNumber(point?.[0]), finiteNumber(point?.[1])
  ]))).filter((polygon) => polygon.length && polygon.every((ring) => ring.length >= 4
    && ring.every((point) => point.every((value) => value != null))))
}

function pointInRing(lng, lat, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > lat) !== (yj > lat) && lng < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function pointInPolygons(lng, lat, polygons) {
  return polygons.some((rings) => pointInRing(lng, lat, rings[0])
    && !rings.slice(1).some((hole) => pointInRing(lng, lat, hole)))
}

function polygonBounds(polygons) {
  const points = polygons.flat(2)
  if (!points.length) return null
  return [
    [Math.min(...points.map(([lng]) => lng)), Math.min(...points.map(([, lat]) => lat))],
    [Math.max(...points.map(([lng]) => lng)), Math.max(...points.map(([, lat]) => lat))]
  ]
}

export function colorForRainfall(value) {
  if (value == null) return [0, 0, 0, 0]
  const last = RAIN_COLORS.at(-1)
  if (value >= last[0]) return [...last[1].slice(0, 3), Math.round(last[1][3] * 255)]
  for (let index = 1; index < RAIN_COLORS.length; index += 1) {
    const [rightValue, right] = RAIN_COLORS[index]
    if (value > rightValue) continue
    const [leftValue, left] = RAIN_COLORS[index - 1]
    const ratio = (value - leftValue) / (rightValue - leftValue)
    return left.map((channel, channelIndex) => Math.round(
      (channel + (right[channelIndex] - channel) * ratio) * (channelIndex === 3 ? 255 : 1)
    ))
  }
  return [0, 0, 0, 0]
}

function drawBasinMask(context, polygons, bounds, size) {
  if (typeof context.beginPath !== 'function' || typeof context.fill !== 'function'
    || typeof context.getImageData !== 'function') return null
  const [min, max] = bounds
  const lngSpan = max[0] - min[0]
  const latSpan = max[1] - min[1]
  context.fillStyle = '#ffffff'
  polygons.forEach((rings) => {
    context.beginPath()
    rings.forEach((ring) => {
      ring.forEach(([lng, lat], index) => {
        const x = (lng - min[0]) / lngSpan * size
        const y = (max[1] - lat) / latSpan * size
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      context.closePath()
    })
    // Fill polygons separately: FeatureCollection and MultiPolygon components
    // form a union, while each polygon retains its own holes.
    context.fill('evenodd')
  })
  return context.getImageData(0, 0, size, size).data
}

export function buildSmoothGridRainfallDataURL({ cells = [], basin, bounds = null, size = DEFAULT_SIZE } = {}) {
  if (typeof document === 'undefined' || !hasGridRainfall(cells)) return null
  const polygons = polygonParts(basin)
  if (!polygons.length) return null
  const area = bounds || polygonBounds(polygons)
  if (!Array.isArray(area) || area.length !== 2
    || area.some((point) => !Array.isArray(point) || point.length < 2
      || point.slice(0, 2).some((value) => !Number.isFinite(value)))
    || !(area[1][0] > area[0][0] && area[1][1] > area[0][1])) return null
  const requestedSize = finiteNumber(size)
  const pixels = requestedSize != null && requestedSize > 0
    ? Math.max(1, Math.min(MAX_SIZE, Math.floor(requestedSize))) : DEFAULT_SIZE
  const canvas = document.createElement('canvas')
  canvas.width = pixels
  canvas.height = pixels
  const context = canvas.getContext('2d')
  if (!context) return null
  const sample = createGridRainfallSampler(cells)
  const mask = drawBasinMask(context, polygons, area, pixels)
  const image = context.createImageData(pixels, pixels)
  const [min, max] = area
  for (let row = 0; row < pixels; row += 1) {
    for (let col = 0; col < pixels; col += 1) {
      const offset = (row * pixels + col) * 4
      const lng = min[0] + (col + 0.5) / pixels * (max[0] - min[0])
      const lat = max[1] - (row + 0.5) / pixels * (max[1] - min[1])
      if (mask ? mask[offset + 3] === 0 : !pointInPolygons(lng, lat, polygons)) continue
      const value = sample(lng, lat)
      if (value == null) continue
      const color = colorForRainfall(value)
      image.data[offset] = color[0]
      image.data[offset + 1] = color[1]
      image.data[offset + 2] = color[2]
      image.data[offset + 3] = mask ? Math.round(color[3] * mask[offset + 3] / 255) : color[3]
    }
  }
  context.putImageData(image, 0, 0)
  return canvas.toDataURL('image/png')
}
