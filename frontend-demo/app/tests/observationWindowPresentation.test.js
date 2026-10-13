import { it } from 'node:test'
import assert from 'node:assert/strict'
import { initialObservationWindow, observationHour, observationTime, stationRainfallName } from '../src/services/observationWindowPresentation.js'

it('selects a week ending at the replay clock, using source-local hours across a leap day', () => {
  const station = { startHour: observationHour('2024-01-01T00:00'), endHour: observationHour('2024-12-31T23:00') }
  const window = initialObservationWindow(station, '2024-03-01 00:00')
  assert.equal(window.end, '2024-03-01T00:00')
  assert.equal(observationHour(window.end) - observationHour(window.start) + 1, 168)
  assert.equal(observationTime(observationHour('1959-05-02T20:00')), '1959-05-02T20:00')
})

it('uses station coverage when replay is outside it and never invents an older 2017 period', () => {
  const station = { startHour: observationHour('2025-08-12T00:00'), endHour: observationHour('2025-08-12T23:00') }
  assert.deepEqual(initialObservationWindow(station, '2024-07-03 09:00'), { start: '2025-08-12T00:00', end: '2025-08-12T23:00' })
  assert.equal(initialObservationWindow({}), null)
})

it('rejects impossible dates and names selected real source periods', () => {
  assert.equal(observationHour('2024-02-30T00:00'), null)
  assert.equal(observationHour('2024-07-03T09:30'), null)
  assert.equal(stationRainfallName('2024-06-01T00:00', '2024-06-30T23:00'), '南安溪站点雨量 · 2024-06-01至2024-06-30')
})
