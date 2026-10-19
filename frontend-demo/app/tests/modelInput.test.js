import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { finiteInputValue, assertModelInputSelection, normalizeModelInputCatalog, normalizeModelInputWindow, sliceModelInputWindow, modelInputWarmupHours } from '../src/services/modelInputService.js'
import { observedModelProcess, modelEventStatistics, modelInputDisplayStatus } from '../src/services/modelInputPresentation.js'

function windowPayload() {
  const times = ['2017-06-19T11:00:00', '2017-06-19T12:00:00']
  return {
    sourceType: 'model-qc', forcingVersion: 'QC-RAIN', observationVersion: 'QC-FLOW',
    product: { resolution: '1km', times, cells: [{ id: 'NX-001', bounds: [[0, 0], [1, 0], [1, 1], [0, 0]], areaKm2: 1, values: [null, 0] }] },
    observationPreview: { times, rain: { 'old-mdb-station': [100, 100] }, river: { flows: [null, 0], levels: [31, 32] } },
    summary: { arealRainfallMm: [null, 0], reportingStationCount: [null, 4] },
    provenance: { files: [{ path: 'registered-input.nc', sha256: 'frozen-digest' }] }
  }
}

describe('fixed model input boundary', () => {
  it('keeps historical station rain and water levels distinct from derived grid rain', () => {
    const payload = windowPayload()
    payload.sourceType = 'database-observed'
    payload.quality = { status: 'raw-observations', gridStatus: 'ready' }
    const input = normalizeModelInputWindow(payload)
    assert.equal(input.product.sourceType, 'database-derived-grid')
    assert.equal(input.observationPreview.source, 'historical-database')
    assert.deepEqual(input.observationPreview.rain['old-mdb-station'], [100, 100])
    assert.deepEqual(input.observationPreview.river.levels, [31, 32])
    const sliced = sliceModelInputWindow(input, input.product.times[1], input.product.times[1])
    assert.deepEqual(sliced.observationPreview.rain['old-mdb-station'], [100])
    assert.deepEqual(sliced.observationPreview.river.levels, [32])
    assert.equal(sliced.quality.status, 'raw-observations')
  })
  it('displays historical raw observations even when spatial preparation fails', () => {
    const payload = windowPayload()
    payload.sourceType = 'database-observed'
    payload.quality = { gridStatus: 'failed', gridError: '空间配置未完成' }
    payload.product.cells = []
    const input = normalizeModelInputWindow(payload)
    assert.deepEqual(input.observationPreview.river.flows, [null, 0])
    assert.deepEqual(input.product.cells, [])
    assert.equal(input.quality.gridError, '空间配置未完成')
  })
  it('distinguishes registered QC, loaded data, loading, offline and an unsupported scale', () => {
    assert.equal(modelInputDisplayStatus({ registered: true }).label, '整编资料已登记')
    assert.equal(modelInputDisplayStatus({ registered: true, loaded: true }).label, '整编资料可用')
    assert.equal(modelInputDisplayStatus({ registered: true, loaded: true, loading: true }).label, '正在查询时段')
    assert.equal(modelInputDisplayStatus({ registered: true, loaded: true, backendError: 'offline' }).label, '整编资料服务离线')
    assert.equal(modelInputDisplayStatus({ registered: true, loaded: true, inputError: 'missing' }).label, '时段查询失败')
    assert.equal(modelInputDisplayStatus({ resolution: '0p1deg', registered: true, loaded: true }).status, 'spatial-only')
    assert.equal(modelInputDisplayStatus().label, '请选择计算资料')
  })
  it('keeps missing observations distinct from genuine zero', () => {
    assert.equal(finiteInputValue(null), null)
    assert.equal(finiteInputValue(''), null)
    assert.equal(finiteInputValue('  '), null)
    assert.equal(finiteInputValue(false), null)
    assert.equal(finiteInputValue([]), null)
    assert.equal(finiteInputValue(0), 0)
    const input = normalizeModelInputWindow(windowPayload())
    assert.deepEqual(input.product.cells[0].values, [null, 0])
    assert.deepEqual(input.observationPreview.river.flows, [null, 0])
    assert.deepEqual(input.summary.arealRainfallMm, [null, 0])
  })
  it('does not substitute MDB station rainfall or unregistered level values', () => {
    const input = normalizeModelInputWindow(windowPayload())
    assert.deepEqual(input.observationPreview.rain, {})
    assert.deepEqual(input.observationPreview.river.levels, [null, null])
    assert.equal(input.provenance.files[0].sha256, 'frozen-digest')
  })
  it('rejects unregistered scales and other basins', () => {
    assert.throws(() => assertModelInputSelection({ basinId: 'nananxi-xufan', modelScale: '0p1deg' }), /对应南安溪 1 km/)
    assert.throws(() => assertModelInputSelection({ basinId: 'selinco', modelScale: '1km' }), /对应南安溪 1 km/)
  })
  it('rejects a shifted flow timeline instead of pairing unrelated hours', () => {
    const payload = windowPayload()
    payload.observationPreview.times = ['2017-06-19T12:00:00', '2017-06-19T13:00:00']
    assert.throws(() => normalizeModelInputWindow(payload), /时次不一致/)
  })
  it('never turns a static fixture product into registered QC input', () => {
    const payload = windowPayload()
    payload.sourceType = 'static-preview'
    assert.throws(() => normalizeModelInputWindow(payload), /不属于已登记/)
  })
  it('catalog coverage contains no invented per-station QC records', () => {
    const input = normalizeModelInputCatalog({
      rain: { start: '1964-01-01', end: '2024-12-31', stations: [{ legacyCode: 'old' }] },
      river: { start: '1964-04-04', end: '2024-07-06' }, events: []
    })
    assert.deepEqual(input.rain.stations, [])
    assert.equal(input.river.station.spatialCode, '70115800')
  })
  it('separates analysis-period playback from an event warmup window', () => {
    const input = normalizeModelInputWindow(windowPayload())
    const slice = sliceModelInputWindow(input, input.product.times[1], input.product.times[1])
    assert.equal(slice.product.times.length, 1)
    assert.deepEqual(slice.product.cells[0].values, [0])
    assert.deepEqual(slice.observationPreview.river.flows, [0])
    assert.deepEqual(slice.summary.arealRainfallMm, [0])
  })
  it('validates the diagnostic warmup limit without truncating user input', () => {
    assert.equal(modelInputWarmupHours(), 336)
    assert.equal(modelInputWarmupHours(168), 168)
    assert.equal(modelInputWarmupHours(0), 0)
    assert.equal(modelInputWarmupHours(744), 744)
    assert.equal(modelInputWarmupHours(8760), 8760)
    for (const value of [8761, -1, 1.5, '168', false]) assert.throws(() => modelInputWarmupHours(value), /最多 365 天/)
  })
  it('observed-only presentation has explicit times and no invented simulation, metrics or temperature', () => {
    const input = normalizeModelInputWindow(windowPayload())
    const process = observedModelProcess(input, input.product.times[0], input.product.times[1])
    assert.deepEqual(process.hydrograph.times, input.product.times)
    assert.deepEqual(process.hydrograph.obs.map((row) => row.value), [null, 0])
    assert.deepEqual(process.hydrograph.forecast, [])
    assert.equal(process.hydrograph.metrics, null)
    assert.equal(process.hydrograph.peak, null)
    assert.deepEqual(process.forcing.precipitation, [null, 0])
    assert.deepEqual(process.forcing.temperature, [])
    assert.deepEqual(process.forcing.components, {})
  })
  it('event statistics do not assert whole-event totals for missing flow or rainfall', () => {
    const input = normalizeModelInputWindow(windowPayload())
    const partial = modelEventStatistics(input, input.product.times[0], input.product.times[1])
    assert.equal(partial.volumeM3, null)
    assert.equal(partial.arealRainfallTotalMm, null)
    assert.equal(partial.flowCompleteness, 0.5)
    assert.equal(partial.rainCompleteness, 0.5)
    const complete = modelEventStatistics(input, input.product.times[1], input.product.times[1])
    assert.equal(complete.volumeM3, 0)
    assert.equal(complete.arealRainfallTotalMm, 0)
  })
})
