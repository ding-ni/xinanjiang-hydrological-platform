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

it('calibration workbench keeps a full process chart and reduces to one column on smaller screens', () => {
  const source = readFileSync(new URL('../src/views/CalibrationView.vue', import.meta.url), 'utf8')
  assert.match(source, /\.process-panel\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column/)
  const chartMinimum = Number(source.match(/\.process-panel :deep\(\.hydrology-figure\)\{[^}]*min-height:\s*(\d+)px/)?.[1])
  assert.ok(chartMinimum >= 420, 'The plotting host has a minimum independent of controls')
  assert.match(source, /@media\(max-width:1100px\)\{\s*\.workbench,\.workbench\.manual-mode\{grid-template-columns:1fr/s)
  assert.match(source, /latestComparableTrial/)
  const dataView = readFileSync(new URL('../src/views/DataView.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(dataView, /id="scheme-task"/)
  const archive = readFileSync(new URL('../src/views/ResultWorkspace.vue', import.meta.url), 'utf8')
  assert.match(archive, /本次计算设置/)
  assert.match(readFileSync(new URL('../src/views/LegacyArchiveView.vue', import.meta.url), 'utf8'), /打开条件草稿/)
  const realtime = readFileSync(new URL('../src/views/RealtimeView.vue', import.meta.url), 'utf8')
  assert.match(realtime, /<label for="monitor-station">测站过程<\/label>/)
  assert.match(realtime, /<select id="monitor-station"/)
})

it('issue exclusion enters edit mode instead of disabling the action on a saved scheme', () => {
  const view = readFileSync(new URL('../src/views/CalibrationView.vue', import.meta.url), 'utf8')
  assert.match(view, /:disabled="cal\.running \|\| legacyEventScheme \|\| !preparationIssues\.some\(issue => issue\.eventId\)" @click="excludeIssues"/)
  assert.match(view, /if \(!editing\.value\) schemeBar\.value\?\.beginEdit\(\)[\s\S]*?cal\.setEventPurposes\(assignments\)/)
  const schemeBar = readFileSync(new URL('../src/components/model/ModelSchemeBar.vue', import.meta.url), 'utf8')
  assert.match(schemeBar, /defineExpose\(\{[^}]*beginEdit/s)
})
