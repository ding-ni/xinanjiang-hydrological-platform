import { it } from 'node:test'
import assert from 'node:assert/strict'
import { effectiveSlopeK, filterRoutingRows, routingIsStable, routingMapCoordinates, setUnitOverride } from '../src/services/routingPresentation.js'

it('unit K uses partial overrides and follows changing defaults after reset', () => {
  const parameters = { default_slope_k_hours: { RS: 1, RI: 2, RG: 3 }, unit_component_slope_k_hours: {} }
  setUnitOverride(parameters, 'A', 'RS', true)
  parameters.unit_component_slope_k_hours.A.RS = 10
  assert.equal(effectiveSlopeK(parameters, 'A', 'RS'), 10)
  assert.equal(effectiveSlopeK(parameters, 'A', 'RI'), 2)
  assert.equal(parameters.default_slope_k_hours.RS, 1)
  setUnitOverride(parameters, 'A', 'RS', false)
  assert.deepEqual(parameters.unit_component_slope_k_hours, {})
  parameters.default_slope_k_hours.RS = 7
  assert.equal(effectiveSlopeK(parameters, 'A', 'RS'), 7)
})

it('turning one component off retains other unit overrides', () => {
  const parameters = { default_slope_k_hours: { RS: 1, RI: 2 }, unit_component_slope_k_hours: {} }
  setUnitOverride(parameters, 'A', 'RS', true)
  setUnitOverride(parameters, 'A', 'RI', true)
  setUnitOverride(parameters, 'A', 'RS', false)
  assert.deepEqual(parameters.unit_component_slope_k_hours, { A: { RI: 2 } })
})

it('routing search is literal, case insensitive and accepts downstream matches', () => {
  const rows = [{ reach_id: 'R0001', downstream_reach_id: 'R0005' }, { reach_id: 'R0002' }]
  assert.deepEqual(filterRoutingRows(rows, ' r0005 ', ['reach_id', 'downstream_reach_id']), [rows[0]])
  assert.deepEqual(filterRoutingRows(rows, '[', ['reach_id']), [])
  assert.deepEqual(filterRoutingRows(rows, '', ['reach_id']), rows)
})

it('hourly Muskingum stable bounds are reported without clipping parameters', () => {
  assert.equal(routingIsStable({ k_hours: 1, x: 0.2 }), true)
  assert.equal(routingIsStable({ k_hours: 5, x: 0.3 }), false)
  for (const value of [null, NaN, Infinity, '1', true]) assert.equal(routingIsStable({ k_hours: value, x: 0.2 }), false)
})

it('map extent includes basin and grid boundaries beyond river endpoints', () => {
  const product = {
    basin: { features: [{ geometry: { coordinates: [[[119, 30], [119.1, 30.2], [119, 30]]] } }] },
    geometry: { features: [{ geometry: { coordinates: [[[[118.9, 29.9], [119, 30]]]] } }, { geometry: null }] },
    topology: { reaches: [{ startLonLat: [119, 30], endLonLat: [119.05, 30.1] }] },
  }
  const points = routingMapCoordinates(product)
  assert.ok(points.some(([lon, lat]) => lon === 118.9 && lat === 29.9))
  assert.ok(points.some(([lon, lat]) => lon === 119.1 && lat === 30.2))
  assert.deepEqual(routingMapCoordinates(null), [])
})
