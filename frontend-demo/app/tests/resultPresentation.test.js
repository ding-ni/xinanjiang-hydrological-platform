import { test } from 'node:test'
import assert from 'node:assert/strict'
import { comparisonReason, csv, hydrographCsv, resultPeriods, summaryRows, SUMMARY_HEADER } from '../src/services/resultPresentation.js'

const h = { forecast: [{ time: '2017-06-20T00:00:00', value: 10 }, { time: '2017-06-20T01:00:00', value: 20 }], obs: [{ time: '2017-06-20T01:00:00', value: 19 }], forcing: { precipitation: [0, 3] }, metrics: { nse: 0, peakErr: -5, timeErrH: 0, volErr: 1 }, peak: 20 }
const request = { context: { contextKey: 'basin' }, inputs: { bundleVersionId: 'in', bundleManifestSHA256: 'hash', evaporation: { mode: 'monthly' } }, lineage: { routingManifestSHA256: 'routing' }, period: { warmupDays: 1 }, parameters: { SM: 20 } }
test('every calibration period is selectable and simulation has one real period', () => {
  const result = { periods: [{ id: 'CALIBRATION', purpose: '率定' }, { id: 'VALIDATION', purpose: '验证' }], hydrographs: { CALIBRATION: h, VALIDATION: h } }
  assert.deepEqual(resultPeriods(result).map(row => row.label), ['率定期', '验证期'])
  assert.equal(resultPeriods({ hydrograph: h })[0].hydrograph, h)
  assert.deepEqual(resultPeriods({ importProvenance: {} }), [])
})
test('comparison permits changed parameters but checks forcing, state, routing and exact timestamps', () => {
  assert.equal(comparisonReason(request, { ...request, parameters: { SM: 60 } }, h, h), '')
  for (const mutate of [r => r.inputs.bundleManifestSHA256 = 'other', r => r.lineage.routingManifestSHA256 = 'other', r => r.period.warmupDays = 2, r => r.period.initialStateVersionId = 'state', r => r.period.continuousOrigin = '2016-01-01', r => r.science = { versionId: 'other' }]) {
    const b = structuredClone(request); mutate(b); assert.notEqual(comparisonReason(request, b, h, h), '')
  }
  assert.notEqual(comparisonReason(request, request, h, { ...h, forecast: [...h.forecast].reverse() }), '')
  assert.notEqual(comparisonReason(request, request, h, { ...h, forcing: { precipitation: [1, 3] } }), '')
  assert.notEqual(comparisonReason({}, {}, h, h), '')
})
test('CSV retains numeric precision, zero, missing observations and quotes formula text', () => {
  const text = csv([['=CMD()', '+X', '-X', '@X', ' a,"b', -1.234567890123, 0, null, '\n=X']])
  assert.ok(text.includes('"\'=CMD()"')); assert.ok(text.includes('-1.234567890123,0,""'))
  assert.ok(text.includes('" a,""b"'))
  const process = hydrographCsv(h, [{ label: '改参', hydrograph: { forecast: [...h.forecast].reverse() } }])
  assert.ok(process.includes('"2017-06-20T00:00:00",0,"",10,10'))
  assert.ok(process.includes('"2017-06-20T01:00:00",3,19,20,20'))
})
test('summary emits one row per period, without inventing metrics for state imports', () => {
  const task = { taskId: 'JOB-a', runTypeLabel: '人工试调' }
  const rows = summaryRows(task, { hydrographs: { A: h, B: h } }, { name: '方案一', collection: '六月', notes: 'SM=20' })
  assert.equal(rows.length, 2); assert.equal(rows[0].length, SUMMARY_HEADER.length)
  assert.equal(rows[0][8], 0); assert.equal(rows[0][1], '方案一')
  const state = summaryRows(task, { stateTime: '2017-06-21T00:00:00' })[0]
  assert.equal(state[6], '2017-06-21T00:00:00'); assert.equal(state[8], undefined)
})
