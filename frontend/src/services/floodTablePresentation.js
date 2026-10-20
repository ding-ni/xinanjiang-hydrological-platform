export function floodValue(row, key) {
  if (key === 'peak') return row.observedPeak ?? row.observedPeakFlowM3s ?? row.observedInputs?.observedPeakFlowM3s
  if (key === 'duration') return row.durationHours ?? (Date.parse(row.end) - Date.parse(row.start)) / 3600000
  return row[key]
}

export function filterFloodRows(rows, { year = '', minimum = '', maximum = '', purpose = '' } = {}) {
  return rows.filter(row => {
    const peak = floodValue(row, 'peak')
    return (!year || String(row.start).startsWith(year)) && (!purpose || row.purpose === purpose)
      && (minimum === '' || minimum == null || peak != null && Number(peak) >= Number(minimum))
      && (maximum === '' || maximum == null || peak != null && Number(peak) <= Number(maximum))
  })
}

export function sortFloodRows(rows, key = '', direction = '') {
  if (!key || !direction) return [...rows]
  return [...rows].sort((a, b) => {
    const left = floodValue(a, key), right = floodValue(b, key)
    const empty = value => value == null || value === '' || ['peak', 'duration'].includes(key) && !Number.isFinite(Number(value))
    if (empty(left) !== empty(right)) return empty(left) ? 1 : -1
    const difference = ['peak', 'duration'].includes(key) ? Number(left) - Number(right) : String(left ?? '').localeCompare(String(right ?? ''))
    return (difference || 0) * (direction === 'desc' ? -1 : 1)
      || String(a.start).localeCompare(String(b.start)) || String(a.id || a.eventId).localeCompare(String(b.id || b.eventId))
  })
}
