import { modelApi, modelBackendEnabled } from './modelBackendService.js'
import { DEFAULT_INPUT_BUNDLE_VERSION_ID, selectedInputBundleVersionId } from './inputProductService.js'

const INPUT_CASE_ID = 'nananxi-xufan'
const INPUT_RESOLUTION = '1km'

export function finiteInputValue(value) {
  if (value == null || !['number', 'string'].includes(typeof value) || (typeof value === 'string' && !value.trim())) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function modelInputSelection(source = null, resolution = null) {
  const context = source?.activeContext || source?.context || source || {}
  return {
    basinId: context.basinId || context.projectId || context.id || context.manifest?.id || INPUT_CASE_ID,
    resolution: resolution || context.modelScale || context.resolutionId || context.spatialResolutionId || INPUT_RESOLUTION,
    bundleVersionId: selectedInputBundleVersionId(source || context)
  }
}

// Use execution mode, not service readiness: a disconnected real service must
// never quietly become an offline station interpolation or fixture result.
export function usesModelInputService(source = null) {
  return modelBackendEnabled() && modelInputSelection(source).basinId === INPUT_CASE_ID
}

export function assertModelInputSelection(source = null, resolution = null) {
  const selection = modelInputSelection(source, resolution)
  if (selection.basinId !== INPUT_CASE_ID || selection.resolution !== INPUT_RESOLUTION) {
    throw new Error('当前计算资料对应南安溪 1 km，请选择该流域和尺度。')
  }
  return selection
}

function inputQuery(source, resolution, values = {}) {
  const selection = assertModelInputSelection(source, resolution)
  return new URLSearchParams({ projectId: selection.basinId, resolutionId: selection.resolution, bundleVersionId: selection.bundleVersionId, ...values })
}

export function modelInputWarmupHours(value = 168) {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value > 8760) {
    throw new Error('洪水场次预热期须为 0 到 8760 小时（最多 365 天）的整数')
  }
  return value
}

function normalizeSeries(values, length, label) {
  if (!Array.isArray(values) || values.length !== length) throw new Error(`${label}与窗口时次不一致`)
  return values.map(finiteInputValue)
}

export function normalizeModelInputCatalog(payload = {}) {
  if (payload.caseKey && payload.caseKey !== `${INPUT_CASE_ID}:${INPUT_RESOLUTION}`) throw new Error('后端资料目录不属于南安溪 1 km 案例。')
  const precipitation = payload.coverage?.precipitation || payload.rain || {}
  const flow = payload.coverage?.observedFlow || payload.river || {}
  if (!precipitation.start || !precipitation.end || !flow.start || !flow.end) {
    throw new Error('降雨与出口流量的资料覆盖范围不完整')
  }
  const events = (Array.isArray(payload.events) ? payload.events : []).map((event) => {
    if (event.basinProjectId && event.basinProjectId !== INPUT_CASE_ID) throw new Error('后端场次目录包含另一流域的场次，不能重标为南安溪。')
    return { ...event, basinProjectId: INPUT_CASE_ID, sectionId: event.sectionId || '70115800' }
  })
  return {
    ...payload,
    bundleVersionId: payload.bundleVersionId || payload.inputBundleVersionId || payload.provenance?.inputBundleVersionId || DEFAULT_INPUT_BUNDLE_VERSION_ID,
    sourceType: payload.sourceType === 'database-observed' ? 'database-observed' : 'model-qc',
    qualityNotes: Array.isArray(payload.qualityNotes) ? payload.qualityNotes.slice() : (payload.provenance?.notes || []).slice(),
    // There are no per-station QC series in the delivery. Do not substitute the
    // old MDB station catalog for the model's accepted gridded precipitation.
    rain: { ...payload.rain, ...precipitation, stations: payload.sourceType === 'database-observed' ? payload.rain?.stations || [] : [], sourceType: payload.sourceType || 'model-qc' },
    river: { ...payload.river, ...flow, sourceType: payload.sourceType || 'model-qc', station: { spatialCode: '70115800', name: '徐畈（二）' } },
    events
  }
}

export function normalizeModelInputWindow(payload = {}) {
  const rawProduct = payload.product || {}
  const rawPreview = payload.observationPreview || {}
  const historical = payload.sourceType === 'database-observed'
  const sourceType = historical ? 'database-observed' : 'model-qc'
  if (!historical && payload.sourceType !== 'model-qc' && rawProduct.sourceType !== 'model-qc') {
    throw new Error('返回的资料窗口不属于已登记的模型计算资料')
  }
  if (payload.caseKey && payload.caseKey !== `${INPUT_CASE_ID}:${INPUT_RESOLUTION}`) {
    throw new Error('返回的资料窗口不属于南安溪 1 km')
  }
  if ((rawProduct.resolution || rawProduct.resolutionId || INPUT_RESOLUTION) !== INPUT_RESOLUTION) {
    throw new Error('后端输入尺度与南安溪 1 km 模型不一致')
  }
  const times = Array.isArray(rawProduct.times) ? rawProduct.times.slice() : []
  if (!times.length || new Set(times).size !== times.length || !Array.isArray(rawProduct.cells) || (!rawProduct.cells.length && !(historical && payload.quality?.gridStatus === 'failed'))) {
    throw new Error('计算资料窗口缺少有效时次或网格')
  }
  if (times.some((time, index) => !Number.isFinite(new Date(time).getTime())
    || (index > 0 && new Date(time).getTime() - new Date(times[index - 1]).getTime() !== 3600000))) {
    throw new Error('计算资料窗口不是连续逐时资料')
  }
  if (!Array.isArray(rawPreview.times) || rawPreview.times.length !== times.length
    || rawPreview.times.some((time, index) => time !== times[index])) {
    throw new Error('后端降雨网格与出口流量的窗口时次不一致')
  }
  const ids = new Set()
  const cells = rawProduct.cells.map((cell) => {
    if (!cell.id || ids.has(cell.id) || !Array.isArray(cell.bounds) || cell.bounds.length < 4) {
      throw new Error('降雨网格缺少编号或有效几何')
    }
    ids.add(cell.id)
    return { ...cell, values: normalizeSeries(cell.values, times.length, `网格 ${cell.id}`) }
  })
  const forcingVersion = payload.forcingVersion || rawProduct.forcingVersion || payload.provenance?.forcingVersion
  const observationVersion = payload.observationVersion || rawPreview.observationVersion || payload.provenance?.observationVersion
  if (!forcingVersion || !observationVersion) throw new Error('后端未登记输入版本，不能作为真实模型资料展示')
  const preview = {
    ...rawPreview,
    sourceType,
    source: historical ? 'historical-database' : 'model-backend-qc-inputs',
    forcingVersion,
    observationVersion,
    start: times[0], end: times[times.length - 1], stepHours: 1, times,
    rain: historical ? Object.fromEntries(Object.entries(rawPreview.rain || {}).map(([key, values]) => [key, normalizeSeries(values, times.length, '站点雨量')])) : {},
    river: {
      spatialCode: '70115800',
      flows: normalizeSeries(rawPreview.river?.flows, times.length, '出口流量'),
      // The QC CSV has no level column. Missing levels are not zero levels.
      levels: historical ? normalizeSeries(rawPreview.river?.levels, times.length, '实测水位') : Array(times.length).fill(null)
    }
  }
  const bundleVersionId = payload.bundleVersionId || payload.inputBundleVersionId || payload.provenance?.inputBundleVersionId || DEFAULT_INPUT_BUNDLE_VERSION_ID
  return {
    ...payload,
    sourceType, forcingVersion, observationVersion, bundleVersionId,
    product: { ...rawProduct, sourceType: historical ? 'database-derived-grid' : 'model-qc', resolution: INPUT_RESOLUTION, forcingVersion, bundleVersionId, times, cells },
    observationPreview: { ...preview, bundleVersionId },
    summary: {
      ...payload.summary,
      arealRainfallMm: normalizeSeries(payload.summary?.arealRainfallMm, times.length, '控制区面雨量'),
      reportingStationCount: normalizeSeries(payload.summary?.reportingStationCount, times.length, '有效雨量站数')
    },
    provenance: { ...payload.provenance, inputBundleVersionId: bundleVersionId, forcingVersion, observationVersion }
  }
}

export async function loadHistoricalDatabaseCatalog({ source = null, resolution = null } = {}) {
  const query = inputQuery(source, resolution)
  query.delete('bundleVersionId')
  return normalizeModelInputCatalog(await modelApi(`database/catalog?${query}`))
}

export async function loadHistoricalDatabaseProcess(start = '', end = '', { source = null, eventId = null } = {}) {
  const query = inputQuery(source, null, eventId ? { eventId } : start && end ? { start, end } : {})
  query.delete('bundleVersionId')
  return normalizeModelInputWindow(await modelApi(`database/process?${query}`, { timeoutMs: 60000 }))
}

export async function loadModelInputCatalog({ source = null, resolution = null } = {}) {
  const query = inputQuery(source, resolution)
  const catalog = normalizeModelInputCatalog(await modelApi(`inputs/catalog?${query}`))
  if (catalog.bundleVersionId !== query.get('bundleVersionId')) throw new Error('后端资料目录与选定输入版本不一致。')
  return catalog
}

export async function loadModelInputWindow(start = '', end = '', { source = null, resolution = null } = {}) {
  if (Boolean(start) !== Boolean(end)) throw new Error('必须同时提供计算资料窗口的开始与结束时刻')
  const query = inputQuery(source, resolution, start && end ? { start, end } : {})
  const input = normalizeModelInputWindow(await modelApi(`inputs/window?${query}`, { timeoutMs: 30000 }))
  if (input.bundleVersionId !== query.get('bundleVersionId')) throw new Error('后端窗口与选定输入版本不一致。')
  return input
}

export async function loadModelInputEvent(eventId, { source = null, resolution = null, warmupHours = 168 } = {}) {
  if (!eventId) throw new Error('请选择已登记洪水场次')
  const query = inputQuery(source, resolution, { warmupHours: modelInputWarmupHours(warmupHours) })
  const input = normalizeModelInputWindow(await modelApi(`inputs/events/${encodeURIComponent(eventId)}?${query}`, { timeoutMs: 30000 }))
  if (input.bundleVersionId !== query.get('bundleVersionId')) throw new Error('后端场次与选定输入版本不一致。')
  return input
}

export function sliceModelInputWindow(input, start, end) {
  const from = new Date(start).getTime()
  const to = new Date(end).getTime()
  const indexes = input.product.times.map((time, index) => ({ index, timestamp: new Date(time).getTime() }))
    .filter((row) => row.timestamp >= from && row.timestamp <= to).map((row) => row.index)
  if (!indexes.length) throw new Error('场次时段不在返回的计算资料窗口内')
  const select = (series) => indexes.map((index) => series[index])
  const times = select(input.product.times)
  return normalizeModelInputWindow({
    ...input,
    product: { ...input.product, start: times[0], end: times.at(-1), times, cells: input.product.cells.map((cell) => ({ ...cell, values: select(cell.values) })) },
    observationPreview: { ...input.observationPreview, times,
      rain: Object.fromEntries(Object.entries(input.observationPreview.rain || {}).map(([key, series]) => [key, select(series)])),
      river: { ...input.observationPreview.river, flows: select(input.observationPreview.river.flows), levels: select(input.observationPreview.river.levels) } },
    summary: {
      ...input.summary, windowHours: times.length,
      arealRainfallMm: select(input.summary.arealRainfallMm), reportingStationCount: select(input.summary.reportingStationCount),
      ...(Array.isArray(input.summary.validGridFraction) ? { validGridFraction: select(input.summary.validGridFraction) } : {})
    }
  })
}
