<template>
  <section class="panel panel-pad daily-result" aria-label="计算成果">
    <div class="result-toolbar"><h3>{{ title }}</h3><select v-if="entries.length > 1" v-model="entryId" class="input" aria-label="查看过程"><option v-for="row in entries" :key="row.id" :value="row.id">{{ row.label }}</option></select><div class="result-actions"><button v-if="sourceId" class="btn" type="button" @click="open('state')">模拟状态</button><button v-if="sourceId" class="btn" type="button" @click="open('diagnostics')">河道流量</button><button v-if="sourceId && entry?.endStateVersionId" class="btn" type="button" @click="open('simulation', true)">继续模拟</button><button v-if="operationId(operation)" class="btn" type="button" @click="router.push({path:'/archive',query:{operation:operationId(operation)}})">计算记录</button><button v-if="hydrograph" class="btn" type="button" @click="exportProcess">导出过程</button></div></div>
    <template v-if="hydrograph?.forecast?.length">
      <div class="hydro-metrics"><div><span>NSE</span><b>{{ number(metrics.nse ?? metrics.NSE, 3) }}</b></div><div><span>{{ chartMode==='hindcast'?'回算':'模拟' }}洪峰 · m³/s</span><b>{{ number(hydrograph.peak ?? peak) }}</b></div><div><span>洪峰误差 · %</span><b>{{ number(metrics.peakErr ?? metrics.peak_error_pct) }}</b></div><div><span>峰现时间误差 · h</span><b>{{ number(metrics.timeErrH ?? metrics.peak_time_error_h) }}</b></div><div><span>水量误差 · %</span><b>{{ number(metrics.volErr ?? metrics.volume_error_pct) }}</b></div></div>
      <div v-if="availableComparisons.length" class="comparison"><label>对比计算<select v-model="compareId" class="input" aria-label="对比计算"><option value="">不叠加</option><option v-for="row in availableComparisons" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><span v-if="comparisonLoading">读取过程…</span><span v-else-if="comparisonError">{{ comparisonError }}</span><span v-else-if="compareId && !overlay">两次计算时段不重合</span></div>
      <div class="chart"><HydrographChart :hydrograph="hydrograph" :mode="chartMode" :overlay="overlay" :overlay-label="comparisonName" :show-prev="false" display-mode="combined" /></div>
    </template>
    <p v-else class="empty">{{ result ? '计算成果已保存' : '完成计算后查看流量过程' }}</p>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import HydrographChart from '@/components/charts/HydrographChart.vue'
import { dailyClient, operationId, resultEntries } from '@/services/dailyOperationService'
import { downloadText, hydrographCsv } from '@/services/resultPresentation'
const props = defineProps({ result: Object, operation: Object, taskId: String, comparisonOptions:{type:Array,default:()=>[]}, title: {type:String,default:'流量过程'} })
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
const chartMode = computed(()=>['hindcast','rollingHindcast'].includes(props.operation?.operation||props.operation?.runType)?'hindcast':props.operation?.runType==='forecast'?'forecast':'simulation')
const compareId=ref(''),compareResult=ref(null),comparisonLoading=ref(false),comparisonError=ref('')
const availableComparisons=computed(()=>props.comparisonOptions.filter(row=>row.id!==operationId(props.operation)))
const comparisonName=computed(()=>availableComparisons.value.find(row=>row.id===compareId.value)?.name||'对比计算')
const overlay=computed(()=>{
  const times=new Set((hydrograph.value?.forecast||[]).map(row=>row.time))
  const rows=resultEntries(compareResult.value||{}),exact=rows.find(row=>row.id===entry.value?.id)
  const candidates=exact?[exact]:rows
  return candidates.find(row=>(row.hydrograph?.forecast||[]).some(point=>times.has(point.time)))?.hydrograph||null
})
let comparisonRevision=0
watch(compareId,async id=>{const token=++comparisonRevision;compareResult.value=null;comparisonError.value='';comparisonLoading.value=Boolean(id);if(!id)return;try{const answer=await dailyClient.result(id);if(token===comparisonRevision)compareResult.value=answer}catch(failure){if(token===comparisonRevision)comparisonError.value=failure.message}finally{if(token===comparisonRevision)comparisonLoading.value=false}})
watch(()=>operationId(props.operation),()=>{compareId.value='';compareResult.value=null})
const peak = computed(() => Math.max(...(hydrograph.value?.forecast || []).filter(row => row.value != null && Number.isFinite(Number(row.value))).map(row => Number(row.value))))
const number = (value, digits=2) => value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—'
function open(page, continuation=false) {
  router.push({path:`/${page}`, query:{source:sourceId.value, operation:operationId(props.operation) || undefined,
    event:entry.value?.id, ...(continuation ? {continue:'1'} : {}), label:props.operation?.name || props.title}})
}
function exportProcess() { downloadText(`${entry.value?.label || '流量过程'}.csv`, hydrographCsv(hydrograph.value, [], 'simulation')) }
</script>
<style scoped>
.daily-result{min-width:0}.result-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.result-toolbar h3{font-size:16px;color:#284e43;margin:0;margin-right:auto}.result-toolbar>.input{max-width:260px}.result-actions{display:flex;gap:8px;flex-wrap:wrap}.hydro-metrics{display:flex;gap:20px;padding:25px 0 14px;flex-wrap:wrap}.hydro-metrics>div{flex:1;min-width:105px;display:grid;gap:8px}.hydro-metrics span{font-size:11px;color:#75877c}.hydro-metrics b{font-size:22px;font-weight:500;color:#285b4b;font-variant-numeric:tabular-nums}.comparison,.comparison label{display:flex;align-items:center;gap:10px;font-size:12px;color:#5d7868;flex-wrap:wrap}.comparison{padding:10px 0 16px}.comparison .input{width:280px;max-width:100%}.comparison>span{font-size:11px;color:#8b7968}.chart{height:410px}.empty{padding:90px 15px;text-align:center;color:#76887e;font-size:13px}@media(max-width:700px){.chart{height:320px}.hydro-metrics{gap:12px}.hydro-metrics b{font-size:18px}}
</style>
