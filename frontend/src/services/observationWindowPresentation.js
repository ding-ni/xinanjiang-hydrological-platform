const HOUR_MS = 3600000

// Source records use local clock labels. UTC arithmetic avoids browser timezone
// and daylight-saving changes when selecting consecutive source hours.
export function observationHour(value) {
  const label = String(value || '').replace(' ', 'T')
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:00$/.test(label)) return null
  const milliseconds = Date.parse(`${label}:00Z`)
  if (!Number.isFinite(milliseconds) || new Date(milliseconds).toISOString().slice(0, 16) !== label) return null
  return milliseconds / HOUR_MS
}

export function observationTime(hour) {
  return new Date(hour * HOUR_MS).toISOString().slice(0, 16)
}

export function initialObservationWindow(station, replayTime, hours = 168) {
  const first = Number(station?.startHour), last = Number(station?.endHour)
  if (!Number.isInteger(first) || !Number.isInteger(last) || first > last) return null
  const replay = observationHour(replayTime)
  const end = replay != null && replay >= first && replay <= last ? replay : last
  return { start: observationTime(Math.max(first, end - hours + 1)), end: observationTime(end) }
}

export function stationRainfallName(start, end) {
  if (observationHour(start) == null || observationHour(end) == null) return '南安溪站点雨量'
  return `南安溪站点雨量 · ${start.slice(0, 10)}至${end.slice(0, 10)}`
}
