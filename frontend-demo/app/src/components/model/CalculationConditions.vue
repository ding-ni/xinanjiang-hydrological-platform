<template><section class="conditions" aria-label="本次计算设置"><p>以下为这次计算实际保存的设置，查看不会修改当前方案。</p><dl><dt>方案</dt><dd>{{ request.scheme?.name || result.lineage?.schemeName || '快捷模拟' }}</dd><dt>起算方式</dt><dd>{{ policy==='continuous-origin'?'连续演算':'各场独立预热' }}</dd><dt>连续演算起点</dt><dd>{{ hour(result.continuousOrigin || request.calibration?.continuousOrigin || request.period?.continuousOrigin) }}</dd><dt>预热</dt><dd>{{ request.period?.warmupDays ?? '原保存条件' }} 天</dd><dt>算法</dt><dd>{{ algorithm }}</dd><dt>评价预算</dt><dd>{{ request.calibration?.optimizer?.maxEvaluations ?? '固定参数模拟' }}</dd></dl>
  <div class="table-wrap"><table><thead><tr><th>洪水／时段</th><th>开始</th><th>结束</th><th>用途</th></tr></thead><tbody><tr v-for="row in windows" :key="row.id"><td>{{ row.name || row.id }}</td><td>{{ hour(row.start) }}</td><td>{{ hour(row.end) }}</td><td>{{ purposeNames[row.purpose] || row.purpose || '模拟' }}</td></tr></tbody></table></div>
  <p v-if="conditionsLoading">正在读取本次蒸散发输入…</p><p v-else-if="conditionsError" role="alert">{{ conditionsError }}</p><template v-else-if="evaporation"><p v-if="evaporation.mode==='constant-assumption'">蒸散发估计输入：固定 {{ evaporation.valueMmPerHour }} mm/h。</p><p v-else-if="evaporation.monthlyValuesMmPerDay">蒸散发估计输入：1—12月依次为 {{ evaporation.monthlyValuesMmPerDay.join('、') }} mm/日，逐时输入为相应日值除以24。</p><p v-else>蒸散发输入：沿用本次冻结的逐时资料。</p></template>
  <details v-if="result.objectiveTerms?.length"><summary>综合拟合得分的组成</summary><p>得分是内部搜索目标的相反数，越高越好，不是百分制。NSE、KGE按权重加入；洪峰、水量、峰现误差按各自尺度扣分。不同权重或不同评价口径不能直接比较得分。</p><ul><li v-for="term in result.objectiveTerms" :key="term.metric">{{ term.metric }}：权重 {{ term.weight }}<template v-if="term.scale">，误差尺度 {{ term.scale }}</template></li></ul></details>
</section></template>
<script setup>
import {computed,ref,watch} from 'vue'
import {modelApi} from '@/services/modelBackendService'
import {purposeNames} from '@/services/calculationPresentation'
const props=defineProps({result:{type:Object,required:true},request:{type:Object,default:()=>({})}})
const evaporation=ref(null),conditionsLoading=ref(false),conditionsError=ref('')
let sequence=0
watch(()=>props.result.science?.versionId || props.request.science?.versionId,async version=>{
  const token=++sequence;evaporation.value=null;conditionsError.value='';conditionsLoading.value=Boolean(version)
  if(!version){evaporation.value=props.request.inputs?.evaporation;return}
  try{const saved=await modelApi('science-conditions?versionId='+encodeURIComponent(version));if(token===sequence)evaporation.value=saved.evaporation}
  catch(error){if(token===sequence)conditionsError.value=error.message}
  finally{if(token===sequence)conditionsLoading.value=false}
},{immediate:true})
const policy=computed(()=>props.result.statePolicy || props.request.calibration?.statePolicy)
const windows=computed(()=>props.request.period?.events || props.request.period?.windows || props.request.events || [{id:'process',name:'连续时段',start:props.request.period?.startTime,end:props.request.period?.endTime}])
const algorithm=computed(()=>({DIFFERENTIAL_EVOLUTION:'差分进化',FIXED_PARAMETERS:'固定参数'}[props.request.calibration?.optimizer?.algorithm] || props.request.calibration?.optimizer?.algorithm || '固定参数模拟'))
const hour=value=>String(value||'—').replace('T',' ').slice(0,16)
</script>
<style scoped>.conditions{padding:16px;color:#486557}.conditions p{line-height:1.7;color:#6c8175}dl{display:grid;grid-template-columns:150px minmax(0,1fr);gap:12px}dd{margin:0}dt{color:#748679}.table-wrap{overflow:auto;max-height:380px}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{padding:10px;border-bottom:1px solid #e0e8e2;text-align:left}details{margin-top:20px}summary{cursor:pointer}li{margin:8px 0}</style>
