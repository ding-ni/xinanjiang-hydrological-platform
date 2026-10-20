const identityFields = ['modelImplementationSHA256', 'scienceConfigurationSHA256', 'petConfigurationSHA256']

export function checkpointForState(state) {
  return state?.checkpoint || state || {}
}

export function stateScienceBlockReason(state, modelImplementationSHA256) {
  const checkpoint = checkpointForState(state)
  const version = checkpoint.scienceVersionId || checkpoint.science?.versionId || checkpoint.lineage?.scienceVersionId
  if (!version || identityFields.some(key => !/^[0-9a-f]{64}$/i.test(checkpoint[key] || checkpoint.lineage?.[key] || ''))) {
    return '历史状态缺少计算条件记录，请重新生成初始状态；原状态仍可查看和导出。'
  }
  if (modelImplementationSHA256 && (checkpoint.modelImplementationSHA256 || checkpoint.lineage?.modelImplementationSHA256) !== modelImplementationSHA256 && state?.implementationReplayable !== true && checkpoint.implementationReplayable !== true) {
    return '此状态由升级前模型生成，请重新初始化；原状态仍可查看和导出。'
  }
  return ''
}

// Continuation always follows the actual parent; the calibration workspace
// cannot replace its PET or initial-state configuration.
export function scientificRequestSelection({ parentState, selected, initializationStrategy, modelImplementationSHA256 } = {}) {
  let version
  if (parentState) {
    const reason = stateScienceBlockReason(parentState, modelImplementationSHA256)
    if (reason) throw new Error(reason)
    const checkpoint = checkpointForState(parentState)
    version = checkpoint.scienceVersionId || checkpoint.science?.versionId || checkpoint.lineage?.scienceVersionId
  } else {
    const continuous = selected?.statePolicy === 'continuous-origin'
    if (!continuous || initializationStrategy === 'CONTINUOUS') version = selected?.scienceVersionId || selected?.science?.versionId
  }
  return version
    ? { science: { versionId: version }, evaporation: { mode: 'science-version' } }
    : { evaporation: { mode: 'monthly-climatology-assumption' } }
}

export function scientificConditionLabel(state) {
  const checkpoint = checkpointForState(state)
  if (stateScienceBlockReason(state)) return '需重新初始化'
  return checkpoint.petMode === 'CONSTANT_ZERO_EXPLICIT_REQUEST' ? '沿用原状态的零蒸散发' : '沿用原状态的蒸散发与初态配置'
}
