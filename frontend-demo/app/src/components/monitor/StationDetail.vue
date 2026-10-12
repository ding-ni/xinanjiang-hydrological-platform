<template>
  <aside class="station-detail" v-if="station">
    <div class="panel panel-pad head">
      <h3 class="panel-title">
        <span>站点监视</span>
        <button class="btn" type="button" @click="closeDetail">×</button>
      </h3>
      <div class="name-row">
        <strong>{{ station.name }}</strong>
        <span class="tag">{{ stationTypeLabel }}</span>
      </div>
      <div class="detail-grid meta">
        <div><b>{{ station.lastTime || '—' }}</b>最近资料时间</div>
        <div><b>{{ station.quality || (observationReady ? '实测' : (isExternal ? '仅空间登记' : '正常')) }}</b>资料标识</div>
        <div><b>{{ station.basin || station.river || station.sourceProperties?.河流 || '—' }}</b>所属河流</div>
        <div><b>{{ station.code || station.id }}</b>站码</div>
        <div><b>{{ station.interval || '待确认' }}</b>采集间隔</div>
        <div><b>{{ sourceLabel }}</b>资料来源</div>
      </div>
      <p class="archive-hint">回放时刻：{{ monitor.currentTimeLabel }}</p>
    </div>

    <div class="panel panel-pad mid" v-if="station.type === 'rain' || (station.hasRainObservation && !station.hasRiverObservation)">
      <h3 class="panel-title">{{ isDatabase ? '近 24 小时实测雨量' : '雨量过程（至回放时刻）' }}</h3>
      <div v-if="!observationReady" class="external-empty">当前回放时段无该站实测记录，可到水文资料库查询其他时段。</div>
      <div v-else-if="rainHasValue" class="chart-box">
        <BaseChart :option="rainOption" />
      </div>
      <div v-else-if="rainDataComplete" class="zero-rain-state">
        <b>当前回放窗口无降雨</b>
        <span>{{ rainTimeRange }}</span>
        <small>资料完整，累计雨量 0.0 mm</small>
      </div>
      <div v-else class="external-empty">当前雨量资料存在缺测，不能将缺测解释为无雨。</div>
    </div>

    <div class="panel panel-pad mid" v-else-if="station.type === 'hydro' || station.type === 'section'">
      <h3 class="panel-title">{{ observationReady ? '水文过程' : '水文资料' }}</h3>
      <div v-if="!observationReady" class="stat-box">
        <div class="stat-line"><span>当前流量</span><b>—</b></div>
        <div class="stat-line"><span>当前水位</span><b>—</b></div>
        <div class="stat-line" v-if="station.registeredCatchmentAreaKm2"><span>源站点登记集水面积</span><b>{{ station.registeredCatchmentAreaKm2 }} km²</b></div>
        <div class="stat-line" v-if="station.projectBasinAreaKm2"><span>当前项目边界面积</span><b>{{ station.projectBasinAreaKm2 }} km²</b></div>
        <p class="area-note">站点集水面积与项目边界面积口径不同。</p>
      </div>
      <div v-else class="stat-box">
        <div class="stat-line"><span>当前流量</span><b class="hot">{{ station.flow ?? '—' }} m³/s</b></div>
        <div class="stat-line" v-if="station.level != null"><span>水位</span><b>{{ station.level }} m</b></div>
        <div class="stat-line" v-if="station.delta3h != null"><span>近 3 小时变化</span><b class="warn">+{{ station.delta3h }} m³/s</b></div>
        <div class="stat-line" v-if="station.controlArea"><span>控制面积</span><b>{{ station.controlArea }} km²</b></div>
      </div>
      <div v-if="observationReady && hydroSeries.length" class="chart-box">
        <BaseChart :option="flowOption" />
      </div>
      <div v-if="observationReady && levelSeries.some(row => row.level != null)" class="chart-box">
        <BaseChart :option="levelOption" />
      </div>
    </div>

    <div class="panel panel-pad mid" v-else-if="station.type === 'water'">
      <h3 class="panel-title">水位实况</h3>
      <div v-if="isExternal" class="external-empty">水位站空间登记已接入，逐时水位与警戒信息待业务数据转换后显示。</div>
      <div v-else class="stat-box">
        <div class="stat-line"><span>当前水位</span><b class="hot">{{ station.level }} m</b></div>
        <div class="stat-line"><span>警戒水位</span><b class="warn">{{ station.levelWarn }} m</b></div>
        <div class="stat-line"><span>距警戒</span><b>{{ (station.levelWarn - station.level).toFixed(2) }} m</b></div>
      </div>
    </div>

    <div class="panel panel-pad mid" v-else>
      <h3 class="panel-title">站点资料</h3>
      <div class="external-empty">暂无逐时过程。</div>
    </div>

    <div v-if="!isExternal" class="panel panel-pad actions">
      <h3 class="panel-title">作业动作</h3>
      <div v-if="!isExternal" class="btn-row">
        <button class="btn" type="button" @click="monitor.flyToStation(station.id)">定位</button>
        <button
          v-if="station.type === 'rain'"
          class="btn"
          type="button"
          @click="monitor.toggleExcludeRainStation(station.id)"
        >
          {{ monitor.isRainExcluded(station.id) ? '恢复网格插值' : '排除网格插值' }}
        </button>
        <button
          v-if="station.type === 'hydro' || station.type === 'section'"
          class="btn primary"
          type="button"
          @click="goForecast"
        >
          进入该断面预报
        </button>
      </div>
      <p v-else class="external-action-note">{{ observationReady ? '实测资料' : '暂无可用过程' }}</p>
      <p v-if="station.type === 'rain' && monitor.isRainExcluded(station.id)" class="exclude-note">
        已排除：不参与当前雨量网格化，原始资料仍保留
      </p>
    </div>
    <div v-else-if="canOpenForecast" class="detail-footer">
      <button class="btn primary block" type="button" @click="goForecast">进入徐畈断面预报</button>
    </div>
    <RouterLink v-if="isDatabase" class="btn" :to="{ path: '/data', query: { station: station.code || station.id, variable: station.type === 'rain' ? 'rain' : 'flow', end: monitor.currentTimeLabel.replace(' ', 'T') } }">查询该站完整过程</RouterLink>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useMonitorStore } from '@/stores/monitor'
import { useForecastStore } from '@/stores/forecast'
import BaseChart from '@/components/charts/BaseChart.vue'

const props = defineProps({
  station: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const monitor = useMonitorStore()
const forecast = useForecastStore()
const router = useRouter()

const station = computed(() => props.station || monitor.selectedStation)
const isExternal = computed(() => Boolean(props.station))
const observationReady = computed(() => Boolean(station.value?.observationReady))
const isModelQC = computed(() => station.value?.sourceType === 'model-qc')
const isDatabase = computed(() => station.value?.sourceType === 'database-observed')
const stationTypeLabel = computed(() => {
  const label = station.value?.typeLabel || '站点'
  return station.value?.hasRainObservation && station.value?.type !== 'rain'
    ? `${label} · 兼有雨量资料`
    : label
})
const canOpenForecast = computed(() => station.value?.type === 'hydro' || station.value?.type === 'section')
const sourceLabel = computed(() => station.value?.sourceLabel || (observationReady.value
  ? '历史实测记录'
  : (station.value?.sourceId === 'verified-spatial-source' ? '已核实空间资料' : station.value?.source || '待确认')))

const rainSeries = computed(() => isDatabase.value ? station.value.rainSeries || [] : observationReady.value
    ? monitor.historicalRainSeries(station.value?.code || station.value?.id)
    : monitor.selectedRainSeries)
const rainHasValue = computed(() => rainSeries.value.some((item) => Number(item.rain) > 0))
const rainDataComplete = computed(() => rainSeries.value.length > 0 && rainSeries.value.every((item) => item.rain != null))
const hydroSeries = computed(() => isDatabase.value ? station.value.flowSeries || [] : observationReady.value && station.value?.hasRiverObservation ? monitor.historicalFlowSeries : [])
const levelSeries = computed(() => isDatabase.value ? station.value.levelSeries || [] : [])
const rainTimeRange = computed(() => {
  if (!rainSeries.value.length) return '当前时段无可用记录'
  return `${rainSeries.value[0].label} 至 ${rainSeries.value[rainSeries.value.length - 1].label}`
})

const rainOption = computed(() => {
  const series = rainSeries.value
  return {
    grid: { left: 36, right: 40, top: 36, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      appendToBody: true,
      backgroundColor: 'rgba(255,255,255,0.98)',
      borderColor: '#d4e0ef',
      textStyle: { fontSize: 12, color: '#203657' },
      axisPointer: { type: 'line' }
    },
    legend: { top: 0, right: 0, textStyle: { fontSize: 11 } },
    xAxis: {
      type: 'category',
      data: series.map((d) => d.label),
      axisLabel: { fontSize: 10, color: '#6a7c95', interval: Math.ceil(series.length / 6) }
    },
    yAxis: [
      {
        type: 'value',
        name: 'mm',
        nameTextStyle: { fontSize: 10 },
        axisLabel: { fontSize: 10 },
        splitLine: { lineStyle: { color: '#e8f0fa' } }
      },
      {
        type: 'value',
        name: '累计',
        nameTextStyle: { fontSize: 10 },
        axisLabel: { fontSize: 10 },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: '时段雨量',
        type: 'bar',
        data: series.map((d) => d.rain),
        itemStyle: { color: '#3b7ddd', borderRadius: [2, 2, 0, 0] },
        barMaxWidth: 12
      },
      {
        name: '累计雨量',
        type: 'line',
        yAxisIndex: 1,
        data: series.map((d) => d.cum),
        smooth: false,
        connectNulls: false,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: '#14833e', width: 2 },
        itemStyle: { color: '#14833e' }
      }
    ]
  }
})

const flowOption = computed(() => ({
  grid: { left: 48, right: 18, top: 30, bottom: 32 },
  tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => value == null ? '缺测' : `${value} m³/s` },
  xAxis: { type: 'category', data: hydroSeries.value.map((row) => row.label), axisLabel: { fontSize: 10, interval: Math.ceil(hydroSeries.value.length / 5) } },
  yAxis: { type: 'value', name: 'm³/s', axisLabel: { fontSize: 10 } },
  series: [{ name: isModelQC.value ? '整编流量' : '实测流量', type: 'line', data: hydroSeries.value.map((row) => row.flow), smooth: false, connectNulls: false, showSymbol: false, lineStyle: { color: '#3b7ddd', width: 2 } }]
}))
const levelOption = computed(() => ({
  grid: { left: 48, right: 18, top: 30, bottom: 32 }, tooltip: { trigger: 'axis', confine: true },
  xAxis: { type: 'category', data: levelSeries.value.map(row => row.label), axisLabel: { fontSize: 10, hideOverlap: true } },
  yAxis: { type: 'value', name: 'm', scale: true },
  series: [{ name: '实测水位', type: 'line', data: levelSeries.value.map(row => row.level), smooth: false, connectNulls: false, showSymbol: false, lineStyle: { color: '#177c72', width: 2 } }]
}))

function goForecast() {
  forecast.prepareFromStation(station.value, monitor.currentTimeLabel)
  forecast.syncFromMonitor(monitor.qualityPayload)
  router.push('/forecast')
}

function closeDetail() {
  if (isExternal.value) emit('close')
  else monitor.closeDetail()
}
</script>

<style scoped>
.station-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
}

.head {
  flex: 0 0 auto;
}

.mid {
  flex: 0 0 auto;
  min-height: 220px;
  display: flex;
  flex-direction: column;
}

.actions {
  flex: 0 0 auto;
  border-top: 1px solid #e8f0fa;
  background: #fbfdff;
}

.name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.name-row strong {
  color: #122c55;
  font-size: 16px;
}

.meta {
  margin-top: 2px;
}

.chart-box {
  height: 230px;
  min-height: 230px;
}
.zero-rain-state {
  display: grid;
  min-height: 190px;
  place-content: center;
  gap: 8px;
  padding: 24px;
  border: 1px solid #d7e3ea;
  background: #f7fafb;
  color: #607486;
  text-align: center;
}
.zero-rain-state b { color: #244454; font-size: 15px; }
.zero-rain-state span,
.zero-rain-state small { font-size: 11px; }
.detail-footer {
  flex: 0 0 auto;
  padding: 0 2px 2px;
}
.external-empty {
  display: grid;
  min-height: 160px;
  place-items: center;
  padding: 24px;
  border: 1px dashed #c9d7e2;
  color: #6a7c95;
  font-size: 12px;
  line-height: 1.8;
  text-align: center;
}

.archive-hint {
  margin: 10px 0 0;
  color: #7a8ba3;
  font-size: 11px;
}

.exclude-note {
  margin: 8px 0 0;
  color: #c45a10;
  font-size: 11px;
  font-weight: 700;
}

.area-note,
.external-action-note {
  margin: 8px 0 0;
  color: #6a7c95;
  font-size: 11px;
  line-height: 1.55;
}

.btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.btn-row .btn {
  min-height: 34px;
}
</style>
