export function validateProjectDataContext(projectData) {
  const errors = []
  const warnings = []
  if (!projectData?.manifest?.id) errors.push('缺少流域项目标识。')
  if (!projectData?.resolution?.id && !projectData?.resolution?.label) errors.push('缺少空间分辨率标识。')
  if (!projectData?.spatialAssets?.basin) errors.push('缺少流域边界。')
  if (!projectData?.spatial?.terrain) errors.push('缺少DEM与有效网格配置。')
  const datasets = projectData?.datasets?.datasets || []
  if (!datasets.some((item) => item.type?.includes('precipitation'))) warnings.push('尚未登记降雨数据集。')
  if (!datasets.some((item) => item.type?.includes('flow') || item.type?.includes('river'))) warnings.push('尚未登记出口水文数据集。')
  const mappingIds = new Set((projectData?.fieldMapping?.standardProducts || []).map((item) => item.id))
  const requiredProducts = projectData?.manifest?.requiredStandardProducts
    || projectData?.manifest?.validation?.requiredStandardProducts
    || []
  requiredProducts.forEach((id) => {
    if (!mappingIds.has(id)) warnings.push(`资料字段清单缺少 ${id}。`)
  })
  return { ok: errors.length === 0, errors, warnings }
}

export function standardProductFor(projectData, productId) {
  return (projectData?.fieldMapping?.standardProducts || []).find((item) => item.id === productId) || null
}
