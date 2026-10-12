import { fixtureAdapter, isFixtureRunConfig, isFixtureTask } from './adapters/fixtureAdapter.js'
import { httpAdapter } from './adapters/httpAdapter.js'
import { modelBackendEnabled } from './modelBackendService.js'

let adapter = fixtureAdapter

export function useTaskAdapter(name = 'fixture') {
  adapter = name === 'http' ? httpAdapter : fixtureAdapter
}

export const taskService = {
  listOptions(context) {
    return adapter.listOptions(context)
  },
  submitTask(runType, runConfig) {
    const basin = runConfig.context?.projectId || runConfig.domainId
    if (basin === 'nananxi-xufan') return httpAdapter.submitTask(runType, runConfig)
    if (!isFixtureRunConfig(runConfig)) throw new Error('当前案例未接入计算服务；演示任务仅允许完整的色林错试算上下文。')
    return adapter.submitTask(runType, runConfig)
  },
  getTask(taskId) {
    return taskId.startsWith('JOB-') ? httpAdapter.getTask(taskId) : fixtureAdapter.getTask(taskId)
  },
  async listTasks() {
    if (adapter === httpAdapter) return httpAdapter.listTasks()
    const fixtures = (await fixtureAdapter.listTasks()).filter(isFixtureTask)
    if (!modelBackendEnabled()) return fixtures
    return [...fixtures, ...await httpAdapter.listTasks()]
  },
  cancelTask(taskId) {
    return taskId.startsWith('JOB-') ? httpAdapter.cancelTask(taskId) : fixtureAdapter.cancelTask(taskId)
  },
  getResult(taskId) {
    return taskId.startsWith('JOB-') ? httpAdapter.getResult(taskId) : fixtureAdapter.getResult(taskId)
  },
  archiveTask(taskId) {
    return taskId.startsWith('JOB-') ? httpAdapter.archiveTask(taskId) : fixtureAdapter.archiveTask(taskId)
  },
  cloneTask(taskId) {
    return taskId.startsWith('JOB-') ? httpAdapter.cloneTask(taskId) : fixtureAdapter.cloneTask(taskId)
  },
  subscribe(listener) {
    return adapter.subscribe(listener)
  }
}

