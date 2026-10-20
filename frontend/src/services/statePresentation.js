export function stateName(row, metadata = {}) {
  return metadata[row.id]?.name || `${row.source || '模型状态'} · ${row.id.slice(-8)}`
}

export function filterStates(rows, metadata = {}, { query = '', favorites = false, strategy = '' } = {}) {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  return rows.filter(row => {
    if (favorites && !metadata[row.id]?.favorite) return false
    if (strategy && (row.checkpoint?.importProvenance ? 'IMPORTED' : row.checkpoint?.initialization?.strategy || 'MODEL_END_STATE') !== strategy) return false
    const haystack = [stateName(row, metadata), row.id, row.time, row.source, row.parameterVersionId].join(' ').toLocaleLowerCase()
    return terms.every(term => haystack.includes(term))
  }).sort((a, b) => Number(Boolean(metadata[b.id]?.favorite)) - Number(Boolean(metadata[a.id]?.favorite)) || b.time.localeCompare(a.time))
}

export function checkpointCsv(checkpoint) {
  const fields = Object.keys(checkpoint.land)
  const quote = value => `"${String(value ?? '').replaceAll('"', '""')}"`
  const rows = [['grid_id', 'time', ...fields], ...checkpoint.gridIds.map((id, index) => [id, checkpoint.time, ...fields.map(field => checkpoint.land[field][index])])]
  return '\uFEFF' + rows.map(row => row.map(quote).join(',')).join('\r\n')
}
