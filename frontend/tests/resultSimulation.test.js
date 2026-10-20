import test from 'node:test'
import assert from 'node:assert/strict'
import { simulationDraft } from '../src/services/resultSimulationService.js'
import { comparisonAvailability, comparisonDifferences, matchingResultPeriod, calculationName } from '../src/services/resultPresentation.js'

test('best-parameter simulation freezes the optimum and leaves the original search intact', () => {
  const request = { runType: 'calibration', scheme: { id: 'scheme', revision: 1 }, period: { mode: 'event', events: [{ id: 'A' }] },
    inputs: { bundleVersionId: 'prepared', sourceBundleVersionId: 'source', preparationTaskId: 'prep' },
    calibration: { initialParameters: { B: .2 }, searchParameters: { B: [0, 1] }, target: 'joint' }, science: { versionId: 'science' } }
  const before = structuredClone(request)
  const draft = simulationDraft(new Proxy(request, {}), { bestParameters: { B: .7 }, routingCandidate: { routingVersionId: 'optimum-routing' } })
  assert.deepEqual(request, before)
  assert.deepEqual(draft.calibration.initialParameters, { B: .7 })
  assert.equal(draft.calibration.simulationOnly, true)
  assert.equal(draft.calibration.target, undefined)
  assert.equal(draft.routing.versionId, 'optimum-routing')
  assert.equal(draft.inputs.preparationTaskId, undefined)
  assert.deepEqual(draft.science, request.science)
})
test('continuous optimum keeps its original continuous origin and saved science', () => {
  const parent = { period: { mode: 'continuous' }, inputs: {}, calibration: {} }
  const child = { runType: 'simulation', period: { startTime: '2017-06-19', endTime: '2017-06-21', continuousOrigin: '2017-06-18' }, inputs: {}, science: { versionId: 'frozen' } }
  const draft = simulationDraft(parent, { bestParameters: { B: .8 } }, child)
  assert.equal(draft.runType, 'simulation')
  assert.equal(draft.period.continuousOrigin, '2017-06-18')
  assert.equal(draft.science.versionId, 'frozen')
  assert.deepEqual(draft.parameters, { B: .8 })
  assert.equal(draft.calibration, undefined)
})
test('scheme comparison allows changed routing with explicit differences and aligns continuous periods', () => {
  const left = { context: { projectId: 'basin' }, routing: { versionId: 'A' } }
  const right = { ...left, routing: { versionId: 'B' } }
  const hydro = { forecast: [{ time: '2017-06-19', value: 1 }] }
  assert.equal(comparisonAvailability(left, right, hydro, hydro), '')
  assert.equal(comparisonDifferences(left, right), '汇流条件')
  assert.equal(matchingResultPeriod({ hydrograph: hydro }, 'CALIBRATION', hydro), hydro)
  assert.notEqual(comparisonAvailability(left, { context: { projectId: 'other' } }, hydro, hydro), '')
  assert.notEqual(comparisonAvailability(left, right, hydro, { forecast: [{ time: '2018', value: 2 }] }), '')
  assert.equal(calculationName({ taskId: 'id', runTypeLabel: '模拟' }, { id: { name: '六月方案' } }), '六月方案')
})
