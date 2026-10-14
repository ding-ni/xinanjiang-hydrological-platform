// Human labels only. Never apply this to version IDs or API payloads.
const qualityReasons = {
  WARMUP_EVENT_RAIN_BELOW_MINIMUM: '预热与洪水时段累计降雨不足',
  DURATION_EXCEEDS_MAXIMUM: '洪水历时超过现行检查范围', FLOW_RECORD_TOO_SHORT: '有效雨流配对时段不足',
  PRECIPITATION_INCOMPLETE: '降雨资料不完整', PRECIPITATION_FILE_MISSING: '缺少降雨资料',
  SOURCE_STATIONS_BELOW_MINIMUM: '有效雨量站不足', SOURCE_STATION_COUNT_UNAVAILABLE: '缺少有效雨量站数量记录',
  RUNOFF_PRECIPITATION_RATIO_EXCEEDS_MAXIMUM: '实测径流与降雨比例异常',
  OBSERVED_COVERAGE_BELOW_MINIMUM: '实测流量覆盖不足', OBSERVED_MISSING_GAP_EXCEEDS_MAXIMUM: '实测流量连续缺测过长',
  NO_VALID_OBSERVED_FLOW: '缺少有效实测流量', OBSERVED_PHASE_COVERAGE_BELOW_MINIMUM: '涨水、洪峰或退水段实测流量不足',
  CATALOG_RAIN_INCOMPLETE: '目录记录的降雨资料不完整', CATALOG_FLOW_INCOMPLETE: '目录记录的流量资料不完整',
  YEAR_OUTSIDE_ALLOWED_RANGES: '洪水年份不在所选资料覆盖内', CROSS_YEAR_EVENT: '跨年洪水需检查连续资料',
  SINGLE_STATION_DOMINATED: '主要依赖单个雨量站', PEAK_NEAR_EVENT_START: '洪峰接近场次起点',
  EVENT_END_FLOW_REMAINS_HIGH: '场次结束时流量仍偏高'
}
export function hydrologyText(value) {
  return String(value || '')
    .replace(/\b[A-Z][A-Z0-9_]{3,}\b/g, code => qualityReasons[code] || code)
    .replace(/\bOBSERVED_FLOW_ANCHORED\b/g, '按实测流量初始化')
    .replace(/\bQC\b/g, '质量检查').replace(/\bIDW\b/g, '距离反比加权插值').replace(/\bPET\b/g, '潜在蒸散发')
}

export function hydrologyNumber(value, digits = 2) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function parameterSchemeLabel(versionId, record = null) {
  const id = String(versionId || '')
  if (id === 'NANANXI_XAJ_REFERENCE_V1') return '新安江参考参数'
  const provenance = record?.importProvenance
  const kind = record?.candidateKind || provenance?.candidateKind
  if (provenance?.sourceTaskId && ['frozen-calibration', 'manual-edit'].includes(kind)
    && (!provenance.candidateKind || provenance.candidateKind === kind)) {
    const plan = record?.originalCalibrationPlan
    const source = plan?.planId && plan.sourceTaskId === provenance.sourceTaskId ? '导入原方案' : '导入候选'
    return `${source} · ${kind === 'frozen-calibration' ? '冻结率定候选' : '人工修改候选'} · ${id.slice(-8)}`
  }
  return id.startsWith('PARAM-JOB-') ? `率定参数 · ${id.slice(-8)}` : id
}
