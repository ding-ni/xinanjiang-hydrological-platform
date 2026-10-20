const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null

export function windowStation(preview, code, timeIndex = 0) {
  const times = preview?.times || []
  const index = Math.max(0, Math.min(timeIndex, times.length - 1))
  const rain = preview?.rain?.[code]
  const river = preview?.river?.spatialCode === code ? preview.river : null
  let cumulative = 0, complete = true
  const rainSeries = rain ? times.map((time, i) => {
    const value = finite(rain[i]); complete &&= value != null
    if (value != null) cumulative += value
    return { time, label: time.slice(5).replace('T', ' '), rain: value, cum: complete ? cumulative : null }
  }) : []
  const series = (values, key) => times.map((time, i) => ({ time, label: time.slice(5).replace('T', ' '), [key]: finite(values?.[i]) }))
  return {
    observationReady: rainSeries.some(row => row.rain != null) || Boolean(river?.flows?.some(value => finite(value) != null)),
    hasRainObservation: rainSeries.some(row => row.rain != null), hasRiverObservation: Boolean(river),
    rain1h: finite(rain?.[index]), rainTotal: rainSeries[index]?.cum ?? null,
    flow: finite(river?.flows?.[index]), level: finite(river?.levels?.[index]),
    rainSeries, flowSeries: river ? series(river.flows, 'flow') : [], levelSeries: river ? series(river.levels, 'level') : [],
    lastTime: times[index]?.replace('T', ' '), sourceType: preview?.sourceType || 'database-observed',
    quality: times.length ? '历史实测记录' : '尚未载入资料', sourceLabel: '水文资料库', interval: '逐小时'
  }
}

export function observationProcess(preview, summary, rainStation = '') {
  const times = preview?.times || []
  const values = rainStation ? preview?.rain?.[rainStation] || [] : summary?.arealRainfallMm || []
  const rain = times.map((_, index) => finite(values[index]))
  return { times, rain, flow: times.map((_, i) => finite(preview?.river?.flows?.[i])),
    total: rain.length && rain.every(value => value != null) ? rain.reduce((sum, value) => sum + value, 0) : null }
}
