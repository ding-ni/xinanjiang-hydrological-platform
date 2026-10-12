<template>
  <section class="result-workbench" aria-label="成果工作台">
    <div class="result-heading"><div><span class="eyebrow">计算成果</span><h3>{{ saved.name || task.runTypeLabel }}</h3><p>{{ task.taskId }}</p></div><div class="actions"><button class="btn" @click="downloadJson">完整结果 JSON</button><button class="btn" :disabled="busy" @click="downloadReports">报告包 ZIP</button></div></div>
    <div class="workbench-layout">
      <div class="result-main">
        <SpatialResults v-if="result.spatial" :task-id="task.taskId" :product="result.spatial" />
        <SearchPlanResult v-if="result.searchPlan" :plan="result.searchPlan" />
        <p v-if="result.routingCandidate" class="parameter-diff">成果汇流方案：{{ result.routingCandidate.name }}</p>
        <div v-if="periods.length" class="process-toolbar"><label>查看时段 <select v-model="periodId" class="input" aria-label="成果时段"><option v-for="row in periods" :key="row.id" :value="row.id">{{ row.label }}</option></select></label><button class="btn" @click="exportProcess">逐时过程 CSV</button><button class="btn" @click="exportSummary">{{ isForecast ? '预报摘要 CSV' : '评价汇总 CSV' }}</button></div>
        <template v-if="hydrograph">
          <div v-if="isForecast" class="hydro-metrics"><div><span>预报洪峰 (m³/s)</span><b>{{ num(hydrograph.peak) }}</b></div><div><span>峰现时刻</span><b>{{ hydrograph.peakTime?.replace('T', ' ') }}</b></div><div><span>预报时段水量 (万 m³)</span><b>{{ num(hydrograph.volume / 1e4) }}</b></div><div><span>累计面雨量 (mm)</span><b>{{ num(rainTotal) }}</b></div></div>
          <div v-else class="hydro-metrics"><div><span>纳什效率系数 NSE</span><b>{{ num(hydrograph.metrics?.nse, 3) }}</b></div><div><span>模拟洪峰 (m³/s)</span><b>{{ num(hydrograph.peak) }}</b></div><div><span>洪峰相对误差 (%)</span><b>{{ num(hydrograph.metrics?.peakErr) }}</b></div><div><span>峰现时间误差 (h)</span><b>{{ num(hydrograph.metrics?.timeErrH) }}</b></div><div><span>洪量相对误差 (%)</span><b>{{ num(hydrograph.metrics?.volErr) }}</b></div></div>
          <div class="result-chart"><HydrographChart v-if="isForecast" :hydrograph="hydrograph" :show-prev="false" /><CalibrationProcessChart v-else :hydrograph="hydrograph" :forcing="hydrograph.forcing || {}" :visible-components="{}" :comparisons="comparisonCurves" :current-curve="{ label: saved.name || '当前模拟', color: '#bb592f' }" /></div>
          <div v-if="!isForecast" class="comparison-toolbar"><label>对比成果 <select v-model="compareId" class="input" aria-label="对比成果"><option value="">不叠加</option><option v-for="row in candidates" :key="row.taskId" :value="row.taskId">{{ metadata[row.taskId]?.name || `${row.runTypeLabel} · ${row.taskId.slice(-8)}` }}</option></select></label><span v-if="comparisonLoading">读取过程…</span><span v-else-if="compareId">{{ compareError || comparisonNotice || '同条件过程对比' }}</span></div>
          <p v-if="comparisonCurves.length" class="parameter-diff">{{ parameterDiff || '两次计算的产流参数相同' }}</p>
          <div v-if="comparisonCurves.length" class="comparison-metrics">对比成果：NSE {{ num(compareHydro.metrics?.nse, 3) }} · 洪峰 {{ num(compareHydro.peak) }} m³/s · 洪量误差 {{ num(compareHydro.metrics?.volErr) }}%</div>
        </template>
        <div v-else-if="!result.spatial" class="result-snapshot"><h4>{{ task.runType === 'stateImport' ? '状态文件已入库' : '计算成果已保存' }}</h4><p v-if="result.stateTime">状态时刻 {{ result.stateTime.replace('T', ' ') }} · {{ result.gridCount }} 个网格</p><button class="btn primary" @click="$emit('open', task)">{{ task.runType === 'stateImport' ? '打开状态库' : '打开计算工作区' }}</button></div>
        <section v-if="parameterRows.length" class="parameter-section"><div class="section-heading"><h4>产流参数</h4><button class="btn" @click="exportParameters">参数表 CSV</button></div><div class="parameter-grid"><div v-for="row in parameterRows" :key="row.key"><span>{{ row.label }} <i>{{ row.key }}</i></span><b :title="String(row.value)">{{ Number.isFinite(row.value) ? Number(row.value.toPrecision(7)) : row.value }} <small>{{ row.unit === '1' ? '' : row.unit === '1/hour' ? 'h⁻¹' : row.unit }}</small></b></div></div></section>
      </div>
      <aside class="result-notes"><form @submit.prevent="save"><h4>成果整理</h4><label>名称<input v-model="draft.name" class="input" aria-label="成果名称" maxlength="80" :disabled="saving" /></label><label>分组<input v-model="draft.collection" class="input" aria-label="成果分组" maxlength="40" list="result-groups" :disabled="saving" /><datalist id="result-groups"><option v-for="group in groups" :key="group" :value="group" /></datalist></label><label>备注<textarea v-model="draft.notes" class="input" aria-label="成果备注" rows="5" maxlength="2000" :disabled="saving" /></label><label class="favorite"><input v-model="draft.favorite" type="checkbox" :disabled="saving" />收藏成果</label><button class="btn primary" :disabled="saving || !dirty">{{ saving ? '保存中…' : '保存整理' }}</button><span role="status">{{ savedMessage }}</span></form><p v-if="error" class="error" role="alert">{{ error }}</p><details><summary>版本信息</summary><dl><dt>参数版本</dt><dd>{{ result.parameterVersionId || result.lineage?.parameterVersionId || '—' }}</dd><dt>输入版本</dt><dd>{{ result.lineage?.inputBundleVersionId || '—' }}</dd><dt>汇流版本</dt><dd>{{ result.lineage?.routingVersionId || '—' }}</dd></dl></details></aside>
    </div>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import CalibrationProcessChart from '@/components/charts/CalibrationProcessChart.vue'
import HydrographChart from '@/components/charts/HydrographChart.vue'
import SpatialResults from '@/components/model/SpatialResults.vue'
import SearchPlanResult from '@/components/params/SearchPlanResult.vue'
import { modelApi } from '@/services/modelBackendService'
import { comparisonReason, csv, downloadText, hydrographCsv, resultPeriods, summaryRows, SUMMARY_HEADER } from '@/services/resultPresentation'
import { changedParameters } from '@/services/manualCalibrationService'
const props = defineProps({ task: Object, result: Object, tasks: Array, metadata: Object, definitions: Array })
const emit = defineEmits(['saved', 'open'])
const blank = () => ({ name: '', collection: '', notes: '', favorite: false, revision: 0 })
const saved = computed(() => props.metadata[props.task.taskId] || blank())
const draft = ref(blank()), saving = ref(false), savedMessage = ref(''), error = ref(''), busy = ref(false)
const periodId = ref(''), compareId = ref(''), compareResult = ref(null), compareError = ref(''), comparisonLoading = ref(false)
const periods = computed(() => resultPeriods(props.result))
const hydrograph = computed(() => periods.value.find(row => row.id === periodId.value)?.hydrograph)
const isForecast = computed(() => props.result.runType === 'forecast')
const rainTotal = computed(() => (hydrograph.value?.forcing?.precipitation || []).reduce((sum, value) => sum + value, 0))
const groups = computed(() => [...new Set(Object.values(props.metadata).map(row => row.collection).filter(Boolean))].sort())
const dirty = computed(() => ['name', 'collection', 'notes', 'favorite'].some(key => draft.value[key] !== saved.value[key]))
const candidates = computed(() => props.tasks.filter(row => row.taskId !== props.task.taskId && row.resultSource === 'model-backend' && ['completed', 'archived'].includes(row.status) && ['eventCalibration', 'continuousCalibration', 'continuousSimulation', 'stateInitialization', 'stateContinuation'].includes(row.runType)))
const compareTask = computed(() => candidates.value.find(row => row.taskId === compareId.value))
const compareHydro = computed(() => resultPeriods(compareResult.value || {}).find(row => row.id === periodId.value)?.hydrograph)
const comparisonNotice = computed(() => !compareResult.value ? '' : comparisonReason(props.task.request, compareTask.value?.request, hydrograph.value, compareHydro.value))
const comparisonCurves = computed(() => compareTask.value && compareHydro.value && !comparisonNotice.value ? [{ taskId: compareId.value, label: props.metadata[compareId.value]?.name || `对比 · ${compareId.value.slice(-8)}`, color: '#18857a', hydrograph: compareHydro.value }] : [])
const parameters = computed(() => props.result.bestParameters || props.result.parameters || {})
const parameterDiff = computed(() => changedParameters(compareResult.value?.bestParameters || compareResult.value?.parameters, parameters.value))
const parameterRows = computed(() => Object.entries(parameters.value).map(([key, value]) => ({ key, value, ...(props.definitions || []).find(row => row.key === key) })))
const num = (v, digits = 2) => Number.isFinite(v) ? v.toFixed(digits) : '—'
watch(() => props.task.taskId, () => { draft.value = { ...blank(), ...saved.value }; periodId.value = periods.value[0]?.id || ''; compareId.value = ''; error.value = ''; savedMessage.value = '' }, { immediate: true })
watch(() => saved.value.revision, () => { draft.value = { ...blank(), ...saved.value } })
let comparisonRevision = 0
watch(compareId, async id => {
  const revision = ++comparisonRevision; compareResult.value = null; compareError.value = ''; comparisonLoading.value = Boolean(id)
  if (!id) return
  try { const result = await modelApi(`tasks/${encodeURIComponent(id)}/result`); if (revision === comparisonRevision) compareResult.value = result }
  catch (failure) { if (revision === comparisonRevision) compareError.value = failure.message }
  finally { if (revision === comparisonRevision) comparisonLoading.value = false }
})
async function save() {
  if (saving.value) return
  const id = props.task.taskId, body = { name: draft.value.name, collection: draft.value.collection, notes: draft.value.notes, favorite: draft.value.favorite, revision: draft.value.revision }
  saving.value = true; error.value = ''; savedMessage.value = ''
  try {
    const row = await modelApi(`result-metadata/${encodeURIComponent(id)}`, { method: 'POST', body })
    emit('saved', id, row)
    if (props.task.taskId === id) { draft.value = { ...row }; savedMessage.value = '已保存' }
  } catch (failure) { if (props.task.taskId === id) error.value = failure.message }
  finally { saving.value = false }
}
function downloadJson() { downloadText(`${props.task.taskId}.json`, JSON.stringify({ request: props.task.request, result: props.result, metadata: saved.value }, null, 2), 'application/json') }
function exportProcess() { downloadText(`${props.task.taskId}-${periodId.value}-process.csv`, hydrographCsv(hydrograph.value, comparisonCurves.value, isForecast.value ? 'forecast' : 'simulation')) }
function exportSummary() {
  const rows = isForecast.value
    ? [['任务编号', '成果名称', '起报时刻', '预见期 (h)', '预报洪峰 (m³/s)', '峰现时刻', '预报时段水量 (m³)', '累计面雨量 (mm)', '降雨情景版本', '起报状态', '参数版本', '汇流版本'],
      [props.task.taskId, saved.value.name || props.task.runTypeLabel, props.result.issue.issueTime, props.result.issue.leadHours,
        hydrograph.value.peak, hydrograph.value.peakTime, hydrograph.value.volume, rainTotal.value,
        props.result.lineage.forecastRainVersionId, props.result.parentStateVersionId,
        props.result.lineage.parameterVersionId, props.result.lineage.routingVersionId]]
    : [SUMMARY_HEADER, ...summaryRows(props.task, props.result, saved.value)]
  downloadText(`${props.task.taskId}-summary.csv`, csv(rows))
}
function exportParameters() { downloadText(`${props.task.taskId}-parameters.csv`, csv([['中文名称', '符号', '数值', '单位'], ...parameterRows.value.map(row => [row.label, row.key, row.value, row.unit])])) }
async function downloadReports() {
  busy.value = true; error.value = ''
  try {
    const base = String(import.meta.env.VITE_MODEL_API_BASE).replace(/\/$/, '')
    const response = await fetch(`${base}/tasks/${encodeURIComponent(props.task.taskId)}/report-package`)
    if (!response.ok) throw new Error((await response.json()).error || '报告包读取失败')
    const url = URL.createObjectURL(await response.blob()), link = document.createElement('a')
    link.href = url; link.download = `${props.task.taskId}-reports.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
</script>

<style scoped>
.result-workbench{border-top:1px solid #dce5e5;margin-top:16px;padding-top:20px;min-width:0}.result-heading{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:20px}.eyebrow{font-size:10px;letter-spacing:.16em;color:#558476}.result-heading h3{font-size:20px;color:#234c46;margin:5px 0;font-family:'Noto Serif CJK SC','SimSun',serif}.result-heading p{font-size:10px;color:#83918d;overflow-wrap:anywhere;margin:0}.actions,.process-toolbar,.comparison-toolbar,.section-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.btn{font-size:11px;min-height:32px}.workbench-layout{display:grid;grid-template-columns:minmax(0,1fr) 235px;gap:24px}.result-main{min-width:0}.process-toolbar label,.comparison-toolbar label{display:flex;align-items:center;gap:8px;font-size:12px;color:#55726a}.process-toolbar .input,.comparison-toolbar .input{width:210px;max-width:100%}.hydro-metrics{display:flex;flex-wrap:wrap;gap:18px;padding:18px 0}.hydro-metrics>div{display:grid;gap:7px;flex:1;min-width:105px}.hydro-metrics span{color:#7b8d86;font-size:10px}.hydro-metrics b{font-size:20px;color:#315b53;font-variant-numeric:tabular-nums;font-weight:500}.result-chart{height:370px;min-width:0}.comparison-toolbar{padding:12px;background:#f3f7f5;border-radius:4px}.comparison-toolbar>span,.comparison-metrics{font-size:11px;color:#62796d}.parameter-diff{font-size:12px;color:#92603e;line-height:1.8;margin:8px 0}.comparison-metrics{margin:8px 0}.parameter-section{margin-top:24px}.section-heading{margin-bottom:12px}.section-heading h4,.result-notes h4{margin:0;color:#36594d;font-size:13px}.parameter-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-top:1px solid #dfe8e2;border-left:1px solid #dfe8e2}.parameter-grid>div{padding:12px;display:grid;gap:10px;border-right:1px solid #dfe8e2;border-bottom:1px solid #dfe8e2;min-width:0}.parameter-grid span{font-size:11px;color:#667f71}.parameter-grid i{font-style:normal;font-weight:700;margin-left:5px;color:#2a6555}.parameter-grid b{font-size:12px;font-weight:500;color:#344a41;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.parameter-grid small{font-size:10px;font-weight:400}.result-notes{border-left:1px solid #e1e8e3;padding-left:20px;min-width:0}.result-notes form{display:grid;gap:14px}.result-notes label{font-size:11px;color:#6f847a;display:grid;gap:6px}.result-notes .input{width:100%;min-width:0}.result-notes textarea{resize:vertical;line-height:1.7}.result-notes .favorite{display:flex;align-items:center;gap:7px}.result-notes [role=status]{font-size:11px;color:#328169}.result-notes details{font-size:11px;color:#6a7d73;margin-top:22px}.result-notes summary{cursor:pointer}.result-notes dd{margin:5px 0 14px;overflow-wrap:anywhere;font-size:10px}.error{font-size:12px;color:#a33d35}.result-snapshot{padding:35px 24px;background:#f3f7f5;color:#365c4f}.result-snapshot p{font-size:12px}@media(max-width:1050px){.workbench-layout{grid-template-columns:1fr}.result-notes{border-left:0;border-top:1px solid #e1e8e3;padding:16px 0}.result-notes form{grid-template-columns:1fr 1fr}.result-notes h4{grid-column:1/-1}.result-heading{align-items:flex-start}.parameter-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:640px){.result-heading{flex-direction:column}.result-chart{height:320px}.result-notes form{grid-template-columns:1fr}.process-toolbar label,.comparison-toolbar label{flex-wrap:wrap}.hydro-metrics b{font-size:17px}}
</style>
