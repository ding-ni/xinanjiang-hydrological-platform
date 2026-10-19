<template>
  <section class="panel panel-pad daily-result" aria-label="计算成果">
    <div class="result-toolbar"><h3>{{ title }}</h3><select v-if="entries.length > 1" v-model="entryId" class="input" aria-label="查看过程"><option v-for="row in entries" :key="row.id" :value="row.id">{{ row.label }}</option></select><div class="result-actions"><button v-if="sourceId" class="btn" type="button" @click="open('state')">状态分布</button><button v-if="sourceId" class="btn" type="button" @click="open('diagnostics')">河道流量</button><button v-if="sourceId && entry?.endStateVersionId" class="btn" type="button" @click="open('simulation', true)">继续模拟</button><button v-if="operationId(operation) && !hideRecordLink" class="btn" type="button" @click="router.push({path:'/archive',query:{operation:operationId(operation)}})">计算记录</button><button v-if="hydrograph" class="btn" type="button" @click="exportProcess">导出过程</button></div></div>
    <template v-if="!hideSaveBar && canonicalResultId && result"><details v-if="compact" class="result-details"><summary>成果整理</summary><ResultSaveBar :result-id="canonicalResultId" :suggestion="resultSuggestion" :hide-record-link="true" /></details><ResultSaveBar v-else :result-id="canonicalResultId" :suggestion="resultSuggestion" :hide-record-link="true" /></template>
    <template v-if="hydrograph?.forecast?.length">
      <div class="hydro-metrics"><div v-if="chartMode!=='forecast'"><span>NSE</span><b>{{ number(normalizedMetrics.nse, 3) }}</b></div><div><span>{{ chartMode==='hindcast'?'回算':chartMode==='forecast'?'预报':'模拟' }}洪峰 · m³/s</span><b>{{ number(hydrograph.peak ?? peak) }}</b></div><template v-if="chartMode==='forecast'"><div><span>起报时刻</span><b class="issue-time">{{ (result?.issue?.issueTime || '').replace('T',' ').slice(0,16) }}</b></div><div><span>预见期 · h</span><b>{{ result?.issue?.leadHours }}</b></div></template><template v-else><div><span>洪峰误差 · %</span><b>{{ signedNumber(normalizedMetrics.peak) }}</b></div><div><span>峰现时间误差 · h</span><b>{{ number(normalizedMetrics.time) }}</b></div><div><span>水量误差 · %</span><b>{{ signedNumber(normalizedMetrics.volume) }}</b></div></template></div>
      <div v-if="availableComparisons.length" class="comparison"><label>对比计算<select v-model="compareId" class="input" aria-label="对比计算"><option value="">不叠加</option><option v-for="row in availableComparisons" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><span v-if="comparisonLoading">读取过程…</span><span v-else-if="comparisonError">{{ comparisonError }}</span><span v-else-if="compareId && !overlay">所选记录没有同一洪水边界的过程，未叠加</span></div>
      <details v-if="peaks" class="peak-details"><summary>实测与模拟峰现时刻</summary><p>实测：{{ peaks.observedTime.replace('T',' ') }} · {{ number(peaks.observedPeak) }} m³/s；模拟：{{ peaks.simulatedTime.replace('T',' ') }} · {{ number(peaks.simulatedPeak) }} m³/s。</p></details><div class="chart"><CalibrationProcessChart :hydrograph="hydrograph" :forcing="hydrograph.forcing || {precipitation:[]}" :visible-components="{}" :show-observed="Boolean(hydrograph.obs?.length)" :current-curve="{label:chartMode==='forecast'?'预报流量':chartMode==='hindcast'?'回算流量':'模拟流量',color:'#D55E00'}" :comparisons="overlay?[{taskId:compareId,label:comparisonName,color:'#187f84',hydrograph:overlay}]:[]" /></div>
    </template>
    <p v-else class="empty">{{ result ? '计算成果已保存' : '完成计算后查看流量过程' }}</p>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import CalibrationProcessChart from '@/components/charts/CalibrationProcessChart.vue'
import {peakDetails,processMetrics as normalizeMetrics} from '@/services/calculationPresentation'
import ResultSaveBar from '@/components/model/ResultSaveBar.vue'
import { dailyClient, operationId, resultEntries } from '@/services/dailyOperationService'
import { downloadText, hydrographCsv } from '@/services/resultPresentation'
const props = defineProps({ result: Object, operation: Object, taskId: String, hideSaveBar: Boolean, hideRecordLink:Boolean, compact:Boolean, comparisonOptions:{type:Array,default:()=>[]}, title: {type:String,default:'流量过程'} })
const emit=defineEmits(['process-change'])
const router = useRouter(), entryId = ref('')
const entries = computed(() => resultEntries(props.result || {}, props.taskId || props.operation?.resultTaskId || props.operation?.taskId))
watch(entries, rows => { if (!rows.some(row => row.id === entryId.value)) entryId.value = rows[0]?.id || '' }, {immediate:true})
const entry = computed(() => entries.value.find(row => row.id === entryId.value))
const hydrograph = computed(() => {
  const graph=entry.value?.hydrograph
  return graph && entry.value.issueTime ? {...graph,startMark:entry.value.issueTime} : graph
})
const sourceId = computed(() => entry.value?.taskId || props.result?.stateCalculationTaskId || props.taskId || props.operation?.resultTaskId)
const metrics = computed(() => hydrograph.value?.metrics || entry.value?.metrics || {})
const normalizedMetrics=computed(()=>normalizeMetrics(metrics.value)),peaks=computed(()=>peakDetails(hydrograph.value||{}))
watch(entryId,id=>emit('process-change',id))
const canonicalResultId = computed(() => props.operation?.canonicalResultId || props.result?.operationId || operationId(props.operation) || props.result?.taskId || props.taskId)
const resultSuggestion = computed(() => props.operation?.name || props.result?.name || props.title)
const chartMode = computed(()=>['hindcast','rollingHindcast'].includes(props.operation?.operation||props.operation?.runType)?'hindcast':['forecast','automaticForecast','manualForecast'].includes(props.operation?.operation||props.operation?.runType)||props.result?.runType==='forecast'?'forecast':'simulation')
const compareId=ref(''),compareResult=ref(null),comparisonLoading=ref(false),comparisonError=ref('')
const availableComparisons=computed(()=>props.comparisonOptions.filter(row=>row.id!==operationId(props.operation)))
const comparisonName=computed(()=>availableComparisons.value.find(row=>row.id===compareId.value)?.name||'对比计算')
const overlay=computed(()=>{
  const times=(hydrograph.value?.forecast||[]).map(row=>String(row.time).replace(' ','T'))
  const rows=resultEntries(compareResult.value||{}),exact=rows.find(row=>row.id===entry.value?.id)
  const candidates=exact?[exact]:rows
  return candidates.find(row=>{const other=(row.hydrograph?.forecast||[]).map(point=>String(point.time).replace(' ','T'));return times.length&&times.length===other.length&&times.every((value,index)=>value===other[index])})?.hydrograph||null
})
let comparisonRevision=0
watch(compareId,async id=>{const token=++comparisonRevision;compareResult.value=null;comparisonError.value='';comparisonLoading.value=Boolean(id);if(!id)return;try{const answer=await dailyClient.result(id);if(token===comparisonRevision)compareResult.value=answer}catch(failure){if(token===comparisonRevision)comparisonError.value=failure.message}finally{if(token===comparisonRevision)comparisonLoading.value=false}})
watch(()=>operationId(props.operation),()=>{compareId.value='';compareResult.value=null})
const peak = computed(() => Math.max(...(hydrograph.value?.forecast || []).filter(row => row.value != null && Number.isFinite(Number(row.value))).map(row => Number(row.value))))
const number = (value, digits=2) => value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—'
const signedNumber = value => (value != null && Number.isFinite(Number(value)) && Number(value)>0 ? '+' : '') + number(value)
function open(page, continuation=false) {
  if (page === 'diagnostics') { router.push({path:'/results/'+encodeURIComponent(canonicalResultId.value)+'/sections',query:{process:entry.value?.id}}); return }
  router.push({path:`/${page}`, query:{source:sourceId.value, operation:operationId(props.operation) || undefined,
    event:entry.value?.id, ...(continuation ? {continue:'1'} : {}), label:props.operation?.name || props.title}})
}
function exportProcess() { downloadText(`${entry.value?.label || '流量过程'}.csv`, hydrographCsv(hydrograph.value, [], chartMode.value)) }
</script>
<style scoped>
.hydro-metrics b.issue-time{font-size:16px}
.result-details{margin-top:14px;font-size:12px;color:#75877c}.result-details summary{cursor:pointer}
.daily-result{min-width:0}.result-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.result-toolbar h3{font-size:16px;color:#284e43;margin:0;margin-right:auto}.result-toolbar>.input{max-width:260px}.result-actions{display:flex;gap:8px;flex-wrap:wrap}.hydro-metrics{display:flex;gap:20px;padding:25px 0 14px;flex-wrap:wrap}.hydro-metrics>div{flex:1;min-width:105px;display:grid;gap:8px}.hydro-metrics span{font-size:11px;color:#75877c}.hydro-metrics b{font-size:22px;font-weight:500;color:#285b4b;font-variant-numeric:tabular-nums}.comparison,.comparison label{display:flex;align-items:center;gap:10px;font-size:12px;color:#5d7868;flex-wrap:wrap}.comparison{padding:10px 0 16px}.comparison .input{width:280px;max-width:100%}.comparison>span{font-size:11px;color:#8b7968}.chart{height:410px}.empty{padding:90px 15px;text-align:center;color:#76887e;font-size:13px}@media(max-width:700px){.chart{height:320px}.hydro-metrics{gap:12px}.hydro-metrics b{font-size:18px}}
</style>
