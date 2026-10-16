<template>
  <div class="hydrology-figure">
    <div class="figure-legend" aria-label="率定过程图图例">
      <span v-if="forcing.precipitation?.length"><i class="bar rain" />{{ rainfallLabel }}</span>
      <span v-if="hasTemperature"><i class="line temperature" />面平均气温</span>
      <span v-if="showObserved && !compactLegend"><i class="line observed" />实测流量</span>
      <span v-if="!observedOnly && showCurrent && !compactLegend"><i class="line simulated" :style="{ borderColor: currentCurve.color }" />{{ currentCurve.label }}</span>
      <span v-for="trial in compactLegend?[]:visibleComparisons" :key="trial.taskId"><i class="line" :style="{ borderColor: trial.color, borderTopStyle: trial.lineType || 'solid' }" />{{ trial.label }}</span>
      <span v-for="item in activeComponentLegends" :key="item.key"><i class="line component" :style="{ borderColor: item.color, borderTopStyle: item.style }" />{{ item.name }}</span>
    </div>
    <BaseChart class="hydrology-chart" :option="option" @select-time="index => emit('select-time', hydrograph.times?.[index] || hydrograph.forecast?.[index]?.time || hydrograph.obs?.[index]?.time)" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import { alignedTrialSeries } from '@/services/manualCalibrationService'
const emit = defineEmits(['select-time'])

const props = defineProps({
  hydrograph: { type: Object, required: true },
  forcing: { type: Object, required: true },
  visibleComponents: { type: Object, required: true },
  displayMode: { type: String, default: 'process' },
  observedOnly: { type: Boolean, default: false },
  showObserved:{type:Boolean,default:true},showCurrent:{type:Boolean,default:true},compactLegend:Boolean
  ,comparisons: { type: Array, default: () => [] }
  ,currentCurve: { type: Object, default: () => ({ label: '模拟流量', color: '#D55E00' }) }
})

const componentDefs = [
  { key: 'rainfall', name: '降雨径流', color: '#009E73', style: 'dashed' },
  { key: 'snowmelt', name: '融雪径流', color: '#0072B2', style: 'dashed' },
  { key: 'glacier', name: '冰川融水', color: '#56B4E9', style: 'dotted' },
  { key: 'baseflow', name: '地下径流', color: '#7A6F5D', style: 'dotted' }
]

const activeComponentLegends = computed(() => props.displayMode === 'components' ? componentDefs.filter((item) => props.visibleComponents[item.key]) : [])
const hasTemperature = computed(() => !props.observedOnly && Boolean(props.forcing.temperature?.length))
const visibleComparisons = computed(() => props.observedOnly ? [] : props.comparisons)
const rainfallLabel = computed(() => props.forcing.rainfallLabel || (props.forcing.controlAreaKm2 ? '控制区面积加权降雨' : '面平均降雨'))

function axisTime(value) {
  const text = String(value)
  if (text.includes('T')) return `${text.slice(5, 10)}\n${text.slice(11, 16)}`
  return `${text.slice(0, 5)}\n${text.slice(6)}`
}

const option = computed(() => {
  const h = props.hydrograph
  const times = h?.times?.length ? h.times : (h?.forecast?.length ? h.forecast : h?.obs || []).map((item) => item.time)
  const observedMap = new Map((h?.obs || []).map((item) => [item.time, item.value]))
  const observed = times.map((time) => observedMap.get(time) ?? null)
  const simulatedMap = new Map((h?.forecast || []).map((item) => [item.time, item.value]))
  const simulated = times.map((time) => simulatedMap.get(time) ?? null)
  const escape=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))
  const tooltip={trigger:'axis',appendToBody:false,confine:true,enterable:false,hideDelay:0,transitionDuration:0,
    backgroundColor:'rgba(255,255,255,.98)',borderColor:'#bed0c7',textStyle:{color:'#25483b',fontSize:12},
    extraCssText:'max-width:360px;pointer-events:none;',formatter(params){
      const index=params[0]?.dataIndex
      if(index==null)return ''
      const rows=[[rainfallLabel.value,props.forcing.precipitation?.[index],'mm']]
      if(hasTemperature.value)rows.push(['面平均气温',props.forcing.temperature[index],'°C'])
      if(props.showObserved)rows.push(['实测流量',observed[index],'m³/s'])
      if(!props.observedOnly&&props.showCurrent)rows.push([props.currentCurve.label,simulated[index],'m³/s'])
      for(const trial of visibleComparisons.value)rows.push([trial.label,alignedTrialSeries(trial.hydrograph,times)[index],'m³/s'])
      return `<b>${escape(String(times[index]||'').replace('T',' '))}</b><br>`+rows.map(([label,value,unit])=>`${escape(label)}：${value==null?'缺测':Number(value).toFixed(2)+' '+unit}`).join('<br>')
    }}
  const trialSeries = axis => visibleComparisons.value.map(trial => ({
    id: trial.taskId, name: trial.label, type: 'line', xAxisIndex: axis, yAxisIndex: axis,
    data: alignedTrialSeries(trial.hydrograph, times), connectNulls: false, showSymbol: false,
    lineStyle: { width: trial.selected?3:1.8, color: trial.color, type: trial.lineType || 'solid' }, itemStyle: { color: trial.color },
    emphasis: { focus: 'series' }, z: 6
  }))
  const components = props.forcing.components || {}
  const componentSeries = activeComponentLegends.value.map((item) => ({
    name: item.name,
    type: 'line',
    xAxisIndex: 2,
    yAxisIndex: 2,
    data: components[item.key] || [],
    showSymbol: false,
    connectNulls: false,
    lineStyle: { width: 1.5, type: item.style, color: item.color, opacity: 0.88 },
    emphasis: { focus: 'series' },
    z: 3
  }))

  if (!hasTemperature.value) {
    return {
      animation: false,
      textStyle: { fontFamily: 'Noto Sans CJK SC, Microsoft YaHei UI, sans-serif' },
      tooltip,
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 64, right: 26, top: 12, height: '17%' },
        { left: 64, right: 26, top: '30%', bottom: 72 }
      ],
      xAxis: [
        { type: 'category', gridIndex: 0, boundaryGap: true, data: times, axisLabel: { show: false }, axisTick: { show: false } },
        { type: 'category', gridIndex: 1, boundaryGap: false, data: times, axisLabel: { color: '#52697c', fontSize: 10, interval: Math.max(0, Math.ceil(times.length / 7) - 1), formatter: axisTime }, axisTick: { show: false } }
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, inverse: true, name: '降雨量 (mm)', nameLocation: 'middle', nameGap: 42, min: 0, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#e7edf1' } } },
        { type: 'value', gridIndex: 1, name: '流量 (m³/s)', nameLocation: 'middle', nameGap: 48, min: 0, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#e3e9ee' } } }
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1], height: 13, bottom: 4, showDetail: false }
      ],
      series: [
        { name: rainfallLabel.value, type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: props.forcing.precipitation || [], barMaxWidth: 8, itemStyle: { color: '#4C9BD3' } },
        { name: '实测流量', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: props.showObserved?observed:[], showSymbol: false, connectNulls: false, lineStyle: { width: 2.2, color: '#1B365D' }, itemStyle: { color: '#1B365D' } },
        ...(!props.observedOnly && props.showCurrent ? [{ name: props.currentCurve.label, type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: simulated, connectNulls: false, showSymbol: false, lineStyle: { width: 2.3, type: 'dashed', color: props.currentCurve.color }, itemStyle: { color: props.currentCurve.color } }] : []),
        ...trialSeries(1)
      ]
    }
  }

  return {
    animation: false,
    textStyle: { fontFamily: 'Microsoft YaHei UI, Microsoft YaHei, sans-serif' },
    tooltip,
    axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: '#557184', width: 1 } },
    grid: [
      { left: 64, right: 30, top: 12, height: '18%' },
      { left: 64, right: 30, top: '25%', height: '15%' },
      { left: 64, right: 30, top: '47%', bottom: 72 }
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, boundaryGap: true, data: times, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#aebdca' } } },
      { type: 'category', gridIndex: 1, boundaryGap: false, data: times, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#aebdca' } } },
      { type: 'category', gridIndex: 2, boundaryGap: false, data: times, axisLabel: { color: '#52697c', fontSize: 10, interval: Math.max(0, Math.ceil(times.length / 7) - 1), formatter: axisTime }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#8fa2b2' } } }
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, inverse: true, name: '降雨量 (mm)', nameLocation: 'middle', nameGap: 42, min: 0, axisLabel: { color: '#52697c', fontSize: 10 }, nameTextStyle: { color: '#344f63', fontSize: 11 }, axisLine: { show: true, lineStyle: { color: '#8fa2b2' } }, axisTick: { show: false }, splitLine: { lineStyle: { color: '#e7edf1' } } },
      { type: 'value', gridIndex: 1, name: '气温 (°C)', nameLocation: 'middle', nameGap: 42, scale: true, axisLabel: { color: '#52697c', fontSize: 10 }, nameTextStyle: { color: '#344f63', fontSize: 11 }, axisLine: { show: true, lineStyle: { color: '#8fa2b2' } }, axisTick: { show: false }, splitLine: { lineStyle: { color: '#edf1f4' } } },
      { type: 'value', gridIndex: 2, name: '流量 (m³/s)', nameLocation: 'middle', nameGap: 48, min: 0, axisLabel: { color: '#52697c', fontSize: 10 }, nameTextStyle: { color: '#344f63', fontSize: 11 }, axisLine: { show: true, lineStyle: { color: '#8fa2b2' } }, axisTick: { show: false }, splitLine: { lineStyle: { color: '#e3e9ee' } } }
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2], height: 13, bottom: 5, borderColor: '#c8d4dd', backgroundColor: '#f5f7f9', fillerColor: 'rgba(42,111,151,0.13)', handleStyle: { color: '#2a6f97' }, textStyle: { color: '#607587', fontSize: 9 }, showDetail: false }
    ],
    series: [
      { name: rainfallLabel.value, type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: props.forcing.precipitation || [], barMaxWidth: 8, itemStyle: { color: '#4C9BD3' }, emphasis: { itemStyle: { color: '#2377ae' } }, z: 2 },
      { name: '面平均气温', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: props.forcing.temperature || [], showSymbol: false, lineStyle: { width: 1.9, color: '#D55E00' }, markLine: { silent: true, symbol: 'none', lineStyle: { color: '#8798a5', type: 'dashed', width: 1 }, label: { formatter: '0 °C', color: '#657786', fontSize: 9 }, data: [{ yAxis: 0 }] }, z: 4 },
      { name: '实测流量', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: props.showObserved?observed:[], connectNulls: false, showSymbol: false, symbol: 'circle', symbolSize: 4, lineStyle: { width: 2.2, color: '#1B365D' }, itemStyle: { color: '#1B365D' }, emphasis: { focus: 'series' }, z: 8 },
      ...(!props.observedOnly && props.showCurrent ? [{ name: props.currentCurve.label, type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: simulated, connectNulls: false, showSymbol: false, lineStyle: { width: 2.3, type: 'dashed', color: props.currentCurve.color }, emphasis: { focus: 'series' }, z: 7 }] : []),
      ...componentSeries,
      ...trialSeries(2)
    ]
  }
})
</script>

<style scoped>
.hydrology-figure { display: grid; grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); width: 100%; height: 100%; min-height: 0; min-width: 0; }
.figure-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; min-height: 29px; padding: 3px 7px 5px; border-bottom: 1px solid #e2e9ee; color: #344f63; font-size: 12px; }
.figure-legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.figure-legend i { display: inline-block; flex: 0 0 auto; }
.figure-legend .bar { width: 12px; height: 8px; background: #4c9bd3; }
.figure-legend .line { width: 18px; height: 0; border-top: 2px solid; }
.figure-legend .temperature { border-color: #d55e00; }
.figure-legend .observed { border-color: #1b365d; }
.figure-legend .simulated { border-color: #d55e00; border-top-style: dashed; }
.figure-legend .component { border-top-width: 1.5px; }
.hydrology-chart { min-height: 0; min-width: 0; height: 100%; }
</style>
