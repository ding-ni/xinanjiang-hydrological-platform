// A comparison is meaningful only when every condition except runoff parameters
// is identical. Task ids remain the source of truth for saved trial results.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
  return value
}

export function trialConditions(request = {}) {
  const p = request.period || {}
  const period = p.mode === 'continuous'
    ? Object.fromEntries(['mode', 'warmupDays', 'calibrationStart', 'calibrationEnd', 'validationStart', 'validationEnd', 'independentStart', 'independentEnd'].map(key => [key, p[key] || null]))
    : { mode: p.mode, warmupDays: p.warmupDays,
        events: (p.events || []).filter(row => row.purpose !== '未使用' && row.status !== 'inactive')
          .map(({ id, start, end, purpose, revision }) => ({ id, start, end, purpose, revision }))
          .sort((a, b) => a.id.localeCompare(b.id)) }
  return JSON.stringify(canonical({ context: request.context?.contextKey, period,
    input: request.inputs?.bundleVersionId, routing: request.routing?.versionId,
    evaporation: request.inputs?.evaporation, objectives: request.calibration?.objectives }))
}

export function changedParameters(before = {}, after = {}) {
  const display = value => value == null ? '—' : Number.isFinite(Number(value)) ? Number(Number(value).toPrecision(6)) : value
  return Object.keys(after).filter(key => Number(before[key]) !== Number(after[key]))
    .map(key => `${key} ${display(before[key])} → ${display(after[key])}`).join(' · ')
}

export const TRIAL_COLORS = ['#718096', '#16847b', '#b35b32', '#8264a2', '#2677ad', '#ad7a17']

export function alignedTrialSeries(hydrograph, times) {
  const values = new Map((hydrograph?.forecast || []).map(row => [row.time, row.value]))
  return times.map(time => values.get(time) ?? null)
}
