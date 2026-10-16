<template><section class="parameter-results" aria-label="本组参数">
  <header><b>本次计算实际参数</b><label v-if="searchRows.length"><input v-model="boundsOnly" type="checkbox" /> 仅看达到边界（{{ boundaryCount }}）</label></header>
  <p>数值来自选中的计算记录，临时编辑值不会出现在这里。达到边界只表示该次搜索受范围限制，不自动扩大范围或修改数值。</p>
  <div v-if="searchRows.length" class="table-wrap"><table><thead><tr><th>参数</th><th>方式</th><th>初值</th><th>结果值</th><th>范围</th><th>说明</th></tr></thead><tbody><tr v-for="row in visibleRows" :key="row.scope+':'+row.key"><td>{{ row.label || row.key }} <small>{{ row.symbol || row.key }} {{ row.unit==='1'?'':row.unit }}</small></td><td>{{ row.active?'参与率定':'未参与率定' }}</td><td>{{ num(row.initial) }}</td><td class="value">{{ num(row.value) }}</td><td>{{ row.active ? num(row.minimum)+' ～ '+num(row.maximum) : '固定' }}</td><td><span v-if="parameterAtBound(row)" class="boundary">{{ parameterAtBound(row) }}</span></td></tr></tbody></table></div>
  <details :open="!searchRows.length"><summary>小时尺度实际产流参数 · KI、KG及固定参数</summary><div class="actual"><div v-for="(value,key) in result.bestParameters || result.parameters || {}" :key="key"><span>{{ key }}</span><b>{{ num(value) }}</b></div></div><p>KI、KG为本次小时尺度运行系数；日总出流比例及地下径流分配比例是配套搜索坐标。</p></details>
  <details v-if="routing"><summary>坡面汇流实际参数</summary><div class="actual"><div v-for="key in ['RS','RI','RG']" :key="key"><span>{{ {RS:'地表径流',RI:'壤中流',RG:'地下径流'}[key] }}传播时间</span><b>{{ num(routing.default_slope_k_hours?.[key]) }} h</b></div></div></details>
  <details v-if="routing?.reach_muskingum"><summary>河道逐段K、X · K为传播时间（小时）</summary><div class="table-wrap"><table><thead><tr><th>河段</th><th>K（小时）</th><th>X</th></tr></thead><tbody><tr v-for="(row,id) in routing.reach_muskingum" :key="id"><td>{{ id }}</td><td>{{ num(row.k_hours) }}</td><td>{{ num(row.x) }}</td></tr></tbody></table></div><p>整体调整系数作用于该次冻结的起始K；上表为实际运行值。</p></details>
  <p v-else-if="result.optimizer">该旧记录未直接保存汇流参数明细，可在本次计算设置中查看关联条件。</p>
</section></template>
<script setup>
import {computed,ref} from 'vue'
import {parameterAtBound} from '@/services/calculationPresentation'
const props=defineProps({result:{type:Object,required:true}}),boundsOnly=ref(false)
const searchRows=computed(()=>props.result.searchPlan?.rows || [])
const boundaryCount=computed(()=>searchRows.value.filter(row=>parameterAtBound(row)).length)
const visibleRows=computed(()=>searchRows.value.filter(row=>!boundsOnly.value||parameterAtBound(row)))
const routing=computed(()=>props.result.bestRoutingParameters || props.result.routingParameters || props.result.routingSnapshot?.parameters)
const num=value=>value!=null&&Number.isFinite(Number(value))?Number(Number(value).toPrecision(7)):'—'
</script>
<style scoped>
.parameter-results{padding:16px;min-width:0}header{display:flex;gap:20px;justify-content:space-between;flex-wrap:wrap}label{display:flex;gap:8px;align-items:center}p{color:#6d8177;line-height:1.7}.table-wrap{max-height:300px;overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap;font-variant-numeric:tabular-nums}th,td{padding:10px;border-bottom:1px solid #e2ebe6;text-align:right}th:first-child,td:first-child{text-align:left}thead{position:sticky;top:0;background:#f4f8f5;color:#698074;font-weight:400}small{color:#758b7f;margin-left:8px}.value{font-weight:600}.boundary{padding:3px 7px;background:#fcf1dc;color:#8b641e;border-radius:3px}details{margin-top:18px}summary{cursor:pointer;color:#396b53}.actual{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;padding:16px 0}.actual>div{display:flex;gap:15px;justify-content:space-between}
</style>
