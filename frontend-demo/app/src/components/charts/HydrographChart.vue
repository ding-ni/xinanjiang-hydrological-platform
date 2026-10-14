<template>
  <div class="hydrograph-figure" :style="{ height }">
    <div v-if="hasData" class="figure-legend" aria-label="水文过程图图例">
      <span v-if="displayMode !== 'flow' && hasRainfall"><i class="bar" />逐小时降雨</span>
      <span v-if="hydrograph.obs?.some(row => row.value != null)"><i class="line observed" />实测流量</span>
      <span><i class="line forecast" />{{ modelName }}</span>
      <span v-if="showPrev && hydrograph.prev?.length && mode !== 'archive'"><i class="line previous" />上一份预报</span>
      <span v-if="overlay?.forecast?.length"><i class="line overlay" />{{ overlayLabel }}</span>
    </div>
    <BaseChart class="hydrograph-chart" :option="option" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import BaseChart from '@/components/charts/BaseChart.vue'

const props = defineProps({
  hydrograph: { type: Object, required: true },
  rainfall: { type: Array, default: null },
  showPrev: { type: Boolean, default: true },
  mode: { type: String, default: 'forecast' },
  overlay: { type: Object, default: null },
  overlayLabel: { type: String, default: '对比档案' },
  showThresholds: { type: Boolean, default: false },
  warnFlow: { type: Number, default: null },
  ensureFlow: { type: Number, default: null },
  height: { type: String, default: '100%' },
  displayMode: { type: String, default: 'combined' }
})

const hasData = computed(() => Boolean(props.hydrograph?.forecast?.length))
const modelName = computed(() => ['calibration','simulation'].includes(props.mode) ? '模拟流量' : props.mode==='hindcast' ? '回算流量' : '预报流量')

function finiteValue(value) {
  return value != null && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null
}

const suppliedRainfall = computed(() => props.rainfall ?? props.hydrograph?.forcing?.precipitation ?? [])
const hasRainfall = computed(() => suppliedRainfall.value.some((value) => finiteValue(value) != null))

function axisTime(value) {
  const text = String(value)
  if (/^\d{4}-\d{2}-\d{2}[T ]/.test(text)) return `${text.slice(5, 10)}\n${text.slice(11, 16)}`
  return `${text.slice(0, 5)}\n${text.slice(6)}`
}

const option = computed(() => {
  const h = props.hydrograph
  if (!h?.forecast?.length) return { title: { text: '暂无过程数据', left: 'center', top: 'center', textStyle: { color: '#718596', fontSize: 13, fontWeight: 500 } } }

  const times = h.forecast.map((item) => item.time)
  const isCal = props.mode === 'calibration'
  const isSimulation = props.mode === 'simulation'
  const isHindcast = props.mode === 'hindcast'
  const isArchive = props.mode === 'archive'
  const flowOnly = props.displayMode === 'flow'
  const flowAxis = flowOnly ? 0 : 1
  const startMark = h.startMark || times[0]
  const validForecast = h.forecast.filter((item) => finiteValue(item.value) != null)
  const peakPoint = validForecast.reduce((best, item) => !best || Number(item.value) > Number(best.value) ? item : best, null)
  const peakX = h.peakTime || peakPoint?.time
  const peakVal = finiteValue(h.peak) ?? finiteValue(peakPoint?.value)
  const observedMap = new Map((h.obs || []).map((item) => [item.time, finiteValue(item.value)]))
  // Only supplied inputs are plotted. Missing/short rainfall stays null; it is
  // never reconstructed from the flow peak or replaced by a demonstration.
  const rainfall = times.map((_, index) => finiteValue(suppliedRainfall.value[index]))
  const markLineData = []

  if (!isArchive && times.includes(startMark)) {
    markLineData.push({
      xAxis: startMark,
      lineStyle: { type: 'solid', color: '#16877D', width: 1.4 },
      label: { formatter: isCal ? '率定起点' : isSimulation ? '模拟起点' : '起报时刻', color: '#126a64', fontWeight: 700, fontSize: 10, position: 'insideEndTop' }
    })
  }
  if (props.showThresholds && props.warnFlow != null) {
    markLineData.push({ yAxis: props.warnFlow, lineStyle: { type: 'dashed', color: '#E69F00', width: 1.1 }, label: { formatter: `警戒 ${props.warnFlow}`, color: '#9a6800', fontSize: 9, position: 'insideEndTop' } })
  }
  if (props.showThresholds && props.ensureFlow != null) {
    markLineData.push({ yAxis: props.ensureFlow, lineStyle: { type: 'dashed', color: '#C23B22', width: 1.1 }, label: { formatter: `保证 ${props.ensureFlow}`, color: '#9f2e1b', fontSize: 9, position: 'insideEndTop' } })
  }

  const series = []
  if (!flowOnly) series.push({ name: '逐小时降雨', type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: rainfall, barMaxWidth: 9, itemStyle: { color: '#4C9BD3' }, emphasis: { itemStyle: { color: '#2377AE' } }, z: 2 })
  series.push(
    {
      name: '实测流量', type: 'line', xAxisIndex: flowAxis, yAxisIndex: flowAxis,
      data: times.map((time) => observedMap.get(time) ?? null), connectNulls: false,
      showSymbol: false, symbol: 'circle', symbolSize: 4,
      lineStyle: { width: 2.2, color: '#1B365D' }, itemStyle: { color: '#1B365D' }, emphasis: { focus: 'series' }, z: 8
    },
    {
      name: modelName.value, type: 'line', xAxisIndex: flowAxis, yAxisIndex: flowAxis,
      data: h.forecast.map((item) => finiteValue(item.value)), connectNulls: false, showSymbol: false,
      lineStyle: { width: 2.5, type: isCal ? 'dashed' : 'solid', color: '#D55E00' }, emphasis: { focus: 'series' }, z: 7,
      markPoint: {
        symbol: 'circle', symbolSize: 13,
        data: peakX && peakVal != null ? [{ name: isCal || isSimulation ? '模拟峰' : isHindcast ? '回算洪峰' : '预报洪峰', coord: [peakX, peakVal], value: Number(peakVal.toFixed(2)) }] : [],
        itemStyle: { color: '#D55E00', borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: ({ value }) => `${value} m³/s`, position: 'top', distance: 8, color: '#9d3d00', fontWeight: 700, fontSize: 11 }
      },
      markLine: { symbol: 'none', silent: true, data: markLineData },
      markArea: !isCal && !isSimulation && !isArchive && times.includes(startMark) ? { silent: true, data: [[{ xAxis: times[0], itemStyle: { color: 'rgba(22,135,125,0.035)' } }, { xAxis: startMark }], [{ xAxis: startMark, itemStyle: { color: 'rgba(213,94,0,0.025)' } }, { xAxis: times[times.length - 1] }]] } : undefined
    }
  )

  if (props.showPrev && h.prev?.length && !isArchive) {
    const previousMap = new Map(h.prev.map((item) => [item.time, finiteValue(item.value)]))
    series.push({ name: '上一份预报', type: 'line', xAxisIndex: flowAxis, yAxisIndex: flowAxis, data: times.map((time) => previousMap.get(time) ?? null), connectNulls: false, showSymbol: false, lineStyle: { width: 1.5, type: 'dashed', color: '#8B98A3' }, z: 4 })
  }
  if (props.overlay?.forecast?.length) {
    const overlayMap = new Map(props.overlay.forecast.map((item) => [item.time, finiteValue(item.value)]))
    series.push({ name: props.overlayLabel || '对比档案', type: 'line', xAxisIndex: flowAxis, yAxisIndex: flowAxis, data: times.map((time) => overlayMap.get(time) ?? null), connectNulls: false, showSymbol: false, lineStyle: { width: 1.8, type: 'dotted', color: '#7B61A8' }, z: 4 })
  }

  return {
    animation: false,
    textStyle: { fontFamily: 'Microsoft YaHei UI, Microsoft YaHei, sans-serif' },
    tooltip: {
      trigger: 'axis', appendToBody: true, confine: false, enterable: false, backgroundColor: 'rgba(255,255,255,0.98)', borderColor: '#bfcdd8', borderWidth: 1, textStyle: { color: '#1e3448', fontSize: 11 }, extraCssText: 'z-index:3000;max-width:320px;box-shadow:0 8px 24px rgba(20,45,55,.16);',
      formatter(params) {
        const rows = params.filter((item) => item.value != null && item.value !== '-').map((item) => `${item.marker}${item.seriesName}<b style="float:right;margin-left:18px">${Number(item.value).toFixed(1)} ${item.seriesName.includes('降雨') ? 'mm' : 'm³/s'}</b>`)
        return `<b>${params[0]?.axisValue || ''}</b><br>${rows.join('<br>')}`
      }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: '#557184', width: 1 } },
    grid: flowOnly
      ? [{ left: 64, right: 30, top: 10, bottom: 48 }]
      : [{ left: 64, right: 30, top: 10, height: '20%' }, { left: 64, right: 30, top: '34%', bottom: 48 }],
    xAxis: flowOnly
      ? [{ type: 'category', gridIndex: 0, boundaryGap: false, data: times, axisLabel: { color: '#52697c', fontSize: 10, interval: Math.max(0, Math.ceil(times.length / 7) - 1), formatter: axisTime }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#8fa2b2' } } }]
      : [{ type: 'category', gridIndex: 0, boundaryGap: true, data: times, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#aebdca' } } }, { type: 'category', gridIndex: 1, boundaryGap: true, data: times, axisLabel: { color: '#52697c', fontSize: 10, hideOverlap: true, interval: Math.max(0, Math.ceil(times.length / 7) - 1), formatter: axisTime }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#8fa2b2' } } }],
    yAxis: flowOnly
      ? [{ type: 'value', gridIndex: 0, name: '流量 (m³/s)', nameLocation: 'middle', nameGap: 48, min: 0, axisLabel: { color: '#52697c', fontSize: 10 }, nameTextStyle: { color: '#344f63', fontSize: 11 }, axisLine: { show: true, lineStyle: { color: '#8fa2b2' } }, axisTick: { show: false }, splitLine: { lineStyle: { color: '#e3e9ee' } } }]
      : [{ type: 'value', gridIndex: 0, inverse: true, name: '降雨量 (mm)', nameLocation: 'middle', nameGap: 42, min: 0, axisLabel: { color: '#52697c', fontSize: 10 }, nameTextStyle: { color: '#344f63', fontSize: 11 }, axisLine: { show: true, lineStyle: { color: '#8fa2b2' } }, axisTick: { show: false }, splitLine: { lineStyle: { color: '#e7edf1' } } }, { type: 'value', gridIndex: 1, name: '流量 (m³/s)', nameLocation: 'middle', nameGap: 48, min: 0, axisLabel: { color: '#52697c', fontSize: 10 }, nameTextStyle: { color: '#344f63', fontSize: 11 }, axisLine: { show: true, lineStyle: { color: '#8fa2b2' } }, axisTick: { show: false }, splitLine: { lineStyle: { color: '#e3e9ee' } } }],
    dataZoom: [
      { type: 'inside', xAxisIndex: flowOnly ? [0] : [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: flowOnly ? [0] : [0, 1], height: 13, bottom: 5, borderColor: '#c8d4dd', backgroundColor: '#f5f7f9', fillerColor: 'rgba(42,111,151,0.13)', handleStyle: { color: '#2a6f97' }, showDetail: false }
    ],
    series
  }
})
</script>

<style scoped>
.hydrograph-figure { display: grid; grid-template-rows: auto minmax(0, 1fr); width: 100%; min-height: 0; }
.figure-legend { display: flex; flex-wrap: nowrap; align-items: center; justify-content: flex-end; gap: 6px 14px; min-height: 29px; padding: 3px 7px 5px; border-bottom: 1px solid #e2e9ee; color: #344f63; font-size: 10px; overflow-x: auto; }
.figure-legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.figure-legend i { display: inline-block; flex: 0 0 auto; }
.figure-legend .bar { width: 12px; height: 8px; background: #4c9bd3; }
.figure-legend .line { width: 18px; height: 0; border-top: 2px solid; }
.figure-legend .observed { border-color: #1b365d; }
.figure-legend .forecast { border-color: #d55e00; }
.figure-legend .previous { border-color: #8b98a3; border-top-style: dashed; }
.figure-legend .overlay { border-color: #7b61a8; border-top-style: dotted; }
.hydrograph-chart { min-height: 0; height: 100%; }
</style>
