<template>
  <section class="panel observed-process" aria-label="实测雨量与流量过程">
    <header><div><h2>实测雨量与流量过程</h2><p>{{ start }} 至 {{ end }} · 点击过程定位回放时刻</p></div><label>降雨过程<select v-model="rainStation" class="input" aria-label="降雨过程测站"><option value="">流域面雨量（插值）</option><option v-for="row in rainStations" :key="row.id" :value="row.id">{{ row.name }} · 实测雨量</option></select></label><div class="rain-total">整场累计 <b>{{ data.total == null ? '缺测' : data.total.toFixed(1) + ' mm' }}</b></div></header>
    <div class="observed-chart"><BaseChart :option="option" @select-time="selectTime" /></div>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import { observationProcess } from '@/services/observationProcessService'
const props = defineProps({ preview: Object, summary: Object, stations: { type: Array, default: () => [] }, timeIndex: Number })
const emit = defineEmits(['select-time'])
const rainStation = ref('')
const rainStations = computed(() => Object.entries(props.preview?.rain || {}).filter(([, values]) => values.some(value => value != null)).map(([id]) => ({ id, name: props.stations.find(row => String(row.code || row.id) === id)?.name || id })))
watch(rainStations, rows => { if (rainStation.value && !rows.some(row => row.id === rainStation.value)) rainStation.value = '' })
const data = computed(() => observationProcess(props.preview, props.summary, rainStation.value))
const start = computed(() => data.value.times[0]?.replace('T', ' ').slice(0, 16))
const end = computed(() => data.value.times.at(-1)?.replace('T', ' ').slice(0, 16))
function selectTime(index) { if (Number.isInteger(index) && index >= 0 && index < data.value.times.length) emit('select-time', index) }
const option = computed(() => ({ animation: false, tooltip: { trigger: 'axis' }, legend: { data: ['小时雨量', '实测流量'], top: 0 },
  grid: [{ left: 66, right: 30, top: 36, height: '23%' }, { left: 66, right: 30, top: '42%', bottom: 44 }],
  xAxis: [0, 1].map(i => ({ type: 'category', gridIndex: i, data: data.value.times, axisLabel: { show: i === 1, formatter: time => time.slice(5, 16).replace('T', '\n') }, axisTick: { show: false } })),
  yAxis: [{ type: 'value', gridIndex: 0, inverse: true, name: '雨量 mm', nameLocation:'middle',nameGap:44,min: 0 }, { type: 'value', gridIndex: 1, name: '流量 m³/s',nameLocation:'middle',nameGap:44, min: 0 }],
  axisPointer: { link: [{ xAxisIndex: 'all' }] }, dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }, { type: 'slider', xAxisIndex: [0, 1], height: 13, bottom: 2 }],
  series: [{ name: '小时雨量', type: 'bar', data: data.value.rain, barMaxWidth: 10, itemStyle: { color: '#4296c3' }, markLine: { silent: true, symbol: 'none', data: [{ xAxis: data.value.times[props.timeIndex || 0] }], label: { show: false } } },
    { name: '实测流量', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: data.value.flow, showSymbol: false, connectNulls: false, lineStyle: { color: '#1b365d', width: 2 } }] }))
</script>
<style scoped>
.observed-process{padding:16px;min-width:0}.observed-process header{display:flex;align-items:center;gap:24px;flex-wrap:wrap}.observed-process h2{font-size:16px;margin:0;color:#28564f}.observed-process p{font-size:14px;color:#71877e;margin:7px 0}.observed-process label{margin-left:auto;font-size:14px;display:flex;gap:10px;align-items:center}.observed-process select{min-width:230px;min-height:36px}.rain-total{font-size:14px;display:grid;gap:6px;color:#6c8279}.rain-total b{font-size:20px;color:#286a73}.observed-chart{height:330px;margin-top:12px}@media(max-width:1400px){.observed-chart{height:285px}.observed-process header{gap:12px}}
</style>
