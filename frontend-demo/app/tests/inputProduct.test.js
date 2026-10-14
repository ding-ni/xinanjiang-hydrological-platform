import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_INPUT_BUNDLE_VERSION_ID, buildInputProcessingRequest, frozenInputBundleVersionId,
  inputResultUsesSelection, parseSourceJson, selectedInputBundleVersionId, sourceJsonTemplate
} from '../src/services/inputProductService.js'
import { modelInputSelection, normalizeModelInputCatalog } from '../src/services/modelInputService.js'

describe('immutable input product frontend boundary', () => {
  it('keeps the fixed baseline as the explicit legacy default, not a current/latest alias', () => {
    assert.equal(selectedInputBundleVersionId({}), DEFAULT_INPUT_BUNDLE_VERSION_ID)
    assert.equal(frozenInputBundleVersionId({}), DEFAULT_INPUT_BUNDLE_VERSION_ID)
    assert.notEqual(DEFAULT_INPUT_BUNDLE_VERSION_ID, 'current')
  })
  it('workspace selection never adopts handoff/result lineage as the live choice', () => {
    const app = { workContext: { selectedInputBundleVersionId: 'INPUT-NEW', inputBundleVersionId: 'INPUT-OLD' } }
    assert.equal(selectedInputBundleVersionId(app), 'INPUT-NEW')
    assert.equal(selectedInputBundleVersionId({ workContext: { inputBundleVersionId: 'INPUT-OLD' } }), DEFAULT_INPUT_BUNDLE_VERSION_ID)
  })
  it('old task/result lineage remains distinguishable after selection changes', () => {
    const job = { inputs: { bundleVersionId: 'INPUT-OLD' }, lineage: { inputBundleVersionId: 'INPUT-OLD', inputBundleManifestSHA256: 'old-sha' } }
    const before = JSON.stringify(job)
    assert.equal(inputResultUsesSelection(job, { selectedInputBundleVersionId: 'INPUT-NEW' }), false)
    assert.equal(frozenInputBundleVersionId(job), 'INPUT-OLD')
    assert.equal(JSON.stringify(job), before)
    assert.equal(inputResultUsesSelection(job, { selectedInputBundleVersionId: 'INPUT-OLD' }), true)
  })
  it('automatically prepared result retains its source selection and immutable computed input', () => {
    const record = { inputBundleVersionId: 'INPUT-PREPARED', frozenRequest: {
      inputs: { bundleVersionId: 'INPUT-PREPARED', sourceBundleVersionId: 'INPUT-SOURCE' }
    } }
    assert.equal(inputResultUsesSelection(record, { selectedInputBundleVersionId: 'INPUT-SOURCE' }), true)
    assert.equal(inputResultUsesSelection(record, { selectedInputBundleVersionId: 'INPUT-OTHER' }), false)
    assert.equal(frozenInputBundleVersionId(record), 'INPUT-PREPARED')
  })
  it('templates contain only null rain, placeholder stations and no claimed observations', () => {
    const template = sourceJsonTemplate()
    assert.ok(template.name.includes('非实测'))
    assert.ok(template.stations.every((row) => row.values.every((value) => value === null)))
    assert.ok(template.stations.every((row) => row.lon === null && row.lat === null))
    assert.throws(() => parseSourceJson(JSON.stringify(template)), /模板不能直接登记/)
  })
  it('source JSON preserves genuine zero, negative raw values and missing data', () => {
    const source = sourceJsonTemplate()
    source.name = 'test-only source'; source.provenanceNote = 'unit-test synthetic fixture, not field observations'
    source.times.push('2017-06-18T02:00')
    source.stations = [{ id: 'TEST-ONLY', lon: 119.1, lat: 29.8, values: [0, null, -1] }]
    assert.deepEqual(parseSourceJson(JSON.stringify(source)).stations[0].values, [0, null, -1])
    source.resolutionId = '0p1deg'
    assert.throws(() => parseSourceJson(JSON.stringify(source)), /仅开放南安溪 1 km/)
  })
  it('JSON registration rejects malformed files, oversized bodies and placeholder coordinates', () => {
    assert.throws(() => parseSourceJson('{'), /格式无效/)
    assert.throws(() => parseSourceJson('x'.repeat(2 * 1024 * 1024 + 1)), /2 MB/)
    const source = sourceJsonTemplate(); source.name = 'test-only source'
    assert.throws(() => parseSourceJson(JSON.stringify(source)), /真实站号和数值坐标/)
  })
  it('QC windows snapshot the selected parent and ignore arbitrary filesystem fields', () => {
    const form = { name: 'test-only window', start: '2017-06-18T00:00', end: '2017-06-18T01:00', path: 'F:/forbidden.nc' }
    const request = buildInputProcessingRequest(form, { selectedInputBundleVersionId: 'INPUT-OLD' })
    form.start = '2018-01-01T00:00'
    assert.equal(request.operation, 'qcWindow')
    assert.equal(request.parentBundleVersionId, 'INPUT-OLD')
    assert.equal(request.start, '2017-06-18T00:00')
    assert.equal(request.projectId, 'nananxi-xufan')
    assert.equal(request.resolutionId, '1km')
    assert.equal(request.path, undefined)
    assert.match(request.requestId, /^DATA-/)
  })
  it('IDW requests route to backend processing and preserve zero policy explicitly', () => {
    const form = { operation: 'stationIdw', name: 'test-only IDW', sourceId: 'SRC-TEST', start: '2017-06-18T00:00', end: '2017-06-18T01:00', idwPower: '2', minimumStations: '1' }
    const request = buildInputProcessingRequest(form)
    assert.equal(request.sourceId, 'SRC-TEST')
    assert.equal(request.idwPower, 2)
    assert.equal(request.minimumStations, 1)
    assert.equal(request.zeroOnlyPolicy, 'KEEP')
    assert.throws(() => buildInputProcessingRequest({ ...form, sourceId: '' }), /先登记并选择/)
    assert.throws(() => buildInputProcessingRequest({ ...form, idwPower: 5 }), /1–4/)
    assert.throws(() => buildInputProcessingRequest({ ...form, minimumStations: 1.5 }), /正整数/)
  })
  it('model input queries use the selected bundle in active context', () => {
    const selection = modelInputSelection({ activeContext: { basinId: 'nananxi-xufan', modelScale: '1km', selectedInputBundleVersionId: 'INPUT-NEW' } })
    assert.equal(selection.bundleVersionId, 'INPUT-NEW')
  })
  it('selected catalog identity, hashes and truthful quality notes survive normalization', () => {
    const catalog = normalizeModelInputCatalog({ bundleVersionId: 'INPUT-USER', bundleManifestSHA256: 'sha-user', qualityNotes: ['用户声明来源，未独立验收'], sourceLabel: '用户声明站点资料，未独立验收', coverage: { precipitation: { start: '2017-06-18T00:00', end: '2017-06-18T01:00' }, observedFlow: { start: '2017-06-18T00:00', end: '2017-06-18T01:00' } }, events: [{ id: 'EVENT-1', observedPeak: null }] })
    assert.equal(catalog.bundleVersionId, 'INPUT-USER')
    assert.equal(catalog.bundleManifestSHA256, 'sha-user')
    assert.deepEqual(catalog.qualityNotes, ['用户声明来源，未独立验收'])
    assert.equal(catalog.sourceLabel, '用户声明站点资料，未独立验收')
    assert.equal(catalog.events[0].observedPeak, null)
  })
})
