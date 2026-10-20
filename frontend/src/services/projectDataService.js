import { normalizeResourceTree, resolveResourceUrl, resourceUrl } from './resourceUrl.js'

const jsonCache = new Map()

async function fetchJson(url, label = '项目数据', baseUrl = null) {
  if (!url) return null
  const resolvedUrl = resolveResourceUrl(url, baseUrl)
  if (!jsonCache.has(resolvedUrl)) {
    jsonCache.set(
      resolvedUrl,
      fetch(resolvedUrl)
        .then(async (response) => {
          if (!response.ok) throw new Error(`${label}加载失败：${response.status} ${response.statusText}`)
          const payload = await response.json()
          return normalizeResourceTree(payload, resolvedUrl)
        })
        .catch((error) => {
          jsonCache.delete(resolvedUrl)
          throw error
        })
    )
  }
  return jsonCache.get(resolvedUrl)
}

export function loadJsonResource(url, label = '项目资源') {
  return fetchJson(url, label)
}

export function loadProjectCatalog() {
  return fetchJson(resourceUrl('/data/projects/catalog.json'), '项目目录')
}

export function loadProjectManifest(manifestUrl, catalogUrl = resourceUrl('/data/projects/catalog.json')) {
  return fetchJson(manifestUrl, '项目清单', catalogUrl)
}

export async function loadProjectContextData(context) {
  const manifest = await loadProjectManifest(context.manifestUrl)
  const resolution = manifest?.resolutions?.[context.spatialResolutionId]
  if (!resolution) throw new Error(`${manifest?.name || context.basinName} 未配置 ${context.spatialResolutionId}`)
  // Field mappings are optional case assets.  The manifest is the only
  // source of truth; cases without a mapping remain usable for spatial-only
  // frontend work and are not coupled to another case's files.
  const fieldMapping = await fetchJson(manifest?.dataFieldMapping, '资料字段对应关系').catch(() => null)

  const [spatial, datasets, fixtures] = await Promise.all([
    fetchJson(resolution.spatialManifest, '空间资料清单'),
    fetchJson(resolution.datasetCatalog, '数据目录'),
    fetchJson(resolution.fixtureCatalog, '样例目录')
  ])

  const [stations, sections, spatialMetadata, parameterLibrary] = await Promise.all([
    fetchJson(spatial?.stationCatalog, '站点目录'),
    fetchJson(spatial?.sectionCatalog, '断面目录'),
    fetchJson(spatial?.metadata, '空间元数据'),
    fetchJson(resolution?.parameterLibrary, '参数库目录')
  ])
  const [basin, rivers] = await Promise.all([
    fetchJson(spatial?.basin, '流域边界'),
    fetchJson(spatial?.rivers, '河网')
  ])

  return {
    manifestUrl: context.manifestUrl,
    manifest,
    resolution,
    spatial,
    spatialAssets: { basin, rivers },
    spatialMetadata,
    parameterLibrary,
    fieldMapping,
    stations: stations
      ? {
          ...stations,
          stations: (stations.stations || []).map((station) =>
            manifest?.id === 'nananxi-xufan' && (station.id === '701AAJ02' || station.code === '701AAJ02')
              ? { ...station, type: 'rain', typeLabel: '雨量站（兼墒情）' }
              : station
          )
        }
      : stations,
    sections,
    datasets,
    fixtures
  }
}

export function clearProjectDataCache() {
  jsonCache.clear()
}
