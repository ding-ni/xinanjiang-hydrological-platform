import { cloneFloodEvent, normalizeFloodEvent, stableFloodEventId } from './floodEventService.js'

function sameTime(left, right) {
  if (left === right) return true
  if (left == null || right == null || left === '' || right === '') return false
  const from = new Date(left).getTime()
  const to = new Date(right).getTime()
  return Number.isFinite(from) && Number.isFinite(to) && from === to
}

function backendStatus(event) {
  if (event.status === 'ready') return 'active'
  return ['active', 'pending', 'draft', 'inactive'].includes(event.status) ? event.status : 'pending'
}

export function registeredBackendEventSelection(events = [], selectedId = null) {
  const registered = (Array.isArray(events) ? events : []).filter((event) => event.runtimeRegistered === true)
  const selected = registered.find((event) => event.id === selectedId || event.eventId === selectedId) || registered[0]
  return { eventId: selected?.id || null, eventStart: selected?.start || null, eventEnd: selected?.end || null }
}

/**
 * Runtime event windows, statistics and sources belong to the registered QC
 * catalog. User assignments and edits are retained separately, never spread
 * over that authoritative input. Nothing is deleted from the local catalog.
 */
export function mergeBackendFloodEvents(currentEvents = [], backendEvents = []) {
  const current = Array.isArray(currentEvents) ? currentEvents : []
  const registered = Array.isArray(backendEvents) ? backendEvents : []
  const previousById = new Map(current.map((event) => [stableFloodEventId(event), event]))
  const knownIds = new Set(registered.map((event) => stableFloodEventId(event)))

  const runtime = registered.map((event) => {
    const previous = previousById.get(stableFloodEventId(event))
    const authoritative = normalizeFloodEvent({
      start: null,
      end: null,
      observedPeak: null,
      observedPeakFlowM3s: null,
      peakTime: null,
      observedPeakTime: null,
      volume: null,
      volumeM3: null,
      rainCompleteness: null,
      flowCompleteness: null,
      riverCompleteness: null,
      sourceFiles: null,
      source: 'model-backend-event-catalog',
      revision: 1,
      ...cloneFloodEvent(event),
      // Recalculate the inclusive hourly duration from the QC endpoints; old
      // front-end durations (or stale derived fields) must not survive a merge.
      durationHours: null,
      registeredStatus: backendStatus(event),
      status: previous?.status === 'inactive' ? 'inactive' : backendStatus(event),
      purpose: previous?.purpose ?? event.purpose ?? '未使用',
      name: previous?.name ?? event.name,
      note: previous?.note ?? event.note,
      history: cloneFloodEvent(previous?.history ?? event.history ?? [])
    })

    if (previous?.localDraft != null) {
      authoritative.localDraft = cloneFloodEvent(previous.localDraft)
    } else if (previous && (Number(previous.revision) > 1
      || !sameTime(previous.start, authoritative.start)
      || !sameTime(previous.end, authoritative.end))) {
      // Preserve the entire original edit, including its own history, source
      // metadata and custom fields. Re-merging keeps this snapshot as-is.
      authoritative.localDraft = cloneFloodEvent(previous)
    }
    authoritative.runtimeRegistered = true
    return authoritative
  })

  const local = current.filter((event) => !knownIds.has(stableFloodEventId(event))).map((event) => normalizeFloodEvent({
    ...cloneFloodEvent(event),
    runtimeRegistered: false,
    registeredStatus: null,
    status: event.status === 'inactive' ? 'inactive' : 'draft'
  }))
  return [...runtime, ...local]
}
