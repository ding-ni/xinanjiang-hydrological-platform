import { it } from 'node:test'
import assert from 'node:assert/strict'
import { mergeBackendFloodEvents } from '../src/services/backendEventCatalogService.js'
import { restoreWorkspaceEventRecords } from '../src/services/workspaceSnapshotService.js'

const context = { domainId: 'nananxi-xufan', modelScale: '1km', contextKey: 'nananxi-xufan:xaj-1km:1km' }
const known = { id: 'xf-known', eventId: 'xf-known', basinProjectId: 'nananxi-xufan', status: 'active', purpose: '未使用', runtimeRegistered: true, observedPeak: 21 }
const foreign = { ...known, basinProjectId: 'selinco', purpose: '独立检验', note: '原样保留的外流域记录',
  history: [{ revision: 3, customReview: { accepted: false } }], localDraft: { customOriginal: { zero: 0, complete: false } } }
const unmatched = { id: 'unmatched-unknown', eventId: 'unmatched-unknown', basinProjectId: 'unknown-project', status: 'draft',
  purpose: '率定', observedPeak: null, custom: { value: 0, flag: false }, history: [{ note: '完整原始历史' }] }

it('excluded foreign and unmatched legacy rows persist verbatim in separate evidence, never in calculation events', () => {
  const old = [structuredClone(foreign), structuredClone(unmatched)]
  const restored = restoreWorkspaceEventRecords([known], old, context, context)
  assert.equal(restored.events.length, 0)
  assert.deepEqual(restored.quarantinedEvents, old)
  const registered = mergeBackendFloodEvents(restored.events, [known])
  assert.equal(registered.length, 1)
  assert.equal(registered[0].purpose, '未使用')
  assert.equal(registered[0].basinProjectId, 'nananxi-xufan')
  assert.notEqual(registered[0].note, foreign.note)
  assert.equal(registered.some(event => event.id === unmatched.id), false)
  assert.deepEqual(old, [foreign, unmatched])
})

it('repeated save and restore retains every excluded snapshot without growing duplicate or nested evidence', () => {
  let saved = restoreWorkspaceEventRecords([known], [foreign, unmatched], context, context)
  const before = structuredClone(saved.quarantinedEvents)
  for (let count = 0; count < 12; count += 1) {
    saved = restoreWorkspaceEventRecords([known], JSON.parse(JSON.stringify(saved.events)), context, context,
      JSON.parse(JSON.stringify(saved.quarantinedEvents)))
  }
  assert.deepEqual(saved.quarantinedEvents, before)
  assert.equal(saved.quarantinedEvents.length, 2)
  assert.equal(saved.quarantinedEvents.some(event => 'quarantinedEvents' in event), false)
})

it('quarantine deduplicates only equal snapshots and retains distinct historical versions with the same event ID', () => {
  const oldForeign = { ...structuredClone(foreign), note: '原来的备注' }
  const reordered = Object.fromEntries(Object.entries(structuredClone(foreign)).reverse())
  const restored = restoreWorkspaceEventRecords([known], [foreign], context, context, [oldForeign, reordered])
  assert.equal(restored.quarantinedEvents.length, 2)
  assert.deepEqual(restored.quarantinedEvents, [oldForeign, reordered])
  assert.equal(restored.quarantinedEvents[1].basinProjectId, 'selinco')
})

it('colliding saved foreign or unmatched unknown IDs retain all originals without growth on repeated restore', () => {
  const otherForeign = { ...structuredClone(foreign), basinProjectId: 'another-basin', note: '同ID的另一完整原记录',
    localDraft: { originalRevision: 8 } }
  const otherUnknown = { ...structuredClone(unmatched), note: '同未知ID的另一完整原记录', history: [{ revision: 8 }] }
  for (const originals of [[foreign, otherForeign], [unmatched, otherUnknown]]) {
    let restored = restoreWorkspaceEventRecords([known], originals, context, context)
    assert.equal(restored.events.some(event => originals.some(original => original.id === event.id)), false)
    assert.deepEqual(restored.quarantinedEvents, originals)
    for (let count = 0; count < 8; count += 1) {
      restored = restoreWorkspaceEventRecords([known], restored.events, context, context, restored.quarantinedEvents)
      assert.deepEqual(restored.quarantinedEvents, originals)
    }
  }
})

it('a foreign snapshot is preserved when a later same-basin row with its ID is adopted, and vice versa', () => {
  const native = { ...known, note: '合法同流域的本地备注', purpose: '验证', history: [{ revision: 5 }] }
  const nativeLast = restoreWorkspaceEventRecords([known], [foreign, native], context, context)
  assert.equal(nativeLast.events.length, 1)
  assert.equal(nativeLast.events[0].note, native.note)
  assert.equal(nativeLast.events[0].purpose, native.purpose)
  assert.deepEqual(nativeLast.quarantinedEvents, [foreign])
  const foreignLast = restoreWorkspaceEventRecords([known], [native, foreign], context, context)
  assert.equal(foreignLast.events.length, 0)
  assert.deepEqual(foreignLast.quarantinedEvents, [native, foreign])
})

it('distinct shadowed same-scope or recoverable legacy snapshots are retained without adopting them twice', () => {
  for (const scope of ['nananxi-xufan', 'unknown-project']) {
    const earlier = { ...known, basinProjectId: scope, note: '前一完整快照', purpose: '率定' }
    const latest = { ...known, basinProjectId: scope, note: '后一完整快照', purpose: '验证' }
    let restored = restoreWorkspaceEventRecords([known], [earlier, latest], context, context)
    assert.equal(restored.events.length, 1)
    assert.equal(restored.events[0].note, latest.note)
    assert.deepEqual(restored.quarantinedEvents, [earlier])
    for (let count = 0; count < 8; count += 1) {
      restored = restoreWorkspaceEventRecords([known], restored.events, context, context, restored.quarantinedEvents)
      assert.deepEqual(restored.quarantinedEvents, [earlier])
      assert.equal(restored.events.length, 1)
    }
  }
})

it('old isolated evidence never promotes itself into active events even when its ID becomes registered later', () => {
  const previouslyQuarantined = { ...known, basinProjectId: 'unknown-project', purpose: '率定', note: '不得自动采用的旧备注' }
  const restored = restoreWorkspaceEventRecords([known], [], context, context, [previouslyQuarantined])
  assert.equal(restored.events.length, 1)
  assert.equal(restored.events[0].purpose, '未使用')
  assert.notEqual(restored.events[0].note, previouslyQuarantined.note)
  assert.deepEqual(restored.quarantinedEvents, [previouslyQuarantined])
})

it('scope recovery is confined to the verified workspace while original nested drafts and other quarantined records remain intact', () => {
  const recoverable = { ...known, basinProjectId: 'unknown-project', purpose: '验证', note: '已知ID保留备注',
    history: [{ revision: 4 }], localDraft: { basinProjectId: 'unknown-project', original: true } }
  const restored = restoreWorkspaceEventRecords([known], [recoverable, unmatched], context, context, [foreign])
  assert.equal(restored.events.length, 1)
  assert.equal(restored.events[0].basinProjectId, 'nananxi-xufan')
  assert.equal(restored.events[0].purpose, '验证')
  assert.deepEqual(restored.events[0].history, recoverable.history)
  assert.deepEqual(restored.events[0].localDraft, recoverable.localDraft)
  assert.deepEqual(restored.quarantinedEvents, [foreign, unmatched])
  const rejected = restoreWorkspaceEventRecords([known], [recoverable], context, { ...context, modelScale: '0p1deg' })
  assert.equal(rejected.events.length, 0)
  assert.deepEqual(rejected.quarantinedEvents, [recoverable])
})

it('retained evidence is independently cloned and absent legacy quarantine fields remain compatible', () => {
  const old = [structuredClone(foreign)]
  const restored = restoreWorkspaceEventRecords([known], old, context, context, null)
  restored.quarantinedEvents[0].history[0].customReview.accepted = true
  restored.quarantinedEvents[0].localDraft.customOriginal.zero = 99
  assert.deepEqual(old, [foreign])
  assert.deepEqual(restoreWorkspaceEventRecords([known], undefined, context, context).quarantinedEvents, [])
})
