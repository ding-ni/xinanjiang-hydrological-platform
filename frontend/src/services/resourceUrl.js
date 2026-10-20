/**
 * Resolve browser-bundled static resources against Vite's configured base URL.
 *
 * The project data manifests were originally authored with `/data/...` paths.
 * Those paths work from the development server root, but break when the built
 * frontend is copied under another folder.  This helper keeps static resources
 * portable while deliberately leaving API URLs and other external URLs alone.
 */
const BASE_URL = import.meta.env?.BASE_URL || './'

function splitSuffix(value) {
  const match = String(value).match(/[?#]/)
  if (!match) return { path: String(value), suffix: '' }
  const index = match.index ?? String(value).length
  return { path: String(value).slice(0, index), suffix: String(value).slice(index) }
}

function joinBase(path) {
  const base = String(BASE_URL || './')
  const cleanPath = String(path).replace(/^\/+/, '')
  if (base === '.' || base === './') return `./${cleanPath}`
  if (base.startsWith('/')) return `${base.replace(/\/+$/, '')}/${cleanPath}`
  return `./${base.replace(/^\.\//, '').replace(/\/+$/, '')}/${cleanPath}`
}

function documentBaseUrl() {
  if (typeof document !== 'undefined' && document.baseURI) return document.baseURI
  if (typeof window !== 'undefined' && window.location?.href) return window.location.href
  return null
}

function normalizePathSegments(path) {
  const prefix = path.startsWith('./') ? './' : path.startsWith('/') ? '/' : ''
  const parts = path.replace(/^\.\//, '').replace(/^\/+/, '').split('/')
  const normalized = []
  for (const part of parts) {
    if (!part || part === '.') continue
    if (part === '..') {
      if (normalized.length && normalized.at(-1) !== '..') normalized.pop()
      else if (!prefix) normalized.push(part)
      continue
    }
    normalized.push(part)
  }
  return `${prefix}${normalized.join('/')}`
}

function resolveRelativePath(value, baseUrl) {
  if (!baseUrl || !/^(?:\.\.\/|\.\/)/.test(value)) return value

  // Keep local project paths relative even in the browser.  Returning an
  // absolute `http://host/...` URL here would leak the current dev-server
  // origin into saved request drafts and make copied deployments less
  // portable.  Remote manifest URLs are the one case where an absolute URL is
  // intentional and should be resolved with the browser URL constructor.
  if (isExternalUrl(baseUrl)) {
    const documentBase = documentBaseUrl()
    try {
      const absoluteBase = documentBase
        ? new URL(resourceUrl(baseUrl), documentBase)
        : new URL(resourceUrl(baseUrl))
      return new URL(value, absoluteBase).href
    } catch {
      // Fall through to a URL-independent path resolver for build-time and
      // Node-based checks where no browser location exists.
    }
  }

  const { path: basePath } = splitSuffix(resourceUrl(baseUrl))
  const directory = basePath.slice(0, basePath.lastIndexOf('/') + 1)
  return normalizePathSegments(`${directory}${value}`)
}

function isExternalUrl(value) {
  return /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(String(value))
}

/**
 * Convert a bundled static resource path to a path that follows Vite's base.
 * API URLs (for example `http://localhost:8000/...`) are returned unchanged.
 */
export function resourceUrl(value) {
  if (value == null || typeof value !== 'string' || !value) return value
  if (isExternalUrl(value) || value.startsWith('#')) return value

  const { path, suffix } = splitSuffix(value)
  if (!path) return value
  if (path.startsWith('../') || path.startsWith('./')) return value

  // Only root `/data` paths are rewritten.  Other root paths may be API or
  // deployment-specific URLs and must retain their original semantics.
  if (path.startsWith('/data/')) return `${joinBase(path)}${suffix}`
  if (path.startsWith('data/')) return `${joinBase(path)}${suffix}`
  return value
}

/**
 * Resolve a static resource path against the file that declared it.
 *
 * Manifests may use either the historical `/data/...` form or a relative
 * path such as `./1km/spatial.json`.  The latter must be resolved against
 * the manifest URL rather than against the current Vue route.  External URLs
 * and API paths remain unchanged.
 */
export function resolveResourceUrl(value, baseUrl = null) {
  if (value == null || typeof value !== 'string' || !value) return value
  if (isExternalUrl(value) || value.startsWith('#')) return value

  const { path, suffix } = splitSuffix(value)
  if (!path) return value

  // JSON resources returned by the loader are already relative to the app
  // base (for example ./data/projects/case/manifest.json). Resolving them
  // against another manifest a second time would duplicate data/projects.
  if (path.startsWith(joinBase('data/'))) return `${path}${suffix}`

  // Resolve authored relative paths against the manifest that declared them.
  // Do this before applying Vite's base URL conversion: converting `/data/...`
  // to `./data/...` first would make it look relative to the manifest directory
  // and produce paths such as `data/projects/case/data/projects/case/...`.
  const isRelativePath = path.startsWith('./') || path.startsWith('../')
  if (isRelativePath && baseUrl) return `${resolveRelativePath(path, baseUrl)}${suffix}`

  // A bare path in a manifest is also treated as relative to that manifest.
  // Keep URL-like root paths (`/api`, `/assets`, etc.) untouched except for the
  // explicitly portable `/data` namespace handled by resourceUrl().
  if (baseUrl && !path.startsWith('/')) return `${resolveRelativePath(`./${path}`, baseUrl)}${suffix}`

  return `${resourceUrl(path)}${suffix}`
}

/**
 * Normalize URL-like values embedded in a static JSON manifest.  This keeps
 * catalog, spatial, dataset and fixture files compatible with both legacy
 * `/data/...` entries and future relative entries without rewriting source
 * files on disk.
 */
const LITERAL_FIELDS = new Set([
  'id', 'name', 'shortName', 'label', 'type', 'status', 'statusLabel',
  'note', 'description', 'source', 'generatedFrom', 'fileName', 'filename',
  'filePattern', 'pattern', 'rasterOutputs'
])
const RESOURCE_FIELDS = new Set([
  'url', 'file', 'path', 'manifest', 'manifestUrl', 'spatialManifest',
  'datasetCatalog', 'fixtureCatalog', 'parameterLibrary', 'dataFieldMapping',
  'catalog', 'metadata', 'basin', 'rivers', 'stations', 'sections',
  'stationCatalog', 'sectionCatalog', 'grid', 'gridUrl', 'rasterUrl', 'preview',
  'latestPreview', 'eventPackages', 'floodEvents', 'spatialStations',
  'rainGridPreview', 'root', 'nc'
])

function isResourceReference(value, field) {
  // A file name, source note, status, id or date is not a browser URL. Only
  // unambiguous resource references are resolved, even inside GeoJSON.
  if (LITERAL_FIELDS.has(field)) return false
  if (/^(?:\.\.?\/|\/data\/|data\/)/.test(value)) return true
  if (!RESOURCE_FIELDS.has(field)) return false
  return /\.(?:json|geojson|png|jpe?g|svg|webp|tiff?|nc|csv|xlsx?|mdb)(?:[?#].*)?$/i.test(value)
}

export function normalizeResourceTree(value, baseUrl = null, field = null) {
  if (typeof value === 'string') return isResourceReference(value, field) ? resolveResourceUrl(value, baseUrl) : value
  if (Array.isArray(value)) return value.map((item) => normalizeResourceTree(item, baseUrl, field))
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, normalizeResourceTree(item, baseUrl, key)]))
}

