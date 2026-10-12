import { modelApi } from './modelBackendService.js'

const taskRoot = (id) => {
  if (!/^JOB-[A-Za-z0-9_-]+$/.test(String(id))) throw new Error('仅真实任务提供已保存的网格诊断。')
  return `tasks/${encodeURIComponent(id)}`
}
export const loadGridHistoryCatalog = (id) => modelApi(`${taskRoot(id)}/grid-state/catalog`)
export const loadGridStateFrame = (id, time) => modelApi(`${taskRoot(id)}/grid-state?time=${encodeURIComponent(time)}`)
export const loadGridStateSeries = (id, unitId) => modelApi(`${taskRoot(id)}/grid-state?unitId=${encodeURIComponent(unitId)}`)
export const loadTaskEventQuality = (id) => modelApi(`${taskRoot(id)}/event-quality`)
