import { it } from 'node:test'
import assert from 'node:assert/strict'
import { mergeBackendFloodEvents, registeredBackendEventSelection } from '../src/services/backendEventCatalogService.js'
import { normalizeModelInputCatalog } from '../src/services/modelInputService.js'
import { cloneWorkspaceValue, mergeWorkspaceFloodEvents } from '../src/services/workspaceSnapshotService.js'
import { eligibleFloodEvents, normalizeFloodEvent } from '../src/services/floodEventService.js'
import { selectCalibrationCandidates } from '../src/services/calibrationSubmissionService.js'

function qcEvent(overrides = {}) {
  return {
    id: 'xf-20170620',
    eventId: 'xf-20170620',
    basinProjectId: 'nananxi-xufan',
    sourceFloodNo: '20170620',
    name: '后端登记场次',
    start: '2017-06-20T00:00:00',
    end: '2017-06-21T23:00:00',
    observedPeak: 86,
    peakTime: '2017-06-21T08:00:00',
    rainCompleteness: 0.98,
    flowCompleteness: 0.95,
    sectionId: '70115800',
    source: 'model-qc-20260819',
    sourceFiles: { rain: 'qc-hourly.nc', river: 'xufan_hourly_qc.csv' },
    lineage: { inputVersion: 'QC-20260819' },
    status: 'active',
    revision: 1,
    purpose: '未使用',
    ...overrides
  }
}

function localEvent(overrides = {}) {
  return normalizeFloodEvent({
    ...qcEvent(),
    name: '人工命名',
    observedPeak: 9999,
    peakTime: '2017-06-21T09:00',
    volume: 20000000,
    volumeM3: 20000000,
    rainCompleteness: 1,
    flowCompleteness: 1,
    durationHours: 999,
    source: 'static-history-workbook',
    sourceFiles: { rain: 'old-rain.xlsx', river: 'old-flow.xlsx' },
    lineage: { inputVersion: 'old-static' },
    purpose: '验证',
    note: '人工复核备注',
    ...overrides
  })
}

it('QC statistics, time window and provenance cannot be overwritten by static event values', () => {
  const previous = localEvent()
  const backend = qcEvent()
  const [merged] = mergeBackendFloodEvents([previous], [backend])
  assert.equal(merged.observedPeak, backend.observedPeak)
  assert.equal(merged.peakTime, backend.peakTime)
  assert.equal(merged.rainCompleteness, backend.rainCompleteness)
  assert.equal(merged.flowCompleteness, backend.flowCompleteness)
  assert.equal(merged.riverCompleteness, backend.flowCompleteness)
  assert.equal(merged.volume, null)
  assert.equal(merged.volumeM3, null)
  assert.equal(merged.start, backend.start)
  assert.equal(merged.end, backend.end)
  assert.equal(merged.durationHours, 48)
  assert.equal(merged.source, backend.source)
  assert.deepEqual(merged.sourceFiles, backend.sourceFiles)
  assert.equal(merged.lineage.inputVersion, backend.lineage.inputVersion)
  assert.equal(merged.runtimeRegistered, true)
  assert.equal(merged.registeredStatus, 'active')
  assert.equal('localDraft' in merged, false)
})

it('all absent backend statistics stay null and supplied aliases come only from the backend', () => {
  const backend = qcEvent({ observedPeak: undefined, peakTime: undefined, rainCompleteness: undefined, flowCompleteness: undefined, sourceFiles: undefined })
  const [absent] = mergeBackendFloodEvents([localEvent()], [backend])
  for (const key of ['observedPeak', 'peakTime', 'volume', 'volumeM3', 'rainCompleteness', 'flowCompleteness', 'riverCompleteness', 'sourceFiles']) {
    assert.equal(absent[key], null, `${key} must not inherit a static value`)
  }
  const [aliased] = mergeBackendFloodEvents([localEvent()], [qcEvent({
    observedPeak: undefined, observedPeakFlowM3s: 0,
    peakTime: undefined, observedPeakTime: '2017-06-20T03:00:00',
    volumeM3: 0, flowCompleteness: undefined, riverCompleteness: 0
  })])
  assert.equal(aliased.observedPeak, 0)
  assert.equal(aliased.peakTime, '2017-06-20T03:00:00')
  assert.equal(aliased.volume, 0)
  assert.equal(aliased.volumeM3, 0)
  assert.equal(aliased.flowCompleteness, 0)
})

it('manual revised windows and the complete original record are preserved only as localDraft', () => {
  const previous = localEvent({
    start: '2017-06-19T10:00',
    end: '2017-06-22T12:00',
    revision: 4,
    customReview: { reviewer: '研究者', accepted: false },
    history: [{ revision: 3, snapshot: { start: '2017-06-19T06:00', observedPeak: 777 } }]
  })
  const [merged] = mergeBackendFloodEvents([previous], [qcEvent()])
  assert.deepEqual(merged.localDraft, previous)
  assert.equal(merged.start, qcEvent().start)
  assert.equal(merged.end, qcEvent().end)
  assert.equal(merged.observedPeak, 86)
  assert.equal(merged.durationHours, 48)
  assert.equal(merged.revision, 1)
  assert.equal(merged.name, previous.name)
  assert.equal(merged.note, previous.note)
  assert.deepEqual(merged.history, previous.history)
  assert.equal(merged.purpose, '验证')
})

it('a changed window is saved even at revision one, while equivalent timestamp forms are not edits', () => {
  const changed = localEvent({ start: '2017-06-19T00:00', revision: 1 })
  assert.deepEqual(mergeBackendFloodEvents([changed], [qcEvent()])[0].localDraft, changed)
  const same = localEvent({ start: '2017-06-20T00:00', end: '2017-06-21T23:00', revision: 1 })
  const [unchanged] = mergeBackendFloodEvents([same], [qcEvent({ durationHours: 999 })])
  assert.equal('localDraft' in unchanged, false)
  assert.equal(unchanged.durationHours, 48)
})

it('a manual revision without time changes still retains its full original snapshot', () => {
  const previous = localEvent({ revision: 2, customFlag: 'retain-me' })
  assert.deepEqual(mergeBackendFloodEvents([previous], [qcEvent()])[0].localDraft, previous)
})

it('user purpose assignments and inactive choices survive, but a local active flag cannot override QC pending', () => {
  for (const purpose of ['未使用', '率定', '验证', '独立检验']) {
    assert.equal(mergeBackendFloodEvents([localEvent({ purpose })], [qcEvent()])[0].purpose, purpose)
  }
  const [inactive] = mergeBackendFloodEvents([localEvent({ status: 'inactive', purpose: '独立检验' })], [qcEvent()])
  assert.equal(inactive.status, 'inactive')
  assert.equal(inactive.registeredStatus, 'active')
  assert.equal(inactive.purpose, '独立检验')
  const [pending] = mergeBackendFloodEvents([localEvent({ status: 'active', revision: 3 })], [qcEvent({ status: 'pending' })])
  assert.equal(pending.status, 'pending')
  assert.equal(pending.registeredStatus, 'pending')
  assert.deepEqual(eligibleFloodEvents([pending, inactive]), [])
  assert.deepEqual(selectCalibrationCandidates([pending, inactive]), [])
})

it('unregistered historical and manual events stay in the catalog as non-eligible drafts or inactive records', () => {
  const extra = localEvent({ id: 'xf-manual-a', eventId: 'xf-manual-a', observedPeak: 500, purpose: '率定', customReview: { note: '保留' } })
  const inactive = localEvent({ id: 'xf-historical-b', eventId: 'xf-historical-b', status: 'inactive', purpose: '验证' })
  const merged = mergeBackendFloodEvents([extra, inactive], [qcEvent()])
  assert.equal(merged.length, 3)
  const drafts = merged.filter((event) => !event.runtimeRegistered)
  assert.deepEqual(drafts.map((event) => event.eventId), ['xf-manual-a', 'xf-historical-b'])
  assert.equal(drafts[0].status, 'draft')
  assert.equal(drafts[0].registeredStatus, null)
  assert.equal(drafts[0].purpose, '率定')
  assert.equal(drafts[0].observedPeak, 500)
  assert.deepEqual(drafts[0].customReview, extra.customReview)
  assert.equal(drafts[1].status, 'inactive')
  assert.equal(drafts[1].purpose, '验证')
  assert.deepEqual(eligibleFloodEvents(drafts), [])
  assert.deepEqual(selectCalibrationCandidates(drafts), [])
})

it('repeated merges are idempotent and never nest or replace an existing localDraft', () => {
  const original = localEvent({ revision: 5, history: [{ revision: 4, note: '原记录' }] })
  const unknown = localEvent({ id: 'xf-old', eventId: 'xf-old', purpose: '独立检验' })
  const once = mergeBackendFloodEvents([original, unknown], [qcEvent()])
  let again = once
  for (let count = 0; count < 10; count += 1) again = mergeBackendFloodEvents(again, [qcEvent()])
  assert.deepEqual(again, once)
  assert.deepEqual(again[0].localDraft, original)
  assert.equal('localDraft' in again[0].localDraft, false)
  const [refreshed] = mergeBackendFloodEvents(again, [qcEvent({ observedPeak: 87 })])
  assert.equal(refreshed.observedPeak, 87)
  assert.deepEqual(refreshed.localDraft, original)
})

it('the merge does not mutate or share nested user snapshots and backend provenance', () => {
  const previous = localEvent({ revision: 2, history: [{ revision: 1, snapshot: { observedPeak: 999 } }] })
  const backend = qcEvent()
  const oldPrevious = structuredClone(previous)
  const oldBackend = structuredClone(backend)
  const [merged] = mergeBackendFloodEvents([previous], [backend])
  merged.localDraft.history[0].snapshot.observedPeak = 42
  merged.history[0].snapshot.observedPeak = 41
  merged.sourceFiles.rain = 'changed'
  merged.lineage.inputVersion = 'changed'
  assert.deepEqual(previous, oldPrevious)
  assert.deepEqual(backend, oldBackend)
})

it('missing backend registration does not delete data or expose local-only events for calibration', () => {
  const previous = localEvent({ purpose: '率定' })
  const [local] = mergeBackendFloodEvents([previous], [])
  assert.equal(local.runtimeRegistered, false)
  assert.equal(local.status, 'draft')
  assert.equal(local.purpose, '率定')
  assert.equal(local.observedPeak, previous.observedPeak)
  assert.deepEqual(eligibleFloodEvents([local]), [])
  assert.deepEqual(mergeBackendFloodEvents(), [])
  assert.deepEqual(mergeBackendFloodEvents(null, null), [])
})

it('an empty selected product has a serializable null selection while every local draft remains intact', () => {
  const original = localEvent({ revision: 3, history: [{ revision: 2, note: '用户原始修订' }] })
  const before = structuredClone(original)
  const events = mergeBackendFloodEvents([original], [])
  const selection = registeredBackendEventSelection(events, original.id)
  assert.deepEqual(selection, { eventId: null, eventStart: null, eventEnd: null })
  assert.deepEqual(cloneWorkspaceValue(selection), selection)
  assert.equal(events.length, 1)
  assert.equal(events[0].runtimeRegistered, false)
  assert.equal(events[0].status, 'draft')
  assert.deepEqual(events[0].history, original.history)
  assert.deepEqual(original, before)
  assert.deepEqual(registeredBackendEventSelection([]), selection)
})

it('legacy undefined optional workspace fields serialize as missing null, never fabricated values', () => {
  assert.equal(cloneWorkspaceValue(undefined), null)
  assert.equal(cloneWorkspaceValue(null), null)
  assert.equal(cloneWorkspaceValue(0), 0)
  assert.equal(cloneWorkspaceValue(false), false)
  assert.deepEqual(cloneWorkspaceValue([]), [])
  const old = { nested: { note: 'unchanged' }, times: ['2017-06-19T11:00'] }
  const snapshot = cloneWorkspaceValue(old)
  snapshot.nested.note = 'changed'
  assert.equal(old.nested.note, 'unchanged')
})

it('real input-catalog event envelopes gain only verified scope and retain QC corrected windows through save/restore', () => {
  const raw = { id: 'xf-31170619', eventId: 'xf-31170619', sourceFloodNo: '31170619', start: '2017-06-19T11:00', end: '2017-06-29T11:00', status: 'ready', source: 'INPUT-REAL-SHAPE', observedPeak: 225, peakTime: '2017-06-24T11:00:00', volumeM3: null, sectionId: '70115800', revision: 1, purpose: '未使用', bundleVersionId: 'INPUT-REAL-SHAPE', rainCompleteness: 1, flowCompleteness: 1 }
  const catalog = normalizeModelInputCatalog({ caseKey: 'nananxi-xufan:1km', bundleVersionId: 'INPUT-REAL-SHAPE', rain: { start: '2017-06-12T11:00', end: '2017-06-29T11:00' }, river: { start: '1964-04-04T00:00', end: '2024-07-06T08:00' }, events: [raw], provenance: { notes: ['仅诊断、未授予生产资格'] } })
  assert.equal(catalog.events[0].basinProjectId, 'nananxi-xufan')
  assert.equal(raw.basinProjectId, undefined, 'normalization must not mutate API payload')
  assert.deepEqual(catalog.qualityNotes, ['仅诊断、未授予生产资格'])
  const previous = localEvent({ id: raw.id, eventId: raw.eventId, start: raw.start, end: '2017-06-25T08:00', revision: 2 })
  const merged = mergeBackendFloodEvents([previous], catalog.events)
  const restored = cloneWorkspaceValue(merged).filter((event) => event.basinProjectId === 'nananxi-xufan')
  assert.equal(restored.length, 1, 'the event must survive exact workspace scope filtering')
  assert.equal(restored[0].end, raw.end, 'the QC adjusted endpoint cannot become the static old endpoint')
  assert.equal(restored[0].observedPeak, 225)
  assert.equal(restored[0].volumeM3, null)
  assert.equal(restored[0].bundleVersionId, raw.bundleVersionId)
  assert.equal(restored[0].registeredStatus, 'active')
  assert.equal(restored[0].purpose, previous.purpose)
  assert.deepEqual(restored[0].localDraft, previous)
  assert.deepEqual(registeredBackendEventSelection(restored), { eventId: raw.id, eventStart: raw.start, eventEnd: raw.end })
})

it('input-catalog scope adaptation rejects explicit foreign case or event metadata rather than relabelling it', () => {
  const payload = { caseKey: 'nananxi-xufan:1km', rain: { start: '2017-06-19T11:00', end: '2017-06-29T11:00' }, river: { start: '2017-06-19T11:00', end: '2017-06-29T11:00' }, events: [qcEvent({ basinProjectId: 'selinco' })] }
  assert.throws(() => normalizeModelInputCatalog(payload), /另一流域/)
  assert.throws(() => normalizeModelInputCatalog({ ...payload, caseKey: 'nananxi-xufan:0p1deg', events: [] }), /不属于南安溪 1 km/)
})

const nativeWorkspace = { domainId: 'nananxi-xufan', modelScale: '1km', contextKey: 'nananxi-xufan:xaj-1km:1km' }

it('same-workspace known legacy event scopes recover without losing local notes, assignments or complete snapshots', () => {
  for (const scope of [undefined, null, '', 'unknown-project']) {
    const previous = { ...localEvent({ purpose: '独立检验', revision: 4,
      history: [{ revision: 3, note: '保留原记录', customReview: { accepted: false } }],
      localDraft: { id: qcEvent().id, basinProjectId: 'unknown-project', note: '原始标签不重写', revision: 3, customOriginal: { untouched: true } } }), basinProjectId: scope }
    const original = structuredClone(previous)
    const [restored] = mergeWorkspaceFloodEvents([qcEvent()], [previous], nativeWorkspace, nativeWorkspace)
    assert.equal(restored.basinProjectId, 'nananxi-xufan')
    for (const key of ['purpose', 'note', 'history', 'localDraft']) assert.deepEqual(restored[key], original[key])
    assert.equal(restored.observedPeak, previous.observedPeak, 'scope recovery does not silently change a saved statistic')
    const [registered] = mergeBackendFloodEvents([restored], [qcEvent()])
    assert.equal(registered.observedPeak, qcEvent().observedPeak, 'only the real backend merge supplies authoritative QC values')
    for (const key of ['purpose', 'note', 'history', 'localDraft']) assert.deepEqual(registered[key], original[key])
    assert.deepEqual(previous, original)
  }
})

it('legacy scope recovery never relabels explicit foreign scopes, unmatched IDs or a foreign base event', () => {
  const foreign = localEvent({ basinProjectId: 'selinco' })
  const unknown = localEvent({ id: 'xf-manual-unknown', eventId: 'xf-manual-unknown', basinProjectId: 'unknown-project' })
  const restored = mergeWorkspaceFloodEvents([qcEvent()], [foreign, unknown], nativeWorkspace, nativeWorkspace)
  assert.equal(restored.find(event => event.id === foreign.id).basinProjectId, 'selinco')
  assert.equal(restored.find(event => event.id === unknown.id).basinProjectId, 'unknown-project')
  const [wrongBase] = mergeWorkspaceFloodEvents([qcEvent({ basinProjectId: 'selinco' })], [localEvent({ basinProjectId: 'unknown-project' })], nativeWorkspace, nativeWorkspace)
  assert.equal(wrongBase.basinProjectId, 'unknown-project')
})

it('legacy scope recovery requires exact verified domain, scale and workspace identity', () => {
  const previous = localEvent({ basinProjectId: 'unknown-project' })
  for (const saved of [{ ...nativeWorkspace, domainId: 'selinco' }, { ...nativeWorkspace, modelScale: '0p1deg' },
    { ...nativeWorkspace, contextKey: 'another-model-workspace' }, {}, null]) {
    const [restored] = mergeWorkspaceFloodEvents([qcEvent()], [previous], nativeWorkspace, saved)
    assert.equal(restored.basinProjectId, 'unknown-project')
  }
})

it('legacy workspace scope restoration is idempotent and does not share nested evidence or grant registration', () => {
  const previous = localEvent({ basinProjectId: 'unknown-project', runtimeRegistered: false,
    localDraft: { history: [{ note: '完整草稿' }] }, history: [{ note: '完整历史' }] })
  const original = structuredClone(previous)
  const base = qcEvent()
  const baseBefore = structuredClone(base)
  const restored = mergeWorkspaceFloodEvents([base], [previous], nativeWorkspace, nativeWorkspace)
  const again = mergeWorkspaceFloodEvents([base], restored, nativeWorkspace, nativeWorkspace)
  assert.deepEqual(again, restored)
  assert.equal(restored[0].runtimeRegistered, false)
  restored[0].history[0].note = 'changed'
  restored[0].localDraft.history[0].note = 'changed draft'
  assert.deepEqual(previous, original)
  assert.deepEqual(base, baseBefore)
})
