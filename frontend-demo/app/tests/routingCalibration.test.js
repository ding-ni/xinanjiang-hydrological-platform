import { it } from 'node:test'
import assert from 'node:assert/strict'
import { buildCalibrationRequest } from '../src/services/calibrationSubmissionService.js'
import { normalizeBackendTask } from '../src/services/modelBackendService.js'
import { searchSelectionError } from '../src/services/calibrationSpaceService.js'
import { comparisonReason, summaryRows } from '../src/services/resultPresentation.js'

it('routing submission freezes only routing bounds, current runoff and selected routing version', () => {
  const calibration = { calibrationMode: 'continuous', calibrationTarget: 'routing', params: { SM: 20 }, criteria: {},
    searchParameters: { SM: { minimum: 8, maximum: 60 } }, routingSearchParameters: { RS_k_hours: { minimum: .5, maximum: 4 } }, searchStrategy: 'staged' }
  const request = buildCalibrationRequest({ app: { basinId: 'nananxi-xufan', modelScale: '1km', selectedRoutingVersionId: 'ROUTING-old', projectData: { backend: {} } }, calibration, operationMode: 'calibration' })
  assert.equal(request.calibration.target, 'routing')
  assert.equal(request.calibration.searchParameters, undefined)
  assert.equal(request.calibration.optimizer.strategy, 'joint')
  assert.deepEqual(request.calibration.initialParameters, { SM: 20 })
  assert.equal(request.routing.versionId, 'ROUTING-old')
  calibration.routingSearchParameters.RS_k_hours.maximum = 7
  assert.equal(request.calibration.routingSearchParameters.RS_k_hours.maximum, 4)
  const task = normalizeBackendTask({ taskId: 'JOB-routing', runType: 'continuousCalibration', runConfig: request })
  assert.equal(task.runConfig.calibrationTarget, 'routing')
  assert.deepEqual(task.runConfig.routingSearchParameters, { RS_k_hours: { minimum: .5, maximum: 4 } })
})

it('routing coordinates validate directly in hours', () => {
  const definitions = [{ key: 'RS_k_hours', symbol: 'Kₛ', minimum: .25, maximum: 2.5, limitMin: .05, limitMax: 720, group: 'routing' }]
  assert.equal(searchSelectionError({ RS_k_hours: { minimum: .5, maximum: 4 } }, definitions, { RS_k_hours: 2 }, 'joint'), '')
  assert.match(searchSelectionError({ RS_k_hours: { minimum: 3, maximum: 4 } }, definitions, { RS_k_hours: 2 }, 'joint'), /当前参数/)
})

it('routing search results never claim unchanged routing in a runoff comparison; exports use candidate identity', () => {
  assert.match(comparisonReason({ calibration: { target: 'routing' } }, {}, {}, {}), /独立/)
  const result = { routingCandidate: { routingVersionId: 'ROUTING-best' }, lineage: { routingVersionId: 'control-split-v2' } }
  assert.equal(summaryRows({ taskId: 'JOB-routing' }, result)[0][16], 'ROUTING-best')
})
