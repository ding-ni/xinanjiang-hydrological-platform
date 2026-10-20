// A comparison is meaningful only when every condition except runoff parameters
// is identical. Task ids remain the source of truth for saved trial results.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
  return value
}

export function trialConditions(request = {}, result = {}) {
  const p = request.period || {}
  const period = p.mode === 'continuous'
    ? Object.fromEntries(['mode', 'warmupDays', 'calibrationStart', 'calibrationEnd', 'validationStart', 'validationEnd', 'independentStart', 'independentEnd'].map(key => [key, p[key] || null]))
    : { mode: p.mode, warmupDays: p.warmupDays,
        events: (p.events || []).filter(row => row.purpose !== '未使用' && row.status !== 'inactive')
          .map(({ id, start, end, purpose, revision }) => ({ id, start, end, purpose, revision }))
          .sort((a, b) => a.id.localeCompare(b.id)) }
  const lineage = { ...request.lineage, ...result.lineage }
  const science = result.scienceSnapshot?.manifest || {}
  return JSON.stringify(canonical({ context: request.context?.contextKey, period,
    input: request.inputs?.bundleVersionId,
    routing: result.routingCandidate?.routingVersionId || lineage.routingVersionId || request.routing?.versionId,
    routingParameters: result.bestRoutingParameters || null,
    scienceVersionId: result.science?.versionId || request.science?.versionId || lineage.scienceVersionId || null,
    continuousOrigin: result.continuousOrigin || request.calibration?.continuousOrigin || p.continuousOrigin || null,
    scienceIdentity: Object.fromEntries(['modelImplementationSHA256', 'scienceConfigurationSHA256', 'petConfigurationSHA256']
      .map(key => [key, lineage[key] || science[key] || null])),
    statePolicy: result.statePolicy || request.calibration?.statePolicy || 'independent-warmup',
    petMode: result.petMode || lineage.petMode || null,
    evaporation: request.inputs?.evaporation, objectives: request.calibration?.objectives }))
}

export function changedParameters(before = {}, after = {}) {
  const display = value => value == null ? '—' : Number.isFinite(Number(value)) ? Number(Number(value).toPrecision(6)) : value
  return Object.keys(after).filter(key => Number(before[key]) !== Number(after[key]))
    .map(key => `${key} ${display(before[key])} → ${display(after[key])}`).join(' · ')
}

export function alignedTrialSeries(hydrograph, times) {
  const hour=value=>String(value||'').replace(' ','T').slice(0,16)
  const values = new Map((hydrograph?.forecast || []).map(row => [hour(row.time), row.value]))
  return times.map(time => values.get(hour(time)) ?? null)
}

export function latestComparableTrial(records = [], current, periodId) {
  if (!current?.frozenRequest || !current?.hydrographs?.[periodId]) return null
  const comparisonIdentity = value => {
    const parsed = JSON.parse(value)
    delete parsed.routing
    delete parsed.routingParameters
    return JSON.stringify(canonical(parsed))
  }
  const identity = comparisonIdentity(trialConditions(current.frozenRequest, current))
  const index = records.findIndex(row => row.taskId === current.taskId)
  const prior = (index < 0 ? records : records.slice(0, index)).filter(row => row.conditions && comparisonIdentity(row.conditions) === identity)
  return prior.at(-1) || null
}

const hourKey = value => String(value || '').replace(' ', 'T').slice(0, 16)

export function resultProcessMatches(editor, result, periodId) {
  const request = result?.frozenRequest
  if (!request || request.period?.mode !== editor.calibrationMode) return false
  if (editor.schemeSourceBundleVersionId && (request.inputs?.sourceBundleVersionId || request.inputs?.bundleVersionId) !== editor.schemeSourceBundleVersionId) return false
  if ((request.calibration?.statePolicy || 'independent-warmup') !== editor.statePolicy) return false
  if (Number(request.period.warmupDays) !== Number(editor.warmupDays)) return false
  if (editor.statePolicy === 'continuous-origin' && hourKey(request.calibration?.continuousOrigin) !== hourKey(editor.continuousOrigin)) return false
  if ((request.science?.versionId || null) !== (editor.scienceVersionId || null)) {
    // Registered runtime science is also created from the editable default
    // conditions; only an explicitly selected reference is compared by ID.
    if (editor.scienceVersionId) return false
    const mode = result.petMode || result.lineage?.petMode
    if (editor.evaporationMode === 'constant-zero' && mode && mode !== 'CONSTANT_ZERO_EXPLICIT_REQUEST') return false
    if (editor.evaporationMode === 'monthly-climatology-assumption' && mode && mode !== 'MONTHLY_CLIMATOLOGY_ASSUMPTION') return false
    const frozenMode = request.inputs?.evaporation?.mode
    if (frozenMode !== editor.evaporationMode && frozenMode !== 'science-version') return false
  }
  if (editor.calibrationMode === 'event') {
    const current = [...(editor.schemeWindows || []), ...(editor.events || [])].find(row => row.id === periodId)
    const frozen = request.period.events?.find(row => row.id === periodId)
    return Boolean(current && frozen && hourKey(current.start) === hourKey(frozen.start) && hourKey(current.end) === hourKey(frozen.end))
  }
  return ['calibrationStart', 'calibrationEnd', 'validationStart', 'validationEnd', 'independentStart', 'independentEnd']
    .every(key => hourKey(request.period[key]) === hourKey(editor[key]))
}

export function calibrationSummaryMetrics(metrics = {}) {
  return {
    nse: metrics.nse ?? metrics.NSE ?? metrics.mean_NSE,
    peak: metrics.peakErrorPercent ?? metrics.peak_error_percent ?? metrics.mean_abs_peak_error_percent,
    time: metrics.peakTimeErrorHours ?? metrics.peak_time_error_hours ?? metrics.median_peak_time_error_hours ?? metrics.mean_abs_peak_time_error_hours,
    volume: metrics.volumeErrorPercent ?? metrics.volume_error_percent ?? metrics.mean_abs_volume_error_percent
  }
}
