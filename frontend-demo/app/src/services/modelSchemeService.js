import { modelApi, normalizeBackendTask, uniqueRequestId } from './modelBackendService'

export function schemeDefinition(request, rainfallMethod = 'reuse') {
  const period = request.period.mode === 'event' ? { mode: 'event', warmupDays: request.period.warmupDays,
    events: request.period.events.filter(row => row.purpose !== '未使用' && row.status !== 'inactive').map(({ id, start, end, purpose }) => ({ id, start, end, purpose })).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0) }
    : Object.fromEntries(Object.entries(request.period).filter(([key]) => !['windows', 'eventSet', 'eventAssignment', 'events'].includes(key)))
  return { context: request.context, inputs: { bundleVersionId: request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId, rainfallMethod, idwPower: request.inputs.idwPower ?? 2, minimumStations: request.inputs.minimumStations ?? 1,
    ...(rainfallMethod === 'stationIdw' && request.inputs.stationSourceId ? { stationSourceId: request.inputs.stationSourceId } : {}),
    ...(rainfallMethod === 'stationIdw' && request.inputs.stationNetwork ? { stationNetwork: request.inputs.stationNetwork } : {}) },
    period, statePolicy: request.calibration?.statePolicy || (request.period.continuousOrigin ? 'continuous-origin' : 'independent-warmup'),
    continuousOrigin: request.calibration?.continuousOrigin || request.period.continuousOrigin || null }
}

export async function saveModelScheme(cal, request) {
  request.inputs.idwPower = cal.idwPower ?? 2
  request.inputs.minimumStations = cal.minimumStations ?? 1
  if (cal.rainfallMethod === 'stationIdw' && cal.stationSourceId) request.inputs.stationSourceId = cal.stationSourceId
  else delete request.inputs.stationSourceId
  if (cal.rainfallMethod === 'stationIdw') request.inputs.stationNetwork = await modelApi(`station-network?bundleVersionId=${encodeURIComponent(request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId)}${cal.stationSourceId ? `&sourceId=${encodeURIComponent(cal.stationSourceId)}` : ''}`)
  else delete request.inputs.stationNetwork
  const row = await modelApi('model-schemes', { method: 'POST', body: {
    ...(cal.modelSchemeId ? { id: cal.modelSchemeId, revision: cal.modelSchemeLatestRevision || cal.modelSchemeRevision } : {}),
    name: cal.modelSchemeName, definition: schemeDefinition(request, cal.rainfallMethod), calculationDraft: request
  } })
  cal.modelSchemeId = row.id; cal.modelSchemeRevision = row.revision; cal.modelSchemeLatestRevision = row.latestRevision; cal.modelSchemeName = row.name
  cal.saveCurrentWorkspace()
  return row
}

export async function restoreModelScheme(cal, app, row) {
  const request = row.calculationDraft
  if (request.context.contextKey !== app.contextKey) throw new Error('方案属于其他流域或模型尺度，请先切换工作区。')
  if (app.selectedInputBundleVersionId !== request.inputs.bundleVersionId) await app.selectInputBundleVersion(request.inputs.bundleVersionId)
  if (request.routing?.versionId) app.updateWorkContext({ selectedRoutingVersionId: request.routing.versionId })
  const type = request.runType === 'simulation' ? 'continuousSimulation' : request.period.mode === 'continuous' ? 'continuousCalibration' : 'eventCalibration'
  cal.loadFromTaskConfig(type, normalizeBackendTask({ runConfig: request }).runConfig)
  cal.schemeWindows = (request.period.events || []).filter(row => !cal.events.some(event => event.id === row.id && event.start === row.start && event.end === row.end && event.runtimeRegistered)).map(row => ({ ...row, sourceBundleVersionId: request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId, runtimeRegistered: false, status: 'active' }))
  cal.modelSchemeId = row.id; cal.modelSchemeRevision = row.revision; cal.modelSchemeLatestRevision = row.latestRevision; cal.modelSchemeName = row.name
  cal.rainfallMethod = row.definition.inputs.rainfallMethod || 'reuse'
  cal.stationSourceId = row.definition.inputs.stationSourceId || null
  cal.idwPower = row.definition.inputs.idwPower ?? 2; cal.minimumStations = row.definition.inputs.minimumStations ?? 1
  // User windows remain in the scheme even before input preparation registers them.
  for (const event of request.period.events || []) {
    if (!cal.events.some(item => item.id === event.id)) cal.events.push({ ...event, runtimeRegistered: false, status: 'active' })
  }
  cal.saveCurrentWorkspace()
}

export async function prepareSchemeInputs(request, rainfallMethod, onProgress) {
  let task = await modelApi('scheme-preparations', { method: 'POST', body: {
    operation: 'schemeInput', requestId: uniqueRequestId('PREP'), name: '方案自动计算资料',
    projectId: request.context.projectId, resolutionId: request.context.resolutionId,
    parentBundleVersionId: request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId, period: request.period,
    rainfallMethod: rainfallMethod || 'reuse',
    ...(request.inputs.stationSourceId ? { stationSourceId: request.inputs.stationSourceId } : {}),
    science: request.science || {}, evaporation: request.inputs.evaporation || {},
    idwPower: request.inputs.idwPower ?? 2, minimumStations: request.inputs.minimumStations ?? 1,
    statePolicy: request.calibration?.statePolicy || (request.period.continuousOrigin ? 'continuous-origin' : 'independent-warmup'),
    continuousOrigin: request.calibration?.continuousOrigin || request.period.continuousOrigin || null
  } })
  while (['queued', 'running', 'cancelling'].includes(task.status)) {
    onProgress?.(task)
    await new Promise(resolve => setTimeout(resolve, 1000))
    task = await modelApi(`input-tasks/${task.taskId}`)
  }
  if (!['completed', 'archived'].includes(task.status)) throw new Error(task.errorMessage || '资料准备未完成')
  const result = await modelApi(`input-tasks/${task.taskId}/result`)
  if (!task.registeredBundleVersionId) throw new Error('计算资料尚未完成核验登记，请刷新后重试')
  return { ...result, bundleVersionId: task.registeredBundleVersionId, taskId: task.taskId }
}
