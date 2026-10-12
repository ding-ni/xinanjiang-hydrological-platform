<template>
  <section class="input-products" aria-label="输入资料产品管理">
    <div class="product-heading">
      <div><h3>降雨整编与计算资料</h3></div>
      <button type="button" class="btn" :disabled="loading || !supported" @click="refresh()">{{ loading ? '正在刷新' : '刷新产品与任务' }}</button>
    </div>
    <p v-if="!supported" class="notice">资料加工：南安溪 · 1 km。</p>
    <template v-else>
      <div class="current-input"><span>工作区当前选用</span><code>{{ app.selectedInputBundleVersionId }}</code><b>当前版本</b></div>
      <details class="version-note"><summary>资料版本记录</summary><p>新资料独立保存；历史任务保留提交时的资料版本。</p></details>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="message" class="success" role="status">{{ message }}</p>

      <div class="product-section">
        <h4>已登记输入版本 <small>{{ versions.length }} 个</small></h4>
        <div class="table-scroll">
          <table class="table product-table" aria-label="已登记输入版本">
            <thead><tr><th>版本 / 来源</th><th>实际覆盖</th><th>文件摘要</th><th>选用</th></tr></thead>
            <tbody>
              <tr v-for="version in versions" :key="version.bundleVersionId" :class="{ selected: version.bundleVersionId === app.selectedInputBundleVersionId }">
                <td><b>{{ hydrologyText(version.name) || version.bundleVersionId }}</b><code>{{ version.bundleVersionId }}</code><small>{{ hydrologyText(version.sourceLabel) || '已登记整编资料' }}</small></td>
                <td>{{ coverageText(version) }}<small>{{ version.summary?.gridCount ?? version.gridCount ?? (version.bundleVersionId === app.selectedInputBundleVersionId ? app.selectedInputCatalog?.gridCount : null) ?? '—' }} 网格 · {{ version.summary?.eventCount ?? version.eventCount ?? version.events?.length ?? (version.bundleVersionId === app.selectedInputBundleVersionId ? app.selectedInputCatalog?.events?.length : null) ?? '—' }} 个登记场次</small></td>
                <td><code :title="version.manifestSHA256">{{ version.manifestSHA256?.slice(0, 14) || '摘要待读取' }}</code><button type="button" class="text-action" @click="inspectedVersion = version">来源与整编记录</button></td>
                <td><button type="button" class="btn" :disabled="busy || !!app.backendError || version.bundleVersionId === app.selectedInputBundleVersionId" @click="selectVersion(version)">{{ version.bundleVersionId === app.selectedInputBundleVersionId ? '当前选用' : '选用此版本' }}</button></td>
              </tr>
              <tr v-if="!versions.length"><td colspan="4">{{ loading ? '正在查询资料版本…' : '暂无登记版本，请刷新或检查服务连接。' }}</td></tr>
            </tbody>
          </table>
        </div>
        <details v-if="inspectedVersion" open class="product-details"><summary>来源与整编记录：{{ inspectedVersion.bundleVersionId }}</summary><p>{{ hydrologyText(inspectedVersion.sourceLabel) }}</p><ul v-if="inspectedVersion.qualityNotes?.length"><li v-for="note in inspectedVersion.qualityNotes" :key="note">{{ hydrologyText(note) }}</li></ul><pre>{{ JSON.stringify(inspectedVersion, null, 2) }}</pre></details>
      </div>

      <details class="product-section source-registration">
        <summary>登记站点来源 JSON <small>1–32 站 · 最多 8784 小时 · 2 MB</small></summary>
        <p class="hint">上传有界 JSON，或粘贴真实资料。单位 mm、EPSG:4326、本地连续逐小时整点；缺测用 null。格式模板完全缺测、非实测，必须替换名称、站号、坐标与真实来源说明后才可登记。不支持本地路径、MDB 自动转换或任意 NC 上传。</p>
        <div class="source-actions"><label class="btn file-label">选择 JSON 文件<input aria-label="选择站点来源 JSON" type="file" accept=".json,application/json" @change="readSourceFile" /></label><button class="btn" type="button" @click="insertTemplate">填入非实测格式模板</button></div>
        <label class="editor-label" for="source-json-editor">来源 JSON</label>
        <textarea id="source-json-editor" v-model="sourceJson" class="input json-editor" spellcheck="false" placeholder="粘贴真实来源 JSON；缺测必须使用 null"></textarea>
        <button class="btn primary" type="button" :disabled="busy || !sourceJson.trim() || !!app.backendError" @click="registerSource">校验并登记来源</button>
      </details>

      <div class="product-section">
        <h4>降雨整编方案</h4>
        <form class="processing-form" @submit.prevent="submitProcessing">
          <div class="field"><label for="input-operation">加工方式</label><select id="input-operation" v-model="form.operation" class="input"><option value="qcWindow">提取网格降雨时段</option><option value="stationIdw">站点雨量检查与空间插值</option></select></div>
          <div class="field"><label for="input-product-name">新产品名称</label><input id="input-product-name" v-model="form.name" class="input" required maxlength="120" placeholder="独立版本名称，不覆盖原版本" /></div>
          <div v-if="form.operation === 'stationIdw'" class="field source-field"><label for="input-source">已登记站点雨量</label><select id="input-source" v-model="form.sourceId" class="input" required><option value="">请选择来源</option><option v-for="source in sources" :key="source.sourceId" :value="source.sourceId">{{ source.name }} · {{ source.stationCount }} 站 · {{ coverageText(source) }}</option></select></div>
          <div class="field"><label for="input-window-start">窗口开始（本地整点）</label><input id="input-window-start" v-model="form.start" class="input" type="datetime-local" step="3600" required /></div>
          <div class="field"><label for="input-window-end">窗口结束（含末整点）</label><input id="input-window-end" v-model="form.end" class="input" type="datetime-local" step="3600" required /></div>
          <template v-if="form.operation === 'stationIdw'">
            <div class="field"><label for="input-idw-power">距离权重幂指数</label><input id="input-idw-power" v-model.number="form.idwPower" class="input" type="number" min="1" max="4" step="0.1" required /></div>
            <div class="field"><label for="input-minimum-stations">最少有效站数</label><input id="input-minimum-stations" v-model.number="form.minimumStations" class="input" type="number" min="1" :max="selectedSource?.stationCount || 32" step="1" required /></div>
            <div class="field source-field"><label for="input-zero-policy">全零记录处理</label><select id="input-zero-policy" v-model="form.zeroOnlyPolicy" class="input"><option value="KEEP">保留原始零值</option><option value="ANNUAL_RECLASSIFY">全年无变化测站判缺（仅完整自然年）</option></select><small>短时段无雨保留为零；负雨量在整编时判缺，原值留存。</small></div>
          </template>
          <div class="submit-row"><span>基础资料版本：<code>{{ app.selectedInputBundleVersionId }}</code> · 整编时段最长 8784 小时</span><button class="btn primary" type="submit" :disabled="busy || !!app.backendError">{{ busy ? '正在提交' : '生成降雨整编产品' }}</button></div>
        </form>
      </div>

      <div class="product-section">
        <h4>资料整编任务 <small>生成 → 登记 → 选用</small></h4>
        <div class="table-scroll"><table class="table product-table" aria-label="资料加工任务">
          <thead><tr><th>任务 / 产品</th><th>状态 / 进度</th><th>冻结来源</th><th>操作</th></tr></thead>
          <tbody><tr v-for="task in tasks" :key="task.taskId"><td><b>{{ taskRequest(task).name || task.taskId }}</b><code>{{ task.taskId }}</code></td><td>{{ taskStatus(task) }} · {{ Math.round(Number(task.progress) || 0) }}%<small>{{ task.stageLabel || task.errorMessage }}</small></td><td>{{ taskRequest(task).operation === 'stationIdw' ? taskRequest(task).sourceId : taskRequest(task).parentBundleVersionId }}<small>{{ taskRequest(task).start }} → {{ taskRequest(task).end }}</small></td><td class="task-actions"><button type="button" class="btn" :disabled="busy" @click="inspectTask(task)">详情 / 日志</button><button v-if="active(task)" type="button" class="btn" :disabled="busy || task.status === 'cancelling'" @click="cancelTask(task)">取消</button><button v-if="task.status === 'completed'" type="button" class="btn" :disabled="busy || !!task.registeredBundleVersionId || !!app.backendError" @click="registerTask(task)">{{ task.registeredBundleVersionId ? '已登记' : '校验并登记结果' }}</button></td></tr><tr v-if="!tasks.length"><td colspan="4">暂无资料加工任务。已有模型任务在原任务中心保留。</td></tr></tbody>
        </table></div>
        <details v-if="inspectedTask" open class="product-details"><summary>加工任务：{{ inspectedTask.taskId }}</summary><p v-if="inspectedTask.errorMessage" class="error">{{ inspectedTask.errorMessage }}</p><h5>提交时冻结的请求</h5><pre>{{ JSON.stringify(taskRequest(inspectedTask), null, 2) }}</pre><h5 v-if="inspectedResult">加工结果（尚未登记时不可选用）</h5><pre v-if="inspectedResult">{{ JSON.stringify(inspectedResult, null, 2) }}</pre><h5>计算日志</h5><pre>{{ inspectedLog || '暂无日志' }}</pre></details>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import { hydrologyText } from '@/services/hydrologyPresentation'
import {
  INPUT_TASK_ACTIVE_STATUSES, buildInputProcessingRequest, cancelInputProcessingTask,
  getInputTaskLog, getInputTaskResult, listInputSources, listInputTasks, parseSourceJson,
  registerInputProduct, registerInputSource, sourceJsonTemplate, submitInputProcessingTask
} from '@/services/inputProductService'

const app = useAppStore()
const props = defineProps({ preparedSource: { type: Object, default: null } })
const supported = computed(() => app.usesModelBackend && app.basinId === 'nananxi-xufan' && app.modelScale === '1km')
const versions = computed(() => supported.value ? app.inputProducts : [])
const sources = ref([])
const tasks = ref([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const message = ref('')
const sourceJson = ref('')
const inspectedVersion = ref(null)
const inspectedTask = ref(null)
const inspectedResult = ref(null)
const inspectedLog = ref('')
const form = reactive({ operation: 'qcWindow', name: '', start: '', end: '', sourceId: '', idwPower: 2, minimumStations: 1, zeroOnlyPolicy: 'KEEP' })
watch(() => props.preparedSource, source => {
  if (source?.sourceId) Object.assign(form, { operation: 'stationIdw', sourceId: source.sourceId,
    name: `${source.name} · 网格降雨`, start: source.start, end: source.end, minimumStations: Math.min(3, source.stationCount) })
}, { immediate: true })
const selectedSource = computed(() => sources.value.find((row) => row.sourceId === form.sourceId))
let requestRevision = 0
let inspectRevision = 0
let pollTimer = null
let disposed = false
const active = (task) => INPUT_TASK_ACTIVE_STATUSES.includes(task.status)
const taskRequest = (task) => task?.request || task?.runConfig || {}
const taskStatus = (task) => ({ queued: '排队', running: '加工中', cancelling: '正在取消', cancelled: '已取消', failed: '失败', completed: task.registeredBundleVersionId ? '已登记' : '加工完成 · 待登记' }[task.status] || task.status)
function coverageText(record) {
  const range = record.coverage?.common || record.coverage?.precipitation || record.coverage || record
  if (range.start && range.end) return `${range.start.replace('T', ' ')} → ${range.end.replace('T', ' ')}`
  if (record.bundleVersionId === app.selectedInputBundleVersionId && app.selectedInputCatalog?.coverage?.common) return coverageText({ coverage: app.selectedInputCatalog.coverage.common })
  if (Array.isArray(range.years) && range.years.length) return `${Math.min(...range.years)}–${Math.max(...range.years)} 年（整点范围见输入目录）`
  return '覆盖待读取'
}
function schedulePoll() {
  clearTimeout(pollTimer)
  if (!disposed && supported.value && tasks.value.some(active)) pollTimer = setTimeout(() => refresh({ quiet: true }), 2500)
}
async function refresh({ quiet = false } = {}) {
  const revision = ++requestRevision
  const key = app.contextKey
  if (!supported.value) return
  if (!quiet) { loading.value = true; error.value = '' }
  try {
    const [, sourceList, taskList] = await Promise.all([app.refreshInputProducts(), listInputSources(), listInputTasks()])
    if (disposed || revision !== requestRevision || key !== app.contextKey) return
    sources.value = sourceList.sources || []
    tasks.value = (taskList.tasks || []).sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
    if (!form.start || !form.end) {
      const range = app.selectedInputCatalog?.coverage?.common || app.selectedInputCatalog?.latestPeriod
      if (range?.start && range?.end) {
        const end = new Date(range.end)
        const start = new Date(Math.max(new Date(range.start).getTime(), end.getTime() - 23 * 3600000))
        const local = (value) => new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
        form.start = local(start); form.end = local(end)
      }
    }
  } catch (failure) {
    if (revision === requestRevision && key === app.contextKey && !disposed) error.value = failure.message
  } finally {
    if (revision === requestRevision && !disposed) { loading.value = false; schedulePoll() }
  }
}
async function mutate(action, successText) {
  if (busy.value) return
  const key = app.contextKey
  busy.value = true; error.value = ''; message.value = ''
  try {
    const result = await action()
    if (disposed || key !== app.contextKey) return
    message.value = typeof successText === 'function' ? successText(result) : successText
    await refresh({ quiet: true })
    return result
  } catch (failure) {
    if (!disposed && key === app.contextKey) error.value = failure.message
  } finally { if (!disposed && key === app.contextKey) busy.value = false }
}
function insertTemplate() { sourceJson.value = JSON.stringify(sourceJsonTemplate(), null, 2); message.value = '已填入格式模板，所有雨量为 null，非实测。请使用真实资料替换。' }
async function readSourceFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error('来源 JSON 最大为 2 MB。')
    sourceJson.value = await file.text()
    error.value = ''
  } catch (failure) { error.value = failure.message }
  event.target.value = ''
}
function registerSource() { return mutate(async () => {
  const source = await registerInputSource(parseSourceJson(sourceJson.value))
  if (supported.value) form.sourceId = source.sourceId
  return source
}, '站点来源已登记。生成并核对网格降雨后，再登记整编产品。') }
function submitProcessing() { return mutate(() => submitInputProcessingTask(buildInputProcessingRequest(form, app)), (task) => `资料任务 ${task.taskId} 已提交，完成后需显式登记。`) }
function selectVersion(version) { return mutate(() => app.selectInputBundleVersion(version.bundleVersionId), `已选用 ${version.bundleVersionId}。回放与场次检查将按新资料重新读取，历史成果保留原资料版本。`) }
function cancelTask(task) { return mutate(() => cancelInputProcessingTask(task.taskId), `已发送取消请求：${task.taskId}。取消/失败产物不会登记。`) }
function registerTask(task) { return mutate(() => registerInputProduct(task.taskId), (version) => `已登记 ${version.bundleVersionId}。`) }
async function inspectTask(task) {
  const revision = ++inspectRevision
  const key = app.contextKey
  inspectedTask.value = task; inspectedResult.value = null; inspectedLog.value = ''
  try {
    const [log, result] = await Promise.all([getInputTaskLog(task.taskId), task.status === 'completed' ? getInputTaskResult(task.taskId) : Promise.resolve(null)])
    if (disposed || revision !== inspectRevision || key !== app.contextKey) return
    inspectedLog.value = log.tail || log.log || ''; inspectedResult.value = result
  } catch (failure) { if (!disposed && revision === inspectRevision && key === app.contextKey) error.value = failure.message }
}
watch(() => app.contextKey, () => {
  requestRevision += 1; inspectRevision += 1; clearTimeout(pollTimer)
  sources.value = []; tasks.value = []; inspectedVersion.value = null; inspectedTask.value = null; inspectedResult.value = null; inspectedLog.value = ''
  busy.value = false; error.value = ''; message.value = ''
  form.sourceId = ''; form.start = ''; form.end = ''
  if (supported.value) refresh()
})
onMounted(refresh)
onBeforeUnmount(() => { disposed = true; requestRevision += 1; inspectRevision += 1; clearTimeout(pollTimer) })
</script>

<style scoped>
.input-products { margin: 18px 0 26px; border-top: 2px solid #193754; color: #243c56; }
.product-heading { display: flex; justify-content: space-between; align-items: center; gap: 14px; margin: 15px 0 10px; }
.product-heading h3 { margin: 0; font-size: 15px; font-weight: 800; }
.product-heading p { font-size: 12px; margin: 7px 0; color: #65778c; }
.current-input { display: flex; align-items: center; gap: 14px; padding: 12px; background: #eef5fc; border-left: 3px solid #245d89; flex-wrap: wrap; font-size: 12px; }
.current-input code { color: #173e63; }
.current-input b { margin-left: auto; color: #9b6a1d; font-size: 11px; }
.notice, .hint { font-size: 12px; line-height: 1.75; color: #687b90; }
.error, .success { padding: 9px 12px; font-size: 12px; line-height: 1.7; border-left: 3px solid; }
.error { color: #9f3835; background: #fff4f2; }.success { color: #216451; background: #edf8f3; }
.product-section { margin-top: 18px; padding-top: 14px; border-top: 1px solid #dbe4ee; }
h4 { font-size: 13px; margin: 0 0 10px; } small { display: block; font-weight: normal; color: #78899a; font-size: 11px; line-height: 1.65; }
h4 small, summary small { display: inline; margin-left: 10px; }
.table-scroll { overflow: auto; max-height: 380px; }
.product-table { min-width: 800px; width: 100%; font-size: 12px; }
.product-table td { vertical-align: top; }.product-table b, .product-table code { display: block; }
.product-table td:first-child { white-space: normal; max-width: 310px; }.product-table tr.selected td { background: #f0f7fc; }
.product-table .btn { white-space: nowrap; font-size: 11px; padding: 5px 9px; }
code, pre, .json-editor { font-family: Consolas, 'SFMono-Regular', monospace; font-size: 11px; }
.text-action { border: 0; background: none; padding: 4px 0; color: #225f97; cursor: pointer; font-size: 11px; }
.source-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.file-label input { display: none; }.editor-label { display: block; font-size: 12px; margin-bottom: 6px; }
.json-editor { width: 100%; min-height: 190px; resize: vertical; line-height: 1.6; margin-bottom: 10px; }
.processing-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.field label { display: block; margin-bottom: 5px; font-size: 12px; }.source-field, .submit-row { grid-column: 1 / -1; }
.submit-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 11px; color: #6c8095; flex-wrap: wrap; }
.task-actions { display: flex; gap: 6px; flex-wrap: wrap; min-width: 170px; }
.product-details { background: #f7faff; border: 1px solid #d6e2ed; padding: 12px; margin-top: 10px; }
summary { font-weight: 700; cursor: pointer; font-size: 13px; }pre { white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow: auto; color: #37546e; line-height: 1.65; }
h5 { margin: 12px 0 5px; font-size: 12px; }.product-details p, .product-details li { font-size: 12px; line-height: 1.65; }
@media (max-width: 850px) { .processing-form { grid-template-columns: 1fr; }.product-heading { align-items: flex-start; }.current-input b { margin-left: 0; } }
</style>
