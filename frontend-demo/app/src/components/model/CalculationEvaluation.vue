<template>
  <section class="evaluation" aria-label="全场评价">
    <button class="btn export-evaluation" @click="exportEvaluation">导出逐场评价</button>
    <p class="explanation">逐场计算后按用途汇总。NSE取平均值，洪峰和水量取绝对误差平均值，峰现误差取中位数。</p>
    <p v-if="result.qualificationPolicy?.method==='NSE_THRESHOLD'" class="explanation">本次合格判据仅为 NSE ≥ {{ result.qualificationPolicy.minimum }}，不代表洪峰、峰现和水量误差全部合格。</p>
    <div class="table-wrap"><table><thead><tr><th>用途</th><th>场数</th><th title="越接近1越好；0为实测平均值基准">平均NSE ⓘ</th><th>平均绝对洪峰误差</th><th>峰现误差中位数</th><th>平均绝对水量误差</th></tr></thead><tbody><tr v-for="row in presentation.summaries" :key="row.purpose"><th>{{ row.purpose }}</th><td>{{ row.count }}</td><td>{{ num(row.nse,3) }}</td><td>{{ num(row.peak,1,'%') }}</td><td>{{ num(row.time,1,' h') }}</td><td>{{ num(row.volume,1,'%') }}</td></tr></tbody></table></div>
    <p v-if="presentation.summaries.some(row=>row.count && row.basis==='合并评价')" class="explanation">部分旧记录没有可恢复的逐场评价，现显示其原合并评价。</p>
    <p v-for="row in incomplete" :key="row.purpose" class="explanation">{{ row.purpose }}共 {{ row.count }} 场；有效指标：NSE {{ row.validCounts.nse }} 场、洪峰 {{ row.validCounts.peak }} 场、峰现 {{ row.validCounts.time }} 场、水量 {{ row.validCounts.volume }} 场。缺失值不参与统计。</p>
    <details v-if="presentation.events.length"><summary>逐场指标（{{ presentation.events.length }} 场）</summary><div class="table-wrap"><table><thead><tr><th>洪水</th><th>用途</th><th>NSE</th><th>洪峰误差</th><th>峰现误差</th><th>水量误差</th></tr></thead><tbody><tr v-for="row in presentation.events" :key="row.id"><td>{{ row.name }}</td><td>{{ row.purpose }}</td><td>{{ num(row.nse,3) }}</td><td>{{ num(row.peak,1,'%') }}</td><td>{{ num(row.time,1,' h') }}</td><td>{{ num(row.volume,1,'%') }}</td></tr></tbody></table></div></details>
    <details v-if="showOverlapDetails && result.overlapGroupMetrics?.length"><summary>搜索评价详情 · 合并窗口</summary><p class="explanation">原搜索评价保持不变；相接或重叠窗口合并后，整段实测与模拟最大峰可能来自不同洪水。上表按原选用窗口逐场汇总，重叠窗口不代表独立样本。</p><div class="table-wrap"><table><thead><tr><th>时段</th><th>用途</th><th>场数</th><th>NSE</th><th>峰现误差</th><th>实测峰现</th><th>模拟峰现</th></tr></thead><tbody><tr v-for="row in result.overlapGroupMetrics" :key="row.groupId"><td>{{ hour(row.start) }} — {{ hour(row.end) }}</td><td>{{ purposeNames[row.group] || row.group }}</td><td>{{ row.memberEventIds?.length }}</td><td>{{ num(row.NSE,3) }}</td><td>{{ num(row.peak_time_error_hours,1,' h') }}</td><td>{{ hour(mergedPeaks[row.groupId]?.observedTime) }}</td><td>{{ hour(mergedPeaks[row.groupId]?.simulatedTime) }}</td></tr></tbody></table></div></details>
  </section>
</template>
<script setup>
import {computed} from 'vue'
import {evaluationPresentation,purposeNames,mergedPeakDetails} from '@/services/calculationPresentation'
import {csv,downloadText} from '@/services/resultPresentation'
const props=defineProps({result:{type:Object,required:true},showOverlapDetails:{type:Boolean,default:true}})
const presentation=computed(()=>evaluationPresentation(props.result))
const incomplete=computed(()=>presentation.value.summaries.filter(row=>presentation.value.events.some(event=>event.purpose===row.purpose)&&Object.values(row.validCounts).some(count=>count<row.count)))
const mergedPeaks=computed(()=>Object.fromEntries((props.result.overlapGroupMetrics || []).map(row=>[row.groupId,mergedPeakDetails(props.result,row)])))
const num=(value,digits,suffix='')=>value!=null&&Number.isFinite(Number(value))?Number(value).toFixed(digits)+suffix:'—'
const hour=value=>String(value||'—').replace('T',' ').slice(0,16)
function exportEvaluation(){
  const rows=presentation.value
  downloadText('逐场评价.csv',csv([
    ['逐场汇总','场数','平均NSE','平均绝对洪峰误差（%）','峰现误差中位数（h）','平均绝对水量误差（%）'],
    ...rows.summaries.map(row=>[row.purpose,row.count,row.nse,row.peak,row.time,row.volume]),
    [],['洪水','用途','NSE','洪峰误差（%）','峰现误差（h）','水量误差（%）'],
    ...rows.events.map(row=>[row.name,row.purpose,row.nse,row.peak,row.time,row.volume])]))
}
</script>
<style scoped>
.evaluation{padding:16px;min-width:0}.export-evaluation{float:right;margin:0 0 12px 16px}.explanation{color:#6c8178;line-height:1.7;margin:0 0 12px}.table-wrap{overflow:auto;max-height:380px;clear:both}table{width:100%;border-collapse:collapse;white-space:nowrap;font-variant-numeric:tabular-nums}th,td{padding:11px 10px;border-bottom:1px solid #e2ebe6;text-align:right;font-weight:400}th:first-child,td:first-child{text-align:left}thead{color:#617d70;background:#f5f9f6;position:sticky;top:0}details{margin-top:16px}summary{cursor:pointer;color:#396951;margin-bottom:12px}
</style>
