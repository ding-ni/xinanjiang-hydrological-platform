<template>
  <section class="simulation-view">
    <header class="page-heading"><h2>{{ continuing ? '继续模拟' : '洪水模拟' }}</h2><div><RouterLink to="/state?tab=library" class="btn">状态库</RouterLink> <RouterLink to="/archive" class="btn">计算成果</RouterLink></div></header>
    <div class="simulation-layout">
      <section class="panel panel-pad conditions">
        <form @submit.prevent="run">
          <template v-if="!continuing">
            <label for="simulation-parameter">采用参数 <button type="button" class="text-button" title="修改参数名称" :disabled="!parameterSchemeId || parameterSchemeId==='@result' || running" @click="renameParameter">✎</button><select id="simulation-parameter" v-model="parameterSchemeId" class="input" :disabled="loading || running" @change="rememberParameter"><option value="" disabled>选择已保存参数或计算记录</option><option v-if="sourceParameters" value="@result">{{ sourceParameters.name }} · 计算参数</option><option v-for="row in parameters" :key="row.id" :value="row.id">{{ row.name }}</option></select></label>
            <p v-if="sourceParameters && parameterSchemeId==='@result'" class="source-parameters">参数来自 {{ sourceParameters.name }}，未另建参数库记录。采用其{{ sourceParameters.statePolicy==='continuous-origin'?'连续起算':'分场预热' }}规则，预热 {{ sourceParameters.warmupDays }} 天；确认选择后点击开始模拟。</p>
            <RouterLink v-if="!loading && !parameters.length" to="/calibration" class="empty-link">进入参数率定</RouterLink>
            <div class="mode-switch" role="tablist" aria-label="模拟方式"><button type="button" role="tab" :aria-selected="mode === 'event'" :class="{active:mode === 'event'}" :disabled="running" @click="mode='event'">洪水场次</button><button type="button" role="tab" :aria-selected="mode === 'continuous'" :class="{active:mode === 'continuous'}" :disabled="running" @click="mode='continuous'">连续时段</button></div>
            <template v-if="mode === 'event'"><div class="selected-count">已选 {{ selectedEvents.length }} 场洪水<button type="button" class="text-button" :disabled="running || !selectedEvents.length" @click="selectedIds=[]">清空</button></div></template>
            <label v-else for="simulation-start">开始时间<input id="simulation-start" v-model="startTime" type="datetime-local" step="3600" class="input" :disabled="loading || running" required /></label>
          </template>
          <div v-else class="continuation-source"><span>承接计算</span><b>{{ sourceLabel }}</b><span v-if="sourceEnd">当前末状态 {{ hourText(sourceEnd) }}</span></div>
          <label v-if="continuing || mode === 'continuous'" for="simulation-end">计算至<input id="simulation-end" v-model="endTime" type="datetime-local" step="3600" class="input" :min="continuing ? hourInput(sourceEnd) : startTime" :disabled="loading || running" required /></label>
          <p v-if="pageError || error" class="failure" role="alert">{{ pageError || error }}</p>
          <ul v-if="operation?.issues?.length" class="issues"><li v-for="(issue,index) in operation.issues" :key="index">{{ preparationIssueText(issue) }}</li></ul>
          <button class="btn primary run-button" :disabled="!canRun">{{ running ? '正在计算…' : continuing ? '开始续算' : '开始模拟' }}</button>
        </form>
        <DailyOperationProgress :operation="operation" @cancel="cancel" />
        <NumericReadiness v-if="app.backendCase" />
      </section>
      <div class="main-column">
        <section v-if="!continuing && mode === 'event'" class="panel panel-pad flood-picker">
          <div class="flood-heading"><h3>选择洪水</h3><span>{{ floods.length }} 场</span></div>
          <div class="flood-filters"><select v-model="year" class="input" aria-label="洪水年份"><option value="">全部年份</option><option v-for="value in years" :key="value" :value="value">{{ value }} 年</option></select><input v-model="search" class="input" type="search" placeholder="洪水名称或日期" aria-label="检索洪水" /><button class="btn" type="button" :disabled="running || !filteredFloods.length" @click="selectVisible">选择当前列表</button></div>
          <p v-if="year || search || peakMinimum!=='' || peakMaximum!=='' || sortKey" class="filter-summary">匹配 {{ filteredFloods.length }} 场 <button class="text-button" @click="year='';search='';peakMinimum='';peakMaximum='';sortKey='';sortDirection=''">清除筛选和排序</button></p><div class="flood-table"><table class="table"><thead><tr><th>选用</th><th>洪水</th><th><FloodColumnHeader label="开始" column="start" :sort-key="sortKey" :direction="sortDirection" filter @sort="sortFloods"><select v-model="year" class="input"><option value="">全部年份</option><option v-for="value in years" :key="value" :value="value">{{ value }} 年</option></select></FloodColumnHeader></th><th><FloodColumnHeader label="结束" column="end" :sort-key="sortKey" :direction="sortDirection" @sort="sortFloods" /></th><th><FloodColumnHeader label="历时 · h" column="duration" :sort-key="sortKey" :direction="sortDirection" @sort="sortFloods" /></th><th><FloodColumnHeader label="实测洪峰 · m³/s" column="peak" :sort-key="sortKey" :direction="sortDirection" filter @sort="sortFloods"><input v-model="peakMinimum" class="input" type="number" placeholder="洪峰下限" /><input v-model="peakMaximum" class="input" type="number" placeholder="洪峰上限" /></FloodColumnHeader></th></tr></thead><tbody><tr v-for="row in pagedFloods" :key="row.id" :class="{selected:selectedIds.includes(row.id)}"><td><input v-model="selectedIds" type="checkbox" :value="row.id" :aria-label="`选用${row.name || row.id}`" :disabled="running" /></td><td>{{ row.name || row.id }}</td><td>{{ hourText(row.start) }}</td><td>{{ hourText(row.end) }}</td><td>{{ number(row.durationHours ?? (Date.parse(row.end)-Date.parse(row.start))/3600000) }}</td><td>{{ number(row.observedPeak ?? row.observedPeakFlowM3s) }}</td></tr><tr v-if="!filteredFloods.length"><td colspan="6" class="empty-cell">{{ loading ? '正在读取洪水目录…' : '暂无匹配洪水' }}</td></tr></tbody></table></div><footer class="flood-filters"><span>第 {{ floodPage }} / {{ floodPages }} 页</span><button class="btn" :disabled="floodPage===1" @click="floodPage--">上一页</button><button class="btn" :disabled="floodPage>=floodPages" @click="floodPage++">下一页</button></footer>
        </section>
        <DailyResultPanel :operation="operation" :result="result" :task-id="result?.primaryTaskId || result?.sourceTaskId" />
      </div>
    </div>
  </section>
</template>
<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { modelApi } from '@/services/modelBackendService'
import { activeOperation, belongsToOperationContext, dailyClient, dailyOperationSelection, historicalParameterId, hourInput, hourText, operationContext, operationId, preparationIssueText, readDailySelection, rememberedParameter, saveDailySelection } from '@/services/dailyOperationService'
import { useDailyOperation } from '@/composables/useDailyOperation'
import DailyOperationProgress from '@/components/model/DailyOperationProgress.vue'
import DailyResultPanel from '@/components/model/DailyResultPanel.vue'
import NumericReadiness from '@/components/model/NumericReadiness.vue'
import FloodColumnHeader from '@/components/model/FloodColumnHeader.vue'
import { filterFloodRows, sortFloodRows } from '@/services/floodTablePresentation'
const app = useAppStore(), route = useRoute()
const loading = ref(false), parameters = ref([]), floods = ref([]), parameterSchemeId = ref(''), selectedIds = ref([])
const mode = ref('event'), startTime = ref(''), endTime = ref(''), year = ref(''), search = ref(''), pageError = ref('')
const sourceEnd = ref(''), sourceLabel = ref('')
const sourceParameters=ref(null)
const continuing = computed(() => route.query.continue === '1' && Boolean(route.query.source))
const { operation, result, error, submitting, inspect, submit, cancel, clear } = useDailyOperation({contextKey:()=>app.contextKey,onChange:row=>saveDailySelection(app.contextKey,'simulation',{...readDailySelection(app.contextKey,'simulation'),operationId:operationId(row)})})
const running = computed(() => submitting.value || activeOperation(operation.value))
const selectedEvents = computed(() => floods.value.filter(row=>selectedIds.value.includes(row.id)))
const years = computed(() => [...new Set(floods.value.map(row=>String(row.start).slice(0,4)))].sort((a,b)=>b.localeCompare(a)))
const sortKey=ref(''),sortDirection=ref(''),peakMinimum=ref(''),peakMaximum=ref(''),floodPage=ref(1)
const filteredFloods = computed(() => sortFloodRows(filterFloodRows(floods.value.filter(row=>(!search.value || `${row.name || row.id} ${row.start} ${row.end}`.includes(search.value))),{year:year.value,minimum:peakMinimum.value,maximum:peakMaximum.value}),sortKey.value,sortDirection.value))
const floodPages=computed(()=>Math.max(1,Math.ceil(filteredFloods.value.length/10)))
const pagedFloods=computed(()=>filteredFloods.value.slice((floodPage.value-1)*10,floodPage.value*10))
function sortFloods(key,direction){sortKey.value=direction?key:'';sortDirection.value=direction}
watch([year,search,sortKey,sortDirection,peakMinimum,peakMaximum],()=>floodPage.value=1)
const canRun = computed(() => !loading.value && !running.value && (continuing.value ? Boolean(endTime.value) : Boolean(parameterSchemeId.value) && (mode.value==='event' ? selectedEvents.value.length>0 : Boolean(startTime.value && endTime.value))))
const number = value => value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(1) : '—'
async function renameParameter(){
  try{const row=parameters.value.find(item=>item.id===parameterSchemeId.value);const {value}=await ElMessageBox.prompt('填写参数名称','修改参数名称',{inputValue:row.name,inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80});const saved=await modelApi('parameter-schemes/'+row.id+'/rename',{method:'POST',body:{name:value.trim()}});row.name=saved.name}
  catch(failure){if(failure instanceof Error)pageError.value=failure.message}
}
function selectVisible() { selectedIds.value=[...new Set([...selectedIds.value,...filteredFloods.value.map(row=>row.id)])] }
let revision=0
watch(()=>[app.contextKey,route.query.source,route.query.operation,route.query.continue,route.query.task,route.query.legacyConditionId,route.query.sourceTask,route.query.parameter,route.query.event,route.query.parameterResult], async()=>{
  const token=++revision, key=app.contextKey; clear(); loading.value=true; pageError.value=''; floods.value=[]; parameters.value=[]; selectedIds.value=[]; sourceEnd.value=''
  sourceParameters.value=null
  const saved=readDailySelection(key,'simulation'); mode.value=saved.mode||'event'; startTime.value=saved.startTime||''; endTime.value=saved.endTime||''
  try {
    const sources=await Promise.allSettled([dailyClient.parameters(),dailyClient.floods(app.activeContext)])
    if(token!==revision) return
    const named=sources[0].status==='fulfilled'?sources[0].value:[],events=sources[1].status==='fulfilled'?sources[1].value:[]
    pageError.value=sources.filter(row=>row.status==='rejected').map(row=>row.reason.message).join('；')
    parameters.value=named.filter(row=>belongsToOperationContext(row,key)); floods.value=events.map(row=>({...row,id:row.id||row.eventId,start:row.start||row.startTime,end:row.end||row.endTime}))
    parameterSchemeId.value=rememberedParameter(parameters.value, route.query.parameter || readDailySelection(key,'parameter').id || saved.parameterSchemeId)
    selectedIds.value=(saved.selectedIds||[]).filter(id=>floods.value.some(row=>row.id===id))
    if(route.query.parameterResult){const snapshot=await modelApi('results/'+encodeURIComponent(route.query.parameterResult)+'/parameter-snapshot');if(token!==revision)return;sourceParameters.value=snapshot;parameterSchemeId.value='@result'}
    if(route.query.event){selectedIds.value=[String(route.query.event)].filter(id=>floods.value.some(row=>row.id===id));mode.value='event'}
    if(continuing.value){
      const [task,payload]=await Promise.all([modelApi(`tasks/${encodeURIComponent(route.query.source)}`),modelApi(`tasks/${encodeURIComponent(route.query.source)}/result`)])
      if(token!==revision)return
      sourceEnd.value=payload.stateTime||payload.hydrograph?.forecast?.at(-1)?.time||task.runConfig?.period?.endTime||''
      sourceLabel.value=route.query.label||task.lineage?.schemeName||'模拟成果'; endTime.value=''
    } else if(route.query.task || route.query.legacyConditionId){
      const task=route.query.legacyConditionId?{taskId:route.query.sourceTask,runConfig:await modelApi(`database/schemes/${encodeURIComponent(route.query.legacyConditionId)}`)}:await modelApi(`tasks/${encodeURIComponent(route.query.task)}`)
      if(token!==revision)return
      const request=task.runConfig||{}; mode.value=request.period?.events?.length?'event':'continuous';startTime.value=hourInput(route.query.start||request.period?.startTime);endTime.value=hourInput(route.query.end||request.period?.endTime)
      selectedIds.value=(request.period?.events||[]).map(row=>row.id)
      if(!route.query.parameterResult)parameterSchemeId.value=historicalParameterId(parameters.value,task,route.query.parameter)
    }
    const id=route.query.operation || (!route.query.parameterResult && !continuing.value && !route.query.event && !route.query.task && !route.query.legacyConditionId ? saved.operationId : '')
    if(id){
      await inspect(id)
      if(token!==revision)return
      const selection=dailyOperationSelection(operation.value,parameterSchemeId.value,{explicitHistory:Boolean(route.query.operation)})
      if(!selection.restore){clear();saveDailySelection(key,'simulation',{...saved,parameterSchemeId:selection.parameterId,operationId:null});return}
      if(operation.value?.runConfig){
        const request=operation.value?.runConfig||operation.value?.request||{}
        if(!continuing.value){mode.value=request.operation==='eventSimulation'?'event':'continuous';parameterSchemeId.value=rememberedParameter(parameters.value,selection.parameterId);selectedIds.value=(request.events||[]).map(row=>row.id);startTime.value=hourInput(request.period?.startTime)}
        endTime.value=hourInput(request.period?.endTime)
        saveDailySelection(key,'simulation',{parameterSchemeId:parameterSchemeId.value,mode:mode.value,startTime:startTime.value,endTime:endTime.value,selectedIds:selectedIds.value,operationId:id})
      }
    }
  } catch(failure){if(token===revision)pageError.value=failure.message}
  finally{await nextTick();if(token===revision)loading.value=false}
},{immediate:true})
watch([parameterSchemeId,mode,startTime,endTime,selectedIds],()=>{
  if(loading.value)return
  if(operation.value&&!activeOperation(operation.value))clear()
  saveDailySelection(app.contextKey,'simulation',{parameterSchemeId:parameterSchemeId.value,mode:mode.value,startTime:startTime.value,endTime:endTime.value,selectedIds:selectedIds.value,operationId:operationId(operation.value)})
},{deep:true})
async function run(){
  if(!canRun.value)return
  pageError.value=''
  const body=continuing.value?{operation:'continuation',sourceTaskId:route.query.source,period:{endTime:endTime.value},context:operationContext(app.activeContext)}
    :{operation:mode.value==='event'?'eventSimulation':'continuousSimulation',...(parameterSchemeId.value==='@result'?{parameterSourceResultId:route.query.parameterResult}:{parameterSchemeId:parameterSchemeId.value}),context:operationContext(app.activeContext),
      ...(mode.value==='event'?{events:selectedEvents.value.map(({id,start,end})=>({id,start,end}))}:{period:{startTime:startTime.value,endTime:endTime.value}})}
  await submit(body)
}
function rememberParameter(){saveDailySelection(app.contextKey,'parameter',{id:parameterSchemeId.value})}
</script>
<style scoped>
.simulation-view{display:grid;gap:18px;min-width:0}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.page-heading h2{margin:0;color:#284c40;font-size:23px;font-family:'Noto Serif CJK SC','SimSun',serif}.simulation-layout{display:grid;grid-template-columns:270px minmax(0,1fr);gap:18px;align-items:start}.conditions form{display:grid;gap:20px}.conditions label{font-size:12px;color:#536f61;display:grid;gap:8px}.conditions .input{width:100%;min-width:0}.mode-switch{display:flex;border:1px solid #dce5de;border-radius:5px;overflow:hidden}.mode-switch button{flex:1;padding:11px 5px;border:0;background:#f7faf7;color:#667d70;cursor:pointer}.mode-switch button.active{background:#e5efe8;color:#25634c;font-weight:700}.selected-count{display:flex;justify-content:space-between;font-size:13px;color:#365d4d}.text-button{border:0;background:transparent;color:#677d70;font-size:12px;cursor:pointer}.run-button{width:100%;min-height:40px}.main-column{display:grid;gap:18px;min-width:0}.flood-heading{display:flex;align-items:center;gap:15px;margin-bottom:14px}.flood-heading h3{margin:0;font-size:16px;color:#315144}.flood-heading span{font-size:12px;color:#7c8c80}.flood-filters{display:flex;gap:10px;margin-bottom:14px}.flood-filters select{width:125px}.flood-filters input{flex:1;min-width:100px}.flood-table{max-height:300px;overflow:auto}.table{min-width:660px}.table th{position:sticky;top:0;background:#f2f7f3;z-index:1}.table td{font-size:12px}.table tr.selected{background:#edf5ef}.empty-cell{text-align:center;padding:25px;color:#74877a}.failure,.issues{color:#a14735;font-size:12px;line-height:1.8;margin:0}.issues{padding-left:17px}.continuation-source{display:grid;gap:12px;color:#748679;font-size:12px}.continuation-source b{color:#345648;font-size:14px}.empty-link{font-size:12px;color:#35785b}@media(max-width:1050px){.simulation-layout{grid-template-columns:1fr}.conditions form{grid-template-columns:1fr 1fr;align-items:end}.conditions .mode-switch,.conditions .failure,.conditions .issues{grid-column:1/-1}.run-button{min-height:38px}}@media(max-width:620px){.conditions form{grid-template-columns:1fr}.flood-filters{flex-wrap:wrap}.flood-filters select{width:110px}.page-heading h2{font-size:20px}}
</style>
