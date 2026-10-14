import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { makeSchemeEditable } from '../src/services/modelSchemeService.js'
import { stratifiedRandomEventSplit, physicalFloodGroups } from '../src/services/calibrationSubmissionService.js'
import { interpolateRainfallAt, pointInGeoJSON, projectRainfallPoint, stationRainfallGrid } from '../src/services/rainfallInterpolationService.js'
import { latestComparableTrial, trialConditions } from '../src/services/manualCalibrationService.js'
import { resultEntries } from '../src/services/dailyOperationService.js'
import { gridToGeoJSON } from '../src/data/basin.js'

test('state map features retain the calculated surface, interflow and groundwater values', () => {
  const values = { RS: 0, RI: 0.146, RG: 0.03 }
  const cell = { id: 'NX-001', bounds: [[118,29],[118.01,29],[118.01,29.01],[118,29.01],[118,29]], ...values }
  const feature = gridToGeoJSON([cell]).features[0]
  for (const key of ['RS', 'RI', 'RG']) assert.equal(feature.properties[key], values[key])
  assert.equal(feature.properties.soil, undefined)
})

test('a retained import opens each recovered flood state without inventing flow results', () => {
  const result = { taskId: 'JOB-import', eventStates: [
    { eventId: 'A', taskId: 'JOB-state', start: '2024-06-01T00:00', end: '2024-06-02T00:00', endStateVersionId: 'STATE-A' },
    { eventId: 'B', taskId: 'JOB-state', start: '2024-06-03T00:00', end: '2024-06-04T00:00', endStateVersionId: 'STATE-B' }
  ] }
  const rows = resultEntries(result, result.taskId)
  assert.deepEqual(rows.map(row => [row.id, row.taskId, row.start]), [
    ['A', 'JOB-state', '2024-06-01T00:00'], ['B', 'JOB-state', '2024-06-03T00:00']
  ])
  assert.equal(rows[0].endStateVersionId, 'STATE-A')
  assert.equal(rows[0].hydrograph.forecast, undefined)
})

test('editable colleague scheme retains search controls without frozen population locks', () => {
  const cal = { sourcePlan: { planSHA256: 'abc' }, sourcePlanRequestTemplate: {},
    optimizerSettings: { initializationPolicy: 'SOURCE_INITIAL_CANDIDATES', initialCandidates: [], effectiveInitialCandidates: [], differentialWeight: 0.6, crossoverProbability: 0.7 },
    schemeRoutingVersionId: 'source-reference', params: { SM: 10 } }
  makeSchemeEditable(cal, { originalCalibrationPlan: { originalRoutingVersionId: 'source-reference' }, routingCandidate: { routingVersionId: 'fitted-routing' }, bestParameters: { SM: 18 } })
  assert.equal(cal.sourcePlan, null)
  assert.deepEqual(cal.optimizerSettings, { differentialWeight: 0.6, crossoverProbability: 0.7 })
  assert.equal(cal.schemeRoutingVersionId, 'fitted-routing')
  assert.deepEqual(cal.params, { SM: 18 })
})

test('three physical floods receive three uses reproducibly without splitting overlaps', () => {
  const rows = [
    { id: 'A', start: '2024-01-01T00:00', end: '2024-01-02T00:00', observedPeak: 200, purpose: '率定' },
    { id: 'A2', start: '2024-01-02T00:00', end: '2024-01-03T00:00', observedPeak: 200, purpose: '率定' },
    { id: 'B', start: '2024-02-01T00:00', end: '2024-02-02T00:00', observedPeak: 20, purpose: '率定' },
    { id: 'C', start: '2024-03-01T00:00', end: '2024-03-02T00:00', observedPeak: null, purpose: '率定' }
  ]
  const before = structuredClone(rows), split = stratifiedRandomEventSplit(rows, 42)
  assert.deepEqual(split, stratifiedRandomEventSplit(rows, 42))
  assert.equal(new Set(split.map(row => row.purpose)).size, 3)
  assert.equal(split.find(row => row.id === 'A').purpose, split.find(row => row.id === 'A2').purpose)
  assert.deepEqual(rows, before)
  assert.equal(physicalFloodGroups(rows).groupCount, 3)
  assert.equal(physicalFloodGroups(rows).groups.find(group => group.memberEventIds.includes('C')).observedPeak, null)
})

test('rainfall uses metric projection, accepts zero and leaves absent stations empty', () => {
  const projected = projectRainfallPoint(118.5, 29)
  // Independently computed with pyproj EPSG:4326 -> EPSG:32650.
  assert.ok(Math.abs(projected.x - 646108.8085815797) < 0.001)
  assert.ok(Math.abs(projected.y - 3208912.979032214) < 0.001)
  const stations = [{ lng: 118.5, lat: 29, rain1h: 0 }, { lng: 118.6, lat: 29.1, rain1h: 20 }, { lng: 118.5, lat: 29, rain1h: -100 }, { lng: 118.5, lat: 29, rain1h: null }]
  assert.equal(interpolateRainfallAt(118.5, 29, stations), 0)
  assert.equal(interpolateRainfallAt(118.5, 29, [{ lng: 118.5, lat: 29, rain1h: -1 }]), null)
  const grid = [{ id: 'G1', bounds: [[118.49,28.99],[118.51,28.99],[118.51,29.01],[118.49,29.01]], qpf: 9 }]
  assert.equal(stationRainfallGrid(grid, stations)[0].qpf, 0)
  assert.equal(stationRainfallGrid(grid, [])[0].qpf, null)
  assert.equal(grid[0].qpf, 9)
  const square = (x,y,n) => [[x,y],[x+n,y],[x+n,y+n],[x,y+n],[x,y]]
  const basin = { type:'MultiPolygon',coordinates:[[square(0,0,4),square(1,1,1)],[square(10,10,1)]] }
  assert.equal(pointInGeoJSON([10.5,10.5],basin),true)
  assert.equal(pointInGeoJSON([1.5,1.5],basin),false)
})

test('manual routing edits can compare the prior flow but changed forcing cannot', () => {
  const request = { period:{mode:'event',events:[{id:'A',start:'2024-01-01',end:'2024-01-02',purpose:'率定'}]}, inputs:{bundleVersionId:'rain'}, routing:{versionId:'old'}, calibration:{} }
  const current = {taskId:'second',frozenRequest:{...request,routing:{versionId:'new'}},hydrographs:{A:{}}}
  const records = [{taskId:'first',conditions:trialConditions(request)},{taskId:'second',conditions:trialConditions(current.frozenRequest)}]
  assert.equal(latestComparableTrial(records,current,'A').taskId,'first')
  current.frozenRequest.inputs={bundleVersionId:'other-rain'}
  assert.equal(latestComparableTrial(records,current,'A'),null)
})

test('all changed workbench views compile with their actual script bindings', () => {
  for (const file of ['views/CalibrationView.vue','views/ConfigView.vue','views/StateView.vue','views/RealtimeView.vue','components/model/ModelSchemeBar.vue','components/params/SearchSpaceEditor.vue']) {
    const source=readFileSync(new URL('../src/'+file,import.meta.url),'utf8'),{descriptor,errors}=parse(source)
    assert.deepEqual(errors,[],file)
    const script=compileScript(descriptor,{id:file})
    const template=compileTemplate({source:descriptor.template.content,filename:file,id:file,compilerOptions:{bindingMetadata:script.bindings}})
    assert.deepEqual(template.errors,[],file)
  }
})
