import { selectObservedHydrologyDataset, selectObservedPrecipitationDataset } from './datasetSelectionService.js'
import { projectResourceRoot } from './projectResourceService.js'
import { modelApi, modelBackendEnabled, uniqueRequestId } from './modelBackendService.js'
import { selectedInputBundleVersionId } from './inputProductService.js'

const STORAGE_KEY = 'hydro_prepared_calibration_requests_v1'

function createRequestId(contextKey, runType) {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14)
  const context = String(contextKey || 'calibration').replace(/[^A-Za-z0-9]/g, '').slice(-12).toUpperCase()
  return uniqueRequestId(`${runType === 'simulation' ? 'SIM' : 'CAL'}-REQ-${context}-${stamp}`)
}

function persistPreparedRequest(request) {
  if (typeof window === 'undefined') return
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    localStorage.setItem(STORAGE_KEY, JSON.stringify([request, ...saved].slice(0, 30)))
  } catch {
    // 浏览器存储不可用时仍返回本次请求。
  }
}

function seedToUint32(seed) {
  let value = 2166136261
  for (const character of String(seed)) {
    value ^= character.charCodeAt(0)
    value = Math.imul(value, 16777619)
  }
  return value >>> 0
}

function seededRandom(seed) {
  let value = seedToUint32(seed)
  return () => {
    value += 0x6D2B79F5
    let result = value
    result = Math.imul(result ^ (result >>> 15), result | 1)
    result ^= result + Math.imul(result ^ (result >>> 7), result | 61)
    return ((result ^ (result >>> 14)) >>> 0) / 4294967296
  }
}

function shuffleWithSeed(rows, seed) {
  const random = seededRandom(seed)
  const shuffled = [...rows]
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const target = Math.floor(random() * (index + 1))
    ;[shuffled[index], shuffled[target]] = [shuffled[target], shuffled[index]]
  }
  return shuffled
}

function tailCounts(size) {
  const ratios = [0.7, 0.2, 0.1]
  const raw = ratios.map((ratio) => size * ratio)
  const counts = raw.map(Math.floor)
  const remaining = size - counts.reduce((sum, value) => sum + value, 0)
  const priority = raw
    .map((value, index) => ({ index, fraction: value - counts[index] }))
    .sort((a, b) => b.fraction - a.fraction || b.index - a.index)
  for (let index = 0; index < remaining; index += 1) counts[priority[index].index] += 1
  return counts
}

export function stratifiedRandomEventSplit(events, seed) {
  const sorted = [...events].sort((a, b) => Number(a.observedPeak || 0) - Number(b.observedPeak || 0))
  const assignments = []
  for (let start = 0; start < sorted.length; start += 10) {
    const group = shuffleWithSeed(sorted.slice(start, start + 10), `${seed}:${start / 10}`)
    const [calibrationCount, validationCount] = group.length === 10 ? [7, 2] : tailCounts(group.length)
    group.forEach((event, index) => {
      const purpose = index < calibrationCount ? '率定' : index < calibrationCount + validationCount ? '验证' : '独立检验'
      assignments.push({ id: event.id, purpose })
    })
  }
  return assignments
}

/**
 * 率定候选集筛选只作用于本次作业，不改变洪水场次主目录状态。
 * Top-N 按实测出口 Qmax 从大到小选取；随机划分在筛选完成后再执行。
 */
export function selectCalibrationCandidates(events = [], options = {}) {
  const method = options.method || 'all-valid'
  const active = events.filter((event) => event.runtimeRegistered !== false
    && (event.runtimeRegistered !== true || event.registeredStatus === 'active')
    && event.status !== 'inactive' && event.status !== 'draft' && event.status !== 'pending')
  const valid = active.filter((event) => {
    const peak = event.observedPeak
    const peakReady = (typeof peak === 'number' || (typeof peak === 'string' && peak.trim() !== ''))
      && Number.isFinite(Number(peak))
    const flowReady = event.flowCompleteness == null || Number(event.flowCompleteness) > 0
    return peakReady && flowReady
  })

  if (method === 'top-n') {
    const count = Math.max(0, Number(options.topN) || 0)
    return [...valid]
      .sort((a, b) => Number(b.observedPeak) - Number(a.observedPeak) || String(a.eventId || a.id).localeCompare(String(b.eventId || b.id)))
      .slice(0, count)
  }
  if (method === 'peak-threshold') {
    const threshold = Number(options.peakThreshold)
    return valid.filter((event) => Number(event.observedPeak) >= (Number.isFinite(threshold) ? threshold : 0))
  }
  if (method === 'year') {
    const year = String(options.year || '')
    return year && year !== 'all' ? valid.filter((event) => String(event.start || '').slice(0, 4) === year) : valid
  }
  if (method === 'date-range') {
    const start = options.startDate ? new Date(options.startDate).getTime() : -Infinity
    const end = options.endDate ? new Date(options.endDate).getTime() : Infinity
    return valid.filter((event) => {
      const eventStart = new Date(event.start).getTime()
      const eventEnd = new Date(event.end).getTime()
      return eventStart >= start && eventEnd <= end
    })
  }
  if (method === 'manual') {
    const ids = new Set((options.manualIds || []).map(String))
    return valid.filter((event) => ids.has(String(event.eventId || event.id)))
  }
  return valid
}

export function buildCalibrationRequest({ app, calibration, operationMode, events = [], eventAssignment = null, eventSelection = null }) {
  const runType = operationMode === 'simulation' ? 'simulation' : 'calibration'
  const projectRoot = projectResourceRoot(app, app.basinId) || './data/projects/'
  const precipitationDatasetId = selectObservedPrecipitationDataset(app.projectData, app.modelScale)?.id || null
  const outletHydrologyDatasetId = selectObservedHydrologyDataset(app.projectData)?.id || null
  const eventRows = events.map((event) => ({
    id: event.eventId || event.id,
    sourceFloodNo: event.sourceFloodNo || null,
    purpose: event.purpose,
    start: event.start,
    end: event.end,
    observedPeak: event.observedPeak ?? null,
    peakTime: event.peakTime || null,
    volumeM3: event.volumeM3 ?? event.volume ?? null,
    rainCompleteness: event.rainCompleteness ?? null,
    flowCompleteness: event.flowCompleteness ?? event.riverCompleteness ?? null,
    source: event.source || null,
    status: event.status || 'active',
    revision: Number(event.revision) || 1
  }))
  return {
    schemaVersion: '2.0',
    requestId: createRequestId(calibration.contextKey, runType),
    runType,
    createdAt: new Date().toISOString(),
    routing: { versionId: app.selectedRoutingVersionId || 'control-split-v2' },
    context: {
      contextKey: calibration.contextKey,
      projectId: app.basinId,
      projectName: app.basinName,
      resolutionId: app.modelScale,
      modelFrameworkId: 'distributed-xinanjiang',
      modelConfigId: app.modelConfigId
    },
    inputs: {
      ...(app.basinId === 'nananxi-xufan' && (app.usesModelBackend || modelBackendEnabled()) ? { bundleVersionId: selectedInputBundleVersionId(app) } : {}),
      precipitationDatasetId,
      outletHydrologyDatasetId,
      floodEventCatalog: `${projectRoot}observations/flood-events.json`,
      eventDataPackages: `${projectRoot}observations/event-data-packages.json`,
      spatialPreparationCatalog: `${projectRoot}input-preparation.json`,
      evaporationDatasetId: null,
      evaporation: runType === 'calibration' && calibration.calibrationMode === 'event'
        ? { mode: 'constant-zero', valueMmPerHour: 0, scope: 'event-simulation' }
        : app.projectData?.backend
          ? { mode: 'monthly-climatology-assumption', scope: 'continuous-simulation', versionId: 'pet_forcing_assumed_monthly' }
          : { mode: 'dataset-required', scope: 'continuous-simulation' }
    },
    period: runType === 'simulation'
      ? {
          startTime: calibration.simulationStart,
          endTime: calibration.simulationEnd,
          warmupDays: Number(calibration.warmupDays),
          initialStateVersionId: calibration.initialStateVersionId || null
        }
      : {
          mode: calibration.calibrationMode,
          calibrationStart: calibration.calibrationStart,
          calibrationEnd: calibration.calibrationEnd,
          validationStart: calibration.validationStart,
          validationEnd: calibration.validationEnd,
          independentStart: calibration.independentStart || null,
          independentEnd: calibration.independentEnd || null,
          warmupDays: Number(calibration.warmupDays),
          ...(calibration.calibrationMode === 'event' ? {
          events: eventRows,
          eventAssignment,
          eventSet: {
            version: calibration.eventSetVersion || null,
            schemaVersion: calibration.eventCatalogSchemaVersion || null,
            selection: eventSelection,
            selectedEventIds: eventRows.filter((event) => event.purpose !== '未使用' && event.status !== 'inactive').map((event) => event.id),
            snapshot: eventRows
          }
          } : {})
        },
    parameters: runType === 'simulation' ? { ...calibration.params } : undefined,
    calibration: runType === 'calibration' ? {
      section: calibration.section,
      parameterLibrary: 'simulation-rate',
      parameterRelease: 'candidate-only',
      initialParameterVersionId: calibration.paramVersion,
      initialParameters: { ...calibration.params },
      ...(calibration.calibrationTarget === 'routing' ? { target: 'routing',
        ...(calibration.routingSearchParameters != null ? { routingSearchParameters: JSON.parse(JSON.stringify(calibration.routingSearchParameters)) } : {}) }
        : calibration.searchParameters != null ? { searchParameters: JSON.parse(JSON.stringify(calibration.searchParameters)) } : {}),
      objectives: { ...calibration.criteria },
      optimizer: {
        algorithm: calibration.algorithm,
        strategy: calibration.calibrationTarget === 'routing' ? 'joint' : calibration.searchStrategy || 'joint',
        seed: Number(calibration.searchSeed ?? 42),
        maxEvaluations: Number(calibration.maxEvaluations),
        populationSize: Number(calibration.populationSize)
      }
    } : null,
    execution: {
      owner: 'model-backend',
      frontEndCalculatesModelResult: false,
      expectedOutputs: runType === 'calibration'
        ? ['best-parameter-version', 'calibration-metrics', 'validation-metrics', 'independent-test-metrics', 'parameter-release-candidate', 'simulated-hydrographs']
        : ['simulated-hydrograph', 'end-state-version']
    },
    lineage: {
      ...(app.basinId === 'nananxi-xufan' && (app.usesModelBackend || modelBackendEnabled()) ? { inputBundleVersionId: selectedInputBundleVersionId(app) } : {}),
      dataVersionId: precipitationDatasetId,
      observationDataVersionId: outletHydrologyDatasetId,
      eventSetVersionId: runType === 'calibration' && calibration.calibrationMode === 'event' ? (calibration.eventSetVersion || null) : null,
      parameterVersionId: calibration.paramVersion || null,
      stateVersionId: runType === 'simulation' ? (calibration.initialStateVersionId || null) : null,
      modelConfigVersionId: app.modelConfigId || null,
      resultVersionId: null,
      sourceTaskId: null
    }
  }
}

/**
 * Normalize the few result envelopes used by model services. The browser
 * never interprets a task as a model result: a result must be explicitly
 * returned under result/outputs or as one of the documented metric fields.
 */
export function normalizeCalibrationResult(payload = {}) {
  const source = payload?.result || payload?.outputs?.calibration || payload?.outputs || payload
  if (!source || typeof source !== 'object') return null
  const independentTest = source.independentTest
    || source.independentMetrics
    || source.metrics?.independentTest
    || source.metrics?.independent
    || null
  const bestParameters = source.bestParameters || source.parameters || source.optimalParameters || null
  const eventMetrics = source.eventMetrics || source.perEventMetrics || source.metrics?.events || source.metrics?.perEvent || null
  const hydrographs = source.hydrographs || source.simulatedHydrographs || source.processes || null
  const hasResult = Boolean(
    bestParameters
    || source.parameterVersionId
    || source.calibrationMetrics
    || source.validationMetrics
    || independentTest
  )
  if (!hasResult) return null
  return {
    ...source,
    taskId: source.taskId || payload.taskId || payload.id || null,
    parameterVersionId: source.parameterVersionId || source.parameterVersion || null,
    bestParameters,
    calibrationMetrics: source.calibrationMetrics || source.metrics?.calibration || null,
    validationMetrics: source.validationMetrics || source.metrics?.validation || null,
    independentTest,
    eventMetrics,
    hydrographs
  }
}

export async function submitCalibrationRequest(request) {
  const backendEnabled = modelBackendEnabled()
  if (!backendEnabled) {
    persistPreparedRequest(request)
    return {
      status: 'prepared',
      backendEnabled,
      requestId: request.requestId,
      request,
      message: backendEnabled ? '未配置模型后端地址，已保存请求草稿' : '当前为前端 Demo，模型后端未启用，已保存请求草稿'
    }
  }
  const endpoint = request.runType === 'simulation' ? 'simulation-tasks' : 'calibration-tasks'
  const task = await modelApi(endpoint, { method: 'POST', body: request })
  return {
    status: 'submitted',
    requestId: request.requestId,
    task,
    result: normalizeCalibrationResult(task),
    request
  }
}
