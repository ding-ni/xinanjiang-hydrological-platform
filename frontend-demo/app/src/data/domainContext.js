import { resourceUrl } from '../services/resourceUrl.js'

/**
 * 同步的前端工作区注册表。
 *
 * 这里只保留应用启动和上下文切换所需的最小字段。站点、断面、数据包、
 * 空间文件和样例结果由 public/data/projects 下的 manifest 与目录文件提供。
 */
export const DEFAULT_BASIN_ID = 'selinco'
export const DOMAIN_CONTEXT_SELECTIONS_KEY = 'hydro_model_context_selection_v2'
export const WORK_CONTEXTS_KEY = 'hydro_work_contexts_v2'

const LEGACY_BASIN_ALIASES = Object.freeze({
  xinanjiang: 'nananxi-xufan'
})

function resolutionConfig({ id, label, paramVersion, statePrefix, status, statusLabel, forcing = null, spatial = {} }) {
  return {
    id,
    name: label,
    label,
    modelScale: id.endsWith('0p1deg') ? '0p1deg' : '1km',
    spatialResolutionId: id.endsWith('0p1deg') ? '0p1deg' : '1km',
    basinConfigVersionId: `${id}-config-v1`,
    paramVersion,
    statePrefix,
    status,
    statusLabel,
    forcing,
    spatial
  }
}

// The registry starts with a checked-in offline fallback so the shell can
// render before the catalog request completes.  It is intentionally mutable:
// project manifests loaded from `public/data/projects/catalog.json` register
// additional cases at runtime.  Keeping the fallback and the runtime entries
// in one registry means the rest of the application can continue using the
// existing synchronous context helpers.
export const DOMAIN_REGISTRY = {
  'nananxi-xufan': {
    id: 'nananxi-xufan',
    name: '南安溪-徐畈',
    shortName: '南安溪-徐畈',
    basinType: 'mountain-single-outlet',
    manifestUrl: resourceUrl('/data/projects/nananxi-xufan/manifest.json'),
    capabilities: {
      coldRegion: false,
      lakeBalance: false,
      multiOutlet: false,
      observationCatalog: true,
      eventCatalog: true
    },
    defaultProjectId: 'distributed-xinanjiang',
    projects: {
      'distributed-xinanjiang': {
        id: 'distributed-xinanjiang',
        name: '分布式新安江模型',
        defaultConfigId: 'nananxi-xufan-1km',
        configs: {
          'nananxi-xufan-1km': resolutionConfig({
            id: 'nananxi-xufan-1km', label: '1 km', paramVersion: 'XAJ-XF-1KM-v0.1',
            statePrefix: 'STATE-XF-1KM', status: 'partial', statusLabel: '实测资料已接入',
            spatial: { label: '1 km', cellCount: null }
          }),
          'nananxi-xufan-0p1deg': resolutionConfig({
            id: 'nananxi-xufan-0p1deg', label: '0.1°', paramVersion: 'XAJ-XF-0P1-v0.1',
            statePrefix: 'STATE-XF-0P1', status: 'partial', statusLabel: '实测资料已接入',
            spatial: { label: '0.1°', cellCount: null }
          })
        }
      }
    }
  },
  llpq: {
    id: 'llpq',
    name: '里龙普曲',
    shortName: '里龙普曲',
    basinType: 'cold-region-single-outlet',
    manifestUrl: resourceUrl('/data/projects/llpq/manifest.json'),
    capabilities: {
      coldRegion: true,
      lakeBalance: false,
      multiOutlet: false,
      observationCatalog: false,
      eventCatalog: false
    },
    defaultProjectId: 'distributed-xinanjiang',
    projects: {
      'distributed-xinanjiang': {
        id: 'distributed-xinanjiang',
        name: '分布式新安江模型',
        defaultConfigId: 'llpq-1km',
        configs: {
          'llpq-1km': resolutionConfig({
            id: 'llpq-1km', label: '1 km', paramVersion: 'XAJ-LLPQ-1KM-PENDING',
            statePrefix: 'STATE-LLPQ-1KM', status: 'spatial-only', statusLabel: '仅空间资料',
            spatial: { label: '1 km', cellCount: null }
          }),
          'llpq-0p1deg': resolutionConfig({
            id: 'llpq-0p1deg', label: '0.1°', paramVersion: 'XAJ-LLPQ-0P1-PENDING',
            statePrefix: 'STATE-LLPQ-0P1', status: 'spatial-only', statusLabel: '仅空间资料',
            spatial: { label: '0.1°', cellCount: null }
          })
        }
      }
    }
  },
  selinco: {
    id: 'selinco',
    name: '色林错',
    shortName: '色林错',
    basinType: 'cold-region-multi-inflow-lake',
    manifestUrl: resourceUrl('/data/projects/selinco/manifest.json'),
    capabilities: {
      coldRegion: true,
      lakeBalance: true,
      multiOutlet: true,
      observationCatalog: false,
      eventCatalog: false
    },
    defaultProjectId: 'selinco-inflow-lake',
    projects: {
      'selinco-inflow-lake': {
        id: 'selinco-inflow-lake',
        name: '分布式新安江模型 · 多入湖口水量平衡',
        defaultConfigId: 'selinco-1km',
        configs: {
          'selinco-1km': resolutionConfig({
            id: 'selinco-1km', label: '1 km', paramVersion: 'XAJ-SEL-v1.0',
            statePrefix: 'STATE-SEL', status: 'frontend-fixture', statusLabel: '试算资料',
            forcing: { name: 'selinco_precip_hourly_1km.nc', resolution: '1 km' },
            spatial: { label: '1 km', cellCount: 36263, terrain: { elevationMin: 4534, elevationMax: 6210 } }
          }),
          'selinco-0p1deg': resolutionConfig({
            id: 'selinco-0p1deg', label: '0.1°', paramVersion: 'XAJ-SEL-0P1-v1.0',
            statePrefix: 'STATE-SEL-0P1', status: 'frontend-fixture', statusLabel: '试算资料',
            forcing: { name: 'selinco_precip_hourly_0p1deg.nc', resolution: '0.1°' },
            spatial: { label: '0.1°', cellCount: 357, terrain: { elevationMin: 4539, elevationMax: 5557 } }
          })
        }
      }
    }
  }
}

function clonePlain(value) {
  if (Array.isArray(value)) return value.map(clonePlain)
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clonePlain(item)]))
  }
  return value
}

function mergeDefined(base, override) {
  const result = { ...(base || {}) }
  Object.entries(override || {}).forEach(([key, value]) => {
    if (value === undefined || value === null) return
    if (value && typeof value === 'object' && !Array.isArray(value) && result[key] && typeof result[key] === 'object') {
      result[key] = mergeDefined(result[key], value)
    } else {
      result[key] = clonePlain(value)
    }
  })
  return result
}

function safeIdPart(value, fallback = 'MODEL') {
  const normalized = String(value || fallback)
    .trim()
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return normalized || fallback
}

function normalizeResolutionKey(key) {
  return String(key || '1km').trim() || '1km'
}

function buildConfigFromResolution(basinId, resolutionId, resolution = {}, fallback = {}) {
  const normalizedResolutionId = normalizeResolutionKey(resolutionId)
  const configId = `${basinId}-${normalizedResolutionId}`
  const modelScale = normalizedResolutionId
  const fallbackConfig = fallback || {}
  return {
    id: configId,
    name: resolution.name || resolution.label || fallbackConfig.name || normalizedResolutionId,
    label: resolution.label || resolution.name || fallbackConfig.label || normalizedResolutionId,
    modelScale,
    spatialResolutionId: normalizedResolutionId,
    basinConfigVersionId: resolution.basinConfigVersionId || fallbackConfig.basinConfigVersionId || `${configId}-config-v1`,
    paramVersion: resolution.paramVersion || resolution.parameterVersion || fallbackConfig.paramVersion || `${configId}-PENDING`,
    statePrefix: resolution.statePrefix || fallbackConfig.statePrefix || `STATE-${safeIdPart(basinId)}-${safeIdPart(normalizedResolutionId)}`,
    status: resolution.status || fallbackConfig.status || 'registered',
    statusLabel: resolution.statusLabel || fallbackConfig.statusLabel || '已登记，等待资料接入',
    forcing: resolution.forcing || fallbackConfig.forcing || null,
    spatial: mergeDefined(fallbackConfig.spatial || {}, resolution.spatial || {})
  }
}

function buildDomainFromManifest(entry = {}, manifest = {}, manifestUrl = null) {
  const basinId = manifest.id || entry.id
  if (!basinId) return null

  const existing = DOMAIN_REGISTRY[basinId]
  const existingProjectId = existing?.defaultProjectId
  // Existing offline definitions may use a case-specific project id (for
  // example selinco-inflow-lake).  Keep that id stable when the manifest only
  // declares the shared model framework; new cases use the manifest/catalog
  // framework id as their default project.
  const modelProjectId = manifest.defaultProjectId || existing?.defaultProjectId || manifest.modelFrameworkId || entry.modelFrameworkId || 'distributed-xinanjiang'
  const existingProject = existing?.projects?.[existingProjectId] || existing?.projects?.[modelProjectId]
  const resolutionEntries = Object.entries(manifest.resolutions || {})
  const fallbackConfigs = existingProject?.configs || {}
  const configs = {}

  if (resolutionEntries.length) {
    resolutionEntries.forEach(([resolutionId, resolution]) => {
      const configId = `${basinId}-${normalizeResolutionKey(resolutionId)}`
      configs[configId] = buildConfigFromResolution(
        basinId,
        resolutionId,
        resolution,
        fallbackConfigs[configId] || {}
      )
    })
  } else if (Object.keys(fallbackConfigs).length) {
    Object.assign(configs, clonePlain(fallbackConfigs))
  } else {
    configs[`${basinId}-1km`] = buildConfigFromResolution(basinId, '1km')
  }

  const fallbackProject = existingProject || {}
  const project = {
    ...clonePlain(fallbackProject),
    id: modelProjectId,
    name: entry.modelFrameworkName || manifest.modelFrameworkName || fallbackProject.name || '分布式新安江模型',
    defaultConfigId: manifest.defaultConfigId || fallbackProject.defaultConfigId || Object.keys(configs)[0],
    configs
  }

  const generated = {
    ...(existing ? clonePlain(existing) : {}),
    id: basinId,
    name: manifest.name || entry.name || existing?.name || basinId,
    shortName: manifest.shortName || entry.shortName || existing?.shortName || manifest.name || entry.name || basinId,
    basinType: manifest.basinType || entry.basinType || existing?.basinType || 'custom-basin',
    manifestUrl: manifestUrl || manifest.manifestUrl || existing?.manifestUrl || null,
    // Keep the normalized manifest alongside the lightweight context.  This
    // lets services resolve case-specific resources (observations, mappings,
    // optional validation rules) without importing a case-id map.
    manifest: Object.keys(manifest || {}).length ? clonePlain(manifest) : existing?.manifest || null,
    capabilities: mergeDefined(existing?.capabilities || {}, manifest.capabilities || entry.capabilities || {}),
    defaultProjectId: modelProjectId,
    projects: {
      ...(existing?.projects || {}),
      [modelProjectId]: project
    }
  }

  // A catalog entry may carry presentation-only metadata.  Preserve it in
  // the context so new case cards/selectors can use it without another map.
  return mergeDefined(generated, {
    icon: manifest.icon || entry.icon,
    description: manifest.description || entry.description,
    tags: manifest.tags || entry.tags,
    source: manifest.source || entry.source
  })
}

/**
 * Register cases discovered from the portable catalog.
 *
 * `manifestsById` is optional so callers can register a catalog in stages;
 * entries without a loaded manifest keep the offline fallback (when one
 * exists) and are skipped otherwise.  The function is synchronous on purpose
 * so stores and components can continue to use the existing context helpers.
 */
export function registerDomainCatalog(catalog, manifestsById = {}, manifestUrlsById = {}) {
  const entries = Array.isArray(catalog?.projects) ? catalog.projects : []
  const registered = []
  entries.forEach((entry) => {
    const id = entry?.id || manifestsById?.[entry?.id]?.id
    const manifest = id ? manifestsById[id] : null
    const manifestUrl = id ? manifestUrlsById[id] || entry?.manifest : entry?.manifest
    const definition = buildDomainFromManifest(entry, manifest || entry, manifestUrl)
    if (!definition) return
    DOMAIN_REGISTRY[definition.id] = definition
    registered.push(definition)
  })
  return registered
}

export function registerDomainDefinition(definition, manifestUrl = null) {
  const generated = buildDomainFromManifest(definition, definition, manifestUrl)
  if (!generated) return null
  DOMAIN_REGISTRY[generated.id] = generated
  return generated
}

export function normalizeBasinId(basinId) {
  const normalized = LEGACY_BASIN_ALIASES[basinId] || basinId
  return DOMAIN_REGISTRY[normalized] ? normalized : DEFAULT_BASIN_ID
}

export function listBasins() {
  return Object.values(DOMAIN_REGISTRY)
}

export function getBasinDefinition(basinId) {
  return DOMAIN_REGISTRY[normalizeBasinId(basinId)]
}

export function getProjectDefinition(basinId, projectId) {
  const basin = getBasinDefinition(basinId)
  return basin.projects[projectId] || basin.projects[basin.defaultProjectId] || Object.values(basin.projects)[0]
}

export function getModelConfigDefinition(basinId, projectId, configId) {
  const project = getProjectDefinition(basinId, projectId)
  return project.configs[configId] || project.configs[project.defaultConfigId] || Object.values(project.configs)[0]
}

export function resolveDomainContext(basinId, options = {}) {
  const basin = getBasinDefinition(basinId)
  const project = getProjectDefinition(basin.id, options.projectId)
  const matchingScale = Object.values(project.configs).find((config) =>
    config.modelScale === options.modelScale || config.spatialResolutionId === options.spatialResolutionId)
  const config = project.configs[options.configId] || matchingScale || getModelConfigDefinition(basin.id, project.id)
  const contextKey = `${basin.id}:${project.id}:${config.id}`

  return {
    basinId: basin.id,
    basinProjectId: basin.id,
    basinName: basin.name,
    projectId: project.id,
    projectName: project.name,
    configId: config.id,
    basinConfigVersionId: config.basinConfigVersionId,
    spatialResolutionId: config.spatialResolutionId,
    contextKey,
    modelScale: config.modelScale,
    manifestUrl: basin.manifestUrl,
    manifest: basin.manifest || null,
    capabilities: basin.capabilities,
    status: config.status,
    statusLabel: config.statusLabel,
    basin,
    project,
    modelConfig: config
  }
}

export function getContextKey(basinId, options = {}) {
  return resolveDomainContext(basinId, options).contextKey
}

export function listBasinConfigs(basinId, projectId) {
  const project = getProjectDefinition(basinId, projectId)
  return Object.values(project.configs)
}
