import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resourceUrl, resolveResourceUrl, normalizeResourceTree } from '../src/services/resourceUrl.js'
import { projectResourceRoot, projectResourceUrl } from '../src/services/projectResourceService.js'
import { clearProjectDataCache } from '../src/services/projectDataService.js'
import { clearProjectCatalogCache, loadAndRegisterProjectCatalog } from '../src/services/projectCatalogService.js'
import { resolveDomainContext } from '../src/data/domainContext.js'
import { selectObservedPrecipitationDataset } from '../src/services/datasetSelectionService.js'
import { buildForecastRequest, submitForecastRequest } from '../src/services/forecastSubmissionService.js'
import { submitCalibrationRequest } from '../src/services/calibrationSubmissionService.js'
import { projectDataForContext, caseExecutionAvailability } from '../src/services/caseAvailabilityService.js'

describe('portable resource references', () => {
  const manifestUrl = './data/projects/test-basin/manifest.json'

  it('resolves references relative to the declaring manifest', () => {
    assert.equal(resolveResourceUrl('./1km/spatial.json', manifestUrl), './data/projects/test-basin/1km/spatial.json')
    assert.equal(resolveResourceUrl('../shared.json?rev=2#item', manifestUrl), './data/projects/shared.json?rev=2#item')
    assert.equal(resolveResourceUrl('spatial/basin.geojson', manifestUrl), './data/projects/test-basin/spatial/basin.geojson')
  })

  it('does not resolve an already normalized reference twice', () => {
    const url = resolveResourceUrl('./test-basin/manifest.json', './data/projects/catalog.json')
    assert.equal(url, manifestUrl)
    assert.equal(resolveResourceUrl(url, './data/projects/catalog.json'), url)
    assert.equal(projectResourceUrl({ manifestUrl }, './observations/catalog.json'), './data/projects/test-basin/observations/catalog.json')
    assert.equal(projectResourceUrl({ manifestUrl }, './data/projects/test-basin/observations/catalog.json'), './data/projects/test-basin/observations/catalog.json')
    assert.equal(projectResourceRoot({ manifestUrl }), './data/projects/test-basin/')
  })

  it('keeps external API URLs unchanged and supports remote manifests', () => {
    assert.equal(resourceUrl('https://example.invalid/api/tasks'), 'https://example.invalid/api/tasks')
    assert.equal(resolveResourceUrl('/api/tasks', manifestUrl), '/api/tasks')
    assert.equal(resolveResourceUrl('./spatial.json', 'https://example.invalid/platform/case/manifest.json'), 'https://example.invalid/platform/case/spatial.json')
  })

  it('keeps bundled Selinco forcing portable and different from catalog-relative references', () => {
    const metadata = JSON.parse(readFileSync(new URL('../src/data/generated/selinco/metadata.json', import.meta.url), 'utf8'))
    const forcingCatalog = JSON.parse(readFileSync(new URL('../public/data/selinco/forcing/metadata.json', import.meta.url), 'utf8'))
    for (const [scale, product] of Object.entries(metadata.forcing)) {
      assert.equal(product.file, `data/selinco/forcing/${product.name}`)
      assert.equal(resourceUrl(product.file), `./data/selinco/forcing/${product.name}`)
      assert.ok(existsSync(new URL(`../public/${product.file}`, import.meta.url)))
      assert.equal(forcingCatalog[scale].file, `./${product.name}`)
      assert.equal(resolveResourceUrl(forcingCatalog[scale].file, './data/selinco/forcing/metadata.json'), resourceUrl(product.file))
    }
  })

  it('normalizes resource fields without changing ids, dates, labels or GeoJSON', () => {
    const original = {
      id: 'test-basin', name: '测试流域', status: 'partial', inputStatus: 'missing',
      updatedAt: '2026-10-09', capabilities: { forecast: true },
      manifest: './manifest.json', spatialManifest: '1km/spatial.json',
      forcing: { file: './forcing/rain.nc', name: 'rain.nc', filePattern: '../external/rain-{year}.nc' },
      source: '外部资料未随包提供',
      feature: { type: 'Feature', properties: { id: 'station-1', name: 'rain.json' }, geometry: { type: 'Point', coordinates: [90, 30] } }
    }
    const result = normalizeResourceTree(original, manifestUrl)
    assert.equal(result.id, 'test-basin')
    assert.equal(result.name, '测试流域')
    assert.equal(result.status, 'partial')
    assert.equal(result.inputStatus, 'missing')
    assert.equal(result.updatedAt, '2026-10-09')
    assert.deepEqual(result.capabilities, original.capabilities)
    assert.deepEqual(result.feature, original.feature)
    assert.equal(result.spatialManifest, './data/projects/test-basin/1km/spatial.json')
    assert.equal(result.forcing.file, './data/projects/test-basin/forcing/rain.nc')
    assert.equal(result.forcing.name, 'rain.nc')
    assert.equal(result.forcing.filePattern, '../external/rain-{year}.nc')
    assert.equal(result.source, original.source)
    assert.equal(original.manifest, './manifest.json')
    assert.deepEqual(normalizeResourceTree(result, manifestUrl), result)
  })
})

describe('catalog-driven parallel cases', () => {
  it('loads a new case without a source-code registry entry or duplicate paths', async () => {
    const id = 'portable-test-basin'
    const manifest = {
      schemaVersion: '1.0', id, name: '迁移测试流域', modelFrameworkId: 'distributed-xinanjiang',
      capabilities: { forecast: false },
      resolutions: {
        '1km': { label: '1 km', status: 'draft', spatialManifest: './1km/spatial.json' },
        '0p1deg': { label: '0.1°', status: 'draft', spatialManifest: './0p1deg/spatial.json' }
      }
    }
    const responses = new Map([
      ['./data/projects/catalog.json', { projects: [{ id, manifest: `./${id}/manifest.json` }] }],
      [`./data/projects/${id}/manifest.json`, manifest]
    ])
    const requested = []
    const originalFetch = globalThis.fetch
    clearProjectDataCache()
    clearProjectCatalogCache()
    globalThis.fetch = async (url) => {
      requested.push(url)
      assert.ok(responses.has(url), `unexpected resource: ${url}`)
      return { ok: true, json: async () => structuredClone(responses.get(url)) }
    }
    try {
      const result = await loadAndRegisterProjectCatalog()
      assert.deepEqual(result.errors, [])
      assert.equal(result.domains[0].id, id)
      assert.equal(result.domains[0].name, '迁移测试流域')
      assert.deepEqual(requested, [...responses.keys()])
      const fine = resolveDomainContext(id, { modelScale: '1km' })
      const coarse = resolveDomainContext(id, { modelScale: '0p1deg' })
      assert.notEqual(fine.contextKey, coarse.contextKey)
      assert.equal(fine.basinId, id)
      assert.equal(fine.status, 'draft')
      assert.equal(fine.manifestUrl, `./data/projects/${id}/manifest.json`)
      assert.equal(fine.manifest.resolutions['1km'].spatialManifest, `./data/projects/${id}/1km/spatial.json`)
    } finally {
      globalThis.fetch = originalFetch
      clearProjectDataCache()
      clearProjectCatalogCache()
    }
  })

  it('does not substitute a precipitation grid from the other scale', () => {
    const projectData = { datasets: { datasets: [
      { id: 'CASE-RAIN-1KM', type: 'observed-precipitation-grid-hourly', resolutionId: '1km' }
    ] } }
    assert.equal(selectObservedPrecipitationDataset(projectData, '1km').id, 'CASE-RAIN-1KM')
    assert.equal(selectObservedPrecipitationDataset(projectData, '0p1deg'), null)
  })

  it('leaves missing dataset identifiers empty instead of inventing inputs', () => {
    const request = buildForecastRequest({
      app: { basinId: 'empty-basin', basinName: '空资料流域', modelScale: '1km' },
      forecast: { contextKey: 'empty-basin:1km', startTime: '2026-10-09 00:00', leadHours: 6 },
      rainProduct: null
    })
    assert.equal(request.inputs.observation.griddedPrecipitationDatasetId, null)
    assert.equal(request.inputs.observation.datasetId, null)
    assert.equal(request.inputs.observation.catalog, './data/projects/empty-basin/observations/catalog.json')
  })
})

describe('frontend-only execution boundary', () => {
  it('does not contact a backend unless it was explicitly enabled', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = () => { throw new Error('unexpected backend request') }
    try {
      const forecast = await submitForecastRequest({ requestId: 'DRAFT-F' })
      const calibration = await submitCalibrationRequest({ requestId: 'DRAFT-C' })
      assert.equal(forecast.status, 'prepared')
      assert.equal(calibration.status, 'prepared')
      assert.equal(forecast.backendEnabled, false)
      assert.equal(calibration.backendEnabled, false)
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('rejects stale data and does not equate capability with readiness', () => {
    const context = { basinId: 'new-basin', modelScale: '1km', status: 'partial' }
    const resolution = { status: 'partial', inputStatus: 'partial-real', spatialManifest: './1km/spatial.json' }
    const data = {
      manifest: { id: 'new-basin', capabilities: { forecast: true }, resolutions: { '1km': resolution } },
      resolution,
      datasets: { datasets: [{ id: 'NEW-RAIN' }] },
      fixtures: { resultSource: 'unavailable' }
    }
    assert.equal(projectDataForContext(context, data), data)
    assert.equal(projectDataForContext({ ...context, basinId: 'another-basin' }, data), null)
    assert.equal(projectDataForContext({ ...context, modelScale: '0p1deg' }, data), null)
    assert.equal(projectDataForContext(context, data, { loading: true }), null)
    assert.equal(caseExecutionAvailability(context, data, 'forecast').ready, false)
    assert.equal(caseExecutionAvailability(context, data, 'forecast').mode, null)
  })
})
