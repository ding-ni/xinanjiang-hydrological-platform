<template>
  <section class="forecast-view">
    <header class="page-heading"><div><h2>洪水预报</h2><span class="history-label">历史回算</span></div><RouterLink to="/archive" class="btn">计算成果</RouterLink></header>
    <div class="forecast-layout">
      <section class="panel panel-pad conditions">
        <form @submit.prevent="run">
          <label for="forecast-parameter">参数<select id="forecast-parameter" v-model="parameterSchemeId" class="input" :disabled="loading || running" @change="rememberParameter"><option value="" disabled>选择已保存参数</option><option v-for="row in parameters" :key="row.id" :value="row.id">{{ row.name }}</option></select></label>
          <RouterLink v-if="!loading && !parameters.length" to="/calibration" class="empty-link">进入参数率定</RouterLink>
          <div class="mode-switch" role="tablist" aria-label="回算方式"><button role="tab" type="button" :aria-selected="mode==='single'" :class="{active:mode==='single'}" :disabled="running" @click="mode='single'">单次回算</button><button role="tab" type="button" :aria-selected="mode==='rolling'" :class="{active:mode==='rolling'}" :disabled="running" @click="mode='rolling'">滚动回算</button></div>
          <label for="forecast-issue-time">{{ mode==='single'?'起报时刻':'首次起报' }}<input id="forecast-issue-time" v-model="issueTime" class="input" type="datetime-local" step="3600" :disabled="running" required /></label>
          <label v-if="mode==='single'" for="forecast-end-time">回算至<input id="forecast-end-time" v-model="endTime" class="input" type="datetime-local" step="3600" :min="issueTime" :disabled="running" required /></label>
          <template v-else><label for="forecast-last-issue">末次起报<input id="forecast-last-issue" v-model="lastIssueTime" class="input" type="datetime-local" step="3600" :min="issueTime" :disabled="running" required /></label><label for="forecast-lead">预见期 · 小时<input id="forecast-lead" v-model.number="leadHours" class="input" type="number" min="1" step="1" :disabled="running" required /></label></template>
          <p v-if="mode==='rolling' && issueTime && lastIssueTime" class="rolling-count">每小时起报 · {{ issueCount }} 次</p>
          <p v-if="pageError || error" role="alert" class="failure">{{ pageError || error }}</p>
          <ul v-if="operation?.issues?.length" class="issues"><li v-for="(item,index) in operation.issues" :key="index">{{ preparationIssueText(item) }}</li></ul>
          <button class="btn primary run-button" :disabled="!canRun">{{ running?'正在回算…':'开始回算' }}</button>
        </form>
        <DailyOperationProgress :operation="operation" @cancel="cancel" />
      </section>
      <DailyResultPanel :operation="historicalTask || operation" :result="historicalResult || result" :task-id="historicalTask?.taskId || result?.primaryTaskId || result?.sourceTaskId" :title="historicalTask ? '历史预报过程' : '回算过程'" />
    </div>
  </section>
</template>
<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { modelApi } from '@/services/modelBackendService'
import { activeOperation, belongsToOperationContext, dailyClient, dailyOperationSelection, historicalParameterId, operationContext, operationId, preparationIssueText, readDailySelection, rememberedParameter, saveDailySelection } from '@/services/dailyOperationService'
import { useDailyOperation } from '@/composables/useDailyOperation'
import DailyOperationProgress from '@/components/model/DailyOperationProgress.vue'
import DailyResultPanel from '@/components/model/DailyResultPanel.vue'
const app=useAppStore(),route=useRoute(),parameters=ref([]),parameterSchemeId=ref(''),loading=ref(false),pageError=ref('')
const mode=ref('single'),issueTime=ref(''),endTime=ref(''),lastIssueTime=ref(''),leadHours=ref(24)
const historicalTask=ref(null),historicalResult=ref(null)
const {operation,result,error,submitting,inspect,submit,cancel,clear}=useDailyOperation({contextKey:()=>app.contextKey,onChange:row=>saveDailySelection(app.contextKey,'forecast',{...readDailySelection(app.contextKey,'forecast'),operationId:operationId(row)})})
const running=computed(()=>submitting.value||activeOperation(operation.value))
const issueCount=computed(()=>Math.max(0,Math.floor((Date.parse(lastIssueTime.value)-Date.parse(issueTime.value))/3600000)+1)||0)
const canRun=computed(()=>!loading.value&&!running.value&&Boolean(parameterSchemeId.value&&issueTime.value)&&(mode.value==='single'?Boolean(endTime.value):Boolean(lastIssueTime.value&&Number.isInteger(leadHours.value)&&leadHours.value>0)))
let revision=0
watch(()=>[app.contextKey,route.query.operation,route.query.task,route.query.parameter],async()=>{
  const token=++revision,key=app.contextKey;clear();historicalTask.value=null;historicalResult.value=null;loading.value=true;pageError.value=''
  const saved=readDailySelection(key,'forecast');mode.value=saved.mode||'single';issueTime.value=saved.issueTime||'';endTime.value=saved.endTime||'';lastIssueTime.value=saved.lastIssueTime||'';leadHours.value=saved.leadHours||24
  try{
    const rows=await dailyClient.parameters();if(token!==revision)return
    parameters.value=rows.filter(row=>belongsToOperationContext(row,key));parameterSchemeId.value=rememberedParameter(parameters.value,readDailySelection(key,'parameter').id||saved.parameterSchemeId)
    if(route.query.task){
      const [task,answer]=await Promise.all([modelApi(`tasks/${encodeURIComponent(route.query.task)}`),modelApi(`tasks/${encodeURIComponent(route.query.task)}/result`)])
      if(token!==revision)return
      if(!belongsToOperationContext(task,key))throw new Error('请在该成果所属流域查看预报')
      parameterSchemeId.value=historicalParameterId(parameters.value,task,route.query.parameter)
      historicalTask.value=task;historicalResult.value=answer;mode.value='single'
      issueTime.value=String(answer.issue?.issueTime||task.runConfig?.issue?.issueTime||'').replace(' ','T').slice(0,16)
      endTime.value=String(answer.hydrograph?.forecast?.at(-1)?.time||task.runConfig?.period?.endTime||'').replace(' ','T').slice(0,16)
      return
    }
    if(route.query.operation||saved.operationId){
      await inspect(route.query.operation||saved.operationId)
      if(token!==revision)return
      const selection=dailyOperationSelection(operation.value,parameterSchemeId.value,{explicitHistory:Boolean(route.query.operation)})
      if(!selection.restore){clear();saveDailySelection(key,'forecast',{...saved,parameterSchemeId:selection.parameterId,operationId:null});return}
      if(operation.value?.runConfig){
        const request=operation.value?.runConfig||operation.value?.request||{}
        mode.value=request.operation==='rollingHindcast'?'rolling':'single';parameterSchemeId.value=rememberedParameter(parameters.value,selection.parameterId)
        issueTime.value=String(request.issueTime||'').replace(' ','T').slice(0,16);endTime.value=String(request.endTime||'').replace(' ','T').slice(0,16)
        lastIssueTime.value=String(request.lastIssueTime||'').replace(' ','T').slice(0,16);leadHours.value=request.leadHours||24
        saveDailySelection(key,'forecast',{parameterSchemeId:parameterSchemeId.value,mode:mode.value,issueTime:issueTime.value,endTime:endTime.value,lastIssueTime:lastIssueTime.value,leadHours:leadHours.value,operationId:operationId(operation.value)})
      }
    }
  }catch(failure){if(token===revision)pageError.value=failure.message}
  finally{await nextTick();if(token===revision)loading.value=false}
},{immediate:true})
watch([parameterSchemeId,mode,issueTime,endTime,lastIssueTime,leadHours],()=>{
  if(loading.value)return
  historicalTask.value=null;historicalResult.value=null
  if(operation.value&&!activeOperation(operation.value))clear()
  saveDailySelection(app.contextKey,'forecast',{parameterSchemeId:parameterSchemeId.value,mode:mode.value,issueTime:issueTime.value,endTime:endTime.value,lastIssueTime:lastIssueTime.value,leadHours:leadHours.value,operationId:operationId(operation.value)})
})
async function run(){
  if(!canRun.value)return
  historicalTask.value=null;historicalResult.value=null
  await submit({operation:mode.value==='single'?'hindcast':'rollingHindcast',parameterSchemeId:parameterSchemeId.value,issueTime:issueTime.value,
    ...(mode.value==='single'?{endTime:endTime.value}:{lastIssueTime:lastIssueTime.value,leadHours:leadHours.value}),context:operationContext(app.activeContext)})
}
function rememberParameter(){saveDailySelection(app.contextKey,'parameter',{id:parameterSchemeId.value})}
</script>
<style scoped>
.forecast-view{display:grid;gap:20px}.page-heading,.page-heading>div{display:flex;align-items:center;gap:16px}.page-heading{justify-content:space-between}.page-heading h2{margin:0;font-size:23px;color:#2a4b40;font-family:'Noto Serif CJK SC','SimSun',serif}.history-label{border:1px solid #d8e4dc;border-radius:3px;padding:4px 8px;font-size:11px;color:#62806d}.forecast-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:20px;align-items:start}.conditions form{display:grid;gap:20px}.conditions label{display:grid;gap:8px;font-size:12px;color:#536c60}.conditions .input{width:100%;min-width:0}.mode-switch{display:flex;border:1px solid #dce5de;border-radius:5px;overflow:hidden}.mode-switch button{flex:1;padding:11px 5px;border:0;background:#f7faf7;color:#667d70;cursor:pointer}.mode-switch button.active{background:#e5efe8;color:#25634c;font-weight:700}.run-button{width:100%;min-height:40px}.failure,.issues{color:#a14735;font-size:12px;line-height:1.8;margin:0}.issues{padding-left:17px}.rolling-count{margin:0;color:#73887a;font-size:12px}.empty-link{color:#35785b;font-size:12px}@media(max-width:1000px){.forecast-layout{grid-template-columns:1fr}.conditions form{grid-template-columns:1fr 1fr;align-items:end}.conditions .mode-switch,.conditions .failure,.conditions .issues{grid-column:1/-1}}@media(max-width:600px){.conditions form{grid-template-columns:1fr}.page-heading h2{font-size:20px}}
</style>
