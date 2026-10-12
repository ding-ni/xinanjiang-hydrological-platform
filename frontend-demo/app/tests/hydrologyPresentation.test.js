import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hydrologyText, hydrologyNumber } from '../src/services/hydrologyPresentation.js'

test('human terminology is expanded without rewriting immutable version identifiers', () => {
  assert.equal(hydrologyText('QC / IDW / PET'), '质量检查 / 距离反比加权插值 / 潜在蒸散发')
  assert.equal(hydrologyText('原 OBSERVED_FLOW_ANCHORED 策略'), '原 按实测流量初始化 策略')
  assert.equal(hydrologyText('NANANXI_QC_20260819_CONTROL_V2'), 'NANANXI_QC_20260819_CONTROL_V2')
  assert.equal(hydrologyText(null), '')
  assert.equal(hydrologyText('交付固定 QC 20260819 / 控制切分 v2'), '交付整编资料 20260819 / 徐畈控制断面 v2')
})

test('hydrological values show bounded precision without making missing values zero', () => {
  assert.equal(hydrologyNumber(65.36309776569246), '65.36')
  assert.equal(hydrologyNumber(0), '0.00')
  for (const value of [null, undefined, '', '65', NaN, Infinity]) assert.equal(hydrologyNumber(value), '—')
})
