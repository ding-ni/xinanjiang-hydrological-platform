// Build a simulation draft from frozen calculation conditions and its optimum.
// This function never submits a calculation or mutates the archived request.
export function simulationDraft(request, result, continuousRequest = null) {
  if (!result?.bestParameters || !request?.period) throw new Error('该成果没有可采用的率定参数')
  const draft = JSON.parse(JSON.stringify(continuousRequest || request))
  delete draft.sourcePlan
  delete draft.requestId
  delete draft.scheme
  delete draft.lineage
  draft.inputs = { ...draft.inputs }
  delete draft.inputs.preparationTaskId
  if (request.period.mode === 'event') {
    draft.calibration = { ...draft.calibration, initialParameters: { ...result.bestParameters },
      evaluationMode: 'manual', simulationOnly: true }
    if (result.parameterVersionId) draft.calibration.initialParameterVersionId = result.parameterVersionId
    for (const key of ['searchParameters', 'routingSearchParameters', 'target', 'jointProfile']) delete draft.calibration[key]
  } else {
    if (!continuousRequest) {
      const periods = request.period.windows || result.periods || []
      const starts = periods.map(row => row.start), ends = periods.map(row => row.end)
      if (!starts.length) throw new Error('成果缺少连续计算时段')
      draft.period = { startTime: starts.sort()[0], endTime: ends.sort().at(-1), warmupDays: request.period.warmupDays,
        ...(result.continuousOrigin ? { continuousOrigin: result.continuousOrigin } : {}) }
    }
    draft.runType = 'simulation'
    draft.parameters = { ...result.bestParameters }
    delete draft.calibration
  }
  if (result.parameterVersionId) draft.lineage = { parameterVersionId: result.parameterVersionId }
  if (result.routingCandidate?.routingVersionId) draft.routing = { versionId: result.routingCandidate.routingVersionId }
  return draft
}
