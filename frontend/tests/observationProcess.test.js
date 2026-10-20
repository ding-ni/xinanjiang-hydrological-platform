import test from 'node:test'
import assert from 'node:assert/strict'
import { observationProcess, windowStation } from '../src/services/observationProcessService.js'

test('a hydrological station retains rain, flow and level independently across the complete selected flood', () => {
  const preview = { times: ['1988-08-08T07:00', '1988-08-08T08:00', '1988-08-08T09:00'],
    rain: { '70115800': [20.3, 0, 19] }, river: { spatialCode: '70115800', flows: [.1, 5, 98.8], levels: [1.97, 2, 3] } }
  const row = windowStation(preview, '70115800', 0)
  assert.equal(row.hasRainObservation, true)
  assert.equal(row.hasRiverObservation, true)
  assert.equal(row.rain1h, 20.3)
  assert.equal(row.rainSeries.length, 3)
  assert.equal(row.rainSeries.at(-1).cum, 39.3)
  assert.equal(row.flow, .1)
  const grid = observationProcess(preview, { arealRainfallMm: [3.498676168920034, 0, 3] })
  assert.equal(grid.rain[0], 3.498676168920034)
  assert.notEqual(grid.rain[0], row.rain1h)
  assert.equal(observationProcess(preview, {}, '70115800').total, 39.3)
})

test('missing rain never becomes zero or a complete total, while genuine zero remains usable', () => {
  const preview = { times: ['a', 'b', 'c'], rain: { rain: [0, null, 5] }, river: { flows: [null, 0, 1] } }
  assert.deepEqual(observationProcess(preview, {}, 'rain').rain, [0, null, 5])
  assert.equal(observationProcess(preview, {}, 'rain').total, null)
  assert.equal(windowStation(preview, 'rain', 1).rainSeries[2].cum, null)
  assert.equal(windowStation(preview, 'missing').hasRainObservation, false)
})
