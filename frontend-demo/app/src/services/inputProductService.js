import { modelApi, uniqueRequestId } from './modelBackendService.js'

export const DEFAULT_INPUT_BUNDLE_VERSION_ID = 'NANANXI_QC_20260819_CONTROL_V2'
export const INPUT_TASK_ACTIVE_STATUSES = ['queued', 'running', 'cancelling']

// Live workspace selection is deliberately separate from handoff/result lineage.
export function selectedInputBundleVersionId(source = {}) {
  return source.selectedInputBundleVersionId || source.workContext?.selectedInputBundleVersionId
    || source.activeContext?.selectedInputBundleVersionId || DEFAULT_INPUT_BUNDLE_VERSION_ID
}

export function frozenInputBundleVersionId(record = {}) {
  return record.inputs?.bundleVersionId || record.lineage?.inputBundleVersionId
    || record.inputBundleVersionId || DEFAULT_INPUT_BUNDLE_VERSION_ID
}

export function inputResultUsesSelection(record, source) {
  return frozenInputBundleVersionId(record) === selectedInputBundleVersionId(source)
}

export function sourceJsonTemplate() {
  return {
    schemaVersion: '1.0', projectId: 'nananxi-xufan', resolutionId: '1km',
    name: '格式模板（非实测，登记前请填写真实来源名称）',
    provenanceNote: '格式模板，非实测。请人工核对来源、坐标、单位、整点时刻与缺测标识。',
    unit: 'mm', crs: 'EPSG:4326', timeConvention: 'SOURCE_LOCAL_NAIVE_HOURLY',
    times: ['2017-06-18T00:00', '2017-06-18T01:00'],
    stations: [{ id: 'REPLACE_WITH_REAL_STATION_ID', lon: null, lat: null, values: [null, null] }]
  }
}

export function parseSourceJson(text) {
  if (new TextEncoder().encode(text).length > 2 * 1024 * 1024) throw new Error('来源 JSON 最大为 2 MB。')
  let payload
  try { payload = JSON.parse(text) } catch { throw new Error('来源 JSON 格式无效，请检查括号、逗号与 null。') }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('来源 JSON 必须为对象。')
  if (payload.projectId !== 'nananxi-xufan' || payload.resolutionId !== '1km') throw new Error('仅开放南安溪 1 km 来源登记。')
  if (!payload.name?.trim() || /格式模板/.test(payload.name)) throw new Error('请填写真实来源名称；非实测格式模板不能直接登记。')
  if (!Array.isArray(payload.stations) || !payload.stations.length || payload.stations.length > 32) throw new Error('来源应包含 1–32 个真实站点。')
  if (payload.stations.some((row) => !row.id || row.id === 'REPLACE_WITH_REAL_STATION_ID' || !Number.isFinite(row.lon) || !Number.isFinite(row.lat))) {
    throw new Error('请填写真实站号和数值坐标，不接受模板占位坐标。')
  }
  // The backend performs the authoritative time/value/physical validation.
  return payload
}

export function buildInputProcessingRequest(form = {}, source = {}) {
  const operation = form.operation || 'qcWindow'
  if (!['qcWindow', 'stationIdw'].includes(operation)) throw new Error('不支持的降雨整编方式。')
  if (!form.name?.trim()) throw new Error('请填写新资料版本名称。')
  if (!form.start || !form.end) throw new Error('请填写整编时段的开始与结束整点。')
  const request = {
    requestId: uniqueRequestId('DATA'), operation, projectId: 'nananxi-xufan', resolutionId: '1km',
    name: form.name.trim(), start: form.start, end: form.end,
    parentBundleVersionId: selectedInputBundleVersionId(source)
  }
  if (operation === 'stationIdw') {
    if (!form.sourceId) throw new Error('请先登记并选择站点雨量资料。')
    Object.assign(request, {
      sourceId: form.sourceId, idwPower: Number(form.idwPower), minimumStations: Number(form.minimumStations),
      zeroOnlyPolicy: form.zeroOnlyPolicy || 'KEEP'
    })
    if (!Number.isFinite(request.idwPower) || request.idwPower < 1 || request.idwPower > 4) throw new Error('距离反比加权插值指数必须为 1–4。')
    if (!Number.isInteger(request.minimumStations) || request.minimumStations < 1) throw new Error('最少有效站数必须为正整数。')
  }
  return request
}

export const listInputProducts = () => modelApi('input-products')
export const listInputSources = () => modelApi('input-sources')
export const listInputTasks = () => modelApi('input-tasks')
export const getInputTask = (id) => modelApi(`input-tasks/${encodeURIComponent(id)}`)
export const getInputTaskResult = (id) => modelApi(`input-tasks/${encodeURIComponent(id)}/result`)
export const getInputTaskLog = (id) => modelApi(`input-tasks/${encodeURIComponent(id)}/log`)
export const registerInputSource = (payload) => modelApi('input-sources', { method: 'POST', body: payload })
export const submitInputProcessingTask = (request) => modelApi('input-tasks', { method: 'POST', body: request })
export const cancelInputProcessingTask = (id) => modelApi(`input-tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {} })
export const registerInputProduct = (id) => modelApi(`input-tasks/${encodeURIComponent(id)}/register`, { method: 'POST', body: {} })
