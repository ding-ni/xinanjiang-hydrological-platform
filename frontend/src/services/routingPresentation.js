export const slopeComponents = [
  { key: 'RS', label: '地表径流' },
  { key: 'RI', label: '壤中径流' },
  { key: 'RG', label: '地下径流' },
]

export function effectiveSlopeK(parameters, unitId, component) {
  return parameters?.unit_component_slope_k_hours?.[unitId]?.[component]
    ?? parameters?.default_slope_k_hours?.[component]
}

export function setUnitOverride(parameters, unitId, component, enabled) {
  const overrides = parameters.unit_component_slope_k_hours
  if (enabled) {
    overrides[unitId] ??= {}
    overrides[unitId][component] = effectiveSlopeK(parameters, unitId, component)
  } else if (overrides[unitId]) {
    delete overrides[unitId][component]
    if (!Object.keys(overrides[unitId]).length) delete overrides[unitId]
  }
}

export function filterRoutingRows(rows, query, fields) {
  const term = query.trim().toLowerCase()
  return term ? rows.filter(row => fields.some(field => String(row[field] ?? '').toLowerCase().includes(term))) : rows
}

export function routingIsStable(row) {
  const k = row?.k_hours, x = row?.x
  return Number.isFinite(k) && Number.isFinite(x) && k >= 0.5 && k <= 720 && x >= 0 && x <= 0.5
    && 2 * k * x <= 1 && 1 <= 2 * k * (1 - x)
}

export function routingMapCoordinates(product) {
  const points = []
  function visit(coordinates) {
    if (!Array.isArray(coordinates)) return
    if (coordinates.length >= 2 && Number.isFinite(coordinates[0]) && Number.isFinite(coordinates[1])) {
      points.push(coordinates.slice(0, 2))
    } else coordinates.forEach(visit)
  }
  for (const collection of [product?.basin, product?.geometry]) {
    for (const feature of collection?.features || []) visit(feature.geometry?.coordinates)
  }
  for (const reach of product?.topology?.reaches || []) {
    visit(reach.startLonLat)
    visit(reach.endLonLat)
  }
  return points
}
