<template>
  <section class="state-view">
    <header class="page-heading"><div><h2>过程与状态</h2><span>{{ sourceLabel }} / {{ entry?.name || entry?.label }}</span></div><RouterLink to="/archive" class="btn">计算成果</RouterLink></header>
    <nav class="state-tabs"><button class="btn" :class="{primary:pageTab==='process'}" @click="pageTab='process'">模拟过程</button><button class="btn" :class="{primary:pageTab==='library'}" @click="pageTab='library'">状态库</button></nav>
    <DailyStateLibrary v-if="pageTab==='library'" />
    <div v-else><section class="panel panel-pad result-selector"><label><input v-model="showHistory" type="checkbox" /> 显示其他历史记录</label><label>计算成果<select class="input" :value="canonicalResultId" @change="selectResult($event.target.value)"><option value="">选择计算成果</option><option v-for="row in visibleResults" :key="row.id" :value="row.id">{{ row.name || '未命名计算 · '+row.createdAt }}</option></select></label></section>
    <section v-if="!sourceId && !route.query.operation" class="panel empty"><p>从计算成果查看模拟状态</p><RouterLink to="/archive" class="btn primary">选择计算成果</RouterLink> <RouterLink to="/state?tab=library" class="btn">打开状态库</RouterLink></section>
    <template v-else>
      <section class="panel panel-pad time-toolbar">
        <label v-if="entries.length">洪水 / 连续过程<select v-model="entryId" class="input" aria-label="状态所属过程"><option v-for="row in entries" :key="row.id" :value="row.id">{{ row.label || row.name }}</option></select></label>
        <label v-if="catalog">状态时刻<select v-model="time" class="input" aria-label="状态回放时刻"><option v-for="value in catalog.times" :key="value" :value="value">{{ hourText(value) }}</option></select></label>
        <span v-else>{{ loading?'读取状态…':checkpoint?.time ? hourText(checkpoint.time) : '' }}</span>
        <button v-if="checkpoint?.stateVersionId && replayTaskId" type="button" class="btn" @click="continuationDialog.showModal()">用于继续模拟</button>
        <button v-if="replayTaskId" type="button" class="btn" @click="router.push({path:'/results/'+canonicalResultId+'/sections',query:{process:entryId}})">断面流量</button>
      </section>
      <section v-if="process?.forecast?.length || process?.times?.length" class="panel panel-pad state-process"><CalibrationProcessChart :hydrograph="process" :forcing="process.forcing || {precipitation:[]}" :visible-components="{}" @select-time="selectStateTime" /><label v-if="catalog?.times?.length" class="state-slider">{{ hourText(time) }}<input type="range" min="0" :max="catalog.times.length-1" :value="catalog.times.indexOf(time)" aria-label="拖动查看逐时状态" @input="time = catalog.times[Number($event.target.value)]" /></label></section>
      <p v-if="error || frameError" class="failure" role="alert">{{ error || frameError }}</p>
      <div v-if="cells.length" class="state-layout">
        <aside class="panel panel-pad variables">
          <h3>当前时刻空间分布</h3><label>色标范围<select v-model="scaleMode" class="input"><option value="event">本场范围</option><option value="fixed">固定范围</option></select></label><p class="range-note">最小 {{ stateNumber(frameRange.min) }} · 最大 {{ stateNumber(frameRange.max) }} · 极差 {{ stateNumber(frameRange.span) }}<br />{{ frameRange.equal ? '当前值一致' : '当前时刻全部网格' }}</p>
          <template v-for="group in variableGroups" :key="group.name"><h4>{{ group.name }}</h4><button v-for="item in variables.filter(item=>item.group===group.id)" :key="item.key" type="button" :class="{active:variable===item.key}" @click="variable=item.key"><span>{{ item.name }}</span><small>{{ item.symbol }}{{ item.unit ? ` · ${item.unit}` : '' }}</small></button></template>
          <div class="mean"><span>网格平均</span><b>{{ number(mean,3) }} <small>{{ current.unit }}</small></b></div>
          <details><summary>计算条件</summary><div class="conditions"><span>时刻 {{ hourText(frame?.time || checkpoint?.time) }}</span><span>{{ cells.length }} 个网格</span><span v-if="checkpoint?.parentStateVersionId">承接前次末状态</span><span v-if="checkpoint?.sourceTaskId">计算 {{ checkpoint.sourceTaskId }}</span></div></details>
        </aside>
        <article class="panel map-panel"><BasinMap :stations="stations" :layers="layers" :grid-cells="cells" :grid-paint-field="current.field" :selected-grid-id="selectedId" interactive-grid :legend-mode="current.legend" :state-scale="colorScale" @select-grid="selectedId=$event" /></article>
        <aside class="panel panel-pad grid-detail">
          <h3>选中网格 · {{ hourText(frame?.time || checkpoint?.time) }}</h3>
          <template v-if="selected">
            <p class="grid-id">{{ selected.id }}</p>
            <div class="values"><div v-for="item in variables" :key="item.key"><span>{{ item.symbol }}</span><b>{{ stateNumber(selected[item.field]) }} {{ item.unit }}</b></div></div>
            <div v-if="series" class="trend"><BaseChart :option="trendOption" /></div>
            <p v-else-if="seriesError" class="failure">{{ seriesError }}</p><div class="export-state"><button class="btn" :disabled="!series || !rangeData" @click="openExport">导出 Excel</button><p v-if="exportError" class="failure">{{ exportError }}</p></div>
          </template><p v-else class="grid-empty">点击网格查看状态</p>
        </aside>
      </div>
      <details v-if="checkpoint?.routingTopology && !frameLoading && !frameError && (!catalog || frame?.time===checkpoint.time)" class="panel panel-pad routing-state"><summary>末时刻汇流状态 · {{ hourText(checkpoint.time) }}</summary><div class="routing-tables"><div><h3>坡面汇流 · m³/s</h3><div class="table-wrap"><table class="table"><thead><tr><th>汇流单元</th><th>地表径流</th><th>壤中流</th><th>地下径流</th></tr></thead><tbody><tr v-for="(id,index) in checkpoint.routingTopology.unitIds" :key="id"><td>{{ id }}</td><td>{{ number(checkpoint.routing.slope_state[index]?.[0],4) }}</td><td>{{ number(checkpoint.routing.slope_state[index]?.[1],4) }}</td><td>{{ number(checkpoint.routing.slope_state[index]?.[2],4) }}</td></tr></tbody></table></div></div><div><h3>河道汇流 · m³/s</h3><div class="table-wrap"><table class="table"><thead><tr><th>河段</th><th>入流</th><th>出流</th></tr></thead><tbody><tr v-for="(id,index) in checkpoint.routingTopology.reachIds" :key="id"><td>{{ id }}</td><td>{{ number(checkpoint.routing.prev_in[index],4) }}</td><td>{{ number(checkpoint.routing.prev_out[index],4) }}</td></tr></tbody></table></div></div></div></details>
      <p v-if="frameLoading" class="state-reading" role="status">正在读取 {{ hourText(time) }} 的空间分布…</p>
      <section v-if="!catalog && !loading && !error" class="panel empty"><p>{{ replayProgress || '这次计算未保存逐时分布；已保存的雨流过程仍可查看。' }}</p><p>生成范围：{{ replayRange }}。使用这次计算的参数与起算规则，恢复水分和汇流状态，不重新率定参数。</p><button class="btn primary" :disabled="replaying || !payload" @click="calculateStates">{{ replaying ? '正在生成状态分布' : '生成状态分布' }}</button></section>
    </template></div>
    <HydrologyDialog ref="exportDialog" width="min(640px,94vw)"><h2>导出选中网格状态</h2><p>{{ entry?.name }} · {{ selectedId }}<br />{{ hourText(entry?.start) }} 至 {{ hourText(entry?.end) }}（不含预热）</p><button class="btn" @click="exportVariables=rangeData.variables.map(row=>row.key)">全选</button> <button class="btn" @click="exportVariables=[]">清空</button><div class="export-variables"><label v-for="row in rangeData?.variables||[]" :key="row.key"><input v-model="exportVariables" type="checkbox" :value="row.key" />{{ row.name }} {{ row.key }} · {{ row.unit }}</label></div><p v-if="exportError" class="failure">{{ exportError }}</p><button class="btn" @click="exportDialog.close()">取消</button> <button class="btn primary" :disabled="exportBusy || !exportVariables.length" @click="downloadExcel">下载 Excel</button></HydrologyDialog>
    <HydrologyDialog ref="continuationDialog"><header class="page-heading"><h2>用于继续模拟</h2><button class="btn" @click="continuationDialog.close()">关闭</button></header><p>来源：{{ sourceLabel }} · {{ entry?.name || entry?.label }}</p><p>可用续算状态时刻：<b>{{ hourText(checkpoint?.time) }}</b>。它包含该时刻的产流、坡面和河道汇流状态。</p><p v-if="entry?.end && hourText(entry.end)!==hourText(checkpoint?.time)">当前洪水结束于 {{ hourText(entry.end) }}；原连续轨迹保存的续算状态位于上面的时刻。</p><button class="btn" @click="saveFinalState">命名保存到状态库</button> <button class="btn primary" @click="continueFromResult">继续模拟</button></HydrologyDialog>
  </section>
</template>
<script setup>
import { computed, ref, watch, onBeforeUnmount,onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { modelApi, modelDownload } from '@/services/modelBackendService'
import { dailyClient, hourText, resultEntries } from '@/services/dailyOperationService'
import { loadGridHistoryCatalog, loadGridStateFrame, loadGridStateSeries } from '@/services/taskDiagnosticService'
import DailyStateLibrary from '@/components/model/DailyStateLibrary.vue'
import HydrologyDialog from '@/components/model/HydrologyDialog.vue'
import BasinMap from '@/components/map/BasinMap.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import CalibrationProcessChart from '@/components/charts/CalibrationProcessChart.vue'
import { stateScale, stateNumber } from '@/services/stateColorScale'
import { retentionChanged } from '@/services/resultRetentionService'
import { ElMessageBox, ElMessage } from 'element-plus'
const route=useRoute(),router=useRouter(),app=useAppStore()
const showHistory=ref(false),scaleMode=ref('event'),rangeData=ref(null),exportDialog=ref(null),exportVariables=ref([]),exportBusy=ref(false),exportError=ref('')
const pageTab=ref(route.query.tab==='library'?'library':'process'),availableResults=ref([])
watch(()=>route.query.tab,value=>pageTab.value=value==='library'?'library':'process')
onMounted(async()=>{try{availableResults.value=((await modelApi('result-catalog',{timeoutMs:60000})).results||[]).filter(row=>!row.contextKey||row.contextKey===app.contextKey)}catch(failure){error.value=failure.message}})
function selectResult(id){const row=availableResults.value.find(row=>row.id===id);if(row)router.push({path:'/state',query:row.id.startsWith('OP-')?{operation:row.id}:{source:row.id}})}
const visibleResults=computed(()=>availableResults.value.filter(row=>showHistory.value||row.retention==='saved'||row.id===canonicalResultId.value))
watch(retentionChanged,async()=>{availableResults.value=((await modelApi('result-catalog')).results||[]).filter(row=>!row.contextKey||row.contextKey===app.contextKey)})
const sourceId=computed(()=>String(route.query.source||route.query.task||''))
const canonicalResultId=computed(()=>String(route.query.operation||payload.value?.operationId||sourceId.value))
const sourceLabel=ref(''),payload=ref(null),entryId=ref(''),catalog=ref(null),checkpoint=ref(null),frame=ref(null),series=ref(null),time=ref(''),loading=ref(false),error=ref(''),seriesError=ref(''),selectedId=ref(null),variable=ref('soil')
const processRows=ref([]),frameLoading=ref(false),frameError=ref(''),continuationDialog=ref(null)
const entries=computed(()=>processRows.value.length?processRows.value:resultEntries(payload.value||{},sourceId.value))
const entry=computed(()=>entries.value.find(row=>row.id===entryId.value))
const process=computed(()=>entry.value?.hydrograph || payload.value?.hydrographs?.[entry.value?.eventId || entryId.value] || payload.value?.hydrograph)
function selectStateTime(value){const selected=catalog.value?.times?.find(item=>item.slice(0,16)===String(value).slice(0,16));if(selected)time.value=selected}
async function saveFinalState(){
  try{
    const {value}=await ElMessageBox.prompt('为该成果的完整末状态命名','保存到状态库',{inputValue:sourceLabel.value+' · '+hourText(checkpoint.value.time),inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80})
    const id=checkpoint.value.stateVersionId
    const states=await modelApi('state-catalog')
    const row=states.metadata?.[id]
    await modelApi('states/'+encodeURIComponent(id)+'/metadata',{method:'POST',body:{name:value.trim(),favorite:true,revision:row?.revision||0}})
    ElMessage.success('完整末状态已保存到状态库')
  }catch(failure){if(failure instanceof Error)error.value=failure.message}
}
const replayTaskId=computed(()=>entry.value?.sourceTaskId||entry.value?.taskId||payload.value?.stateCalculationTaskId||sourceId.value)
const replayRange=computed(()=>{const period=payload.value?.frozenRequest?.period||{},rows=period.events||period.windows||[];const start=payload.value?.continuousOrigin||payload.value?.frozenRequest?.calibration?.continuousOrigin||period.continuousOrigin||rows.map(row=>row.start).sort()[0]||period.startTime;const end=rows.map(row=>row.end).sort().at(-1)||period.endTime;return `${hourText(start)} 至 ${hourText(end)}${period.warmupDays?'（含原定'+period.warmupDays+'天预热）':''}`})
const replaying=ref(false),replayProgress=ref('')
const layers={rain:false,hydro:false,water:false,section:true,river:true,subbasin:true,rainField:false,dem:false,grid:true}
const variableGroups=[{id:'soil',name:'土壤蓄水'},{id:'free',name:'自由水'},{id:'runoff',name:'产流 · 本时段径流深'}]
const variables=[
  {key:'soil',name:'张力水相对蓄量',symbol:'W/WM',unit:'',field:'soil',legend:'soil'},
  {key:'wu',name:'上层张力水',symbol:'WU',unit:'mm',field:'wu',raw:'WU',legend:'soil'},
  {key:'wl',name:'下层张力水',symbol:'WL',unit:'mm',field:'wl',raw:'WL',legend:'soil'},
  {key:'wd',name:'深层张力水',symbol:'WD',unit:'mm',field:'wd',raw:'WD',legend:'soil'},
  {key:'free',name:'自由水蓄量',symbol:'S',unit:'mm',field:'freeWater',raw:'S',legend:'runoff'},
  {key:'rs',name:'地表径流',symbol:'RS',unit:'mm',field:'RS',raw:'RS',legend:'runoff'},
  {key:'ri',name:'壤中径流',symbol:'RI',unit:'mm',field:'RI',raw:'RI',legend:'runoff'},
  {key:'rg',name:'地下径流',symbol:'RG',unit:'mm',field:'RG',raw:'RG',legend:'runoff'}
].map(item=>({...item,group:['rs','ri','rg'].includes(item.key)?'runoff':item.key==='free'?'free':'soil'}))
const colorScale=computed(()=>stateScale(current.value.field,scaleMode.value,rangeData.value?.ranges,rangeData.value?.parameters||catalog.value?.parameters))
const frameRange=computed(()=>{const values=cells.value.map(row=>row[current.value.field]).filter(Number.isFinite);if(!values.length)return {};const min=Math.min(...values),max=Math.max(...values);return {min,max,span:max-min,equal:Math.abs(max-min)<=1e-10*Math.max(1,Math.abs(max))}})
async function openExport(){exportVariables.value=[current.value.key==='soil'?'W_WM':current.value.raw];exportError.value='';exportDialog.value.showModal()}
async function downloadExcel(){exportBusy.value=true;exportError.value='';try{await modelDownload('results/'+encodeURIComponent(canonicalResultId.value)+'/state-excel',{processId:entryId.value,gridId:selectedId.value,variables:exportVariables.value},entryId.value+'-'+selectedId.value+'-状态.xlsx');exportDialog.value.close()}catch(failure){exportError.value=failure.message}finally{exportBusy.value=false}}
watch([canonicalResultId,entryId,replayTaskId],async([id,processId])=>{rangeData.value=null;if(!id||!processId)return;try{const data=await modelApi('results/'+encodeURIComponent(id)+'/state-ranges?'+new URLSearchParams({processId}));if(id===canonicalResultId.value&&processId===entryId.value)rangeData.value=data}catch(failure){if(id===canonicalResultId.value&&processId===entryId.value)exportError.value=failure.message}})
const current=computed(()=>variables.find(row=>row.key===variable.value)||variables[0])
const cells=computed(()=>{
  if(frame.value)return frame.value.cells||[]
  if(catalog.value || frameLoading.value || frameError.value || error.value)return []
  const saved=checkpoint.value;if(!saved?.gridGeometry?.length)return[]
  if(entry.value?.end && hourText(saved.time)>hourText(entry.value.end))return []
  const wm=saved.parameters?.['runoff.WM']??saved.parameters?.WM
  return saved.gridGeometry.map((geometry,index)=>{
    const fields=Object.fromEntries(Object.entries(saved.land||{}).map(([key,values])=>[key,values[index]]))
    return {...geometry,...fields,wu:fields.WU,wl:fields.WL,wd:fields.WD,freeWater:fields.S,soil:wm?(fields.WU+fields.WL+fields.WD)/wm:null}
  })
})
const selected=computed(()=>cells.value.find(row=>row.id===selectedId.value))
let spatialSelectionIdentity=''
watch(cells,rows=>{
  if(!rows.length)return
  const identity=JSON.stringify(rows.map(row=>[row.id,row.lon??row.lng,row.lat,row.coordinates||row.polygon||row.bounds]))
  if(spatialSelectionIdentity&&identity!==spatialSelectionIdentity)selectedId.value=null
  spatialSelectionIdentity=identity
  if(!rows.some(row=>row.id===selectedId.value))selectedId.value=rows[0].id
})
const mean=computed(()=>{const values=cells.value.map(row=>row[current.value.field]).filter(value=>value!=null&&Number.isFinite(Number(value)));return values.length?values.reduce((sum,value)=>sum+Number(value),0)/values.length:null})
const number=(value,digits=2)=>value!=null&&Number.isFinite(Number(value))?Number(value).toFixed(digits):'—'
const stations=computed(()=>[...(app.projectData?.stations?.stations||[]),...(app.projectData?.sections?.sections||[])])
const trendOption=computed(()=>{
  const history=series.value,wm=catalog.value?.parameters?.WM||checkpoint.value?.parameters?.['runoff.WM']
  const allowed=new Set(catalog.value?.times||[]),indices=(history?.times||[]).map((value,index)=>({value,index})).filter(row=>allowed.has(row.value))
  const values=current.value.key==='soil'?indices.map(({index})=>wm?(history.series.WU[index]+history.series.WL[index]+history.series.WD[index])/wm:null):indices.map(({index})=>history?.series?.[current.value.raw]?.[index]??null)
  return {animation:false,grid:{left:45,right:10,top:20,bottom:50},tooltip:{trigger:'axis',confine:true},xAxis:{type:'category',data:indices.map(row=>hourText(row.value)),axisLabel:{fontSize:9,hideOverlap:true}},yAxis:{type:'value',scale:true,name:current.value.unit},series:[{type:'line',showSymbol:false,connectNulls:false,data:values,lineStyle:{width:2,color:'#287763'}}]}
})
let sourceRevision=0,replayRevision=0,frameRevision=0,seriesRevision=0
onBeforeUnmount(()=>{sourceRevision++;replayRevision++;frameRevision++;seriesRevision++})
async function calculateStates(){
  const token=sourceRevision
  const source=payload.value?.primaryTaskId||payload.value?.taskId||sourceId.value
  replaying.value=true;error.value='';replayProgress.value='正在计算状态'
  try{
    let operation=await dailyClient.submit({operation:'stateReplay',sourceTaskId:source,context:app.backendCase?.context||{projectId:app.basinId,resolutionId:app.modelScale,modelConfigId:app.modelConfigId,contextKey:app.contextKey}})
    while(['queued','running','cancelling'].includes(operation.status)){
      if(token!==sourceRevision)return
      replayProgress.value=operation.stageLabel
      await new Promise(resolve=>setTimeout(resolve,700))
      operation=await dailyClient.read(operation.taskId)
    }
    if(token!==sourceRevision)return
    if(operation.status!=='completed')throw new Error(operation.errorMessage||'状态计算已停止')
    const saved=await dailyClient.result(operation.taskId)
    if(token!==sourceRevision)return
    payload.value={...payload.value,...Object.fromEntries(['eventStates','stateCalculationTaskId','endStateVersionId'].filter(key=>saved[key]).map(key=>[key,saved[key]]))}
    const processes=await modelApi('result-catalog/'+encodeURIComponent(canonicalResultId.value)+'/processes')
    if(token!==sourceRevision)return
    processRows.value=processes.processes || []
    if(!entries.value.some(row=>row.id===entryId.value))entryId.value=entries.value.find(row=>row.id===route.query.event)?.id||entries.value[0]?.id||''
    replayProgress.value=''
  }catch(failure){if(token===sourceRevision)error.value=failure.message}
  finally{if(token===sourceRevision)replaying.value=false}
}
watch(()=>[sourceId.value,route.query.operation,app.contextKey,route.query.event,route.query.process],async()=>{
  const token=++sourceRevision;payload.value=null;processRows.value=[];entryId.value='';error.value='';sourceLabel.value=route.query.label||'';replaying.value=false;replayProgress.value=''
  if(!sourceId.value&&!route.query.operation)return
  loading.value=true
  try{
    const answer=route.query.operation?await dailyClient.result(route.query.operation):await modelApi(`tasks/${encodeURIComponent(sourceId.value)}/result`)
    if(token!==sourceRevision)return
    payload.value=answer
    const processes=await modelApi('result-catalog/'+encodeURIComponent(canonicalResultId.value)+'/processes')
    if(token!==sourceRevision)return
    processRows.value=processes.processes || []
    entryId.value=entries.value.find(row=>row.id===(route.query.event||route.query.process))?.id||entries.value[0]?.id||''
    if(!sourceLabel.value)sourceLabel.value=answer.name||answer.lineage?.schemeName||'计算成果'
    modelApi('result-metadata/'+canonicalResultId.value).then(row=>{if(token===sourceRevision&&row.name)sourceLabel.value=row.name}).catch(()=>{})
  }catch(failure){if(token===sourceRevision)error.value=failure.message}
  finally{if(token===sourceRevision)loading.value=false}
},{immediate:true})
watch([replayTaskId,entryId],async([id])=>{
  const token=++replayRevision;++frameRevision;++seriesRevision;catalog.value=null;checkpoint.value=null;frame.value=null;frameError.value='';series.value=null;time.value='';
  if(!id||!/^JOB-/.test(id))return
  loading.value=true;error.value=''
  try{
    const [taskResult,catalogResult]=await Promise.allSettled([modelApi(`tasks/${encodeURIComponent(id)}/result`),loadGridHistoryCatalog(id)])
    if(token!==replayRevision)return
    if(taskResult.status==='fulfilled'){
      const stateId=taskResult.value.endStateVersionId||taskResult.value.stateVersionId
      if(stateId){const saved=await modelApi(`states/${encodeURIComponent(stateId)}/checkpoint`);if(token!==replayRevision)return;if(saved.context?.contextKey!==app.contextKey)throw new Error('请在该成果所属流域查看状态');checkpoint.value=saved}
    }
    if(token!==replayRevision)return
    if(catalogResult.status==='fulfilled'){
      if(catalogResult.value.context?.contextKey!==app.contextKey)throw new Error('请在该成果所属流域查看状态')
      const bounds=entry.value
      const first=bounds?.start || process.value?.forecast?.[0]?.time,last=bounds?.end || process.value?.forecast?.at(-1)?.time
      catalog.value={...catalogResult.value,times:catalogResult.value.times.filter(value=>(!first||value.slice(0,16)>=String(first).replace(' ','T').slice(0,16))&&(!last||value.slice(0,16)<=String(last).replace(' ','T').slice(0,16)))}
      time.value=catalog.value.times.at(-1)||''
      const resultId=canonicalResultId.value,processId=entryId.value
      modelApi('results/'+encodeURIComponent(resultId)+'/state-ranges?'+new URLSearchParams({processId})).then(data=>{if(token===replayRevision)rangeData.value=data}).catch(failure=>{if(token===replayRevision)exportError.value=failure.message})
    }else if(catalogResult.reason?.message!=='该成果尚未保存逐时状态')throw catalogResult.reason
  }catch(failure){if(token===replayRevision)error.value=failure.message}
  finally{if(token===replayRevision)loading.value=false}
},{immediate:true})
watch(time,async value=>{
  const token=++frameRevision;frame.value=null;frameLoading.value=false;frameError.value=''
  if(!value||!catalog.value)return
  frameLoading.value=true
  try{const answer=await loadGridStateFrame(catalog.value.taskId,value);if(token===frameRevision){frame.value=answer;if(!selectedId.value)selectedId.value=answer.cells[0]?.id||null}}
  catch(failure){if(token===frameRevision)frameError.value=failure.message}
  finally{if(token===frameRevision)frameLoading.value=false}
})
watch([selectedId,catalog],async([id,source])=>{
  const token=++seriesRevision;series.value=null;seriesError.value=''
  if(!id||!source)return
  try{const answer=await loadGridStateSeries(source.taskId,id);if(token===seriesRevision)series.value=answer}
  catch(failure){if(token===seriesRevision)seriesError.value=failure.message}
})
function continueFromResult(){router.push({path:'/simulation',query:{source:replayTaskId.value,continue:'1',label:sourceLabel.value}})}
</script>
<style scoped>
.range-note{line-height:1.7;color:#6c8178;overflow-wrap:anywhere}.export-variables{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:20px 0}.export-state{margin-top:14px}.variables label{display:grid;gap:8px}.export-variables label{display:flex;align-items:center;gap:8px}
.state-tabs{display:flex;gap:12px}.result-selector{margin-bottom:18px}.result-selector label{display:flex;align-items:center;gap:18px}.result-selector select{min-width:320px}
.state-process{height:340px;display:grid;grid-template-rows:minmax(0,1fr) auto;gap:12px}.state-slider{display:flex;align-items:center;gap:12px}.state-slider input{flex:1;accent-color:#287763}
.routing-state summary{font-size:13px;color:#547460;cursor:pointer}.routing-tables{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:20px}.routing-tables h3{font-size:13px;color:#375d49}.routing-tables .table-wrap{max-height:300px;overflow:auto}.routing-tables td{font-size:12px}@media(max-width:700px){.routing-tables{grid-template-columns:1fr}}
.state-view{display:grid;gap:18px;min-width:0}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.page-heading>div{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}.page-heading h2{font-family:'Noto Serif CJK SC','SimSun',serif;color:#2e5142;font-size:23px;margin:0}.page-heading span{color:#7b8b80;font-size:12px}.time-toolbar{display:flex;align-items:end;gap:16px;flex-wrap:wrap}.time-toolbar label{display:grid;gap:7px;font-size:12px;color:#587362}.time-toolbar .input{min-width:210px}.time-toolbar>span{flex:1;color:#74867a;font-size:12px}.state-layout{display:grid;grid-template-columns:200px minmax(0,1fr) 290px;gap:16px;min-height:540px}.variables h3,.grid-detail h3{margin:0 0 18px;color:#345748;font-size:15px}.variables>button{display:grid;gap:5px;padding:10px 12px;width:100%;text-align:left;border:0;border-left:3px solid transparent;background:transparent;color:#4e6a58;cursor:pointer}.variables>button small{color:#7d9081;font-size:10px}.variables>button.active{background:#eaf2e9;border-color:#47815c}.mean{display:grid;gap:10px;padding:22px 0;border-top:1px solid #e1e8e0;margin-top:15px;color:#6a806f;font-size:11px}.mean b{font-size:24px;font-weight:500;color:#335e48}.mean small{font-size:12px}.variables details{font-size:11px;color:#748579}.conditions{display:grid;gap:8px;padding-top:12px;overflow-wrap:anywhere}.map-panel{overflow:hidden;min-height:540px}.values{display:grid;gap:10px}.values>div{display:flex;justify-content:space-between;font-size:12px;color:#738579}.values b{font-weight:500;color:#2c5542;font-variant-numeric:tabular-nums}.grid-id{color:#326047;font-size:13px}.trend{height:255px;margin-top:22px}.grid-empty{color:#809184;font-size:12px;padding-top:45px}.empty{padding:80px 25px;text-align:center;color:#718579}.empty p{margin-bottom:22px}.failure{color:#a24338;font-size:12px;line-height:1.8}@media(max-width:1150px){.state-layout{grid-template-columns:190px minmax(0,1fr)}.grid-detail{grid-column:1/-1}.values{grid-template-columns:repeat(4,1fr);gap:20px}.values>div{display:grid;gap:8px}.trend{height:250px}}@media(max-width:650px){.state-layout{grid-template-columns:1fr}.variables{display:flex;flex-wrap:wrap;gap:6px}.variables h3{width:100%}.variables>button{width:auto}.variables .mean{padding:10px}.map-panel{min-height:440px}.time-toolbar .input{min-width:0;width:100%}.values{grid-template-columns:repeat(2,1fr)}}
.state-view :is(label,p,small,summary,td,th,.page-heading span,.time-toolbar>span,.values>div,.grid-id,.conditions,.mean){font-size:14px}
.state-view :is(.btn,.input,.variables>button){min-height:36px;font-size:14px}
.variables h4{margin:18px 0 7px;color:#70877a;font-size:13px}.state-reading{padding:24px;background:#f5f9f6;color:#597869;border:1px solid #dce7df}.result-selector select{max-width:100%;min-width:0;width:min(650px,100%)}.time-toolbar label{min-width:0}.time-toolbar select{max-width:100%}.grid-detail h3{line-height:1.6}
</style>
