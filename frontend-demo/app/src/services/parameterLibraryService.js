import { resolveDomainContext } from '../data/domainContext.js'
import { visibleWorkspaceResult } from './savedModelResultService.js'

const STORAGE_KEY = 'hydro_parameter_library_overrides_v1'

const LIBRARY_IDS = Object.freeze({
  simulation: 'simulation-rate',
  automatic: 'automatic-forecast'
})

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value))
}

function readOverrides() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}
  } catch {
    return {}
  }
}

function writeOverrides(value) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // The browser may disable local storage; the current page can still work.
  }
}

function contextKey(projectId, resolutionId) {
  return `${projectId || 'project'}:${resolutionId || '1km'}`
}

function now() {
  return new Date().toISOString()
}

function mergeLibrary(base, override) {
  const result = clone(base || {}) || {}
  for (const libraryId of ['simulationRateLibrary', 'automaticForecastLibrary']) {
    const source = result[libraryId] || {}
    const patch = override?.[libraryId] || {}
    result[libraryId] = {
      ...source,
      ...patch,
      versions: Array.isArray(patch.versions) ? patch.versions : (Array.isArray(source.versions) ? source.versions : [])
    }
  }
  return result
}

function registeredLibraryContext(projectId, resolutionId, activeContext = null) {
  if (!projectId || !resolutionId) return null
  const context = resolveDomainContext(projectId, {
    modelScale: resolutionId,
    configId: activeContext?.configId,
    projectId: activeContext?.projectId
  })
  // Unknown IDs must not inherit the registry's default Selinco demo.
  if (context.basinId !== projectId || context.modelScale !== resolutionId) return null
  if (activeContext && ['basinId', 'modelScale', 'projectId', 'configId', 'contextKey']
    .some((field) => activeContext[field] !== context[field])) return null
  return context
}

function parameterContext(version) {
  const keys = [version?.contextKey, version?.context?.contextKey].filter((key) => typeof key === 'string')
  const key = keys[0]
  if (!key || keys.some((value) => value !== key)) return null
  const [basinId, projectId, configId, extra] = key.split(':')
  if (!basinId || !projectId || !configId || extra != null) return null
  const context = resolveDomainContext(basinId, { projectId, configId })
  return context.basinId === basinId && context.projectId === projectId && context.configId === configId
    && context.contextKey === key ? context : null
}

function visibleParameterVersion(version, context) {
  if (!version || typeof version !== 'object' || !version.id || !context) return null
  const keys = [version.contextKey, version.context?.contextKey].filter((key) => key != null)
  if (!keys.length || keys.some((key) => key !== context.contextKey)) return null
  if (version.source != null && version.resultSource != null && version.source !== version.resultSource) return null
  if (version.sourceTaskId != null && version.taskId != null && version.sourceTaskId !== version.taskId) return null
  const normalized = { ...version, resultSource: version.source ?? version.resultSource, taskId: version.sourceTaskId ?? version.taskId }
  if (!visibleWorkspaceResult(normalized, context)) return null
  // Check each supplied scope, including conflicting flat/nested fields.
  const scopes = [
    [version.projectId, context.basinId], [version.domainId, context.basinId],
    [version.context?.projectId, context.basinId], [version.modelProjectId, context.projectId],
    [version.resolutionId, context.modelScale], [version.modelScale, context.modelScale],
    [version.context?.resolutionId, context.modelScale],
    [version.modelConfigId, context.configId], [version.context?.modelConfigId, context.configId]
  ]
  if (scopes.some(([actual, expected]) => actual != null && actual !== expected)) return null
  return version
}

function backendParameterVersion(row) {
  return {
    id: row.parameterVersionId, libraryId: LIBRARY_IDS.simulation, status: 'candidate',
    context: clone(row.context), contextKey: row.contextKey ?? row.context?.contextKey,
    source: row.resultSource, resultSource: row.resultSource, sourceTaskId: row.taskId,
    projectId: row.context?.projectId, resolutionId: row.context?.resolutionId, modelConfigId: row.context?.modelConfigId,
    parameters: clone(row.bestParameters), eventMetrics: clone(row.eventMetrics), hydrographs: clone(row.hydrographs),
    routingCandidate: clone(row.routingCandidate),
    productionEligible: row.productionEligible, qualityNotes: clone(row.warnings),
    independentTest: clone(row.independentTest),
    metrics: { calibration: clone(row.calibrationMetrics), validation: clone(row.validationMetrics), independentTest: clone(row.independentTest) },
    review: { status: '待审核' }
  }
}

function projectLibrarySource(projectData, context) {
  if (projectData?.manifest?.id && projectData.manifest.id !== context?.basinId) return null
  return projectData?.parameterLibrary
}

/** Saved same-case model candidates remain readable offline; old mocks are retained only in storage. */
export function parameterLibraryFor(projectData, projectId = projectData?.manifest?.id, resolutionId = projectData?.resolution?.id || projectData?.parameterLibrary?.resolutionId, activeContext = null) {
  const context = registeredLibraryContext(projectId, resolutionId, activeContext)
  const overrides = readOverrides()[contextKey(projectId, resolutionId)]
  const library = mergeLibrary(projectLibrarySource(projectData, context), overrides)
  for (const libraryId of ['simulationRateLibrary', 'automaticForecastLibrary']) {
    library[libraryId].versions = library[libraryId].versions.filter((row) => visibleParameterVersion(row, context))
  }
  for (const record of projectData?.backendParameterVersions || []) {
    if (!record || record.resultSource !== 'model-backend'
      || !visibleParameterVersion({ ...record, id: record.parameterVersionId }, context)) continue
    const row = backendParameterVersion(record)
    if (!visibleParameterVersion(row, context)) continue
    const index = library.simulationRateLibrary.versions.findIndex((version) => version.id === row.id)
    if (index < 0) library.simulationRateLibrary.versions.push(row)
    else {
      const existing = library.simulationRateLibrary.versions[index]
      // Numerical results and scientific eligibility always come from the server.
      // Preserve only a local review of the identical frozen task/context.
      library.simulationRateLibrary.versions[index] = existing.sourceTaskId === row.sourceTaskId
        ? { ...row, review: clone(existing.review || row.review), createdAt: existing.createdAt }
        : row
    }
    library.automaticForecastLibrary.versions = library.automaticForecastLibrary.versions.map((version) =>
      version.sourceTaskId === row.sourceTaskId
        ? { ...version, productionEligible: row.productionEligible, independentTest: clone(row.independentTest) }
        : version)
  }
  library.projectId = projectId
  library.resolutionId = resolutionId
  library.contextKey = context?.contextKey || null
  library.modelConfigId = context?.configId || null
  library.automaticForecastLibrary.versions = library.automaticForecastLibrary.versions.filter((version) =>
    version.status === 'production' && releaseReadiness(version).canPublish)
  const productionId = library.automaticForecastLibrary.productionVersion
  const production = library.automaticForecastLibrary.versions.find((version) => version.id === productionId && releaseReadiness(version).canPublish)
  library.automaticForecastLibrary.productionVersion = production?.id || null
  if (context?.basinId !== 'selinco') {
    library.simulationRateLibrary.status = library.simulationRateLibrary.versions.length ? '候选参数' : '暂无候选参数'
    if (!production) library.automaticForecastLibrary.status = '暂无发布版本'
  }
  return library
}

export function libraryIdFor(mode = 'automatic') {
  return mode === 'manual' || mode === 'simulation' ? LIBRARY_IDS.simulation : LIBRARY_IDS.automatic
}

export function libraryById(library, id) {
  if (id === LIBRARY_IDS.simulation) return library?.simulationRateLibrary || null
  if (id === LIBRARY_IDS.automatic) return library?.automaticForecastLibrary || null
  return null
}

export function productionParameterVersion(library) {
  const productionId = library?.automaticForecastLibrary?.productionVersion
  if (!productionId) return null
  return (library.automaticForecastLibrary.versions || []).find((version) => version.id === productionId
    && version.status === 'production' && releaseReadiness(version).canPublish
    && (!library.contextKey || (version.contextKey ?? version.context?.contextKey) === library.contextKey)) || null
}

export function independentMetricsFor(result = {}) {
  return result.independentTest || result.independentMetrics || result.metrics?.independentTest || result.metrics?.independent || null
}

export function independentTestPassed(metrics) {
  if (!metrics || typeof metrics !== 'object') return false
  if (metrics.passed === false || metrics.pass === false || metrics.status === 'failed') return false
  if (metrics.passed === true || metrics.pass === true || metrics.status === 'passed') return true
  const numeric = (value) => (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value))
  if (numeric(metrics.nse) && numeric(metrics.peakErrorPercent)
    && Number(metrics.nse) >= 0.5 && Math.abs(Number(metrics.peakErrorPercent)) <= 20) return true
  return false
}

export function independentTestConclusion(metrics) {
  if (!metrics || typeof metrics !== 'object') return '待返回'
  const explicit = metrics.conclusion || metrics.result || metrics.status
  if (explicit === '待生产验收') return explicit
  if (explicit && ['合格', '部分合格', '不合格', 'passed', 'failed', 'partial'].includes(String(explicit))) {
    if (explicit === 'passed') return '合格'
    if (explicit === 'failed') return '不合格'
    if (explicit === 'partial') return '部分合格'
    return explicit
  }
  if (metrics.partial === true || metrics.passed === 'partial') return '部分合格'
  return independentTestPassed(metrics) ? '合格' : '不合格'
}

function saveContextLibrary(projectId, resolutionId, library, activeContext = null, updatedLibraries = ['simulationRateLibrary', 'automaticForecastLibrary']) {
  const key = contextKey(projectId, resolutionId)
  const overrides = readOverrides()
  const context = registeredLibraryContext(projectId, resolutionId, activeContext)
  const previous = overrides[key] || {}
  const saved = { ...previous }
  for (const libraryId of updatedLibraries) {
    const current = clone(library[libraryId])
    const updatedIds = new Set(current.versions.map((row) => row.id))
    const priorVersions = Array.isArray(previous[libraryId]?.versions) ? previous[libraryId].versions : []
    const retained = priorVersions.filter((row) =>
      !visibleParameterVersion(row, context) || !updatedIds.has(row.id))
    saved[libraryId] = { ...previous[libraryId], ...current, versions: [...current.versions, ...clone(retained)] }
  }
  overrides[key] = saved
  writeOverrides(overrides)
}

function versionId(projectId, resolutionId) {
  const prefix = String(projectId || 'project').replace(/[^A-Za-z0-9]/g, '-').toUpperCase()
  const scale = String(resolutionId || '1km').toUpperCase()
  const stamp = now().replace(/[-:TZ.]/g, '').slice(0, 14)
  return `CAL-${prefix}-${scale}-${stamp}`
}

export function registerCalibrationResult({ projectData, projectId, resolutionId, request, result, taskId = null, activeContext = null } = {}) {
  if (!result || typeof result !== 'object') return null
  const resolvedProjectId = projectId || projectData?.manifest?.id
  const resolvedResolutionId = resolutionId || projectData?.resolution?.id || '1km'
  const context = registeredLibraryContext(resolvedProjectId, resolvedResolutionId, activeContext)
  const resultTaskId = taskId || result.taskId
  if (result.taskId != null && result.taskId !== resultTaskId) return null
  if (request?.context && ['projectId', 'resolutionId', 'modelConfigId', 'contextKey'].some((field) =>
    request.context[field] !== ({ projectId: context?.basinId, resolutionId: context?.modelScale, modelConfigId: context?.configId, contextKey: context?.contextKey })[field])) return null
  const normalized = { ...result, taskId: resultTaskId, context: result.context || request?.context }
  if (!visibleParameterVersion({ ...normalized, id: result.parameterVersionId || 'pending-registration' }, context)) return null
  const library = parameterLibraryFor(projectData, resolvedProjectId, resolvedResolutionId, activeContext)
  const independentTest = independentMetricsFor(result)
  const version = {
    id: result.parameterVersionId || versionId(resolvedProjectId, resolvedResolutionId),
    libraryId: LIBRARY_IDS.simulation,
    status: 'candidate',
    createdAt: now(),
    source: result.resultSource,
    resultSource: result.resultSource,
    productionEligible: result.productionEligible,
    qualityNotes: result.warnings || result.qualityNotes || [],
    sourceTaskId: resultTaskId,
    requestId: request?.requestId || null,
    context: clone(normalized.context),
    contextKey: normalized.contextKey ?? normalized.context?.contextKey,
    projectId: resolvedProjectId,
    resolutionId: resolvedResolutionId,
    modelProjectId: context.projectId,
    dataVersionId: request?.inputs?.precipitationDatasetId || null,
    eventSetVersion: request?.period?.eventSet?.version || null,
    stateVersionId: request?.period?.initialStateVersionId || null,
    modelConfigId: context.configId,
    parameters: result.bestParameters || result.parameters || null,
    routingCandidate: clone(result.routingCandidate),
    metrics: {
      calibration: result.calibrationMetrics || result.metrics?.calibration || result.metrics || null,
      validation: result.validationMetrics || result.metrics?.validation || null,
      independentTest
    },
    eventMetrics: result.eventMetrics || result.perEventMetrics || null,
    hydrographs: result.hydrographs || result.simulatedHydrographs || null,
    independentTest: independentTest
      ? { ...clone(independentTest), passed: independentTestPassed(independentTest) }
      : null,
    review: { status: '待审核', reviewer: null, reviewedAt: null, note: '' }
  }
  if (!visibleParameterVersion(version, context)) return null
  library.simulationRateLibrary.versions = [version, ...(library.simulationRateLibrary.versions || []).filter((item) => item.id !== version.id)]
  saveContextLibrary(resolvedProjectId, resolvedResolutionId, library, activeContext, ['simulationRateLibrary'])
  return version
}

export function reviewSimulationParameter({ projectData, projectId, resolutionId, versionId: id, decision, reviewer = '当前用户', note = '', activeContext = null } = {}) {
  const library = parameterLibraryFor(projectData, projectId, resolutionId, activeContext)
  const versions = library.simulationRateLibrary?.versions || []
  const index = versions.findIndex((version) => version.id === id)
  if (index < 0) throw new Error('找不到待审核的率定参数版本')
  const current = versions[index]
  if (!current.independentTest || !independentTestPassed(current.independentTest)) {
    throw new Error('独立检验尚未通过，不能审核发布')
  }
  const normalizedDecision = decision === 'reject' ? 'rejected' : decision === 'approve' ? 'approved' : 'pending-review'
  versions[index] = {
    ...current,
    status: normalizedDecision,
    review: { status: normalizedDecision === 'approved' ? '审核通过' : normalizedDecision === 'rejected' ? '已退回' : '待审核', reviewer, reviewedAt: now(), note }
  }
  saveContextLibrary(projectId, resolutionId, library, activeContext, ['simulationRateLibrary'])
  return versions[index]
}

export function publishAutomaticParameter({ projectData, projectId, resolutionId, versionId: id, reviewer = '当前用户', note = '', activeContext = null } = {}) {
  const library = parameterLibraryFor(projectData, projectId, resolutionId, activeContext)
  const candidate = (library.simulationRateLibrary?.versions || []).find((version) => version.id === id)
  if (!candidate) throw new Error('找不到率定候选参数版本')
  if (candidate.productionEligible !== true || (candidate.source ?? candidate.resultSource) !== 'model-backend'
    || !visibleParameterVersion(candidate, parameterContext(candidate))) {
    throw new Error('该参数尚无明确的真实模型生产验收资格，不能发布到自动预报。')
  }
  if (!candidate.independentTest || !independentTestPassed(candidate.independentTest)) {
    throw new Error('独立检验尚未通过，不能发布自动预报参数')
  }
  if (candidate.review?.status !== '审核通过') throw new Error('请先完成人工审核')
  const published = {
    ...clone(candidate),
    id: `AUTO-${candidate.id}`,
    libraryId: LIBRARY_IDS.automatic,
    status: 'production',
    publishedAt: now(),
    review: { ...candidate.review, reviewer, reviewedAt: now(), note }
  }
  const automatic = library.automaticForecastLibrary
  automatic.versions = [published, ...(automatic.versions || []).filter((version) => version.id !== published.id)]
  automatic.productionVersion = published.id
  automatic.status = '已发布生产版本'
  saveContextLibrary(projectId, resolutionId, library, activeContext, ['automaticForecastLibrary'])
  return published
}

export function releaseReadiness(version) {
  const qualified = version?.productionEligible === true && (version?.source ?? version?.resultSource) === 'model-backend'
    && Boolean(visibleParameterVersion(version, parameterContext(version)))
  return {
    hasIndependentTest: Boolean(version?.independentTest),
    independentPassed: independentTestPassed(version?.independentTest),
    reviewed: version?.review?.status === '审核通过',
    canPublish: Boolean(qualified && version?.independentTest && independentTestPassed(version.independentTest) && version.review?.status === '审核通过')
  }
}

export { LIBRARY_IDS }
