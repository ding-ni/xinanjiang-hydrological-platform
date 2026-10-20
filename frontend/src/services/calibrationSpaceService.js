export function searchCoordinates(params) {
  const total = Number(params.KI) + Number(params.KG)
  return { ...params, K_total_day: 1 - (1 - total) ** 24, groundwater_fraction: total > 0 ? params.KG / total : 0 }
}

export function selectedSearchParameters(definitions, keys = null) {
  return Object.fromEntries(definitions.filter(row => !row.unavailable && (keys == null || keys.includes(row.key)))
    .map(row => [row.key, { minimum: row.minimum, maximum: row.maximum }]))
}

export const SEARCH_RANGE_TEMPLATES = {
  project: { WM:[120,240],WUM:[5,30],WLM:[40,100],B:[.05,.8],SM:[8,60],EX:[.5,2.5],K_total_day:[.3,.9],groundwater_fraction:[.02,.8],IM:[.0001,.08] },
  standard: { WM: [120, 200], B: [.1, .4], SM: [10, 50], EX: [1, 1.5], IM: [.01, .04] },
  extended: { B: [.05, 3], SM: [1, 80], EX: [.5, 2.5], IM: [.0001, .1] }
}

export function applySearchRangeTemplate(selection, definitions, template) {
  if (!Object.hasOwn(SEARCH_RANGE_TEMPLATES, template)) throw new Error('范围模板未登记')
  const active = selection == null ? selectedSearchParameters(definitions) : selection
  return Object.fromEntries(Object.keys(active).map(key => {
    const row = definitions.find(item => item.key === key)
    const bounds = SEARCH_RANGE_TEMPLATES[template][key] || (template==='extended'?SEARCH_RANGE_TEMPLATES.project[key]:null)
    return [key, bounds ? { minimum: bounds[0], maximum: bounds[1] } : { minimum: row.minimum, maximum: row.maximum }]
  }))
}

export function routingSearchCoordinates(payload = {}) {
  payload ||= {}
  const xs = [...new Set(Object.values(payload.reach_muskingum || {}).map(row => Number(row.x)))]
  return { ...Object.fromEntries(['RS', 'RI', 'RG'].map(key => [`${key}_k_hours`, payload.default_slope_k_hours?.[key]])),
    muskingum_k_scale: 1, muskingum_x: xs.length === 1 && Number.isFinite(xs[0]) ? xs[0] : null }
}

export function searchSelectionError(selection, definitions, params, strategy, allowEmpty = false) {
  if (selection == null) return ''
  if (typeof selection !== 'object' || Array.isArray(selection)) return '请选择已登记的率定参数'
  const keys = Object.keys(selection)
  if (!keys.length) return allowEmpty ? '' : '请至少选择一个率定参数，或使用人工试算'
  const current = searchCoordinates(params)
  for (const key of keys) {
    const definition = definitions.find(row => row.key === key)
    if (!definition) return `${key} 未登记为搜索参数`
    if (definition.unavailable) return definition.unavailable
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
