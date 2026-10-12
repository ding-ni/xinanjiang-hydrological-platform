import { DEFAULT_PARAMETER_VALUES } from '../catalogs/parameter-versions.js'

const defaultParams = { ...DEFAULT_PARAMETER_VALUES }

const legacyProducts = [
  {
    id: 'RUN-20260602-1400', section: '新安江大桥', start: '2026-06-02 14:00', peak: 2850,
    peakTime: '2026-06-03 02:00', peakLevel: 34.2, level: '关注', warnLevel: 34.5, ensureLevel: 36,
    volume: 1.86e8, param: 'XAJ-ZJ-v3.2', state: 'STATE-20260602-1400', data: 'RAIN-20260602-1400',
    status: '已发布', operator: '值班预报员', reviewer: '预报复核员', type: '实时报', leadHours: 24,
    repro: true, publishedAt: '2026-06-02 14:20', wetness: 0.61, force: false, forceNote: '', params: defaultParams,
    fixtureStatus: 'legacy-compatibility'
  },
  {
    id: 'RUN-20260602-0800', section: '新安江大桥', start: '2026-06-02 08:00', peak: 2720,
    peakTime: '2026-06-03 03:00', peakLevel: 33.9, level: '关注', warnLevel: 34.5, ensureLevel: 36,
    volume: 1.74e8, param: 'XAJ-ZJ-v3.2', state: 'STATE-20260602-0800', data: 'RAIN-20260602-0800',
    status: '已发布', operator: '值班预报员', reviewer: '预报复核员', type: '订正报', leadHours: 24,
    repro: true, publishedAt: '2026-06-02 08:35', wetness: 0.55, force: true, forceNote: '水位跳变已复核，采用替代站',
    params: { ...defaultParams, WM: 130, L: 2.5 }, fixtureStatus: 'legacy-compatibility'
  },
  {
    id: 'RUN-20260601-2000', section: '兰江口', start: '2026-06-01 20:00', peak: 1980,
    peakTime: '2026-06-02 18:00', peakLevel: 28.5, level: '关注', warnLevel: 28.2, ensureLevel: 29.8,
    volume: 1.2e8, param: 'XAJ-ZJ-v3.1', state: 'STATE-20260601-2000', data: 'RAIN-20260601-2000',
    status: '待复核', operator: '值班预报员', reviewer: '', type: '会商报', leadHours: 24,
    repro: true, publishedAt: '', wetness: 0.58, force: false, forceNote: '',
    params: { ...defaultParams, K: 0.7, WM: 115 }, fixtureStatus: 'legacy-compatibility'
  }
]

const selincoProducts = [
  ['SEL-LAKE-1KM-20260602-1400', '色林错全湖', '1km', 675, '2026-06-03 10:00', 451, 48],
  ['SEL-LAKE-0P1-20260602-1400', '色林错全湖', '0p1deg', 642, '2026-06-03 11:00', 438, 48],
  ['SEL-1KM-20260602-1400', '扎加藏布入湖口', '1km', 264, '2026-06-03 08:00', 186, 24],
  ['SEL-0P1-20260602-1400', '扎加藏布入湖口', '0p1deg', 251, '2026-06-03 09:00', 181, 24],
  ['SEL-1KM-20260602-0800', '扎根藏布入湖口', '1km', 171, '2026-06-03 10:00', 112, 24],
  ['SEL-0P1-20260601-2000', '阿里藏布入湖口', '0p1deg', 106, '2026-06-02 18:00', 67, 24]
].map(([id, section, modelScale, peak, peakTime, currentFlow, leadHours], index) => ({
  id, section, modelScale, domainId: 'selinco',
  start: index === 5 ? '2026-06-01 20:00' : '2026-06-02 14:00',
  peak, peakTime, currentFlow, leadHours, peakLevel: 4530.42, level: '关注', warnLevel: 4531.1,
  ensureLevel: 4531.6, volume: 1.12e8,
  param: modelScale === '1km' ? 'XAJ-SEL-v1.0' : 'XAJ-SEL-0P1-v1.0',
  state: `STATE-SEL-${modelScale === '1km' ? '1KM' : '0P1'}-20260602-1400`,
  data: `selinco_precip_hourly_${modelScale}.nc`, status: index === 4 ? '待复核' : '已发布',
  operator: '值班预报员', reviewer: index === 4 ? '' : '预报复核员', type: '实时报', repro: true,
  publishedAt: index === 4 ? '' : '2026-06-02 14:20', wetness: 0.62, force: false, forceNote: '',
  params: defaultParams, fixtureStatus: 'frontend-fixture'
}))

export const FORECAST_PRODUCT_FIXTURES = Object.freeze([...selincoProducts, ...legacyProducts])
