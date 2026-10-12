import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('diagnostic candidate adoption refreshes the registry and uses the complete candidate loader', async () => {
  const source = readFileSync(new URL('../src/views/DiagnosticView.vue', import.meta.url), 'utf8')
  const action = source.slice(source.indexOf('async function adoptCandidate()'), source.indexOf('watch(() => route.query.task'))
  const app = { contextKey: 'basin:1km', basinId: 'nananxi-xufan', modelScale: '1km', modelConfigId: 'model',
    projectData: {}, updateWorkContext(value) { calls.push(['selected', value]) } }
  const calls = [], error = { value: '' }, message = { value: '' }, busy = { value: false }
  const row = { parameterVersionId: 'PARAM-complete', science: { versionId: 'SCIENCE-parent' }, routingCandidate: { routingVersionId: 'ROUTING-best' } }
  const calibration = { setContext() {}, async loadVersion(value) { calls.push(['complete-loader', value]);
    assert.deepEqual(app.projectData.backendParameterVersions, [row]); return true } }
  const adopt = new Function('app', 'calibration', 'busy', 'error', 'message', 'result', 'inspected', 'modelApi',
    `${action}; return adoptCandidate`)(app, calibration, busy, error, message, { value: row },
    { value: { runConfig: { context: { contextKey: 'basin:1km' } } } }, async () => ({ versions: [row] }))
  await adopt()
  assert.deepEqual(calls.map(row => row[0]), ['complete-loader', 'selected'])
  assert.equal(error.value, '')
  assert.match(message.value, /完整参数方案/)
  assert.equal(busy.value, false)
})
