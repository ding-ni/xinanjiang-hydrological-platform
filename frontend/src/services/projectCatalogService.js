import { registerDomainCatalog } from '../data/domainContext.js'
import { clearProjectDataCache, loadProjectCatalog, loadProjectManifest } from './projectDataService.js'
import { resolveResourceUrl, resourceUrl } from './resourceUrl.js'

let catalogPromise = null

/**
 * Load the portable project catalog and register every readable manifest.
 *
 * The catalog is the extension point for future cases: a new case only needs
 * a catalog entry and a manifest-backed data directory.  Manifest failures
 * are reported per case so one incomplete case does not hide the other cases
 * that are already usable in the frontend demo.
 */
export function loadAndRegisterProjectCatalog({ force = false } = {}) {
  if (!force && catalogPromise) return catalogPromise
  if (force) clearProjectDataCache()

  catalogPromise = (async () => {
    const catalog = await loadProjectCatalog()
    const catalogUrl = resourceUrl('/data/projects/catalog.json')
    const entries = Array.isArray(catalog?.projects) ? catalog.projects : []
    const manifestsById = {}
    const manifestUrlsById = {}
    const errors = []

    await Promise.all(entries.map(async (entry) => {
      if (!entry?.id) return
      // Resolve catalog entries against catalog.json, not the current Vue
      // route.  This is what keeps ./case/manifest.json portable on hash
      // routes and when the built site is copied to another directory.
      manifestUrlsById[entry.id] = entry.manifest
        ? resolveResourceUrl(entry.manifest, catalogUrl)
        : null
      if (!entry.manifest) {
        errors.push({ id: entry.id, error: '未配置 manifest 路径' })
        return
      }
      try {
        manifestsById[entry.id] = await loadProjectManifest(entry.manifest, catalogUrl)
      } catch (error) {
        errors.push({
          id: entry.id,
          error: error instanceof Error ? error.message : String(error)
        })
      }
    }))

    // Carry the catalog-level model framework into entries that omit it. This
    // keeps the catalog concise while still allowing future catalogs to host
    // more than one model framework without changing the registry API.
    const normalizedCatalog = {
      ...catalog,
      projects: entries.map((entry) => ({
        ...entry,
        modelFrameworkId: entry.modelFrameworkId || catalog.modelFramework?.id,
        modelFrameworkName: entry.modelFrameworkName || catalog.modelFramework?.name
      }))
    }
    const domains = registerDomainCatalog(normalizedCatalog, manifestsById, manifestUrlsById)
    return {
      catalog,
      manifestsById,
      manifestUrlsById,
      domains,
      errors
    }
  })().catch((error) => {
    // Allow a later retry after a transient static-server or deployment-path
    // failure.  The caller still receives the original error for diagnostics.
    catalogPromise = null
    throw error
  })

  return catalogPromise
}

export function clearProjectCatalogCache() {
  catalogPromise = null
}

