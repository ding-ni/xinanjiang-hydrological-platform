export const DEFAULT_PARAMETER_VALUES = Object.freeze({
  K: 0.72, B: 0.35, C: 0.15, WM: 120, SM: 35, EX: 1.2, KI: 0.35, KG: 0.35, CS: 0.18, L: 2,
  KH: 0.55, MSK_K: 3.5, MSK_X: 0.22,
  TSNOW: 0.5, DDM: 2.8, ICE_MELT: 1.5, FROST_WM: 0.72, LAKE_K: 0.08, LAKE_A: 0.65
})

export const PARAMETER_VERSION_CATALOG = Object.freeze([
  {
    id: 'XAJ-ZJ-v3.2', basinProjectId: 'legacy-xinanjiang-fixture', resolutionId: '1km',
    publishedAt: '2026-03-12', section: '新安江大桥', nse: 0.86, approvedBy: '兼容样例', status: 'compatibility', overrides: {}
  },
  {
    id: 'XAJ-ZJ-v3.1', basinProjectId: 'legacy-xinanjiang-fixture', resolutionId: '1km',
    publishedAt: '2025-11-08', section: '新安江大桥', nse: 0.83, approvedBy: '兼容样例', status: 'compatibility',
    overrides: { K: 0.7, B: 0.32, C: 0.14, WM: 115, SM: 32, EX: 1.15, KI: 0.33, KG: 0.34, CS: 0.2, L: 2.5, KH: 0.62, MSK_K: 4, MSK_X: 0.2 }
  },
  {
    id: 'XAJ-ZJ-v2.8', basinProjectId: 'legacy-xinanjiang-fixture', resolutionId: '1km',
    publishedAt: '2024-06-20', section: '兰江口', nse: 0.79, approvedBy: '兼容样例', status: 'compatibility',
    overrides: { K: 0.68, B: 0.3, C: 0.12, WM: 110, SM: 30, EX: 1.1, KI: 0.3, KG: 0.32, CS: 0.22, L: 3, KH: 0.7, MSK_K: 4.5, MSK_X: 0.18 }
  },
  {
    id: 'XAJ-SEL-v1.0', basinProjectId: 'selinco', resolutionId: '1km',
    publishedAt: '2026-07-21', section: '四个入湖断面', nse: 0.85, approvedBy: '试算目录', status: 'frontend-fixture',
    overrides: { WM: 108, SM: 28, K: 0.68, B: 0.31, TSNOW: 0.5, DDM: 3.2, ICE_MELT: 1.8, FROST_WM: 0.68, LAKE_K: 0.08, LAKE_A: 0.65 }
  },
  {
    id: 'XAJ-SEL-0P1-v1.0', basinProjectId: 'selinco', resolutionId: '0p1deg',
    publishedAt: '2026-07-21', section: '四个入湖断面', nse: 0.82, approvedBy: '试算目录', status: 'frontend-fixture',
    overrides: { WM: 116, SM: 31, K: 0.7, B: 0.33, TSNOW: 0.4, DDM: 2.9, ICE_MELT: 1.5, FROST_WM: 0.74, LAKE_K: 0.07, LAKE_A: 0.7 }
  }
])

export const PARAMETER_PRESETS = Object.freeze(Object.fromEntries(
  PARAMETER_VERSION_CATALOG.map((version) => [version.id, { ...DEFAULT_PARAMETER_VALUES, ...version.overrides }])
))
