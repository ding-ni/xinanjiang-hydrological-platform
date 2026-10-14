import { defineStore } from 'pinia'
import { taskService } from '@/services/taskService'
import { modelBackendEnabled } from '@/services/modelBackendService'
import { useAppStore } from '@/stores/app'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'archived'])
const REMOTE_STORAGE_KEY = 'hydro_remote_tasks_v1'
let unsubscribe = null
let refreshTimer = null

function readRemoteTasks() {
  try {
    return JSON.parse(localStorage.getItem(REMOTE_STORAGE_KEY) || '{}')
  } catch {
    return {}
  }
}

function writeRemoteTasks(tasks) {
  try {
    localStorage.setItem(REMOTE_STORAGE_KEY, JSON.stringify(tasks))
  } catch {
    // 本地存储不可用时仍保留当前会话的任务记录。
  }
}

function remoteRunType(request, fallback) {
  if (fallback === 'forecast') return 'forecast'
  if (request?.runType === 'simulation' || fallback === 'continuousSimulation') return 'continuousSimulation'
  if (request?.calibration?.simulationOnly || fallback === 'eventSimulation') return 'eventSimulation'
  return request?.period?.mode === 'continuous' || fallback === 'continuousCalibration'
    ? 'continuousCalibration'
    : 'eventCalibration'
}

function remoteRunConfig(request = {}) {
  const context = request.context || {}
  const period = request.period || {}
  const calibration = request.calibration || {}
  return {
    ...request,
    domainId: context.projectId,
    modelScale: context.resolutionId,
    modelConfigId: context.modelConfigId,
    sectionId: request.issue?.sectionId || calibration.section || null,
    sectionName: request.issue?.sectionName || calibration.section || null,
    startTime: request.issue?.issueTime || period.startTime || period.calibrationStart || null,
    endTime: request.issue?.forecastEndTime || period.endTime || period.calibrationEnd || null,
    issueTime: request.issue?.issueTime || null,
    leadHours: request.issue?.leadHours || null,
    parameterVersionId: request.inputs?.parameterVersionId || calibration.initialParameterVersionId || null,
    stateVersionId: request.inputs?.stateVersionId || period.initialStateVersionId || null,
    eventSetVersionId: period.eventSet?.version || request.lineage?.eventSetVersionId || null,
    sourceTaskId: request.lineage?.sourceTaskId || null
  }
}

export const useTasksStore = defineStore('tasks', {
  state: () => ({
    tasksById: {},
    activeTaskIds: {},
    hydrated: false,
    connectionError: ''
  }),
  getters: {
    tasks(state) {
      return Object.values(state.tasksById).sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
    },
    taskById: (state) => (taskId) => state.tasksById[taskId] || null,
    tasksForContext(state) {
      return (contextKey) => Object.values(state.tasksById).filter((task) => task.contextKey === contextKey)
    },
    activeTaskFor(state) {
      return (scopeKey) => state.tasksById[state.activeTaskIds[scopeKey]] || null
    }
  },
  actions: {
    async hydrate() {
      if (!unsubscribe) {
        unsubscribe = taskService.subscribe((task) => {
          this.tasksById[task.taskId] = task
        })
      }
      this.tasksById = { ...readRemoteTasks(), ...this.tasksById }
      const tasks = await this.refresh()
      this.hydrated = true
      if (modelBackendEnabled() && typeof window !== 'undefined' && !refreshTimer) {
        const tick = async () => {
          await this.refresh()
          refreshTimer = setTimeout(tick, 2000)
        }
        refreshTimer = setTimeout(tick, 2000)
      }
      return tasks
    },
    async refresh() {
      try {
        const tasks = await taskService.listTasks()
        tasks.forEach((task) => { this.tasksById[task.taskId] = task })
        writeRemoteTasks(Object.fromEntries(this.tasks.filter((task) => task.resultSource === 'model-backend').map((task) => [task.taskId, task])))
        this.connectionError = ''
        const app = useAppStore()
        if (modelBackendEnabled() && app.backendError && !app.backendLoading) await app.refreshBackend()
        return tasks
      } catch (error) {
        this.connectionError = error.message
        useAppStore().markBackendOffline(error.message)
        return []
      }
    },
    async submitTask(runType, runConfig, scopeKey = runType) {
      if (!this.hydrated) await this.hydrate()
      const task = await taskService.submitTask(runType, runConfig)
      this.tasksById[task.taskId] = task
      this.activeTaskIds[scopeKey] = task.taskId
      return task
    },
    async waitForTask(taskId) {
      return new Promise((resolve, reject) => {
        const inspect = async () => {
          try {
            const task = await taskService.getTask(taskId)
            if (task) this.tasksById[task.taskId] = task
            if (!task || TERMINAL.has(task.status)) {
              resolve(task)
              return
            }
            setTimeout(inspect, taskId.startsWith('JOB-') ? 1500 : 60)
          } catch (error) {
            this.connectionError = error.message
            if (taskId.startsWith('JOB-')) useAppStore().markBackendOffline(error.message)
            reject(error)
          }
        }
        inspect()
      })
    },
    async submitAndWait(runType, runConfig, scopeKey = runType) {
      const submitted = await this.submitTask(runType, runConfig, scopeKey)
      const task = await this.waitForTask(submitted.taskId)
      const result = task?.status === 'completed' || task?.status === 'archived'
        ? await taskService.getResult(task.taskId)
        : null
      return { task, result }
    },
    async cancelTask(taskId) {
      const task = await taskService.cancelTask(taskId)
      if (task) this.tasksById[task.taskId] = task
      return task
    },
    async getResult(taskId) {
      return taskService.getResult(taskId)
    },
    trackRemoteTask({ runType = 'forecast', request = {}, task = {}, result = null } = {}) {
      const taskId = task.taskId || task.id || task.runId || request.requestId
      if (!taskId) return null
      const normalizedRunType = remoteRunType(request, runType)
      const status = ['queued', 'running', 'completed', 'failed', 'cancelled', 'archived'].includes(task.status)
        ? task.status
        : task.completedAt || result ? 'completed' : 'queued'
      const existing = this.tasksById[taskId] || {}
      const record = {
        ...existing,
        taskId,
        runType: normalizedRunType,
        runTypeLabel: task.runTypeLabel || {
          forecast: '洪水预报',
          continuousSimulation: '连续模拟',
          continuousCalibration: '连续时段率定',
          eventCalibration: '洪水场次率定'
        }[normalizedRunType] || normalizedRunType,
        contextKey: task.contextKey || request.context?.contextKey,
        runConfig: remoteRunConfig(request),
        status,
        progress: Number(task.progress ?? (status === 'completed' ? 100 : 0)),
        stageLabel: task.stageLabel || (status === 'completed' ? '后端任务完成' : '后端任务已提交'),
        resultSource: 'model-backend',
        resultVersionId: task.resultVersionId || result?.resultVersionId || result?.versionId || null,
        errorMessage: task.errorMessage || '',
        createdAt: task.createdAt || new Date().toISOString().replace('T', ' ').slice(0, 19),
        startedAt: task.startedAt || null,
        completedAt: task.completedAt || (status === 'completed' ? new Date().toISOString().replace('T', ' ').slice(0, 19) : null),
        sourceTaskId: task.sourceTaskId || request.lineage?.sourceTaskId || null,
        lineage: {
          ...(request.lineage || {}),
          ...(task.lineage || {}),
          resultVersionId: task.resultVersionId || result?.resultVersionId || result?.versionId || request.lineage?.resultVersionId || null,
          sourceTaskId: task.sourceTaskId || request.lineage?.sourceTaskId || null
        }
      }
      this.tasksById[taskId] = record
      const saved = readRemoteTasks()
      saved[taskId] = record
      writeRemoteTasks(saved)
      return record
    },
    updateRemoteTask(taskId, partial = {}) {
      const current = this.tasksById[taskId]
      if (!current) return null
      const next = { ...current, ...partial, lineage: { ...(current.lineage || {}), ...(partial.lineage || {}) } }
      this.tasksById[taskId] = next
      const saved = readRemoteTasks()
      saved[taskId] = next
      writeRemoteTasks(saved)
      return next
    },
    async archiveTask(taskId) {
      const task = await taskService.archiveTask(taskId)
      if (task) this.tasksById[task.taskId] = task
      return task
    },
    async cloneTask(taskId, scopeKey = 'clone') {
      const task = await taskService.cloneTask(taskId)
      if (task) {
        this.tasksById[task.taskId] = task
        this.activeTaskIds[scopeKey] = task.taskId
      }
      return task
    }
  }
})
