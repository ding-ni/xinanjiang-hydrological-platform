import { modelApi, uniqueRequestId } from './modelBackendService.js'
import { hydrologyText } from './hydrologyPresentation.js'

export const operationLabels = {
  eventSimulation: '洪水模拟', continuousSimulation: '连续模拟', continuation: '续算',
  hindcast: '单次回算', rollingHindcast: '滚动回算',
  eventCalibration: '洪水率定', continuousCalibration: '连续率定'
  , calibration: '参数率定', manualTrial: '人工试调'
}
export const operationStatuses = {
  queued: '等待计算', preparing: '准备资料', running: '正在计算', cancelling: '正在停止',
  completed: '已完成', archived: '已完成', failed: '计算失败', cancelled: '已停止'
}
export const activeOperation = row => ['queued', 'preparing', 'running', 'cancelling'].includes(row?.status)
export const completedOperation = row => ['completed', 'archived'].includes(row?.status)
export const operationId = row => row?.id || row?.operationId || row?.taskId || ''
export const hourInput = value => String(value || '').replace(' ', 'T').slice(0, 16)
export const hourText = value => hourInput(value).replace('T', ' ')

export function preparationIssueText(issue) {
  if (typeof issue === 'string') return hydrologyText(issue)
  if (!issue || typeof issue !== 'object') return ''
  const label = issue.eventName || issue.eventId || issue.label || ''
  const reasons = Array.isArray(issue.reasons) ? issue.reasons.map(row => hydrologyText(typeof row === 'string' ? row : row.message || row.reason || '')).filter(Boolean) : []
  const message = hydrologyText(issue.message || issue.reason || reasons.join('；'))
  const ranges = gaps => Array.isArray(gaps) ? gaps.map(row => typeof row === 'string' ? row : [hourText(row.start || row.startTime),hourText(row.end || row.endTime)].filter(Boolean).join('—')).filter(Boolean).join('；') : ''
  const intervals = [ranges(issue.missingIntervals || issue.intervals || issue.missingRanges),
    issue.petMissingIntervals?.length ? `蒸散发 ${ranges(issue.petMissingIntervals)}` : '',
    issue.flowMissingIntervals?.length ? `实测流量 ${ranges(issue.flowMissingIntervals)}` : ''].filter(Boolean).join('；')
  return [label, message, intervals].filter(Boolean).join(' · ') || '所选时段资料不足'
}

export function operationContext(context) {
  return { projectId: context.basinId || context.projectId, resolutionId: context.modelScale || context.resolutionId,
    modelConfigId: context.configId || context.modelConfigId, contextKey: context.contextKey }
}

export function belongsToOperationContext(row, contextKey) {
  const key = row.contextKey || row.context?.contextKey || row.request?.context?.contextKey
    || row.runConfig?.context?.contextKey
  return key === contextKey
}

export function rememberedParameter(schemes, savedId) {
  if (savedId && schemes.some(row => row.id === savedId)) return savedId
  return schemes.length === 1 ? schemes[0].id : ''
}

export function historicalParameterId(schemes, task, explicitId = '') {
  if (explicitId) return schemes.find(row => row.id === explicitId)?.id || ''
  const request = task?.runConfig || task || {}
  const savedId = request.lineage?.parameterSchemeId || request.lineage?.parameterVersionId || request.parameterSchemeId
  const linked = schemes.find(row => row.id === savedId)
  if (linked) return linked.id
  if (!task?.taskId) return ''
  return schemes.find(row => row.sourceTaskId === task?.taskId)?.id || ''
}

export function dailyOperationSelection(row, lastParameterId, { explicitHistory = false } = {}) {
  const request = row?.runConfig || row?.request || {}
  const frozen = request.parameterSchemeId || request.parameterSnapshot?.id || ''
  if (activeOperation(row) || explicitHistory) return { restore: true, parameterId: frozen || lastParameterId }
  if (!frozen || (lastParameterId && frozen !== lastParameterId)) return { restore: false, parameterId: lastParameterId }
  return { restore: true, parameterId: frozen }
}

const storageKey = (contextKey, kind) => `hydrology.daily.${contextKey}.${kind}`
export function readDailySelection(contextKey, kind) {
  try { return JSON.parse(globalThis.localStorage?.getItem(storageKey(contextKey, kind)) || '{}') } catch { return {} }
}
export function saveDailySelection(contextKey, kind, value) {
  try { globalThis.localStorage?.setItem(storageKey(contextKey, kind), JSON.stringify(value)) } catch { /* Browser storage is optional. */ }
}

export function resultEntries(result = {}, taskId = '') {
  if (Array.isArray(result.issues)) return result.issues.map((row, index) => ({ ...row,
    id: row.issueTime || `issue-${index}`, label: `${hourText(row.issueTime)} 起报`,
    taskId: row.stateCalculationTaskId || row.taskId, hydrograph: row.hydrograph }))
  const rows = Array.isArray(result.hydrographs) ? result.hydrographs
    : result.hydrographs && typeof result.hydrographs === 'object' ? Object.entries(result.hydrographs).map(([id, hydrograph]) => ({ id, hydrograph }))
      : Array.isArray(result.events) && result.events.length ? result.events
        : Array.isArray(result.eventStates) ? result.eventStates : []
  if (rows.length) return rows.map((row, index) => {
    const id = row.eventId || row.id || row.label || `event-${index}`
    const state = Array.isArray(result.eventStates) ? result.eventStates.find(item => (item.eventId || item.id) === id)
      : result.eventStates?.[id]
    const window = (result.frozenRequest?.period?.events || result.frozenRequest?.period?.windows || []).find(item => item.id === id)
    const label = row.label || row.name || state?.label || window?.name || (/^WINDOW-/.test(id) && (row.start || state?.start) ? `${hourText(row.start || state.start)} 洪水` : id)
    return { ...row, id, label, hydrograph: row.hydrograph || row,
      start:row.start || state?.start, end:row.end || state?.end,
      taskId: row.stateCalculationTaskId || state?.taskId || state?.stateCalculationTaskId || row.taskId || result.stateCalculationTaskId || result.primaryTaskId || taskId,
      endStateVersionId: row.endStateVersionId || state?.endStateVersionId }
  })
  if (result.hydrograph) return [{ id: 'process', label: '流量过程', taskId: result.stateCalculationTaskId || result.primaryTaskId || result.sourceTaskId || taskId,
    hydrograph: result.hydrograph, endStateVersionId: result.endStateVersionId }]
  return []
}

export function createDailyClient(api = modelApi) {
  return {
    async parameters() { const payload = await api('parameter-schemes'); return payload.schemes || [] },
    async floods(context) { const payload = await api(`flood-catalog?projectId=${encodeURIComponent(context.basinId || context.projectId)}`); return payload.events || [] },
    async list(contextKey) { const payload = await api('operations'); return (payload.operations || []).filter(row => belongsToOperationContext(row, contextKey)) },
    submit(body) { return api('operations', { method: 'POST', body: { requestId: uniqueRequestId('OP'), ...body } }) },
    read(id) { return api(`operations/${encodeURIComponent(id)}`) },
    result(id) { return api(`operations/${encodeURIComponent(id)}/result`) },
    cancel(id) { return api(`operations/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {} }) },
    saveParameter(body) { return api('parameter-schemes', { method: 'POST', body }) }
  }
}

export const dailyClient = createDailyClient()
