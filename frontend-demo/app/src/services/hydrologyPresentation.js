// Human labels only. Never apply this to version IDs or API payloads.
export function hydrologyText(value) {
  return String(value || '')
    .replace('交付固定 QC 20260819 / 控制切分 v2', '交付整编资料 20260819 / 徐畈控制断面 v2')
    .replace('合作者交付固定 QC；参考诊断资料，未获得生产资格', '合作者交付的整编计算资料')
    .replace(/\bOBSERVED_FLOW_ANCHORED\b/g, '按实测流量初始化')
    .replace(/\bQC\b/g, '质量检查').replace(/\bIDW\b/g, '距离反比加权插值').replace(/\bPET\b/g, '潜在蒸散发')
}

export function hydrologyNumber(value, digits = 2) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
