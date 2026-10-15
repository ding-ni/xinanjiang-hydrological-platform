<template>
  <section class="data-workspace">
    <header class="data-heading">
      <div><span class="eyebrow">{{ app.basinName }}</span><h1>水文资料库</h1></div>
      <div class="heading-actions">
        <span v-if="summary" class="database-state"><i /> 本地资料库</span>
        <button class="btn icon-action" title="刷新资料库" aria-label="刷新资料库" :disabled="busy" @click="load"><Refresh /></button>
        <button class="btn" :disabled="busy || !summary" @click="backup"><FolderChecked />备份资料库</button>
      </div>
    </header>
    <p v-if="!available" class="notice">请选择南安溪-徐畈，并使用统一平台入口。</p>
    <template v-else>
      <div v-if="error" role="alert" class="error-message">{{ error }}</div>
      <div v-if="message" role="status" class="success-message">{{ message }}</div>
      <div class="data-totals" v-if="summary && tab !== 'processing'" aria-label="实测资料库总量">
        <div v-for="item in summary.coverage" :key="item.variable"><span>{{ item.label }}记录</span><b>{{ number(item.records) }}<small>条</small></b><p>{{ item.stations }} 站 · {{ item.start?.slice(0, 10) }} 至 {{ item.end?.slice(0, 10) }}</p></div>
        <div><span>历史洪水目录</span><b>{{ summary.events }}<small>场</small></b><p>{{ summary.sourceFiles }} 份年资料 · {{ summary.stations }} 个登记站点</p></div>
      </div>
      <nav class="data-tabs" aria-label="资料分类">
        <button v-for="item in tabs" :key="item.id" :class="{ active: tab === item.id }" @click="tab = item.id">{{ item.label }}</button>
      </nav>

      <div v-if="tab === 'observations'" class="observation-workspace">
        <aside class="station-browser">
          <div class="segmented" aria-label="水文要素"><button v-for="(v, key) in variables" :key="key" :class="{ active: variable === key }" @click="changeVariable(key)">{{ v.label }}</button></div>
          <label class="search-box"><Search /><input v-model="stationSearch" aria-label="查找测站" placeholder="站名或站码" /></label>
          <div class="station-list">
            <button v-for="station in filteredStations" :key="station.id" :class="{ selected: stationId === station.id }" @click="selectStation(station.id)">
              <span><b>{{ station.name }}</b><small>{{ station.id }}</small></span><span>{{ number(station.valid) }}<small>有效记录</small></span>
            </button>
          </div>
        </aside>
        <main class="observation-main">
          <form class="query-toolbar" @submit.prevent="queryWindow">
            <span class="query-note">使用水文资料库 · 跨年查询无需选择原文件</span>
            <label>起始时间<input class="input" type="datetime-local" v-model="start" step="3600" required aria-label="资料起始时间" /></label>
            <label>结束时间<input class="input" type="datetime-local" v-model="end" step="3600" required aria-label="资料结束时间" /></label>
            <button class="btn primary" :disabled="busy || !stationId"><Search />查询</button>
            <button class="btn" type="button" title="导出完整查询时段的逐小时记录" aria-label="导出全部逐时资料" :disabled="busy || !result || queryChanged" @click="exportSeries"><Download />导出全部逐时资料</button>
          </form>
          <div v-if="result && !queryChanged" class="series-content">
            <div class="series-title"><h2>{{ result.station.name }} · {{ variables[result.variable].label }}过程</h2><span>{{ result.unit }}</span></div>
            <p class="query-range">完整查询时段：{{ timeLabel(result.range?.start || start) }} → {{ timeLabel(result.range?.end || end) }}</p>
            <div class="series-summary"><span>有效 <b>{{ result.valid }}</b> / {{ result.expected }} 小时</span><span>缺测 <b :class="{ caution: result.missing }">{{ result.missing }}</b> 小时</span><span>最大值 <b>{{ value(result.maximum) }}</b> {{ result.unit }}</span><span v-if="result.variable === 'rain'">有效记录累计 <b>{{ value(result.total) }}</b> mm</span></div>
            <div v-if="result.display" class="display-toolbar" aria-label="逐时显示时段">
              <span>显示时段：{{ timeLabel(result.display.start) }} → {{ timeLabel(result.display.end) }}</span>
              <div v-if="result.expected > result.display.limitHours" class="display-actions">
                <button class="btn" :disabled="busy || !result.display.hasPrevious" @click="showDisplayPeriod(-1)"><ArrowLeft />上一时段</button>
                <span>{{ Math.floor(result.display.offsetHours / result.display.limitHours) + 1 }} / {{ Math.ceil(result.expected / result.display.limitHours) }} 段</span>
                <button class="btn" :disabled="busy || !result.display.hasNext" @click="showDisplayPeriod(1)">下一时段<ArrowRight /></button>
              </div>
            </div>
            <div class="hydrograph"><BaseChart :option="chartOption" /></div>
            <details class="source-details"><summary>资料来源 · {{ result.sources.length }} 份原始年资料</summary><p>时间按源资料本地整点。空白表示缺测，0 为原始记录中的零值；查询和导出保留原始值。</p><code>{{ result.sources.join(' · ') }}</code></details>
            <div class="table-heading"><h3>逐时记录</h3><div><button class="btn icon-action" aria-label="上一页记录" :disabled="page === 0" @click="page--"><ArrowLeft /></button><span>{{ page + 1 }} / {{ pageCount }}</span><button class="btn icon-action" aria-label="下一页记录" :disabled="page + 1 >= pageCount" @click="page++"><ArrowRight /></button></div></div>
            <div class="records-scroll"><table class="table"><thead><tr><th>时间</th><th>{{ variables[result.variable].label }}（{{ result.unit }}）</th><th>记录状态</th><th>来源</th></tr></thead><tbody><tr v-for="row in pagedSeries" :key="row.time"><td>{{ row.time.replace('T', ' ') }}</td><td class="numeric">{{ value(row.value) }}</td><td>{{ row.value !== null ? '原始记录' : row.recorded ? '原表空值' : '缺测' }}</td><td>{{ row.sourceId || '—' }}</td></tr></tbody></table></div>
          </div>
          <p v-else class="empty-state">{{ busy ? '正在查询资料…' : '选择测站和时段后查询。' }}</p>
        </main>
      </div>

      <section v-else-if="tab === 'events'" class="event-section">
        <div class="section-toolbar"><h2>历史洪水目录 <small>{{ filteredEvents.length }} 场</small></h2><label class="search-box"><Search /><input v-model="eventSearch" placeholder="年份或场次编号" aria-label="检索洪水场次" /></label></div>
        <p class="section-intro">公共目录登记226场洪水，直接查询历史库实测过程。方案独有时段与不同边界在各方案内保留，选用场次及用途在项目方案中设置。</p>
        <div class="records-scroll"><table class="table"><thead><tr><th>场次编号</th><th>开始</th><th>结束</th><th>历时</th><th>洪峰流量（m³/s）</th><th>操作</th></tr></thead><tbody><tr v-for="event in filteredEvents" :key="event.eventId"><td><b>{{ event.floodCode || event.eventId }}</b></td><td>{{ event.start?.replace('T',' ') }}</td><td>{{ event.end?.replace('T',' ') }}</td><td>{{ event.durationHours }} h</td><td class="numeric">{{ value(event.observedInputs?.observedPeakFlowM3s) }}</td><td><button class="text-action" @click="viewEvent(event)">查看实测过程</button></td></tr></tbody></table></div>
      </section>

      <section v-else-if="tab === 'processing'" class="processing-section">
        <InputSourceRecords />
      </section>

    </template>
  </section>
</template>

<script setup>
import { computed, ref, onBeforeUnmount, watch } from 'vue'
import { Refresh, FolderChecked, Search, Download, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useMonitorStore } from '@/stores/monitor'
import { modelApi, modelBackendEnabled } from '@/services/modelBackendService'
import BaseChart from '@/components/charts/BaseChart.vue'
import InputSourceRecords from '@/components/config/InputSourceRecords.vue'
import { initialObservationWindow, observationHour, observationTime } from '@/services/observationWindowPresentation.js'

const app = useAppStore()
const monitor = useMonitorStore()
const route = useRoute()
const available = computed(() => app.basinId === 'nananxi-xufan' && modelBackendEnabled())
const tabs = [{ id: 'observations', label: '实测资料' }, { id: 'events', label: '洪水目录' }, { id: 'processing', label: '入库记录' }]
const variables = { rain: { label: '降雨量', color: '#287ab4' }, level: { label: '水位', color: '#177c72' }, flow: { label: '流量', color: '#bb633e' } }
const tab = ref(route.query.tab === 'inputs' ? 'processing' : tabs.some(item => item.id === route.query.tab) ? route.query.tab : 'observations'), busy = ref(false), error = ref(''), message = ref('')
const summary = ref(null), stations = ref([]), events = ref([]), sources = ref([]), sourceId = ref('')
const variable = ref('rain'), stationId = ref(''), stationSearch = ref(''), eventSearch = ref('')
const start = ref(''), end = ref(''), result = ref(null), page = ref(0), resultQuery = ref('')
if (typeof route.query.station === 'string') stationId.value = route.query.station
if (Object.hasOwn(variables, route.query.variable)) variable.value = route.query.variable
if (observationHour(route.query.end) != null) {
  const last = observationHour(route.query.end)
  end.value = observationTime(last)
  start.value = observationTime(last - 23)
}
let revision = 0, disposed = false
onBeforeUnmount(() => { disposed = true; revision++ })
const number = (v) => Number(v || 0).toLocaleString('zh-CN')
const value = (v) => v == null ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 3 })
const timeLabel = v => String(v || '').replace('T', ' ')
const queryKey = computed(() => [stationId.value, variable.value, start.value, end.value, sourceId.value].join('|'))
const queryChanged = computed(() => resultQuery.value !== queryKey.value)
const filteredStations = computed(() => stations.value.filter(s => s.variable === variable.value && `${s.name}${s.id}`.includes(stationSearch.value.trim())))
const filteredEvents = computed(() => events.value.filter(e => `${e.eventId}${e.floodCode}${e.start}`.includes(eventSearch.value.trim())))
const pageCount = computed(() => Math.max(1, Math.ceil((result.value?.series.length || 0) / 24)))
const pagedSeries = computed(() => result.value?.series.slice(page.value * 24, (page.value + 1) * 24) || [])
const chartOption = computed(() => {
  const r = result.value
  if (!r) return {}
  const rain = r.variable === 'rain'
  return { animation: false, color: [variables[r.variable].color], tooltip: { trigger: 'axis', confine: true },
    grid: { left: 55, right: 20, top: 24, bottom: 65 },
    xAxis: { type: 'category', data: r.series.map(s => s.time.replace('T', ' ')), axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', name: r.unit, inverse: rain, scale: r.variable === 'level', splitLine: { lineStyle: { color: '#e7eced' } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: [{ name: variables[r.variable].label, type: rain ? 'bar' : 'line', data: r.series.map(s => s.value), showSymbol: false, connectNulls: false, smooth: false, barMaxWidth: 8, lineStyle: { width: 1.8 } }] }
})
function params(offsetHours = 0) { return new URLSearchParams({ stationId: stationId.value, variable: variable.value, start: start.value, end: end.value, ...(sourceId.value ? { sourceId: sourceId.value } : {}), ...(offsetHours ? { offsetHours } : {}) }) }
async function act(work) {
  if (!available.value || busy.value) return
  const current = ++revision
  busy.value = true; error.value = ''; message.value = ''
  try { await work(() => !disposed && current === revision && available.value) }
  catch (e) { if (!disposed && current === revision) error.value = e.message }
  finally { if (!disposed && current === revision) busy.value = false }
}
async function load() {
  await act(async valid => {
    const data = await Promise.all(['summary', 'stations', 'events', 'sources'].map(p => modelApi(`database/${p}`)))
    if (!valid()) return
    summary.value = data[0]; stations.value = data[1].stations; events.value = data[2].events; sources.value = data[3].sources
    if (!stations.value.some(s => s.variable === variable.value && s.id === stationId.value)) stationId.value = stations.value.find(s => s.variable === variable.value)?.id || ''
    if (!start.value || !end.value) {
      const window = initialObservationWindow(stations.value.find(s => s.variable === variable.value && s.id === stationId.value), monitor.hasHistoricalObservations ? monitor.currentTimeLabel : app.dataTime)
      if (window) { start.value = window.start; end.value = window.end }
    }
    if (!stationId.value || !start.value || !end.value) return
    const key = queryKey.value
    const dataWindow = await modelApi(`database/window?${params()}`)
    if (valid() && key === queryKey.value) { result.value = dataWindow; resultQuery.value = key; page.value = 0 }
  })
}
async function queryWindow(offsetHours = 0) {
  await act(async valid => { const key = queryKey.value; const data = await modelApi(`database/window?${params(Number.isInteger(offsetHours) ? offsetHours : 0)}`); if (valid() && key === queryKey.value) { result.value = data; resultQuery.value = key; page.value = 0 } })
}
function showDisplayPeriod(direction) {
  const display = result.value?.display
  if (!display || queryChanged.value) return
  queryWindow(Math.max(0, display.offsetHours + direction * display.limitHours))
}
function changeVariable(key) { variable.value = key; stationSearch.value = ''; stationId.value = stations.value.find(s => s.variable === key)?.id || ''; queryWindow() }
function selectStation(id) { stationId.value = id; queryWindow() }
function viewEvent(event) {
  variable.value = 'flow'
  const outlet = event.observedInputs?.stationId || event.sectionId || event.stationId
  stationId.value = stations.value.find(s => s.variable === 'flow' && s.id === outlet)?.id
    || stations.value.find(s => s.variable === 'flow')?.id || ''
  start.value = event.start.slice(0, 16); end.value = event.end.slice(0, 16); tab.value = 'observations'; queryWindow()
}
function exportSeries() {
  if (queryChanged.value || !result.value) return
  const anchor = document.createElement('a'); anchor.href = `${String(import.meta.env.VITE_MODEL_API_BASE).replace(/\/$/, '')}/database/export?${params()}`; anchor.download = 'observations.csv'; anchor.click()
}
async function backup() { await act(async valid => { const r = await modelApi('database/backup', { method: 'POST', body: {}, timeoutMs: 180000 }); if (valid()) message.value = `备份已校验：${r.path}` }) }
watch([available, () => app.contextKey], ([enabled], previous) => {
  revision++; busy.value = false; result.value = null
  if (previous) {
    sourceId.value = ''; start.value = ''; end.value = ''
  }
  if (enabled) load()
}, { immediate: true })
watch(() => route.query.tab, value => {
  if (value === 'inputs') tab.value = 'processing'
  else if (tabs.some(item => item.id === value)) tab.value = value
})
</script>

<style scoped>
.data-workspace { padding: 20px 24px; min-width: 0; color: #243b43; background: #fff; }
.data-heading,.heading-actions,.section-toolbar,.series-title,.table-heading,.table-heading>div { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.data-heading,.series-title,.table-heading { justify-content: space-between; }
.eyebrow { font-size: 12px; color: #64777e; } h1 { font-size: 23px; margin: 5px 0 16px; } h2 { font-size: 16px; margin: 0; } h3 { font-size: 14px; margin: 0; } small { font-weight: 400; }
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 7px; text-decoration: none; } .btn svg,.search-box svg { width: 16px; height: 16px; flex-shrink: 0; } .icon-action { width: 34px; height: 34px; padding: 7px; flex: 0 0 34px; }
.database-state { color: #44786a; font-size: 12px; }.database-state i { display: inline-block; width: 6px; height: 6px; background: #448e6d; border-radius: 50%; margin-right: 5px; }
.data-totals { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); border-top: 1px solid #dde4e7; border-bottom: 1px solid #dde4e7; margin-top: 6px; }
.data-totals>div { padding: 18px 18px 16px 0; } .data-totals>div+div { padding-left: 20px; border-left: 1px solid #e5ebed; }.data-totals span { color: #687c85; font-size: 12px; }.data-totals b { display: block; font-size: 25px; font-variant-numeric: tabular-nums; margin: 5px 0; }.data-totals small { font-size: 12px; margin-left: 8px; }.data-totals p { font-size: 11px; color: #697c81; margin: 0; line-height: 1.7; }
.data-tabs { display: flex; gap: 26px; border-bottom: 1px solid #dde4e7; margin-bottom: 20px; overflow-x: auto; }.data-tabs button { border: 0; background: transparent; padding: 16px 0 12px; font-size: 14px; color: #687b82; border-bottom: 3px solid transparent; white-space: nowrap; cursor: pointer; }.data-tabs .active { color: #146858; border-bottom-color: #146858; font-weight: 700; }
.observation-workspace { display: grid; grid-template-columns: 220px minmax(0,1fr); gap: 24px; }.station-browser { border-right: 1px solid #e0e7e9; padding-right: 18px; min-width: 0; }.segmented { display: flex; background: #f0f4f4; padding: 3px; border-radius: 4px; margin-bottom: 12px; }.segmented button { flex: 1; border: 0; border-radius: 3px; background: transparent; padding: 7px 3px; cursor: pointer; font-size: 12px; }.segmented .active { background: #fff; color: #146858; box-shadow: 0 1px 3px #0001; }
.search-box { display: flex; align-items: center; gap: 6px; border: 1px solid #dce4e6; padding: 7px 8px; border-radius: 4px; color: #71848a; }.search-box input { width: 100%; min-width: 0; border: 0; outline: 0; font-size: 12px; }.station-list { margin-top: 10px; }.station-list button { width: 100%; border: 0; border-left: 3px solid transparent; padding: 12px 7px; display: flex; align-items: center; justify-content: space-between; gap: 6px; background: #fff; text-align: left; cursor: pointer; font-size: 12px; }.station-list .selected { background: #eaf3ee; border-left-color: #2e806c; }.station-list button:hover { background: #f2f6f4; }.station-list small { display: block; color: #7b8a90; font-size: 10px; margin-top: 4px; }.station-list button>span:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.observation-main { min-width: 0; }.query-toolbar,.scheme-form,.source-form { display: flex; align-items: end; flex-wrap: wrap; gap: 12px; }.query-toolbar label,.scheme-form label,.source-form>label { display: grid; gap: 5px; color: #65777e; font-size: 12px; }.query-toolbar .input { max-width: 220px; }.input { min-width: 0; width: 100%; }.series-title { margin-top: 24px; }.series-title>span { font-size: 12px; color: #77898e; }.series-summary { display: flex; gap: 22px; flex-wrap: wrap; margin-top: 12px; font-size: 12px; color: #6b7d85; }.series-summary b { color: #263b42; font-variant-numeric: tabular-nums; }.series-summary .caution { color: #a96116; }.hydrograph { height: 270px; margin: 12px 0; }.source-details { color: #75878e; font-size: 11px; border-top: 1px solid #e3eaeb; padding: 9px 0; }.source-details summary { cursor: pointer; }.source-details code { overflow-wrap: anywhere; }.table-heading { margin: 12px 0; }.table-heading>div { font-size: 12px; }.records-scroll { overflow: auto; max-height: 540px; }.table { min-width: 560px; }.table th { position: sticky; top: 0; background: #f3f6f7; z-index: 1; }.table td { font-size: 12px; }.numeric { font-variant-numeric: tabular-nums; }.text-action { border: 0; background: none; color: #197367; cursor: pointer; padding: 4px; }
.section-toolbar { margin: 12px 0 20px; }.section-toolbar h2 { margin-right: auto; }.source-form { margin: 16px 0 24px; }.source-form fieldset { flex-basis: 100%; border: 0; border-top: 1px solid #e0e7e9; padding: 12px 0 0; display: flex; flex-wrap: wrap; gap: 14px; }.source-form fieldset label { display: flex; gap: 5px; align-items: center; font-size: 12px; }.source-form legend { color: #65777e; font-size: 12px; padding-right: 12px; }.scheme-form { padding-bottom: 20px; }.scheme-form label:first-child { flex: 1; min-width: 220px; }.source-receipt { font-size: 12px; overflow-wrap: anywhere; }.error-message,.success-message,.notice { padding: 10px 12px; font-size: 13px; margin: 10px 0; overflow-wrap: anywhere; }.error-message { background: #fff1ed; color: #a44531; }.success-message { background: #edf6ee; color: #36734d; }.notice { background: #f5f7f7; }.empty-state { padding: 80px 20px; color: #798b91; text-align: center; }
.scheme-field { display: grid; gap: 5px; flex: 1; min-width: 220px; font-size: 12px; color: #65777e; }.scheme-field label { min-width: 0; }
.query-note,.query-range { color: #61777e; font-size: 12px; line-height: 1.7; }.query-range { margin: 8px 0; }.display-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; margin-top: 16px; padding: 10px 12px; border: 1px solid #dce7e2; background: #f5f8f6; font-size: 12px; }.display-toolbar small { display: block; margin-top: 4px; color: #75878e; }.display-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.section-intro { margin: 0 0 18px; color: #597179; font-size: 13px; line-height: 1.8; }
.saved-conditions { margin-top: 28px; padding: 14px 0; border-top: 1px solid #dfe8e7; color: #536e74; font-size: 13px; }
.saved-conditions>summary { display: flex; align-items: center; gap: 12px; cursor: pointer; list-style: none; }
.saved-conditions>summary::before { content: '›'; color: #347d6c; font-size: 18px; }
.saved-conditions[open]>summary::before { transform: rotate(90deg); }
.saved-conditions>summary span { color: #7a8d92; font-size: 12px; }
@media (max-width: 1100px) { .data-totals { grid-template-columns: repeat(2,minmax(0,1fr)); }.data-totals>div:nth-child(3) { border-left: 0; padding-left: 0; border-top: 1px solid #e5ebed; }.data-totals>div:nth-child(4) { border-top: 1px solid #e5ebed; }.observation-workspace { grid-template-columns: 185px minmax(0,1fr); gap: 16px; } }
@media (max-width: 700px) { .data-workspace { padding: 14px 12px; }.data-totals b { font-size: 21px; }.data-totals>div { padding: 12px 8px 12px 0; }.data-totals>div+div { padding-left: 10px; }.observation-workspace { grid-template-columns: minmax(0,1fr); }.station-browser { border-right: 0; padding-right: 0; }.station-list { display: flex; overflow-x: auto; }.station-list button { flex: 0 0 145px; }.data-tabs { gap: 20px; }.query-toolbar label { flex: 1; min-width: 145px; }.query-toolbar .input { max-width: 100%; }.series-summary { gap: 10px; }.source-form>label { width: 100%; }.scheme-form label { width: 100%; }.hydrograph { height: 250px; } }
</style>
