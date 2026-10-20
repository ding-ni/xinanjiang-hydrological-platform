import { test } from 'node:test'
import assert from 'node:assert/strict'
import { filterTasks, recoverableCalculation } from '../src/services/taskPresentation.js'

test('returning to a scheme draft never adopts another historical calculation', () => {
  const history = [{ taskId: 'old', createdAt: '2026-01-01' }, { taskId: 'new', createdAt: '2026-02-01' }]
  for (const draft of [{ dirty: true }, { modelSchemeId: 'scheme' }, { modelSchemeName: '新方案' }]) {
    assert.equal(recoverableCalculation(history, draft), null)
  }
  assert.equal(recoverableCalculation(history, { activeTaskId: 'old', dirty: true }), history[0])
  assert.equal(recoverableCalculation(history, { activeTaskId: 'missing' }), null)
  assert.equal(recoverableCalculation(history, {}), history[1])
  assert.equal(history[0].taskId, 'old')
})

const rows = [
  { taskId: 'JOB-ABC123', runTypeLabel: '点查询', status: 'completed', stageLabel: '结果保存', runConfig: { runType: 'pointQuery' } },
  { taskId: 'JOB-DEF456', runTypeLabel: '稳定性实验', status: 'running', stageLabel: '参数搜索' },
  { taskId: 'JOB-789', runTypeLabel: '稳定性实验', status: 'failed', errorMessage: '雨量缺测' },
]

test('task search combines literal terms, names, IDs and hydrological errors', () => {
  assert.deepEqual(filterTasks(rows, { query: ' abc123 完成 ' }), [rows[0]])
  assert.deepEqual(filterTasks(rows, { query: 'PointQuery' }), [rows[0]])
  assert.deepEqual(filterTasks(rows, { query: '雨量缺测' }), [rows[2]])
  assert.deepEqual(filterTasks(rows, { query: '[' }), [])
})

test('task status and search compose without changing the task registry', () => {
  const original = JSON.stringify(rows)
  assert.deepEqual(filterTasks(rows, { query: '实验', status: 'running' }), [rows[1]])
  assert.deepEqual(filterTasks(rows), rows)
  assert.deepEqual(filterTasks([], { query: '点查询', status: 'completed' }), [])
  assert.equal(JSON.stringify(rows), original)
})
