export function searchCoordinates(params) {
  const total = Number(params.KI) + Number(params.KG)
  return { ...params, K_total_day: 1 - (1 - total) ** 24, groundwater_fraction: total > 0 ? params.KG / total : 0 }
}

export function selectedSearchParameters(definitions, keys = null) {
  return Object.fromEntries(definitions.filter(row => keys == null || keys.includes(row.key))
    .map(row => [row.key, { minimum: row.minimum, maximum: row.maximum }]))
}

export function searchSelectionError(selection, definitions, params, strategy) {
  if (selection == null) return ''
  if (typeof selection !== 'object' || Array.isArray(selection)) return '请选择已登记的率定参数'
  const keys = Object.keys(selection)
  if (!keys.length) return '请至少选择一个率定参数，或使用人工试算'
  const current = searchCoordinates(params)
  for (const key of keys) {
    const definition = definitions.find(row => row.key === key)
    if (!definition) return `${key} 未登记为搜索参数`
    const bounds = selection[key]
    if (!bounds || typeof bounds !== 'object' || Object.keys(bounds).sort().join(',') !== 'maximum,minimum') return `${definition.symbol} 需要完整的搜索下限和上限`
    const { minimum: lo, maximum: hi } = bounds
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo >= hi) return `${definition.symbol} 下限须小于上限`
    if (lo < definition.limitMin || hi > definition.limitMax) return `${definition.symbol} 范围须在 ${definition.limitMin}–${definition.limitMax} 内`
    if (!Number.isFinite(current[key]) || current[key] < lo || current[key] > hi) return `${definition.symbol} 当前参数须在搜索范围内`
  }
  if (strategy === 'staged' && !['storage', 'sources'].every(group => definitions.some(row => row.group === group && keys.includes(row.key)))) {
    return '单组参数请使用联合搜索；分阶段须同时选择蓄水产流与水源划分参数'
  }
  return ''
}
