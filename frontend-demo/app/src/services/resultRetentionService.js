import { ref } from 'vue'
import { modelApi } from './modelBackendService.js'

export const retentionChanged = ref(0)
export const leaveRequest = ref(null)
const storageKey = () => `xaj-pending-results:${localStorage.getItem('xaj_browser_workspace') || 'unbound'}`
const readPending = () => { try { return JSON.parse(sessionStorage.getItem(storageKey()) || '[]') } catch { return [] } }
export function trackTemporary(row) {
  if (row?.retention !== 'temporary' || row.legacy) return
  const rows = readPending().filter(item => item.id !== row.id)
  rows.push({ id: row.id, name: row.name, schemeId: row.schemeId, contextKey: row.contextKey, sourceTaskIds:row.sourceTaskIds||[] })
  sessionStorage.setItem(storageKey(), JSON.stringify(rows))
}
export function forgetTemporary(id) {
  sessionStorage.setItem(storageKey(), JSON.stringify(readPending().filter(row => row.id !== id)))
}
export async function changeRetention(id, action, revision) {
  const result = await modelApi(`result-retention/${encodeURIComponent(id)}/${action}`, {method:'POST', body:{revision}})
  if (result.retention !== 'temporary') forgetTemporary(id)
  retentionChanged.value++
  return result
}
export async function settleTemporary({ schemeId, contextKey } = {}) {
  const pending = readPending().filter(row => (!schemeId || row.schemeId === schemeId) && (!contextKey || row.contextKey === contextKey))
  if (!pending.length) return true
  const payload = await modelApi('result-catalog')
  const rows = pending.map(item => payload.results.find(row => row.id === item.id)).filter(row => row?.retention === 'temporary' && !row.legacy)
  for (const item of pending) if (!rows.some(row => row.id === item.id)) forgetTemporary(item.id)
  if (!rows.length) return true
  if (leaveRequest.value) return false
  return new Promise(resolve => { leaveRequest.value = {rows, resolve} })
}
export function completeLeave(value) { const request=leaveRequest.value; leaveRequest.value=null; request?.resolve(value) }
export function hasPendingResults() { return readPending().length > 0 }
export function sameResultWorkspace(to, from) {
  const related = route => ['/calibration','/simulation','/forecast','/state'].includes(route.path) || route.name === 'result-sections'
  if (!related(to) || !related(from)) return false
  if (['/calibration','/simulation','/forecast'].includes(to.path) && ['/calibration','/simulation','/forecast'].includes(from.path)) return to.path === from.path
  const id = route => route.query.operation || route.query.result || route.params.id || route.query.source || route.query.task
  const relatedIds=readPending().flatMap(row=>[row.id,...(row.sourceTaskIds||[])])
  return !id(to) || !id(from) || id(to) === id(from) || (relatedIds.includes(id(to)) && relatedIds.includes(id(from)))
}
