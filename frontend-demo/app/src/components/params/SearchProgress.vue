<template>
  <section v-if="history.length" class="search-progress" aria-label="参数搜索过程">
    <p>已评价 {{ optimizer?.evaluations ?? progress?.evaluations }} 次 · 最佳目标值 {{ number(best) }}<span v-if="optimizer"> · {{ termination }}</span></p>
    <BaseChart class="trace" :option="option" />
  </section>
</template>
<script setup>
import { computed } from 'vue'
import BaseChart from '@/components/charts/BaseChart.vue'
const props = defineProps({ optimizer: Object, progress: Object })
const history = computed(() => props.optimizer?.history || props.progress?.history || [])
const values = computed(() => { let best = Infinity; return history.value.map(row => { if (Number.isFinite(row.loss)) best = Math.min(best, row.loss); return Number.isFinite(best) ? best : null }) })
const best = computed(() => props.optimizer?.best_loss ?? props.optimizer?.bestLoss ?? props.progress?.bestLoss ?? history.value.at(-1)?.bestLoss ?? values.value.at(-1))
const number = value => Number.isFinite(value) ? value.toFixed(4) : '—'
const termination = computed(() => ({ BUDGET_EXHAUSTED: '达到评价次数上限', SEARCH_SPACE_EXHAUSTED: '搜索结束', FIXED_PARAMETERS_EVALUATED: '试算完成' }[props.optimizer?.terminationReason] || '计算完成'))
const option = computed(() => ({ animation: false, tooltip: { trigger: 'axis' }, grid: { left: 60, right: 20, top: 25, bottom: 35 }, xAxis: { type: 'category', name: '评价次数', data: history.value.map(row => row.evaluation) }, yAxis: { type: 'value', name: '目标值', scale: true }, series: [{ type: 'line', name: '最佳目标值', data: values.value, showSymbol: false, lineStyle: { color: '#217966', width: 2 } }] }))
</script>
<style scoped>.search-progress{margin:12px 0;font-size:12px;color:#496a5a}.trace{height:200px}</style>
