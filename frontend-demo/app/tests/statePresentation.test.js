import { test } from 'node:test'
import assert from 'node:assert/strict'
import { checkpointCsv, filterStates, stateName } from '../src/services/statePresentation.js'

const rows = [
  { id: 'STATE-JOB-a', time: '2017-06-20 11:00', source: '降雨预热', checkpoint: { initialization: { strategy: 'FIXED_WARMUP' } } },
  { id: 'STATE-JOB-b', time: '2017-06-21 11:00', source: '按实测流量初始化', checkpoint: { initialization: { strategy: 'OBSERVED_FLOW_ANCHORED' } } },
]
const metadata = { 'STATE-JOB-a': { name: '六月洪水前状态', favorite: true } }

test('state search combines names, dates, strategy and favorite flags', () => {
  assert.deepEqual(filterStates(rows, metadata, { query: '六月 2017' }), [rows[0]])
  assert.deepEqual(filterStates(rows, metadata, { favorites: true }), [rows[0]])
  assert.deepEqual(filterStates(rows, metadata, { strategy: 'OBSERVED_FLOW_ANCHORED' }), [rows[1]])
  assert.deepEqual(filterStates(rows, metadata, { query: '[' }), [])
  const legacy = { id: 'STATE-JOB-legacy', time: '2017-06-19 11:00', source: '模型末状态', checkpoint: {} }
  assert.deepEqual(filterStates([...rows, legacy], metadata, { strategy: 'MODEL_END_STATE' }), [legacy])
})
test('state ordering pins favourites without mutating the original registry', () => {
  const before = JSON.stringify(rows)
  assert.deepEqual(filterStates(rows), [rows[1], rows[0]])
  assert.deepEqual(filterStates(rows, metadata), rows)
  assert.equal(JSON.stringify(rows), before)
  assert.equal(stateName(rows[0], metadata), '六月洪水前状态')
})
test('checkpoint CSV exports each exact grid state with escaped identifiers', () => {
  const checkpoint = { gridIds: ['NX-1', 'NX-"2'], time: '2017-06-21T11:00:00', land: { WU: [1.234567890123, 0], S: [0, 9] } }
  const csv = checkpointCsv(checkpoint)
  assert.ok(csv.startsWith('\uFEFF"grid_id","time","WU","S"'))
  assert.ok(csv.includes('"1.234567890123","0"'))
  assert.ok(csv.includes('"NX-""2"'))
  assert.equal(csv.split('\r\n').length, 3)
})
