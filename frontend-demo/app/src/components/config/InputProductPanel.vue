<template>
  <section class="input-products" aria-label="降雨整编与计算资料">
    <div class="product-heading">
      <div><h3>降雨整编与计算资料</h3></div>
      <button type="button" class="btn" :disabled="loading || !supported" @click="refresh()">{{ loading ? '正在刷新' : '刷新资料与记录' }}</button>
    </div>
    <p v-if="!supported" class="notice">降雨整编适用于南安溪 · 1 km。</p>
    <template v-else>
      <div class="current-input">
        <div><span>当前计算资料</span><strong>{{ currentPresentation.name }}</strong><small>{{ currentPresentation.purpose }}</small></div>
        <b>当前使用</b>
      </div>
      <details class="version-note"><summary>资料编号与来源说明</summary><code>{{ app.selectedInputBundleVersionId }}</code><p>测站实测记录保存在本地 SQLite 水文资料库；这里选用的计算资料由网格降雨 NC、徐畈流量 CSV 和洪水场次等组成。新资料独立保存，已有成果保留计算时的资料版本。</p></details>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="message" class="success" role="status">{{ message }}</p>

      <div class="product-section">
        <h4>已登记计算资料 <small>{{ versions.length }} 个版本</small></h4>
        <div class="table-scroll">
          <table class="table product-table" aria-label="已登记计算资料">
            <thead><tr><th>资料名称</th><th>用途</th><th>提取时段</th><th>状态与操作</th></tr></thead>
            <tbody>
              <tr v-for="row in versionRows" :key="row.record.bundleVersionId" :data-input-version="row.record.bundleVersionId" :class="{ selected: row.state.selected }">
                <td><b>{{ row.presentation.name }}</b><small>{{ gridAndEventText(row.record) }}</small></td>
                <td class="purpose-cell">{{ row.presentation.purpose }}</td>
                <td>{{ row.presentation.range }}<small>{{ row.presentation.rangeNote }}</small></td>
                <td><span class="selection-status">{{ row.state.selected ? '当前使用' : '已登记 · 未选用' }}</span><div class="task-actions"><button type="button" class="btn" @click="inspectedVersion = row.record">查看来源</button><button type="button" class="btn" :disabled="busy || !!app.backendError || row.state.selected" @click="selectVersion(row.record)">{{ row.state.selected ? '当前使用' : '选用此资料' }}</button></div></td>
              </tr>
              <tr v-if="!versions.length"><td colspan="4">{{ loading ? '正在查询资料版本…' : '暂无登记版本，请刷新或检查服务连接。' }}</td></tr>
            </tbody>
          </table>
        </div>
        <details v-if="inspectedVersion" open class="product-details">
          <summary>正在查看：{{ inspectedPresentation.name }}</summary>
          <p class="view-state">{{ inspectedState.selected ? '本资料正在使用。' : '本资料尚未选用；查看来源不会切换当前计算资料。' }}</p>
          <p>登记名称：{{ inspectedVersion.name }}</p><p>{{ hydrologyText(inspectedVersion.sourceLabel) }}</p>
          <p>提取时段：{{ inspectedPresentation.range }}</p><p>{{ inspectedPresentation.rangeNote }}</p>
          <ul v-if="inspectedVersion.qualityNotes?.length"><li v-for="note in inspectedVersion.qualityNotes" :key="note">{{ hydrologyText(note) }}</li></ul>
          <details class="technical-details"><summary>版本编号与完整登记信息</summary><code>{{ inspectedVersion.bundleVersionId }}</code><pre>{{ JSON.stringify(inspectedVersion, null, 2) }}</pre></details>
        </details>
      </div>

      <details class="product-section optional-processing" :open="processingOpen" @toggle="processingOpen = $event.target.open">
        <summary>重新整编降雨（可选）</summary>
        <p class="hint">已有计算资料可直接选用。需要选取另一时段或重新进行站点空间插值时，再填写下面的条件。</p>
        <slot name="station-source" />
        <details class="source-registration">
        <summary>高级：登记站点雨量 JSON <small>1–32 站 · 最多 8784 小时 · 2 MB</small></summary>
        <p class="hint">上传有界 JSON，或粘贴真实资料。单位 mm、EPSG:4326、本地连续逐小时整点；缺测用 null。格式模板完全缺测、非实测，必须替换名称、站号、坐标与真实来源说明后才可登记。不支持本地路径、MDB 自动转换或任意 NC 上传。</p>
        <div class="source-actions"><label class="btn file-label">选择 JSON 文件<input aria-label="选择站点来源 JSON" type="file" accept=".json,application/json" @change="readSourceFile" /></label><button class="btn" type="button" @click="insertTemplate">填入非实测格式模板</button></div>
        <label class="editor-label" for="source-json-editor">来源 JSON</label>
        <textarea id="source-json-editor" v-model="sourceJson" class="input json-editor" spellcheck="false" placeholder="粘贴真实来源 JSON；缺测必须使用 null"></textarea>
        <button class="btn primary" type="button" :disabled="busy || !sourceJson.trim() || !!app.backendError" @click="registerSource">校验并登记站点雨量</button>
      </details>

      <div class="processing-conditions">
        <h4>整编条件</h4>
        <form class="processing-form" @submit.prevent="submitProcessing">
          <div class="field"><label for="input-operation">整编方式</label><select id="input-operation" v-model="form.operation" class="input"><option value="qcWindow">提取网格降雨时段</option><option value="stationIdw">站点雨量检查与空间插值</option></select></div>
          <div class="field"><label for="input-product-name">新资料版本名称</label><input id="input-product-name" v-model="form.name" class="input" required maxlength="120" placeholder="填写本次资料的用途或时段" /></div>
          <div v-if="form.operation === 'stationIdw'" class="field source-field"><label for="input-source">已登记站点雨量</label><select id="input-source" v-model="form.sourceId" class="input" required><option value="">请选择来源</option><option v-for="source in sources" :key="source.sourceId" :value="source.sourceId">{{ source.name }} · {{ source.stationCount }} 站 · {{ coverageText(source) }}</option></select></div>
          <div class="field"><label for="input-window-start">开始时刻（本地整点）</label><input id="input-window-start" v-model="form.start" class="input" type="datetime-local" step="3600" required /></div>
          <div class="field"><label for="input-window-end">结束时刻（含末整点）</label><input id="input-window-end" v-model="form.end" class="input" type="datetime-local" step="3600" required /></div>
          <template v-if="form.operation === 'stationIdw'">
            <div class="field"><label for="input-idw-power">距离权重幂指数</label><input id="input-idw-power" v-model.number="form.idwPower" class="input" type="number" min="1" max="4" step="0.1" required /></div>
            <div class="field"><label for="input-minimum-stations">最少有效站数</label><input id="input-minimum-stations" v-model.number="form.minimumStations" class="input" type="number" min="1" :max="selectedSource?.stationCount || 32" step="1" required /></div>
            <div class="field source-field"><label for="input-zero-policy">全零记录处理</label><select id="input-zero-policy" v-model="form.zeroOnlyPolicy" class="input"><option value="KEEP">保留原始零值</option><option value="ANNUAL_RECLASSIFY">全年无变化测站判缺（仅完整自然年）</option></select><small>短时段无雨保留为零；负雨量在整编时判缺，原值留存。</small></div>
          </template>
          <div class="submit-row"><span>空间及场次基础：{{ currentPresentation.name }} · 单次整编最长 8784 小时</span><button class="btn primary" type="submit" :disabled="busy || !!app.backendError">{{ busy ? '正在提交' : '生成降雨资料版本' }}</button></div>
        </form>
      </div>
      </details>

      <p v-if="activeTasks.length" class="processing-progress" role="status">正在整编：{{ activeTasks.map(task => `${taskRequest(task).name || task.taskId} ${Math.round(Number(task.progress) || 0)}%`).join('；') }}</p>
      <details class="product-section" :open="historyOpen" @toggle="historyOpen = $event.target.open">
        <summary>降雨整编记录 <small>{{ tasks.length }} 项 · 生成 → 核对登记 → 选用</small></summary>
        <p class="hint">这里记录已有资料如何形成。完成的资料无需重新整编；只有显式选用后才用于后续计算。</p>
        <div class="table-scroll"><table class="table product-table" aria-label="降雨整编记录">
          <thead><tr><th>记录 / 资料</th><th>状态 / 进度</th><th>来源与时段</th><th>操作</th></tr></thead>
          <tbody><tr v-for="task in tasks" :key="task.taskId"><td><b>{{ taskName(task) }}</b></td><td>{{ taskStatus(task) }} · {{ Math.round(Number(task.progress) || 0) }}%<small>{{ task.stageLabel || task.errorMessage }}</small></td><td>{{ taskSourceText(task) }}<small>{{ coverageText(taskRequest(task)) }}</small></td><td class="task-actions"><button type="button" class="btn" :disabled="busy" @click="inspectTask(task)">详情 / 日志</button><button v-if="active(task)" type="button" class="btn" :disabled="busy || task.status === 'cancelling'" @click="cancelTask(task)">取消</button><button v-if="task.status === 'completed'" type="button" class="btn" :disabled="busy || !!task.registeredBundleVersionId || !!app.backendError" @click="registerTask(task)">{{ task.registeredBundleVersionId ? '已登记' : '校验并登记结果' }}</button></td></tr><tr v-if="!tasks.length"><td colspan="4">暂无降雨整编记录。模型计算记录见成果管理。</td></tr></tbody>
        </table></div>
        <details v-if="inspectedTask" open class="product-details"><summary>整编记录：{{ taskName(inspectedTask) }}</summary><code>{{ inspectedTask.taskId }}</code><p v-if="inspectedTask.errorMessage" class="error">{{ inspectedTask.errorMessage }}</p><h5>提交时冻结的请求</h5><pre>{{ JSON.stringify(taskRequest(inspectedTask), null, 2) }}</pre><h5 v-if="inspectedResult">整编结果（尚未登记时不可选用）</h5><pre v-if="inspectedResult">{{ JSON.stringify(inspectedResult, null, 2) }}</pre><h5>整编日志</h5><pre>{{ inspectedLog || '暂无日志' }}</pre></details>
      </details>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import { hydrologyText } from '@/services/hydrologyPresentation'
import { inputProductPresentation, inputVersionViewState } from '@/services/inputProductPresentation'
import { latestInputProcessingWindow, mergeInputSourceChoices } from '@/services/inputSourceChoices'
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
const registeredSources = ref([])
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
const processingOpen = ref(false)
const historyOpen = ref(false)
const presentationOptions = computed(() => ({ catalog: app.selectedInputCatalog, parameterVersions: app.projectData?.backendParameterVersions ?? null }))
const present = (record) => inputProductPresentation(record, presentationOptions.value)
const versionRows = computed(() => versions.value.map(record => ({ record, presentation: present(record), state: inputVersionViewState(record, app.selectedInputBundleVersionId) })))
const currentPresentation = computed(() => present(app.selectedInputProduct || { bundleVersionId: app.selectedInputBundleVersionId }))
const inspectedPresentation = computed(() => present(inspectedVersion.value || {}))
const inspectedState = computed(() => inputVersionViewState(inspectedVersion.value, app.selectedInputBundleVersionId))
const form = reactive({ operation: 'qcWindow', name: '', start: '', end: '', sourceId: '', idwPower: 2, minimumStations: 1, zeroOnlyPolicy: 'KEEP' })
watch(() => props.preparedSource, source => {
  if (source?.sourceId && supported.value) {
    registeredSources.value = mergeInputSourceChoices(registeredSources.value, [source])
    sources.value = mergeInputSourceChoices(sources.value, registeredSources.value)
    Object.assign(form, { operation: 'stationIdw', sourceId: source.sourceId,
      name: `${source.name} · 网格降雨`, start: source.start, end: source.end, minimumStations: Math.min(3, source.stationCount) })
    processingOpen.value = true
  }
}, { immediate: true })
const selectedSource = computed(() => sources.value.find((row) => row.sourceId === form.sourceId))
let requestRevision = 0
let inspectRevision = 0
let pollTimer = null
let disposed = false
const active = (task) => INPUT_TASK_ACTIVE_STATUSES.includes(task.status)
const taskRequest = (task) => task?.request || task?.runConfig || {}
const activeTasks = computed(() => tasks.value.filter(active))
const taskStatus = (task) => ({ queued: '排队', running: '整编中', cancelling: '正在取消', cancelled: '已取消', failed: '失败', completed: task.registeredBundleVersionId ? '已登记' : '整编完成 · 待登记' }[task.status] || task.status)
const coverageText = (record) => present(record).range
function gridAndEventText(version) {
  const catalog = version.bundleVersionId === app.selectedInputBundleVersionId ? app.selectedInputCatalog : null
  return `${version.summary?.gridCount ?? version.gridCount ?? catalog?.gridCount ?? '—'} 网格 · ${version.summary?.eventCount ?? version.eventCount ?? version.events?.length ?? catalog?.events?.length ?? '—'} 个登记场次`
}
function taskName(task) {
  const version = versions.value.find(row => row.bundleVersionId === task.registeredBundleVersionId)
  return version ? present(version).name : hydrologyText(taskRequest(task).name) || '降雨整编'
}
function taskSourceText(task) {
  const request = taskRequest(task)
  if (request.sourceId) return sources.value.find(row => row.sourceId === request.sourceId)?.name || (request.operation === 'mdbImport' ? '原MDB雨量、徐畈流量及场次' : '已登记站点雨量')
  const parent = versions.value.find(row => row.bundleVersionId === request.parentBundleVersionId)
  return parent ? present(parent).name : '提交时选定的计算资料'
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
    sources.value = mergeInputSourceChoices(sourceList.sources || [], registeredSources.value)
    tasks.value = (taskList.tasks || []).sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
    if (!form.start || !form.end) {
      const range = currentPresentation.value.rangeKind === 'extracted' ? currentPresentation.value
        : app.selectedInputCatalog?.latestPeriod || app.selectedInputCatalog?.coverage?.common
      const window = latestInputProcessingWindow(range)
      if (window) Object.assign(form, window)
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
function registerSource() { const key = app.contextKey; return mutate(async () => {
  const source = await registerInputSource(parseSourceJson(sourceJson.value))
  if (!disposed && key === app.contextKey && supported.value) {
    registeredSources.value = mergeInputSourceChoices(registeredSources.value, [source])
    sources.value = mergeInputSourceChoices(sources.value, registeredSources.value)
    form.sourceId = source.sourceId
  }
  return source
}, '站点雨量已登记。生成并核对网格降雨后，再登记计算资料。') }
function submitProcessing() { return mutate(async () => {
  const task = await submitInputProcessingTask(buildInputProcessingRequest(form, app))
  historyOpen.value = true
  return task
}, '降雨整编已提交。完成后核对并登记结果，再选用新资料。') }
function selectVersion(version) { return mutate(() => app.selectInputBundleVersion(version.bundleVersionId), `已选用“${present(version).name}”。回放与场次检查将重新读取；已有成果保留计算时的资料。`) }
function cancelTask(task) { return mutate(() => cancelInputProcessingTask(task.taskId), `已发送取消请求：${task.taskId}。取消/失败产物不会登记。`) }
function registerTask(task) { return mutate(() => registerInputProduct(task.taskId), '新资料已登记，核对后可在资料表中选用。') }
async function inspectTask(task) {
  const revision = ++inspectRevision
  const key = app.contextKey
  inspectedTask.value = task; inspectedResult.value = null; inspectedLog.value = ''
  historyOpen.value = true
  try {
    const [log, result] = await Promise.all([getInputTaskLog(task.taskId), task.status === 'completed' ? getInputTaskResult(task.taskId) : Promise.resolve(null)])
    if (disposed || revision !== inspectRevision || key !== app.contextKey) return
    inspectedLog.value = log.tail || log.log || ''; inspectedResult.value = result
  } catch (failure) { if (!disposed && revision === inspectRevision && key === app.contextKey) error.value = failure.message }
}
watch(() => app.contextKey, () => {
  requestRevision += 1; inspectRevision += 1; clearTimeout(pollTimer)
  sources.value = []; registeredSources.value = []; tasks.value = []; inspectedVersion.value = null; inspectedTask.value = null; inspectedResult.value = null; inspectedLog.value = ''
  busy.value = false; error.value = ''; message.value = ''
  processingOpen.value = false; historyOpen.value = false
  Object.assign(form, { operation: 'qcWindow', name: '', sourceId: '', start: '', end: '' })
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
.current-input { display: flex; align-items: center; gap: 14px; padding: 14px; background: #eef5fc; border-left: 3px solid #245d89; flex-wrap: wrap; font-size: 12px; }
.current-input strong { display: block; margin: 5px 0; font-size: 14px; color: #173e63; }
.current-input b { margin-left: auto; color: #276b55; font-size: 11px; }
.version-note { margin: 10px 0 14px; }.version-note summary { font-size: 12px; font-weight: 500; color: #57748f; }
.version-note code { display: block; margin-top: 9px; overflow-wrap: anywhere; }
.version-note p { font-size: 12px; line-height: 1.75; margin: 8px 0; color: #687b90; }
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
.product-table .purpose-cell { max-width: 230px; white-space: normal; line-height: 1.7; }
.selection-status { display: block; font-size: 11px; color: #5f788f; margin-bottom: 7px; }
.product-table .btn { white-space: nowrap; font-size: 11px; padding: 5px 9px; }
code, pre, .json-editor { font-family: Consolas, 'SFMono-Regular', monospace; font-size: 11px; }
.text-action { border: 0; background: none; padding: 4px 0; color: #225f97; cursor: pointer; font-size: 11px; }
.source-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.file-label input { display: none; }.editor-label { display: block; font-size: 12px; margin-bottom: 6px; }
.json-editor { width: 100%; min-height: 190px; resize: vertical; line-height: 1.6; margin-bottom: 10px; }
.processing-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.source-registration, .processing-conditions { margin-top: 18px; }.optional-processing { padding-bottom: 4px; }
.processing-progress { margin: 16px 0 8px; padding: 9px 12px; background: #eef5fc; font-size: 12px; line-height: 1.7; color: #235f89; }
.technical-details { margin-top: 12px; }.technical-details summary { font-weight: 500; font-size: 12px; }.technical-details code { display: block; margin-top: 9px; overflow-wrap: anywhere; }
.product-details .view-state { color: #215f80; font-weight: 600; }
.field label { display: block; margin-bottom: 5px; font-size: 12px; }.source-field, .submit-row { grid-column: 1 / -1; }
.submit-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 11px; color: #6c8095; flex-wrap: wrap; }
.task-actions { display: flex; gap: 6px; flex-wrap: wrap; min-width: 170px; }
.product-details { background: #f7faff; border: 1px solid #d6e2ed; padding: 12px; margin-top: 10px; }
summary { font-weight: 700; cursor: pointer; font-size: 13px; }pre { white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow: auto; color: #37546e; line-height: 1.65; }
h5 { margin: 12px 0 5px; font-size: 12px; }.product-details p, .product-details li { font-size: 12px; line-height: 1.65; }
@media (max-width: 850px) { .processing-form { grid-template-columns: 1fr; }.product-heading { align-items: flex-start; }.current-input b { margin-left: 0; } }
</style>
