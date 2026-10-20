export const modelProject = {
  id: 'XAJ-DIST-XAJIANG-01',
  name: '新安江流域分布式预报模型',
  version: 'DIST-XAJ-v3.2',
  status: '生产',
  spatial: {
    domain: '新安江流域',
    grid: '1 km 网格',
    gridCount: 704,
    subbasins: 6,
    dem: '30 m 地形与分级河网',
    note: '网格用于产流计算，子流域用于坡面汇集与河网拓扑组织'
  },
  forcing: {
    observed: '雨水情整编资料',
    observedNc: 'OBS_GRID_1KM.nc',
    forecast: '0.1° 数值预报降雨',
    forecastNc: 'QPF_EC_0P1DEG.nc',
    downscaledNc: 'QPF_EC_1KM.nc',
    summary: '0.1° 预报降水转换至 1 km 计算尺度；流域面雨量用于资料质控与成果摘要'
  },
  runoff: {
    method: '网格新安江蓄满产流',
    separation: '自由水蓄水库三水源划分',
    params: '分区参数 · 子流域继承，网格修正'
  },
  routing: {
    hillslope: '线性水库坡面汇流',
    gridToRiver: '沿地形汇流路径汇入河道',
    channel: '马斯京根分河段演算',
    outlet: '控制断面流量过程'
  },
  checkpoint: {
    time: '2026-06-02 08:00',
    version: 'STATE-20260602-0800',
    nextIssue: '2026-06-02 14:00',
    catchup: '08:00–14:00 实测驱动续算'
  },
  calibration: {
    sqlDataset: 'hydro_observation_v202606',
    ncDataset: 'forcing_history_2018_2024_1km.nc',
    period: '2018-01-01–2022-12-31',
    validation: '2023-01-01–2024-12-31',
    warmup: '90 天',
    mode: '连续率定 + 独立洪水场次检验'
  }
}

export const rainfallProducts = [
  { id: 'ecmwf', name: 'ECMWF 确定性预报', cycle: '2026-06-02 12:00', rawNc: 'QPF_EC_2026060212_0P1DEG.nc', nc: 'QPF_EC_2026060212_1KM.nc' },
  { id: 'cma', name: 'CMA-GFS 预报', cycle: '2026-06-02 12:00', rawNc: 'QPF_CMA_2026060212_0P1DEG.nc', nc: 'QPF_CMA_2026060212_1KM.nc' },
  { id: 'no-rain', name: '无后续降雨', cycle: '—', rawNc: 'ZERO_QPF_0P1DEG.nc', nc: 'ZERO_QPF_1KM.nc' }
]

export const runPreparation = [
  { key: 'sync', label: '资料入库', value: '外部资料源 → 水雨情资料库', status: '完成' },
  { key: 'grid', label: '降水场', value: '实测与预报降水已更新', status: '完成' },
  { key: 'state', label: '状态续算', value: '08:00 已保存状态续算至 14:00', status: '完成' },
  { key: 'model', label: '生产方案', value: 'DIST-XAJ-v3.2', status: '已锁定' }
]
