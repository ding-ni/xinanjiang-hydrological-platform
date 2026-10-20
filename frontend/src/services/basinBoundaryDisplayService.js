// A basin boundary is not a set of routing units or a soil-wetness product.
export const EMPTY_SUBBASINS = { type: 'FeatureCollection', features: [] }

export const SUBBASIN_FILL_COLOR = [
  'match', ['get', 'basinIndex'],
  1, 'rgba(218,181,90,0.20)',
  2, 'rgba(82,157,190,0.20)',
  3, 'rgba(122,172,118,0.22)',
  4, 'rgba(181,126,173,0.20)',
  'rgba(170,190,198,0.15)'
]

export function actualSubbasinGeometry(geometry) {
  const features = geometry?.type === 'Feature' ? [geometry] : geometry?.features || []
  return features.some((feature) => ['Polygon', 'MultiPolygon'].includes(feature.geometry?.type))
    ? geometry
    : EMPTY_SUBBASINS
}

export function applyBasinBoundaryVisibility(map, { isSelinco = false, subbasins, enabled = true } = {}) {
  const requested = enabled !== false
  const hasSubbasins = actualSubbasinGeometry(subbasins) !== EMPTY_SUBBASINS
  const visibility = {
    'basin-line': isSelinco || requested,
    'subbasin-fill': requested && hasSubbasins,
    'subbasin-line': requested && hasSubbasins,
    'subbasin-label': isSelinco && requested && hasSubbasins
  }
  for (const [id, visible] of Object.entries(visibility)) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
  }
}
