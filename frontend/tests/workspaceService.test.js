import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const key = 'hydro_calibration_workspaces_v1'
const eventKey = 'hydro_flood_event_overrides_v1:nananxi-xufan'
let moduleId = 0
async function fixture(t, { local = {}, drafts = {}, id = 'WS-first' } = {}) {
  const storage = Object.assign({}, local)
  Object.defineProperties(storage, {
    getItem: { value: name => storage[name] ?? null },
    setItem: { value: (name, value) => { storage[name] = String(value) } },
    removeItem: { value: name => { delete storage[name] } },
  })
  t.mock.method(globalThis, 'setTimeout', () => 0)
  const original = globalThis.localStorage
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
  t.after(() => Object.defineProperty(globalThis, 'localStorage', { value: original, configurable: true }))
  const server = { id, drafts: structuredClone(drafts), posts: [], failure: false, beforePost: null }
  const symbol = '__workspaceTest' + moduleId++
  globalThis[symbol] = async (path, options) => {
    if (server.failure) throw new Error('连接失败')
    if (path === 'workspace') return { workspace: { id: server.id } }
    if (!options) return { drafts: structuredClone(server.drafts) }
    const body = structuredClone(options.body)
    if (server.beforePost) await server.beforePost(body)
    server.posts.push(body)
    if ((server.drafts[body.key]?.revision || 0) !== body.revision) throw new Error('草稿已在另一页面更新')
    const row = { value: body.value, revision: body.revision + 1 }
    server.drafts[body.key] = row
    return structuredClone(row)
  }
  t.after(() => { delete globalThis[symbol] })
  const raw = await readFile(new URL('../src/services/workspaceService.js', import.meta.url), 'utf8')
  const source = raw.replace("import { reactive } from 'vue'", 'const reactive = value => value')
    .replace("import { modelApi, modelBackendEnabled } from './modelBackendService'", `const modelApi = globalThis.${symbol}; const modelBackendEnabled = () => true`)
  const api = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
  return { ...api, server, storage }
}

test('legacy business drafts migrate once; browser preferences stay local', async t => {
  const f = await fixture(t, { local: { [key]: '{"note":"率定方案"}', theme: 'light' } })
  await f.initializeBusinessDrafts()
  assert.deepEqual(f.server.drafts[key], { value: { note: '率定方案' }, revision: 1 })
  assert.equal(f.storage.theme, 'light')
  const history = Object.keys(f.storage).filter(k => k.startsWith('xaj_draft_history:'))
  await f.initializeBusinessDrafts()
  assert.equal(f.server.posts.length, 1)
  assert.deepEqual(Object.keys(f.storage).filter(k => k.startsWith('xaj_draft_history:')), history)
})

test('fresh browser restores shared SQL drafts', async t => {
  const f = await fixture(t, { drafts: { [key]: { value: { note: '已保存' }, revision: 7 } } })
  await f.initializeBusinessDrafts()
  assert.equal(JSON.parse(f.storage[key]).note, '已保存')
  f.saveBusinessDraft(key, '{"note":"继续修改"}')
  await f.flushBusinessDrafts()
  assert.equal(f.server.drafts[key].revision, 8)
  assert.equal(f.workspaceSync.pending, 0)
})

test('workspace switching isolates business drafts and keeps local history', async t => {
  const f = await fixture(t, { local: { xaj_browser_workspace: 'WS-old', [key]: '{"note":"旧流域"}' }, id: 'WS-new' })
  await f.initializeBusinessDrafts()
  assert.equal(f.storage[key], undefined)
  assert.equal(f.server.posts.length, 0)
  assert.ok(Object.keys(f.storage).some(k => k.startsWith('xaj_draft_history:WS-old:')))
})

test('conflicting pending revisions survive edits and reload without overwriting shared draft', async t => {
  const f = await fixture(t, { local: {
    xaj_browser_workspace: 'WS-first',
    'xaj_pending_drafts:WS-first': JSON.stringify({ [key]: { value: { note: '本机' }, revision: 2 } }),
  }, drafts: { [key]: { value: { note: '其他页面' }, revision: 3 } } })
  await f.initializeBusinessDrafts()
  f.saveBusinessDraft(key, '{"note":"本机继续修改"}')
  await f.flushBusinessDrafts()
  assert.equal(f.server.posts.at(-1).revision, 2)
  assert.equal(f.server.drafts[key].value.note, '其他页面')
  assert.equal(f.workspaceSync.pending, 1)
  await f.initializeBusinessDrafts()
  assert.equal(JSON.parse(f.storage[key]).note, '本机继续修改')
  await f.reloadWorkspaceDrafts()
  assert.equal(JSON.parse(f.storage[key]).note, '其他页面')
  assert.equal(f.workspaceSync.pending, 0)
  assert.match(JSON.stringify(f.exportBusinessDrafts()), /本机继续修改/)
})

test('an edit during an in-flight save uses the acknowledged revision for its next save', async t => {
  const f = await fixture(t)
  await f.initializeBusinessDrafts()
  f.saveBusinessDraft(key, '{"note":"first"}')
  f.server.beforePost = async () => { f.server.beforePost = null; f.saveBusinessDraft(key, '{"note":"second"}') }
  await f.flushBusinessDrafts()
  assert.equal(f.workspaceSync.pending, 1)
  await f.flushBusinessDrafts()
  assert.equal(f.server.drafts[key].value.note, 'second')
  assert.equal(f.server.drafts[key].revision, 2)
})

test('failed saves keep local pending payload; reconnect persists it', async t => {
  const f = await fixture(t)
  await f.initializeBusinessDrafts()
  f.server.failure = true
  f.saveBusinessDraft(eventKey, '{"event":{"note":"洪峰复核"}}')
  await f.flushBusinessDrafts()
  assert.equal(f.workspaceSync.pending, 1)
  assert.match(f.workspaceSync.error, /连接失败/)
  f.server.failure = false
  await f.initializeBusinessDrafts()
  assert.equal(f.server.drafts[eventKey].value.event.note, '洪峰复核')
  assert.equal(f.workspaceSync.pending, 0)
})
