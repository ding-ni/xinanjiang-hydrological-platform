<template>
  <div class="diagnostic-view">
    <header class="heading"><div><span class="eyebrow">{{ route.query.label || app.basinName }}</span><h2>{{ resultContext ? '河道流量' : '进阶诊断' }}</h2></div><div class="actions"><RouterLink v-if="resultContext" to="/archive" class="btn">计算成果</RouterLink><button class="btn" :disabled="loading || busy || !supported" @click="refresh"><Refresh class="tool-icon" />刷新</button></div></header>
    <p v-if="!supported" class="notice">模型诊断：南安溪 · 1 km。</p>
    <template v-else>
      <p v-if="!resultContext" class="notice">这里保留历史查询。日常断面管理与图件制作请进入 <RouterLink to="/config?tab=routing">河网与断面</RouterLink>；流量查询请从计算成果进入。</p>
      <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="message" role="status" class="message">{{ message }}</p>
      <nav v-if="!resultContext" class="tabs" role="tablist" aria-label="诊断分区"><button v-for="item in tabs" :key="item.id" role="tab" :aria-selected="tab === item.id" :class="{ selected: tab === item.id }" @click="tab = item.id">{{ item.label }}</button></nav>
      <section v-show="tab === 'routing'" class="section">
        <RoutingWorkbench :spatial="spatial" query-only :visible="tab === 'routing'" />
        <div class="spatial-toolbar"><div><h3>空间成果</h3><span>30 m 地形 · 河网 · 汇流分区</span></div><button class="btn primary" :disabled="busy || loading || spatialRunning" @click="submitSpatial">{{ spatialRunning ? '正在制作空间成果…' : '生成空间成果' }}</button><button v-if="latestSpatial" class="btn" :disabled="busy" @click="inspect(latestSpatial)">查看最近成果</button></div>
      </section>
      <section v-if="tab === 'point'" class="section">
        <form class="form" @submit.prevent="submitPoint"><label class="wide">计算成果<select v-model="pointSource" class="input" required><option value="">请选择已完成模拟</option><option v-for="row in simulations" :key="row.taskId" :value="row.taskId">{{ sourceName(row) }}</option></select></label>
          <label>开始<input v-model="pointStart" class="input" type="datetime-local" step="3600" required /></label><label>结束<input v-model="pointEnd" class="input" type="datetime-local" step="3600" required /></label><button class="btn primary" :disabled="busy || !pointSource || point.lon == null">查询河道流量</button>
        </form><details v-if="selectedSource"><summary>来源资料与汇流版本</summary><p class="identity">输入 {{ selectedSource.runConfig.inputs.bundleVersionId }} · 汇流 {{ selectedSource.runConfig.routing?.versionId || 'control-split-v2' }}</p></details>
        <RoutingWorkbench v-if="querySpatial || spatial" :spatial="querySpatial || spatial" query-only :queried-point="point.lon == null || point.lat == null ? null : [point.lon, point.lat]" :visible="tab === 'point'" @point="choosePoint" />
        <button v-if="selectedSource && (independentRequired || outsidePeriod)" class="btn" :disabled="busy" @click="openIndependentSimulation">{{ independentRequired ? '按此条件独立复算' : '模拟所选时段' }}</button>
        <p v-if="!simulations.length" class="notice">暂无可查询成果 <RouterLink to="/simulation">开始模拟</RouterLink></p>
        <details><summary>进阶：坡面估算与位置设置</summary><div class="form"><label>经度<input v-model.number="point.lon" class="input" type="number" step="any" /></label><label>纬度<input v-model.number="point.lat" class="input" type="number" step="any" /></label><label>分类河宽（m）<input v-model.number="point.channelWidthM" class="input" type="number" min="1" max="1000" /></label><button class="btn" :disabled="busy || !pointSource || point.lon == null" @click="submitPoint(false)">查询坡面或河道估算</button></div></details>
      </section>
      <section v-if="tab === 'experiment'" class="section">
        <form class="form" @submit.prevent="submitExperiment"><label class="wide">率定方案<select v-model="experimentSource" class="input" required><option value="">请选择率定任务</option><option v-for="row in calibrations" :key="row.taskId" :value="row.taskId">{{ sourceName(row) }} · {{ row.runConfig.period.mode === 'continuous' ? `${row.runConfig.period.windows?.length || 0} 个连续时段` : `${row.runConfig.period.events?.length || 0} 场洪水` }} · {{ row.runConfig.calibration.optimizer.maxEvaluations }} 次评价</option></select></label>
          <label>实验方式<select v-model="experimentMode" class="input" @change="resetValues"><option value="multiSeed">多随机种子稳定性</option><option value="wlmBoundary" :disabled="customSearchSource">WLM 上界比较</option></select></label><label>最大参数评价次数<input v-model.number="budget" class="input" type="number" :min="minimumBudget" max="500" required /></label>
          <label v-for="(_, index) in values" :key="index">{{ experimentMode === 'multiSeed' ? '种子' : '上界 mm' }} {{ index + 1 }}<input v-model.number="values[index]" class="input" type="number" :min="experimentMode === 'multiSeed' ? 0 : 60" :max="experimentMode === 'multiSeed' ? 4294967295 : 200" step="1" required /></label><button class="btn primary" :disabled="busy || !experimentSource || (customSearchSource && experimentMode === 'wlmBoundary')">开始参数检验</button>
        </form>
        <p v-if="customSearchSource" class="notice">沿用所选参数及搜索范围；WLM 上界比较使用预设范围方案。</p>
      </section>
      <section v-if="tab === 'workflow'" class="section">
        <form class="form" @submit.prevent="submitWorkflow">
          <label class="wide">率定方案<select v-model="experimentSource" class="input" required><option value="">请选择率定任务</option><option v-for="row in calibrations" :key="row.taskId" :value="row.taskId">{{ sourceName(row) }}</option></select></label>
          <label>最大参数评价次数<input v-model.number="budget" class="input" type="number" :min="minimumBudget" max="500" required /></label>
          <label>连续预热年<input v-model.number="warmupYear" class="input" type="number" min="1960" max="2023" required /></label>
          <label v-for="group in yearLabels" :key="group.id">{{ group.label }}年份<input v-model="yearText[group.id]" class="input" required /></label>
          <label>WLM 上界（mm）<input v-model="workflowBounds" class="input" required /></label>
          <label>随机种子<input v-model="workflowSeeds" class="input" required /></label>
          <button class="btn primary" :disabled="busy || !experimentSource || customSearchSource">执行模型综合检验</button>
        </form>
        <p v-if="customSearchSource" class="notice">综合检验含 WLM 上界比较，请选择预设范围方案。</p>
      </section>
      <section class="section"><div class="task-heading"><h3>诊断任务 <small>{{ filteredDiagnostics.length }} / {{ diagnostics.length }}</small></h3><div class="task-filters"><label class="search-field"><Search class="tool-icon" /><input v-model="taskQuery" class="input" type="search" aria-label="检索诊断任务" placeholder="任务编号、名称或计算阶段" /></label><select v-model="taskStatus" class="input" aria-label="诊断任务状态"><option value="">全部状态</option><option v-for="(label, value) in statuses" :key="value" :value="value">{{ label }}</option></select><button v-if="taskQuery || taskStatus" class="btn icon-reset" type="button" title="清除筛选" aria-label="清除任务筛选" @click="taskQuery = ''; taskStatus = ''"><Close class="tool-icon" /></button></div></div><div class="table-scroll"><table class="table" aria-label="诊断任务"><thead><tr><th>任务</th><th>状态</th><th>进度</th><th>操作</th></tr></thead><tbody><tr v-for="row in filteredDiagnostics" :key="row.taskId"><td>{{ row.runTypeLabel }}<small>{{ row.createdAt?.replace('T', ' ').slice(0,16) }}</small></td><td>{{ statuses[row.status] || row.status }}<small>{{ hydrologyText(row.errorMessage || row.stageLabel) }}</small></td><td>{{ Math.round(row.progress) }}%</td><td class="actions"><button class="btn" :disabled="busy" @click="inspect(row)">详情 / 结果</button><button v-if="active(row)" class="btn" :disabled="busy || row.status === 'cancelling'" @click="operate(row, 'cancel')">取消</button><button v-if="row.status === 'completed'" class="btn" :disabled="busy" @click="operate(row, 'archive')">归档</button></td></tr><tr v-if="!filteredDiagnostics.length"><td colspan="4">{{ diagnostics.length ? '没有匹配的任务' : '暂无诊断任务' }}</td></tr></tbody></table></div>
        <div v-if="inspected" class="result"><h3 :title="inspected.taskId">{{ inspected.runTypeLabel }}结果</h3><p v-if="result?.pointQuery">{{ result.pointQuery.classification.point_type === 'RIVER' ? '河道切分汇流' : '坡面估算' }} · {{ result.pointQuery.location?.reachId || hydrologyText(result.pointQuery.quality.warning) }}</p><p v-if="result?.pointQuery?.location">选定位置 {{ result.pointQuery.location.clicked.map(v => v.toFixed(6)).join('，') }} · 实际断面 {{ result.pointQuery.location.queried.map(v => v.toFixed(6)).join('，') }} · 定位距离 {{ result.pointQuery.location.distanceM.toFixed(0) }} m · {{ result.pointQuery.location.reachId }}</p><div v-if="result?.pointQuery" class="chart"><BaseChart :option="chartOption" /></div>
          <SpatialResults v-if="result?.spatial" :task-id="inspected.taskId" :product="result.spatial" />
          <template v-if="result?.experiment"><h4>{{ diagnosticValue(result.experiment.evaluation.status) }}</h4><div class="table-scroll"><table class="table" aria-label="实验运行结果"><thead><tr><th>实验值</th><th>目标损失</th><th>验证 NSE</th><th>独立检验 NSE</th></tr></thead><tbody><tr v-for="row in result.experiment.runs" :key="row.value"><td>{{ row.value }}</td><td>{{ metric(row.optimizer.best_loss) }}</td><td>{{ metric(row.validationMetrics.mean_NSE) }}</td><td>{{ metric(row.independentTest.mean_NSE) }}</td></tr></tbody></table></div><button class="btn" :disabled="busy" @click="adoptCandidate">采用候选至率定工作区</button></template>
          <template v-if="result?.workflow"><h4>模型综合检验：{{ diagnosticValue(result.workflow.gate.status) }}</h4><div class="table-scroll"><table class="table gate-table" aria-label="验收检查"><thead><tr><th>检查项</th><th>结论</th><th>实际值</th><th>门槛</th></tr></thead><tbody><tr v-for="check in result.workflow.gate.checks" :key="check.check_id"><td>{{ hydrologyText(check.label) }}</td><td :class="check.passed ? 'gate-pass' : 'gate-review'">{{ check.passed ? '通过' : '未通过' }}</td><td>{{ diagnosticValue(check.actual) }}</td><td>{{ diagnosticThreshold(check.threshold) }}</td></tr></tbody></table></div>
            <div class="table-scroll"><table class="table" aria-label="年度水量平衡"><thead><tr><th>年</th><th>分组</th><th>降雨 mm</th><th>实际 ET mm</th><th>产流 mm</th><th>储量变化 mm</th><th>内部误差 mm</th><th>实测覆盖</th><th>NSE</th></tr></thead><tbody><tr v-for="row in result.workflow.continuous.annual" :key="row.year"><td>{{ row.year }}</td><td>{{ yearLabels.find(group => group.id === row.group)?.label }}</td><td>{{ metric(row.precipitation_mm) }}</td><td>{{ metric(row.actual_et_mm) }}</td><td>{{ metric(row.generated_runoff_mm) }}</td><td>{{ metric(row.land_storage_change_mm) }}</td><td>{{ metric(row.internal_balance_error_mm) }}</td><td>{{ metric(row.observed_coverage) }}</td><td>{{ metric(row.NSE) }}</td></tr></tbody></table></div><button class="btn" :disabled="busy" @click="adoptCandidate">采用候选至率定工作区</button>
          </template>
          <div class="actions"><button class="btn" :disabled="!result" @click="download(inspected.taskId, { request: inspected.runConfig, result })">导出结果 JSON</button><a v-if="result" class="btn" :href="reportUrl" download>下载报告包</a></div><details v-if="result?.warnings?.length"><summary>计算条件与说明</summary><p v-for="warning in result.warnings" :key="warning" class="notice">{{ hydrologyText(warning) }}</p></details><details><summary>计算请求与完整结果</summary><pre>{{ JSON.stringify({ request: inspected.runConfig, result }, null, 2) }}</pre></details><details><summary>计算日志</summary><pre>{{ log || '暂无日志' }}</pre></details>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Search, Close } from '@element-plus/icons-vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import RoutingWorkbench from '@/components/model/RoutingWorkbench.vue'
import SpatialResults from '@/components/model/SpatialResults.vue'
import { useAppStore } from '@/stores/app'
import { useCalibrationStore } from '@/stores/calibration'
import { modelApi, normalizeBackendTask, uniqueRequestId } from '@/services/modelBackendService'
import { calculationName } from '@/services/resultPresentation'
import { hydrologyText } from '@/services/hydrologyPresentation'
import { diagnosticValue, diagnosticThreshold } from '@/services/diagnosticPresentation'
import { filterTasks, taskStatusLabels } from '@/services/taskPresentation'
import { isEditableDiagnosticSource } from '@/services/diagnosticSourceService'
import { dailyClient } from '@/services/dailyOperationService'
import { ElMessageBox } from 'element-plus'

const app = useAppStore()
const calibration = useCalibrationStore()
const route = useRoute()
const router = useRouter()
const resultContext = computed(() => Boolean(route.query.source))
const resultMetadata = ref({})
const sourceName = row => calculationName(row, resultMetadata.value)
const supported = computed(() => app.usesModelBackend && app.basinId === 'nananxi-xufan' && app.modelScale === '1km')
const tabs = [{ id: 'point', label: '河道流量查询' }, { id: 'routing', label: '汇流与拓扑设置' }]
const tab = ref('point'), loading = ref(false), busy = ref(false), error = ref(''), message = ref('')
const pointStart = ref(''), pointEnd = ref(''), querySpatial = ref(null)
const tasks = ref([]), spatial = ref(null)
const pointSource = ref(''), experimentSource = ref(''), experimentMode = ref('multiSeed'), values = ref([42, 101, 2026]), budget = ref(4)
const point = reactive({ lon: null, lat: null, channelWidthM: 20 })
const warmupYear = ref(2014), workflowBounds = ref('80,120,160'), workflowSeeds = ref('42,101,2026')
const yearLabels = [{ id: 'CALIBRATION', label: '率定' }, { id: 'VALIDATION', label: '验证' }, { id: 'INDEPENDENT_TEST', label: '独立检验' }]
const yearText = reactive({ CALIBRATION: '2015,2016,2017,2018', VALIDATION: '2019,2020', INDEPENDENT_TEST: '2021' })
const inspected = ref(null), result = ref(null), log = ref('')
let poll = null, disposed = false, revision = 0, inspectRevision = 0
const statuses = taskStatusLabels
const taskQuery = ref(''), taskStatus = ref('')
const active = row => ['queued', 'running', 'cancelling'].includes(row.status)
const diagnostics = computed(() => tasks.value.filter(row => ['pointQuery', 'diagnosticExperiment', 'productionWorkflow', 'spatialExport'].includes(row.runConfig?.runType)))
const spatialTasks = computed(() => tasks.value.filter(row => row.runType === 'spatialExport' && row.runConfig?.inputs?.bundleVersionId === app.selectedInputBundleVersionId))
const spatialRunning = computed(() => spatialTasks.value.some(active))
const latestSpatial = computed(() => spatialTasks.value.find(row => ['completed', 'archived'].includes(row.status)))
const filteredDiagnostics = computed(() => filterTasks(resultContext.value ? diagnostics.value.filter(row=>row.runConfig?.runType==='pointQuery'&&row.runConfig.sourceTaskId===pointSource.value) : diagnostics.value, { query: taskQuery.value, status: taskStatus.value }))
const simulations = computed(() => tasks.value.filter(row => ['completed','archived'].includes(row.status) && ['simulation','stateInitialization','stateContinuation'].includes(row.runConfig?.runType)))
const calibrations = computed(() => tasks.value.filter(isEditableDiagnosticSource))
const customSearchSource = computed(() => calibrations.value.find(row => row.taskId === experimentSource.value)?.runConfig.calibration?.searchParameters != null)
const minimumBudget = computed(() => {
  const optimizer = calibrations.value.find(row => row.taskId === experimentSource.value)?.runConfig?.calibration?.optimizer
  return optimizer?.strategy === 'staged' ? (optimizer.algorithm === 'DIFFERENTIAL_EVOLUTION' ? 10 : 4) : optimizer?.algorithm === 'DIFFERENTIAL_EVOLUTION' ? 4 : 1
})
watch(minimumBudget, value => { budget.value = Math.max(budget.value, value) })
const selectedSource = computed(() => simulations.value.find(row => row.taskId === pointSource.value))
const independentRequired = ref(false)
watch(()=>[pointSource.value,point.lon,point.lat],()=>{independentRequired.value=false})
const outsidePeriod = computed(() => Boolean(selectedSource.value && (pointStart.value < selectedSource.value.runConfig.period.startTime.slice(0,16) || pointEnd.value > selectedSource.value.runConfig.period.endTime.slice(0,16))))
watch(selectedSource, async (source, previous) => { if (!source || source.taskId === previous?.taskId) return; inspected.value = null; result.value = null; querySpatial.value = null; point.lon = null; point.lat = null; pointStart.value = source.runConfig.period.startTime.slice(0,16); pointEnd.value = source.runConfig.period.endTime.slice(0,16); const id = source.taskId; try { const product = await modelApi(`spatial-products?bundleVersionId=${encodeURIComponent(source.runConfig.inputs.bundleVersionId)}`); if (selectedSource.value?.taskId === id) querySpatial.value = product } catch (failure) { error.value = failure.message } })
const reportUrl = computed(() => `${String(import.meta.env.VITE_MODEL_API_BASE).replace(/\/$/, '')}/tasks/${inspected.value?.taskId}/report-package`)
const metric = value => Number.isFinite(value) ? value.toFixed(4) : '缺测'
const pointSeries = computed(() => { const data = result.value?.pointQuery?.series; const rows = (data?.time || []).map((time, i) => [time, data.flow_cms[i]]).filter(([time]) => (!pointStart.value || time >= pointStart.value) && (!pointEnd.value || time <= pointEnd.value + ':00')); return rows })
const chartOption = computed(() => ({ tooltip: { trigger: 'axis' }, grid: { left: 65, right: 20, bottom: 45, top: 25 }, xAxis: { type: 'category', data: pointSeries.value.map(row => row[0]) }, yAxis: { type: 'value', name: 'm³/s' }, series: [{ type: 'line', showSymbol: false, data: pointSeries.value.map(row => row[1]), lineStyle: { color: '#127f78', width: 2 } }] }))
function choosePoint(coordinates) { [point.lon, point.lat] = coordinates; independentRequired.value=false; error.value='';tab.value = 'point' }
function resetValues() { values.value = experimentMode.value === 'multiSeed' ? [42, 101, 2026] : [80, 120, 160] }
function download(name, payload) { const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = `${name}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }
async function refresh(propagateError = false) {
  const key = app.contextKey, input = app.selectedInputBundleVersionId, token = ++revision
  if (!supported.value) return
  loading.value = true
  try {
    const [listing, metadata] = await Promise.all([modelApi('tasks'), modelApi('result-catalog')])
    const product = resultContext.value ? null : spatial.value?.bundleVersionId === input ? spatial.value : await modelApi(`spatial-products?bundleVersionId=${encodeURIComponent(input)}`, { timeoutMs: 30000 })
    if (disposed || token !== revision || key !== app.contextKey || input !== app.selectedInputBundleVersionId) return
    tasks.value = listing.tasks.filter(row => row.contextKey === key)
    resultMetadata.value = metadata.metadata
    if (spatial.value !== product) spatial.value = product
    if (!pointSource.value) pointSource.value = simulations.value.find(row => row.taskId === route.query.source)?.taskId || simulations.value[0]?.taskId || ''
    const selected = inspected.value?.taskId || route.query.task
    if (selected) { const latest = tasks.value.find(row => row.taskId === selected); if (latest) await inspect(latest, true) }
    error.value = ''
  } catch (failure) { if (!disposed && token === revision) error.value = failure.message; if (propagateError === true) throw failure }
  finally { if (!disposed && token === revision) { loading.value = false; clearTimeout(poll); if (tasks.value.some(active)) poll = setTimeout(refresh, 2500) } }
}
async function mutate(action) { if (busy.value) return; busy.value = true; error.value = ''; message.value = ''; const key = app.contextKey; try { const answer = await action(); if (disposed || key !== app.contextKey) return; await refresh(true); return answer } catch (failure) { if (!disposed && key === app.contextKey) error.value = failure.message } finally { busy.value = false } }
async function submitSpatial() {
  const row = await mutate(() => modelApi('tasks', { method: 'POST', body: {
    requestId: uniqueRequestId('SPATIAL'), runType: 'spatialExport', inputs: { bundleVersionId: app.selectedInputBundleVersionId },
  } }))
  if (row) { await inspect(row); message.value = '空间成果制作已提交。' }
}
async function submitPoint(riverOnly = true) {
  independentRequired.value=false;error.value='';message.value=''
  if (pointStart.value > pointEnd.value || pointStart.value < selectedSource.value?.runConfig.period.startTime.slice(0,16) || pointEnd.value > selectedSource.value?.runConfig.period.endTime.slice(0,16)) { error.value = '查询时段须落在来源模拟覆盖内；请调整时段或建立对应模拟。'; return }
  const cached = diagnostics.value.find(row => ['completed','archived'].includes(row.status) && row.runConfig?.runType === 'pointQuery' && row.runConfig.sourceTaskId === pointSource.value && row.runConfig.riverOnly === (riverOnly !== false) && ['lon', 'lat', 'channelWidthM'].every(key => row.runConfig.point?.[key] === point[key]))
  if (cached) { await inspect(cached); message.value = '已读取同一成果的点位流量。'; return }
  const row = await mutate(() => modelApi('tasks', { method: 'POST', body: { requestId: uniqueRequestId('POINT'), runType: 'pointQuery', sourceTaskId: pointSource.value, riverOnly: riverOnly !== false, point: { ...point } } }))
  if(error.value.includes('河道中间断面需独立复算'))independentRequired.value=true
  if (row) { await inspect(row); message.value = '已提交河道定位与流量查询。' }
}
async function openIndependentSimulation() {
  const source=selectedSource.value
  if(!source)return
  const identifier=source.runConfig.lineage?.parameterSchemeId || source.runConfig.lineage?.parameterVersionId
  busy.value=true;error.value=''
  try{
    let named=(await dailyClient.parameters()).find(row=>row.id===identifier||row.sourceTaskId===source.taskId)
    if(!named){
      const {value}=await ElMessageBox.prompt('参数名称','保存原计算参数',{inputPlaceholder:'填写参数名称',confirmButtonText:'保存并打开',cancelButtonText:'取消',inputValidator:value=>Boolean(value?.trim())||'请填写名称'})
      named=await dailyClient.saveParameter({name:value.trim(),sourceTaskId:source.taskId})
    }
    if(named)router.push({path:'/simulation',query:{task:source.taskId,start:pointStart.value,end:pointEnd.value,parameter:named.id}})
  }catch(failure){if(failure!=='cancel'&&failure!=='close')error.value=failure.message}
  finally{busy.value=false}
}
async function submitExperiment() { const row = await mutate(() => { const source = calibrations.value.find(item => item.taskId === experimentSource.value); if (!source) throw new Error('来源率定任务已不可用，请重新选择。'); const base = JSON.parse(JSON.stringify(source.runConfig)); base.calibration.optimizer.maxEvaluations = budget.value; return modelApi('tasks', { method: 'POST', body: { requestId: uniqueRequestId('EXPERIMENT'), runType: 'diagnosticExperiment', mode: experimentMode.value, values: values.value, baseRequest: base } }) }); if (row) { await inspect(row); message.value = '参数稳定性检验已提交。' } }
function parseList(value) { const fields = value.split(/[,，]/).map(item => item.trim()); if (fields.some(item => !item || !Number.isFinite(Number(item)))) throw new Error('请填写以逗号分隔的有限数值。'); return fields.map(Number) }
async function submitWorkflow() { const row = await mutate(() => { const source = calibrations.value.find(item => item.taskId === experimentSource.value); if (!source) throw new Error('来源率定任务已不可用，请重新选择。'); const base = JSON.parse(JSON.stringify(source.runConfig)); base.calibration.optimizer.maxEvaluations = budget.value; return modelApi('tasks', { method: 'POST', body: { requestId: uniqueRequestId('WORKFLOW'), runType: 'productionWorkflow', baseRequest: base, warmupYear: warmupYear.value, yearGroups: Object.fromEntries(yearLabels.map(group => [group.id, parseList(yearText[group.id])])), wlmUpperBounds: parseList(workflowBounds.value), seeds: parseList(workflowSeeds.value) } }) }); if (row) { await inspect(row); message.value = '模型综合检验已提交。' } }
async function operate(row, command) { await mutate(() => modelApi(`tasks/${row.taskId}/${command}`, { method: 'POST', body: {} })) }
async function inspect(row, propagateError = false) { const token = ++inspectRevision, key = app.contextKey; if (inspected.value?.taskId !== row.taskId) { result.value = null; log.value = '' } inspected.value = row; try { const logs = await modelApi(`tasks/${row.taskId}/log`); const answer = ['completed','archived'].includes(row.status) ? await modelApi(`tasks/${row.taskId}/result`) : null; if (!disposed && token === inspectRevision && key === app.contextKey) { log.value = logs.tail; result.value = answer } } catch (failure) { if (token === inspectRevision && !disposed && key === app.contextKey) error.value = failure.message; if (propagateError === true) throw failure } }
async function adoptCandidate() {
  error.value = ''; message.value = ''
  if (calibration.running || busy.value) { error.value = '率定工作区正在计算。'; return }
  const context = app.contextKey, version = result.value?.parameterVersionId
  if (!version || inspected.value?.runConfig?.context?.contextKey !== context) { error.value = '候选方案与当前流域不一致。'; return }
  busy.value = true
  try {
    const { versions } = await modelApi('parameter-versions')
    if (app.contextKey !== context) throw new Error('工作区已切换，请在原流域采用方案。')
    app.projectData.backendParameterVersions = versions
    calibration.setContext(app.basinId, app.modelScale, app.modelConfigId)
    if (await calibration.loadVersion(version) !== true) throw new Error(calibration.runMessage || '候选方案尚未登记。')
    app.updateWorkContext({ parameterVersionId: version })
    message.value = '已采用完整参数方案，蒸散发、初态和汇流条件一并载入。'
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
watch(() => route.query.task, () => { inspected.value = null; result.value = null; refresh() })
watch(() => route.query.source, () => {
  pointSource.value = ''
  inspected.value = null
  result.value = null
  message.value = ''
  tab.value = 'point'
  refresh()
})
watch(() => [app.contextKey, app.selectedInputBundleVersionId], () => { revision++; inspectRevision++; clearTimeout(poll); tasks.value = []; spatial.value = null; inspected.value = null; result.value = null; pointSource.value = ''; experimentSource.value = ''; error.value = ''; message.value = ''; refresh() })
onMounted(refresh)
onBeforeUnmount(() => { disposed = true; revision++; inspectRevision++; clearTimeout(poll) })
</script>

<style scoped>
.spatial-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:20px;padding:18px 20px;background:#f1f5ef;border:1px solid #dce5d8;border-radius:6px}.spatial-toolbar>div{margin-right:auto}.spatial-toolbar h3{margin:0 0 5px}.spatial-toolbar span{font-size:12px;color:#70816f}
.task-heading{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:14px}.task-heading h3{margin:0}.task-heading h3 small{font-size:11px;color:#758279;font-weight:400;margin-left:8px}.task-filters{display:flex;align-items:center;flex-wrap:wrap;gap:8px;max-width:100%}.search-field{position:relative;display:block;flex:1 1 220px;min-width:0}.search-field .tool-icon{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:#758279;pointer-events:none}.search-field input{padding-left:32px;width:100%}.task-filters select{width:126px}.icon-reset{width:34px;height:34px;padding:8px;display:grid;place-items:center}.icon-reset .tool-icon{margin:0}@media(max-width:600px){.task-filters{width:100%}.search-field{flex-basis:100%}}
.gate-table td{white-space:normal;overflow-wrap:anywhere;min-width:90px;max-width:280px;line-height:1.6}.gate-table td:nth-child(2){white-space:nowrap;min-width:54px}.gate-pass{color:#287156}.gate-review{color:#a16514}
.tool-icon{width:15px;height:15px;vertical-align:middle;margin-right:6px;flex-shrink:0}
.diagnostic-view{height:100%;overflow:auto;padding:22px 24px;background:#fff;color:#293c30}.heading{display:flex;align-items:center;gap:16px;flex-wrap:wrap}.eyebrow{display:block;font-size:11px;color:#849086;margin-bottom:5px}.heading h2{font-size:21px;font-weight:650;margin:0}.heading .btn{margin-left:auto}.qualification{color:#88723d;font-size:11px;border-left:1px solid #dedfce;padding-left:16px}.identity{display:flex;align-items:center;gap:18px;flex-wrap:wrap;font-size:12px;padding:14px 0 18px;color:#7d897f;overflow-wrap:anywhere}.identity b{font-weight:600;color:#45614e;margin-left:8px}.identity details{margin:0}.identity details div{padding-top:8px;font-size:11px}.identity summary{color:#7d897f}.tabs{display:flex;flex-wrap:wrap;border-bottom:1px solid #d7e0d7;gap:20px}.tabs button{border:0;background:none;padding:12px 2px;color:#738075;cursor:pointer;font-size:13px}.tabs .selected{color:#286849;border-bottom:2px solid #286849;font-weight:700}.section{padding:20px 0;border-bottom:1px solid #e2e8e2}.section h3{font-size:15px;margin:0 0 12px}.form{display:flex;flex-wrap:wrap;gap:14px;align-items:end;margin-bottom:16px}label{display:flex;flex-direction:column;gap:6px;font-size:12px;max-width:100%;min-width:120px}label.wide{flex:1 1 100%}.input{max-width:100%;min-width:0}.table-scroll{overflow:auto;margin-bottom:14px}.table{width:100%;font-size:12px}.table td,.table th{white-space:nowrap;vertical-align:middle}.table code,.table small{display:block;white-space:normal;overflow-wrap:anywhere;max-width:440px;font-size:11px;margin-top:5px}.actions{display:flex;gap:8px;flex-wrap:wrap}.notice{font-size:12px;color:#837449;line-height:1.6}.error{color:#b43745;font-size:13px;padding:10px 12px;background:#fff1ef;border-left:3px solid #c5524b}.message{color:#287156;font-size:13px}.chart{height:300px}.result{padding:16px 0}.result h4{font-size:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;max-height:360px;overflow:auto;background:#f2f5f0;padding:12px}details{margin-top:14px}summary{cursor:pointer;font-size:12px}@media(max-width:600px){.diagnostic-view{padding:16px 12px;height:auto;overflow:visible}.tabs{gap:16px 12px}.tabs button{padding:12px 0;font-size:12px}.form label{flex:1 1 140px}.identity{gap:10px;padding-bottom:14px}}
</style>
