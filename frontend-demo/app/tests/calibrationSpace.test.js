import { it } from 'node:test'
import assert from 'node:assert/strict'
import { searchCoordinates, selectedSearchParameters, searchSelectionError } from '../src/services/calibrationSpaceService.js'
import { buildCalibrationRequest } from '../src/services/calibrationSubmissionService.js'
import { normalizeBackendTask } from '../src/services/modelBackendService.js'

const definitions = [
  { key: 'SM', symbol: 'SM', minimum: 8, maximum: 60, limitMin: 5, limitMax: 100, group: 'sources' },
  { key: 'WM', symbol: 'WM', minimum: 120, maximum: 240, limitMin: 50, limitMax: 300, group: 'storage' }
]
const params = { SM: 20, WM: 150, KI: .02, KG: .01 }

it('search bounds are independent copies and daily coordinates match the declared conversion', () => {
  const selection = selectedSearchParameters(definitions, ['SM'])
  assert.deepEqual(Object.keys(selection), ['SM'])
  selection.SM.minimum = 10
  assert.equal(definitions[0].minimum, 8)
  const coordinates = searchCoordinates(params)
  assert.equal(coordinates.K_total_day, 1 - .97 ** 24)
  assert.ok(Math.abs(coordinates.groundwater_fraction - 1 / 3) < 1e-14)
  assert.equal(searchCoordinates({ KI: 0, KG: 0 }).groundwater_fraction, 0)
})

it('custom selection validates bounds, current value and stage groups; defaults remain compatible', () => {
  assert.equal(searchSelectionError(null, definitions, params, 'staged'), '')
  assert.equal(searchSelectionError(selectedSearchParameters(definitions), definitions, params, 'staged'), '')
  const one = selectedSearchParameters(definitions, ['SM'])
  assert.equal(searchSelectionError(one, definitions, params, 'joint'), '')
  assert.match(searchSelectionError(one, definitions, params, 'staged'), /单组/)
  for (const invalid of [{}, [], true, { UNKNOWN: {} }, { SM: null }, { SM: {} },
    { SM: { minimum: true, maximum: 60 } }, { SM: { minimum: NaN, maximum: 60 } },
    { SM: { minimum: 60, maximum: 8 } }, { SM: { minimum: 4, maximum: 60 } },
    { SM: { minimum: 8, maximum: 101 } }, { SM: { minimum: 30, maximum: 60 } }]) {
    assert.ok(searchSelectionError(invalid, definitions, params, 'joint'))
  }
})

it('submission freezes search selection and task recovery returns the same bounds', () => {
  const selection = selectedSearchParameters(definitions, ['SM'])
  const request = buildCalibrationRequest({ app: { basinId: 'nananxi-xufan', modelScale: '1km', projectData: { backend: {} } },
    calibration: { calibrationMode: 'continuous', params, criteria: {}, searchParameters: selection }, operationMode: 'calibration', events: [] })
  selection.SM.minimum = 10
  assert.equal(request.calibration.searchParameters.SM.minimum, 8)
  const recovered = normalizeBackendTask({ taskId: 'JOB-selection', runType: 'continuousCalibration', runConfig: request })
  assert.deepEqual(recovered.runConfig.searchParameters, request.calibration.searchParameters)
})
