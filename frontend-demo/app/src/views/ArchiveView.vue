<template>
  <section class="view-archive">
    <header class="archive-heading"><h2>计算成果</h2><button class="btn" type="button" :disabled="operationLoading" @click="refreshOperations">刷新</button></header>
    <section class="panel panel-pad daily-ledger">
      <div class="daily-filters"><select v-model="dailySchemeFilter" class="input" aria-label="计算方案"><option value="">全部方案</option><option v-for="row in schemes" :key="row.id" :value="row.id">{{ row.name }}</option><option value="daily">模拟与回算</option></select><select v-model="operationType" class="input" aria-label="计算类型"><option value="">全部计算</option><option v-for="(label,type) in dailyTypes" :key="type" :value="type">{{ label }}</option></select><input v-model="operationSearch" class="input" aria-label="查找计算" placeholder="查找计算名称或时间" /><span>{{ visibleOperations.length }} 次计算</span></div>
      <p v-if="operationError" class="warn-text" role="alert">{{ operationError }}</p>
      <div class="table-wrap"><table class="table daily-table"><thead><tr><th>计算</th><th>参数</th><th>状态</th><th>时间</th><th></th></tr></thead><tbody><tr v-for="row in visibleOperations" :key="operationId(row)" :class="{active:operationId(row)===operationId(activeDailyOperation)}"><td>{{ operationName(row) }}</td><td>{{ row.runConfig?.parameterSnapshot?.name || row.parameterSchemeName || savedParameters.find(item=>item.id===(row.runConfig?.parameterSchemeId||row.request?.parameterSchemeId||row.parameterSchemeId))?.name || '—' }}</td><td>{{ operationStatuses[row.status] || row.status }}</td><td>{{ formatCreated(row.createdAt) }}</td><td><button class="btn mini" type="button" @click="inspectOperation(row)">查看</button><button v-if="completedOperation(row)" class="btn mini" type="button" @click="restoreOperation(row)">打开条件</button></td></tr><tr v-if="!visibleOperations.length"><td colspan="5" class="empty-cell">{{ operationLoading ? '正在读取计算…' : '暂无计算成果' }}</td></tr></tbody></table></div>
      <DailyOperationProgress v-if="activeDailyOperation" :operation="activeDailyOperation" @cancel="cancelDailyOperation" />
      <p v-if="dailyResultError" class="warn-text" role="alert">{{ dailyResultError }}</p>
      <div v-if="dailyResult?.bestParameters" class="btn-row named-parameter"><button class="btn" type="button" @click="saveNamedParameter(activeDailyOperation)">保存参数</button><button class="btn" type="button" @click="openBestSimulation(activeDailyOperation,dailyResult)">采用此参数模拟</button></div>
      <DailyResultPanel v-if="dailyResult" :operation="activeDailyOperation" :result="dailyResult" :task-id="dailyResult.primaryTaskId || dailyResult.sourceTaskId" :comparison-options="dailyComparisonOptions" :title="operationName(activeDailyOperation)" />
      <details v-if="dailyResult?.primaryTaskId" class="daily-notes"><summary>名称与备注</summary><form @submit.prevent="saveDailyMetadata"><label>名称<input v-model="dailyMetadataDraft.name" class="input" maxlength="80" aria-label="计算成果名称" /></label><label>备注<textarea v-model="dailyMetadataDraft.notes" class="input" rows="3" maxlength="2000" aria-label="计算成果备注" /></label><button class="btn" :disabled="savingDailyMetadata">保存</button></form></details>
    </section>
    <details class="legacy-computations" :open="Boolean(route.query.task || selectedTaskId)"><summary>率定成果与历史计算 · {{ contextTasks.length }} 项</summary>
    <div class="metric-strip" aria-label="当前成果摘要">
      <div class="metric"><span>当前流域作业</span><b>{{ contextTasks.length }} 项</b></div>
      <div class="metric"><span>运行中</span><b>{{ runningTaskCount }}</b></div>
      <div class="metric"><span>完成/归档</span><b class="ok">{{ completedTaskCount }}</b></div>
      <div class="metric metric-current"><span>模型配置</span><b>{{ archive.activeModelConfigLabel }}</b></div>
      <ContextStatusMetric />
    </div>

    <section class="panel panel-pad task-ledger">
      <h3 class="panel-title">
        <span>历次计算</span>
      </h3>
      <div class="task-filters">
        <select class="input" v-model="schemeFilter" aria-label="按方案查看成果"><option value="">全部方案与历史计算</option><option v-for="row in schemes" :key="row.id" :value="row.id">{{ row.name }}</option><option value="historical">历史计算（未关联方案）</option></select>
        <select class="input" v-model="taskTypeFilter"><option value="">全部任务类型</option><option v-for="type in taskTypeOptions" :key="type" :value="type">{{ runTypeLabel(type) }}</option></select>
        <select class="input" v-model="taskStateFilter"><option value="">全部任务状态</option><option v-for="status in taskStateOptions" :key="status" :value="status">{{ taskStatusLabel(status) }}</option></select>
        <input class="input" v-model="taskKeyword" aria-label="检索成果" placeholder="成果名称、分组或备注" />
        <select class="input" v-model="collectionFilter" aria-label="成果分组筛选"><option value="">全部分组</option><option v-for="group in collections" :key="group" :value="group">{{ group }}</option></select>
        <button class="btn mini" :class="{ primary: favoriteFilter }" :aria-pressed="favoriteFilter" @click="favoriteFilter = !favoriteFilter">收藏</button>
        <button class="btn mini" :disabled="exporting || !successfulFiltered.length" @click="exportFiltered">{{ exporting ? '汇总中…' : '导出筛选汇总' }}</button>
        <button class="btn mini" @click="refreshMetadata">刷新整理</button>
        <span>{{ filteredTasks.length }} / {{ contextTasks.length }} 项</span>
      </div>
      <p v-if="catalogError" class="warn-text" role="alert">{{ catalogError }}</p>
      <div class="table-wrap"><table class="table task-table">
        <thead><tr><th>成果名称</th><th>任务类型</th><th>状态</th><th>分组 / 阶段</th><th>来源</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="task in filteredTasks" :key="task.taskId" :class="{ active: task.taskId === selectedTaskId }">
            <td class="product-cell" :title="task.taskId"><b>{{ resultMetadata[task.taskId]?.favorite ? '★ ' : '' }}{{ resultMetadata[task.taskId]?.name || `${task.lineage?.schemeName || '历史计算'} · ${task.runTypeLabel} · ${task.createdAt?.replace('T', ' ').slice(0,16)}` }}</b><small v-if="task.lineage?.schemeRevision">方案修订 {{ task.lineage.schemeRevision }}</small></td>
            <td>{{ task.runTypeLabel }}</td>
            <td><span class="tag" :class="taskStatusTag(task.status)">{{ taskStatusLabel(task.status) }}</span></td>
            <td>{{ resultMetadata[task.taskId]?.collection || (['completed', 'archived'].includes(task.status) ? '未分组' : `${task.stageLabel} · ${task.progress}%`) }}</td>
            <td>{{ task.resultSource === 'model-backend' ? '模型计算' : task.resultSource === 'frontend-fixture' ? '本地试算' : task.resultSource }}</td>
            <td>{{ formatCreated(task.createdAt) }}</td>
            <td>
              <button v-if="task.status === 'completed'" class="btn mini" type="button" @click="tasks.archiveTask(task.taskId)">归档</button>
              <button class="btn mini" type="button" @click="selectedTaskId = task.taskId">查看成果</button>
              <button v-if="['completed','archived'].includes(task.status)" class="btn mini" type="button" @click="openTask(task)">按原条件打开</button>
            </td>
          </tr>
          <tr v-if="!filteredTasks.length"><td colspan="7" class="empty-cell">当前筛选条件下没有统一任务记录</td></tr>
        </tbody>
      </table></div>
      <details v-if="selectedTask" class="task-inspector"><summary>冻结计算条件</summary><div class="task-inspector-fields">
        <b>{{ selectedTask.runTypeLabel }} · {{ selectedTask.taskId }}</b>
        <span>方案：{{ selectedTaskLineage.schemeName || '历史计算' }} · 修订 {{ selectedTaskLineage.schemeRevision || '—' }}</span>
        <span>来源任务：{{ selectedTaskLineage.sourceTaskId || selectedTask.sourceTaskId || '首次作业' }}</span>
        <span>资料版本：{{ selectedTaskLineage.dataVersionId || '—' }}</span>
        <span>场次集版本：{{ selectedTaskLineage.eventSetVersionId || '—' }}</span>
        <span>参数版本：{{ selectedTaskLineage.parameterVersionId || '—' }}</span>
        <span>状态版本：{{ selectedTaskLineage.stateVersionId || '—' }}</span>
        <span>配置版本：{{ selectedTaskLineage.modelConfigVersionId || '—' }}</span>
        <span>结果版本：{{ selectedTaskLineage.resultVersionId || selectedTask.resultVersionId || '待返回' }}</span>
        <span>时刻：{{ selectedTask.runConfig.issueTime || selectedTask.runConfig.targetTime || selectedTask.runConfig.startTime || '—' }}</span>
      </div></details>
      <section v-if="selectedTask?.resultSource === 'model-backend'" class="backend-result-inspector">
        <p v-if="taskResultError" class="warn-text">{{ taskResultError }}</p>
        <template v-if="taskResult">
          <div class="btn-row"><button v-if="taskResult.bestParameters || taskResult.parameters || taskResult.endStateVersionId" class="btn" @click="openStates">状态分布</button><button v-if="taskResult.stateCalculationTaskId || taskResult.endStateVersionId" class="btn" @click="router.push({ path: '/diagnostics', query: { source: taskResult.stateCalculationTaskId || selectedTask.taskId } })">河道流量</button><button v-if="taskResult.bestParameters || taskResult.parameters" class="btn" @click="saveNamedParameter()">保存参数</button><button v-if="selectedTask.runConfig?.sourcePlan || selectedTask.runConfig?.originalCalibrationPlan" class="btn" @click="rerunOriginal">原条件重算</button></div>
          <p v-if="taskResult.stateCalculationError" class="warn-text">最佳参数状态保存失败：{{ taskResult.stateCalculationError }}。可按最佳参数独立模拟后查看状态。</p>
          <ResultWorkbench :key="selectedTask.taskId" :task="selectedTask" :result="taskResult" :tasks="contextTasks" :metadata="resultMetadata" :definitions="app.backendCase?.parameterDefinitions || []" @saved="saveResultMetadata" @open="openTask" @simulate="openBestSimulation" />
        </template>
        <p v-else-if="!taskResultError && ['completed','archived'].includes(selectedTask.status)" class="loading-result">正在读取计算成果…</p>
      </section>
    </section>
    </details>

    <details v-if="legacyConditions.length" class="panel panel-pad"><summary>历史保存条件 · {{ legacyConditions.length }} 条</summary><div v-for="row in legacyConditions" :key="row.id" class="task-filters"><span>{{ row.name }}</span><button class="btn mini" @click="restoreLegacy(row)">打开条件草稿</button></div></details>
    <div v-if="archive.contextRows.length" class="body">
      <aside class="panel panel-pad filter-panel">
        <h3 class="panel-title">档案筛选</h3>
        <div class="context-card" aria-label="当前成果范围">
          <span>流域</span><b>{{ archive.activeDomainLabel }}</b>
          <span>配置</span><b>{{ archive.activeModelConfigLabel }}</b>
        </div>
        <div class="form-stack">
          <div class="field">
            <label>预报对象</label>
            <select class="input" v-model="archive.filterSection">
              <option value="">全部预报对象</option>
              <option v-for="name in sectionOptions" :key="name" :value="name">{{ name }}</option>
            </select>
          </div>
          <div class="field">
            <label>发布状态</label>
            <select class="input" v-model="archive.filterStatus">
              <option value="">全部</option>
              <option>待复核</option>
              <option>已发布</option>
              <option>已归档</option>
            </select>
          </div>
          <div class="field">
            <label>预报类型</label>
            <select class="input" v-model="archive.filterType">
              <option value="">全部</option>
              <option>实时报</option>
              <option>订正报</option>
              <option>会商报</option>
            </select>
          </div>
          <div class="field">
            <label>作业员</label>
            <select class="input" v-model="archive.filterOperator">
              <option value="">全部</option>
              <option v-for="name in operatorOptions" :key="name" :value="name">{{ name }}</option>
            </select>
          </div>
          <div class="field">
            <label>起报时刻从</label>
            <input class="input" type="datetime-local" v-model="archive.filterDateFrom" />
          </div>
          <div class="field">
            <label>起报时刻至</label>
            <input class="input" type="datetime-local" v-model="archive.filterDateTo" />
          </div>
          <div class="field">
            <label>关键字</label>
            <input class="input" v-model="archive.filter" placeholder="预报编号 / 预报对象 / 作业员" />
          </div>
        </div>
      </aside>

      <main class="center">
        <div class="panel panel-pad result-table-panel">
          <h3 class="panel-title">
            <span>旧预报成果兼容区</span>
            <span class="sub">{{ archive.filtered.length }} / {{ archive.contextRows.length }} 条</span>
          </h3>
          <div class="table-wrap"><table class="table archive-table">
            <thead>
              <tr>
                <th>预报编号</th>
                <th>预报类型</th>
                <th>预报对象</th>
                <th>起报</th>
                <th>洪峰流量</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="r in archive.filtered"
                :key="r.productId"
                :class="{ active: r.productId === archive.selected?.productId }"
                role="button"
                tabindex="0"
                @click="archive.select(r.productId)"
                @keydown.enter="archive.select(r.productId)"
                @keydown.space.prevent="archive.select(r.productId)"
              >
                <td class="product-cell"><b>{{ r.productId }}</b></td>
                <td>{{ r.type }}</td>
                <td>{{ r.section }}</td>
                <td>{{ r.start }}</td>
                <td><b class="hot">{{ r.peak }}</b></td>
                <td><span class="tag" :class="statusTag(r.status)">{{ r.status }}</span></td>
              </tr>
              <tr v-if="!archive.filtered.length">
                <td colspan="6" class="empty-cell">当前流域与模型配置下没有匹配的预报成果</td>
              </tr>
            </tbody>
          </table></div>
        </div>
        <div class="panel panel-pad hydro-panel" v-if="archive.selected">
          <h3 class="panel-title">
            <span>预报过程复盘</span>
            <span class="sub" v-if="compareRow">对比：{{ compareRow.productId }}</span>
          </h3>
          <div class="hydro">
            <HydrographChart
              :hydrograph="archive.selectedHydrograph"
              mode="archive"
              :show-prev="false"
              :overlay="compareHydro"
              height="100%"
            />
          </div>
        </div>
        <div class="panel panel-pad empty-selection" v-else>
          当前筛选条件下没有可查看的预报成果。
        </div>
      </main>

      <aside class="panel panel-pad detail-panel" v-if="archive.selected">
        <h3 class="panel-title">成果详情</h3>

        <div class="form-group-title">预报信息</div>
        <div class="detail-grid">
          <div><span>预报编号</span><b :title="archive.selected.productId">{{ archive.selected.productId }}</b></div>
          <div><span>预报类型</span><b>{{ archive.selected.type }}</b></div>
          <div><span>流域</span><b>{{ archive.selected.domainName }}</b></div>
          <div><span>模型配置</span><b :title="archive.selected.modelConfigId">{{ archive.selected.modelConfigLabel }}</b></div>
        </div>

        <div class="form-group-title">预报摘要</div>
        <div class="stat-box">
          <div class="stat-line"><span>预报对象</span><b>{{ archive.selected.section }}</b></div>
          <div class="stat-line"><span>起报时刻</span><b>{{ archive.selected.start }}</b></div>
          <div class="stat-line"><span>洪峰</span><b class="hot">{{ archive.selected.peak }} m³/s</b></div>
          <div class="stat-line"><span>峰现</span><b>{{ archive.selected.peakTime }}</b></div>
          <div class="stat-line"><span>发布状态</span><b>{{ archive.selected.status }}</b></div>
        </div>

        <div class="form-group-title">起报资料</div>
        <div class="stat-box compact input-summary">
          <div class="stat-line"><span>起报状态</span><b>{{ archive.selected.lineage.state.label }}</b></div>
          <div class="stat-line"><span>预报降水</span><b>{{ archive.selected.lineage.precipitation.label }}</b></div>
          <div class="stat-line"><span>雨水情资料</span><b>{{ archive.selected.lineage.observation.label }}</b></div>
        </div>

        <div class="form-group-title">发布记录</div>
        <ul class="trail">
          <li v-for="(t, i) in auditTrail" :key="i">
            <span class="t-time">{{ t.time }}</span>
            <span class="t-act">{{ t.action }}</span>
            <span class="t-who">{{ t.who }}</span>
          </li>
        </ul>

        <div class="btn-row action-row">
          <button class="btn primary" type="button" :disabled="!archive.selected.repro" @click="reload">
            采用此条件起报
          </button>
          <button class="btn" type="button" @click="pinCompare">
            {{ compareId === archive.selected.productId ? '取消对比' : '设为过程对比' }}
          </button>
        </div>

        <template v-if="compareRow && compareRow.productId !== archive.selected.productId">
          <div class="form-group-title">过程对比</div>
          <div class="stat-box">
            <div class="stat-line"><span>对比预报</span><b :title="compareRow.productId">{{ compareRow.productId }}</b></div>
            <div class="stat-line"><span>洪峰流量差</span><b class="hot">{{ peakDiff }} m³/s</b></div>
          </div>
        </template>
      </aside>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useArchiveStore } from '@/stores/archive'
import { useForecastStore } from '@/stores/forecast'
import { useAppStore } from '@/stores/app'
import { useTasksStore } from '@/stores/tasks'
import HydrographChart from '@/components/charts/HydrographChart.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ContextStatusMetric from '@/components/common/ContextStatusMetric.vue'
import ResultWorkbench from '@/components/model/ResultWorkbench.vue'
import { modelApi, modelBackendEnabled, normalizeBackendTask } from '@/services/modelBackendService'
import { csv, downloadText, summaryRows, SUMMARY_HEADER } from '@/services/resultPresentation'
import { dailyClient, operationId, operationLabels, operationStatuses, completedOperation } from '@/services/dailyOperationService'
import { useDailyOperation } from '@/composables/useDailyOperation'
import DailyResultPanel from '@/components/model/DailyResultPanel.vue'
import DailyOperationProgress from '@/components/model/DailyOperationProgress.vue'

const archive = useArchiveStore()
const forecast = useForecastStore()
const app = useAppStore()
const tasks = useTasksStore()
const router = useRouter()
const route = useRoute()
const operationRows=ref([]), operationSearch=ref(''), operationType=ref(''), dailySchemeFilter=ref(''), operationLoading=ref(false), operationError=ref(''), savedParameters=ref([])
const dailyTypes={eventSimulation:'洪水模拟',continuousSimulation:'连续模拟',continuation:'续算',hindcast:'单次回算',rollingHindcast:'滚动回算',calibration:'参数率定',manualTrial:'人工试调'}
const {operation:activeDailyOperation,result:dailyResult,error:dailyResultError,inspect:inspectDailyOperation,cancel:cancelDailyOperation,clear:clearDailyOperation}=useDailyOperation({contextKey:()=>app.contextKey,onChange:row=>{const id=operationId(row);operationRows.value=operationRows.value.map(item=>operationId(item)===id?row:item)}})
const operationName=row=>resultMetadata.value[row?.primaryTaskId]?.name||[row?.lineage?.schemeName || row?.name || operationLabels[row?.operation||row?.runType]||row?.runTypeLabel||'计算',row?.createdAt?.replace('T',' ').slice(0,16)].filter(Boolean).join(' · ')
const visibleOperations=computed(()=>operationRows.value.filter(row=>(!dailySchemeFilter.value||(dailySchemeFilter.value==='daily'?!row.lineage?.schemeId:row.lineage?.schemeId===dailySchemeFilter.value))&&(!operationType.value||(row.operation||row.runType)===operationType.value)&&(!operationSearch.value||`${operationName(row)} ${row.createdAt} ${row.parameterSchemeName||''}`.includes(operationSearch.value))))
const dailyComparisonOptions=computed(()=>operationRows.value.filter(completedOperation).map(row=>({id:operationId(row),name:operationName(row)})))
let operationRevision=0
async function refreshOperations(){
  const token=++operationRevision,key=app.contextKey;operationLoading.value=true;operationError.value=''
  try{const [rows,named]=await Promise.all([dailyClient.list(key),dailyClient.parameters()]);if(token!==operationRevision)return;operationRows.value=rows.sort((a,b)=>String(b.createdAt).localeCompare(String(a.createdAt)));savedParameters.value=named;if(route.query.operation&&operationId(activeDailyOperation.value)!==route.query.operation)await inspectDailyOperation(route.query.operation)}
  catch(failure){if(token===operationRevision)operationError.value=failure.message}
  finally{if(token===operationRevision)operationLoading.value=false}
}
function inspectOperation(row){selectedTaskId.value=null;inspectDailyOperation(operationId(row))}
async function restoreOperation(row){
  const id=operationId(row),kind=row.operation||row.runType
  if(['calibration','manualTrial'].includes(kind)){
    try{const payload=await dailyClient.result(id),scheme=row.runConfig?.request?.scheme;router.push({path:'/calibration',query:{schemeId:scheme?.id,taskId:payload.primaryTaskId}})}catch(failure){operationError.value=failure.message}
    return
  }
  if(kind==='continuation'){
    router.push({path:'/simulation',query:{source:row.runConfig?.sourceTaskId,continue:'1',operation:id,restore:'1',label:operationName(row)}})
    return
  }
  router.push({path:['hindcast','rollingHindcast'].includes(kind)?'/forecast':'/simulation',query:{operation:id,restore:'1'}})
}
onMounted(refreshOperations)
watch(()=>[app.contextKey,route.query.operation],()=>{clearDailyOperation();operationRows.value=[];refreshOperations();refreshMetadata()})
const compareId = ref(null)
const taskTypeFilter = ref('')
const taskStateFilter = ref('')
const taskKeyword = ref('')
const schemeFilter = ref(''), schemes = ref([]), legacyConditions = ref([])
const selectedTaskId = ref(null)
const taskResult = ref(null)
const taskResultError = ref('')
const resultMetadata = ref({}), collectionFilter = ref(''), favoriteFilter = ref(false), catalogError = ref(''), exporting = ref(false)
const dailyMetadataDraft=ref({name:'',notes:''}),savingDailyMetadata=ref(false)
watch(()=>dailyResult.value?.primaryTaskId,id=>{const saved=resultMetadata.value[id]||{};dailyMetadataDraft.value={name:saved.name||'',notes:saved.notes||''}})
async function saveDailyMetadata(){
  const id=dailyResult.value?.primaryTaskId;if(!id||savingDailyMetadata.value)return
  savingDailyMetadata.value=true;operationError.value=''
  try{const saved=resultMetadata.value[id]||{},row=await modelApi(`result-metadata/${encodeURIComponent(id)}`,{method:'POST',body:{name:dailyMetadataDraft.value.name,notes:dailyMetadataDraft.value.notes,collection:saved.collection||'',favorite:saved.favorite||false,revision:saved.revision||0}});resultMetadata.value={...resultMetadata.value,[id]:row}}
  catch(failure){operationError.value=failure.message}
  finally{savingDailyMetadata.value=false}
}
const collections = computed(() => [...new Set(contextTasks.value.map(row => resultMetadata.value[row.taskId]?.collection).filter(Boolean))].sort())
const successfulFiltered = computed(() => filteredTasks.value.filter(row => row.resultSource === 'model-backend' && ['completed', 'archived'].includes(row.status)))
async function refreshMetadata() {
  if (!modelBackendEnabled()) return
  catalogError.value = ''
  const key=app.contextKey
  try {
    const [catalog, named, legacy] = await Promise.all([modelApi('result-catalog'), modelApi('model-schemes'), modelApi('database/schemes')])
    if(key!==app.contextKey)return
    resultMetadata.value = catalog.metadata; schemes.value = named.schemes; legacyConditions.value = legacy.schemes
  }
  catch (failure) { catalogError.value = failure.message }
}
function saveResultMetadata(id, row) { resultMetadata.value = { ...resultMetadata.value, [id]: row } }
const formatCreated = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
async function exportFiltered() {
  if (exporting.value) return
  exporting.value = true; catalogError.value = ''
  const rows = [...successfulFiltered.value], metadata = { ...resultMetadata.value }, context = app.contextKey
  try {
    const output = [SUMMARY_HEADER]
    for (const task of rows) output.push(...summaryRows(task, await tasks.getResult(task.taskId), metadata[task.taskId]))
    downloadText(`${context.replaceAll(':', '-')}-summary.csv`, csv(output))
  } catch (failure) { catalogError.value = failure.message }
  finally { exporting.value = false }
}
onMounted(refreshMetadata)
let resultRequest = 0
watch([selectedTaskId, () => tasks.taskById(selectedTaskId.value)?.status, () => app.contextKey], async () => {
  const revision = ++resultRequest
  taskResult.value = null
  taskResultError.value = ''
  if (selectedTask.value?.resultSource !== 'model-backend' || !['completed', 'archived'].includes(selectedTask.value.status)) return
  try {
    const result = await tasks.getResult(selectedTaskId.value)
    if (revision === resultRequest) taskResult.value = result
  } catch (error) {
    if (revision === resultRequest) taskResultError.value = error.message
  }
})
const contextTasks = computed(() => { const childIds=new Set(operationRows.value.flatMap(row=>row.childTaskIds||[]));return tasks.tasksForContext(app.contextKey).filter(row=>!childIds.has(row.taskId)) })
const taskTypeOptions = computed(() => [...new Set(contextTasks.value.map((task) => task.runType))])
const taskStateOptions = computed(() => [...new Set(contextTasks.value.map((task) => task.status))])
const filteredTasks = computed(() => {
  const keyword = taskKeyword.value.trim().toLowerCase()
  return contextTasks.value.filter((task) => {
    const schemeId = task.lineage?.schemeId || task.runConfig?.lineage?.schemeId
    if (schemeFilter.value && (schemeFilter.value === 'historical' ? Boolean(schemeId) : schemeId !== schemeFilter.value)) return false
    if (taskTypeFilter.value && task.runType !== taskTypeFilter.value) return false
    if (taskStateFilter.value && task.status !== taskStateFilter.value) return false
    const metadata = resultMetadata.value[task.taskId] || {}
    if (collectionFilter.value && metadata.collection !== collectionFilter.value) return false
    if (favoriteFilter.value && !metadata.favorite) return false
    if (keyword && !`${task.taskId} ${task.sourceTaskId || ''} ${task.runTypeLabel} ${metadata.name || ''} ${metadata.collection || ''} ${metadata.notes || ''}`.toLowerCase().includes(keyword)) return false
    return true
  })
})
const selectedTask = computed(() => contextTasks.value.find((task) => task.taskId === selectedTaskId.value) || null)
watch(()=>[route.query.task,contextTasks.value.length],()=>{if(route.query.task&&contextTasks.value.some(row=>row.taskId===route.query.task))selectedTaskId.value=route.query.task},{immediate:true})
const selectedTaskLineage = computed(() => {
  if (!selectedTask.value) return {}
  const task = selectedTask.value
  const config = task.runConfig || {}
  return {
    ...(config.lineage || {}),
    ...(task.lineage || {}),
    sourceTaskId: task.sourceTaskId || config.sourceTaskId || config.lineage?.sourceTaskId || null,
    dataVersionId: config.inputs?.bundleVersionId || config.dataVersionId || config.inputBundleVersionId || config.observationDataId || config.lineage?.dataVersionId || null,
    parameterVersionId: config.parameterVersionId || config.lineage?.parameterVersionId || null,
    stateVersionId: config.stateVersionId || config.sourceStateVersionId || config.lineage?.stateVersionId || null,
    modelConfigVersionId: config.modelConfigVersionId || config.modelConfigId || config.lineage?.modelConfigVersionId || null,
    resultVersionId: task.resultVersionId || task.lineage?.resultVersionId || config.lineage?.resultVersionId || null
  }
})
const runningTaskCount = computed(() => contextTasks.value.filter((task) => ['queued', 'running'].includes(task.status)).length)
const completedTaskCount = computed(() => contextTasks.value.filter((task) => ['completed', 'archived'].includes(task.status)).length)

const sectionOptions = computed(() => [...new Set(archive.contextRows.map((row) => row.section))])
const operatorOptions = computed(() => [...new Set(archive.contextRows.map((row) => row.operator).filter(Boolean))])
const publishedCount = computed(() => archive.contextRows.filter((row) => row.status === '已发布').length)
const reproducibleCount = computed(() => archive.contextRows.filter((row) => row.repro).length)

const taskStatusLabels = { queued: '排队', running: '运行中', completed: '已完成', archived: '已归档', failed: '失败', cancelled: '已停止' }
const runTypeLabels = { continuousSimulation: '连续模拟', continuousCalibration: '连续时段率定', eventCalibration: '洪水场次率定', eventSimulation: '场次模拟', stateInitialization: '起算状态生成', stateContinuation: '状态推算', forecast: '洪水预报', pointQuery: '指定位置流量查询', diagnosticExperiment: '参数稳定性检验', productionWorkflow: '模型综合检验' }
runTypeLabels.stateImport = '状态导入'
runTypeLabels.spatialExport = '空间成果制作'
function runTypeLabel(type) { return runTypeLabels[type] || type }
function taskStatusLabel(status) { return taskStatusLabels[status] || status }
function taskStatusTag(status) {
  if (['completed', 'archived'].includes(status)) return 'ok'
  if (status === 'failed') return 'bad'
  if (status === 'cancelled') return 'muted-tag'
  return 'warn'
}
async function openTask(task) {
  const config = task.runConfig || {}
  if (['continuousSimulation','eventSimulation'].includes(task.runType)) {
    router.push({path:'/simulation',query:{task:task.taskId}})
    return
  }
  if(['continuousCalibration','eventCalibration'].includes(task.runType)){
    router.push({path:'/calibration',query:{schemeId:config.scheme?.id,taskId:task.taskId}})
    return
  }
  if (task.runType === 'stateImport') {
    router.push({path:'/state',query:{source:task.taskId,label:resultMetadata.value[task.taskId]?.name||'导入状态'}})
    return
  }
  if (['pointQuery', 'diagnosticExperiment', 'productionWorkflow', 'spatialExport'].includes(task.runType)) {
    router.push({ path: '/diagnostics', query: { task: task.taskId } })
    return
  }
  if (['stateInitialization', 'stateContinuation'].includes(task.runType)) {
    router.push({path:'/state',query:{source:task.taskId,label:resultMetadata.value[task.taskId]?.name||task.runTypeLabel}})
  } else if (task.runType === 'forecast') {
    router.push({ path: '/forecast', query: { task: task.taskId } })
  }
}
async function restoreLegacy(row) {
  try {
    const request = await modelApi(`database/schemes/${row.id}`)
    if(request.runType==='calibration')router.push({path:'/calibration',query:{legacyConditionId:row.id}})
    else if(request.runType==='simulation')router.push({path:'/simulation',query:{legacyConditionId:row.id,sourceTask:row.sourceTaskId}})
    else if(row.sourceTaskId)await openTask({...normalizeBackendTask({taskId:row.sourceTaskId,runConfig:request,runType:request.runType,resultSource:'model-backend'}),runType:request.runType})
  } catch (failure) { ElMessage.error(failure.message) }
}
async function openStates() {
  try {
    router.push({path:'/state',query:{source:selectedTask.value.taskId,label:resultMetadata.value[selectedTask.value.taskId]?.name||selectedTask.value.lineage?.schemeName||selectedTask.value.runTypeLabel}})
  } catch (failure) { ElMessage.warning(failure.message) }
}

async function rerunOriginal() {
  try {
    const config = selectedTask.value.runConfig
    const source = config.sourcePlan || { parameterVersionId: config.lineage?.parameterVersionId || taskResult.value.parameterVersionId }
    const payload = await modelApi(`parameter-versions/${encodeURIComponent(source.parameterVersionId)}/original-plan?operation=search`)
    const operation = await dailyClient.submit({ operation: 'calibration', request: payload.request, rainfallMethod: 'reuse', name: '原条件重算' })
    await refreshOperations()
    await inspectDailyOperation(operation.taskId)
  } catch (failure) { ElMessage.error(failure.message) }
}

async function saveNamedParameter(task=selectedTask.value){
  if(!task)return null
  try{
    const {value}=await ElMessageBox.prompt('参数名称','保存参数',{inputPlaceholder:'填写参数名称',confirmButtonText:'保存',cancelButtonText:'取消',inputValidator:value=>Boolean(value?.trim())||'请填写名称'})
    const row=await dailyClient.saveParameter({name:value.trim(),sourceTaskId:task.taskId});savedParameters.value=[row,...savedParameters.value.filter(item=>item.id!==row.id)]
    ElMessage.success('参数已保存');return row
  }catch(failure){if(failure!=='cancel'&&failure!=='close')ElMessage.error(failure.message);return null}
}
async function openBestSimulation(task, result) {
  try {
    const named=savedParameters.value.find(row=>row.sourceTaskId===task.taskId)||await saveNamedParameter(task)
    if(named)router.push({path:'/simulation',query:{parameter:named.id,task:result?.primaryTaskId||task.taskId}})
  } catch (failure) { ElMessage.error(failure.message) }
}

const pinnedCompareRow = computed(() => archive.rows.find((row) => row.productId === compareId.value) || null)
const compareRow = computed(() => {
  const current = archive.selected
  const pinned = pinnedCompareRow.value
  return archive.canCompare(current, pinned) ? pinned : null
})
const peakDiff = computed(() => {
  if (!archive.selected || !compareRow.value) return 0
  return archive.selected.peak - compareRow.value.peak
})
const compareHydro = computed(() => {
  if (!compareRow.value || compareRow.value.productId === archive.selected?.productId) return null
  return archive.hydroFor(compareRow.value)
})
const auditTrail = computed(() => {
  const row = archive.selected
  if (!row) return []
  const trail = [{ time: row.start, action: '完成预报计算', who: row.operator || '—' }]
  if (row.status === '待复核' || row.status === '已发布' || row.status === '已归档') {
    trail.push({ time: row.start, action: '提交复核', who: row.operator || '—' })
  }
  if (row.status === '已发布' || row.status === '已归档') {
    trail.push({ time: row.publishedAt || row.start, action: '复核通过并发布', who: row.reviewer || '预报复核员' })
  }
  if (row.status === '已归档') {
    trail.push({ time: row.publishedAt || row.start, action: '归档', who: '系统' })
  }
  return trail
})

watch(
  () => [app.basinId, app.modelConfigId],
  ([domainId, modelConfigId]) => {
    archive.setContext(domainId, modelConfigId)
    compareId.value = null
    selectedTaskId.value = null
    taskTypeFilter.value = ''
    taskStateFilter.value = ''
    taskKeyword.value = ''
    collectionFilter.value = ''
    favoriteFilter.value = false
  },
  { immediate: true }
)

watch(
  () => archive.selectedId,
  () => {
    if (!compareId.value || compareId.value === archive.selected?.productId) return
    if (archive.comparisonReason(archive.selected, pinnedCompareRow.value)) compareId.value = null
  }
)

function statusTag(status) {
  if (status === '已发布') return 'ok'
  if (status === '待复核') return 'warn'
  if (status === '已归档') return 'gray'
  return ''
}

function pinCompare() {
  const selected = archive.selected
  if (!selected) return
  compareId.value = compareId.value === selected.productId ? null : selected.productId
}

function reload() {
  const row = archive.selected
  if (!row || !row.repro) return

  app.setBasin(row.domainId)
  if (typeof app.setModelConfig === 'function') app.setModelConfig(row.modelConfigId, row.projectId)
  else app.setModelScale(row.modelScale)
  forecast.setContext(row.domainId, row.modelScale, row.modelConfigId)
  forecast.loadFromArchive(row)
  app.setRunId(row.runId)
  ElMessage.success('已带入该次预报的起报条件，可继续开展预报')
  router.push('/forecast')
}
</script>

<style scoped>
.daily-notes{border-top:1px solid #e1e8e0;padding-top:15px;font-size:12px;color:#5d7865}.daily-notes summary{cursor:pointer}.daily-notes form{display:grid;grid-template-columns:1fr 2fr auto;gap:12px;align-items:end;margin-top:12px}.daily-notes label{display:grid;gap:7px}.daily-notes .input{width:100%;min-width:0}.daily-notes textarea{resize:vertical}.named-parameter{margin-top:15px}@media(max-width:700px){.daily-notes form{grid-template-columns:1fr}}
.archive-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.archive-heading h2{margin:0;font-size:23px;color:#2b5141;font-family:'Noto Serif CJK SC','SimSun',serif}.daily-filters{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}.daily-filters select{width:150px}.daily-filters input{flex:1;min-width:180px}.daily-filters span{font-size:12px;color:#75867a}.daily-table{min-width:700px}.daily-table td{font-size:12px}.daily-ledger>.table-wrap{max-height:350px;overflow:auto}.daily-ledger>.daily-result{border-top:1px solid #e0e7df;margin-top:18px;padding-top:24px}.legacy-computations{margin-top:10px}.legacy-computations>summary{cursor:pointer;font-size:13px;color:#54705e;padding:12px 0}.legacy-computations>.metric-strip{margin:8px 0}
.view-archive {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: max-content;
  min-height: 0;
}
.task-ledger { overflow: visible; min-width: 0; }
.task-ledger>.table-wrap { max-height: 300px; overflow:auto; }
.task-inspector summary { cursor:pointer; }
.task-inspector-fields { display:flex; flex-wrap:wrap; gap:8px 18px; margin-top:12px; }
.loading-result { font-size:12px; color:#658375; padding:20px; }
.task-ledger .panel-title { padding-top: 4px; background: var(--panel); }
.task-filters { display: flex; flex-wrap:wrap; align-items: center; gap: 8px; margin: 14px 0; }
.task-filters select { width:150px; }
.task-filters input { flex:1; min-width:200px; }
.task-filters > span { color: #6a7c95; font-size: 12px; white-space: nowrap; }
.task-inspector { display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 8px 10px; border-top: 1px solid #dce6ee; background: #f6f9fb; color: #52647d; font-size: 11px; }
.task-inspector b { color: #173052; }
.task-table { min-width: 980px; }
.task-table td { vertical-align: middle; font-size: 11px; }
.btn.mini { min-height: 26px; padding: 0 7px; font-size: 10px; }
.tag.muted-tag { background: #eeeae6; color: #796a5f; }
.tag.bad { background: #fdeceb; color: #b33a34; }
.body {
  display: grid;
  grid-template-columns: minmax(228px, 250px) minmax(0, 1.25fr) minmax(300px, 330px);
  gap: 10px;
  min-height: 0;
}
.center {
  display: grid;
  grid-template-rows: minmax(150px, auto) minmax(0, 1fr);
  gap: 10px;
  min-height: 0;
  overflow: hidden;
}
.panel.panel-pad { min-height: 0; }
.filter-panel,
.detail-panel { overflow: auto; overflow-x: hidden; }
.filter-panel .form-stack,
.filter-panel .field,
.filter-panel .input { width: 100%; min-width: 0; max-width: 100%; }
.filter-panel input[type='datetime-local'] { font-size: 12px; }
.context-card {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 5px 8px;
  margin: 0 0 14px;
  padding: 9px;
  border-left: 3px solid var(--teal);
  background: #f3faf8;
  color: #587078;
  font-size: 11px;
}
.context-card b {
  overflow: hidden;
  color: #183d43;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.result-table-panel { overflow: auto; max-height: 280px; }
.result-table-panel .panel-title { position: sticky; top: 0; z-index: 2; background: var(--panel); }
.archive-table { min-width: 640px; }
.archive-table th,
.archive-table td { vertical-align: middle; }
.product-cell { max-width: 168px; }
.product-cell b {
  display: block;
  overflow: hidden;
  color: #223f5f;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty-cell {
  padding: 22px !important;
  color: #667b8c;
  text-align: center;
}
.hydro-panel {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
}
.hydro { min-height: 0; height: 100%; }
.empty-selection {
  display: grid;
  place-items: center;
  color: #62788a;
  font-size: 13px;
}
.sub {
  overflow: hidden;
  color: #6a7c95;
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-grid { margin-bottom: 12px; }
.detail-grid b { font-size: 12px; }
.input-summary b {
  overflow: hidden;
  max-width: 180px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trail {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.trail li {
  display: grid;
  gap: 2px;
  padding: 6px 8px;
  border: 1px solid #e8f0fa;
  border-radius: 5px;
  background: #fbfdff;
  font-size: 11px;
}
.t-time { color: #8aa0bb; }
.t-act { color: #173052; font-weight: 700; }
.t-who { color: #6a7c95; }
.action-row { margin-top: 12px; }
tbody tr { cursor: pointer; }
.tag.ok { background: #e6f7ed; color: #14833e; }
.tag.warn { background: #fff3e8; color: #c45a10; }
.tag.gray { background: #eef2f7; color: #6a7c95; }
.hot { color: var(--orange); }
.metric-current b {
  overflow: hidden;
  max-width: 260px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1280px) {
  .view-archive { height: auto; min-height: 100%; grid-template-rows: auto auto 1fr; }
  .body { grid-template-columns: 270px minmax(0, 1fr); }
  .detail-panel { grid-column: 1 / -1; }
  .filter-panel,
  .detail-panel,
  .center { overflow: visible; }
  .hydro-panel { min-height: 420px; }
}

@media (max-width: 760px) {
  .body { grid-template-columns: 1fr; }
  .task-filters { grid-template-columns: 1fr; }
  .task-filters > span { justify-self: start; }
  .detail-panel { grid-column: 1; }
  .metric-strip { grid-auto-flow: row; grid-auto-columns: auto; grid-template-columns: 1fr 1fr; }
  .metric { min-height: 42px; border-bottom: 1px solid var(--line); }
  .metric:nth-child(2n) { border-right: 0; }
  .metric:nth-last-child(-n + 2) { border-bottom: 0; }
}
</style>
