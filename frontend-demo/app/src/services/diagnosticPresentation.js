import { hydrologyText } from './hydrologyPresentation.js'

const labels = {
  REVIEW_REQUIRED: '待复核', PASSED: '通过', FAILED: '未通过',
  MONTHLY_CLIMATOLOGY: '逐月假设蒸散发', ZERO: '零蒸散发假设',
  HOURLY_CSV: '逐时蒸散发资料',
  validation: '验证', independent_test: '独立检验', calibration: '率定',
  'selected value strictly inside bounds': '所选值位于边界内',
  'observed or independently derived PET': '实测或独立推求蒸散发',
}

export function diagnosticValue(value) {
  if (value == null) return '缺测'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '缺测'
    if (value !== 0 && Math.abs(value) < 0.0001) return value.toExponential(2)
    return String(Number(value.toFixed(4)))
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.map(diagnosticValue).join('、')
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${labels[key] || key} ${diagnosticValue(item)}`).join('；')
  return labels[value] || hydrologyText(value)
}

export function diagnosticThreshold(value) {
  return diagnosticValue(value).replace(/^absolute <=\s*/, '绝对值 ≤ ').replace(/^>=\s*/, '≥ ').replace(/^<=\s*/, '≤ ')
}
