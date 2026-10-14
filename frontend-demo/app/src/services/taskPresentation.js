export const taskStatusLabels = {
  queued: '排队', running: '计算中', cancelling: '正在取消', cancelled: '已取消',
  completed: '完成', archived: '已归档', failed: '失败',
}

export function recoverableCalculation(rows, workspace) {
  // An open draft is not a request to reopen the latest historical result.
  if (workspace.activeTaskId) return rows.find(row => row.taskId === workspace.activeTaskId) || null
  if (workspace.modelSchemeId || workspace.modelSchemeName || workspace.dirty) return null
  return [...rows].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0] || null
}

export function filterTasks(rows, { query = '', status = '' } = {}) {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  return rows.filter(row => {
    if (status && row.status !== status) return false
    const text = [row.taskId, row.runTypeLabel, row.stageLabel, row.errorMessage,
      taskStatusLabels[row.status], row.runConfig?.runType].filter(Boolean).join(' ').toLocaleLowerCase()
    return terms.every(term => text.includes(term))
  })
}
