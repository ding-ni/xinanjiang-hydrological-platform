<template>
  <section class="results-workspace">
    <header><h1>计算成果</h1><button class="btn" :disabled="loading" @click="refresh">刷新</button></header>
    <div class="results-tools">
      <nav><button v-for="item in [{id:'saved',name:'我的成果'},{id:'temporary',name:'临时计算'},{id:'recent',name:'历史记录'},{id:'trashed',name:'回收站'}]" :key="item.id" class="btn" :class="{primary:tab===item.id}" @click="tab=item.id">{{ item.name }}</button></nav>
      <input v-model="search" type="search" class="input" aria-label="查找成果" placeholder="名称、参数或计算日期" />
      <select v-model="schemeFilter" class="input" aria-label="按方案筛选"><option value="">全部方案</option><option v-for="row in schemeOptions" :key="row.id" :value="row.id">{{ row.name }}</option></select>
      <select v-model="type" class="input" aria-label="成果计算类型"><option value="">全部类型</option><option v-for="(label,key) in operationLabels" :key="key" :value="key">{{ label }}</option></select>
      <template v-if="tab==='temporary' || tab==='recent'"><label class="bulk-select"><input type="checkbox" :checked="allDiscardSelected" :disabled="!discardableRows.length || runningAction" @change="toggleAllDiscard" />全选可丢弃结果</label><button class="btn" :disabled="!selectedTrashIds.size || runningAction" @click="discardSelected">移入回收站（{{ selectedTrashIds.size }}）</button></template><template v-else-if="tab==='trashed'"><label class="bulk-select"><input type="checkbox" :checked="allTrashSelected" :disabled="!filtered.length || runningAction" @change="toggleAllTrash" />全选回收站结果</label><button class="btn" :disabled="!selectedTrashIds.size || runningAction" @click="purgeSelected">批量移除（{{ selectedTrashIds.size }}）</button></template>
    </div>
    <section v-if="tab==='trashed'" class="panel"><p>移入平台回收站时文件保留；从回收站移除后，未被引用的任务及自产状态会送入 Windows 回收站。</p><ArtifactCleanup @cleaned="refresh" /></section>
    <p v-if="actionMessage" role="status">{{ actionMessage }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="results-layout">
      <section class="panel results-list"><div class="list-count">{{ filtered.length }} 次计算</div>
        <div v-for="row in paged" :key="row.id" class="result-row-wrap"><label v-if="tab==='trashed' || tab==='temporary' || tab==='recent'" class="result-select" :aria-label="`选择 ${row.name}`"><input type="checkbox" :checked="selectedTrashIds.has(row.id)" :disabled="runningAction || (tab!=='trashed' && !discardable(row))" @click.stop @change="toggleTrash(row.id)" /></label><button class="result-row" :class="{selected:selectedId===row.id}" @click="inspect(row)">
          <b>{{ row.favorite?'★ ':'' }}{{ row.name }}</b><span>{{ operationLabels[row.type] || '水文计算' }} · {{ statusLabels[row.status] || row.status }} · {{ row.processCount }} 个过程</span><small>{{ dateLabel(row.createdAt) }}</small>
        </button></div>
        <p v-if="!paged.length" class="empty">{{ loading?'正在读取…':tab==='saved'?'暂无正式成果，可在计算完成后点击保存成果。':'暂无计算记录' }}</p>
        <footer v-if="filtered.length"><span>{{ page }} / {{ pages }} 页</span><button class="btn" :disabled="page<=1" @click="page--">上一页</button><button class="btn" :disabled="page>=pages" @click="page++">下一页</button></footer>
      </section>
      <section v-if="selected" class="result-detail">
        <header class="panel record-heading"><h2>{{ selected.name }}</h2><span>{{ operationLabels[selected.type] || '水文计算' }} · {{ dateLabel(selected.createdAt) }}</span><p v-if="selected.error" class="error">{{ selected.error }}</p><p v-if="loadingResult">正在读取过程…</p><DailyOperationProgress v-if="activeStatus" :operation="status" @cancel="cancel" /></header>
        <DailyResultPanel v-if="result" :operation="displayStatus" :result="result" :task-id="result.primaryTaskId || result.taskId || selectedId" :comparison-options="comparisons" title="雨量—流量过程" hide-save-bar hide-record-link @process-change="activeProcess=$event" />
        <section v-if="result" class="panel record-details">
          <div class="record-toolbar"><button class="btn" @click="saveParameter">保存参数</button><button class="btn" @click="simulateParameters">用这组参数模拟其他洪水</button><details><summary>更多操作</summary><button class="btn" :disabled="runningAction" @click="rerun">{{ repeatLabel }}</button><button class="btn" @click="saveAsScheme">另存为方案</button></details></div>
          <ResultSaveBar :result-id="selectedId" :suggestion="selected.name" hide-record-link @saved="refresh" @unavailable="clearSelected" @discarded="result=null;selected=null;selectedId='';refresh()" />
          <nav class="detail-tabs"><button v-for="row in detailTabs" :key="row.id" :class="{active:detailTab===row.id}" @click="detailTab=row.id">{{ row.name }}</button></nav>
          <CalculationEvaluation v-if="detailTab==='evaluation'" :result="result" />
          <CalculationParameters v-if="detailTab==='parameters'" :result="result" />
          <CalculationConditions v-if="detailTab==='settings'" :result="result" :request="calculationRequest" />
          <div v-if="detailTab==='timings'" class="timings"><p v-for="(value,key) in timingRows" :key="key">{{ timingLabels[key] }}：{{ value }} 秒</p><p>阶段可能包含内部子阶段，不将重叠耗时相加。</p></div>
          <p v-if="actionMessage" role="status">{{ actionMessage }}</p>
        </section>
      </section><section v-else class="panel result-placeholder">选择计算记录，查看洪水过程、评价、参数和状态。</section>
    </div>
  </section>
</template>
<script setup>
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ElMessageBox} from 'element-plus'
import {useAppStore} from '@/stores/app'
import {modelApi, isMissingResult} from '@/services/modelBackendService'
import { resultsPurged } from '@/services/resultRetentionService'
import {schemeDefinition} from '@/services/modelSchemeService'
import DailyResultPanel from '@/components/model/DailyResultPanel.vue'
import ArtifactCleanup from '@/components/model/ArtifactCleanup.vue'
import { cleanupConfirmation } from '@/services/artifactCleanupService'
import DailyOperationProgress from '@/components/model/DailyOperationProgress.vue'
import ResultSaveBar from '@/components/model/ResultSaveBar.vue'
import CalculationEvaluation from '@/components/model/CalculationEvaluation.vue'
import CalculationParameters from '@/components/model/CalculationParameters.vue'
import CalculationConditions from '@/components/model/CalculationConditions.vue'
import { comparableOperations } from '@/services/dailyOperationService'
const app=useAppStore(),route=useRoute(),router=useRouter()
const rows=ref([]),trashedSchemeIds=ref(new Set()),selectedId=ref(''),selected=ref(null),result=ref(null),status=ref(null),tab=ref('saved'),search=ref(''),type=ref(''),page=ref(1),loading=ref(false),loadingResult=ref(false),error=ref(''),runningAction=ref(false),actionMessage=ref(''),schemeFilter=ref(''),detailTab=ref('evaluation'),activeProcess=ref(''),selectedTrashIds=ref(new Set())
const operationLabels={calibration:'自动率定',manualTrial:'人工试调',eventSimulation:'洪水模拟',continuousSimulation:'连续模拟',simulation:'模拟',continuation:'继续模拟',rerun:'再次计算',eventCalibration:'场次率定',continuousCalibration:'连续率定',hindcast:'历史回算',rollingHindcast:'滚动回算',forecast:'洪水预报',manualForecast:'手动预报',automaticForecast:'自动预报'}
const statusLabels={completed:'已完成',archived:'已完成',running:'计算中',queued:'排队中',cancelling:'正在停止',cancelled:'已停止',failed:'未完成'}
const timingLabels={operationSeconds:'总耗时',queueSeconds:'排队',inputPreparationSeconds:'资料准备',preparationSeconds:'读取与检查',searchSeconds:'参数搜索',finalEvaluationSeconds:'最终评价',stateSavingSeconds:'状态生成与保存',simulationSeconds:'模型计算',statePersistenceSeconds:'状态文件保存',warmupSeconds:'预热产流',loadingSeconds:'读取资料',runoffSeconds:'评价期产流',routingSeconds:'全程汇流',checkpointSeconds:'末状态保存'}
const detailTabs=[{id:'evaluation',name:'全场评价'},{id:'parameters',name:'本组参数'},{id:'settings',name:'本次计算设置'},{id:'timings',name:'计算耗时'}]
const schemeOptions=computed(()=>[...new Map(rows.value.filter(row=>row.schemeId).map(row=>[row.schemeId,{id:row.schemeId,name:row.schemeName||row.schemeId}])).values()])
const filtered=computed(()=>rows.value.filter(row=>(tab.value==='recent'?row.retention!=='trashed':row.retention===tab.value)&&(!type.value||row.type===type.value)&&(!schemeFilter.value||row.schemeId===schemeFilter.value)&&[row.name,row.parameterName,row.createdAt].join(' ').toLowerCase().includes(search.value.trim().toLowerCase())))
const pages=computed(()=>Math.max(1,Math.ceil(filtered.value.length/10))),paged=computed(()=>filtered.value.slice((page.value-1)*10,page.value*10))
const discardable=row=>row?.retention==='temporary'&& !['queued','running','cancelling'].includes(row.status)
const discardableRows=computed(()=>filtered.value.filter(discardable))
const allDiscardSelected=computed(()=>discardableRows.value.length>0&&discardableRows.value.every(row=>selectedTrashIds.value.has(row.id)))
const allTrashSelected=computed(()=>tab.value==='trashed'&&filtered.value.length>0&&filtered.value.every(row=>selectedTrashIds.value.has(row.id)))
watch([tab,search,type,schemeFilter],()=>{page.value=1;if(tab.value!=='trashed')selectedTrashIds.value=new Set()})
watch(pages,value=>page.value=Math.min(page.value,value))
const activeStatus=computed(()=>['queued','running','cancelling'].includes(status.value?.status))
const displayStatus=computed(()=>({...status.value,name:selected.value?.name,operationId:selectedId.value.startsWith('OP-')?selectedId.value:undefined,taskId:selectedId.value,canonicalResultId:selectedId.value}))
const comparisons=computed(()=>comparableOperations(rows.value,rows.value,trashedSchemeIds.value,row=>row.name))
const calculationRequest=computed(()=>result.value?.frozenRequest||status.value?.runConfig?.request||status.value?.runConfig||{})
const repeatLabel=computed(()=>calculationRequest.value.runType==='calibration'&&result.value?.evaluationMode!=='manual'?'再次自动率定':'再次模拟')
const timingRows=computed(()=>Object.fromEntries(Object.entries(result.value?.timings||{}).filter(([key,value])=>timingLabels[key]&&typeof value==='number')))
const dateLabel=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'日期未登记'
let sequence=0,poll,disposed=false
async function refresh(){loading.value=true;error.value='';try{const [payload,discarded]=await Promise.all([modelApi('result-catalog?retention=all',{timeoutMs:60000}),modelApi('model-schemes?retention=trashed')]);if(disposed)return;rows.value=(payload.results||[]).filter(row=>!row.contextKey||row.contextKey===app.contextKey);trashedSchemeIds.value=new Set((discarded.schemes||[]).map(row=>row.id));const requested=route.query.result||route.query.operation||route.query.task;const wanted=requested&&rows.value.find(row=>row.id===requested||row.sourceTaskIds?.includes(requested));if(wanted&&selectedId.value!==wanted.id){tab.value=wanted.retention||'recent';await inspect(wanted)}else if(selected.value){const found=rows.value.find(row=>row.id===selectedId.value);if(found)selected.value=found;else clearSelected()}}catch(failure){error.value=failure.message}finally{loading.value=false}}
async function inspect(row){const token=++sequence;clearTimeout(poll);selected.value=row;selectedId.value=row.id;result.value=null;status.value=null;loadingResult.value=true;error.value='';actionMessage.value='';try{if(['completed','archived'].includes(row.status)){const payload=await modelApi('results/'+row.id,{timeoutMs:60000});if(token!==sequence||disposed)return;result.value=payload.result;status.value=payload.status}else{const current=await modelApi((row.id.startsWith('OP-')?'operations/':'tasks/')+row.id);if(token!==sequence||disposed)return;status.value=current;if(['queued','running','cancelling'].includes(current.status))poll=setTimeout(async()=>{await refresh();if(disposed||token!==sequence)return;const latest=rows.value.find(item=>item.id===row.id);if(latest)inspect(latest)},1500)}}catch(failure){if(token===sequence){if(isMissingResult(failure)){clearSelected();actionMessage.value='该计算记录已移除或文件缺失，请刷新计算列表'}else error.value=failure.message}}finally{if(token===sequence)loadingResult.value=false}}
function clearSelected(){sequence++;clearTimeout(poll);selected.value=null;selectedId.value='';result.value=null;status.value=null;loadingResult.value=false}
function renamed(metadata){selected.value={...selected.value,name:metadata.name,favorite:metadata.favorite,saved:metadata.retention==='saved',retention:metadata.retention||selected.value.retention,metadata};rows.value=rows.value.map(row=>row.id===selectedId.value?selected.value:row)}
async function cancel(){try{await modelApi((selectedId.value.startsWith('OP-')?'operations/':'tasks/')+selectedId.value+'/cancel',{method:'POST',body:{}});await refresh()}catch(failure){error.value=failure.message}}
async function rerun(){runningAction.value=true;error.value='';try{const task=await modelApi('operations',{method:'POST',body:{operation:'rerun',sourceTaskId:selectedId.value}});tab.value='recent';await refresh();const row=rows.value.find(item=>item.id===task.taskId);if(row)await inspect(row)}catch(failure){error.value=failure.message}finally{runningAction.value=false}}
async function saveParameter(){try{const {value}=await ElMessageBox.prompt('保存所选计算记录实际采用的产汇流参数','保存参数',{inputValue:selected.value.name,inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80});await modelApi('parameter-schemes',{method:'POST',body:{name:value.trim(),sourceTaskId:selectedId.value}});actionMessage.value='参数已保存，可在洪水模拟中选用。'}catch(failure){if(failure instanceof Error)error.value=failure.message}}
function simulateParameters(){router.push({path:'/simulation',query:{parameterResult:selectedId.value}})}
async function saveAsScheme(){try{const request=await modelApi('results/'+selectedId.value+'/scheme-conditions');const {value}=await ElMessageBox.prompt('以这次计算设置建立独立方案','另存为方案',{inputValue:selected.value.name,inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80});await modelApi('model-schemes',{method:'POST',body:{name:value.trim(),definition:schemeDefinition(request,'reuse'),calculationDraft:request,editor:{editorTrialMode:request.calibration?.evaluationMode==='manual'?'manual':'automatic'}}});actionMessage.value='方案已保存，可在参数率定中打开。'}catch(failure){if(failure instanceof Error)error.value=failure.message}}
function toggleTrash(id){const next=new Set(selectedTrashIds.value);next.has(id)?next.delete(id):next.add(id);selectedTrashIds.value=next}
function toggleAllDiscard(){const next=new Set(selectedTrashIds.value);if(allDiscardSelected.value)discardableRows.value.forEach(row=>next.delete(row.id));else discardableRows.value.forEach(row=>next.add(row.id));selectedTrashIds.value=next}
function toggleAllTrash(){const next=new Set(selectedTrashIds.value);if(allTrashSelected.value)filtered.value.forEach(row=>next.delete(row.id));else filtered.value.forEach(row=>next.add(row.id));selectedTrashIds.value=next}
async function discardSelected(){const ids=[...selectedTrashIds.value].filter(id=>discardable(rows.value.find(row=>row.id===id)));if(!ids.length)return;try{await ElMessageBox.confirm(`将选中的 ${ids.length} 条临时计算移入回收站？正式成果、参数和输入资料不会删除。`,'批量移入回收站',{confirmButtonText:'移入回收站',cancelButtonText:'取消'});runningAction.value=true;const failures=[];for(const id of ids){const row=rows.value.find(item=>item.id===id);try{await modelApi('result-retention/'+encodeURIComponent(id)+'/discard',{method:'POST',body:{revision:row.retentionRevision}})}catch(failure){failures.push(`${row.name}：${failure.message}`)}}selectedTrashIds.value=new Set();await refresh();if(failures.length)actionMessage.value=`已移入 ${ids.length-failures.length} 条，${failures.length} 条未处理：${failures.join('；')}`;else actionMessage.value=`已将 ${ids.length} 条临时计算移入回收站。`}catch(failure){if(failure instanceof Error)error.value=failure.message}finally{runningAction.value=false}}
async function purgeSelected(){
  const ids=[...selectedTrashIds.value];if(!ids.length||runningAction.value)return
  runningAction.value=true;error.value=''
  try{
    const plan=await modelApi('result-cleanup?ids='+encodeURIComponent(ids.join(',')),{timeoutMs:60000})
    await ElMessageBox.confirm(cleanupConfirmation(plan),'预览回收站清理',{confirmButtonText:'确认移入 Windows 回收站',cancelButtonText:'取消',customClass:'artifact-cleanup-confirm'})
    const outcome=await modelApi('result-cleanup',{method:'POST',body:{ids,previewSHA256:plan.previewSHA256},timeoutMs:60000})
    resultsPurged(outcome)
    selectedTrashIds.value=new Set();clearSelected()
    await refresh();actionMessage.value=`已移除 ${ids.length} 条回收站记录，实际移走 ${outcome.movedArtifacts.length} 项文件/目录。`
  }catch(failure){if(failure instanceof Error)error.value=failure.message}finally{runningAction.value=false}
}
watch(()=>[app.contextKey,route.query.result,route.query.operation,route.query.task],refresh,{immediate:true})
onBeforeUnmount(()=>{disposed=true;sequence++;clearTimeout(poll)})
</script>
<style scoped>
.result-row-wrap{display:flex;align-items:stretch;border-bottom:1px solid #e3ebe6}.result-select{display:flex;align-items:flex-start;padding:18px 0 0 12px}.bulk-select{display:inline-flex;gap:6px;align-items:center;color:#587266;font-size:13px}
.results-workspace{display:grid;gap:16px;min-width:0;color:#31574a}.results-workspace>header{display:flex;align-items:center;justify-content:space-between}h1{font-size:25px;margin:0}.results-tools,.results-tools nav{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.results-tools>input{flex:1;min-width:150px}.results-tools>select{max-width:180px}.results-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:16px;align-items:start}.results-list{overflow:hidden}.list-count{padding:14px;color:#778a7d;border-bottom:1px solid #e3ebe6}.result-row{display:grid;gap:8px;padding:16px;width:100%;border:0;border-bottom:1px solid #e3ebe6;background:white;cursor:pointer;text-align:left;color:#315a46}.result-row.selected{background:#ecf5ee;box-shadow:inset 3px 0 #408365}.result-row b{font-size:15px;overflow-wrap:anywhere}.result-row span,.result-row small{color:#788a7e;font-size:13px}.results-list footer{display:flex;align-items:center;gap:6px;padding:12px}.results-list footer>span{margin-right:auto}.result-detail{display:grid;gap:14px;min-width:0}.record-heading{padding:16px}.record-heading h2{font-size:18px;margin:0 0 8px}.record-heading>span{font-size:13px;color:#788b7f}.record-details{padding:16px;min-width:0}.record-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.record-toolbar details summary{cursor:pointer;color:#6e8476;padding:10px}.record-toolbar details[open]{display:flex;gap:8px}.detail-tabs{display:flex;gap:6px;border-bottom:1px solid #dfe9e2;flex-wrap:wrap}.detail-tabs button{padding:12px;border:0;background:none;cursor:pointer;color:#708578}.detail-tabs button.active{color:#286b4c;border-bottom:2px solid #39805e}.timings{padding:16px;color:#6b8073}.error{color:#a64835}.empty,.result-placeholder{padding:32px;color:#7b8e80;line-height:1.8}.results-workspace :deep(.btn),.input{min-height:36px;font-size:14px}@media(max-width:1100px){.results-layout{grid-template-columns:1fr}.results-list{max-height:260px;overflow:auto}}
</style>
<style>.artifact-cleanup-confirm .el-message-box__message{white-space:pre-line;max-height:55vh;overflow:auto;overflow-wrap:anywhere}</style>
