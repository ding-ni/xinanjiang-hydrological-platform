const timeText = (value) => String(value).replace('T', ' ').slice(0, 16)
const count = (value) => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
const firstCount = (...values) => values.map(count).find(value => value !== null) ?? null
const sameCatalog = (record, catalog) => record.bundleVersionId && catalog?.bundleVersionId === record.bundleVersionId ? catalog : null

function period(record, catalog) {
  if (record.recordQueryScope?.start && record.recordQueryScope?.end) {
    return { ...record.recordQueryScope, rangeKind: 'extracted', rangeNote: '本版实际提取时段；范围内仍可能缺测。' }
  }
  const coverage = record.coverage?.common || record.coverage?.precipitation || record.coverage || record
  if (coverage.start && coverage.end) {
    return { ...coverage, rangeKind: 'declared', rangeNote: record.bundleVersionId ? '登记时段；有效时次与缺测见资料检查。' : '' }
  }
  const common = sameCatalog(record, catalog)?.coverage?.common
  if (common?.start && common?.end) return { ...common, rangeKind: 'common', rangeNote: '降雨与流量共同起止范围；不表示每个小时资料完整。' }
  const years = (coverage.years || []).filter(Number.isInteger)
  return { years, rangeKind: years.length ? 'calendar-years' : 'unknown', rangeNote: years.length ? '年文件范围；有效时段以资料目录和检查为准。' : '' }
}

/** Workspace records provide names and provenance; no imported scheme is built into the interface. */
export function inputProductPresentation(record = {}, { catalog = null } = {}) {
  const purposes = {
    fixedBaseline: '已登记的网格降雨、出口流量与洪水目录。',
    mdbImport: '从数据库资料导入，并生成网格降雨及配套资料。',
    qcWindow: '从已有网格降雨中提取时段，保留相应配套资料。',
    stationIdw: '由站点雨量空间插值生成网格降雨，沿用所选底板的出口流量与洪水目录。'
  }
  const range = period(record, catalog)
  return {
    kind: Object.hasOwn(purposes, record.operation) ? record.operation : 'registered',
    name: String(record.name || '').trim() || '未命名模型计算资料',
    purpose: purposes[record.operation] || '按登记的来源和时段使用，资料组成见详情。',
    start: range.start || null, end: range.end || null, rangeKind: range.rangeKind, rangeNote: range.rangeNote,
    range: range.start && range.end ? `${timeText(range.start)} → ${timeText(range.end)}`
      : range.years?.length ? `${Math.min(...range.years)}–${Math.max(...range.years)} 年（年文件范围）` : '时段待读取'
  }
}

/** Show counts only when registration, a matching catalog or the exact source supplies evidence. */
export function inputProductContents(record = {}, { catalog = null, sources = [] } = {}) {
  const matched = sameCatalog(record, catalog)
  const descriptor = record.contents || {}
  const source = sources.find(row => row.sourceId && row.sourceId === record.sourceId)
  const gridCount = firstCount(descriptor.gridCount, record.summary?.gridCount, record.gridCount, matched?.gridCount)
  const eventCount = firstCount(descriptor.eventCount, record.summary?.eventCount, record.eventCount,
    Array.isArray(record.events) ? record.events.length : null, Array.isArray(matched?.events) ? matched.events.length : null)
  const acceptedEventCount = firstCount(descriptor.acceptedEventCount, record.summary?.acceptedEventCount)
  const rainStationCount = firstCount(descriptor.rainStationCount,
    Array.isArray(record.recordQueryScope?.rainStations) ? record.recordQueryScope.rainStations.length : null,
    Array.isArray(record.sourceOrigin?.stationIds) ? record.sourceOrigin.stationIds.length : null,
    source?.stationCount)
  const hasGridRainfall = descriptor.hasGridRainfall === true || !!matched?.coverage?.precipitation
  const hasOutletFlow = descriptor.hasOutletFlow === true || !!matched?.coverage?.observedFlow
  const hasEventCatalog = descriptor.hasEventCatalog === true || Array.isArray(matched?.events)
  const flowStationId = descriptor.flowStationId || record.recordQueryScope?.flowStation || matched?.river?.station?.spatialCode || null
  const flowStationName = descriptor.flowStationName || matched?.river?.station?.name || null
  const acceptedText = acceptedEventCount === null ? '通过检查数量未统计' : `按本版规则通过检查 ${acceptedEventCount} 个`
  return {
    gridCount, eventCount, acceptedEventCount, rainStationCount,
    hasGridRainfall, hasOutletFlow, hasEventCatalog,
    rows: [
      { key: 'rain', label: '逐小时网格降雨', value: hasGridRainfall ? `已整编${gridCount === null ? '' : ` · ${gridCount} 个网格`}` : '资料组成待读取', note: '按提取时段保存逐小时网格雨量；洪水目录只索引时段，缺测单独保留。' },
      { key: 'flow', label: '出口实测流量', value: hasOutletFlow ? `已包含${flowStationName || flowStationId ? ` · ${[flowStationName, flowStationId].filter(Boolean).join('（')}${flowStationName && flowStationId ? '）' : ''}` : ''}` : '资料组成待读取', note: '保存逐小时记录，用于历史过程比较和率定，数值仍可能缺测。' },
      { key: 'events', label: '洪水窗口目录', value: hasEventCatalog ? `${eventCount === null ? '已包含' : `${eventCount} 个登记窗口`} · ${acceptedText}` : '目录待读取', note: '保存洪水编号及起止时段；率定、验证用途在作业页选择。' },
      { key: 'stations', label: '站点雨量来源', value: rainStationCount === null ? '未登记站数' : `${rainStationCount} 个来源站`, note: '站点资料是网格雨量的来源，保存来源不会自动生成网格降雨。' }
    ]
  }
}

export function inputVersionRelatedTasks(record = {}, tasks = []) {
  if (!record.bundleVersionId) return []
  return tasks.filter(task => task.registeredBundleVersionId === record.bundleVersionId
    || task.bundleVersionId === record.bundleVersionId || task.productVersionId === record.bundleVersionId
    || !!record.sourceTaskId && task.taskId === record.sourceTaskId)
}

export function inputVersionViewState(record, selectedVersionId) {
  const selected = !!record?.bundleVersionId && record.bundleVersionId === selectedVersionId
  return { selected, viewingOnly: !!record?.bundleVersionId && !selected }
}
