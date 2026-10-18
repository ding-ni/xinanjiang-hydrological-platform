import { modelApi, normalizeBackendTask } from './modelBackendService.js'

export function schemeDefinition(request, rainfallMethod = 'reuse') {
  const period = request.period.mode === 'event' ? { mode: 'event', warmupDays: request.period.warmupDays,
    events: request.period.events.filter(row => row.purpose !== '未使用' && row.status !== 'inactive').map(({ id, start, end, purpose }) => ({ id, start, end, purpose })).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0) }
    : Object.fromEntries(Object.entries(request.period).filter(([key]) => !['windows', 'eventSet', 'eventAssignment', 'events'].includes(key)))
  return { context: request.context, inputs: { bundleVersionId: request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId, rainfallMethod, idwPower: request.inputs.idwPower ?? 2, minimumStations: request.inputs.minimumStations ?? 1,
    ...(request.inputs.dataMode ? { dataMode: request.inputs.dataMode } : {}),
    ...(rainfallMethod === 'stationIdw' && request.inputs.stationSourceId ? { stationSourceId: request.inputs.stationSourceId } : {}),
    ...(rainfallMethod === 'stationIdw' && request.inputs.stationNetwork ? { stationNetwork: request.inputs.stationNetwork } : {}) },
    period, statePolicy: request.calibration?.statePolicy || (request.period.continuousOrigin ? 'continuous-origin' : 'independent-warmup'),
    continuousOrigin: request.calibration?.continuousOrigin || request.period.continuousOrigin || null }
}

// A trial freezes its own inputs without committing or changing the scheme editor.
export async function prepareTrialInputs(cal, request, api = modelApi) {
  const saved = cal.modelSchemeRevision ? await api(`model-schemes/${cal.modelSchemeId}?revision=${cal.modelSchemeRevision}`) : null
  const projected = schemeDefinition(request, cal.rainfallMethod)
  const selection = definition => ({ period: definition?.period, statePolicy: definition?.statePolicy,
    continuousOrigin: definition?.continuousOrigin, stationNetwork: definition?.inputs?.stationNetwork,
    rainfallMethod: definition?.inputs?.rainfallMethod, idwPower: definition?.inputs?.idwPower, minimumStations: definition?.inputs?.minimumStations })
  const rebuild = saved?.definition?.period && JSON.stringify(selection(saved.definition)) !== JSON.stringify(selection(projected))
  if (rebuild || !saved?.definition?.period || cal.schemeDataMode === 'historical-database') {
    request.inputs = { ...request.inputs, dataMode: 'historical-database', bundleVersionId: 'HISTORICAL_SPATIAL_V1',
      sourceBundleVersionId: 'HISTORICAL_SPATIAL_V1', rainfallMethod: 'stationIdw', stationSourceId: 'LOCAL_OBSERVATIONS',
      stationNetwork: rebuild && cal.schemeDataMode !== 'historical-database' ? await api('database/network') : cal.schemeStationNetwork || await api('database/network') }
    for (const key of ['bundleManifestSHA256', 'preparationTaskId']) delete request.inputs[key]
    for (const key of ['inputBundleVersionId', 'bundleManifestSHA256', 'preparationTaskId', 'preparationSHA256']) delete request.lineage[key]
  }
  return request
}

export async function saveModelScheme(cal, request, api = modelApi) {
  if (!cal.modelSchemeName?.trim()) throw new Error('请先填写方案名称')
  cal.modelSchemeName = cal.modelSchemeName.trim()
  const baseId = cal.modelSchemeId || cal.modelSchemeSourceId
  const baseRevision = cal.modelSchemeRevision || cal.modelSchemeSourceRevision
  if (baseId && baseRevision) {
    const saved = await api(`model-schemes/${baseId}?revision=${baseRevision}`)
    const before = saved.definition
    const projected = schemeDefinition({ ...request, inputs: { ...request.inputs,
      sourceBundleVersionId: cal.schemeSourceBundleVersionId, stationNetwork: cal.schemeStationNetwork,
      stationSourceId: cal.stationSourceId, idwPower: cal.idwPower ?? 2, minimumStations: cal.minimumStations ?? 1 } }, cal.rainfallMethod)
    const inputSelection = definition => ({ period: definition?.period, statePolicy: definition?.statePolicy,
      continuousOrigin: definition?.continuousOrigin, stationNetwork: definition?.inputs?.stationNetwork,
      rainfallMethod: definition?.inputs?.rainfallMethod, idwPower: definition?.inputs?.idwPower, minimumStations: definition?.inputs?.minimumStations })
    if (before?.period && JSON.stringify(inputSelection(before)) !== JSON.stringify(inputSelection(projected))) {
      if (cal.schemeDataMode !== 'historical-database') cal.schemeStationNetwork = await api('database/network')
      cal.schemeDataMode = 'historical-database'; cal.rainfallMethod = 'stationIdw'
      cal.stationSourceId = 'LOCAL_OBSERVATIONS'; cal.schemeSourceBundleVersionId = 'HISTORICAL_SPATIAL_V1'
      makeSchemeEditable(cal)
      delete request.sourcePlan
      for (const key of ['initializationPolicy', 'initialCandidates', 'effectiveInitialCandidates']) delete request.calibration.optimizer[key]
    }
  }
  if (cal.schemeDataMode === 'historical-database') request.inputs.dataMode = 'historical-database'
  else delete request.inputs.dataMode
  request.inputs.sourceBundleVersionId = cal.schemeSourceBundleVersionId || request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId
  request.inputs.bundleVersionId = request.inputs.sourceBundleVersionId
  if (cal.schemeRoutingVersionId) request.routing.versionId = cal.schemeRoutingVersionId
  request.inputs.idwPower = cal.idwPower ?? 2
  request.inputs.minimumStations = cal.minimumStations ?? 1
  if (cal.rainfallMethod === 'stationIdw' && cal.stationSourceId) request.inputs.stationSourceId = cal.stationSourceId
  else delete request.inputs.stationSourceId
  if (cal.rainfallMethod === 'stationIdw') {
    cal.schemeStationNetwork ||= await api('database/network')
    request.inputs.stationNetwork = cal.schemeStationNetwork
  }
  else delete request.inputs.stationNetwork
  const row = await api('model-schemes', { method: 'POST', body: {
    ...(cal.modelSchemeId ? { id: cal.modelSchemeId, revision: cal.modelSchemeLatestRevision || cal.modelSchemeRevision } : {}),
    name: cal.modelSchemeName, definition: schemeDefinition(request, cal.rainfallMethod), calculationDraft: request,
    editor: calibrationEditor(cal), ...(cal.modelSchemeDraftRevision != null ? { draftRevision: cal.modelSchemeDraftRevision } : {})
  } })
  cal.modelSchemeId = row.id; cal.modelSchemeRevision = row.revision; cal.modelSchemeLatestRevision = row.latestRevision; cal.modelSchemeName = row.name
  cal.modelSchemeDraftRevision = row.draftRevision
  cal.saveCurrentWorkspace()
  return row
}

export async function restoreModelScheme(cal, app, row, { draft = false } = {}) {
  row = { ...row, editor: draft || !row.revision ? row.editor || {} : row.savedEditor || {} }
  const request = row.calculationDraft
  const context = request?.context || row.editor?.context || row.definition?.context
  if (context?.contextKey && context.contextKey !== app.contextKey) throw new Error('请选择当前流域的方案')
  cal.resetCalibrationScheme({}, app.backendCase)
  if (!request?.period) {
    restoreCalibrationEditor(cal, row.editor || {})
    cal.modelSchemeId = row.id; cal.modelSchemeRevision = row.revision; cal.modelSchemeLatestRevision = row.latestRevision; cal.modelSchemeName = row.name
    cal.modelSchemeDraftRevision = row.draftRevision
    cal.saveCurrentWorkspace()
    return
  }
  const type = request.runType === 'simulation' ? 'continuousSimulation' : request.period.mode === 'continuous' ? 'continuousCalibration' : 'eventCalibration'
  const catalogue = cal.events.map(event=>({...event}))
  cal.loadFromTaskConfig(type, normalizeBackendTask({ runConfig: request }).runConfig)
  const partition=partitionSchemeWindows(catalogue,request.period.events || [])
  cal.events=partition.catalogue
  cal.schemeWindows=partition.windows.map(event=>({...event,sourceBundleVersionId:request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId,runtimeRegistered:false,status:event.status || 'active'}))
  cal.modelSchemeId = row.id; cal.modelSchemeRevision = row.revision; cal.modelSchemeLatestRevision = row.latestRevision; cal.modelSchemeName = row.name
  cal.modelSchemeDraftRevision = row.draftRevision
  cal.rainfallMethod = row.definition.inputs.rainfallMethod || 'reuse'
  cal.schemeDataMode = row.definition.inputs.dataMode || 'frozen-input'
  cal.schemeStationNetwork = request.inputs.stationNetwork || null
  cal.stationSourceId = row.definition.inputs.stationSourceId || null
  cal.idwPower = row.definition.inputs.idwPower ?? 2; cal.minimumStations = row.definition.inputs.minimumStations ?? 1
  cal.schemeSourceBundleVersionId = row.definition.inputs.bundleVersionId || request.inputs.sourceBundleVersionId || request.inputs.bundleVersionId
  cal.schemeRoutingVersionId = request.routing?.versionId || null
  cal.editorTrialMode = request.calibration?.evaluationMode === 'manual' ? 'manual' : 'automatic'
  cal.requiredInputBundleVersionId = null
  if (row.editor && Object.keys(row.editor).length > 1) restoreCalibrationEditor(cal, row.editor)
  if (cal.originalCalibrationPlan && request.sourcePlan?.parameterVersionId) cal.originalCalibrationPlan = { ...cal.originalCalibrationPlan, parameterVersionId: request.sourcePlan.parameterVersionId }
  if (request.period.mode === 'event' && !request.period.events?.some(event => event.id === cal.eventId)) {
    const first = request.period.events?.[0]
    if (first) { cal.eventId = first.id; cal.eventStart = first.start; cal.eventEnd = first.end }
  }
  cal.activeOperationId = row.editor?.activeOperationId || null
  cal.activeOperationRequest = row.editor?.activeOperationRequest || null
  cal.saveCurrentWorkspace()
}

export function partitionSchemeWindows(catalogue, events) {
  const sameTime=(a,b)=>String(a||'').replace(' ','T').slice(0,16)===String(b||'').replace(' ','T').slice(0,16)
  const originals=new Map(catalogue.map(row=>[row.id,row]))
  const selected=new Map(events.map(row=>[row.id,row]))
  const changed=row=>{const original=originals.get(row.id);return original?.start&&original?.end&&row.start&&row.end&&(!sameTime(original.start,row.start)||!sameTime(original.end,row.end))}
  return {catalogue:catalogue.map(row=>({...row,start:row.start||selected.get(row.id)?.start,end:row.end||selected.get(row.id)?.end,purpose:selected.get(row.id)&&!changed(selected.get(row.id)) ? selected.get(row.id).purpose : '未使用'})),
    windows:events.filter(row=>!originals.has(row.id)||changed(row)).map(row=>({...row,...(changed(row)?{catalogueBoundaryOverride:true}:{})}))}
}

// Editing is stored before the selected periods are ready for calculation.
export const CALIBRATION_EDITOR_FIELDS = [
  'section', 'calibrationMode', 'eventId', 'schemeWindows', 'rainfallMethod', 'stationSourceId', 'idwPower', 'minimumStations',
  'schemeSourceBundleVersionId', 'schemeRoutingVersionId', 'schemeDataMode', 'schemeStationNetwork', 'calibrationStart', 'calibrationEnd', 'validationStart', 'validationEnd',
  'independentStart', 'independentEnd', 'params', 'baseline', 'paramVersion', 'criteria', 'algorithm', 'maxEvaluations', 'populationSize',
  'searchStrategy', 'searchParameters', 'routingSearchParameters', 'calibrationTarget', 'jointProfile', 'searchSeed', 'optimizerSettings',
  'searchParameterOrder', 'eventSplitSeed', 'eventAssignment', 'warmupDays', 'statePolicy', 'continuousOrigin', 'scienceVersionId',
  'scienceDescriptor', 'evaporationMode', 'scienceConditionsDraft', 'candidateWarmupHours', 'sourcePlan', 'sourcePlanRequestTemplate', 'originalCalibrationPlan',
  'activeOperationId', 'activeOperationRequest', 'editorTrialMode', 'manualRoutingPayload', 'searchRangeOverrides',
  'deliverySelection', 'configurationPending', 'deliveryProcessing', 'modeStartSettings'
]

export function calibrationEditor(cal) {
  return JSON.parse(JSON.stringify({
    context: { contextKey: cal.contextKey, projectId: cal.domainId, resolutionId: cal.modelScale, modelConfigId: cal.modelConfigId },
    ...Object.fromEntries(CALIBRATION_EDITOR_FIELDS.filter(key => cal[key] !== undefined).map(key => [key, cal[key]])),
    events: (cal.events || []).map(({ id, start, end, purpose, status }) => ({ id, start, end, purpose, status }))
  }))
}

export function calibrationEditorIdentity(editor) {
  const copy = JSON.parse(JSON.stringify(editor))
  for (const key of ['eventId', 'activeOperationId', 'activeOperationRequest', 'editorTrialMode']) delete copy[key]
  return JSON.stringify(copy)
}

export function restoreCalibrationEditor(cal, editor) {
  for (const key of CALIBRATION_EDITOR_FIELDS) if (editor[key] !== undefined) cal[key] = JSON.parse(JSON.stringify(editor[key]))
  if (Array.isArray(editor.events)) {
    const partition=partitionSchemeWindows(cal.events,editor.events)
    cal.events=partition.catalogue
    const windows = new Map((cal.schemeWindows || []).map(row => [row.id, row]))
    for (const row of partition.windows) {
      windows.set(row.id, { ...row, ...windows.get(row.id), status: row.status || 'active', runtimeRegistered: false,
        sourceBundleVersionId: cal.schemeSourceBundleVersionId })
    }
    cal.schemeWindows = [...windows.values()]
  }
  const event = [...(cal.schemeWindows || []), ...cal.events].find(row => row.id === cal.eventId) || cal.events[0]
  if (event) { cal.eventId = event.id; cal.eventStart = event.start; cal.eventEnd = event.end }
  cal.operationMode = 'calibration'; cal.fixedEventSimulation = false
  cal.requiredInputBundleVersionId = null; cal.initialStateVersionId = null
  cal.runResult = null; cal.simulationResult = null; cal.trialRecords = []
  cal.running = Boolean(cal.activeOperationId); cal.dirty = true; cal.runStatus = 'needs-run'; cal.runMessage = ''
}

export async function saveCalibrationEditor(cal, api = modelApi) {
  if (!cal.modelSchemeId) return null
  const row = await api(`model-schemes/${cal.modelSchemeId}/draft`, { method: 'POST', body: { editor: calibrationEditor(cal),
    revision: cal.modelSchemeLatestRevision || cal.modelSchemeRevision || 0,
    ...(cal.modelSchemeDraftRevision != null ? { draftRevision: cal.modelSchemeDraftRevision } : {}) } })
  cal.modelSchemeDraftRevision = row.draftRevision
  return row
}

export async function recoverActiveCalibrationOperation(cal, api = modelApi) {
  if (!cal.activeOperationId) return false
  const operation = await api(`operations/${cal.activeOperationId}`)
  if (['queued', 'preparing', 'running', 'cancelling'].includes(operation.status)) return true
  cal.activeOperationId = null; cal.activeOperationRequest = null; cal.running = false
  cal.operationProgress = operation
  cal.saveCurrentWorkspace()
  return false
}

export function originalCalibrationRecords(versions = [], contextKey) {
  const byPlan = new Map()
  for (const row of versions) {
    const plan = row.originalCalibrationPlan
    if (!plan?.planId || !/^[a-f0-9]{64}$/.test(plan.planSHA256 || '') || !row.parameterVersionId
      || (row.contextKey || row.context?.contextKey) !== contextKey) continue
    const existing = byPlan.get(plan.planSHA256)
    if (!existing || (existing.candidateKind !== 'frozen-calibration' && row.candidateKind === 'frozen-calibration')) byPlan.set(plan.planSHA256, row)
  }
  return [...byPlan.values()]
}

export function makeSchemeEditable(cal, record = null) {
  if (record?.routingCandidate?.routingVersionId && cal.schemeRoutingVersionId === record.originalCalibrationPlan?.originalRoutingVersionId) {
    cal.schemeRoutingVersionId = record.routingCandidate.routingVersionId
    if (record.bestParameters || record.parameters) {
      cal.params = JSON.parse(JSON.stringify(record.bestParameters || record.parameters))
      cal.baseline = JSON.parse(JSON.stringify(cal.params))
    }
  }
  cal.sourcePlan = null
  cal.sourcePlanRequestTemplate = null
  cal.originalPlanChanged = true
  const settings = { ...(cal.optimizerSettings || {}) }
  for (const key of ['initializationPolicy', 'initialCandidates', 'effectiveInitialCandidates']) delete settings[key]
  cal.optimizerSettings = settings
}

export function schemeWindowCounts(windows = []) {
  return windows.reduce((counts, row) => {
    if (row.catalogueBoundaryOverride) counts.boundaryOverrides++
    else if (row.purpose && row.purpose !== '未使用' && row.status !== 'inactive') counts.selectedExtra++
    else counts.excludedExtra++
    return counts
  }, { selectedExtra: 0, excludedExtra: 0, boundaryOverrides: 0 })
}

export function continuousOriginError(policy, origin, firstEvaluation) {
  if (policy !== 'continuous-origin' || !firstEvaluation) return ''
  const first = new Date(firstEvaluation).getTime(), start = new Date(origin || '').getTime()
  if (!Number.isFinite(start) || start >= first) return '连续演算开始时间须早于最早评价时刻 ' + String(firstEvaluation).replace('T', ' ').slice(0, 16) + '，请在率定设置中调整'
  return ''
}

export async function adoptHistoricalPreparation(cal, api = modelApi) {
  const [network, defaults] = await Promise.all([api('database/network'), api('database/preparation-defaults')])
  cal.schemeStationNetwork = JSON.parse(JSON.stringify(network))
  cal.idwPower = defaults.idwPower
  cal.minimumStations = defaults.minimumStations
  cal.rainfallMethod = 'stationIdw'
  cal.stationSourceId = 'LOCAL_OBSERVATIONS'
  cal.markNeedsRun(''); cal.saveCurrentWorkspace()
}

export async function adoptOriginalBestParameters(cal, api = modelApi) {
  const parameterId = cal.originalCalibrationPlan?.parameterVersionId
  if (!parameterId) throw new Error('原方案未关联可核实的最佳参数记录')
  const payload = await api(`parameter-versions/${encodeURIComponent(parameterId)}/original-plan?operation=fixed-replay`)
  if (payload.plan?.planSHA256 !== cal.originalCalibrationPlan.planSHA256 || payload.request?.context?.contextKey !== cal.contextKey) throw new Error('原参数记录与当前方案不一致')
  const request = payload.request
  if (!request.calibration?.initialParameters || !request.routing?.versionId) throw new Error('原最佳参数不完整')
  cal.params = JSON.parse(JSON.stringify(request.calibration.initialParameters))
  cal.baseline = JSON.parse(JSON.stringify(request.calibration.initialParameters))
  cal.schemeRoutingVersionId = request.routing.versionId
  cal.manualRoutingPayload = null
  cal.paramVersion = parameterId
  makeSchemeEditable(cal)
  cal.markNeedsRun(''); cal.saveCurrentWorkspace()
}

export async function openOriginalCalibrationRecord(cal, app, record, api = modelApi) {
  const payload = await api(`parameter-versions/${encodeURIComponent(record.parameterVersionId)}/original-plan?operation=search`)
  const { request, plan } = payload
  if (request?.context?.contextKey !== app.contextKey || plan?.planSHA256 !== record.originalCalibrationPlan?.planSHA256) throw new Error('原率定记录与当前流域不一致')
  const rows = (await api('model-schemes')).schemes || []
  for (const row of rows) {
    const saved = await api(`model-schemes/${row.id}`)
    const source = saved.savedEditor?.originalCalibrationPlan || saved.editor?.originalCalibrationPlan || saved.calculationDraft?.sourcePlan
    if (source?.planSHA256 === plan.planSHA256 && source?.planId === plan.planId) {
      await restoreModelScheme(cal, app, saved)
      cal.originalCalibrationPlan = { ...JSON.parse(JSON.stringify(plan)), parameterVersionId: record.parameterVersionId }
      return saved
    }
  }
  const name = record.name || plan.name
  if (!name?.trim()) throw new Error('原率定记录缺少名称')
  await restoreModelScheme(cal, app, { id: null, name, revision: 0, latestRevision: 0,
    definition: schemeDefinition(request, 'reuse'), calculationDraft: request, editor: {} })
  cal.originalCalibrationPlan = { ...JSON.parse(JSON.stringify(plan)), parameterVersionId: record.parameterVersionId }
  cal.eventSplitSeed = plan.sourceSplitSeed ?? cal.eventSplitSeed
  // A compatibility entry preserves the original request. Startup migration
  // normally registers it before the scheme list is displayed.
  const saved = await api('model-schemes', { method: 'POST', body: { name,
    definition: schemeDefinition(request, 'reuse'), calculationDraft: request,
    editor: { originalCalibrationPlan: plan } } })
  await restoreModelScheme(cal, app, saved)
  return saved
}
