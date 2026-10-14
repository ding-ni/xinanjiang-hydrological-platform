<template>
  <section class="state-view">
    <header class="page-heading"><div><h2>模拟状态</h2><span>{{ sourceLabel }}</span></div><RouterLink to="/archive" class="btn">计算成果</RouterLink></header>
    <section v-if="!sourceId && !route.query.operation" class="panel empty"><p>从计算成果查看模拟状态</p><RouterLink to="/archive" class="btn primary">选择计算成果</RouterLink></section>
    <template v-else>
      <section class="panel panel-pad time-toolbar">
        <label v-if="entries.length>1">洪水 / 起报时刻<select v-model="entryId" class="input" aria-label="状态所属过程"><option v-for="row in entries" :key="row.id" :value="row.id">{{ row.label }}</option></select></label>
        <label v-if="catalog">状态时刻<select v-model="time" class="input" aria-label="状态回放时刻"><option v-for="value in catalog.times" :key="value" :value="value">{{ hourText(value) }}</option></select></label>
        <span v-else>{{ loading?'读取状态…':checkpoint?.time ? hourText(checkpoint.time) : '' }}</span>
        <button v-if="checkpoint?.stateVersionId && replayTaskId && (!catalog || time===checkpoint.time)" type="button" class="btn" @click="continueFromResult">继续模拟</button>
        <button v-if="replayTaskId" type="button" class="btn" @click="router.push({path:'/diagnostics',query:{source:replayTaskId,label:sourceLabel}})">河道流量</button>
      </section>
      <p v-if="error" class="failure" role="alert">{{ error }}</p>
      <div v-if="cells.length" class="state-layout">
        <aside class="panel panel-pad variables">
          <h3>流域状态</h3>
          <button v-for="item in variables" :key="item.key" type="button" :class="{active:variable===item.key}" @click="variable=item.key"><span>{{ item.name }}</span><small>{{ item.symbol }}{{ item.unit ? ` · ${item.unit}` : '' }}</small></button>
          <div class="mean"><span>网格平均</span><b>{{ number(mean,3) }} <small>{{ current.unit }}</small></b></div>
          <details><summary>计算条件</summary><div class="conditions"><span>时刻 {{ hourText(frame?.time || checkpoint?.time) }}</span><span>{{ cells.length }} 个网格</span><span v-if="checkpoint?.parentStateVersionId">承接前次末状态</span><span v-if="checkpoint?.sourceTaskId">计算 {{ checkpoint.sourceTaskId }}</span></div></details>
        </aside>
        <article class="panel map-panel"><BasinMap :stations="stations" :layers="layers" :grid-cells="cells" :grid-paint-field="current.field" interactive-grid :legend-mode="current.legend" @select-grid="selectedId=$event" /></article>
        <aside class="panel panel-pad grid-detail">
          <h3>网格状态</h3>
          <template v-if="selected">
            <p class="grid-id">{{ selected.id }}</p>
            <div class="values"><div v-for="item in variables" :key="item.key"><span>{{ item.symbol }}</span><b>{{ number(selected[item.field],3) }} {{ item.unit }}</b></div></div>
            <div v-if="series" class="trend"><BaseChart :option="trendOption" /></div>
            <p v-else-if="seriesError" class="failure">{{ seriesError }}</p>
          </template><p v-else class="grid-empty">点击网格查看状态</p>
        </aside>
      </div>
      <details v-if="checkpoint?.routingTopology && (!frame || frame.time===checkpoint.time)" class="panel panel-pad routing-state"><summary>末时刻汇流状态 · {{ hourText(checkpoint.time) }}</summary><div class="routing-tables"><div><h3>坡面汇流 · m³/s</h3><div class="table-wrap"><table class="table"><thead><tr><th>汇流单元</th><th>地表径流</th><th>壤中流</th><th>地下径流</th></tr></thead><tbody><tr v-for="(id,index) in checkpoint.routingTopology.unitIds" :key="id"><td>{{ id }}</td><td>{{ number(checkpoint.routing.slope_state[index]?.[0],4) }}</td><td>{{ number(checkpoint.routing.slope_state[index]?.[1],4) }}</td><td>{{ number(checkpoint.routing.slope_state[index]?.[2],4) }}</td></tr></tbody></table></div></div><div><h3>河道汇流 · m³/s</h3><div class="table-wrap"><table class="table"><thead><tr><th>河段</th><th>入流</th><th>出流</th></tr></thead><tbody><tr v-for="(id,index) in checkpoint.routingTopology.reachIds" :key="id"><td>{{ id }}</td><td>{{ number(checkpoint.routing.prev_in[index],4) }}</td><td>{{ number(checkpoint.routing.prev_out[index],4) }}</td></tr></tbody></table></div></div></div></details>
      <section v-if="!catalog && !loading" class="panel empty"><p>{{ replayProgress || '尚未保存逐时状态' }}</p><button class="btn primary" :disabled="replaying || !payload" @click="calculateStates">{{ replaying ? '正在计算状态' : '计算状态' }}</button></section>
    </template>
  </section>
</template>
<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { modelApi } from '@/services/modelBackendService'
import { dailyClient, hourText, resultEntries } from '@/services/dailyOperationService'
import { loadGridHistoryCatalog, loadGridStateFrame, loadGridStateSeries } from '@/services/taskDiagnosticService'
import BasinMap from '@/components/map/BasinMap.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
const route=useRoute(),router=useRouter(),app=useAppStore()
const sourceId=computed(()=>String(route.query.source||route.query.task||''))
const sourceLabel=ref(''),payload=ref(null),entryId=ref(''),catalog=ref(null),checkpoint=ref(null),frame=ref(null),series=ref(null),time=ref(''),loading=ref(false),error=ref(''),seriesError=ref(''),selectedId=ref(null),variable=ref('soil')
const entries=computed(()=>resultEntries(payload.value||{},sourceId.value))
const entry=computed(()=>entries.value.find(row=>row.id===entryId.value))
const replayTaskId=computed(()=>entry.value?.taskId||payload.value?.stateCalculationTaskId||sourceId.value)
const replaying=ref(false),replayProgress=ref('')
const layers={rain:false,hydro:false,water:false,section:true,river:true,subbasin:true,rainField:false,dem:false,grid:true}
const variables=[
  {key:'soil',name:'张力水相对蓄量',symbol:'W/WM',unit:'',field:'soil',legend:'soil'},
  {key:'wu',name:'上层张力水',symbol:'WU',unit:'mm',field:'wu',raw:'WU',legend:'soil'},
  {key:'wl',name:'下层张力水',symbol:'WL',unit:'mm',field:'wl',raw:'WL',legend:'soil'},
  {key:'wd',name:'深层张力水',symbol:'WD',unit:'mm',field:'wd',raw:'WD',legend:'soil'},
  {key:'free',name:'自由水蓄量',symbol:'S',unit:'mm',field:'freeWater',raw:'S',legend:'runoff'},
  {key:'rs',name:'地表径流',symbol:'RS',unit:'mm',field:'RS',raw:'RS',legend:'runoff'},
  {key:'ri',name:'壤中径流',symbol:'RI',unit:'mm',field:'RI',raw:'RI',legend:'runoff'},
  {key:'rg',name:'地下径流',symbol:'RG',unit:'mm',field:'RG',raw:'RG',legend:'runoff'}
]
const current=computed(()=>variables.find(row=>row.key===variable.value)||variables[0])
const cells=computed(()=>{
  if(frame.value)return frame.value.cells||[]
  const saved=checkpoint.value;if(!saved?.gridGeometry?.length)return[]
  const wm=saved.parameters?.['runoff.WM']??saved.parameters?.WM
  return saved.gridGeometry.map((geometry,index)=>{
    const fields=Object.fromEntries(Object.entries(saved.land||{}).map(([key,values])=>[key,values[index]]))
    return {...geometry,...fields,wu:fields.WU,wl:fields.WL,wd:fields.WD,freeWater:fields.S,soil:wm?(fields.WU+fields.WL+fields.WD)/wm:null}
  })
})
const selected=computed(()=>cells.value.find(row=>row.id===selectedId.value))
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
    if(!entries.value.some(row=>row.id===entryId.value))entryId.value=entries.value.find(row=>row.id===route.query.event)?.id||entries.value[0]?.id||''
    replayProgress.value=''
  }catch(failure){if(token===sourceRevision)error.value=failure.message}
  finally{if(token===sourceRevision)replaying.value=false}
}
watch(()=>[sourceId.value,route.query.operation,app.contextKey],async()=>{
  const token=++sourceRevision;payload.value=null;entryId.value='';error.value='';sourceLabel.value=route.query.label||'';replaying.value=false;replayProgress.value=''
  if(!sourceId.value&&!route.query.operation)return
  loading.value=true
  try{
    const answer=route.query.operation?await dailyClient.result(route.query.operation):await modelApi(`tasks/${encodeURIComponent(sourceId.value)}/result`)
    if(token!==sourceRevision)return
    payload.value=answer
    entryId.value=entries.value.find(row=>row.id===route.query.event)?.id||entries.value[0]?.id||''
    if(!sourceLabel.value)sourceLabel.value=answer.name||answer.lineage?.schemeName||'计算成果'
  }catch(failure){if(token===sourceRevision)error.value=failure.message}
  finally{if(token===sourceRevision)loading.value=false}
},{immediate:true})
watch([replayTaskId,entryId],async([id])=>{
  const token=++replayRevision;++frameRevision;++seriesRevision;catalog.value=null;checkpoint.value=null;frame.value=null;series.value=null;time.value='';selectedId.value=null
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
      catalog.value={...catalogResult.value,times:catalogResult.value.times.filter(value=>(!bounds?.start||value>=String(bounds.start).replace(' ','T'))&&(!bounds?.end||value.slice(0,16)<=String(bounds.end).replace(' ','T').slice(0,16)))}
      time.value=catalog.value.times.at(-1)||''
    }else if(catalogResult.reason?.message!=='该成果尚未保存逐时状态')throw catalogResult.reason
  }catch(failure){if(token===replayRevision)error.value=failure.message}
  finally{if(token===replayRevision)loading.value=false}
},{immediate:true})
watch(time,async value=>{
  const token=++frameRevision;frame.value=null
  if(!value||!catalog.value)return
  try{const answer=await loadGridStateFrame(catalog.value.taskId,value);if(token===frameRevision){frame.value=answer;if(!selectedId.value)selectedId.value=answer.cells[0]?.id||null}}
  catch(failure){if(token===frameRevision)error.value=failure.message}
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
.routing-state summary{font-size:13px;color:#547460;cursor:pointer}.routing-tables{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:20px}.routing-tables h3{font-size:13px;color:#375d49}.routing-tables .table-wrap{max-height:300px;overflow:auto}.routing-tables td{font-size:12px}@media(max-width:700px){.routing-tables{grid-template-columns:1fr}}
.state-view{display:grid;gap:18px;min-width:0}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.page-heading>div{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}.page-heading h2{font-family:'Noto Serif CJK SC','SimSun',serif;color:#2e5142;font-size:23px;margin:0}.page-heading span{color:#7b8b80;font-size:12px}.time-toolbar{display:flex;align-items:end;gap:16px;flex-wrap:wrap}.time-toolbar label{display:grid;gap:7px;font-size:12px;color:#587362}.time-toolbar .input{min-width:210px}.time-toolbar>span{flex:1;color:#74867a;font-size:12px}.state-layout{display:grid;grid-template-columns:200px minmax(0,1fr) 290px;gap:16px;min-height:540px}.variables h3,.grid-detail h3{margin:0 0 18px;color:#345748;font-size:15px}.variables>button{display:grid;gap:5px;padding:10px 12px;width:100%;text-align:left;border:0;border-left:3px solid transparent;background:transparent;color:#4e6a58;cursor:pointer}.variables>button small{color:#7d9081;font-size:10px}.variables>button.active{background:#eaf2e9;border-color:#47815c}.mean{display:grid;gap:10px;padding:22px 0;border-top:1px solid #e1e8e0;margin-top:15px;color:#6a806f;font-size:11px}.mean b{font-size:24px;font-weight:500;color:#335e48}.mean small{font-size:12px}.variables details{font-size:11px;color:#748579}.conditions{display:grid;gap:8px;padding-top:12px;overflow-wrap:anywhere}.map-panel{overflow:hidden;min-height:540px}.values{display:grid;gap:10px}.values>div{display:flex;justify-content:space-between;font-size:12px;color:#738579}.values b{font-weight:500;color:#2c5542;font-variant-numeric:tabular-nums}.grid-id{color:#326047;font-size:13px}.trend{height:255px;margin-top:22px}.grid-empty{color:#809184;font-size:12px;padding-top:45px}.empty{padding:80px 25px;text-align:center;color:#718579}.empty p{margin-bottom:22px}.failure{color:#a24338;font-size:12px;line-height:1.8}@media(max-width:1150px){.state-layout{grid-template-columns:190px minmax(0,1fr)}.grid-detail{grid-column:1/-1}.values{grid-template-columns:repeat(4,1fr);gap:20px}.values>div{display:grid;gap:8px}.trend{height:250px}}@media(max-width:650px){.state-layout{grid-template-columns:1fr}.variables{display:flex;flex-wrap:wrap;gap:6px}.variables h3{width:100%}.variables>button{width:auto}.variables .mean{padding:10px}.map-panel{min-height:440px}.time-toolbar .input{min-width:0;width:100%}.values{grid-template-columns:repeat(2,1fr)}}
.state-view :is(label,p,small,summary,td,th,.page-heading span,.time-toolbar>span,.values>div,.grid-id,.conditions,.mean){font-size:14px}
.state-view :is(.btn,.input,.variables>button){min-height:36px;font-size:14px}
</style>
