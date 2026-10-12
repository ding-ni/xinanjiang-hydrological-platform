import { test } from 'node:test'
import assert from 'node:assert/strict'
import { diagnosticValue, diagnosticThreshold } from '../src/services/diagnosticPresentation.js'

test('diagnostic gates use human hydrological labels without altering raw objects', () => {
  const value = { validation: -87.81147386, independent_test: 26.70501438 }
  assert.equal(diagnosticValue(value), '验证 -87.8115；独立检验 26.705')
  assert.equal(value.validation, -87.81147386)
  assert.equal(diagnosticValue('MONTHLY_CLIMATOLOGY'), '逐月假设蒸散发')
  assert.equal(diagnosticValue('REVIEW_REQUIRED'), '待复核')
  assert.equal(diagnosticThreshold('observed or independently derived PET'), '实测或独立推求蒸散发')
  assert.equal(diagnosticThreshold('absolute <= 15.0%'), '绝对值 ≤ 15.0%')
})

test('gate precision preserves small nonzero balance errors and absent values', () => {
  assert.equal(diagnosticValue(1.1742916448061826e-12), '1.17e-12')
  assert.equal(diagnosticValue(0), '0')
  for (const value of [null, undefined, NaN, Infinity]) assert.equal(diagnosticValue(value), '缺测')
  assert.equal(diagnosticValue('NANANXI_QC_20260819_CONTROL_V2'), 'NANANXI_QC_20260819_CONTROL_V2')
})
