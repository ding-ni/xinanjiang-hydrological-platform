import { reactive } from 'vue'
import { modelApi, modelBackendEnabled } from './modelBackendService'

export const workspaceSync = reactive({ ready: false, saving: false, pending: 0, error: '', workspaceId: '' })
const revisions = new Map(), pending = new Map()
let timer, flushing = false
const allowed = key => key === 'hydro_calibration_workspaces_v1' || /^hydro_flood_event_overrides_v1:[a-z0-9-]{1,80}$/.test(key)
const pendingKey = id => `xaj_pending_drafts:${id}`

async function historyKey(content) {
  if (globalThis.crypto?.subtle) {
    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content)))).map(n => n.toString(16).padStart(2, '0')).join('')
  }
  // Local history indexing also works on a LAN HTTP address (no secure context).
  let a = 2166136261, b = 5381
  for (const ch of content) { a = Math.imul(a ^ ch.charCodeAt(0), 16777619); b = Math.imul(b, 33) ^ ch.charCodeAt(0) }
  return `${content.length}-${a >>> 0}-${b >>> 0}`
}

function rememberPending() {
  workspaceSync.pending = pending.size
  try { localStorage.setItem(pendingKey(workspaceSync.workspaceId), JSON.stringify(Object.fromEntries(pending))) }
  catch { workspaceSync.error = '本地草稿缓存已满，请导出草稿' }
}

export function saveBusinessDraft(key, value) {
  localStorage.setItem(key, value)
  if (!modelBackendEnabled() || !allowed(key)) return
  if (!workspaceSync.ready) { workspaceSync.error = '草稿保存在本机；连接工作区后可同步'; return }
  pending.set(key, { value: JSON.parse(value), revision: pending.get(key)?.revision ?? revisions.get(key) ?? 0 })
  rememberPending()
  clearTimeout(timer)
  timer = setTimeout(flushBusinessDrafts, 700)
}

export async function flushBusinessDrafts() {
  if (!workspaceSync.ready || flushing) return
  flushing = true; workspaceSync.saving = true; workspaceSync.error = ''
  try {
    for (const [key, entry] of pending) {
      const response = await modelApi('workspace/drafts', { method: 'POST', body: { key, ...entry } })
      revisions.set(key, response.revision)
      if (pending.get(key) === entry) pending.delete(key)
      else pending.get(key).revision = response.revision
      rememberPending()
    }
  } catch (error) { workspaceSync.error = error.message }
  finally {
    flushing = false; workspaceSync.saving = false
    if (pending.size && !workspaceSync.error) { clearTimeout(timer); timer = setTimeout(flushBusinessDrafts, 700) }
  }
}

export async function initializeBusinessDrafts() {
  if (!modelBackendEnabled()) return
  workspaceSync.ready = false
  try {
    const [info, response] = await Promise.all([modelApi('workspace'), modelApi('workspace/drafts')])
    const id = info.workspace.id
    const previous = localStorage.getItem('xaj_browser_workspace')
    const local = Object.fromEntries(Object.keys(localStorage).filter(allowed).map(key => [key, localStorage.getItem(key)]))
    // Preserve browser-local history before loading another workstation's SQL drafts.
    const differs = Object.entries(local).some(([key, raw]) => !response.drafts[key] || JSON.stringify(response.drafts[key].value) !== raw)
    if (Object.keys(local).length && (differs || previous !== id)) {
      const content = JSON.stringify(local)
      const hash = await historyKey(content)
      localStorage.setItem(`xaj_draft_history:${previous || 'legacy'}:${hash}`, content)
    }
    const savedPending = JSON.parse(localStorage.getItem(pendingKey(id)) || '{}')
    for (const key of Object.keys(local)) localStorage.removeItem(key)
    workspaceSync.workspaceId = id
    revisions.clear(); pending.clear()
    for (const [key, row] of Object.entries(response.drafts)) {
      if (!allowed(key)) continue
      revisions.set(key, row.revision)
      localStorage.setItem(key, JSON.stringify(row.value))
    }
    // First migration only: a different workspace never inherits these drafts.
    if (!previous || previous === id) {
      for (const [key, raw] of Object.entries(local)) {
        if (!response.drafts[key]) savedPending[key] ||= { value: JSON.parse(raw), revision: 0 }
      }
    }
    for (const [key, entry] of Object.entries(savedPending)) {
      if (!allowed(key)) continue
      pending.set(key, entry)
      localStorage.setItem(key, JSON.stringify(entry.value))
    }
    localStorage.setItem('xaj_browser_workspace', id)
    workspaceSync.ready = true
    rememberPending()
    await flushBusinessDrafts()
  } catch (error) { workspaceSync.error = error.message }
}

export async function reloadWorkspaceDrafts() {
  if (flushing) throw new Error('正在保存，请稍后读取工作区版本')
  // Keep the full local content before replacing it with the shared revision.
  const content = JSON.stringify(exportBusinessDrafts())
  localStorage.setItem(`xaj_draft_history:${workspaceSync.workspaceId}:manual-${await historyKey(content)}`, content)
  clearTimeout(timer)
  localStorage.removeItem(pendingKey(workspaceSync.workspaceId))
  for (const key of Object.keys(localStorage).filter(allowed)) localStorage.removeItem(key)
  pending.clear(); rememberPending()
  await initializeBusinessDrafts()
  if (!workspaceSync.ready) throw new Error(workspaceSync.error)
}

export function exportBusinessDrafts() {
  const entries = Object.fromEntries(Object.keys(localStorage).filter(key => allowed(key) || key.startsWith('xaj_draft_history:') || key.startsWith('xaj_pending_drafts:')).map(key => [key, JSON.parse(localStorage.getItem(key))]))
  return { schemaVersion: 1, workspaceId: workspaceSync.workspaceId, exportedAt: new Date().toISOString(), entries }
}
