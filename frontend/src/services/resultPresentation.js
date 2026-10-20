const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value

export function resultPeriods(result = {}) {
  if (result.hydrographs && Object.keys(result.hydrographs).length) {
    return Object.entries(result.hydrographs).map(([id, hydrograph]) => {
      const period = (result.periods || result.events || []).find(row => (row.id || row.eventId) === id)
      const group = (result.eventMetrics || []).find(row => row.eventId === id)?.group
      const purpose = period?.purpose || { CALIBRATION: '率定', VALIDATION: '验证', INDEPENDENT_TEST: '独立检验' }[group]
      const label = ['CALIBRATION', 'VALIDATION', 'INDEPENDENT'].includes(id) && purpose ? `${purpose}期` : purpose ? `${purpose} · ${id}` : id
      return { id, label, hydrograph }
    })
  }
  return result.hydrograph ? [{ id: result.runType === 'forecast' ? 'FORECAST' : 'SIMULATION', label: result.runType === 'forecast' ? '预见期' : '计算时段', hydrograph: result.hydrograph }] : []
}

export function comparisonReason(left, right, leftHydro, rightHydro) {
  if (left?.calibration?.target === 'routing' || right?.calibration?.target === 'routing') return '坡面汇流成果采用独立的汇流方案'
  if (!leftHydro?.forecast?.length || !rightHydro?.forecast?.length) return '所选成果没有同一时段的流量过程'
  const conditions = request => ({
    context: request.context, input: request.inputs?.bundleVersionId,
    inputHash: request.inputs?.bundleManifestSHA256,
    routing: request.lineage?.routingManifestSHA256,
    evaporation: request.inputs?.evaporation,
    warmupDays: request.period?.warmupDays,
    parent: request.period?.initialStateVersionId || null,
    statePolicy: request.calibration?.statePolicy || null,
    continuousOrigin: request.calibration?.continuousOrigin || request.period?.continuousOrigin || null,
    science: request.science?.versionId || request.lineage?.scienceVersionId || null,
    initialization: request.initialization || null,
  })
  const a = conditions(left || {}), b = conditions(right || {})
  if (!a.context || !a.inputHash || !a.routing || !b.inputHash || !b.routing) return '成果缺少冻结的输入或汇流版本'
  if (JSON.stringify(canonical(a)) !== JSON.stringify(canonical(b))) return '输入资料、汇流方案或起算条件不同'
  const times = h => h.forecast.map(row => row.time)
  if (JSON.stringify(times(leftHydro)) !== JSON.stringify(times(rightHydro))) return '计算时段或逐时时刻不同'
  if (JSON.stringify(leftHydro.obs) !== JSON.stringify(rightHydro.obs)
      || JSON.stringify(leftHydro.forcing?.precipitation) !== JSON.stringify(rightHydro.forcing?.precipitation)) return '实测流量或降雨过程不同'
  return ''
}

export function comparisonAvailability(left, right, leftHydro, rightHydro) {
  if (!leftHydro?.forecast?.length || !rightHydro?.forecast?.length) return '所选成果没有对应流量过程'
  if (JSON.stringify(canonical(left?.context)) !== JSON.stringify(canonical(right?.context))) return '所选成果属于其他计算流域'
  const times = new Set(leftHydro.forecast.map(row => row.time))
  if (!rightHydro.forecast.some(row => times.has(row.time))) return '两次计算没有重合时段'
  return ''
}

export function comparisonDifferences(left = {}, right = {}) {
  const fields = [
    ['降雨资料', r => [r.inputs?.bundleManifestSHA256, r.inputs?.bundleVersionId]],
    ['汇流条件', r => [r.routing, r.lineage?.routingManifestSHA256]],
    ['蒸散发与初态', r => [r.science, r.inputs?.evaporation, r.initialization]],
    ['起算方式', r => [r.period?.warmupDays, r.period?.initialStateVersionId, r.period?.continuousOrigin || r.calibration?.continuousOrigin, r.calibration?.statePolicy]],
  ]
  return fields.filter(([, read]) => JSON.stringify(canonical(read(left))) !== JSON.stringify(canonical(read(right)))).map(([name]) => name).join('、')
}

export function matchingResultPeriod(result, id, hydrograph) {
  const periods = resultPeriods(result)
  const exact = periods.find(row => row.id === id)
  if (exact) return exact.hydrograph
  const times = new Set((hydrograph?.forecast || []).map(row => row.time))
  return periods.map(row => ({ ...row, overlap: (row.hydrograph.forecast || []).filter(point => times.has(point.time)).length }))
    .sort((a, b) => b.overlap - a.overlap).find(row => row.overlap)?.hydrograph
}

export function calculationName(task, metadata = {}) {
  return metadata[task?.taskId]?.name || [task?.lineage?.schemeName || '历史计算', task?.runTypeLabel, task?.createdAt?.replace('T', ' ').slice(0, 16)].filter(Boolean).join(' · ')
}

export function csv(rows) {
  const cell = value => {
    if (typeof value === 'number') return Number.isFinite(value) ? String(value) : ''
    let text = String(value ?? '')
    // User annotations must remain text when opened in spreadsheet software.
    if (/^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = `'${text}`
    return `"${text.replaceAll('"', '""')}"`
  }
  return '\uFEFF' + rows.map(row => row.map(cell).join(',')).join('\r\n')
}

export function hydrographCsv(hydrograph, comparisons = [], mode = 'simulation') {
  if (mode === 'forecast') return csv([['时刻', '降雨量 (mm)', '预报流量 (m³/s)'],
    ...(hydrograph.forecast || []).map((row, i) => [row.time, hydrograph.forcing?.precipitation?.[i], row.value])])
  const obs = new Map((hydrograph.obs || []).map(row => [row.time, row.value]))
  const other = comparisons.map(row => new Map((row.hydrograph.forecast || []).map(p => [p.time, p.value])))
  return csv([['时刻', '降雨量 (mm)', '实测流量 (m³/s)', '模拟流量 (m³/s)', ...comparisons.map(row => `${row.label} (m³/s)`)],
    ...(hydrograph.forecast || []).map((row, i) => [row.time, hydrograph.forcing?.precipitation?.[i], obs.get(row.time), row.value, ...other.map(values => values.get(row.time))])])
}

export function summaryRows(task, result, metadata = {}) {
  const periods = resultPeriods(result)
  return (periods.length ? periods : [{ id: '', label: '', hydrograph: {} }]).map(({ id, label, hydrograph: h }) => [
    task.taskId, metadata.name || task.runTypeLabel, metadata.collection || '', task.runTypeLabel, id, label,
    h.forecast?.[0]?.time || result.stateTime, h.forecast?.at(-1)?.time || result.stateTime,
    h.metrics?.nse, h.peak, h.peakTime, h.metrics?.peakErr, h.metrics?.timeErrH, h.metrics?.volErr,
    result.parameterVersionId || result.lineage?.parameterVersionId, result.lineage?.inputBundleVersionId,
    result.routingCandidate?.routingVersionId || result.lineage?.routingVersionId, metadata.notes || '',
  ])
}
export const SUMMARY_HEADER = ['任务编号', '成果名称', '分组', '类型', '时段编号', '时段用途', '开始', '结束', '纳什效率系数 NSE', '模拟洪峰 (m³/s)', '峰现时刻', '洪峰相对误差 (%)', '峰现时间误差 (h)', '洪量相对误差 (%)', '参数版本', '输入版本', '汇流版本', '备注']

export function downloadText(name, contents, type = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([contents], { type }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
