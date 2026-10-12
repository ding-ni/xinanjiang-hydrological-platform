// Explicit opt-in: the standalone demo never silently calls a model service.
export const modelBackendEnabled = () => String(import.meta.env?.VITE_ENABLE_MODEL_BACKEND || '').toLowerCase() === 'true'
  && Boolean(String(import.meta.env?.VITE_MODEL_API_BASE || '').trim())

export async function modelApi(path, { method = 'GET', body, timeoutMs = 15000 } = {}) {
  if (!modelBackendEnabled()) throw new Error('模型服务未启用，请使用统一平台入口。')
  const base = String(import.meta.env.VITE_MODEL_API_BASE).replace(/\/$/, '')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${base}/${path}`, {
      method, signal: controller.signal,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.error || `模型接口失败（HTTP ${response.status}）`)
    return payload
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('模型服务响应超时；任务可能仍在执行，请在任务中心刷新。')
    if (error instanceof TypeError) throw new Error('无法连接模型服务。')
    throw error
  } finally {
    clearTimeout(timer)
  }
}

export function uniqueRequestId(prefix = 'REQ') {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`
}

export function normalizeBackendTask(task) {
  const request = task.runConfig || {}
  const context = request.context || {}
  const period = request.period || {}
  return {
    ...task, request,
    runConfig: {
      ...request,
      domainId: context.projectId, modelScale: context.resolutionId,
      modelConfigId: context.modelConfigId, contextKey: context.contextKey,
      sectionId: '70115800', sectionName: '徐畈（二）',
      startTime: request.issue?.issueTime || period.startTime || period.calibrationStart,
      endTime: period.endTime || period.calibrationEnd,
      issueTime: request.issue?.issueTime, leadHours: request.issue?.leadHours,
      params: request.parameters || request.calibration?.initialParameters,
      warmupDays: period.warmupDays, stateVersionId: period.initialStateVersionId,
      targetTime: ['stateInitialization', 'stateContinuation'].includes(request.runType) ? period.endTime : undefined,
      parameterVersionId: request.lineage?.parameterVersionId,
      eventSetVersionId: period.eventSet?.version,
      events: period.events, criteria: request.calibration?.objectives,
      algorithm: request.calibration?.optimizer?.algorithm,
      searchStrategy: request.calibration?.optimizer?.strategy || 'joint',
      searchParameters: request.calibration?.searchParameters ?? null,
      calibrationTarget: request.calibration?.target || 'runoff',
      routingSearchParameters: request.calibration?.routingSearchParameters ?? null,
      searchSeed: request.calibration?.optimizer?.seed ?? 42,
      maxEvaluations: request.calibration?.optimizer?.maxEvaluations,
      populationSize: request.calibration?.optimizer?.populationSize
    }
  }
}
