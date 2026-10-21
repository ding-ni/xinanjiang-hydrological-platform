import { modelApi } from './modelBackendService'
import { resultsPurged } from './resultRetentionService.js'

export function cleanupSummary(plan) {
  return `${plan.taskCount || 0} 个任务目录、${plan.stateCount || 0} 个状态文件、${plan.operationCount || 0} 个操作目录，共 ${(Number(plan.bytes || 0) / 1048576).toFixed(2)} MiB`
}
export function cleanupConfirmation(plan) {
  if (plan.protected?.length) {
    const first = plan.protected[0]
    throw new Error(`仍被引用，未清理：${first.relativePath}；${first.reason}`)
  }
  return `将以下 ${cleanupSummary(plan)} 移入 Windows 回收站，可从系统回收站恢复。\n${plan.artifacts.map(row => row.relativePath).join('\n')}`
}
export function previewResultCleanup(id) {
  return modelApi(`result-retention/${encodeURIComponent(id)}/deletion-preview`, { timeoutMs: 60000 })
}
export async function executeResultCleanup(id, plan) {
  const result = await modelApi(`result-retention/${encodeURIComponent(id)}/purge`, {
    method: 'POST', body: { revision: plan.revision, previewSHA256: plan.previewSHA256 }, timeoutMs: 60000,
  })
  resultsPurged(result)
  return result
}
