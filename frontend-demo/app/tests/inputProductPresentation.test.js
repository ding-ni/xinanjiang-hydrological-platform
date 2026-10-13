import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { inputProductPresentation, inputVersionViewState } from '../src/services/inputProductPresentation.js'
import { DEFAULT_INPUT_BUNDLE_VERSION_ID, frozenInputBundleVersionId, inputResultUsesSelection } from '../src/services/inputProductService.js'

const databaseSHA256 = '1'.repeat(64)
const qcSHA256 = '2'.repeat(64)
const source = {
  bundleVersionId: 'INPUT-MDB-source', name: 'technical original name', operation: 'mdbImport',
  sourceOrigin: { type: 'mdb-snapshot', databaseSHA256 }, summary: { eventCount: 17 },
  targetCoordinatePolicy: 'LEGACY_CLOSED_RING_VERTEX_MEAN', forcingValueDtype: 'float64', sourceReplayOnly: true,
  sourceQcPolicy: 'SOURCE_ORIGINAL_20261011', eventQualityConfigurationSHA256: qcSHA256,
  sourceQcOrigin: { sourceTaskId: 'CAL-original', sha256: qcSHA256 },
  recordQueryScope: { start: '2020-12-25T00:00:00', end: '2024-06-30T15:00:00' },
  coverage: { start: '2020-01-01T00:00:00', end: '2024-12-31T23:00:00', years: [2020, 2021, 2022, 2023, 2024] }
}
const boundCandidate = {
  requiredInputBundleVersionId: source.bundleVersionId, candidateKind: 'frozen-calibration',
  importProvenance: { sourceTaskId: 'CAL-original' },
  originalCalibrationPlan: { planId: 'SOURCE-PLAN-known', sourceTaskId: 'CAL-original', sourceMDBSHA256: databaseSHA256, qcConfigurationSHA256: qcSHA256 }
}

describe('calculation input presentation follows scientific provenance', () => {
  it('uses method, precision and QC provenance rather than final-version names or timestamps', () => {
    assert.equal(inputProductPresentation(source).kind, 'source-scheme')
    assert.equal(inputProductPresentation({ ...source, name: 'old label', createdAt: '1900-01-01' }).kind, 'source-scheme')
    assert.equal(inputProductPresentation({ name: '20261012终结版同事最新', createdAt: '2099-01-01' }).kind, 'registered')
    for (const patch of [
      { targetCoordinatePolicy: 'NC_GRID_AXIS_CENTRE' }, { forcingValueDtype: 'float32' },
      { sourceReplayOnly: false }, { eventQualityConfigurationSHA256: '3'.repeat(64) },
      { sourceQcOrigin: { sourceTaskId: 'CAL-original', sha256: '3'.repeat(64) } }
    ]) assert.notEqual(inputProductPresentation({ ...source, ...patch }).kind, 'source-scheme')
  })

  it('distinguishes independent and earlier interpolation comparisons without changing original registration', () => {
    const before = JSON.stringify(source)
    const legacy = { ...source, sourceQcPolicy: null }
    const independent = { bundleVersionId: 'INPUT-MDB-center', operation: 'mdbImport', name: '20261012终结版MDB独立资料', sourceOrigin: source.sourceOrigin }
    assert.equal(inputProductPresentation(legacy).kind, 'legacy-comparison')
    assert.equal(inputProductPresentation(independent).kind, 'independent-comparison')
    assert.equal(inputProductPresentation({ bundleVersionId: DEFAULT_INPUT_BUNDLE_VERSION_ID, operation: 'fixedBaseline' }).kind, 'historical')
    assert.equal(JSON.stringify(source), before)
  })

  it('checks an available original-plan binding against the exact input, MDB, QC and source task', () => {
    assert.equal(inputProductPresentation(source, { parameterVersions: [boundCandidate] }).kind, 'source-scheme')
    for (const patch of [
      { requiredInputBundleVersionId: 'INPUT-MDB-other' },
      { originalCalibrationPlan: { ...boundCandidate.originalCalibrationPlan, sourceMDBSHA256: '3'.repeat(64) } },
      { originalCalibrationPlan: { ...boundCandidate.originalCalibrationPlan, qcConfigurationSHA256: '3'.repeat(64) } },
      { importProvenance: { sourceTaskId: 'CAL-other' } }
    ]) assert.notEqual(inputProductPresentation(source, { parameterVersions: [{ ...boundCandidate, ...patch }] }).kind, 'source-scheme')
    assert.notEqual(inputProductPresentation(source, { parameterVersions: [] }).kind, 'source-scheme')
  })

  it('shows the extracted interval before full calendar axes or a catalog range', () => {
    const catalog = { bundleVersionId: source.bundleVersionId, coverage: { common: source.coverage } }
    const result = inputProductPresentation(source, { catalog })
    assert.equal(result.start, source.recordQueryScope.start)
    assert.equal(result.end, source.recordQueryScope.end)
    assert.equal(result.rangeKind, 'extracted')
    assert.ok(!result.range.includes('2024-12-31'))
    assert.ok(result.rangeNote.includes('缺测'))
  })

  it('only uses matching catalog coverage and identifies annual file axes as a file range', () => {
    const baseline = { bundleVersionId: DEFAULT_INPUT_BUNDLE_VERSION_ID, operation: 'fixedBaseline', coverage: { years: [1959, 2025] } }
    const foreign = { bundleVersionId: 'INPUT-MDB-other', coverage: { common: source.recordQueryScope } }
    assert.equal(inputProductPresentation(baseline, { catalog: foreign }).rangeKind, 'calendar-years')
    assert.equal(inputProductPresentation(baseline, { catalog: { ...foreign, bundleVersionId: baseline.bundleVersionId } }).rangeKind, 'common')
    assert.ok(inputProductPresentation(baseline).range.includes('年文件范围'))
  })

  it('viewing another record does not select it or alter frozen results', () => {
    const selection = { selectedInputBundleVersionId: DEFAULT_INPUT_BUNDLE_VERSION_ID }
    const result = { inputs: { bundleVersionId: source.bundleVersionId }, lineage: { inputBundleVersionId: source.bundleVersionId } }
    const before = JSON.stringify({ selection, result, source })
    assert.deepEqual(inputVersionViewState(source, selection.selectedInputBundleVersionId), { selected: false, viewingOnly: true })
    assert.deepEqual(inputVersionViewState(source, source.bundleVersionId), { selected: true, viewingOnly: false })
    assert.deepEqual(inputVersionViewState(null, source.bundleVersionId), { selected: false, viewingOnly: false })
    assert.equal(frozenInputBundleVersionId(result), source.bundleVersionId)
    assert.equal(inputResultUsesSelection(result, selection), false)
    assert.equal(JSON.stringify({ selection, result, source }), before)
  })
})
