<template>
  <aside class="selinco-detail panel panel-pad">
    <div class="detail-head">
      <div><span class="eyebrow">{{ station.typeLabel }}</span><h3>{{ station.name }}</h3></div>
      <button class="icon-close" type="button" aria-label="关闭站点详情" title="关闭" @click="$emit('close')"><Close /></button>
    </div>
    <div class="detail-meta">
      <span>{{ station.code }}</span>
      <span>{{ station.lng.toFixed(4) }}°E，{{ station.lat.toFixed(4) }}°N</span>
    </div>

    <div v-if="station.type === 'rain'" class="detail-grid">
      <div><span>最新时刻</span><b>{{ station.lastTime }}</b></div>
      <div><span>近 1 小时雨量</span><b>{{ station.rain1h }} mm</b></div>
      <div><span>近 3 小时雨量</span><b>{{ station.rain3h }} mm</b></div>
      <div><span>本场累计雨量</span><b>{{ station.rainTotal }} mm</b></div>
      <div><span>站点高程</span><b>{{ station.elev }} m</b></div>
      <div><span>资料状态</span><b class="ok">{{ station.quality }}</b></div>
    </div>
    <div v-else-if="station.type === 'water'" class="detail-grid">
      <div><span>最新时刻</span><b>{{ station.lastTime }}</b></div>
      <div><span>湖面水位</span><b>{{ station.level }} m</b></div>
      <div><span>关注水位</span><b>{{ station.levelWarn }} m</b></div>
      <div><span>测站高程</span><b>{{ station.elev }} m</b></div>
      <div><span>资料来源</span><b>{{ station.source }}</b></div>
      <div><span>报送间隔</span><b>{{ station.interval }}</b></div>
    </div>
    <div v-else class="detail-grid">
      <div><span>最新时刻</span><b>{{ station.lastTime }}</b></div>
      <div><span>实测流量</span><b>{{ station.flow }} m³/s</b></div>
      <div><span>预报洪峰</span><b class="hot">{{ station.peakForecast }} m³/s</b></div>
      <div><span>预计峰现</span><b>{{ station.peakTime }}</b></div>
      <div><span>控制流域</span><b>{{ station.basin }}</b></div>
      <div><span>资料状态</span><b class="ok">{{ station.quality }}</b></div>
    </div>

    <section class="process-block">
      <div class="process-head"><b>{{ chartTitle }}</b><span>逐小时</span></div>
      <BaseChart class="station-chart" :option="chartOption" />
    </section>

    <div class="detail-source">
      <span>资料时间精度</span>
      <b>{{ station.source || '自动接入' }} · {{ station.interval || '1 h' }}</b>
    </div>
    <button v-if="station.type === 'section'" class="btn primary block" type="button" @click="$emit('forecast', station)">以此断面进入预报</button>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { Close } from '@element-plus/icons-vue'
import BaseChart from '@/components/charts/BaseChart.vue'

const props = defineProps({ station: { type: Object, required: true } })
defineEmits(['close', 'forecast'])

const hours = ['03:00', '04:00', '05:00', '06:00', '07:00', '08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00']
const chartTitle = computed(() => props.station.type === 'rain' ? '小时雨量过程' : props.station.type === 'water' ? '湖面水位过程' : '实测与预报流量过程')

function rainfallSeries() {
  const last = Number(props.station.rain1h || 0)
  const previousTwo = Math.max(0, Number(props.station.rain3h || 0) - last)
  const recent = [+(previousTwo * 0.42).toFixed(1), +(previousTwo * 0.58).toFixed(1), last]
  const earlierTotal = Math.max(0, Number(props.station.rainTotal || 0) - recent.reduce((sum, value) => sum + value, 0))
  const weights = [0.02, 0.04, 0.08, 0.13, 0.18, 0.22, 0.17, 0.1, 0.06]
  const hourly = [...weights.map((weight) => +(earlierTotal * weight).toFixed(1)), ...recent]
  let sum = 0
  return { hourly, cumulative: hourly.map((value) => +(sum += value).toFixed(1)) }
}

const chartOption = computed(() => {
  const common = {
    animation: false,
    grid: { left: 48, right: 38, top: 38, bottom: 34 },
    tooltip: { trigger: 'axis', confine: true },
    legend: { top: 3, right: 2, itemWidth: 16, itemHeight: 7, textStyle: { color: '#51677f', fontSize: 10 } },
    xAxis: { type: 'category', data: hours, boundaryGap: props.station.type === 'rain', axisLabel: { color: '#71839a', fontSize: 9, interval: 2 }, axisLine: { lineStyle: { color: '#c8d5e2' } } },
    dataZoom: [{ type: 'inside', zoomOnMouseWheel: true, moveOnMouseMove: true }]
  }
  if (props.station.type === 'rain') {
    const data = rainfallSeries()
    return { ...common,
      yAxis: [
        { type: 'value', name: 'mm', min: 0, nameTextStyle: { fontSize: 9 }, axisLabel: { color: '#71839a', fontSize: 9 }, splitLine: { lineStyle: { color: '#e8eef4' } } },
        { type: 'value', name: '累计', min: 0, nameTextStyle: { fontSize: 9 }, axisLabel: { color: '#71839a', fontSize: 9 }, splitLine: { show: false } }
      ],
      series: [
        { name: '小时雨量', type: 'bar', data: data.hourly, barMaxWidth: 15, itemStyle: { color: '#3d96bd' } },
        { name: '累计雨量', type: 'line', yAxisIndex: 1, data: data.cumulative, symbol: 'none', lineStyle: { color: '#d66c24', width: 2 } }
      ] }
  }
  if (props.station.type === 'water') {
    const end = Number(props.station.level || 4530.24)
    const values = hours.map((_, index) => +(end - 0.09 + index * 0.008 + Math.sin(index / 2) * 0.006).toFixed(3))
    return { ...common,
      yAxis: { type: 'value', name: 'm', scale: true, axisLabel: { color: '#71839a', fontSize: 9, formatter: (value) => value.toFixed(2) }, splitLine: { lineStyle: { color: '#e8eef4' } } },
      series: [{ name: '实测水位', type: 'line', data: values, symbol: 'circle', symbolSize: 4, lineStyle: { color: '#167f83', width: 2 }, itemStyle: { color: '#167f83' } }] }
  }
  const current = Number(props.station.flow || 0)
  const peak = Number(props.station.peakForecast || current * 1.35)
  const observed = hours.map((_, index) => index < 8 ? Math.round(current * (0.76 + index * 0.034 + Math.sin(index / 2) * 0.025)) : null)
  observed[7] = current
  const forecast = hours.map((_, index) => index < 7 ? null : Math.round(index === 7 ? current : current + (peak - current) * Math.sin(((index - 7) / 8) * Math.PI)))
  return { ...common,
    xAxis: { ...common.xAxis, boundaryGap: false },
    yAxis: { type: 'value', name: 'm³/s', min: 0, axisLabel: { color: '#71839a', fontSize: 9 }, splitLine: { lineStyle: { color: '#e8eef4' } } },
    series: [
      { name: '实测流量', type: 'line', data: observed, symbol: 'none', lineStyle: { color: '#167f83', width: 2.2 } },
      { name: '预报流量', type: 'line', data: forecast, symbol: 'none', lineStyle: { color: '#d66c24', width: 2.2, type: 'dashed' } }
    ] }
})
</script>

<style scoped>
.selinco-detail { min-width: 0; overflow: auto; }
.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.eyebrow { color: #647991; font-size: 11px; font-weight: 700; }
h3 { margin: 3px 0 0; color: #173052; font-size: 17px; }
.icon-close { display: grid; place-items: center; width: 32px; height: 32px; border: 1px solid #cbd9e6; border-radius: 6px; background: #fff; color: #4e6781; cursor: pointer; }
.icon-close svg { width: 17px; height: 17px; }
.icon-close:hover { border-color: #7fa5bf; background: #f2f8fb; }
.icon-close:focus-visible { outline: 2px solid #2388aa; outline-offset: 2px; }
.detail-meta { display: grid; gap: 3px; margin: 12px 0; color: #6f859d; font-size: 11px; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; border: 1px solid #d9e5f0; background: #d9e5f0; }
.detail-grid div { display: grid; gap: 4px; min-width: 0; padding: 9px; background: #fff; }
.detail-grid span { color: #6c8097; font-size: 11px; }
.detail-grid b { overflow-wrap: anywhere; color: #183457; font-size: 12px; font-variant-numeric: tabular-nums; }
.detail-grid b.ok { color: #168044; }
.detail-grid b.hot { color: #c95716; }
.process-block { margin-top: 12px; border: 1px solid #d9e5f0; background: #fbfdff; }
.process-head { display: flex; align-items: center; justify-content: space-between; min-height: 34px; padding: 0 10px; border-bottom: 1px solid #e1eaf2; }
.process-head b { color: #294562; font-size: 12px; }
.process-head span { color: #71859b; font-size: 10px; }
.station-chart { height: 220px; min-height: 220px; }
.detail-source { display: grid; gap: 4px; margin: 12px 0; padding: 10px; border-left: 3px solid #2f8fb4; background: #f2f8fb; color: #60768f; font-size: 11px; }
.detail-source b { color: #294562; }
</style>
