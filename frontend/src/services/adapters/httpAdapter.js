import { modelApi, normalizeBackendTask } from '../modelBackendService.js'

const taskPath = (id, suffix = '') => `tasks/${encodeURIComponent(id)}${suffix}`

export const httpAdapter = {
  listOptions: () => modelApi('catalog'),
  submitTask: async (_runType, request) => normalizeBackendTask(await modelApi('tasks', { method: 'POST', body: request })),
  getTask: async (id) => normalizeBackendTask(await modelApi(taskPath(id))),
  listTasks: async () => (await modelApi('tasks?includeReplayability=false', { timeoutMs: 60000 })).tasks.map(normalizeBackendTask),
  cancelTask: async (id) => normalizeBackendTask(await modelApi(taskPath(id, '/cancel'), { method: 'POST' })),
  getResult: (id) => modelApi(taskPath(id, '/result')),
  archiveTask: async (id) => normalizeBackendTask(await modelApi(taskPath(id, '/archive'), { method: 'POST' })),
  cloneTask: async (id) => normalizeBackendTask(await modelApi(taskPath(id, '/clone'), { method: 'POST' })),
  subscribe: () => () => {}
}
