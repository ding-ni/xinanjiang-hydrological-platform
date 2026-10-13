import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hydrologyText, hydrologyNumber, parameterSchemeLabel } from '../src/services/hydrologyPresentation.js'

test('human terminology is expanded without rewriting immutable version identifiers', () => {
  assert.equal(hydrologyText('QC / IDW / PET'), '质量检查 / 距离反比加权插值 / 潜在蒸散发')
  assert.equal(hydrologyText('原 OBSERVED_FLOW_ANCHORED 策略'), '原 按实测流量初始化 策略')
  assert.equal(hydrologyText('NANANXI_QC_20260819_CONTROL_V2'), 'NANANXI_QC_20260819_CONTROL_V2')
  assert.equal(hydrologyText(null), '')
  assert.equal(hydrologyText('交付固定 QC 20260819 / 控制切分 v2'), '交付固定 质量检查 20260819 / 控制切分 v2')
  assert.equal(hydrologyText('合作者交付固定 QC；参考诊断资料，未获得生产资格'), '合作者交付固定 质量检查；参考诊断资料，未获得生产资格')
})

test('hydrological values show bounded precision without making missing values zero', () => {
  assert.equal(hydrologyNumber(65.36309776569246), '65.36')
  assert.equal(hydrologyNumber(0), '0.00')
  for (const value of [null, undefined, '', '65', NaN, Infinity]) assert.equal(hydrologyNumber(value), '—')
})

test('parameter labels distinguish the original frozen and manual candidates using source metadata', () => {
  const id = 'PARAM-JOB-import-20261012-12345678'
  const source = {
    candidateKind: 'frozen-calibration',
    importProvenance: { sourceTaskId: 'CAL-source', candidateKind: 'frozen-calibration' },
    originalCalibrationPlan: { sourceTaskId: 'CAL-source', planId: 'SOURCE-PLAN-original' }
  }
  assert.equal(parameterSchemeLabel(id, source), '导入原方案 · 冻结率定候选 · 12345678')
  const manual = { ...source, candidateKind: 'manual-edit', importProvenance: { ...source.importProvenance, candidateKind: 'manual-edit' } }
  assert.equal(parameterSchemeLabel(id, manual), '导入原方案 · 人工修改候选 · 12345678')
  assert.equal(parameterSchemeLabel(id, { ...source, originalCalibrationPlan: null }), '导入候选 · 冻结率定候选 · 12345678')
  assert.equal(parameterSchemeLabel(id, { ...source, originalCalibrationPlan: { ...source.originalCalibrationPlan, sourceTaskId: 'CAL-another' } }), '导入候选 · 冻结率定候选 · 12345678')
  assert.equal(source.candidateKind, 'frozen-calibration')
})

test('import-looking IDs or incomplete metadata do not manufacture original-scheme labels', () => {
  const id = 'PARAM-JOB-import-20261012-12345678'
  for (const record of [null, { candidateKind: 'frozen-calibration' }, { importProvenance: {} },
    { candidateKind: 'manual-edit', importProvenance: { sourceTaskId: 'CAL-source', candidateKind: 'frozen-calibration' } }]) {
    assert.equal(parameterSchemeLabel(id, record), '率定参数 · 12345678')
  }
  assert.equal(parameterSchemeLabel('NANANXI_XAJ_REFERENCE_V1'), '新安江参考参数')
  assert.equal(parameterSchemeLabel('XAJ-SEL-v1.0'), 'XAJ-SEL-v1.0')
})
