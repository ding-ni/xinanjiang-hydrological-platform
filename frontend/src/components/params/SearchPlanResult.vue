<template>
  <details class="search-plan">
    <summary>本次率定范围与固定参数 <span>{{ plan.rows.filter(row => row.active).length }} 项搜索</span></summary>
    <div class="plan-tools"><button type="button" class="btn" @click="exportPlan">搜索参数表 CSV</button></div>
    <div class="plan-table"><table aria-label="本次搜索参数"><thead><tr><th>参数</th><th>方式</th><th>下限</th><th>上限</th><th>起始值</th><th>结果值</th></tr></thead><tbody><tr v-for="row in plan.rows" :key="`${row.scope || ''}:${row.key}`"><td><b>{{ row.symbol }}</b> {{ row.label }}{{ row.unit === 'h' ? ' (h)' : '' }}</td><td>{{ row.active ? '率定' : '固定' }}</td><td>{{ number(row.minimum) }}</td><td>{{ number(row.maximum) }}</td><td :title="String(row.initial)">{{ number(row.initial) }}</td><td :title="String(row.value)">{{ number(row.value) }}</td></tr></tbody></table></div>
    <p v-if="plan.target === 'joint'">产流、坡面与河道采用同一组联合候选；K 倍数以冻结的起始汇流方案为基准。采用候选时同时恢复汇流与科学条件。</p>
    <p v-if="plan.target === 'routing'">产流、河道及单元单列参数保持不变；全局坡面汇流结果保存为新方案。</p>
    <p v-else-if="plan.hourlySource && plan.target !== 'joint'">小时出流系数：KI {{ number(plan.hourlySource.KI) }} · KG {{ number(plan.hourlySource.KG) }}；汇流方案保持不变。</p>
  </details>
</template>
<script setup>
import { csv, downloadText } from '@/services/resultPresentation'
const props = defineProps({ plan: { type: Object, required: true } })
const number = value => Number.isFinite(value) ? Number(value.toPrecision(7)) : '—'
function exportPlan() { downloadText('率定搜索参数.csv', csv([['参数', '符号', '单位', '方式', '下限', '上限', '起始值', '结果值'], ...props.plan.rows.map(row => [row.label, row.symbol, row.unit, row.active ? '率定' : '固定', row.minimum, row.maximum, row.initial, row.value])])) }
</script>
<style scoped>
.search-plan{margin:14px 0;border-top:1px solid #dae5dc;padding-top:12px;min-width:0;color:#405e4c;font-size:12px}.search-plan summary{cursor:pointer}.search-plan summary span{color:#829080;margin-left:10px;font-size:11px}.plan-tools{text-align:right;margin:8px 0}.plan-table{overflow:auto;max-height:320px}table{width:100%;border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums}td,th{padding:8px;text-align:right;border-bottom:1px solid #e5ebe3;white-space:nowrap}td:first-child,th:first-child{text-align:left}td b{margin-right:5px;color:#286b57}th{background:#f3f7f0}.search-plan p{font-size:11px;color:#81907d;line-height:1.6}
</style>
