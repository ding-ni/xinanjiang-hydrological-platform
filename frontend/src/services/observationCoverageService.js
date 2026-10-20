function toDate(value) {
  if (!value) return null
  const date = new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * Returns the time range in which every required observed input is available.
 * For the current project this is the overlap of station rainfall and outlet
 * level/flow records. Keeping the calculation here prevents each view from
 * silently applying a different boundary.
 */
export function getObservationCoverage(catalog = {}) {
  const sources = [catalog?.rain, catalog?.river]
  const starts = sources.map((source) => toDate(source?.start)).filter(Boolean)
  const ends = sources.map((source) => toDate(source?.end)).filter(Boolean)
  if (!starts.length || !ends.length) return null
  const start = new Date(Math.max(...starts.map((date) => date.getTime())))
  const end = new Date(Math.min(...ends.map((date) => date.getTime())))
  return {
    start,
    end,
    startText: formatObservationDate(start),
    endText: formatObservationDate(end),
    valid: start <= end
  }
}

export function formatObservationDate(value) {
  const date = value instanceof Date ? value : toDate(value)
  if (!date) return null
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatObservationCoverage(catalog = {}, fallback = '待目录读取') {
  const coverage = getObservationCoverage(catalog)
  if (!coverage?.valid) return fallback
  return `${coverage.startText} 至 ${coverage.endText}`
}

export function observationBoundaryInput(catalog = {}, kind = 'start') {
  const coverage = getObservationCoverage(catalog)
  if (!coverage?.valid) return ''
  const date = kind === 'end' ? coverage.end : coverage.start
  return formatObservationDate(date)?.replace(' ', 'T') || ''
}
