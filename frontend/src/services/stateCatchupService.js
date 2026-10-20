import { stateScienceBlockReason } from './scientificSelectionService.js'

// State times are local model hours, not browser-clock forecast publication times.
export function catchupWindow(stateTime, issueTime) {
  const parse = value => {
    const raw = String(value || '').replace(' ', 'T')
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:00(?::00)?$/.test(raw)) return NaN
    const date = new Date(raw)
    return Number.isFinite(date.getTime()) ? date.getTime() : NaN
  }
  const from = parse(stateTime), to = parse(issueTime)
  if (!Number.isFinite(from) || !Number.isFinite(to)) return { hours: null, ready: false, reason: '请选择整点起报时刻' }
  const hours = (to - from) / 3600000
  if (hours < 0) return { hours, ready: false, reason: '状态晚于起报时刻' }
  if (hours === 0) return { hours, ready: false, reason: '状态已衔接起报时刻' }
  if (hours > 366 * 24) return { hours, ready: false, reason: '跨度超过一年，请在状态页分段续算' }
  return { hours, ready: true, reason: '', endTime: String(issueTime).replace(' ', 'T') }
}

export function compatibleCatchupState(node, inputVersion, routingVersion, modelImplementationSHA256) {
  const checkpoint = node.checkpoint || node
  const input = node.inputBundleVersionId || checkpoint.inputBundleVersionId || checkpoint.lineage?.inputBundleVersionId
  const routing = checkpoint.routingVersionId || checkpoint.lineage?.routingVersionId || 'control-split-v2'
  return node.resultSource === 'model-backend' && input === inputVersion && routing === routingVersion && !stateScienceBlockReason(node, modelImplementationSHA256)
}
