/**
 * 洪水场次目录的前端数据契约。
 *
 * 原始洪号（sourceFloodNo）来自资料源，只读且不重排；eventId 是平台内部
 * 的稳定关联键，供雨量、出口水文、模型任务和结果档案共同使用。
 */
export const FLOOD_EVENT_SCHEMA_VERSION = '1.2'

export const FLOOD_EVENT_STATUS = Object.freeze([
  { value: 'draft', label: '草稿' },
  { value: 'pending', label: '待核查' },
  { value: 'active', label: '启用' },
  { value: 'inactive', label: '停用' }
])

export const FLOOD_EVENT_PURPOSES = Object.freeze(['未使用', '率定', '验证', '独立检验'])

export function cloneFloodEvent(event) {
  return event ? JSON.parse(JSON.stringify(event)) : event
}

function finiteNumber(value) {
  if (value === '' || value == null) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function durationHours(start, end) {
  const from = new Date(start)
  const to = new Date(end)
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || to < from) return null
  return Math.round((to.getTime() - from.getTime()) / 3600000) + 1
}

export function sourceFloodNoOf(event) {
  if (event?.sourceFloodNo != null && String(event.sourceFloodNo).trim()) return String(event.sourceFloodNo).trim()
  if (event?.floodCode != null && String(event.floodCode).trim()) return String(event.floodCode).trim()
  const id = String(event?.id || event?.eventId || '')
  return id.replace(/^[^-]+-/, '') || null
}

export function stableFloodEventId(event, basinProjectId = event?.basinProjectId) {
  if (event?.eventId) return String(event.eventId)
  if (event?.id) return String(event.id)
  const prefix = basinProjectId === 'nananxi-xufan' ? 'xf' : basinProjectId === 'selinco' ? 'sel' : 'evt'
  const source = sourceFloodNoOf(event)
  if (source) return `${prefix}-${source}`
  const start = String(event?.start || '').replace(/[-:T ]/g, '').slice(0, 12)
  return `${prefix}-${start || 'manual-event'}`
}

export function normalizeFloodEvent(event, packageData = null) {
  const source = event || {}
  const observedInputs = packageData?.observedInputs || {}
  const eventId = stableFloodEventId(source)
  const start = source.start || packageData?.start || null
  const end = source.end || packageData?.end || null
  const status = ['active', 'draft', 'pending', 'inactive'].includes(source.status)
    ? source.status
    : source.active === false
      ? 'inactive'
      : 'active'
  const observedPeak = finiteNumber(source.observedPeak ?? source.observedPeakFlowM3s ?? observedInputs.observedPeakFlowM3s)
  const rainCompleteness = finiteNumber(source.rainCompleteness ?? observedInputs.rainCompleteness)
  const flowCompleteness = finiteNumber(source.flowCompleteness ?? source.riverCompleteness ?? observedInputs.flowCompleteness ?? observedInputs.riverCompleteness)
  const volume = finiteNumber(source.volume ?? source.volumeM3 ?? observedInputs.volumeM3 ?? observedInputs.riverVolumeM3)
  const duration = finiteNumber(source.durationHours) ?? durationHours(start, end)
  const note = String(source.note || '').replace('徐畈历史实测洪水目录', '徐畈实测洪水场次目录')

  return {
    ...source,
    schemaVersion: FLOOD_EVENT_SCHEMA_VERSION,
    id: source.id || eventId,
    eventId,
    basinProjectId: source.basinProjectId || 'unknown-project',
    sourceFloodNo: sourceFloodNoOf(source),
    sectionId: source.sectionId || (source.basinProjectId === 'nananxi-xufan' ? '70115800' : null),
    name: String(source.name || source.sourceFloodNo || eventId),
    start,
    end,
    durationHours: duration,
    observedPeak,
    peakTime: source.peakTime || source.observedPeakTime || observedInputs.observedPeakTime || null,
    volume,
    volumeM3: volume,
    rainCompleteness,
    flowCompleteness,
    riverCompleteness: flowCompleteness,
    sourceFiles: source.sourceFiles || packageData?.references || null,
    note: note || source.note || packageData?.note || null,
    history: Array.isArray(source.history) ? source.history : [],
    status,
    revision: Math.max(1, Number(source.revision) || 1),
    purpose: FLOOD_EVENT_PURPOSES.includes(source.purpose) ? source.purpose : '未使用',
    source: source.source || packageData?.source || 'flood-event-catalog',
    lineage: {
      ...(source.lineage || {}),
      catalogEventId: source.id || null,
      dataPackageEventId: packageData?.eventId || null,
      sourceFloodNo: sourceFloodNoOf(source),
      observedInputs: packageData ? 'event-data-packages.json' : null
    }
  }
}

export function mergeFloodEventPackages(events, packages = []) {
  const packageById = new Map((packages || []).map((item) => [String(item.eventId), item]))
  return (events || []).map((event) => normalizeFloodEvent(event, packageById.get(String(event.eventId || event.id))))
}

export function activeFloodEvents(events) {
  return (events || []).filter((event) => event.status !== 'inactive')
}

export function eligibleFloodEvents(events) {
  return activeFloodEvents(events).filter((event) => {
    if (event.status === 'draft' || event.status === 'pending') return false
    const hasPeak = Number.isFinite(Number(event.observedPeak))
    const flowReady = event.flowCompleteness == null || Number(event.flowCompleteness) > 0
    return hasPeak && flowReady
  })
}

export function eventDataQuality(event) {
  const rain = event?.rainCompleteness
  const flow = event?.flowCompleteness
  if (rain == null && flow == null) return '待检查'
  if (rain != null && flow != null && rain >= 0.99 && flow >= 0.99) return '完整'
  if ((rain ?? 1) > 0 && (flow ?? 1) > 0) return '有缺测'
  return '缺资料'
}

export function buildEventSetVersion(events, catalogVersion = 'catalog') {
  const active = activeFloodEvents(events)
  const revision = active.reduce((max, event) => Math.max(max, Number(event.revision) || 1), 1)
  return `${catalogVersion}-${active.length}-r${revision}`
}
