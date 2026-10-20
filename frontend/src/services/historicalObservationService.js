import { loadJsonResource } from '@/services/projectDataService'
import { getObservationCoverage } from '@/services/observationCoverageService'
import { projectResourceUrl } from '@/services/projectResourceService'
import { resourceUrl } from '@/services/resourceUrl'

// The historical loaders predate the case catalog and keep their old public
// names for compatibility.  Resource paths themselves are resolved from the
// active case manifest whenever a source/context is supplied.
const DEFAULT_SOURCE = Object.freeze({
  id: 'nananxi-xufan',
  manifestUrl: resourceUrl('/data/projects/nananxi-xufan/manifest.json')
})
const YEAR_CACHE_LIMIT = 8
const WINDOW_CACHE_LIMIT = 6
const yearCache = new Map()
const yearRequests = new Map()
const windowCache = new Map()
const windowRequests = new Map()

function withRefresh(path, refresh = false) {
  if (!refresh) return path
  return `${path}${path.includes('?') ? '&' : '?'}refresh=${Date.now()}`
}

function touchCache(cache, key, value, limit) {
  cache.delete(key)
  cache.set(key, value)
  while (cache.size > limit) cache.delete(cache.keys().next().value)
}

function normalizeTimeKey(value) {
  return String(value || '').replace('T', ' ').slice(0, 16)
}

function sourceValue(source = {}) {
  return source?.activeContext || source?.context || source || {}
}

function sourceManifest(source = {}) {
  const value = sourceValue(source)
  return value?.manifest || value?.projectData?.manifest || null
}

function sourceManifestUrl(source = {}) {
  const value = sourceValue(source)
  return value?.manifestUrl || value?.projectData?.manifestUrl || sourceManifest(value)?.manifestUrl || null
}

function sourceId(source = {}) {
  const value = sourceValue(source)
  const manifest = sourceManifest(value)
  const explicit = value?.basinId || value?.basinProjectId || value?.projectId || value?.id || manifest?.id
  if (explicit) return String(explicit)
  const match = String(sourceManifestUrl(value) || '').match(/projects\/([^/]+)\/manifest(?:\.json)?/i)
  return match?.[1] || DEFAULT_SOURCE.id
}

function configuredObservation(source = {}) {
  const manifest = sourceManifest(source)
  return manifest?.observations || manifest?.observation || manifest?.observationResources || {}
}

function resolveSourcePath(source, configuredPath, fallbackPath) {
  const value = configuredPath || fallbackPath
  if (!value) return null
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(String(value))) return value
  if (String(value).startsWith('/data/') || String(value).startsWith('data/')) return resourceUrl(value)
  return projectResourceUrl(sourceValue(source), value, fallbackPath)
}

function observationSource(source = null) {
  const actualSource = source || DEFAULT_SOURCE
  const id = sourceId(actualSource)
  const fallbackRoot = `/data/projects/${id}/observations`
  const configured = configuredObservation(actualSource)
  const root = resolveSourcePath(actualSource, configured.root, `${fallbackRoot}`)
  const rootRelative = configured.root || './observations'
  const fallback = (name) => `${fallbackRoot}/${name}`
  const file = (key, name) => resolveSourcePath(
    actualSource,
    configured[key] || `${String(rootRelative).replace(/\/$/, '')}/${name}`,
    fallback(name)
  )
  const yearPattern = configured.yearPattern || configured.yearFilePattern || null
  return {
    id,
    key: `${id}:${root || fallbackRoot}`,
    root,
    catalog: file('catalog', 'catalog.json'),
    latestPreview: file('latestPreview', 'previews/latest-72h.json'),
    eventPackages: file('eventPackages', 'event-data-packages.json'),
    floodEvents: file('floodEvents', 'flood-events.json'),
    spatialStations: resolveSourcePath(
      actualSource,
      configured.spatialStations,
      `/data/projects/${id}/spatial/stations.json`
    ),
    rainGridPreview: file('rainGridPreview', 'previews/historical-rain-grid-72h.json'),
    yearPattern
  }
}

export function resolveObservationResource(source, key, fallbackPath = null) {
  const resolved = observationSource(source)
  if (key === 'root') return resolved.root
  if (resolved[key]) return resolved[key]
  return fallbackPath ? resolveSourcePath(source || DEFAULT_SOURCE, fallbackPath, fallbackPath) : null
}

function windowKey(sourceKey, start, end) {
  return `${sourceKey}|${normalizeTimeKey(start)}|${normalizeTimeKey(end)}`
}

/** 清除资料更新前的浏览器缓存；不影响模型输入文件。 */
export function clearNananxiObservationCache() {
  yearCache.clear()
  yearRequests.clear()
  windowCache.clear()
  windowRequests.clear()
}

function localDateFromHour(year, hour) {
  const date = new Date(year, 0, 1, 0, 0, 0, 0)
  date.setHours(date.getHours() + Number(hour))
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:00`
}

export function loadObservationCatalog({ source = null, refresh = false } = {}) {
  const resolved = observationSource(source)
  return loadJsonResource(withRefresh(resolved.catalog, refresh), `${resolved.id} 实测资料目录`)
}

export function loadObservationLatestPreview({ source = null, refresh = false } = {}) {
  const resolved = observationSource(source)
  return loadJsonResource(withRefresh(resolved.latestPreview, refresh), `${resolved.id} 实测过程预览`)
}

export function loadObservationEventPackages({ source = null, refresh = false } = {}) {
  const resolved = observationSource(source)
  return loadJsonResource(withRefresh(resolved.eventPackages, refresh), `${resolved.id} 洪水场次数据包目录`)
}

export function loadObservationFloodEvents({ source = null, refresh = false } = {}) {
  const resolved = observationSource(source)
  return loadJsonResource(withRefresh(resolved.floodEvents, refresh), `${resolved.id} 洪水场次目录`)
}

function yearResourceUrl(resolved, kind, year, catalog) {
  const yearEntry = catalog?.[kind]?.years?.find((item) => Number(item.year) === Number(year))
  if (yearEntry?.url) return yearEntry.url
  if (resolved.yearPattern) {
    const pattern = typeof resolved.yearPattern === 'object'
      ? resolved.yearPattern[kind]
      : resolved.yearPattern
    if (pattern) return String(pattern).replaceAll('{kind}', kind).replaceAll('{year}', String(year))
  }
  return `${String(resolved.root).replace(/\/$/, '')}/${kind}/${year}.json`
}

function normalizeObservationRows(kind, data, year) {
  const rows = Array.isArray(data?.rows) ? data.rows : []
  if (kind === 'rain') {
    return rows.map((row) => {
      if (!Array.isArray(row)) return {
        ...row,
        time: row.time || localDateFromHour(data?.year || year, row.hour || 0),
        stationCode: row.stationCode || row.station || row.code,
        rainMm: row.rainMm ?? row.value ?? row.precipitation
      }
      const [hour, stationIndex, rainMm] = row
      return {
        time: localDateFromHour(data?.year || year, hour),
        stationCode: data?.stationCodes?.[stationIndex] ?? stationIndex,
        rainMm
      }
    })
  }
  return rows.map((row) => {
    if (!Array.isArray(row)) return {
      ...row,
      time: row.time || localDateFromHour(data?.year || year, row.hour || 0),
      levelM: row.levelM ?? row.level ?? row.zr,
      flowM3s: row.flowM3s ?? row.flow ?? row.q
    }
    const [hour, levelM, flowM3s] = row
    return {
      time: localDateFromHour(data?.year || year, hour),
      levelM,
      flowM3s
    }
  })
}

export async function loadObservationYear(kind, year, { source = null, catalog = null, refresh = false } = {}) {
  if (!['rain', 'river'].includes(kind)) throw new Error(`不支持的实测资料类型：${kind}`)
  const resolved = observationSource(source)
  const cacheKey = `${resolved.key}:${kind}:${year}`
  if (!refresh && yearCache.has(cacheKey)) {
    const cached = yearCache.get(cacheKey)
    touchCache(yearCache, cacheKey, cached, YEAR_CACHE_LIMIT)
    return cached
  }
  if (!refresh && yearRequests.has(cacheKey)) return yearRequests.get(cacheKey)

  let request
  const yearUrl = yearResourceUrl(resolved, kind, year, catalog)
  request = loadJsonResource(withRefresh(yearUrl, refresh), `${resolved.id} ${year} 年${kind === 'rain' ? '雨量' : '水位流量'}资料`)
    .then((data) => {
      const normalized = {
        ...data,
        year: data?.year || year,
        rows: normalizeObservationRows(kind, data, year)
      }
      if (!refresh) touchCache(yearCache, cacheKey, normalized, YEAR_CACHE_LIMIT)
      return normalized
    })
    .finally(() => {
      if (yearRequests.get(cacheKey) === request) yearRequests.delete(cacheKey)
    })
  if (!refresh) yearRequests.set(cacheKey, request)
  return request
}

export async function loadObservationWindow(start, end, { source = null, refresh = false } = {}) {
  const from = new Date(start)
  const to = new Date(end)
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from > to) {
    throw new Error('实测资料时段无效')
  }
  const years = []
  for (let year = from.getFullYear(); year <= to.getFullYear(); year += 1) years.push(year)
  const sourceCatalog = await loadObservationCatalog({ source, refresh })
  const [catalog, rainYears, riverYears] = await Promise.all([
    Promise.resolve(sourceCatalog),
    Promise.all(years.map((year) => loadObservationYear('rain', year, { source, catalog: sourceCatalog, refresh }).catch(() => null))),
    Promise.all(years.map((year) => loadObservationYear('river', year, { source, catalog: sourceCatalog, refresh }).catch(() => null)))
  ])
  const inWindow = (row) => {
    const time = new Date(row.time)
    return time >= from && time <= to
  }
  return {
    catalog,
    start,
    end,
    rain: rainYears.filter(Boolean).flatMap((data) => data.rows).filter(inWindow),
    river: riverYears.filter(Boolean).flatMap((data) => data.rows).filter(inWindow)
  }
}

export function summarizeNananxiObservationWindow(windowData = {}) {
  const rainRows = Array.isArray(windowData.rain) ? windowData.rain : []
  const riverRows = Array.isArray(windowData.river) ? windowData.river : []
  const flowRows = riverRows.filter((row) => Number.isFinite(Number(row.flowM3s)))
  const peakRow = flowRows.reduce((best, row) => (!best || Number(row.flowM3s) > Number(best.flowM3s) ? row : best), null)
  const from = new Date(windowData.start)
  const to = new Date(windowData.end)
  const expectedHours = Number.isFinite(from.getTime()) && Number.isFinite(to.getTime())
    ? Math.max(1, Math.floor((to.getTime() - from.getTime()) / 3600000) + 1)
    : null
  const stationCodes = new Set(rainRows.map((row) => row.stationCode).filter(Boolean))
  const expectedRainRecords = expectedHours && stationCodes.size ? expectedHours * stationCodes.size : null
  const validRainRecords = rainRows.filter((row) => Number.isFinite(Number(row.rainMm))).length
  const rainCompleteness = expectedRainRecords ? Math.min(1, validRainRecords / expectedRainRecords) : null
  const flowCompleteness = expectedHours ? Math.min(1, flowRows.length / expectedHours) : null
  const volumeM3 = flowRows.reduce((sum, row) => sum + Number(row.flowM3s) * 3600, 0)
  return {
    rainCompleteness,
    flowCompleteness,
    riverCompleteness: flowCompleteness,
    observedPeakFlowM3s: peakRow ? Number(peakRow.flowM3s) : null,
    observedPeakTime: peakRow?.time || null,
    observedPeakLevelM: peakRow && Number.isFinite(Number(peakRow.levelM)) ? Number(peakRow.levelM) : null,
    volumeM3: flowRows.length ? volumeM3 : null,
    durationHours: expectedHours,
    rainRecords: rainRows.length,
    riverRecords: riverRows.length,
    reportingRainStations: stationCodes.size
  }
}

export async function loadObservationEventStatistics(start, end, { source = null, refresh = false } = {}) {
  const windowData = await loadObservationWindow(start, end, { source, refresh })
  return { ...summarizeNananxiObservationWindow(windowData), windowData }
}

function formatLocalHour(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:00`
}

/** 按需读取一个资料窗口，供过程回放使用，不把全部年度资料一次性装入浏览器。 */
export async function loadObservationWindowPreview(start, end, { source = null, refresh = false, catalog = null } = {}) {
  const from = new Date(start)
  const to = new Date(end)
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from > to) throw new Error('实测资料时间窗口无效')
  const resolved = observationSource(source)
  const cacheKey = windowKey(resolved.key, start, end)
  if (!refresh && windowCache.has(cacheKey)) {
    const cached = windowCache.get(cacheKey)
    touchCache(windowCache, cacheKey, cached, WINDOW_CACHE_LIMIT)
    return cached
  }
  if (!refresh && windowRequests.has(cacheKey)) return windowRequests.get(cacheKey)

  const years = []
  for (let year = from.getFullYear(); year <= to.getFullYear(); year += 1) years.push(year)
  let request
  request = Promise.all([
    catalog || loadObservationCatalog({ source, refresh }),
    Promise.all(years.map((year) => loadObservationYear('rain', year, { source, catalog, refresh }).catch(() => null))),
    Promise.all(years.map((year) => loadObservationYear('river', year, { source, catalog, refresh }).catch(() => null)))
  ]).then(([sourceCatalog, rainYears, riverYears]) => {
    const times = []
    for (let cursor = new Date(from); cursor <= to; cursor.setHours(cursor.getHours() + 1)) times.push(formatLocalHour(cursor))
    const timeIndex = new Map(times.map((time, index) => [time, index]))
    const rain = Object.fromEntries((sourceCatalog?.rain?.stations || []).map((station) => [station.legacyCode, Array(times.length).fill(null)]))
    rainYears.filter(Boolean).forEach((data) => data.rows.forEach((row) => {
      const index = timeIndex.get(row.time)
      if (index == null || !rain[row.stationCode]) return
      rain[row.stationCode][index] = row.rainMm == null ? null : Number(row.rainMm)
    }))
    const riverSource = sourceCatalog?.river?.station || sourceCatalog?.river || {}
    const river = {
      spatialCode: riverSource.spatialCode || riverSource.station?.spatialCode || null,
      levels: Array(times.length).fill(null),
      flows: Array(times.length).fill(null)
    }
    riverYears.filter(Boolean).forEach((data) => data.rows.forEach((row) => {
      const index = timeIndex.get(row.time)
      if (index == null) return
      river.levels[index] = row.levelM == null ? null : Number(row.levelM)
      river.flows[index] = row.flowM3s == null ? null : Number(row.flowM3s)
    }))
    return {
      schemaVersion: '1.0',
      source: `${resolved.id}-yearly-observed-data-on-demand`,
      status: 'observed',
      start: times[0],
      end: times[times.length - 1],
      stepHours: 1,
      times,
      rain,
      river
    }
  }).then((preview) => {
    if (!refresh) touchCache(windowCache, cacheKey, preview, WINDOW_CACHE_LIMIT)
    return preview
  }).finally(() => {
    if (windowRequests.get(cacheKey) === request) windowRequests.delete(cacheKey)
  })
  if (!refresh) windowRequests.set(cacheKey, request)
  return request
}

function formatLocalDateTime(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 以雨量和出口水文资料的共同覆盖时刻生成最新资料窗口。 */
export async function loadObservationLatestWindowPreview({ source = null, hours = 72, catalog = null, refresh = false } = {}) {
  const resolved = observationSource(source)
  const sourceCatalog = catalog || await loadObservationCatalog({ source, refresh })
  const coverage = getObservationCoverage(sourceCatalog)
  const end = coverage?.valid ? coverage.end : null
  if (!end) return loadObservationLatestPreview({ source, refresh })
  const duration = Math.max(1, Number(hours) || 72) - 1
  const requestedStart = new Date(end.getTime() - duration * 3600000)
  const commonStart = coverage?.valid ? coverage.start : null
  if (commonStart && commonStart > end) return loadObservationLatestPreview({ source, refresh })
  const start = commonStart && requestedStart < commonStart ? commonStart : requestedStart
  return loadObservationWindowPreview(formatLocalDateTime(start), formatLocalDateTime(end), {
    source,
    refresh,
    catalog: sourceCatalog
  })
}

// Compatibility API retained for existing views/stores.  Callers may pass a
// `source`, `context`, or `projectData` object to select another case; old
// calls without one continue to use the historical Nananxi fixture.
function sourceFromOptions(options = {}) {
  return options.source || options.context || options.projectData || null
}

export function loadNananxiObservationCatalog(options = {}) {
  return loadObservationCatalog({ ...options, source: sourceFromOptions(options) })
}

export function loadNananxiLatestPreview(options = {}) {
  return loadObservationLatestPreview({ ...options, source: sourceFromOptions(options) })
}

export function loadNananxiEventPackages(options = {}) {
  return loadObservationEventPackages({ ...options, source: sourceFromOptions(options) })
}

export function loadNananxiFloodEvents(options = {}) {
  return loadObservationFloodEvents({ ...options, source: sourceFromOptions(options) })
}

export function loadNananxiYear(kind, year, options = {}) {
  return loadObservationYear(kind, year, { ...options, source: sourceFromOptions(options) })
}

export function loadNananxiObservationWindow(start, end, options = {}) {
  return loadObservationWindow(start, end, { ...options, source: sourceFromOptions(options) })
}

export function loadNananxiEventStatistics(start, end, options = {}) {
  return loadObservationEventStatistics(start, end, { ...options, source: sourceFromOptions(options) })
}

export function loadNananxiWindowPreview(start, end, options = {}) {
  return loadObservationWindowPreview(start, end, { ...options, source: sourceFromOptions(options) })
}

export function loadNananxiLatestWindowPreview(options = {}) {
  return loadObservationLatestWindowPreview({ ...options, source: sourceFromOptions(options) })
}
