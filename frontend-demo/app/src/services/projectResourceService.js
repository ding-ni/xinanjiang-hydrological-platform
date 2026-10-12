import { resolveResourceUrl, resourceUrl } from './resourceUrl.js'

/**
 * Resolve resources belonging to the active case without assuming a drive,
 * checkout directory, or a fixed case id.  A context may be the app store, an
 * activeContext object, or projectData returned by projectDataService.
 */
function asContext(source = {}) {
  return source?.activeContext || source?.context || source || {}
}

export function manifestUrlFor(source = {}) {
  const context = asContext(source)
  return context.manifestUrl || context.projectData?.manifestUrl || context.manifest?.manifestUrl || null
}

export function projectResourceUrl(source, relativePath, fallbackPath = null) {
  const rawPath = String(relativePath || '')
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(rawPath)) return rawPath
  if (rawPath.startsWith('/data/') || rawPath.startsWith('data/')) return resourceUrl(rawPath)

  const path = rawPath.replace(/^\/+/, '')
  if (!path) return fallbackPath ? resourceUrl(fallbackPath) : null

  const manifestUrl = manifestUrlFor(source)
  if (manifestUrl) return resolveResourceUrl(path, manifestUrl)
  return fallbackPath ? resourceUrl(fallbackPath) : `./${path}`
}

export function projectResourceRoot(source, fallbackProjectId = null) {
  const manifestUrl = manifestUrlFor(source)
  if (manifestUrl) return resolveResourceUrl('./', manifestUrl).replace(/\/?$/, '/')
  if (fallbackProjectId) return `${resourceUrl(`/data/projects/${fallbackProjectId}`)}/`
  return null
}

