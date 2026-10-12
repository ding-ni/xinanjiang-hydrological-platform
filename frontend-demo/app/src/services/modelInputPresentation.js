import { finiteInputValue, sliceModelInputWindow } from './modelInputService.js'
import { DEFAULT_INPUT_BUNDLE_VERSION_ID } from './inputProductService.js'

// Registration, connection and a loaded window are different facts. In
// particular, a static manifest must not keep a live-looking badge offline.
export function modelInputDisplayStatus({ resolution = '1km', registered = false, loaded = false, loading = false, backendError = '', inputError = '', bundleVersionId = DEFAULT_INPUT_BUNDLE_VERSION_ID } = {}) {
  const source = bundleVersionId === DEFAULT_INPUT_BUNDLE_VERSION_ID ? '整编资料' : '计算资料'
  if (resolution !== '1km') return { label: '空间资料浏览', status: 'spatial-only' }
  if (backendError) return { label: `${source}服务离线`, status: 'spatial-only' }
  if (loading) return { label: '正在查询时段', status: 'input-loading' }
  if (inputError) return { label: '时段查询失败', status: 'spatial-only' }
  if (registered && loaded) return { label: `${source}可用`, status: 'qc-loaded' }
  if (registered) return { label: `${source}已登记`, status: 'qc-registered' }
  return { label: '请选择计算资料', status: 'spatial-only' }
}

// Presentation is a projection of server-owned data, not another forcing model.
export function observedModelProcess(input, start, end) {
  const window = sliceModelInputWindow(input, start, end)
  const times = window.product.times
  const obs = times.map((time, index) => ({ time, timestamp: time, value: window.observationPreview.river.flows[index] }))
  return {
    hydrograph: { times, forecast: [], obs, prev: [], metrics: null, peak: null, peakTime: null },
    forcing: {
      times, precipitation: window.summary.arealRainfallMm, temperature: [], components: {},
      sourceLabel: `整编降雨 · 控制区面积加权 · ${window.forcingVersion}`,
      forcingVersion: window.forcingVersion, observationVersion: window.observationVersion,
      controlAreaKm2: window.summary.controlAreaKm2
    },
    summary: window.summary, provenance: window.provenance, quality: input.quality,
    sourceType: 'model-qc'
  }
}

export function modelEventStatistics(input, start, end) {
  const process = observedModelProcess(input, start, end)
  const rows = process.hydrograph.obs
  const valid = rows.filter((row) => finiteInputValue(row.value) != null)
  const peak = valid.reduce((best, row) => !best || row.value > best.value ? row : best, null)
  const rain = process.forcing.precipitation
  // Incomplete windows have no asserted whole-event rain/volume total.
  return {
    observedPeakFlowM3s: peak?.value ?? null, observedPeakTime: peak?.time ?? null,
    volumeM3: valid.length === rows.length ? valid.reduce((sum, row) => sum + row.value * 3600, 0) : null,
    arealRainfallTotalMm: rain.every((value) => value != null) ? rain.reduce((sum, value) => sum + value, 0) : null,
    rainCompleteness: rain.filter((value) => value != null).length / rain.length,
    flowCompleteness: valid.length / rows.length, durationHours: rows.length,
    controlAreaKm2: process.summary.controlAreaKm2, sourceType: 'model-qc'
  }
}
