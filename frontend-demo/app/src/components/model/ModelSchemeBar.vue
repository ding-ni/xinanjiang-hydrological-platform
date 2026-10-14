<template>
  <section v-if="!opened" class="scheme-list" aria-label="率定方案">
    <header><div><h1>参数率定</h1><p>{{ app.basinName }} · {{ cal.section }}</p></div><button class="btn primary" :disabled="busy" @click="beginCreate">新建方案</button></header>
    <form v-if="creating" class="scheme-create" @submit.prevent="create">
      <label for="new-scheme-name">方案名称</label>
      <input id="new-scheme-name" ref="nameInput" v-model.trim="newName" class="input" required maxlength="80" placeholder="填写名称" :disabled="busy" />
      <button class="btn primary" :disabled="busy || !newName">建立方案</button>
      <button type="button" class="btn" :disabled="busy" @click="creating = false">取消</button>
    </form>
    <p v-if="error" role="alert" class="scheme-error">{{ error }}</p>
    <div v-if="rows.length" class="scheme-cards">
      <button v-for="row in rows" :key="row.id" class="scheme-choice" :data-scheme-id="row.id" :disabled="busy" @click="load(row.id)">
        <div><b>{{ row.name }}</b><p v-if="row.summary?.mode !== 'continuous'">已选 {{ row.summary?.selectedCount || 0 }} 场 · 率定 {{ row.summary?.calibration || 0 }} · 验证 {{ row.summary?.validation || 0 }} · 独立检验 {{ row.summary?.independent || 0 }}</p><p v-else>连续时段率定</p><p v-if="row.latestCalculation">最近计算：{{ operationStatuses[row.latestCalculation.status] || row.latestCalculation.status }} · {{ dateLabel(row.latestCalculation.completedAt || row.latestCalculation.createdAt) }}</p></div><span>{{ dateLabel(row.updated_at || row.updatedAt) }}</span><i aria-hidden="true">→</i>
      </button>
    </div>
    <p v-else-if="!busy && !creating && !error" class="empty">暂无率定方案</p>
    <details v-if="originalRecords.length" class="original-records"><summary>已有率定记录</summary><button v-for="record in originalRecords" :key="record.originalCalibrationPlan.planId" class="scheme-choice" :disabled="busy" @click="loadOriginal(record)"><b>{{ record.name || record.originalCalibrationPlan.name }}</b><i aria-hidden="true">→</i></button></details>
  </section>
  <header v-else class="scheme-heading" aria-label="当前率定方案">
    <button class="btn" :disabled="busy" @click="back">← 方案列表</button><h1>{{ cal.modelSchemeName }}</h1>
    <span v-if="saving" class="save-status" role="status">保存中</span>
    <span v-else-if="error" class="scheme-error" role="alert">{{ error }} <button class="text-button" @click="flush">重试</button></span>
  </header>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { modelApi } from '@/services/modelBackendService'
import { calibrationEditor, restoreModelScheme, saveCalibrationEditor, schemeDefinition, originalCalibrationRecords, openOriginalCalibrationRecord, recoverActiveCalibrationOperation, makeSchemeEditable } from '@/services/modelSchemeService'
import { operationStatuses } from '@/services/dailyOperationService'
import { useAppStore } from '@/stores/app'
import { useCalibrationStore } from '@/stores/calibration'

const app = useAppStore(), cal = useCalibrationStore()
const route = useRoute()
const rows = ref([]), opened = ref(false), busy = ref(false), saving = ref(false), creating = ref(false)
const originalRecords = ref([])
const newName = ref(''), nameInput = ref(null), error = ref('')
const emit = defineEmits(['loaded', 'opened'])
let timer, loading = false, pendingSave = Promise.resolve(), restoringUnnamed = false

function dateLabel(value) { return String(value || '').replace('T', ' ').slice(0, 16) }
async function refresh() {
  try {
    const [schemes, parameters] = await Promise.all([modelApi('model-schemes'), modelApi('parameter-versions')])
    rows.value = (schemes.schemes || []).filter(row => !row.context?.contextKey || row.context.contextKey === app.contextKey)
    const linked = new Set(rows.value.map(row => row.originalPlanSHA256).filter(Boolean))
    originalRecords.value = originalCalibrationRecords(parameters.versions || [], app.contextKey).filter(row => !linked.has(row.originalCalibrationPlan.planSHA256))
  }
  catch (failure) { error.value = failure.message }
}
async function loadOriginal(record) {
  if (cal.running || cal.activeOperationId) { error.value = '请先完成或停止当前计算'; return }
  busy.value = true; loading = true; error.value = ''
  try {
    await refreshCatalog()
    await openOriginalCalibrationRecord(cal, app, record)
    opened.value = true; emit('opened', true); emit('loaded')
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false; await nextTick(); loading = false }
}
async function refreshCatalog() {
  const payload = await modelApi('flood-catalog?projectId=' + encodeURIComponent(app.basinId) + '&resolutionId=' + encodeURIComponent(app.modelScale))
  cal.loadFullFloodCatalog(payload.events || [])
}
async function load(id) {
  if ((cal.running || cal.activeOperationId) && cal.modelSchemeId !== id) { error.value = '请先完成或停止当前计算'; return }
  busy.value = true; loading = true; error.value = ''
  try {
    await refreshCatalog()
    await restoreModelScheme(cal, app, await modelApi('model-schemes/' + id))
    const reference = cal.originalCalibrationPlan
    const source = reference ? originalCalibrationRecords((await modelApi('parameter-versions')).versions || [], app.contextKey).find(row => row.originalCalibrationPlan.planSHA256 === reference.planSHA256) : null
    makeSchemeEditable(cal, source)
    opened.value = true; emit('opened', true); emit('loaded')
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false; await nextTick(); loading = false }
}
async function create() {
  if (!newName.value.trim()) return
  if (cal.running || cal.activeOperationId) { error.value = '请先完成或停止当前计算'; return }
  busy.value = true; loading = true; error.value = ''
  try {
    if (!restoringUnnamed) {
      const defaults = await modelApi('preparation-defaults')
      await refreshCatalog()
      cal.resetCalibrationScheme(defaults, app.backendCase)
    }
    cal.modelSchemeName = newName.value.trim()
    const editor = calibrationEditor(cal)
    const row = await modelApi('model-schemes', { method: 'POST', body: { name: cal.modelSchemeName, context: editor.context, editor } })
    cal.modelSchemeId = row.id; cal.modelSchemeRevision = row.revision; cal.modelSchemeLatestRevision = row.latestRevision
    cal.saveCurrentWorkspace()
    creating.value = false; newName.value = ''; opened.value = true; restoringUnnamed = false
    emit('opened', true); emit('loaded')
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false; await nextTick(); loading = false }
}
function beginCreate() { restoringUnnamed = false; newName.value = ''; creating.value = true }
async function restoreTask(taskId, schemeId, legacyConditionId = null) {
  busy.value = true; loading = true; error.value = ''
  try {
    await refreshCatalog()
    const task = legacyConditionId ? null : await modelApi('calibration-tasks/' + encodeURIComponent(taskId))
    const request = legacyConditionId ? await modelApi('database/schemes/' + encodeURIComponent(legacyConditionId)) : task.runConfig || task.request
    if (request?.runType !== 'calibration') throw new Error('请选择率定计算记录')
    const id = schemeId || request.scheme?.id
    const existing = id ? await modelApi('model-schemes/' + encodeURIComponent(id)) : null
    const method = request.inputs?.rainfallMethod || existing?.definition?.inputs?.rainfallMethod || 'reuse'
    await restoreModelScheme(cal, app, { id: existing?.id || null, name: existing?.name || '',
      revision: request.scheme?.revision || existing?.revision || 0, latestRevision: existing?.latestRevision || 0,
      definition: schemeDefinition(request, method), calculationDraft: request, editor: {} })
    cal.activeOperationId = null; cal.activeOperationRequest = null; cal.running = false
    makeSchemeEditable(cal)
    if (existing) { opened.value = true; emit('opened', true); emit('loaded') }
    else { restoringUnnamed = true; creating.value = true; newName.value = '' }
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false; await nextTick(); loading = false }
}
function queueSave() {
  if (!opened.value || loading || !cal.modelSchemeId) return
  clearTimeout(timer)
  timer = setTimeout(flush, 500)
}
async function flush() {
  clearTimeout(timer)
  if (!opened.value || loading || !cal.modelSchemeId) return
  const id = cal.modelSchemeId
  pendingSave = pendingSave.catch(() => {}).then(async () => {
    if (cal.modelSchemeId !== id) return
    saving.value = true
    try { await saveCalibrationEditor(cal); error.value = '' }
    catch (failure) { error.value = failure.message; throw failure }
    finally { saving.value = false }
  })
  try { await pendingSave } catch { /* The failed save remains visible beside the scheme name. */ }
}
async function back() { await flush(); if (error.value) return; opened.value = false; emit('opened', false); await refresh() }
watch(() => JSON.stringify(calibrationEditor(cal)), queueSave)
watch(() => app.contextKey, () => { opened.value = false; emit('opened', false); error.value = ''; refresh() })
watch(creating, async value => { if (value) { await nextTick(); nameInput.value?.focus() } })
onMounted(async () => {
  await refresh()
  if (route.query.legacyConditionId) await restoreTask(null, route.query.schemeId && String(route.query.schemeId), String(route.query.legacyConditionId))
  else if (route.query.taskId) await restoreTask(String(route.query.taskId), route.query.schemeId && String(route.query.schemeId))
  else if (route.query.schemeId) await load(String(route.query.schemeId))
  else if (cal.activeOperationId && cal.modelSchemeId) {
    try {
      if (await recoverActiveCalibrationOperation(cal)) { opened.value = true; emit('opened', true); emit('loaded') }
    } catch (failure) { error.value = failure.message }
  }
})
onBeforeUnmount(() => { clearTimeout(timer); if (opened.value && !loading) saveCalibrationEditor(cal).catch(() => {}) })
defineExpose({ refresh, flush, load })
</script>

<style scoped>
.original-records{margin-top:20px;border-top:1px solid #e4ece9;padding-top:14px}.original-records summary{font-size:12px;color:#768a7d;cursor:pointer;margin-bottom:12px}.original-records .scheme-choice{grid-template-columns:1fr 28px;margin-top:8px}
.scheme-list{max-width:960px;margin:32px auto;padding:28px;background:#fff;border:1px solid #d6e2df;border-radius:5px}.scheme-list header,.scheme-heading{display:flex;align-items:center;gap:16px}.scheme-list header{justify-content:space-between;margin-bottom:24px}.scheme-list h1,.scheme-heading h1{font-size:20px;color:#244c44;margin:0}.scheme-list header p{margin:8px 0 0;color:#71837d;font-size:13px}.scheme-create{display:grid;grid-template-columns:auto minmax(160px,1fr) auto auto;gap:10px;align-items:center;padding:18px 0;border-top:1px solid #e4ece9}.scheme-create label{font-size:13px}.scheme-cards{display:grid;gap:8px}.scheme-choice{display:grid;grid-template-columns:1fr auto 28px;align-items:center;gap:20px;padding:17px 18px;background:#fbfdfc;border:1px solid #dce7e2;border-radius:4px;text-align:left;cursor:pointer;color:#31584c}.scheme-choice:hover{background:#eef6f2;border-color:#92b5a8}.scheme-choice span{font-size:12px;color:#75877e}.scheme-choice i{font-style:normal;font-size:20px}.scheme-choice:focus-visible{outline:2px solid #258276;outline-offset:2px}.empty{padding:52px 0;text-align:center;color:#8b9b94;font-size:14px}.scheme-heading{padding:7px 0}.save-status{margin-left:auto;color:#8a9d94;font-size:12px}.scheme-error{color:#a93636;font-size:13px}.text-button{border:0;background:none;color:inherit;text-decoration:underline;cursor:pointer}
@media(max-width:640px){.scheme-list{margin:12px 0;padding:18px}.scheme-create{grid-template-columns:1fr 1fr}.scheme-create label,.scheme-create input{grid-column:1/-1}.scheme-choice{grid-template-columns:1fr 25px}.scheme-choice span{display:none}}
.scheme-list :is(label,p,span,summary,.btn,.input),.scheme-heading :is(span,.btn){font-size:14px}
.scheme-list :is(.btn,.input),.scheme-heading .btn{min-height:36px}
</style>
