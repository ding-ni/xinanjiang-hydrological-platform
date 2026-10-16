<template>
  <section class="river-sections"><header><div><span>计算成果 / 断面流量</span><h1>{{ resultName }}</h1></div><RouterLink class="btn" :to="{path:'/archive',query:{result:resultId}}">返回成果</RouterLink></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="selection-bar panel"><label>洪水过程<select v-model="processId" class="input" aria-label="断面查询洪水"><option value="" disabled>请选择洪水或连续过程</option><option v-for="row in processes" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><div v-if="process"><b>{{ hour(process.start) }} → {{ hour(process.end) }}</b><span>查询范围采用本次洪水，不重新指定计算时段。</span></div></div>
    <p v-if="loading" class="notice" role="status">正在读取成果与过程…</p><p v-else-if="process && !process.canQuery" class="notice">{{ process.queryUnavailableReason }}</p>
    <div v-else-if="process" class="query-layout"><aside class="panel section-controls"><h2>选择查询断面</h2><select v-model="sectionId" class="input" size="6" aria-label="已保存断面"><option v-for="row in sections" :key="row.id" :value="row.id">{{ row.name }}{{ row.compatible ? '' : '（需重新定位）' }}</option></select><div v-if="section" class="section-facts"><b>{{ section.name }}</b><span>{{ section.definition.queried.map(value=>value.toFixed(6)).join('，') }}</span><p v-if="!section.compatible" class="error">断面与当前河网不匹配，请重新定位并另存。</p><button v-if="!section.predefined" class="btn" :disabled="busy" @click="rename">修改断面名称</button></div><button class="btn primary" :disabled="busy || !section?.compatible" @click="query()">{{ busy ? '正在计算该断面…' : '查看断面流量' }}</button>
      <div class="new-section"><h2>保存新断面</h2><p>在河道地图点击位置，再选择“选用此位置”。</p><p v-if="locating" role="status">正在定位到河道…</p><template v-if="location"><p>已定位到河道，距离点击位置 {{ location.distanceM.toFixed(0) }} 米。</p><label>断面名称<input v-model.trim="sectionName" class="input" maxlength="80" placeholder="填写熟悉的断面名称" aria-label="新断面名称" /></label><button class="btn" :disabled="busy || !sectionName" @click="saveSection">保存断面</button></template></div></aside>
      <div class="river-main"><section v-if="stage && !answer" class="query-progress panel" role="status"><b>{{ section?.name }} · {{ process.name }}</b><p>{{ stage }}</p><p>已用 {{ elapsed }} 秒 <span v-if="queryTask?.remainingSeconds!=null">· 约计剩余 {{ queryTask.remainingSeconds }} 秒（依据同源查询）</span><span v-else>· 首次处理，正在估计</span></p><p v-if="queryTask?.totalSteps">本阶段已整理 {{ queryTask.completedSteps }} / {{ queryTask.totalSteps }} 个时步</p><p>查询窗口：{{ hour(process.start) }} 至 {{ hour(process.end) }}</p><p v-if="queryTask?.calculationStart || process.trajectoryStart">必要演算：{{ hour(queryTask?.calculationStart || process.trajectoryStart) }} 至 {{ hour(queryTask?.calculationEnd || process.trajectoryEnd) }}，按原起算条件恢复汇流。</p><button v-if="busy && queryTask?.taskId" class="btn" @click="stopQuery">停止计算</button></section><div class="panel river-map"><p v-if="mapError" class="error">{{ mapError }} <button class="btn" @click="loadSpatial">重试地图</button></p><p v-if="!spatial && !mapError">正在读取本次成果河网…</p><RoutingWorkbench v-if="spatial" :spatial="spatial" query-only :queried-point="location?.queried || section?.definition.queried" visible @point="choosePoint" /></div><section v-if="answer" class="panel section-answer"><h2>{{ section?.name || answer.selection.sectionName }} · {{ process.name }}</h2><p>{{ resultName }} · {{ hour(process.start) }} 至 {{ hour(process.end) }}</p><div class="answer-toolbar"><span>{{ cacheHit ? '已读取保存的断面过程' : '断面过程已计算保存' }}</span><button class="btn" @click="exportFlow">导出流量过程</button></div><div class="flow-chart"><BaseChart :option="chart" /></div></section></div>
    </div><button v-if="needsIndependent" class="btn" @click="independent">按原参数建立独立模拟</button>
  </section>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { modelApi } from '@/services/modelBackendService'
import { downloadText, csv } from '@/services/resultPresentation'
import RoutingWorkbench from '@/components/model/RoutingWorkbench.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
const route=useRoute(),router=useRouter(),resultId=computed(()=>String(route.params.id))
const resultName=ref('计算成果'),processes=ref([]),processId=ref(''),sections=ref([]),sectionId=ref(''),spatial=ref(null),location=ref(null),clicked=ref(null),sectionName=ref(''),answer=ref(null),error=ref(''),mapError=ref(''),stage=ref(''),loading=ref(false),busy=ref(false),locating=ref(false),cacheHit=ref(false)
const process=computed(()=>processes.value.find(row=>row.id===processId.value)),section=computed(()=>sections.value.find(row=>row.id===sectionId.value))
const needsIndependent=computed(()=>error.value.includes('独立复算')||error.value.includes('完整产流'))
const hour=value=>String(value||'').replace('T',' ').slice(0,16)
let sequence=0,locationSequence=0,querySequence=0,disposed=false
watch(resultId,async id=>{const token=++sequence;loading.value=true;error.value='';answer.value=null;processes.value=[];processId.value='';try{const [payload,catalog]=await Promise.all([modelApi('result-catalog/'+encodeURIComponent(id)+'/processes',{timeoutMs:60000}),modelApi('result-catalog')]);if(token!==sequence)return;processes.value=payload.processes;resultName.value=catalog.results.find(row=>row.id===id)?.name||'计算成果';processId.value=payload.processes.find(row=>row.id===route.query.process)?.id||(payload.processes.length===1?payload.processes[0].id:'')}catch(failure){if(token===sequence)error.value=failure.message}finally{if(token===sequence)loading.value=false}},{immediate:true})
watch(processId,async()=>{const token=++sequence;++querySequence;++locationSequence;answer.value=null;error.value='';stage.value='';busy.value=false;locating.value=false;location.value=null;clicked.value=null;sections.value=[];sectionId.value='';spatial.value=null;if(!process.value?.canQuery)return;try{const payload=await modelApi('river-sections?'+new URLSearchParams({resultId:resultId.value,processId:processId.value}),{timeoutMs:60000});if(token!==sequence)return;sections.value=payload.sections;sectionId.value=(payload.sections.find(row=>row.id===route.query.section)||payload.sections.find(row=>row.id==='SECTION-XUFAN'))?.id||''}catch(failure){if(token===sequence)error.value=failure.message}loadSpatial()})
watch(sectionId,()=>{++querySequence;busy.value=false;answer.value=null;stage.value='';location.value=null;clicked.value=null})
async function loadSpatial(){const token=sequence;mapError.value='';try{const task=await modelApi('tasks/'+process.value.sourceTaskId);const row=await modelApi('spatial-products?'+new URLSearchParams({bundleVersionId:task.runConfig.inputs.bundleVersionId}),{timeoutMs:60000});if(token===sequence)spatial.value=row}catch(failure){if(token===sequence)mapError.value=failure.message}}
async function choosePoint(coordinates){const token=++locationSequence;location.value=null;clicked.value={lon:coordinates[0],lat:coordinates[1]};locating.value=true;error.value='';try{const row=await modelApi('river-sections/locate',{method:'POST',body:{resultId:resultId.value,processId:processId.value,point:clicked.value},timeoutMs:60000});if(token===locationSequence)location.value=row}catch(failure){if(token===locationSequence)error.value=failure.message}finally{if(token===locationSequence)locating.value=false}}
async function saveSection(){busy.value=true;error.value='';try{const row=await modelApi('river-sections',{method:'POST',body:{name:sectionName.value,resultId:resultId.value,processId:processId.value,point:clicked.value},timeoutMs:60000});sections.value=[...sections.value,row];sectionId.value=row.id;sectionName.value='';location.value=null}catch(failure){error.value=failure.message}finally{busy.value=false}}
async function rename(){try{const row=section.value;const {value}=await ElMessageBox.prompt('填写断面名称','修改断面名称',{inputValue:row.name,inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80});const saved=await modelApi('river-sections/'+row.id+'/rename',{method:'POST',body:{name:value.trim(),revision:row.revision}});sections.value=sections.value.map(item=>item.id===saved.id?{...saved,compatible:row.compatible}:item)}catch(failure){if(failure instanceof Error)error.value=failure.message}}
const queryTask=ref(null),clock=ref(Date.now()),queryStarted=ref(0)
const clockTimer=setInterval(()=>clock.value=Date.now(),1000)
const elapsed=computed(()=>Math.max(0,Math.round((clock.value-(Date.parse(queryTask.value?.startedAt||queryTask.value?.createdAt)||queryStarted.value||clock.value))/1000)))
const pendingKey=()=>['hydrology.section',resultId.value,processId.value,sectionId.value].join('.')
async function query(resumeId){
  const token=++querySequence,key=pendingKey();busy.value=true;error.value='';answer.value=null;stage.value='准备读取断面过程';queryStarted.value=Date.now();queryTask.value=null
  try{
    let task=typeof resumeId==='string'?await modelApi('tasks/'+resumeId):await modelApi('result-section-queries',{method:'POST',body:{resultId:resultId.value,processId:processId.value,sectionId:sectionId.value},timeoutMs:60000})
    if(disposed||token!==querySequence)return
    cacheHit.value=Boolean(task.cacheHit)
    if(task.taskId)try{localStorage.setItem(key,task.taskId)}catch{}
    while(['queued','running','cancelling'].includes(task.status)){
      if(disposed||token!==querySequence)return
      queryTask.value=task;stage.value=task.stageLabel||'正在计算断面汇流'
      await new Promise(resolve=>setTimeout(resolve,1000));task=await modelApi('tasks/'+task.taskId)
    }
    if(disposed||token!==querySequence)return
    queryTask.value=task
    if(!['completed','archived'].includes(task.status))throw new Error(task.errorMessage||'断面查询已停止')
    const value=task.result || await modelApi('result-section-queries/'+task.taskId,{timeoutMs:60000})
    if(token===querySequence){answer.value=value;stage.value=''}
  }catch(failure){if(token===querySequence)error.value=failure.message}
  finally{if(token===querySequence)busy.value=false}
}
async function stopQuery(){if(!queryTask.value?.taskId)return;try{await modelApi('tasks/'+queryTask.value.taskId+'/cancel',{method:'POST',body:{}});stage.value='正在停止断面计算'}catch(failure){error.value=failure.message}}
watch(sectionId,()=>{if(!sectionId.value)return;let id;try{id=localStorage.getItem(pendingKey())}catch{};if(id)query(id)},{flush:'post'})
const chart=computed(()=>{const series=answer.value?.pointQuery?.series||{time:[],flow_cms:[]};return{animation:false,tooltip:{trigger:'axis'},grid:{left:65,right:25,top:28,bottom:45},xAxis:{type:'category',data:series.time,axisLabel:{formatter:value=>value.slice(5,16).replace('T','\n')}},yAxis:{type:'value',name:'流量 m³/s',min:0},dataZoom:[{type:'inside'},{type:'slider',height:13,bottom:0}],series:[{type:'line',showSymbol:false,connectNulls:false,data:series.flow_cms,lineStyle:{color:'#197e83',width:2}}]}})
function exportFlow(){const values=answer.value.pointQuery.series;downloadText(section.value.name+' · '+process.value.name+'.csv',csv([['时间','流量（m³/s）'],...values.time.map((time,i)=>[time,values.flow_cms[i]])]))}
function independent(){router.push({path:'/simulation',query:{parameterResult:resultId.value,task:process.value.sourceTaskId,start:process.value.start,end:process.value.end}})}
onBeforeUnmount(()=>{disposed=true;clearInterval(clockTimer);++sequence;++querySequence;++locationSequence})
</script>
<style scoped>
.river-sections{display:grid;gap:18px;color:#31574e}.river-sections>header{display:flex;align-items:center;justify-content:space-between;gap:20px}.river-sections header span{font-size:14px;color:#71897d}.river-sections h1{font-size:25px;margin:7px 0}.selection-bar{padding:18px;display:flex;gap:24px;align-items:center}.selection-bar label{display:grid;gap:8px;font-size:14px;min-width:320px}.selection-bar>div{display:grid;gap:8px;font-size:14px;color:#72887c}.query-layout{display:grid;grid-template-columns:285px minmax(0,1fr);gap:18px;align-items:start}.section-controls{display:grid;gap:16px;padding:20px}.section-controls h2,.section-answer h2{font-size:17px;margin:0}.section-controls select{width:100%;min-height:170px}.section-controls option{padding:9px 5px}.section-facts,.section-controls label{display:grid;gap:10px;font-size:14px}.section-facts span{font-size:13px;color:#72867d}.new-section{border-top:1px solid #dde7e0;padding-top:20px;display:grid;gap:12px}.new-section p{font-size:14px;color:#738b7f;line-height:1.7}.river-main{display:grid;gap:16px;min-width:0}.river-map{padding:18px}.section-answer{padding:20px}.section-answer p{font-size:14px;color:#70867b}.answer-toolbar{display:flex;align-items:center;justify-content:space-between;font-size:14px;color:#71867b}.flow-chart{height:420px}.query-progress{padding:20px;color:#4f7560;line-height:1.7;border-left:3px solid #438269}.query-progress p{margin:8px 0}.error{color:#aa4636;font-size:14px}.notice{padding:20px;font-size:14px;color:#6b8778}.river-sections :deep(.btn),.river-sections .input{font-size:14px;min-height:36px}@media(max-width:1000px){.query-layout{grid-template-columns:1fr}.selection-bar{flex-wrap:wrap}}
</style>
