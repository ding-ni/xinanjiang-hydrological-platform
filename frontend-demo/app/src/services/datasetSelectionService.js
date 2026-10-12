/**
 * Resolve the observed input datasets for the active project and model scale.
 * The active case catalog is the only source of truth. Missing resources do
 * not fall back to another scale or to guessed dataset identifiers.
 */

function datasetRows(projectData) {
  const rows = projectData?.datasets?.datasets
  return Array.isArray(rows) ? rows : []
}

function normalizedScale(modelScale) {
  const value = String(modelScale || '').toLowerCase()
  return value === '0p1deg' || value === '0.1deg' || value === '0.1°' ? '0p1deg' : '1km'
}

function isObserved(dataset) {
  const role = String(dataset?.role || '').toLowerCase()
  const type = String(dataset?.type || '').toLowerCase()
  return role === 'observation' || type.includes('observed') || type.includes('observation')
}

function scaleMatches(dataset, scale) {
  const haystack = [
    dataset?.id,
    dataset?.name,
    dataset?.resolution,
    dataset?.resolutionId,
    dataset?.scale,
    dataset?.filePattern,
    dataset?.file,
    dataset?.preview
  ].filter(Boolean).join(' ').toLowerCase()
  if (scale === '0p1deg') return haystack.includes('0p1deg') || haystack.includes('0.1') || haystack.includes('0.1°')
  return haystack.includes('1km') || haystack.includes('1 km')
}

export function selectObservedPrecipitationDataset(projectData, modelScale = '1km') {
  const scale = normalizedScale(modelScale)
  const rows = datasetRows(projectData)
  const grid = rows.find((dataset) => {
    const type = String(dataset?.type || '').toLowerCase()
    return isObserved(dataset) && type.includes('precipitation') && type.includes('grid') && scaleMatches(dataset, scale)
  })
  if (grid) return grid

  const observed = rows.find((dataset) => {
    const type = String(dataset?.type || '').toLowerCase()
    return isObserved(dataset) && type.includes('precipitation') && scaleMatches(dataset, scale)
  })
  if (observed) return observed

  // Unscaled station observations may be used by either grid. A dataset
  // explicitly belonging to the opposite grid must never be substituted.
  return rows.find((dataset) => {
    const type = String(dataset?.type || '').toLowerCase()
    return isObserved(dataset) && type.includes('precipitation') && !type.includes('grid')
      && !scaleMatches(dataset, '1km') && !scaleMatches(dataset, '0p1deg')
  }) || null
}

export function selectObservedHydrologyDataset(projectData) {
  const rows = datasetRows(projectData)
  return rows.find((dataset) => {
      const type = String(dataset?.type || '').toLowerCase()
      return isObserved(dataset) && type.includes('outlet')
    })
    || rows.find((dataset) => {
      const type = String(dataset?.type || '').toLowerCase()
      return isObserved(dataset) && (type.includes('outlet') || type.includes('river') || type.includes('flow'))
    })
    || rows.find((dataset) => {
      const type = String(dataset?.type || '').toLowerCase()
      return type.includes('outlet') || type.includes('river') || type.includes('flow')
    })
    || null
}

export { datasetRows, normalizedScale }
