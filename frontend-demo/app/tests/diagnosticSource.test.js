import { it } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

it('diagnostic and archive Vue sources compile with their real template bindings', () => {
  for (const name of ['DiagnosticView', 'ArchiveView', 'CalibrationView', 'DataView', 'RealtimeView', 'RoutingWorkbench']) {
    const filename = name === 'RoutingWorkbench' ? 'src/components/model/RoutingWorkbench.vue' : `src/views/${name}.vue`
    const source = readFileSync(new URL(`../${filename}`, import.meta.url), 'utf8')
    const parsed = parse(source, { filename })
    assert.deepEqual(parsed.errors, [])
    const script = compileScript(parsed.descriptor, { id: name })
    const template = compileTemplate({ source: parsed.descriptor.template.content, filename, id: name, compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(template.errors, [])
  }
})

it('simulation chart retains a stable row and mobile simulation uses one column', () => {
  const source = readFileSync(new URL('../src/views/CalibrationView.vue', import.meta.url), 'utf8')
  assert.match(source, /\.process-panel\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column/)
  const chartMinimum = Number(source.match(/\.hydro\s*\{[^}]*min-height:\s*(\d+)px/)?.[1])
  assert.ok(chartMinimum >= 320, 'The process chart retains at least 320px of plotting space')
  assert.match(source, /\.process-panel\s*\{[^}]*height:\s*max-content/)
  assert.match(source, /\.calibration-layout\.simulation-mode,[^{]*\{\s*grid-template-columns:\s*1fr/s)
  assert.match(source, /simulationPeakLabel/)
  const dataView = readFileSync(new URL('../src/views/DataView.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(dataView, /id="scheme-task"/)
  const archive = readFileSync(new URL('../src/views/ArchiveView.vue', import.meta.url), 'utf8')
  assert.match(archive, /打开条件草稿/)
  const realtime = readFileSync(new URL('../src/views/RealtimeView.vue', import.meta.url), 'utf8')
  assert.match(realtime, /<label for="monitor-station">测站过程<\/label>/)
  assert.match(realtime, /<select id="monitor-station"/)
})
