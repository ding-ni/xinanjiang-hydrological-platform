// Human labels only. Never apply this to version IDs or API payloads.
export function hydrologyText(value) {
  return String(value || '')
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
