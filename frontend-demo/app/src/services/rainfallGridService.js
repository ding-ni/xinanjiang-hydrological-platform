import { loadJsonResource } from '@/services/projectDataService'
import {
  loadNananxiObservationCatalog,
  loadNananxiWindowPreview,
  resolveObservationResource
} from '@/services/historicalObservationService'
import { interpolateRainfallAt } from '@/services/rainfallInterpolationService'
import { loadModelInputWindow, usesModelInputService } from './modelInputService.js'

const DEFAULT_PREVIEW_PATH = '/data/projects/nananxi-xufan/observations/previews/historical-rain-grid-72h.json'
const DEFAULT_STATIONS_PATH = '/data/projects/nananxi-xufan/spatial/stations.json'

function normalizeProduct(product, resolution) {
  if (!product) return null
  return {
    ...product,
    resolution,
    times: Array.isArray(product.times) ? product.times : [],
    cells: Array.isArray(product.cells) ? product.cells : [],
    displayOnly: true,
    sourceType: product.source === 'delivery-package-netcdf-preview' ? 'preprocessed-netcdf' : 'static-preview'
  }
}

function sameWindow(product, start, end) {
  if (!start || !end || !product?.times?.length) return false
  const normalize = (value) => String(value).replace('T', ' ').slice(0, 16)
  return normalize(product.times[0]) === normalize(start) && normalize(product.times[product.times.length - 1]) === normalize(end)
}

function cellCenter(bounds = []) {
  const first = bounds[0]
  const opposite = bounds[2] || bounds[1]
  if (!first || !opposite) return null
  return [(Number(first[0]) + Number(opposite[0])) / 2, (Number(first[1]) + Number(opposite[1])) / 2]
}

async function buildObservedWindowProduct({ source = null, baseProduct, resolution, start, end, refresh = false, catalog = null }) {
  if (!baseProduct || sameWindow(baseProduct, start, end)) return baseProduct
  const [windowData, sourceCatalog, spatial] = await Promise.all([
    loadNananxiWindowPreview(start, end, { source, refresh, catalog }),
    catalog || loadNananxiObservationCatalog({ source, refresh }),
    loadJsonResource(resolveObservationResource(source, 'spatialStations', DEFAULT_STATIONS_PATH), '案例站点空间资料')
  ])
  const spatialStations = spatial?.stations || spatial || []
  const coordinates = new Map(spatialStations.map((station) => [String(station.id || station.code), station]))
  const stationMeta = (sourceCatalog?.rain?.stations || []).map((station) => {
    const location = coordinates.get(String(station.spatialCode))
    return {
      lng: location?.lng,
      lat: location?.lat,
      legacyCode: station.legacyCode,
      values: windowData.rain?.[station.legacyCode]
    }
  }).filter((station) => Number.isFinite(Number(station.lng)) && Number.isFinite(Number(station.lat)))
  const cells = baseProduct.cells.map((cell) => {
    const center = cellCenter(cell.bounds)
    const values = windowData.times.map((_, index) => {
      if (!center) return null
      const stations = stationMeta
        .map((station) => ({ lng: station.lng, lat: station.lat, rain1h: station.values?.[index] }))
        .filter((station) => station.rain1h != null)
      const value = interpolateRainfallAt(center[0], center[1], stations, 2)
      return value == null ? null : +Number(value).toFixed(3)
    })
    return { ...cell, values }
  })
  return {
    ...baseProduct,
    resolution,
    source: 'observed-station-window-idw-preview',
    sourceType: 'observed-window-derived',
    times: windowData.times,
    cells
  }
}

/**
 * Read a gridded observation window from the active case's bundled static
 * resources.  The frontend demo deliberately has no backend dependency.
 */
export async function loadRainfallGrid({ source = null, resolution = '1km', start = '', end = '', refresh = false, catalog = null } = {}) {
  if (usesModelInputService(source)) {
    // Failure or an unsupported scale must propagate; never replace registered
    // model inputs with the old static preview or browser-side station IDW.
    return (await loadModelInputWindow(start, end, { source, resolution })).product
  }
  const normalizedResolution = resolution === '0p1deg' ? '0p1deg' : '1km'
  const previewUrl = resolveObservationResource(source, 'rainGridPreview', DEFAULT_PREVIEW_PATH)
  const preview = await loadJsonResource(
    refresh ? `${previewUrl}${previewUrl.includes('?') ? '&' : '?'}refresh=${Date.now()}` : previewUrl,
    '案例实测降雨栅格预览'
  )
  const product = normalizeProduct(
    preview?.products?.[normalizedResolution] || preview?.products?.[resolution] || preview?.product || preview,
    normalizedResolution
  )
  if (!product) throw new Error(`没有 ${normalizedResolution} 降雨栅格预览`)
  return buildObservedWindowProduct({ source, baseProduct: product, resolution: normalizedResolution, start, end, refresh, catalog })
}

export function loadNananxiRainfallGrid(options = {}) {
  const source = options.source || options.context || options.projectData || null
  return loadRainfallGrid({ ...options, source })
}

export function gridCellsAt(product, timeIndex = 0) {
  if (!product?.cells?.length) return []
  const index = Math.max(0, Math.min(Number(timeIndex) || 0, (product.times?.length || 1) - 1))
  return product.cells.map((cell) => ({
    id: cell.id,
    row: cell.row,
    col: cell.col,
    bounds: cell.bounds,
    areaKm2: cell.areaKm2,
    controlAreaKm2: cell.controlAreaKm2,
    qpf: cell.values?.[index] == null ? null : Number(cell.values[index])
  }))
}

export function gridCellsAtTime(product, time, fallbackIndex = 0) {
  if (!product?.times?.length || !time) return ['model-qc', 'database-derived-grid'].includes(product?.sourceType) ? [] : gridCellsAt(product, fallbackIndex)
  const target = String(time).replace('T', ' ').slice(0, 16)
  const index = product.times.findIndex((item) => String(item).replace('T', ' ').slice(0, 16) === target)
  if (index < 0 && ['model-qc', 'database-derived-grid'].includes(product.sourceType)) return []
  return gridCellsAt(product, index >= 0 ? index : fallbackIndex)
}
