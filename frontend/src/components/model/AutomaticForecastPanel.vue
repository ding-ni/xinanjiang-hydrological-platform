<template>
  <div class="automatic-layout">
    <section class="panel panel-pad automatic-controls">
      <div class="control-heading"><h3>{{ manual ? '手动预报' : '自动预报' }}</h3><span>北京时间</span></div>
      <form @submit.prevent="manual ? runManual() : start()">
        <label for="auto-parameter">参数<select id="auto-parameter" v-model="form.parameterSchemeId" class="input" :disabled="locked" required><option value="" disabled>选择已保存参数</option><option v-for="row in parameters" :key="row.id" :value="row.id">{{ row.name }}</option></select></label>
        <RouterLink v-if="!loading && !parameters.length" to="/calibration" class="text-link">去率定并保存参数</RouterLink>
        <label for="auto-source">预报降雨<select id="auto-source" v-model="form.sourceId" class="input" :disabled="locked" required><option value="" disabled>选择降雨来源</option><option v-for="row in sources" :key="row.id" :value="row.id">{{ row.sourceLabel || row.name }}</option></select></label>
        <label for="auto-first">{{ manual ? '起报时刻' : '首次起报' }}<input id="auto-first" v-model="form.firstIssueTime" class="input" type="datetime-local" step="3600" :disabled="locked" required /></label>
        <div class="hour-fields" :class="{single:manual}"><label v-if="!manual" for="auto-interval">间隔 · 小时<input id="auto-interval" v-model.number="form.issueIntervalHours" class="input" type="number" min="1" step="1" :disabled="locked" required /></label><label for="auto-lead">预见期 · 小时<input id="auto-lead" v-model.number="form.leadHours" class="input" type="number" min="1" max="72" step="1" :disabled="locked" required /></label></div>
        <template v-if="manual">
          <button class="btn primary start-button" :disabled="locked || !valid">{{ manualRunning ? '正在预报…' : '开始预报' }}</button>
          <DailyOperationProgress :operation="manualOperation" @cancel="cancelManual" />
          <ul v-if="manualOperation?.issues?.length" class="missing-issues"><li v-for="(row,index) in manualOperation.issues" :key="index">{{ preparationIssueText(row) }}</li></ul>
        </template>
        <template v-else>
          <button v-if="!schedule?.enabled" class="btn primary start-button" :disabled="busy || loading || !valid">自动开始</button>
          <button v-else class="btn stop-button" type="button" :disabled="busy" @click="control('stop')">关闭自动预报</button>
          <div v-if="schedule && !schedule.enabled" class="catch-up"><button class="btn" type="button" :disabled="busy || !pendingCount" @click="control('catch-up')">补做预报</button><span>{{ pendingCount ? '按原设置补做 '+pendingCount+' 次' : '暂无待补起报' }}</span></div>
        </template>
        <p v-if="error || (manual && manualError)" role="alert" class="failure">{{ error || manualError }}</p>
      </form>
      <div v-if="!manual && schedule" class="schedule-state" role="status" aria-live="polite">
        <div class="state-heading"><i :class="schedule.enabled ? schedule.status : 'stopped'"></i><b>{{ statusText }}</b><span>完成 {{ schedule.completedCount }} 次</span></div>
        <dl><div><dt>最近起报</dt><dd>{{ hour(schedule.latestIssueTime || issues[0]?.issueTime) }}</dd></div><div><dt>{{ schedule.enabled ? '下次起报' : '待续起报' }}</dt><dd>{{ hour(schedule.nextIssueTime) }}</dd></div><div v-if="schedule.enabled && schedule.runMode==='catchup'"><dt>补做至</dt><dd>{{ hour(schedule.catchUpUntil) }}</dd></div></dl>
        <p v-if="schedule.enabled || schedule.message?.startsWith('补做完成')" class="status-reason">{{ schedule.message }}</p>
        <ul v-if="schedule.issues?.length" class="missing-issues"><li v-for="(row,index) in schedule.issues" :key="index">{{ preparationIssueText(row) }}</li></ul>
      </div>
      <p class="service-note">{{ manual ? '按所选时刻预报一次，不开启自动任务。' : '后台运行时持续起报，关闭页面不停止。再次开始会接续空档；补做完成后自动关闭。' }}</p>
      <details class="source-details"><summary>降雨资料与设置说明</summary>
        <p v-if="selectedSource">{{ selectedSource.spatialMethod==='model-grids'?'逐网格雨量':'全域均匀雨量' }} · 产品起报 {{ hour(selectedSource.issueTime) }}<br>覆盖至 {{ hour(selectedSource.times?.at(-1)) }}</p>
        <p>需持续提供实测资料和预报降雨产品，当前未配置气象供应商实时接口。补做使用当时可用的历史产品，缺少时等待，不用未来实测代替。</p>
        <p>保存参数直接用于全流域。首次按该参数的预热设置形成状态，以后只接续实测状态，不重复率定；改变自动设置会另建预报序列。</p>
        <div class="import-actions"><label class="btn file-button">导入降雨产品<input type="file" accept=".json,application/json" :disabled="busy" @change="importProduct" /></label><button class="text-link" type="button" @click="downloadExample">格式示例</button></div>
      </details>
      <p v-if="!loading && !sources.length" class="failure">尚无预报降雨，请在“降雨资料与设置说明”中导入。</p>
    </section>
    <div class="automatic-output">
      <div v-if="!manual && issues.length" class="panel issue-picker"><label for="auto-record">起报记录<select id="auto-record" v-model="selectedOperation" class="input"><option v-for="row in issues" :key="row.operationId" :value="row.operationId">{{ hour(row.issueTime) }}</option></select></label></div>
      <DailyResultPanel :result="manual ? manualResult : result" :operation="manual ? manualOperation : operation" :task-id="(manual ? manualResult : result)?.primaryTaskId" :hide-record-link="true" :compact="true" title="预报过程 · 徐畈断面" />
    </div>
  </div>
</template>
<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import { useDailyOperation } from '@/composables/useDailyOperation'
import { automaticForecastClient as client, beijingHourInput, canStartForecast, forecastSources, latestForecastProduct, pendingForecastCount, sameForecastSettings, sampleForecastProduct } from '@/services/automaticForecastService'
import { activeOperation, belongsToOperationContext, dailyClient, operationContext, operationId, preparationIssueText, readDailySelection, rememberedParameter, saveDailySelection } from '@/services/dailyOperationService'
import { downloadText } from '@/services/resultPresentation'
import DailyResultPanel from './DailyResultPanel.vue'
import DailyOperationProgress from './DailyOperationProgress.vue'
const props=defineProps({mode:{type:String,default:'automatic'}})
const app=useAppStore(),parameters=ref([]),products=ref([]),schedule=ref(null),issues=ref([]),loading=ref(true),busy=ref(false),error=ref(''),clock=ref(beijingHourInput())
const selectedOperation=ref(''),operation=ref(null),result=ref(null)
const emptyForm=()=>({parameterSchemeId:'',sourceId:'',firstIssueTime:beijingHourInput(),issueIntervalHours:1,leadHours:24})
const autoForm=reactive(emptyForm()),manualForm=reactive(emptyForm()),manual=computed(()=>props.mode==='manual'),form=computed(()=>manual.value?manualForm:autoForm)
const {operation:manualOperation,result:manualResult,error:manualError,submitting,inspect,submit,cancel:cancelManual,clear}=useDailyOperation({contextKey:()=>app.contextKey,onChange:row=>saveDailySelection(app.contextKey,'productForecast',{...manualForm,operationId:operationId(row)})})
const manualRunning=computed(()=>submitting.value||activeOperation(manualOperation.value))
const sources=computed(()=>forecastSources(products.value,app.contextKey)),selectedSource=computed(()=>sources.value.find(r=>r.id===form.value.sourceId))
const locked=computed(()=>loading.value||busy.value||(manual.value?manualRunning.value:Boolean(schedule.value?.enabled))),valid=computed(()=>canStartForecast(form.value))
const pendingCount=computed(()=>pendingForecastCount(schedule.value,clock.value))
const statusText=computed(()=>!schedule.value?.enabled?'已关闭':schedule.value.runMode==='catchup'?'正在补做':schedule.value.status==='running'?'正在预报':'自动预报已开启')
const hour=value=>String(value||'—').replace('T',' ').slice(0,16)
let timer,revision=0,resultRevision=0,refreshRevision=0
function fill(row){Object.assign(autoForm,{parameterSchemeId:row.parameterSchemeId,sourceId:row.sourceId,firstIssueTime:row.firstIssueTime.slice(0,16),issueIntervalHours:row.issueIntervalHours,leadHours:row.leadHours})}
async function refresh(token,initial=false){
  const refreshToken=++refreshRevision;clearTimeout(timer)
  try{
    const [payload,rain]=await Promise.all([client.list(),client.products()]);if(token!==revision||refreshToken!==refreshRevision)return
    const rows=payload.schedules||[];products.value=rain;clock.value=payload.currentTime||beijingHourInput()
    const found=rows.filter(r=>belongsToOperationContext(r,app.contextKey)),next=found.find(r=>r.enabled)||found.find(r=>r.id===schedule.value?.id)||found[0]||null
    schedule.value=next
    if(initial&&next)fill(next)
    for(const draft of [autoForm,manualForm])if(!draft.sourceId)draft.sourceId=sources.value[0]?.id||''
    if(next){
      const records=await client.issues(next.id);if(token!==revision||refreshToken!==refreshRevision)return
      const previous=issues.value[0]?.operationId;issues.value=records
      if(!selectedOperation.value||selectedOperation.value===previous||!records.some(r=>r.operationId===selectedOperation.value))selectedOperation.value=records[0]?.operationId||''
    }
  }catch(failure){if(token===revision&&refreshToken===refreshRevision)error.value=failure.message}
  finally{if(token===revision&&refreshToken===refreshRevision)timer=setTimeout(()=>refresh(token),5000)}
}
watch(()=>app.contextKey,async key=>{
  const token=++revision;clearTimeout(timer);++resultRevision;clear();schedule.value=null;issues.value=[];selectedOperation.value='';operation.value=null;result.value=null;parameters.value=[];products.value=[];loading.value=true;busy.value=false;error.value=''
  Object.assign(autoForm,emptyForm());Object.assign(manualForm,emptyForm())
  try{
    const rows=await dailyClient.parameters();if(token!==revision)return
    parameters.value=rows.filter(r=>belongsToOperationContext(r,key));const remembered=rememberedParameter(parameters.value,readDailySelection(key,'parameter').id)
    autoForm.parameterSchemeId=remembered;manualForm.parameterSchemeId=remembered
    const saved=readDailySelection(key,'productForecast');if(saved.operationId){Object.assign(manualForm,saved);await inspect(saved.operationId);if(token!==revision)return}
    await refresh(token,true)
  }catch(failure){if(token===revision)error.value=failure.message}finally{if(token===revision)loading.value=false}
},{immediate:true})
watch(selectedOperation,async id=>{
  const token=++resultRevision;result.value=null;operation.value=null;if(!id)return
  try{const [status,answer]=await Promise.all([dailyClient.read(id),dailyClient.result(id)]);if(token!==resultRevision)return;operation.value=status;result.value=answer}catch(failure){if(token===resultRevision)error.value=failure.message}
})
watch(()=>props.mode,()=>{error.value=''})
async function start(){
  if(!valid.value||busy.value)return
  if(sameForecastSettings(autoForm,schedule.value))return control('start')
  busy.value=true;error.value='';const token=revision
  try{const row=await client.create({...autoForm,context:operationContext(app.activeContext)});if(token!==revision)return;schedule.value=row;fill(row);clearTimeout(timer);await refresh(token)}catch(failure){if(token===revision)error.value=failure.message}finally{if(token===revision)busy.value=false}
}
async function control(action){
  if(busy.value)return;busy.value=true;error.value='';const token=revision
  try{const row=await client.control(schedule.value.id,action);if(token!==revision)return;schedule.value=row;if(action!=='stop')fill(row);clearTimeout(timer);await refresh(token)}catch(failure){if(token===revision)error.value=failure.message}finally{if(token===revision)busy.value=false}
}
async function runManual(){
  if(!valid.value||locked.value)return;error.value=''
  const product=latestForecastProduct(products.value.filter(r=>r.contextKey===app.contextKey),manualForm.sourceId,manualForm.firstIssueTime,manualForm.leadHours)
  if(!product){error.value='缺少覆盖本次起报完整预见期的降雨产品；历史时刻需对应的历史产品。';return}
  await submit({operation:'manualForecast',context:operationContext(app.activeContext),parameterSchemeId:manualForm.parameterSchemeId,issueTime:manualForm.firstIssueTime,leadHours:manualForm.leadHours,forecastRainVersionId:product.versionId,name:'手动预报 · '+hour(manualForm.firstIssueTime)})
}
async function importProduct(event){
  const file=event.target.files?.[0];if(!file)return
  busy.value=true;error.value='';const token=revision
  try{if(file.size>2*1024*1024)throw new Error('JSON产品不能超过2 MB');const row=await client.importProduct(JSON.parse(await file.text()));if(token!==revision)return;products.value=await client.products();if(token!==revision)return;if(manual.value||!schedule.value?.enabled)form.value.sourceId=row.sourceId||row.versionId}catch(failure){if(token===revision)error.value=failure.message}finally{if(token===revision)busy.value=false;event.target.value=''}
}
function downloadExample(){downloadText('forecast-rain-example.json',JSON.stringify(sampleForecastProduct(app.contextKey,form.value.firstIssueTime),null,2))}
onBeforeUnmount(()=>{++revision;++resultRevision;clearTimeout(timer)})
</script>
<style scoped>
.automatic-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:20px;align-items:start}.automatic-controls,.automatic-output{min-width:0}.control-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:20px}.control-heading h3{margin:0;color:#2a5141;font-size:16px}.control-heading>span{color:#7b8b80;font-size:11px}.automatic-controls form{display:grid;gap:16px}.automatic-controls label{display:grid;gap:7px;font-size:12px;color:#536c60}.input{width:100%;min-width:0}.hour-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}.hour-fields.single{grid-template-columns:1fr}.import-actions{display:flex;align-items:center;gap:12px}.file-button{position:relative;overflow:hidden;display:inline-flex!important;cursor:pointer;font-size:11px!important}.file-button input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%}.text-link{border:0;padding:0;background:none;color:#35765a;font-size:11px;text-decoration:underline;text-underline-offset:3px;cursor:pointer;text-align:left}.start-button,.stop-button{width:100%;min-height:40px}.stop-button{border-color:#bdcbbf;color:#51665a}.failure{color:#a34732;font-size:12px;line-height:1.7;margin:0}.catch-up{display:flex;align-items:center;gap:12px}.catch-up span{font-size:11px;color:#7b897f}.schedule-state{margin-top:20px;padding-top:18px;border-top:1px solid #e0e7df;font-size:12px;line-height:1.8;color:#506e5b}.state-heading{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.state-heading i{width:7px;height:7px;border-radius:50%;background:#a5ada6}.state-heading i.running{background:#3f8862}.state-heading i.waiting{background:#b58b41}.state-heading span{margin-left:auto;font-size:11px;color:#7b897f}.schedule-state dl{display:grid;gap:6px;margin:12px 0 0}.schedule-state dl>div{display:flex;justify-content:space-between;gap:8px}.schedule-state dt{color:#7b897f}.schedule-state dd{margin:0;font-variant-numeric:tabular-nums}.status-reason{margin:10px 0 0;color:#8a7551;overflow-wrap:anywhere}.missing-issues{padding-left:16px;max-height:180px;overflow:auto;color:#a34732;font-size:12px;line-height:1.7}.service-note,.source-details{font-size:11px;line-height:1.8;color:#728276;margin:16px 0 0;overflow-wrap:anywhere}.source-details{border-top:1px solid #e0e7df;padding-top:12px}.source-details summary{cursor:pointer;color:#536c60}.source-details p{margin:10px 0}.automatic-output{display:grid;gap:14px}.issue-picker{display:flex;align-items:center;gap:16px;padding:12px 16px}.issue-picker label{display:flex;align-items:center;gap:10px;font-size:12px;color:#536c60}.issue-picker .input{width:185px}@media(max-width:1000px){.automatic-layout{grid-template-columns:1fr}.automatic-controls form{grid-template-columns:1fr 1fr;align-items:end}.automatic-controls .failure,.automatic-controls .missing-issues{grid-column:1/-1}}@media(max-width:600px){.automatic-controls form{grid-template-columns:1fr}}
</style>
