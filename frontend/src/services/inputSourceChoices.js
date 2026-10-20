import { observationHour, observationTime } from './observationWindowPresentation.js'

/** Newly registered source receipts stay selectable if an earlier list request finishes later. */
export function mergeInputSourceChoices(loadedSources = [], registeredSources = []) {
  const choices = new Map()
  for (const source of [...loadedSources, ...registeredSources]) {
    if (source?.sourceId) choices.set(source.sourceId, { ...choices.get(source.sourceId), ...source })
  }
  return [...choices.values()]
}

function sourceHour(value) {
  const label = String(value || '').replace(' ', 'T')
  return observationHour(/^\d{4}-\d{2}-\d{2}T\d{2}:00:00(?:\.0+)?$/.test(label) ? label.slice(0, 16) : label)
}

export function latestInputProcessingWindow(range, hours = 24) {
  const first = sourceHour(range?.start), last = sourceHour(range?.end)
  if (first == null || last == null || first > last || !Number.isInteger(hours) || hours < 1) return null
  return { start: observationTime(Math.max(first, last - hours + 1)), end: observationTime(last) }
}
