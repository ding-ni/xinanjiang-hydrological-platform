<template>
  <div class="timeline panel">
    <div class="timeline-main">
      <div class="label-block">
        <span class="ttl">过程回放</span>
        <span class="sub">{{ app.hasLakeBalance ? '离线降水场预览' : usesModelInputs ? '网格降雨与站点实测回放' : '离线原始雨量资料' }} · {{ playbackMetadataReady ? monitor.timelineLabel : '选择回放时段' }}</span>
      </div>
      <button class="play" type="button" :disabled="playbackDisabled" :aria-label="monitor.playing ? '暂停回放' : '开始回放'" :title="monitor.playing ? '暂停回放' : '开始回放'" @click="monitor.togglePlay()">
      <component :is="monitor.playing ? VideoPause : VideoPlay" />
      </button>
      <button class="step" type="button" :disabled="playbackDisabled" @click="step(-1)"><ArrowLeft />{{ stepText }}</button>
      <button class="step" type="button" :disabled="playbackDisabled" @click="step(1)">{{ stepText }}<ArrowRight /></button>
      <label class="play-speed">
        <span>播放速度</span>
        <select class="replay-input" :value="monitor.playbackSpeed" :disabled="playbackDisabled" @change="monitor.setPlaybackSpeed($event.target.value)">
          <option :value="0.5">0.5×</option>
          <option :value="1">1×</option>
          <option :value="2">2×</option>
          <option :value="4">4×</option>
        </select>
      </label>
      <label class="time-picker">
        <span>回放时刻</span>
        <input type="datetime-local" :value="currentInput" :min="minInput" :max="maxInput" :disabled="playbackDisabled" :step="monitor.timelineStepMinutes * 60" @change="setCalendarTime" />
      </label>
      <div class="range-wrap">
        <input
          class="range"
          type="range"
          :min="playbackMetadataReady ? monitor.timeMin : 0"
          :max="playbackMetadataReady ? monitor.timeMax : 0"
          :value="playbackMetadataReady ? monitor.timeIndex : 0"
          :disabled="playbackDisabled"
          @input="onInput"
        />
        <div class="ticks">
          <span>{{ playbackMetadataReady ? monitor.timelineStart.slice(5) : '—' }}</span>
          <span>{{ playbackMetadataReady ? monitor.timelineEnd.slice(5) : '—' }}</span>
          <b>当前 {{ playbackMetadataReady ? monitor.currentTimeLabel.slice(5) : '—' }}</b>
        </div>
      </div>
    </div>
    <div v-if="app.hasObservationCatalog" class="replay-controls">
      <label class="replay-mode">
        <span>资料窗口</span>
        <select v-model="replayMode" class="replay-input" :disabled="monitor.historicalLoading" @change="onReplayModeChange">
          <option value="latest">最新共同覆盖时段</option>
          <option value="range">指定时段</option>
          <option value="event">洪水场次</option>
        </select>
      </label>
      <template v-if="replayMode === 'event'">
        <label class="replay-event">
          <span>选择场次</span>
          <select v-model="selectedEventId" class="replay-input" :disabled="monitor.historicalLoading || !eventOptions.length" @change="loadSelectedEvent">
            <option value="" disabled>{{ eventOptions.length ? '请选择洪水场次' : noEventCatalogLabel }}</option>
            <option v-for="event in eventOptions" :key="event.eventId || event.id" :value="event.eventId || event.id">{{ eventOptionLabel(event) }}</option>
          </select>
        </label>
        <span class="replay-window">{{ replayWindowLabel }}</span>
      </template>
      <template v-else-if="replayMode === 'range'">
        <label><span>开始</span><input v-model="rangeStart" class="replay-input" type="datetime-local" :min="catalogMinInput" :max="catalogMaxInput" /></label>
        <label><span>结束</span><input v-model="rangeEnd" class="replay-input" type="datetime-local" :min="catalogMinInput" :max="catalogMaxInput" /></label>
        <button class="replay-load" type="button" :disabled="monitor.historicalLoading" @click="loadRange">载入时段</button>
      </template>
      <template v-else>
        <span class="replay-window">{{ replayWindowLabel }}</span>
        <label class="latest-window"><span>窗口长度</span><select v-model.number="latestHours" class="replay-input" :disabled="monitor.historicalLoading" @change="loadLatest"><option :value="24">24 小时</option><option :value="72">72 小时</option><option :value="168">7 天</option></select></label>
        <button class="replay-load" type="button" :disabled="monitor.historicalLoading" @click="loadLatest">刷新并载入最新共同覆盖时段</button>
      </template>
      <span v-if="replayError || monitor.historicalError" class="replay-error">{{ replayError || monitor.historicalError }}</span>
      <span v-else class="replay-help">{{ replayHelp }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { ArrowLeft, ArrowRight, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { useMonitorStore } from '@/stores/monitor'
import { useAppStore } from '@/stores/app'
import { useCalibrationStore } from '@/stores/calibration'
import { observationBoundaryInput } from '@/services/observationCoverageService'
import { finiteInputValue, usesModelInputService } from '@/services/modelInputService'

const monitor = useMonitorStore()
const app = useAppStore()
const calibration = useCalibrationStore()
const replayError = ref('')
const selectedEventId = ref('')
const rangeStart = ref('')
const rangeEnd = ref('')
const latestHours = ref(72)
const usesModelInputs = computed(() => usesModelInputService(app.activeContext))
const playbackMetadataReady = computed(() => !usesModelInputs.value || monitor.hasHistoricalObservations)
const playbackDisabled = computed(() => monitor.historicalLoading || (usesModelInputs.value && !monitor.hasHistoricalObservations))
const replayWindowLabel = computed(() => playbackMetadataReady.value
  ? `${monitor.historicalReplayLabel} · ${monitor.timelineStart} 至 ${monitor.timelineEnd}`
  : '请选择有效回放时段')
const replayMode = computed({
  get: () => monitor.historicalReplayMode,
  set: (value) => { monitor.historicalReplayMode = value }
})
const currentInput = computed(() => !playbackMetadataReady.value || monitor.currentTimeLabel === '—' ? '' : monitor.currentTimeLabel.replace(' ', 'T'))
function commonCatalogBoundary(kind) {
  if (!app.hasObservationCatalog) return ''
  return observationBoundaryInput(monitor.historicalCatalog, kind)
}
const minInput = computed(() => commonCatalogBoundary('start') || monitor.timelineStart.replace(' ', 'T'))
const maxInput = computed(() => commonCatalogBoundary('end') || monitor.timelineEnd.replace(' ', 'T'))
const stepText = computed(() => usesModelInputs.value || monitor.timelineStepMinutes === 60 ? '1 h' : '10 min')
const eventOptions = computed(() => [...(usesModelInputs.value ? monitor.historicalCatalog?.events || [] : app.hasEventCatalog ? (calibration.events || []) : (monitor.historicalEvents || []))]
  .filter((event) => event.status !== 'inactive')
  .sort((a, b) => new Date(b.start || 0) - new Date(a.start || 0)))
const noEventCatalogLabel = computed(() => {
  if (monitor.historicalLoading || app.inputCatalogLoading) return '场次目录加载中'
  if (monitor.historicalError || app.inputCatalogError || app.backendError) return '场次目录读取失败'
  if (usesModelInputs.value && monitor.historicalCatalog?.bundleVersionId === app.selectedInputBundleVersionId) return '本输入版本无覆盖内登记场次'
  return '场次目录待载入'
})
const catalogMinInput = computed(() => commonCatalogBoundary('start'))
const catalogMaxInput = computed(() => commonCatalogBoundary('end'))
const replayHelp = computed(() => {
  if (usesModelInputs.value) return '降雨网格采用选定计算资料；测站显示同一时刻的库内实测雨量、水位和流量。'
  if (replayMode.value === 'event') return '按已登记洪水场次的起止时间读取对应雨量、流量和降雨场。'
  if (replayMode.value === 'range') return '在雨量与出口水文资料的共同覆盖范围内读取指定时段。'
  return '按雨量与出口水文资料的共同覆盖范围读取最新窗口。'
})

watch(() => app.basinId, (domainId) => monitor.setTimelineContext(domainId), { immediate: true })
watch(() => app.inputSelectionKey, () => {
  if (!usesModelInputs.value) return
  rangeStart.value = ''; rangeEnd.value = ''; selectedEventId.value = ''; replayError.value = ''; replayMode.value = 'latest'
})
watch(() => monitor.historicalPreview, (preview) => {
  if (!preview) return
  if (!rangeStart.value) rangeStart.value = preview.start
  if (!rangeEnd.value) rangeEnd.value = preview.end
}, { immediate: true })
watch(eventOptions, (events) => {
  if (!events.some((event) => String(event.eventId || event.id) === selectedEventId.value)) selectedEventId.value = events.length ? String(events[0].eventId || events[0].id) : ''
}, { immediate: true })
watch(() => calibration.events, (events) => {
  if (app.hasEventCatalog && !usesModelInputs.value) monitor.syncHistoricalEvents(events)
}, { immediate: true, deep: true })

function onInput(e) {
  monitor.setTimeIndex(e.target.value)
  app.setDataTime(monitor.currentTimeLabel, { persist: false })
}

function step(delta) {
  monitor.setTimeIndex(monitor.timeIndex + delta)
  app.setDataTime(monitor.currentTimeLabel, { persist: false })
}

async function setCalendarTime(e) {
  replayError.value = ''
  try {
    const base = dayjs(monitor.timelineStart)
    const picked = dayjs(e.target.value)
    if (app.hasObservationCatalog && (picked.isBefore(base) || picked.isAfter(dayjs(monitor.timelineEnd)))) {
      const catalogMin = dayjs(minInput.value)
      const catalogMax = dayjs(maxInput.value)
      const requestedStart = picked.subtract(36, 'hour')
      const requestedEnd = picked.add(36, 'hour')
      const start = requestedStart.isBefore(catalogMin) ? catalogMin : requestedStart
      const end = requestedEnd.isAfter(catalogMax) ? catalogMax : requestedEnd
      await monitor.loadHistoricalWindow(
        start.format('YYYY-MM-DDTHH:00'),
        end.format('YYYY-MM-DDTHH:00'),
        { mode: 'range', label: '指定资料时段' }
      )
    }
    const loadedBase = dayjs(monitor.timelineStart)
    monitor.setTimeIndex(Math.round(picked.diff(loadedBase, 'minute') / monitor.timelineStepMinutes))
    app.setDataTime(monitor.currentTimeLabel)
  } catch (error) {
    replayError.value = error instanceof Error ? error.message : String(error)
  }
}

function eventOptionLabel(event) {
  const code = event.sourceFloodNo || event.floodCode || event.eventId || event.id
  const observedPeak = finiteInputValue(event.observedPeak)
  const peak = observedPeak != null ? ` · Qmax ${observedPeak.toLocaleString()} m³/s` : ''
  return `${code} · ${String(event.start || '').replace('T', ' ')}${peak}`
}

async function loadLatest() {
  replayError.value = ''
  try {
    await monitor.loadLatestHistoricalWindow(latestHours.value, { refresh: true })
  } catch (error) {
    replayError.value = error instanceof Error ? error.message : String(error)
  }
}

async function loadSelectedEvent() {
  if (!selectedEventId.value) return
  replayError.value = ''
  try {
    await monitor.loadHistoricalEvent(selectedEventId.value)
  } catch (error) {
    replayError.value = error instanceof Error ? error.message : String(error)
  }
}

async function loadRange() {
  replayError.value = ''
  const start = dayjs(rangeStart.value)
  const end = dayjs(rangeEnd.value)
  if (!start.isValid() || !end.isValid() || !start.isBefore(end)) {
    replayError.value = '请填写有效的资料起止时段'
    return
  }
  const catalogMin = dayjs(catalogMinInput.value)
  const catalogMax = dayjs(catalogMaxInput.value)
  if (
    (catalogMin.isValid() && start.isBefore(catalogMin)) ||
    (catalogMax.isValid() && end.isAfter(catalogMax))
  ) {
    replayError.value = `资料范围应在 ${catalogMin.format('YYYY-MM-DD HH:mm')} 至 ${catalogMax.format('YYYY-MM-DD HH:mm')} 内`
    return
  }
  try {
    await monitor.loadHistoricalWindow(start.format('YYYY-MM-DDTHH:00'), end.format('YYYY-MM-DDTHH:00'), { mode: 'range', label: '指定资料时段' })
  } catch (error) {
    replayError.value = error instanceof Error ? error.message : String(error)
  }
}

function onReplayModeChange() {
  replayError.value = ''
  monitor.stopPlay()
  if (replayMode.value === 'latest') loadLatest()
  else if (replayMode.value === 'event' && selectedEventId.value) loadSelectedEvent()
  else if (replayMode.value === 'range' && monitor.historicalPreview) {
    rangeStart.value = monitor.historicalPreview.start
    rangeEnd.value = monitor.historicalPreview.end
  }
}

onBeforeUnmount(() => {
  if (monitor.currentTimeLabel !== '—') app.setDataTime(monitor.currentTimeLabel)
  monitor.stopPlay()
})
</script>

<style scoped>
.timeline {
  display: grid;
  gap: 9px;
  min-height: 72px;
  padding: 10px 14px;
}
.timeline-main { display: grid; grid-template-columns: auto auto auto auto auto minmax(190px, auto) minmax(180px, 1fr); gap: 10px; align-items: center; min-width: 0; }
.time-picker {
  display: grid;
  gap: 3px;
  color: #60747b;
  font-size: 10px;
}
.time-picker input {
  width: 190px;
  height: 34px;
  padding: 0 8px;
  border: 1px solid #c8d8ec;
  border-radius: 5px;
  background: #fff;
  color: #17343e;
}

.play-speed { display: grid; gap: 3px; min-width: 74px; color: #60747b; font-size: 10px; }
.play-speed .replay-input { height: 32px; }

.label-block {
  display: grid;
  gap: 2px;
  padding-right: 8px;
  border-right: 1px solid #e0eaf6;
}

.ttl {
  color: #173052;
  font-size: 13px;
  font-weight: 800;
}

.sub {
  color: #6a7c95;
  font-size: 11px;
  white-space: nowrap;
}

.play,
.step {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  border: 1px solid #c8d8ec;
  border-radius: 8px;
  background: linear-gradient(180deg, #fff, #f5f9ff);
  color: var(--blue);
  cursor: pointer;
  font-weight: 800;
}

.play {
  width: 36px;
}

.play svg,
.step svg {
  width: 15px;
  height: 15px;
}

.step {
  min-width: 54px;
  padding: 0 8px;
  font-size: 12px;
}

.play:hover,
.step:hover {
  border-color: var(--blue);
  background: var(--blue-soft);
}
.play:disabled,
.step:disabled,
.replay-load:disabled { cursor: wait; opacity: .55; }

.range-wrap {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.range {
  width: 100%;
  accent-color: var(--blue);
}

.ticks {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: #6a7c95;
  font-size: 11px;
}

.ticks b {
  color: var(--blue);
  font-weight: 800;
}

.replay-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 7px 10px; padding-top: 8px; border-top: 1px solid #e0eaf6; }
.replay-controls label { display: grid; gap: 3px; min-width: 150px; color: #60747b; font-size: 10px; }
.replay-controls .replay-mode { min-width: 118px; }
.replay-controls .replay-event { flex: 1 1 270px; min-width: 240px; }
.replay-controls .latest-window { min-width: 110px; }
.replay-input { width: 100%; height: 30px; padding: 0 7px; border: 1px solid #c8d8ec; border-radius: 5px; background: #fff; color: #17343e; font-size: 11px; }
.replay-load { height: 30px; padding: 0 11px; border: 1px solid #9bc9c2; border-radius: 5px; background: #edf8f5; color: #14776e; font-size: 11px; font-weight: 800; cursor: pointer; }
.replay-load:hover { border-color: #178276; background: #dff2ed; }
.replay-window { align-self: center; min-width: 220px; color: #55717a; font-size: 10px; font-variant-numeric: tabular-nums; }
.replay-error { align-self: center; color: #a44f42; font-size: 10px; }
.replay-help { align-self: center; color: #6f858c; font-size: 10px; }

@media (max-width: 900px) {
  .timeline-main { grid-template-columns: auto auto auto auto minmax(0, 1fr); }
  .label-block,
  .play-speed,
  .time-picker,
  .range-wrap {
    grid-column: 1 / -1;
  }
  .replay-controls { align-items: stretch; }
  .replay-controls label,
  .replay-controls .replay-event { flex: 1 1 160px; min-width: 0; }
  .time-picker input {
    width: 100%;
  }
}
</style>
