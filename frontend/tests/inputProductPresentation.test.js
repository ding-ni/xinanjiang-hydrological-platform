import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { inputProductContents, inputProductPresentation, inputVersionRelatedTasks, inputVersionViewState } from '../src/services/inputProductPresentation.js'
import { DEFAULT_INPUT_BUNDLE_VERSION_ID, frozenInputBundleVersionId, inputResultUsesSelection } from '../src/services/inputProductService.js'

const source = {
  bundleVersionId: 'INPUT-import-source', name: '测试流域数据库导入资料', operation: 'mdbImport',
  sourceOrigin: { type: 'mdb-snapshot', databaseSHA256: '1'.repeat(64) }, summary: { eventCount: 9, acceptedEventCount: 7 },
  sourceTaskId: 'DATA-original',
  recordQueryScope: { start: '2020-12-25T00:00:00', end: '2024-06-30T15:00:00' },
  coverage: { start: '2020-01-01T00:00:00', end: '2024-12-31T23:00:00', years: [2020, 2021, 2022, 2023, 2024] }
}

describe('calculation input presentation uses workspace records', () => {
  it('keeps record names for any project and classifies only the formation operation', () => {
    assert.equal(inputProductPresentation(source).kind, 'mdbImport')
    assert.equal(inputProductPresentation(source).name, source.name)
    const legacy = { ...source, name: '原任务 QC 与 IDW 方案', sourceReplayOnly: true, forcingValueDtype: 'float64' }
    assert.equal(inputProductPresentation(legacy).name, legacy.name)
    assert.equal(inputProductPresentation(legacy).kind, 'mdbImport')
    assert.equal(inputProductPresentation({ name: '最终方案', createdAt: '2099-01-01' }).kind, 'registered')
    assert.equal(inputProductPresentation({ ...source, operation: 'stationIdw' }).kind, 'stationIdw')
    assert.ok(inputProductPresentation({ operation: 'qcWindow' }).purpose.includes('提取时段'))
    assert.equal(inputProductPresentation({ operation: 'futureOperation' }).kind, 'registered')
  })

  it('retains legacy provenance and frozen records without built-in display aliases', () => {
    const before = JSON.stringify(source)
    inputProductPresentation(source, { parameterVersions: [{ requiredInputBundleVersionId: source.bundleVersionId }] })
    assert.equal(inputProductPresentation({ bundleVersionId: 'INPUT-another-project', operation: 'fixedBaseline', name: '项目原登记资料' }).name, '项目原登记资料')
    assert.equal(inputProductPresentation({ bundleVersionId: DEFAULT_INPUT_BUNDLE_VERSION_ID, operation: 'fixedBaseline' }).kind, 'fixedBaseline')
    assert.equal(JSON.stringify(source), before)
  })

  it('distinguishes rainfall, observed flow, directory windows and actual check counts', () => {
    const record = { ...source, contents: { hasGridRainfall: true, gridCount: 32, hasOutletFlow: true,
      flowStationId: 'FLOW-A', hasEventCatalog: true, eventCount: 9, acceptedEventCount: 7, rainStationCount: 5 } }
    const before = JSON.stringify(record)
    const content = inputProductContents(record)
    assert.deepEqual(content.rows.map(row => row.key), ['rain', 'flow', 'events', 'stations'])
    assert.ok(content.rows[0].value.includes('32'))
    assert.ok(content.rows[0].note.includes('按提取时段'))
    assert.ok(content.rows[1].value.includes('FLOW-A'))
    assert.ok(content.rows[2].value.includes('9 个登记窗口'))
    assert.ok(content.rows[2].value.includes('通过检查 7 个'))
    assert.ok(content.rows[2].note.includes('作业页选择'))
    assert.equal(content.rainStationCount, 5)
    assert.equal(JSON.stringify(record), before)
  })

  it('does not invent station counts or acceptance from directory presence and rejects foreign catalog data', () => {
    const unknown = inputProductContents({ bundleVersionId: 'INPUT-empty' }, { catalog: {
      bundleVersionId: source.bundleVersionId, gridCount: 999, events: [{ id: 'foreign' }],
      coverage: { precipitation: source.recordQueryScope, observedFlow: source.recordQueryScope }
    }, sources: [{ sourceId: 'foreign', stationCount: 32 }] })
    assert.equal(unknown.gridCount, null)
    assert.equal(unknown.eventCount, null)
    assert.equal(unknown.rainStationCount, null)
    assert.equal(unknown.acceptedEventCount, null)
    assert.equal(unknown.hasGridRainfall, false)
    const catalog = { bundleVersionId: source.bundleVersionId, gridCount: 23, events: [],
      coverage: { precipitation: source.recordQueryScope, observedFlow: source.recordQueryScope } }
    const content = inputProductContents({ bundleVersionId: source.bundleVersionId }, { catalog })
    assert.equal(content.gridCount, 23)
    assert.equal(content.eventCount, 0)
    assert.equal(content.acceptedEventCount, null)
    assert.ok(content.rows[2].value.includes('未统计'))
  })

  it('uses original station scope or exact registered source as evidence, including explicit zero', () => {
    assert.equal(inputProductContents({ ...source, recordQueryScope: { ...source.recordQueryScope, rainStations: ['A', 'B'] } }).rainStationCount, 2)
    assert.equal(inputProductContents({ ...source, sourceOrigin: { stationIds: ['A'] } }).rainStationCount, 1)
    assert.equal(inputProductContents({ ...source, sourceId: 'source-a' }, { sources: [{ sourceId: 'source-a', stationCount: 3 }] }).rainStationCount, 3)
    assert.equal(inputProductContents({ ...source, contents: { rainStationCount: 0 } }).rainStationCount, 0)
  })

  it('shows only tasks that produced the viewed version, not siblings from the same source', () => {
    const tasks = [{ taskId: source.sourceTaskId }, { taskId: 'same-version', registeredBundleVersionId: source.bundleVersionId },
      { taskId: 'sibling', request: { parentBundleVersionId: source.bundleVersionId } },
      { taskId: 'other', registeredBundleVersionId: 'INPUT-other' }]
    assert.deepEqual(inputVersionRelatedTasks(source, tasks).map(row => row.taskId), [source.sourceTaskId, 'same-version'])
    assert.deepEqual(inputVersionRelatedTasks({}, tasks), [])
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

  it('retains a generated rainfall interval when paired flow covers only a shorter period', () => {
    const generated = { bundleVersionId: 'INPUT-generated', operation: 'stationIdw',
      coverage: { start: '2024-05-01T00:00:00', end: '2024-05-07T23:00:00' } }
    const catalog = { bundleVersionId: generated.bundleVersionId,
      coverage: { common: { start: '2024-05-02T00:00:00', end: '2024-05-06T23:00:00' } } }
    const viewed = inputProductPresentation(generated, { catalog })
    assert.equal(viewed.start, generated.coverage.start)
    assert.equal(viewed.end, generated.coverage.end)
    assert.equal(viewed.rangeKind, 'declared')
    const unscoped = { gridCount: 999, events: [], coverage: { precipitation: generated.coverage } }
    assert.equal(inputProductContents({}, { catalog: unscoped }).gridCount, null)
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
