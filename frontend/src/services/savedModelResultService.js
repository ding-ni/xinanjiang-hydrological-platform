import { resolveDomainContext } from '../data/domainContext.js'
import { isCaseFixtureContext } from './caseAvailabilityService.js'

function hasRegisteredFixtureContext(context) {
  if (!isCaseFixtureContext(context) || !['1km', '0p1deg'].includes(context.modelScale)
    || !context.configId || !context.contextKey) return false
  const registered = resolveDomainContext('selinco', { modelScale: context.modelScale, configId: context.configId })
  return isCaseFixtureContext(registered) && registered.modelScale === context.modelScale
    && registered.configId === context.configId && registered.contextKey === context.contextKey
}

function belongsToContext(result, context) {
  const keys = [result.contextKey, result.context?.contextKey].filter((value) => value != null)
  if (keys.some((key) => key !== context.contextKey)) return false
  const scopes = [
    [result.context?.projectId ?? result.domainId, context.basinId],
    [result.context?.resolutionId ?? result.modelScale, context.modelScale],
    [result.context?.modelConfigId ?? result.modelConfigId, context.configId]
  ]
  return scopes.every(([actual, expected]) => actual == null || actual === expected)
}

/**
 * Read-only projection of a saved result. Keep legacy records in storage, but
 * never promote an unlabelled/mock/cross-case result into the active workspace.
 * A genuine saved model result remains viewable while its service is offline.
 */
export function visibleWorkspaceResult(result, context) {
  if (!result || typeof result !== 'object' || !context?.contextKey || !belongsToContext(result, context)) return null
  if (result.resultSource === 'model-backend') {
    const key = result.contextKey ?? result.context?.contextKey
    return /^JOB-[A-Za-z0-9][A-Za-z0-9_-]*$/.test(String(result.taskId || '')) && key === context.contextKey ? result : null
  }
  if (result.resultSource === 'frontend-fixture' && hasRegisteredFixtureContext(context)
    && !String(result.taskId || '').startsWith('JOB-')) return result
  return null
}
