import { normalizeFloodEvent } from './floodEventService.js'

// Workspaces are JSON records. An absent optional field (for example, no
// selected event in an empty input catalogue) is missing data, not a JSON text.
// Normalize it to null so persistence never calls JSON.parse(undefined).
export function cloneWorkspaceValue(value) {
  return value === undefined ? null : JSON.parse(JSON.stringify(value))
}
// The older API normalizer wrote known events with `unknown-project`. Recover
// only that missing scope, using an exact existing event identity inside the
// same verified workspace. This is not an event registration or QC approval.
// Explicit foreign scopes and unknown IDs are never relabelled; nested user
// history/localDraft snapshots retain their original contents unchanged.
export function mergeWorkspaceFloodEvents(baseEvents = [], savedEvents = [], baseContext = null, savedContext = null) {
  const base = Array.isArray(baseEvents) ? baseEvents : []
  const saved = Array.isArray(savedEvents) ? savedEvents : []
  const savedById = new Map(saved.map((event) => [event.eventId || event.id, event]))
  const verifiedWorkspace = ['domainId', 'modelScale', 'contextKey'].every((key) =>
    baseContext?.[key] != null && baseContext[key] === savedContext?.[key])
  const baseIds = new Set()
  const merged = base.map((event) => {
    const key = event.eventId || event.id
    baseIds.add(key)
    const previous = savedById.get(key)
    const restored = { ...cloneWorkspaceValue(event), ...(previous ? cloneWorkspaceValue(previous) : {}) }
    if (previous && verifiedWorkspace && event.basinProjectId === baseContext.domainId
      && [undefined, null, '', 'unknown-project'].includes(previous.basinProjectId)) {
      restored.basinProjectId = event.basinProjectId
    }
    return normalizeFloodEvent(restored)
  })
  saved.filter((event) => !baseIds.has(event.eventId || event.id))
    .forEach((event) => merged.push(normalizeFloodEvent(cloneWorkspaceValue(event))))
  return merged
}

function snapshotIdentity(value) {
  // Canonicalize only the comparison key, never the preserved evidence. Equal
  // JSON snapshots with reordered keys must not accumulate on each reload.
  return JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]]))
    : item)
}

export function restoreWorkspaceEventRecords(baseEvents = [], savedEvents = [], baseContext = null, savedContext = null, savedQuarantine = []) {
  const base = Array.isArray(baseEvents) ? baseEvents : []
  const saved = Array.isArray(savedEvents) ? savedEvents : []
  const merged = mergeWorkspaceFloodEvents(base, saved, baseContext, savedContext)
  const domainId = baseContext?.domainId
  const verifiedWorkspace = ['domainId', 'modelScale', 'contextKey'].every((key) =>
    baseContext?.[key] != null && baseContext[key] === savedContext?.[key])
  const knownIds = new Set(base.filter((event) => event.basinProjectId === domainId)
    .map((event) => event.eventId || event.id))
  const adoptedById = new Map(saved.map((event) => [event.eventId || event.id, event]))
  // Inspect all original rows rather than an ID map. Multiple foreign snapshots
  // can share a native ID, and each distinct original must remain recoverable.
  const excluded = [
    ...base.filter((event) => event.basinProjectId !== domainId),
    ...saved.filter((event) => {
      const adopted = adoptedById.get(event.eventId || event.id)
      // Preserve conflicting earlier user snapshots too, including a valid
      // scope shadowed by a later foreign row or another same-scope revision.
      if (adopted !== event && snapshotIdentity(adopted) !== snapshotIdentity(event)) return true
      return event.basinProjectId !== domainId
        && !(verifiedWorkspace && knownIds.has(event.eventId || event.id)
          && [undefined, null, '', 'unknown-project'].includes(event.basinProjectId))
    })
  ]
  const quarantinedEvents = []
  const seen = new Set()
  for (const original of [...(Array.isArray(savedQuarantine) ? savedQuarantine : []), ...excluded]) {
    const snapshot = cloneWorkspaceValue(original)
    const identity = snapshotIdentity(snapshot)
    if (!seen.has(identity)) {
      seen.add(identity)
      quarantinedEvents.push(snapshot)
    }
  }
  return {
    events: merged.filter((event) => event.basinProjectId === baseContext?.domainId),
    // This evidence is never passed into an event catalog or candidate list.
    // In particular, retain the original scope and registration claim as data,
    // without treating either as permission to execute a model task.
    quarantinedEvents
  }
}
