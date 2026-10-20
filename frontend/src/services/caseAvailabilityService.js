const OPERATION_LABELS = {
  forecast: '预报',
  stateInitialization: '状态初始化',
  stateContinuation: '状态续算',
  eventCalibration: '洪水场次率定',
  continuousCalibration: '连续时段率定',
  continuousSimulation: '连续模拟'
}

/** Reject stale project data while another case/resolution is loading. */
export function projectDataForContext(context, projectData, { loading = false, error = '' } = {}) {
  if (loading || error || !context || !projectData) return null
  if (projectData.manifest?.id !== context.basinId) return null
  const expected = projectData.manifest.resolutions?.[context.spatialResolutionId || context.modelScale]
  if (!expected || !projectData.resolution) return null
  for (const field of ['spatialManifest', 'datasetCatalog', 'fixtureCatalog']) {
    if (expected[field] !== projectData.resolution[field]) return null
  }
  return projectData
}

/** The current local response engine is a Selinco fixture, not a general model. */
export function isCaseFixtureContext(context) {
  return context?.basinId === 'selinco' && context.status === 'frontend-fixture'
}

/** Capabilities describe supported features; they do not prove execution readiness. */
export function caseExecutionAvailability(context, projectData, operation, status = {}) {
  const label = OPERATION_LABELS[operation] || '模型计算'
  const blocked = (reason, missingInputs = []) => ({ ready: false, mode: null, reason, missingInputs })
  if (status.loading) return blocked('项目资料目录正在加载。')
  if (status.error) return blocked(`项目资料目录加载失败：${status.error}`)
  const data = projectDataForContext(context, projectData, status)
  if (!data) return blocked('当前案例与尺度的资料尚未加载。')
  const capability = operation === 'stateInitialization' ? 'stateContinuation' : operation
  if (data.manifest.capabilities?.[capability] !== true) return blocked(`当前案例尚未启用${label}能力。`)
  if (data.backend?.mode === 'model-backend') {
    if (data.backendError) return blocked(`真实模型服务离线：${data.backendError}`)
    if (data.backend.ready && data.backend.operations.includes(operation)) {
      return { ready: true, mode: 'model-backend', reason: '模型服务可用', missingInputs: [] }
    }
    return blocked(data.backend.unsupported?.[operation] || data.backend.reason || `${label}服务不可用。`, data.backend.missingInputs)
  }
  if (data.backendError && context?.basinId === 'nananxi-xufan') return blocked(`真实模型服务离线：${data.backendError}`)
  const missingInputs = data.resolution.missingInputs || data.datasets?.missingInputs || []
  if (data.resolution.inputStatus === 'missing') {
    return blocked(missingInputs.length ? `缺少${missingInputs.join('、')}，${label}不可执行。` : `${label}所需资料未接入。`, missingInputs)
  }
  if (!data.datasets?.datasets?.length) return blocked(`${label}所需的小时资料未登记。`, missingInputs)
  if (!isCaseFixtureContext(context) || data.resolution.status !== 'frontend-fixture' || data.fixtures?.resultSource !== 'frontend-fixture') {
    return blocked(`${label}计算服务不可用。`, missingInputs)
  }
  return { ready: true, mode: 'frontend-fixture', reason: '当前仅为已登记的前端试算演示。', missingInputs: [] }
}
