export function isEditableDiagnosticSource(task) {
  const request = task?.runConfig || task?.request || {}
  const calibration = request.calibration || {}
  return request.runType === 'calibration' && calibration.evaluationMode !== 'manual' && calibration.target !== 'routing'
    && !request.sourcePlan && calibration.optimizer?.initializationPolicy !== 'SOURCE_INITIAL_CANDIDATES'
}
