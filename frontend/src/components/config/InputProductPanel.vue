<template>
  <section class="input-products" aria-label="模型计算资料">
    <div class="product-heading">
      <div><h3>模型计算资料</h3><p>先确认已有网格降雨与出口流量，再到率定与模拟选择洪水和用途。</p></div>
      <div class="task-actions"><button type="button" class="btn" :disabled="!supported" :aria-expanded="processingOpen" @click="processingOpen = !processingOpen">{{ processingOpen ? '收起资料整编' : '资料整编' }}</button><button type="button" class="btn" :disabled="loading || !supported" @click="refresh()">{{ loading ? '正在刷新' : '刷新资料' }}</button></div>
    </div>
    <p v-if="!supported" class="notice">当前流域和尺度尚未开放模型计算资料登记与整编。</p>
    <template v-else>
      <div class="current-input" data-current-input :data-input-version="app.selectedInputBundleVersionId">
        <div class="current-title"><span>当前使用</span><strong>{{ currentPresentation.name }}</strong><small>{{ currentPresentation.purpose }}</small></div>
        <button type="button" class="btn" @click="inspectVersion(currentRecord)">查看资料内容</button>
        <dl class="current-composition"><div v-for="row in currentContents.rows.slice(0, 3)" :key="row.key"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl>
        <p class="current-range">{{ currentPresentation.range }}<small>{{ currentPresentation.rangeNote }}</small></p>
        <p class="hint">这里保存计算用的网格雨量、出口实测流量及洪水起止目录。挑选率定场次只改变使用范围，无须重新生成资料；参数和起算状态另行保存。</p>
      </div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="message" class="success" role="status">{{ message }}</p>
      <p v-if="activeTasks.length" class="processing-progress" role="status">正在整编：{{ activeTasks.map(task => `${taskRequest(task).name || task.taskId} ${Math.round(Number(task.progress) || 0)}%`).join('；') }} <button type="button" class="text-action" @click="processingOpen = true; historyOpen = true">查看进度</button></p>

      <details class="product-section other-inputs">
        <summary>其他模型计算资料 <small>{{ otherVersionRows.length }} 个</small></summary>
        <p class="hint">名称来自工作区登记记录。查看资料不会改变当前选择；选用后用于后续作业，已有成果继续保留原资料版本。</p>
        <div class="table-scroll">
          <table class="table product-table" aria-label="其他模型计算资料">
            <thead><tr><th>资料名称</th><th>用途</th><th>提取时段</th><th>状态与操作</th></tr></thead>
            <tbody>
              <tr v-for="row in otherVersionRows" :key="row.record.bundleVersionId" :data-input-version="row.record.bundleVersionId">
                <td><b>{{ row.presentation.name }}</b><small>{{ gridAndEventText(row.record) }}</small></td>
                <td class="purpose-cell">{{ row.presentation.purpose }}</td>
                <td>{{ row.presentation.range }}<small>{{ row.presentation.rangeNote }}</small></td>
                <td><span class="selection-status">已登记 · 未选用</span><div class="task-actions"><button type="button" class="btn" @click="inspectVersion(row.record)">查看资料内容</button><button type="button" class="btn" :disabled="busy || !!app.backendError" @click="selectVersion(row.record)">选用此资料</button></div></td>
              </tr>
              <tr v-if="!otherVersionRows.length"><td colspan="4">{{ loading ? '正在查询资料…' : '当前工作区没有其他资料版本。' }}</td></tr>
            </tbody>
          </table>
        </div>
      </details>
      <details v-if="inspectedVersion" open class="product-details" data-input-detail :data-inspected-version="inspectedVersion.bundleVersionId">
          <summary>正在查看：{{ inspectedPresentation.name }}</summary>
          <div class="detail-heading"><p class="view-state">{{ inspectedState.selected ? '本资料正在使用。' : '仅查看资料内容，当前计算资料未改变。' }}</p><button type="button" class="btn" @click="closeVersionDetails">关闭详情</button><button v-if="!inspectedState.selected" type="button" class="btn" :disabled="busy || !!app.backendError" @click="selectVersion(inspectedVersion)">选用此资料</button></div>
          <p v-if="catalogLoading" role="status">正在读取本资料的时段、场次及组成…</p>
          <p v-if="catalogError" class="error" role="alert">{{ catalogError }}</p>
          <div class="table-scroll"><table class="table contents-table" aria-label="资料组成"><thead><tr><th>保存内容</th><th>本版资料</th><th>用途与含义</th></tr></thead><tbody><tr v-for="row in inspectedContents.rows" :key="row.key" :data-content-key="row.key"><th scope="row">{{ row.label }}</th><td>{{ row.value }}</td><td>{{ row.note }}</td></tr></tbody></table></div>
          <p>提取时段：{{ inspectedPresentation.range }}<small>{{ inspectedPresentation.rangeNote }}</small></p>
          <details v-if="inspectedCatalog" class="detail-group"><summary>洪水窗口清单 <small>{{ inspectedEvents.length }} 个登记窗口</small></summary><p class="hint">这里只登记编号和起止时段。窗口是否通过资料检查，与是否选为率定、验证或独立检验是两件事。</p><div class="table-scroll"><table class="table product-table" aria-label="本资料洪水窗口"><thead><tr><th>洪水编号</th><th>开始</th><th>结束</th><th>资料检查</th><th>降雨／流量完整率</th></tr></thead><tbody><tr v-for="event in inspectedEvents" :key="event.id || event.eventId"><td>{{ event.sourceFloodNo || event.id || event.eventId }}</td><td>{{ timeText(event.start) }}</td><td>{{ timeText(event.end) }}</td><td>{{ eventStatus(event) }}</td><td>{{ completenessText(event.rainCompleteness) }}／{{ completenessText(event.flowCompleteness) }}</td></tr><tr v-if="!inspectedEvents.length"><td colspan="5">本版没有登记洪水窗口；仍可按覆盖时段准备连续模拟。</td></tr></tbody></table></div></details>
          <details class="detail-group"><summary>资料检查</summary><p>{{ inspectedContents.rows[2].value }}。合格数量来自本版登记结果，不代表已完成模型率定或验证。</p><ul v-if="qualityNotes.length"><li v-for="note in qualityNotes" :key="note">{{ hydrologyText(note) }}</li></ul><p v-else>本版未登记检查说明；不据覆盖起止日期推定逐小时完整。</p></details>
          <details class="detail-group"><summary>来源与整编记录</summary><p>{{ hydrologyText(inspectedVersion.sourceLabel) || '未登记来源说明。' }}</p><p>形成方式：{{ inspectedPresentation.purpose }}</p><p v-if="inspectedVersion.sourceOrigin?.originalName">原始资料：{{ inspectedVersion.sourceOrigin.originalName }}</p><p v-if="inspectedVersion.parentBundleVersionId">沿用底板：{{ parentName(inspectedVersion.parentBundleVersionId) }}</p><div v-for="task in inspectedVersionTasks" :key="task.taskId" class="related-task"><span>{{ taskName(task) }} · {{ taskStatus(task) }}</span><button class="btn" type="button" @click="inspectTask(task, { openProcessing: false })">查看整编记录</button></div><p v-if="!inspectedVersionTasks.length">未找到关联整编任务；保留本版已登记的来源说明。</p></details>
          <details class="technical-details"><summary>技术信息：编号与完整登记记录</summary><code>{{ inspectedVersion.bundleVersionId }}</code><pre>{{ JSON.stringify(inspectedVersion, null, 2) }}</pre></details>
          <div v-if="inspectedTaskPlace === 'version' && inspectedTask && inspectedVersionTasks.some(task => task.taskId === inspectedTask.taskId)" class="related-task-details"><h5>整编记录：{{ taskName(inspectedTask) }}</h5><p>{{ taskStatus(inspectedTask) }} · {{ inspectedTask.stageLabel }}</p><p v-if="inspectedTask.errorMessage" class="error">{{ inspectedTask.errorMessage }}</p><details><summary>请求、结果与日志</summary><pre>{{ JSON.stringify(taskRequest(inspectedTask), null, 2) }}</pre><pre v-if="inspectedResult">{{ JSON.stringify(inspectedResult, null, 2) }}</pre><pre>{{ inspectedLog || '暂无日志' }}</pre></details></div>
      </details>

      <section v-if="processingOpen" class="product-section optional-processing" aria-label="资料整编" data-input-processing>
        <div class="detail-heading"><h4>资料整编</h4><button class="btn" type="button" @click="processingOpen = false">收起资料整编</button></div>
        <p class="hint">从测站雨量生成模型网格降雨，在这里分三步完成。沿用已有资料、只选择洪水场次时，无须整编。</p>
        <div class="processing-step"><h4>1．选取站点雨量</h4><p class="hint">保存站点、逐小时雨量和来源时段。这一步只准备来源，尚未生成网格降雨。</p>
        <slot name="station-source" />
        <details class="source-registration">
        <summary>高级：导入站点雨量 JSON <small>1–32 站 · 最多 8784 小时 · 2 MB</small></summary>
        <p class="hint">上传有界 JSON，或粘贴真实资料。单位 mm、EPSG:4326、本地连续逐小时整点；缺测用 null。格式模板完全缺测、非实测，必须替换名称、站号、坐标与真实来源说明后才可登记。不支持本地路径、MDB 自动转换或任意 NC 上传。</p>
        <div class="source-actions"><label class="btn file-label">选择 JSON 文件<input aria-label="选择站点来源 JSON" type="file" accept=".json,application/json" @change="readSourceFile" /></label><button class="btn" type="button" @click="insertTemplate">填入非实测格式模板</button></div>
        <label class="editor-label" for="source-json-editor">来源 JSON</label>
        <textarea id="source-json-editor" v-model="sourceJson" class="input json-editor" spellcheck="false" placeholder="粘贴真实来源 JSON；缺测必须使用 null"></textarea>
        <button class="btn primary" type="button" :disabled="busy || !sourceJson.trim() || !!app.backendError" @click="registerSource">校验并登记站点雨量</button>
      </details></div>

      <div class="processing-conditions">
        <h4>2．生成网格降雨</h4>
        <p class="hint">按下面条件实际生成新资料，并保存来源与检查结果。原资料、参数和历史计算成果保持各自版本。</p>
        <form class="processing-form" @submit.prevent="submitProcessing">
          <div class="field"><label for="input-operation">整编方式</label><select id="input-operation" v-model="form.operation" class="input"><option value="stationIdw">站点雨量 → 网格降雨</option><option value="qcWindow">已有网格降雨 → 提取时段</option></select></div>
          <p v-if="form.operation === 'qcWindow'" class="hint source-field">沿用当前资料已经形成的网格雨量，只提取另一个时段。若只是选择洪水参与率定，直接在率定与模拟中选择即可。</p>
          <div class="field"><label for="input-product-name">新资料版本名称</label><input id="input-product-name" v-model="form.name" class="input" required maxlength="120" placeholder="填写本次资料的用途或时段" /></div>
          <div v-if="form.operation === 'stationIdw'" class="field source-field"><label for="input-source">已登记站点雨量</label><select id="input-source" v-model="form.sourceId" class="input" required><option value="">请选择来源</option><option v-for="source in stationSources" :key="source.sourceId" :value="source.sourceId">{{ source.name }} · {{ source.stationCount }} 站 · {{ coverageText(source) }}</option></select><small v-if="!stationSources.length">尚未保存逐小时站点雨量，请先完成第1步。</small></div>
          <div class="field"><label for="input-window-start">开始时刻（本地整点）</label><input id="input-window-start" v-model="form.start" class="input" type="datetime-local" step="3600" required /></div>
          <div class="field"><label for="input-window-end">结束时刻（含末整点）</label><input id="input-window-end" v-model="form.end" class="input" type="datetime-local" step="3600" required /></div>
          <template v-if="form.operation === 'stationIdw'">
            <div class="field"><label for="input-idw-power">距离权重幂指数</label><input id="input-idw-power" v-model.number="form.idwPower" class="input" type="number" min="1" max="4" step="0.1" required /></div>
            <div class="field"><label for="input-minimum-stations">最少有效站数</label><input id="input-minimum-stations" v-model.number="form.minimumStations" class="input" type="number" min="1" :max="selectedSource?.stationCount || 32" step="1" required /></div>
            <div class="field source-field"><label for="input-zero-policy">全零记录处理</label><select id="input-zero-policy" v-model="form.zeroOnlyPolicy" class="input"><option value="KEEP">保留原始零值</option><option value="ANNUAL_RECLASSIFY">全年无变化测站判缺（仅完整自然年）</option></select><small>短时段无雨保留为零；负雨量在整编时判缺，原值留存。</small></div>
          </template>
          <div class="submit-row"><span>空间、流量及洪水目录沿用：{{ currentPresentation.name }} · 单次整编最长 8784 小时</span><button class="btn primary" type="submit" :disabled="busy || !!app.backendError">{{ busy ? '正在提交' : form.operation === 'stationIdw' ? '开始生成网格降雨' : '开始提取资料时段' }}</button></div>
        </form>
      </div>
      <div class="processing-conditions"><h4>3．核对并采用生成资料</h4><p class="hint">任务完成后核对结果，先“登记生成资料”，再“选用此资料”。登记只保存资料；选用才改变后续计算采用的版本。</p></div>

      <details class="product-section" :open="historyOpen" @toggle="historyOpen = $event.target.open">
        <summary>整编进度与记录 <small>{{ tasks.length }} 项</small></summary>
        <p class="hint">这里记录资料如何形成；运行进度、检查结果和原请求可追溯。生成资料不会自动开始产汇流计算。</p>
        <div class="table-scroll"><table class="table product-table" aria-label="降雨整编记录">
          <thead><tr><th>记录 / 资料</th><th>状态 / 进度</th><th>来源与时段</th><th>操作</th></tr></thead>
          <tbody><tr v-for="task in tasks" :key="task.taskId"><td><b>{{ taskName(task) }}</b></td><td>{{ taskStatus(task) }} · {{ Math.round(Number(task.progress) || 0) }}%<small>{{ task.stageLabel || task.errorMessage }}</small></td><td>{{ taskSourceText(task) }}<small>{{ coverageText(taskRequest(task)) }}</small></td><td class="task-actions"><button type="button" class="btn" :disabled="busy" @click="inspectTask(task)">核对结果 / 日志</button><button v-if="active(task)" type="button" class="btn" :disabled="busy || task.status === 'cancelling'" @click="cancelTask(task)">取消</button><button v-if="task.status === 'completed' && !task.registeredBundleVersionId" type="button" class="btn" :disabled="busy || !!app.backendError" @click="registerTask(task)">登记生成资料</button><button v-if="task.registeredBundleVersionId" type="button" class="btn" :disabled="busy || !!app.backendError || task.registeredBundleVersionId === app.selectedInputBundleVersionId" @click="selectTaskVersion(task)">{{ task.registeredBundleVersionId === app.selectedInputBundleVersionId ? '当前使用' : '选用此资料' }}</button></td></tr><tr v-if="!tasks.length"><td colspan="4">暂无资料整编记录。产汇流计算记录见成果管理。</td></tr></tbody>
        </table></div>
        <details v-if="inspectedTaskPlace === 'processing' && inspectedTask" open class="product-details"><summary>整编记录：{{ taskName(inspectedTask) }}</summary><code>{{ inspectedTask.taskId }}</code><p v-if="inspectedTask.errorMessage" class="error">{{ inspectedTask.errorMessage }}</p><h5>提交时冻结的请求</h5><pre>{{ JSON.stringify(taskRequest(inspectedTask), null, 2) }}</pre><h5 v-if="inspectedResult">整编结果（尚未登记时不可选用）</h5><pre v-if="inspectedResult">{{ JSON.stringify(inspectedResult, null, 2) }}</pre><h5>整编日志</h5><pre>{{ inspectedLog || '暂无日志' }}</pre></details>
      </details>
      </section>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import { hydrologyText } from '@/services/hydrologyPresentation'
import { inputProductContents, inputProductPresentation, inputVersionRelatedTasks, inputVersionViewState } from '@/services/inputProductPresentation'
import { latestInputProcessingWindow, mergeInputSourceChoices } from '@/services/inputSourceChoices'
import { loadModelInputCatalog } from '@/services/modelInputService'
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
const stationSources = computed(() => sources.value.filter(source => source.sourceType !== 'mdb'
  && Number.isInteger(source.stationCount) && source.stationCount > 0 && source.start && source.end))
const registeredSources = ref([])
const tasks = ref([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const message = ref('')
const sourceJson = ref('')
const inspectedVersion = ref(null)
const inspectedCatalog = ref(null)
const catalogLoading = ref(false)
const catalogError = ref('')
const inspectedTask = ref(null)
const inspectedTaskPlace = ref('processing')
const inspectedResult = ref(null)
const inspectedLog = ref('')
const processingOpen = ref(false)
const historyOpen = ref(false)
const present = (record) => inputProductPresentation(record, { catalog: app.selectedInputCatalog })
const versionRows = computed(() => versions.value.map(record => ({ record, presentation: present(record), state: inputVersionViewState(record, app.selectedInputBundleVersionId) })))
const otherVersionRows = computed(() => versionRows.value.filter(row => !row.state.selected))
const currentRecord = computed(() => app.selectedInputProduct || { bundleVersionId: app.selectedInputBundleVersionId })
const currentPresentation = computed(() => present(currentRecord.value))
const currentContents = computed(() => inputProductContents(currentRecord.value, { catalog: app.selectedInputCatalog, sources: sources.value }))
const inspectedPresentation = computed(() => inputProductPresentation(inspectedVersion.value || {}, { catalog: inspectedCatalog.value }))
const inspectedContents = computed(() => inputProductContents(inspectedVersion.value || {}, { catalog: inspectedCatalog.value, sources: sources.value }))
const inspectedEvents = computed(() => inspectedCatalog.value?.events || [])
const inspectedVersionTasks = computed(() => inputVersionRelatedTasks(inspectedVersion.value || {}, tasks.value))
const qualityNotes = computed(() => [...new Set([...(inspectedVersion.value?.qualityNotes || []), ...(inspectedCatalog.value?.qualityNotes || [])])])
const inspectedState = computed(() => inputVersionViewState(inspectedVersion.value, app.selectedInputBundleVersionId))
const form = reactive({ operation: 'stationIdw', name: '', start: '', end: '', sourceId: '', idwPower: 2, minimumStations: 1, zeroOnlyPolicy: 'KEEP' })
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
const timeText = value => value ? String(value).replace('T', ' ').slice(0, 16) : '—'
const completenessText = value => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '未登记'
const eventStatus = event => event.quality?.accepted === true ? '按本版规则通过'
  : event.quality?.accepted === false ? '按本版规则未通过'
    : ({ ready: '已登记', review: '需复核', rejected: '未通过', excluded: '未通过' }[event.status] || '未登记检查结论')
const parentName = id => versions.value.find(row => row.bundleVersionId === id)?.name || id
let requestRevision = 0
let inspectRevision = 0
let catalogRevision = 0
let pollTimer = null
let disposed = false
const active = (task) => INPUT_TASK_ACTIVE_STATUSES.includes(task.status)
const taskRequest = (task) => task?.request || task?.runConfig || {}
const activeTasks = computed(() => tasks.value.filter(active))
const taskStatus = (task) => ({ queued: '排队', running: '整编中', cancelling: '正在取消', cancelled: '已取消', failed: '失败', completed: task.registeredBundleVersionId ? '已登记' : '整编完成 · 待登记' }[task.status] || task.status)
const coverageText = (record) => present(record).range
function gridAndEventText(version) {
  const content = inputProductContents(version, { catalog: app.selectedInputCatalog, sources: sources.value })
  return `${content.gridCount === null ? '网格数未登记' : `${content.gridCount} 个网格`} · ${content.eventCount === null ? '窗口数未登记' : `${content.eventCount} 个洪水窗口`}`
}
function taskName(task) {
  const version = versions.value.find(row => row.bundleVersionId === task.registeredBundleVersionId)
  return version ? present(version).name : hydrologyText(taskRequest(task).name) || '降雨整编'
}
function taskSourceText(task) {
  const request = taskRequest(task)
  if (request.sourceId) return sources.value.find(row => row.sourceId === request.sourceId)?.name || (request.operation === 'mdbImport' ? '已登记数据库雨量、流量及洪水目录' : '已登记站点雨量')
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
    Object.assign(form, { operation: 'stationIdw', sourceId: source.sourceId,
      name: `${source.name} · 网格降雨`, start: source.start, end: source.end,
      minimumStations: Math.min(3, source.stationCount) })
  }
  return source
}, '第1步完成：站点雨量已保存，尚未生成网格降雨。请在第2步选择来源并生成。') }
function submitProcessing() { return mutate(async () => {
  const task = await submitInputProcessingTask(buildInputProcessingRequest(form, app))
  historyOpen.value = true
  return task
}, '第2步已提交：正在生成资料。完成后核对结果，在第3步登记并选用。') }
function selectVersion(version) { return mutate(() => app.selectInputBundleVersion(version.bundleVersionId), `已选用“${present(version).name}”。后续回放和作业使用此资料；洪水用途在率定与模拟中选择，参数及已有成果不随之改写。`) }
function selectTaskVersion(task) {
  const version = versions.value.find(row => row.bundleVersionId === task.registeredBundleVersionId)
  if (version) return selectVersion(version)
  error.value = '未读取到登记资料，请刷新资料后再选用。'
}
function cancelTask(task) { return mutate(() => cancelInputProcessingTask(task.taskId), `已发送取消请求：${task.taskId}。取消/失败产物不会登记。`) }
function registerTask(task) { return mutate(() => registerInputProduct(task.taskId), '第3步：生成资料已登记保存，当前计算资料未改变。确认后点击“选用此资料”。') }
function closeVersionDetails() {
  catalogRevision += 1; catalogLoading.value = false
  inspectedVersion.value = null; inspectedCatalog.value = null; catalogError.value = ''
}
async function inspectVersion(version) {
  if (!version?.bundleVersionId) return
  const revision = ++catalogRevision
  const key = app.contextKey
  inspectedVersion.value = version; inspectedCatalog.value = null; catalogError.value = ''
  catalogLoading.value = true
  try {
    const catalog = await loadModelInputCatalog({ source: { ...app.activeContext, selectedInputBundleVersionId: version.bundleVersionId } })
    if (disposed || revision !== catalogRevision || key !== app.contextKey) return
    inspectedCatalog.value = catalog
  } catch (failure) {
    if (!disposed && revision === catalogRevision && key === app.contextKey) catalogError.value = failure.message
  } finally {
    if (!disposed && revision === catalogRevision && key === app.contextKey) catalogLoading.value = false
  }
}
async function inspectTask(task, { openProcessing = true } = {}) {
  const revision = ++inspectRevision
  const key = app.contextKey
  inspectedTask.value = task; inspectedResult.value = null; inspectedLog.value = ''
  inspectedTaskPlace.value = openProcessing ? 'processing' : 'version'
  if (openProcessing) { processingOpen.value = true; historyOpen.value = true }
  try {
    const [log, result] = await Promise.all([getInputTaskLog(task.taskId), task.status === 'completed' ? getInputTaskResult(task.taskId) : Promise.resolve(null)])
    if (disposed || revision !== inspectRevision || key !== app.contextKey) return
    inspectedLog.value = log.tail || log.log || ''; inspectedResult.value = result
  } catch (failure) { if (!disposed && revision === inspectRevision && key === app.contextKey) error.value = failure.message }
}
watch(() => app.contextKey, () => {
  requestRevision += 1; inspectRevision += 1; catalogRevision += 1; clearTimeout(pollTimer)
  sources.value = []; registeredSources.value = []; tasks.value = []; inspectedVersion.value = null; inspectedTask.value = null; inspectedResult.value = null; inspectedLog.value = ''
  busy.value = false; error.value = ''; message.value = ''
  inspectedCatalog.value = null; catalogLoading.value = false; catalogError.value = ''
  processingOpen.value = false; historyOpen.value = false
  Object.assign(form, { operation: 'stationIdw', name: '', sourceId: '', start: '', end: '' })
  if (supported.value) refresh()
})
onMounted(refresh)
onBeforeUnmount(() => { disposed = true; requestRevision += 1; inspectRevision += 1; catalogRevision += 1; clearTimeout(pollTimer) })
</script>

<style scoped>
.input-products { margin: 18px 0 26px; border-top: 2px solid #193754; color: #243c56; }
.product-heading { display: flex; justify-content: space-between; align-items: center; gap: 14px; margin: 15px 0 10px; }
.product-heading h3 { margin: 0; font-size: 15px; font-weight: 800; }
.product-heading p { font-size: 12px; margin: 7px 0; color: #65778c; }
.current-input { display: flex; align-items: center; gap: 14px; padding: 14px; background: #eef5fc; border-left: 3px solid #245d89; flex-wrap: wrap; font-size: 12px; }
.current-title { flex: 1; min-width: 230px; }
.current-input strong { display: block; margin: 5px 0; font-size: 14px; color: #173e63; }
.current-input b { margin-left: auto; color: #276b55; font-size: 11px; }
.current-composition { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; width: 100%; margin: 5px 0 0; }
.current-composition div { padding: 10px 12px; background: #fff; border: 1px solid #dce6ee; }
.current-composition dt { color: #60758b; margin-bottom: 5px; }.current-composition dd { margin: 0; color: #173e63; font-weight: 600; line-height: 1.65; }
.current-range { width: 100%; margin: 0; }.current-input > .hint { margin: 0; }
.detail-heading { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }.detail-heading h4, .detail-heading p { margin-right: auto; }
.contents-table { width: 100%; min-width: 580px; font-size: 12px; }.contents-table td, .contents-table th { text-align: left; vertical-align: top; white-space: normal; line-height: 1.65; }.contents-table th:first-child { min-width: 140px; }.contents-table td:nth-child(2) { min-width: 165px; }
.detail-group { padding-top: 10px; margin-top: 12px; border-top: 1px solid #dce6ee; }.detail-group summary { font-size: 12px; }
.related-task { display: flex; gap: 10px; justify-content: space-between; align-items: center; padding: 8px 0; flex-wrap: wrap; font-size: 12px; }.related-task-details { border-top: 1px solid #dce6ee; margin-top: 10px; }
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
.source-registration, .processing-conditions, .processing-step { margin-top: 18px; }.optional-processing { padding: 15px; border: 1px solid #dbe4ee; background: #fbfdff; }.processing-conditions { padding-top: 12px; border-top: 1px solid #dbe4ee; }
.processing-progress { margin: 16px 0 8px; padding: 9px 12px; background: #eef5fc; font-size: 12px; line-height: 1.7; color: #235f89; }
.technical-details { margin-top: 12px; }.technical-details summary { font-weight: 500; font-size: 12px; }.technical-details code { display: block; margin-top: 9px; overflow-wrap: anywhere; }
.product-details .view-state { color: #215f80; font-weight: 600; }
.field label { display: block; margin-bottom: 5px; font-size: 12px; }.source-field, .submit-row { grid-column: 1 / -1; }
.submit-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 11px; color: #6c8095; flex-wrap: wrap; }
.task-actions { display: flex; gap: 6px; flex-wrap: wrap; min-width: 170px; }
.product-details { background: #f7faff; border: 1px solid #d6e2ed; padding: 12px; margin-top: 10px; }
summary { font-weight: 700; cursor: pointer; font-size: 13px; }pre { white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow: auto; color: #37546e; line-height: 1.65; }
h5 { margin: 12px 0 5px; font-size: 12px; }.product-details p, .product-details li { font-size: 12px; line-height: 1.65; }
@media (max-width: 850px) { .processing-form, .current-composition { grid-template-columns: 1fr; }.product-heading { align-items: flex-start; flex-wrap: wrap; }.current-input b { margin-left: 0; }.optional-processing { padding: 12px; } }
</style>
