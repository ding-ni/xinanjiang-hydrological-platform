import { it } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

it('unified build explicitly enables the same-origin model backend', () => {
  const env = readFileSync(new URL('../.env.unified', import.meta.url), 'utf8')
  assert.match(env, /^VITE_ENABLE_MODEL_BACKEND=true$/m)
  assert.match(env, /^VITE_MODEL_API_BASE=\/api\/model$/m)
  const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
  assert.equal(pkg.scripts['build:unified'], 'vite build --mode unified --outDir dist-unified')
  const startup = readFileSync(new URL('../../../scripts/start_unified.ps1', import.meta.url), 'utf8')
  assert.match(startup, /npm\.cmd run build:unified/)
  assert.match(startup, /settings\.frontend/)
})
