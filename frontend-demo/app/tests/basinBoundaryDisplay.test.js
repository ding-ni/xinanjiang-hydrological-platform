import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createExpression, latest } from '@maplibre/maplibre-gl-style-spec'
import {
  actualSubbasinGeometry, applyBasinBoundaryVisibility, EMPTY_SUBBASINS, SUBBASIN_FILL_COLOR
} from '../src/services/basinBoundaryDisplayService.js'

const basin = JSON.parse(readFileSync(new URL('../public/data/projects/nananxi-xufan/spatial/basin.geojson', import.meta.url), 'utf8'))
const selincoSubbasins = JSON.parse(readFileSync(new URL('../src/data/generated/selinco/subbasins.json', import.meta.url), 'utf8'))

function displayedLayers() {
  const layers = Object.fromEntries(['basin-line', 'subbasin-fill', 'subbasin-line', 'subbasin-label']
    .map((id) => [id, { visibility: 'visible' }]))
  return {
    layers,
    getLayer: (id) => layers[id],
    setLayoutProperty(id, property, value) { layers[id][property] = value }
  }
}

test('a project without a separate subbasin source has no fabricated subdivisions', () => {
  assert.equal(actualSubbasinGeometry(undefined), EMPTY_SUBBASINS)
  assert.equal(actualSubbasinGeometry({ type: 'FeatureCollection', features: [] }), EMPTY_SUBBASINS)
  const genuine = { type: 'FeatureCollection', features: [{
    type: 'Feature', properties: { basinIndex: 1 }, geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [0, 1], [0, 0]]] }
  }] }
  assert.equal(actualSubbasinGeometry(genuine), genuine)
})

test('the boundary switch controls a basin outline without displaying empty subdivisions', () => {
  const map = displayedLayers()
  for (const enabled of [true, false, true]) {
    applyBasinBoundaryVisibility(map, { subbasins: EMPTY_SUBBASINS, enabled })
    assert.equal(map.layers['basin-line'].visibility, enabled ? 'visible' : 'none')
    for (const id of ['subbasin-fill', 'subbasin-line', 'subbasin-label']) assert.equal(map.layers[id].visibility, 'none')
  }
})

test('switching between real subbasins and an external basin clears stale fill and labels', () => {
  const map = displayedLayers()
  assert.equal(selincoSubbasins.features.length, 4)
  applyBasinBoundaryVisibility(map, { isSelinco: true, subbasins: selincoSubbasins, enabled: true })
  assert.ok(Object.values(map.layers).every((layer) => layer.visibility === 'visible'))
  applyBasinBoundaryVisibility(map, { isSelinco: true, subbasins: selincoSubbasins, enabled: false })
  assert.equal(map.layers['basin-line'].visibility, 'visible')
  assert.equal(map.layers['subbasin-fill'].visibility, 'none')
  applyBasinBoundaryVisibility(map, { subbasins: EMPTY_SUBBASINS, enabled: false })
  assert.ok(Object.values(map.layers).every((layer) => layer.visibility === 'none'))
  applyBasinBoundaryVisibility(map, { isSelinco: true, subbasins: selincoSubbasins, enabled: true })
  assert.equal(map.layers['subbasin-label'].visibility, 'visible')
})

test('real basin properties without wetness evaluate to a translucent color rather than opaque black', () => {
  const feature = basin.features[0]
  assert.equal(feature.properties.wetness, undefined)
  const expression = createExpression(SUBBASIN_FILL_COLOR, latest.paint_fill['fill-color'])
  assert.equal(expression.result, 'success')
  const color = expression.value.evaluate({ zoom: 11 }, feature)
  assert.ok(color.r + color.g + color.b > 0)
  assert.ok(color.a > 0 && color.a < 1)
  const classified = expression.value.evaluate({ zoom: 11 }, { ...feature, properties: { basinIndex: 1 } })
  assert.notDeepEqual(classified, color)
})
