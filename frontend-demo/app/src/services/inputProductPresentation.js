import { DEFAULT_INPUT_BUNDLE_VERSION_ID } from './inputProductService.js'
import { hydrologyText } from './hydrologyPresentation.js'

const SOURCE_QC = 'SOURCE_ORIGINAL_20261011'
const SOURCE_TARGET = 'LEGACY_CLOSED_RING_VERTEX_MEAN'
const sha256 = (value) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const timeText = (value) => String(value).replace('T', ' ').slice(0, 16)

function databaseDigest(record) {
  return record.sourceOrigin?.databaseSHA256 || record.databaseSHA256
}

function originalBinding(record, parameterVersions) {
  return parameterVersions.some((parameter) => {
    const plan = parameter.originalCalibrationPlan
    const requiredId = parameter.requiredInputBundleVersionId || parameter.inputBundleVersionId || parameter.lineage?.inputBundleVersionId
    const kind = parameter.candidateKind || parameter.importProvenance?.candidateKind
    return requiredId === record.bundleVersionId && ['frozen-calibration', 'manual-edit'].includes(kind)
      && plan?.planId && plan.sourceTaskId === parameter.importProvenance?.sourceTaskId
      && plan.sourceMDBSHA256 === databaseDigest(record)
      && plan.qcConfigurationSHA256 === record.eventQualityConfigurationSHA256
      && plan.sourceTaskId === record.sourceQcOrigin?.sourceTaskId
  })
}

function presentationKind(record, parameterVersions) {
  if (record.bundleVersionId === DEFAULT_INPUT_BUNDLE_VERSION_ID && record.operation === 'fixedBaseline') return 'historical'
  const mdb = record.operation === 'mdbImport' && record.sourceOrigin?.type === 'mdb-snapshot'
    && sha256(databaseDigest(record))
  if (!mdb) return 'registered'
  const replay = record.sourceReplayOnly === true && record.targetCoordinatePolicy === SOURCE_TARGET
    && record.forcingValueDtype === 'float64'
  const sourceRules = replay && record.sourceQcPolicy === SOURCE_QC
    && sha256(record.eventQualityConfigurationSHA256)
    && record.sourceQcOrigin?.sha256 === record.eventQualityConfigurationSHA256
    && !!record.sourceQcOrigin?.sourceTaskId
  // An supplied candidate list must confirm the complete original-plan binding.
  // Names, registration order and dates never establish a matching scheme.
  if (sourceRules && (parameterVersions == null || originalBinding(record, parameterVersions))) return 'source-scheme'
  if (replay && record.sourceQcPolicy !== SOURCE_QC) return 'legacy-comparison'
  if (record.sourceReplayOnly !== true && (!record.targetCoordinatePolicy || record.targetCoordinatePolicy === 'NC_GRID_AXIS_CENTRE')
      && (!record.forcingValueDtype || record.forcingValueDtype === 'float32') && record.sourceQcPolicy !== SOURCE_QC) return 'independent-comparison'
  return 'registered'
}

function period(record, catalog) {
  if (record.recordQueryScope?.start && record.recordQueryScope?.end) {
    return { ...record.recordQueryScope, rangeKind: 'extracted', rangeNote: '本版提取范围；有效时次与缺测另见资料检查。' }
  }
  const matchingCatalog = catalog?.bundleVersionId === record.bundleVersionId ? catalog : null
  const common = matchingCatalog?.coverage?.common
  if (common?.start && common?.end) return { ...common, rangeKind: 'common', rangeNote: '降雨与流量共同起止范围，区间内仍可能缺测。' }
  const coverage = record.coverage?.common || record.coverage?.precipitation || record.coverage || record
  if (coverage.start && coverage.end) {
    return { ...coverage, rangeKind: 'declared', rangeNote: record.bundleVersionId ? '登记时间范围，不能据此认定逐小时资料完整。' : '' }
  }
  const years = (coverage.years || []).filter(Number.isInteger)
  return { years, rangeKind: years.length ? 'calendar-years' : 'unknown', rangeNote: years.length ? '年文件范围；有效时段以资料目录和检查为准。' : '' }
}

/** Human names do not replace immutable version IDs, original names or frozen lineage. */
export function inputProductPresentation(record = {}, { catalog = null, parameterVersions = null } = {}) {
  const kind = presentationKind(record, parameterVersions)
  const names = {
    historical: '历史交付整编资料 · 20260819',
    'source-scheme': `同事终结方案配套资料 · ${record.summary?.eventCount ?? '已登记'}个洪水窗口`,
    'independent-comparison': '终结版MDB独立整编对照',
    'legacy-comparison': '原插值方法整编对照'
  }
  const purposes = {
    historical: '历史资料查询与原交付方案计算。',
    'source-scheme': '复现同事原方案；配套原插值、数值精度及场次筛选规则。',
    'independent-comparison': '独立整编比较，计算条件与同事原方案配套资料不同。',
    'legacy-comparison': '核对原插值方法；场次检查规则与最终配套资料不同。',
    registered: '按本版来源、时段及整编记录采用计算资料。'
  }
  const range = period(record, catalog)
  return {
    kind, name: names[kind] || hydrologyText(record.name) || '整编计算资料', purpose: purposes[kind],
    start: range.start || null, end: range.end || null, rangeKind: range.rangeKind, rangeNote: range.rangeNote,
    range: range.start && range.end ? `${timeText(range.start)} → ${timeText(range.end)}`
      : range.years?.length ? `${Math.min(...range.years)}–${Math.max(...range.years)} 年（年文件范围）` : '提取时段待读取'
  }
}

export function inputVersionViewState(record, selectedVersionId) {
  const selected = !!record?.bundleVersionId && record.bundleVersionId === selectedVersionId
  return { selected, viewingOnly: !!record?.bundleVersionId && !selected }
}
