import { DEFAULT_PARAMETER_VALUES, PARAMETER_PRESETS } from '../fixtures/catalogs/parameter-versions.js'

/**
 * 新安江参数响应计算器。
 * 用于浏览器内的预报与率定交互，输出水文过程线及评价指标。
 */

const BASE_PARAM_DEFS = [
  { key: 'K', label: 'K · 蒸发系数', min: 0.5, max: 0.95, step: 0.01, unit: '', duty: false, desc: '↑ 蒸发增强 → 产流略减、洪峰略降' },
  { key: 'B', label: 'B · 蓄水容量曲线指数', min: 0.1, max: 0.5, step: 0.01, unit: '', duty: true, desc: '↑ 易产流 → 洪峰抬升' },
  { key: 'C', label: 'C · 深层蒸发系数', min: 0.05, max: 0.3, step: 0.01, unit: '', duty: false, desc: '↑ 基流背景略降' },
  { key: 'WM', label: 'WM · 张力水容量', min: 80, max: 160, step: 1, unit: 'mm', duty: true, desc: '↑ 初损加大 → 洪峰降低、峰现略晚' },
  { key: 'SM', label: 'SM · 自由水容量', min: 20, max: 50, step: 0.5, unit: 'mm', duty: true, desc: '↑ 地表峰削缓、退水变长' },
  { key: 'EX', label: 'EX · 自由水蓄水曲线', min: 1.0, max: 1.5, step: 0.01, unit: '', duty: false, desc: '↑ 地表响应更快' },
  { key: 'KI', label: 'KI · 壤中流出流系数', min: 0.2, max: 0.5, step: 0.01, unit: '', duty: false, desc: '↑ 退水中段抬升' },
  { key: 'KG', label: 'KG · 地下出流系数', min: 0.2, max: 0.5, step: 0.01, unit: '', duty: false, desc: '↑ 退水尾部抬升' },
  { key: 'CS', label: 'CS · 地表径流消退系数', min: 0.05, max: 0.4, step: 0.01, unit: '', duty: false, desc: '控制网格地表径流退水' },
  { key: 'L', label: 'L · 网格汇流滞时', min: 0, max: 6, step: 0.5, unit: 'h', duty: false, desc: '网格产流到子流域出口的平均滞时' },
  { key: 'KH', label: 'KH · 坡面线性水库系数', min: 0.1, max: 1.2, step: 0.05, unit: '', duty: false, desc: '坡面汇流调蓄系数，增大后峰形更平缓' },
  { key: 'MSK_K', label: 'K河 · 马斯京根传播时间', min: 0.5, max: 12, step: 0.5, unit: 'h', duty: false, desc: '河段洪水波传播时间参数' },
  { key: 'MSK_X', label: 'X河 · 马斯京根权重', min: 0, max: 0.5, step: 0.01, unit: '', duty: false, desc: '河段蓄量权重参数，按河段率定' }
]

export const COLD_LAKE_PARAM_DEFS = [
  { key: 'TSNOW', label: '降雪相态温度', min: -2, max: 3, step: 0.1, unit: '°C', duty: false, group: 'snow', desc: '低于阈值的降水进入积雪模块' },
  { key: 'DDM', label: '度日融雪系数', min: 0, max: 8, step: 0.1, unit: 'mm/°C·d', duty: false, group: 'snow', desc: '按小时折算积雪融水对产流的补给' },
  { key: 'ICE_MELT', label: '冰川融冰系数', min: 0, max: 12, step: 0.1, unit: 'mm/°C·d', duty: false, group: 'snow', desc: '控制冰川消融形成的有效水量' },
  { key: 'FROST_WM', label: '冻土蓄水修正', min: 0.4, max: 1, step: 0.01, unit: '', duty: false, group: 'snow', desc: '修正冻土期有效张力水容量' },
  { key: 'LAKE_K', label: '湖量平衡系数', min: 0.01, max: 0.25, step: 0.01, unit: 'd⁻¹', duty: false, group: 'lake', desc: '控制总入湖流量到湖量变化的响应' },
  { key: 'LAKE_A', label: '湖位响应系数', min: 0.1, max: 2, step: 0.05, unit: 'm/(10⁶m³)', duty: false, group: 'lake', desc: '将湖量变化换算为湖位变化' }
]

export const PARAM_DEFS = [...BASE_PARAM_DEFS, ...COLD_LAKE_PARAM_DEFS]

export const DUTY_PARAM_KEYS = PARAM_DEFS.filter((d) => d.duty).map((d) => d.key)

export const DEFAULT_PARAMS = { ...DEFAULT_PARAMETER_VALUES }

export const VERSION_PRESETS = PARAMETER_PRESETS

function pad(n) {
  return String(n).padStart(2, '0')
}

export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v))
}

/**
 * @param {object} params
 * @param {object} opt
 * @param {number} [opt.wetness=0.61] 张力水饱和度 W/WM，进入初值项
 */
export function simulateHydrograph(params = DEFAULT_PARAMS, opt = {}) {
  const p = { ...DEFAULT_PARAMS, ...params }
  const hours = opt.hours ?? 72
  const basePeak = opt.basePeak ?? 2850
  const start = new Date(opt.start || '2026-06-02T08:00:00')
  const obsHours = opt.obsHours ?? 6
  const observedBeforeStart = opt.observedBeforeStart === true
  const seed = opt.seed ?? 1
  const wetness = clamp(opt.wetness ?? 0.61, 0.15, 0.98)
  const baseFlow = opt.baseFlow ?? (520 + p.KG * 280 + p.KI * 120 + wetness * 80)

  // 状态湿润：高 → 产流增益
  const wetGain = 0.75 + wetness * 0.55

  const runoffGain =
    (1 +
      (p.B - 0.35) * 1.15 +
      (120 - p.WM) / 120 * 0.55 +
      (0.72 - p.K) * 0.45 +
      (p.EX - 1.2) * 0.2) *
    wetGain

  const storageDamp =
    1 - (p.SM - 35) / 80 * 0.35 - (p.CS - 0.18) * 0.9 - (p.C - 0.15) * 0.25

  const peakScale = clamp(runoffGain * storageDamp * (0.92 + seed * 0.04), 0.5, 1.55)
  const peakVal = opt.targetPeak
    ? Math.max(0, (basePeak - baseFlow) / 0.85)
    : basePeak * peakScale

  const basePeakHour = 18 + (seed - 1) * 2
  // 偏湿峰现略早
  const peakHour = clamp(
    basePeakHour + p.L * 0.65 + (p.KH - 0.55) * 2.5 + (p.MSK_K - 3.5) * 0.45 + (p.MSK_X - 0.22) * 2 + (p.CS - 0.18) * 5 + (p.WM - 120) / 40 - (wetness - 0.61) * 4,
    6,
    hours - 4
  )

  const recess = clamp(0.35 + p.KI * 0.45 + p.KG * 0.55, 0.25, 1.2)
  const width = clamp(0.09 + p.SM / 400 + p.CS * 0.12 + (p.KH - 0.55) * 0.025 + (p.MSK_K - 3.5) * 0.002, 0.07, 0.22)

  const obs = []
  const forecast = []
  const prev = []

  let peakTimeLabel = ''
  let maxFc = -Infinity
  let startMarkLabel = null

  const firstHour = observedBeforeStart ? -obsHours : 0
  for (let i = firstHour; i <= hours; i++) {
    const t = new Date(start.getTime() + i * 3600 * 1000)
    const label = `${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:00`
    if (i === 0) startMarkLabel = label
    const x = Math.max(0, i) / hours
    const ph = peakHour / hours

    const gauss = Math.exp(-Math.pow((x - ph) / width, 2))
    const tail = Math.exp(-Math.max(0, i - peakHour) * recess * 0.08)
    const base = baseFlow
    const modelVal = base + peakVal * 0.78 * gauss + peakVal * 0.12 * tail * (1 - gauss * 0.3)
    const observationNoise = opt.domainId === 'selinco' ? Math.max(2, baseFlow * 0.02) : 24
    const historyProgress = (i + obsHours) / Math.max(obsHours, 1)
    const obsVal = observedBeforeStart
      ? (opt.issueFlow != null
        ? opt.issueFlow + (1 - historyProgress) * 18 + Math.sin(i * 0.4 + seed) * 8 * (1 - historyProgress)
        : 700 + 320 * Math.sin(historyProgress * Math.PI * 0.85) + historyProgress * 360 * (0.9 + seed * 0.05) + Math.sin(i * 0.4 + seed) * 30)
      : modelVal * (0.99 + Math.sin(i * 0.42 + seed) * 0.012) + observationNoise * Math.cos(i * 0.21)

    const prevVal =
      base * 0.98 + peakVal * 0.72 * Math.exp(-Math.pow((x - (ph + 0.03)) / (width * 1.05), 2)) + 80
    if ((observedBeforeStart && i <= 0) || (!observedBeforeStart && i <= Math.min(obsHours, hours))) {
      obs.push({ time: label, value: Math.round(Math.max(opt.domainId === 'selinco' ? 0 : 400, obsVal)) })
    }
    // 业务过程线必须在起报时刻连续：最后一个实测点就是第一个预报点。
    const issueFlow = opt.issueFlow ?? obsVal
    const anchoredModel = i > 0 && observedBeforeStart
      ? modelVal + (issueFlow - modelVal) * Math.exp(-i / (opt.anchorHours ?? 6))
      : modelVal
    const fv =
      i < 0
        ? null
        : i === 0 && observedBeforeStart
          ? Math.round(Math.max(0, issueFlow))
          : Math.round(Math.max(0, anchoredModel))
    forecast.push({ time: label, value: fv })
    prev.push({ time: label, value: i < 0 ? null : Math.round(Math.max(400, prevVal)) })

    if (fv != null && fv > maxFc) {
      maxFc = fv
      peakTimeLabel = label
    }
  }

  let vol = 0
  for (let i = 1; i < forecast.length; i++) {
    const a = forecast[i - 1].value
    const b = forecast[i].value
    if (a != null && b != null) vol += ((a + b) / 2) * 3600
  }

  const metrics = computeMetrics(obs, forecast)

  return {
    obs,
    forecast,
    prev,
    peak: maxFc,
    peakTime: peakTimeLabel,
    peakHour: Math.round(peakHour),
    volume: vol,
    metrics,
    params: { ...p },
    startMark: startMarkLabel,
    obsHours,
    wetness,
    engine: 'xaj-parameter-response-v2'
  }
}

export function computeMetrics(obs, sim) {
  const finite = (value) => value != null && value !== '' && Number.isFinite(Number(value))
  const map = new Map(sim.filter((d) => finite(d.value)).map((d) => [d.time, Number(d.value)]))
  const pairs = obs
    .map((o) => {
      const s = map.get(o.time)
      return !finite(o.value) || s == null ? null : { o: Number(o.value), s }
    })
    .filter(Boolean)

  if (pairs.length < 3) {
    return { nse: null, peakErr: null, timeErrH: null, volErr: null, r: null, peakObs: null, peakSim: null }
  }

  const oMean = pairs.reduce((a, p) => a + p.o, 0) / pairs.length
  let num = 0
  let den = 0
  let sumO = 0
  let sumS = 0
  pairs.forEach((p) => {
    num += (p.o - p.s) ** 2
    den += (p.o - oMean) ** 2
    sumO += p.o
    sumS += p.s
  })
  const nse = den === 0 ? null : 1 - num / den
  const sMean = sumS / pairs.length
  let cov = 0
  let vo = 0
  let vs = 0
  pairs.forEach((p) => {
    cov += (p.o - oMean) * (p.s - sMean)
    vo += (p.o - oMean) ** 2
    vs += (p.s - sMean) ** 2
  })
  const r = vo * vs === 0 ? null : cov / Math.sqrt(vo * vs)
  const peakObs = Math.max(...pairs.map((p) => p.o))
  const peakSim = Math.max(...pairs.map((p) => p.s))
  const peakErr = peakObs === 0 ? 0 : ((peakSim - peakObs) / peakObs) * 100
  const volErr = sumO === 0 ? 0 : ((sumS - sumO) / sumO) * 100
  const iObs = pairs.reduce((bi, p, i, arr) => (p.o > arr[bi].o ? i : bi), 0)
  const iSim = pairs.reduce((bi, p, i, arr) => (p.s > arr[bi].s ? i : bi), 0)

  return {
    nse: nse == null ? null : +nse.toFixed(3),
    peakErr: +peakErr.toFixed(1),
    timeErrH: iSim - iObs,
    volErr: +volErr.toFixed(1),
    r: r == null ? null : +r.toFixed(3),
    peakObs: Math.round(peakObs),
    peakSim: Math.round(peakSim)
  }
}

export function scaleGridByParams(cells, params = DEFAULT_PARAMS, wetness = 0.61) {
  const p = { ...DEFAULT_PARAMS, ...params }
  const soilScale = clamp(1 + (p.WM - 120) / 200 - (p.K - 0.72) + (wetness - 0.61) * 0.4, 0.65, 1.35)
  const runoffScale = clamp(
    1 + (p.B - 0.35) * 0.8 + (120 - p.WM) / 150 - (p.SM - 35) / 80 + (wetness - 0.61) * 0.6,
    0.55,
    1.5
  )
  return cells.map((c) => ({
    ...c,
    soil: +clamp(c.soil * soilScale, 0.15, 0.98).toFixed(2),
    freeWater: +(clamp(c.soil * soilScale, 0.15, 0.98) * (p.SM * 1.2)).toFixed(1),
    runoff: +Math.max(0, c.runoff * runoffScale).toFixed(2),
    surface: Math.round(Math.max(0, c.surface * runoffScale))
  }))
}

export function sectionBasePeak(sectionId) {
  return { 'SEC-33000200': 2850, 'SEC-LANJIANG': 1980, 'SEC-SHOUCHANG': 1420 }[sectionId] || 2850
}

/** 阈值 → 预警等级（可单测） */
export function warnLevelFromPeak(peak, peakLevel, th) {
  const warnLevel = th?.warnLevel ?? 34.5
  const ensureLevel = th?.ensureLevel ?? 36
  const warnFlow = th?.warnFlow ?? 2800
  if (peakLevel >= ensureLevel || peak >= warnFlow * 1.15) return '保证'
  if (peakLevel >= warnLevel || peak >= warnFlow) return '警戒'
  if (peakLevel >= warnLevel - 1 || peak >= warnFlow * 0.85) return '关注'
  return '正常'
}

export function formatMetric(m) {
  if (!m) return []
  const finite = (value) => value != null && Number.isFinite(Number(value))
  const signed = (value, unit) => finite(value) ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}${unit}` : '—'
  const rows = [
    { name: 'NSE', value: finite(m.nse) ? Number(m.nse).toFixed(3) : '—', cls: !finite(m.nse) ? '' : m.nse >= 0.8 ? 'ok' : m.nse >= 0.6 ? 'warn' : 'bad' },
    { name: '洪峰相对误差', value: signed(m.peakErr, '%'), cls: !finite(m.peakErr) ? '' : Math.abs(m.peakErr) <= 10 ? 'ok' : 'warn' },
    { name: '峰现误差', value: signed(m.timeErrH, ' h'), cls: !finite(m.timeErrH) ? '' : Math.abs(m.timeErrH) <= 2 ? 'ok' : 'warn' },
    { name: '水量相对误差', value: signed(m.volErr, '%'), cls: !finite(m.volErr) ? '' : Math.abs(m.volErr) <= 10 ? 'ok' : 'warn' }
  ]
  // The native backend does not calculate R. Do not invent it or reject the
  // valid metrics just because a frontend-only metric is absent.
  if (finite(m.r)) rows.push({ name: '相关系数 R', value: Number(m.r).toFixed(3), cls: m.r >= 0.85 ? 'ok' : 'warn' })
  return rows
}

export function paramDiff(a = {}, b = {}) {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  const rows = []
  keys.forEach((k) => {
    if (a[k] !== b[k]) rows.push({ key: k, a: a[k], b: b[k], d: +(Number(b[k]) - Number(a[k]) || 0).toFixed(3) })
  })
  return rows
}
