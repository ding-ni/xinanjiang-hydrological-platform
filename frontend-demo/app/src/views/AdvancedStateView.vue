<template>
  <section class="view-state">
    <div class="metric-strip">
      <div class="metric"><span>当前状态</span><b :title="state.activeInitialState.id">{{ isModelState ? '模拟蓄水状态' : state.activeInitialState.id }}</b></div>
      <div class="metric"><span>状态时刻</span><b>{{ state.activeInitialState.time }}</b></div>
      <div class="metric"><span>来源断面</span><b>{{ state.activeInitialState.sourceSection }}</b></div>
      <div class="metric"><span>状态用途</span><b>{{ state.activeInitialState.analysisStatus }}</b></div>
      <ContextStatusMetric />
    </div>

    <StateLibrary v-if="isModelWorkspace" :states="state.domainInitialStates" :active-id="state.activeInitialState.id" :model-hash="app.backendCase?.modelImplementationSHA256" :disabled="state.taskRunning" @select="loadNode" @continue="prepareContinuation" @refresh="refreshStates" @imported="selectImportedState" />
    <div v-else class="node-workbench panel">
      <div class="node-heading">
        <strong>可用起算状态</strong>
        <span>{{ isModelWorkspace ? '状态文件状态' : '演示状态' }}</span>
      </div>
      <div class="node-list">
        <button
          v-for="node in state.domainInitialStates"
          :key="node.id"
          type="button"
          class="node-item"
          :title="node.id"
          :class="{ active: node.id === state.activeInitialState.id }"
          @click="loadNode(node.id)"
        >
          <span><b>{{ node.time }}</b><small>{{ node.sourceSection }} · {{ node.source }}<template v-if="node.resultSource === 'model-backend'"> · {{ node.id.slice(-8) }}</template></small></span>
          <em>{{ node.status }}</em>
        </button>
      </div>
      <div v-if="state.domainInitialStates.length" class="node-means" :class="{ 'node-means-snow': app.isColdRegion }" aria-label="当前节点状态量">
        <div><span>张力水 W/WM</span><b>{{ formatNumber(state.activeInitialState.means.soil) }}</b></div>
        <div><span>自由水 S</span><b>{{ formatNumber(state.activeInitialState.means.free) }} mm</b></div>
        <div v-if="app.isColdRegion"><span>积雪水当量 SWE</span><b>{{ state.activeInitialState.means.swe }} mm</b></div>
        <div v-if="app.isColdRegion"><span>冻土冻结程度</span><b>{{ state.activeInitialState.means.frost }}%</b></div>
      </div>
      <div v-else class="state-empty-inline">暂无起算状态</div>
      <button class="btn primary use-node" type="button" :disabled="!state.domainInitialStates.length || state.taskRunning" @click="isModelWorkspace ? prepareContinuation() : continueForecast()">{{ isModelWorkspace ? '用于续算' : '采用为演示状态' }}</button>
    </div>

    <section class="panel panel-pad state-task-panel" :class="{ 'has-task': activeTask }">
      <p v-if="state.pendingHandoff" class="state-source-note">来源：{{ state.pendingHandoff.sourceLabel }} · {{ state.pendingHandoff.issueTime }}</p>
      <div class="state-task-form">
        <div>
          <span class="task-label">状态计算</span>
          <div class="mode-switch">
            <button type="button" :class="{ active: stateTaskMode === 'initialization' }" @click="stateTaskMode = 'initialization'">进阶初始化</button>
            <button type="button" :class="{ active: stateTaskMode === 'continuation' }" @click="stateTaskMode = 'continuation'">续算状态</button>
          </div>
        </div>
        <template v-if="isModelWorkspace && stateTaskMode === 'initialization'">
          <div class="field compact-field"><label for="initialization-strategy">初始化方式</label><select id="initialization-strategy" v-model="initializationStrategy" class="input"><option v-for="strategy in initializationStrategies" :key="strategy.id" :value="strategy.id">{{ strategy.label }}</option></select></div>
          <div v-if="initializationStrategy !== 'CONTINUOUS'" class="field compact-field"><label for="state-warmup-days">预热天数</label><input id="state-warmup-days" v-model.number="stateWarmupDays" class="input" type="number" min="1" max="365" step="1" /></div>
          <p v-else class="state-source-note">从 {{ calibration.continuousOrigin?.replace('T', ' ') }} 连续补算至指定时刻。</p>
        </template>
        <div class="field compact-field"><label for="state-target-time">计算至</label><input id="state-target-time" ref="targetTimeInput" v-model="stateTargetTime" class="input" type="datetime-local" :min="stateTaskMode === 'continuation' ? continuationStartTime : undefined" /></div>
        <button v-if="state.taskRunning" class="btn" type="button" @click="state.cancelStateTask()">停止任务</button>
        <button v-else class="btn primary" type="button" :disabled="!stateCanRun" @click="runStateTask">开始{{ stateTaskMode === 'initialization' ? '初始化' : '续算' }}</button>
      </div>
      <details v-if="app.basinId === 'nananxi-xufan'" class="state-input-details"><summary>计算方案</summary><div class="state-input-status" aria-label="状态计算输入">
        <div><span>参数方案</span><b>{{ stateParameterVersion }}</b></div>
        <div><span>当前输入版本</span><b :class="app.hasModelInput ? 'ok' : 'warn'">{{ app.hasModelInput ? app.selectedInputBundleVersionId : '当前尺度真实输入未就绪' }}</b></div>
        <div><span>{{ stateTaskMode === 'continuation' ? '续算终点' : '预热终点' }}</span><b>{{ stateTargetTime.replace('T', ' ') }}</b></div>
        <div><span>计算条件</span><b :class="app.backendCase ? 'ok' : 'warn'">{{ stateScientificLabel }}</b></div>
      </div></details>
      <p v-if="stateTaskMode === 'initialization' && hasContinuousCandidate && initializationStrategy !== 'CONTINUOUS'" class="state-source-note">此方式改用独立初始化条件；原连续方案请选“从原连续起点补算”。</p>
      <TaskStatusPanel v-if="activeTask" :task="activeTask" />
      <p v-if="!stateCanRun" class="state-block-note">{{ stateBlockReason }}</p>
    </section>

    <template v-if="state.domainInitialStates.length && (isModelState || stateAvailability.ready)">
    <div v-if="isModelState" class="time-toolbar panel" aria-label="真实逐时网格回放">
      <div class="time-title"><strong>{{ state.activeInitialState.checkpoint?.importProvenance ? '导入状态' : '状态演变' }}</strong><span>{{ state.activeInitialState.checkpoint?.importProvenance ? state.activeInitialState.time : replayCatalog ? `${replayCatalog.times.length} 个时刻` : replayError || '正在读取过程' }}</span></div>
      <select v-if="replayCatalog" class="input time-input" v-model="replayTime" aria-label="状态回放时刻">
        <option v-for="time in replayCatalog.times" :key="time" :value="time">{{ time.replace('T', ' ') }}</option>
      </select>
      <span v-if="replayLoading" class="hint">读取真实网格状态…</span>
    </div>
    <div v-else class="time-toolbar panel">
      <div class="time-title">
        <strong>本次预报状态演变</strong>
        <span>展示预报时段内的状态空间分布和网格变化。</span>
      </div>
      <div class="time-tabs" aria-label="常用预报时效">
        <button
          v-for="hour in quickHours"
          :key="hour"
          type="button"
          :class="{ active: selectedHour === hour }"
          @click="selectedHour = hour"
        >
          {{ hour === 0 ? '起报' : `+${hour} h` }}
        </button>
      </div>
      <input class="input time-input" type="datetime-local" :min="startInput" :max="endInput" :value="displayInput" @change="onTimeInput" />
    </div>

    <div class="body">
      <aside class="panel panel-pad left">
        <h3 class="panel-title">流域蓄水状态</h3>
        <div v-if="app.isColdRegion" class="state-family" aria-label="状态变量类别">
          <button type="button" :class="{ active: activeGroup === 'water' }" @click="selectGroup('water')">水土状态</button>
          <button type="button" :class="{ active: activeGroup === 'snow' }" @click="selectGroup('snow')">冰雪冻土</button>
        </div>
        <div class="variable-tabs">
          <button v-for="v in visibleVariables" :key="v.key" type="button" :class="{ active: state.activeVar === v.key }" @click="state.setActiveVar(v.key)">
            <b>{{ v.name }}</b><span>{{ v.short }}{{ v.unit ? ` · ${v.unit}` : '' }}</span>
          </button>
        </div>

        <div class="form-group-title">{{ isModelState ? '有效网格算术均值' : '演示流域均值' }}</div>
        <div class="state-value">
          <strong>{{ displayMean }}</strong>
          <span>{{ stateUnit }}</span>
        </div>
        <div v-if="isModelState" class="stat-box compact">
          <div class="stat-line"><span>计算时刻</span><b>{{ displayTime }}</b></div>
          <div class="stat-line"><span>状态来源</span><b>{{ replayFrame ? '逐时网格过程' : '末状态状态文件' }}</b></div>
        </div>
        <div v-else class="stat-box compact">
          <div class="stat-line"><span>起报值</span><b>{{ formatValue(baseMean) }}</b></div>
          <div class="stat-line"><span>当前时刻</span><b class="hot">{{ formatValue(currentMean) }}</b></div>
          <div class="stat-line"><span>相对起报</span><b :class="deltaValue >= 0 ? 'warn' : 'ok'">{{ deltaText }}</b></div>
        </div>

        <details><summary class="form-group-title">状态来源</summary><div class="stat-box compact">
          <div class="stat-line"><span>模型配置</span><b>{{ state.activeInitialState.modelConfigId }}</b></div>
          <div class="stat-line"><span>观测资料</span><b :title="state.activeInitialState.observationDataId">{{ state.activeInitialState.observationSource }}</b></div>
          <div class="stat-line"><span>{{ isModelState ? '历史驱动' : '演示降水' }}</span><b :title="state.activeInitialState.precipitationDataId">{{ state.activeInitialState.forcingSource }}</b></div>
          <div class="stat-line"><span>父状态</span><b>{{ state.activeInitialState.parentStateVersionId || '初始状态' }}</b></div>
          <div class="stat-line"><span>参数版本</span><b>{{ state.activeInitialState.parameterVersionId || app.paramVersion }}</b></div>
          <div class="stat-line"><span>来源任务</span><b>{{ state.activeInitialState.sourceTaskId || '资料登记' }}</b></div>
        </div></details>
      </aside>

      <article class="panel map-panel">
        <div class="map-head">
          <div><strong>{{ currentVar.name }}状态分布</strong><span>{{ displayTime }} · {{ isModelState ? currentVar.short : `演示 +${selectedHour} h` }}</span></div>
          <span class="map-sub">点击网格查看状态及已保存过程</span>
        </div>
        <div class="map-body">
          <BasinMap
            :stations="activeStations"
            :layers="layers"
            :grid-cells="liveCells"
            :grid-paint-field="currentVar.field"
            interactive-grid
            :legend-mode="currentVar.legend"
            :show-tools="true"
            @select-grid="onGrid"
          />
        </div>
      </article>

      <aside class="panel panel-pad right">
        <h3 class="panel-title">{{ isModelState ? '网格状态' : '演示网格状态' }}</h3>
        <template v-if="selected">
          <div class="cell-head"><span>计算网格</span><strong>{{ selected.id }}</strong></div>
          <div class="stat-box compact">
            <div class="stat-line"><span>计算时刻</span><b>{{ displayTime }}</b></div>
            <div class="stat-line"><span>上层张力水 WU</span><b :title="String(selected.wu ?? '')">{{ formatGridValue(selected.wu) }} mm</b></div>
            <div class="stat-line"><span>下层张力水 WL</span><b :title="String(selected.wl ?? '')">{{ formatGridValue(selected.wl) }} mm</b></div>
            <div class="stat-line"><span>深层张力水 WD</span><b :title="String(selected.wd ?? '')">{{ formatGridValue(selected.wd) }} mm</b></div>
            <div class="stat-line"><span>张力水 W/WM</span><b :title="String(selected.soil ?? '')">{{ formatGridValue(selected.soil, 3) }}</b></div>
            <div class="stat-line"><span>自由水 S</span><b :title="String(selected.freeWater ?? '')">{{ formatGridValue(selected.freeWater, 3) }} mm</b></div>
            <div v-if="selected.runoff != null" class="stat-line"><span>当前小时产流深 R</span><b class="hot">{{ formatNumber(selected.runoff) }} mm</b></div>
            <div v-if="selected.water_balance_error != null" class="stat-line"><span>小时水量闭合误差</span><b>{{ Number(selected.water_balance_error).toExponential(2) }} mm</b></div>
          </div>
          <template v-if="app.isColdRegion">
            <div class="form-group-title">冰雪冻土状态</div>
            <div class="snow-state-grid">
              <div><span>积雪水当量 SWE</span><b>{{ selected.snowWater }} mm</b></div>
              <div><span>积雪深度</span><b>{{ selected.snowDepth }} cm</b></div>
              <div><span>雪层液态水</span><b>{{ selected.snowLiquid }} mm</b></div>
              <div><span>冻土冻结程度</span><b>{{ selected.frozenIndex }}%</b></div>
              <div><span>冰川消融量</span><b>{{ selected.glacierMelt }} mm/h</b></div>
            </div>
          </template>
          <template v-if="isModelState">
            <div class="trend-head"><span>逐小时网格过程</span></div>
            <div v-if="replaySeries" class="mini-chart"><BaseChart :option="trendOption" /></div>
            <p v-else class="hint">{{ replaySeriesError || '点击网格读取已保存的过程；旧作业可能只有末状态。' }}</p>
          </template>
          <template v-else><div class="trend-head"><span>{{ currentVar.name }}变化过程</span><b>{{ currentVar.short }} · 0–{{ forecast.leadHours }} h</b></div>
          <div class="mini-chart"><BaseChart :option="trendOption" /></div></template>
        </template>
        <div v-else class="empty-state">
          <strong>请选择计算网格</strong>
          <span>{{ isModelState ? '网格状态与逐时过程' : '演示网格过程' }}</span>
        </div>
      </aside>
    </div>
    </template>
    <section v-else class="panel state-workspace-empty">
      <strong>暂无状态空间场</strong>
      <span>WU · WL · WD · 自由水蓄量</span>
    </section>
  </section>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { useForecastStore } from '@/stores/forecast'
import { useStateStore } from '@/stores/state'
import { useCalibrationStore } from '@/stores/calibration'
import { useAppStore } from '@/stores/app'
import { useTasksStore } from '@/stores/tasks'
import { loadSelincoGridCells, selincoStations } from '@/data/selinco'
import BasinMap from '@/components/map/BasinMap.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import { caseExecutionAvailability, projectDataForContext } from '@/services/caseAvailabilityService'
import TaskStatusPanel from '@/components/tasks/TaskStatusPanel.vue'
import ContextStatusMetric from '@/components/common/ContextStatusMetric.vue'
import { ElMessage } from 'element-plus'
import { loadGridHistoryCatalog, loadGridStateFrame, loadGridStateSeries } from '@/services/taskDiagnosticService'
import { frozenInputBundleVersionId } from '@/services/inputProductService'
import StateLibrary from '@/components/model/StateLibrary.vue'
import { modelApi } from '@/services/modelBackendService'
import { scientificConditionLabel, stateScienceBlockReason } from '@/services/scientificSelectionService'

const forecast = useForecastStore()
const state = useStateStore()
const calibration = useCalibrationStore()
const app = useAppStore()
const tasks = useTasksStore()
const router = useRouter()
const isModelState = computed(() => state.activeInitialState.resultSource === 'model-backend')
const isModelWorkspace = computed(() => isModelState.value || (app.usesModelBackend && app.basinId !== 'selinco'))
const replayCatalog = ref(null)
const replayFrame = ref(null)
const replaySeries = ref(null)
const replayTime = ref('')
const replayError = ref('')
const replaySeriesError = ref('')
const replayLoading = ref(false)
let replayRequest = 0
let frameRequest = 0
let seriesRequest = 0
const selectedHour = ref(0)
const selectedId = ref(null)
const activeGroup = ref(app.isColdRegion ? 'snow' : 'water')
const selincoGridCells = ref([])
const stateTaskMode = ref('continuation')
const initializationStrategy = ref('FIXED_WARMUP')
const hasContinuousCandidate = computed(() => calibration.contextKey === state.contextKey && Boolean(calibration.scienceVersionId)
  && calibration.statePolicy === 'continuous-origin' && Boolean(calibration.continuousOrigin))
const initializationStrategies = computed(() => [
  ...(hasContinuousCandidate.value ? [{ id: 'CONTINUOUS', label: '从原连续起点补算' }] : []),
  ...(app.backendCase?.initializationStrategies || []).filter(row => row.id !== 'CONTINUOUS')
])
watch(() => [hasContinuousCandidate.value, calibration.scienceVersionId], ([continuous]) => {
  if (continuous) initializationStrategy.value = 'CONTINUOUS'
  else if (initializationStrategy.value === 'CONTINUOUS') initializationStrategy.value = 'FIXED_WARMUP'
}, { immediate: true })
const stateScientificLabel = computed(() => stateTaskMode.value === 'continuation'
  ? scientificConditionLabel(state.activeInitialState)
  : initializationStrategy.value === 'CONTINUOUS' || (calibration.scienceVersionId && calibration.statePolicy !== 'continuous-origin')
    ? '沿用所选方案的蒸散发与初态配置' : isModelWorkspace.value ? '逐月给定蒸散发与独立初始化' : '演示配置')
const stateWarmupDays = ref(7)
const stateTargetTime = ref(String(app.workContext.issueTime || app.dataTime).replace(' ', 'T'))
const targetTimeInput = ref(null)
const continuationStartTime = computed(() => {
  const time = dayjs(String(state.activeInitialState.time).replace(' ', 'T'))
  return state.domainInitialStates.length && time.isValid() ? time.add(1, 'hour').format('YYYY-MM-DDTHH:mm') : undefined
})
const stateParameterVersion = computed(() => {
  if (!app.backendCase) return app.paramVersion
  if (stateTaskMode.value === 'continuation') return state.activeInitialState.parameterVersionId || '未选择真实状态'
  return calibration.contextKey === state.contextKey && calibration.backendContractVersion
    ? calibration.paramVersion : app.backendCase.parameterVersionId
})
let gridRequest = 0
const activeTask = computed(() => state.activeTaskId ? tasks.taskById(state.activeTaskId) : null)
const caseData = computed(() => projectDataForContext(app.activeContext, app.projectData, { loading: app.projectDataLoading, error: app.projectDataError }))
const stateAvailability = computed(() => caseExecutionAvailability(app.activeContext, app.projectData, stateTaskMode.value === 'initialization' ? 'stateInitialization' : 'stateContinuation', { loading: app.projectDataLoading, error: app.projectDataError }))
const stateBlockReason = computed(() => {
  if (!stateAvailability.value.ready) return stateAvailability.value.reason
  if (isModelWorkspace.value && (app.inputCatalogError || app.inputCatalogLoading)) return app.inputCatalogError || '正在校验选定输入版本。'
  if (isModelWorkspace.value && stateTaskMode.value === 'initialization' && initializationStrategy.value !== 'CONTINUOUS' && (!Number.isInteger(stateWarmupDays.value) || stateWarmupDays.value < 1 || stateWarmupDays.value > 365)) return '预热期为 1–365 天。'
  if (stateTaskMode.value !== 'initialization' && !state.domainInitialStates.length) return '当前案例没有可续算的状态版本。'
  if (isModelState.value && stateTaskMode.value !== 'initialization' && frozenInputBundleVersionId(state.activeInitialState.checkpoint || state.activeInitialState) !== app.selectedInputBundleVersionId) return '父状态使用另一输入版本，请选择相同版本。'
  if (isModelState.value && stateTaskMode.value === 'continuation') {
    const checkpoint = state.activeInitialState.checkpoint
    if (stateScienceBlockReason(state.activeInitialState, app.backendCase?.modelImplementationSHA256)) return stateScienceBlockReason(state.activeInitialState, app.backendCase?.modelImplementationSHA256)
    if ((checkpoint?.routingVersionId || checkpoint?.lineage?.routingVersionId || 'control-split-v2') !== app.selectedRoutingVersionId) return '父状态使用另一汇流版本，请选择相同版本。'
    if (!dayjs(stateTargetTime.value).isValid() || dayjs(stateTargetTime.value).isBefore(dayjs(continuationStartTime.value))) return '续算终点应晚于当前状态时刻。'
  }
  return ''
})
const stateCanRun = computed(() => !state.taskRunning && !stateBlockReason.value)
watch(() => app.backendCase, (backend) => {
  if (backend && !state.domainInitialStates.length) {
    stateTaskMode.value = 'initialization'
    stateTargetTime.value = backend.defaultSimulationPeriod.start
  }
}, { immediate: true })
const activeStations = computed(() => {
  if (!caseData.value) return []
  if (app.hasLakeBalance) return selincoStations
  const rows = [
    ...(caseData.value?.stations?.stations || []),
    ...(caseData.value?.sections?.sections || [])
  ]
  return [...new Map(rows.map((row) => [row.id, row])).values()]
})

watch(() => [app.basinId, app.modelScale, app.modelConfigId], ([domainId, modelScale]) => {
  selectedId.value = null
  selectedHour.value = 0
  stateTargetTime.value = String(app.workContext.issueTime || '').replace(' ', 'T')
  forecast.setContext(domainId, modelScale, app.modelConfigId)
  state.setContext(domainId, modelScale, app.modelConfigId)
  refreshSelincoGrid(domainId, modelScale)
  if (domainId === 'selinco') {
    activeGroup.value = 'snow'
    if (!['swe', 'snowDepth', 'snowLiquid', 'frost', 'glacierMelt'].includes(state.activeVar)) state.setActiveVar('swe')
  } else {
    activeGroup.value = 'water'
    if (!['wu', 'wl', 'wd', 'soil', 'free', 'runoff'].includes(state.activeVar)) state.setActiveVar('soil')
  }
}, { immediate: true })

watch(() => app.workContext, (workContext) => {
  const handoff = state.prepareFromWorkContext(workContext)
  if (handoff?.issueTime) stateTargetTime.value = handoff.issueTime.replace(' ', 'T')
}, { immediate: true, deep: true })
watch(() => [app.workContext.handoff?.targetPage, app.workContext.stateVersionId, state.domainInitialStates.length], ([target, id]) => {
  if (target === 'state' && id && state.adoptedVersion !== id && state.domainInitialStates.some(row => row.id === id)) loadNode(id)
}, { immediate: true })

watch([() => state.activeInitialState.id, stateTaskMode], () => {
  if (isModelState.value && stateTaskMode.value === 'continuation' && continuationStartTime.value) {
    stateTargetTime.value = continuationStartTime.value
  }
}, { immediate: true })

async function refreshSelincoGrid(domainId, modelScale) {
  const request = ++gridRequest
  if (domainId !== 'selinco') {
    selincoGridCells.value = []
    return
  }
  const cells = await loadSelincoGridCells(modelScale)
  if (request === gridRequest) {
    selincoGridCells.value = cells
    if (!selectedId.value && cells.length) selectedId.value = cells[Math.floor(cells.length * 0.56)].id
  }
}

const variables = [
  { key: 'wu', group: 'water', short: 'WU', name: '上层张力水', unit: 'mm', field: 'wu', legend: 'soil', base: 18, desc: '对近期降雨响应最快，反映表层土壤湿润变化。' },
  { key: 'wl', group: 'water', short: 'WL', name: '下层张力水', unit: 'mm', field: 'wl', legend: 'soil', base: 42, desc: '反映中层土壤蓄水，是中期产流的重要初始条件。' },
  { key: 'wd', group: 'water', short: 'WD', name: '深层张力水', unit: 'mm', field: 'wd', legend: 'soil', base: 55, desc: '变化较慢，表征深层土壤蓄水状态。' },
  { key: 'soil', group: 'water', short: 'W/WM', name: '张力水相对蓄量', unit: '', field: 'soil', legend: 'soil', base: 0.61, desc: '流域张力水相对饱和度，数值越高越容易产流。' },
  { key: 'free', group: 'water', short: 'S', name: '自由水蓄量', unit: 'mm', field: 'freeWater', legend: 'runoff', base: 32, desc: '影响地表、壤中和地下径流的水源划分。' },
  { key: 'runoff', group: 'water', short: 'R', name: '时段产流深', unit: 'mm', field: 'runoff', legend: 'runoff', base: 2.1, desc: '当前预报时段的产流结果，不属于初始状态量。' },
  { key: 'swe', group: 'snow', short: 'SWE', name: '积雪水当量', unit: 'mm', field: 'snowWater', legend: 'snow', base: 38.6, desc: '网格积雪库的水量状态，是后续融雪补给的水源。' },
  { key: 'snowDepth', group: 'snow', short: 'SD', name: '积雪深度', unit: 'cm', field: 'snowDepth', legend: 'snow', base: 24.1, desc: '由积雪水当量和雪密度换算的网格雪深。' },
  { key: 'snowLiquid', group: 'snow', short: 'LW', name: '雪层液态水', unit: 'mm', field: 'snowLiquid', legend: 'snow', base: 2.8, desc: '积雪层暂存的液态水，超过持水能力后形成融雪出流。' },
  { key: 'frost', group: 'snow', short: 'FR', name: '冻土冻结程度', unit: '%', field: 'frozenIndex', legend: 'frost', base: 68, desc: '反映冻结土层对下渗和蓄水容量的限制程度。' },
  { key: 'glacierMelt', group: 'snow', short: 'GM', name: '冰川消融量', unit: 'mm/h', field: 'glacierMelt', legend: 'melt', base: 0.18, desc: '冰川覆盖网格在当前小时形成的消融水量。' }
]

const currentVar = computed(() => variables.find((v) => v.key === state.activeVar) || variables[3])
const visibleVariables = computed(() => variables.filter((item) => item.group === activeGroup.value && (!isModelState.value || replayCatalog.value || item.key !== 'runoff')))
const quickHours = computed(() => [...new Set([0, 6, 12, 24, 48, forecast.leadHours].filter((h) => h <= forecast.leadHours))])
const start = computed(() => dayjs(forecast.startTime))
const displayTime = computed(() => isModelState.value ? String(replayFrame.value?.time || state.activeInitialState.time).replace('T', ' ') : start.value.add(selectedHour.value, 'hour').format('YYYY-MM-DD HH:mm'))
const startInput = computed(() => start.value.format('YYYY-MM-DDTHH:mm'))
const endInput = computed(() => start.value.add(forecast.leadHours, 'hour').format('YYYY-MM-DDTHH:mm'))
const displayInput = computed(() => displayTime.value.replace(' ', 'T'))
const progress = computed(() => selectedHour.value / Math.max(1, forecast.leadHours))
const baseMean = computed(() => isModelState.value ? state.activeInitialState.means[currentVar.value.key] ?? null : currentVar.value.key === 'soil' ? forecast.wetness : Number(state.activeInitialState.means[currentVar.value.key] ?? currentVar.value.base))
const currentMean = computed(() => {
  if (!isModelState.value) return evolveValue(baseMean.value, progress.value, currentVar.value.key)
  if (!replayFrame.value) return baseMean.value
  const values = liveCells.value.map((cell) => cell[currentVar.value.field === 'soil' ? currentVar.value.key : currentVar.value.field]).filter((value) => value != null && Number.isFinite(Number(value)))
  return values.length ? values.reduce((sum, value) => sum + Number(value), 0) / values.length : null
})
const deltaValue = computed(() => currentMean.value - baseMean.value)
const displayMean = computed(() => formatNumber(currentMean.value))
const stateUnit = computed(() => currentVar.value.unit || '相对饱和度')
const deltaText = computed(() => `${deltaValue.value >= 0 ? '+' : ''}${formatNumber(deltaValue.value)} ${currentVar.value.unit}`.trim())

const layers = { rain: false, hydro: false, water: false, section: true, river: true, subbasin: true, rainField: false, dem: true, grid: true }

const liveCells = computed(() => {
  if (isModelState.value) return replayFrame.value?.cells || state.cells
  if (!state.domainInitialStates.length || !stateAvailability.value.ready || !app.hasLakeBalance) return []
  const baseCells = selincoGridCells.value
  const cells = state.applyMods(baseCells, forecast.effectiveParams)
  const factor = 1 + Math.sin(progress.value * Math.PI) * (0.18 + forecast.wetness * 0.08)
  return cells.map((c) => ({
    ...c,
    soil: +Math.min(0.98, c.soil * (1 + (factor - 1) * 0.45)).toFixed(2),
    freeWater: +(c.freeWater * factor).toFixed(1),
    runoff: +(c.runoff * (0.8 + factor * 0.55)).toFixed(2),
    surface: Math.round(c.surface * (0.75 + factor * 0.6)),
    wu: +(c.wu * (1 + (factor - 1) * 0.7)).toFixed(1),
    wl: +(c.wl * (1 + (factor - 1) * 0.4)).toFixed(1),
    wd: +(c.wd * (1 + (factor - 1) * 0.18)).toFixed(1),
    ...snowCellState(c, progress.value)
  }))
})

const selected = computed(() => liveCells.value.find((c) => c.id === selectedId.value) || null)

const trendOption = computed(() => {
  if (isModelState.value) {
    const fields = { wu: 'WU', wl: 'WL', wd: 'WD', free: 'S' }
    const history = replaySeries.value
    const series = history?.series || {}
    const data = currentVar.value.key === 'soil'
      ? (history?.times || []).map((_, index) => (series.WU[index] + series.WL[index] + series.WD[index]) / state.activeInitialState.parameters.WM)
      : currentVar.value.key === 'runoff'
        ? (history?.times || []).map((_, index) => series.RS[index] + series.RI[index] + series.RG[index])
        : series[fields[currentVar.value.key]] || []
    return { grid: { left: 44, right: 12, top: 24, bottom: 42 }, tooltip: { trigger: 'axis', confine: true },
      xAxis: { type: 'category', data: (history?.times || []).map((time) => time.replace('T', ' ')), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', scale: true }, series: [{ type: 'line', data, smooth: false, connectNulls: false, symbol: 'none', lineStyle: { color: '#0b57c7', width: 2 } }] }
  }
  const cell = selected.value
  const key = currentVar.value.key
  const hours = Array.from({ length: Math.floor(forecast.leadHours / 3) + 1 }, (_, i) => i * 3)
  const base = cell ? cellValue(cell, key) : currentMean.value
  const data = hours.map((h) => evolveValue(base, h / Math.max(1, forecast.leadHours), key))
  const labelInterval = hours.length > 17 ? 5 : hours.length > 9 ? 2 : 1
  return {
    grid: { left: 44, right: 12, top: 24, bottom: 38 },
    tooltip: { trigger: 'axis', confine: true, appendToBody: true },
    xAxis: { type: 'category', data: hours.map((h) => `+${h}h`), axisLabel: { fontSize: 10, interval: labelInterval } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#e8f0fa' } } },
    series: [{ type: 'line', data, smooth: false, symbol: 'none', lineStyle: { width: 2.2, color: currentVar.value.group === 'snow' ? '#0072B2' : '#0b57c7' }, markLine: { symbol: 'none', data: [{ xAxis: `+${selectedHour.value}h`, label: { formatter: '当前时刻' } }] } }]
  }
})

function evolveValue(base, p, key) {
  if (key === 'swe' || key === 'snowDepth') return +(base * (1 - p * 0.34 + Math.sin(p * Math.PI) * 0.08)).toFixed(1)
  if (key === 'snowLiquid') return +(base * (0.8 + Math.sin(p * Math.PI) * 1.1)).toFixed(1)
  if (key === 'frost') return +(Math.max(18, base * (1 - p * 0.28))).toFixed(1)
  if (key === 'glacierMelt') return +(base * (0.55 + Math.sin(p * Math.PI) * 1.8)).toFixed(2)
  const amp = key === 'runoff' ? 0.95 : key === 'soil' ? 0.22 : key === 'free' ? 0.42 : 0.28
  return +(base * (1 + Math.sin(Math.min(1, p) * Math.PI) * amp - Math.max(0, p - 0.7) * amp * 0.8)).toFixed(key === 'soil' || key === 'runoff' ? 2 : 1)
}
function cellValue(cell, key) {
  const map = { free: 'freeWater', swe: 'snowWater', frost: 'frozenIndex' }
  return Number(cell[map[key] || key] ?? currentMean.value)
}
function formatNumber(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return Number(value).toFixed(['soil', 'runoff', 'glacierMelt'].includes(currentVar.value.key) ? 2 : 1)
}
function formatGridValue(value, digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return Number(value).toFixed(digits)
}
function formatValue(value) {
  return `${formatNumber(value)}${currentVar.value.unit ? ` ${currentVar.value.unit}` : ''}`
}
function onTimeInput(event) {
  const hour = dayjs(event.target.value).diff(start.value, 'hour', true)
  selectedHour.value = Math.max(0, Math.min(forecast.leadHours, Math.round(hour)))
}
function onGrid(id) {
  selectedId.value = id
}

async function selectImportedState(id) {
  await refreshStates()
  loadNode(id)
}
watch(() => [state.activeInitialState.sourceTaskId, state.contextKey, isModelState.value], async ([taskId, key, real]) => {
  const request = ++replayRequest
  ++frameRequest
  ++seriesRequest
  replayCatalog.value = null
  replayFrame.value = null
  replaySeries.value = null
  replayTime.value = ''
  replayError.value = ''
  replaySeriesError.value = ''
  replayLoading.value = false
  if (!real || !taskId || state.activeInitialState.checkpoint?.importProvenance) return
  try {
    const catalog = await loadGridHistoryCatalog(taskId)
    if (request !== replayRequest || state.contextKey !== key) return
    if (catalog.context.contextKey !== key) throw new Error('网格回放与当前案例不一致。')
    replayCatalog.value = catalog
    replayTime.value = catalog.times.at(-1) || ''
    selectedId.value = catalog.gridIds[0] || null
  } catch (error) {
    if (request === replayRequest) replayError.value = error.message
  }
}, { immediate: true })
watch(replayTime, async (time) => {
  const request = ++frameRequest
  replayFrame.value = null
  if (!time || !replayCatalog.value) return
  replayLoading.value = true
  try {
    const frame = await loadGridStateFrame(replayCatalog.value.taskId, time)
    if (request === frameRequest && frame.context.contextKey === state.contextKey) replayFrame.value = frame
  } catch (error) {
    if (request === frameRequest) replayError.value = error.message
  } finally { if (request === frameRequest) replayLoading.value = false }
})
watch([selectedId, replayCatalog], async ([id, catalog]) => {
  const request = ++seriesRequest
  replaySeries.value = null
  replaySeriesError.value = ''
  if (!id || !catalog || !isModelState.value) return
  try {
    const series = await loadGridStateSeries(catalog.taskId, id)
    if (request === seriesRequest && series.context.contextKey === state.contextKey) replaySeries.value = series
  } catch (error) { if (request === seriesRequest) replaySeriesError.value = error.message }
})

function selectGroup(group) {
  activeGroup.value = group
  const first = variables.find((item) => item.group === group)
  if (first) state.setActiveVar(first.key)
}

function snowCellState(cell, p) {
  const spatial = Math.max(0.35, Math.min(1.25, 0.72 + (cell.lat - 31.4) * 0.16 + Math.sin(cell.lng * 1.9) * 0.12))
  const snowWater = Math.max(0, 52 * spatial * (1 - p * 0.38 + Math.sin(p * Math.PI) * 0.07))
  const density = 0.22 + p * 0.09
  const glacierFactor = Math.max(0, Math.min(1, (cell.lat - 32.3) * 1.4 + Math.sin((cell.lng - 87.5) * 1.7) * 0.35))
  return {
    snowWater: +snowWater.toFixed(1),
    snowDepth: +(snowWater / Math.max(0.18, density) / 10).toFixed(1),
    snowLiquid: +(snowWater * (0.035 + Math.sin(p * Math.PI) * 0.055)).toFixed(1),
    frozenIndex: +Math.max(15, 82 * spatial * (1 - p * 0.26)).toFixed(1),
    glacierMelt: +(glacierFactor * (0.08 + Math.sin(p * Math.PI) * 0.72)).toFixed(2)
  }
}

function loadNode(id) {
  const node = state.loadInitialState(id)
  if (!node) return
  forecast.setStateVersion(node.id, 'initial-state')
  forecast.setWetness(node.means.soil, 'initial-state')
  selectedHour.value = 0
}

async function prepareContinuation() {
  const bundle = frozenInputBundleVersionId(state.activeInitialState.checkpoint || state.activeInitialState)
  if (isModelState.value && bundle !== app.selectedInputBundleVersionId) {
    try { await app.selectInputBundleVersion(bundle) }
    catch (failure) { ElMessage.error(`无法采用该检查点的原始计算资料：${failure.message}`); return }
  }
  stateTaskMode.value = 'continuation'
  if (!dayjs(stateTargetTime.value).isValid() || dayjs(stateTargetTime.value).isBefore(dayjs(continuationStartTime.value))) {
    stateTargetTime.value = continuationStartTime.value
  }
  await nextTick()
  targetTimeInput.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  targetTimeInput.value?.focus({ preventScroll: true })
}

async function refreshStates() {
  const context = app.contextKey
  try {
    const response = await modelApi('states')
    if (app.contextKey === context) state.hydrateBackendStates(response.states)
  } catch (failure) { ElMessage.error(failure.message) }
}

function continueForecast() {
  if (!state.domainInitialStates.length) return
  loadNode(state.activeInitialState.id)
  app.prepareHandoff('forecast', {
    sourcePage: 'state',
    sourceLabel: '起算状态',
    issueTime: state.activeInitialState.time,
    stateVersionId: state.activeInitialState.id,
    parameterVersionId: state.activeInitialState.parameterVersionId || app.paramVersion,
    sourceTaskId: state.activeInitialState.sourceTaskId
  })
  router.push('/forecast')
}

async function runStateTask() {
  if (!stateCanRun.value) {
    ElMessage.warning(stateBlockReason.value)
    return
  }
  const result = await state.runStateTask(stateTaskMode.value, stateTargetTime.value.replace('T', ' '), { strategy: initializationStrategy.value, warmupDays: stateWarmupDays.value })
  if (!result.ok) {
    ElMessage.warning(result.error)
    return
  }
  forecast.setStateVersion(result.state.id, 'state-task')
  app.setStateVersion(result.state.id)
  if (stateTaskMode.value === 'continuation') stateTargetTime.value = dayjs(result.state.time.replace(' ', 'T')).add(1, 'hour').format('YYYY-MM-DDTHH:mm')
  ElMessage.success('状态已生成')
}

watch(() => forecast.leadHours, (hours) => {
  if (selectedHour.value > hours) selectedHour.value = hours
})
</script>

<style scoped>
.state-input-details{grid-column:1/-1;font-size:11px;color:#71857a}.state-input-details summary{cursor:pointer;margin:8px 0}.state-task-panel .state-task-form{display:flex;flex-wrap:wrap;align-items:end;gap:14px}.state-task-form .compact-field{min-width:130px;flex:1}.state-task-panel{box-shadow:none;border-color:#dce4de}.state-task-panel.has-task{grid-template-columns:1fr}.state-task-panel .state-task-form>div:first-child{min-width:220px}
.state-source-note {
  margin: 0 0 8px;
  color: #31506f;
  font-size: 12px;
}
.view-state { display: grid; grid-template-rows: auto; gap: 10px; height: auto; min-height: 100%; }
.state-task-panel { display: grid; grid-template-columns: minmax(0, 1fr); width: 100%; gap: 10px; align-items: center; }
.state-task-panel.has-task { grid-template-columns: minmax(0, 1fr); width: 100%; }
.state-task-form { display: grid; grid-template-columns: minmax(260px, 1fr) 210px auto; gap: 10px; align-items: end; }
.task-label { display: block; margin-bottom: 5px; color: #657991; font-size: 10px; font-weight: 800; }
.mode-switch { display: flex; gap: 5px; }
.mode-switch button { min-height: 30px; padding: 0 12px; border: 1px solid #cad8e4; border-radius: 5px; background: #fff; color: #425b70; cursor: pointer; font-size: 11px; font-weight: 800; }
.mode-switch button.active { border-color: #247b72; background: #eaf6f3; color: #17635c; }
.compact-field { margin: 0; }
.state-block-note { grid-column: 1 / -1; margin: 0; color: #9a5a2b; font-size: 11px; }
.state-input-status { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #d8e3e8; }
.state-input-status div { display: grid; align-content: start; min-width: 0; gap: 3px; padding: 8px 10px; border-left: 1px solid #d8e3e8; }
.state-input-status div:first-child { border-left: 0; }
.state-input-status span { color: #6d818d; font-size: 10px; }
.state-input-status b { min-width: 0; overflow-wrap: anywhere; color: #294753; font-size: 11px; line-height: 1.4; }
.state-input-status .ok { color: #21734f; }
.state-input-status .warn { color: #93601f; }
.state-input-status .bad { color: #a34f2d; }
.state-empty-inline { grid-area: means; padding: 12px; border: 1px dashed #cbd9df; background: #f7fafb; color: #667c87; font-size: 11px; }
.state-workspace-empty { display: grid; min-height: 300px; place-content: center; gap: 8px; padding: 32px; color: #667c87; text-align: center; }
.state-workspace-empty strong { color: #294753; font-size: 17px; }
.state-workspace-empty span { max-width: 540px; font-size: 12px; line-height: 1.6; }
.node-workbench {
  display: grid;
  grid-template-columns: minmax(220px, 270px) minmax(0, 1fr) auto;
  grid-template-areas:
    'heading list action'
    'heading means action';
  align-items: center;
  gap: 8px 12px;
  padding: 10px 12px;
}
.node-heading { display: grid; grid-area: heading; gap: 3px; }
.node-heading strong { color: #173052; font-size: 14px; }
.node-heading span { color: #657991; font-size: 11px; line-height: 1.4; }
.node-list { display: grid; grid-area: list; grid-template-columns: repeat(auto-fit, minmax(164px, 1fr)); gap: 6px; min-width: 0; max-height: 160px; overflow-y: auto; }
.node-item { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 8px; min-height: 48px; padding: 7px 9px; border: 1px solid #cbd9eb; border-radius: 5px; background: #fff; color: #173052; text-align: left; cursor: pointer; }
.node-item > span { display: grid; gap: 2px; min-width: 0; }
.node-item b { font-size: 12px; font-variant-numeric: tabular-nums; }
.node-item small { overflow: hidden; color: #6a7c95; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.node-item em { color: var(--green); font-size: 10px; font-style: normal; font-weight: 800; white-space: nowrap; }
.node-item.active { border-color: var(--teal); background: #edf8f5; box-shadow: inset 3px 0 0 var(--teal); }
.node-means { display: grid; grid-area: means; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid #d4e0e7; border-radius: 5px; overflow: hidden; }
.node-means.node-means-snow { grid-template-columns: repeat(4, minmax(72px, 1fr)); }
.node-means div { display: grid; gap: 2px; padding: 7px 8px; border-right: 1px solid #e0e8ec; }
.node-means div:last-child { border-right: 0; }
.node-means span { color: #6a7c95; font-size: 10px; }
.node-means b { color: #173052; font-size: 11px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.use-node { grid-area: action; min-height: 40px; white-space: nowrap; }
.time-toolbar { display: grid; grid-template-columns: minmax(210px, 1fr) auto 190px; align-items: center; gap: 16px; padding: 8px 12px; overflow: visible; }
.time-title { display: grid; gap: 2px; min-width: 0; }
.time-title strong { color: #173052; font-size: 13px; }
.time-title span { color: #6a7c95; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.time-tabs { display: flex; align-items: center; padding: 3px; border: 1px solid #cbd9eb; border-radius: 6px; background: #f2f6fb; }
.time-tabs button { min-width: 52px; height: 30px; padding: 0 10px; border: 0; border-radius: 4px; background: transparent; color: #50627c; font-size: 12px; font-weight: 700; cursor: pointer; }
.time-tabs button.active { background: #0b57c7; color: #fff; box-shadow: 0 2px 6px rgba(11, 87, 199, 0.24); }
.time-input { height: 34px; }
.body { display: grid; grid-template-columns: minmax(248px, 270px) minmax(560px, 1fr) minmax(332px, 360px); gap: 10px; min-height: 0; }
.left, .right { min-height: 0; overflow: auto; }
.left { display: flex; flex-direction: column; }
.left .stat-line { display: grid; grid-template-columns: 70px minmax(0, 1fr); align-items: start; gap: 6px; }
.left .stat-line > b { min-width: 0; overflow-wrap: anywhere; text-align: right; }
.state-family { display: grid; grid-template-columns: 1fr 1fr; gap: 3px; margin-bottom: 8px; padding: 3px; border: 1px solid #cbd9eb; border-radius: 6px; background: #f2f6f8; }
.state-family button { min-height: 32px; border: 0; border-radius: 4px; background: transparent; color: #52677d; font-size: 12px; font-weight: 800; cursor: pointer; }
.state-family button.active { background: #fff; color: #126a64; box-shadow: 0 1px 3px rgba(26, 61, 74, 0.12); }
.variable-tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.variable-tabs button { display: grid; gap: 3px; min-height: 52px; padding: 7px 9px; border: 1px solid #d4e0ef; border-radius: 6px; background: #fff; color: #61728a; text-align: left; cursor: pointer; }
.variable-tabs button b { color: #173052; font-size: 12px; }
.variable-tabs button span { color: #647b91; font-size: 10px; font-weight: 700; }
.variable-tabs button.active { border-color: #0b57c7; background: #edf5ff; box-shadow: inset 3px 0 0 #0b57c7; }
.variable-desc { min-height: 46px; margin: 10px 0; color: #5a6d88; font-size: 11px; line-height: 1.5; }
.state-value { display: flex; align-items: baseline; gap: 6px; padding: 8px 0 10px; }
.state-value strong { color: #f26c18; font-size: 25px; }
.state-value span { color: #6a7c95; font-size: 11px; }
.left .btn { margin-top: auto; }
.stat-box.compact { gap: 5px; padding: 8px 10px; margin-bottom: 12px; }
.map-panel { display: grid; grid-template-rows: 48px minmax(440px, 1fr); min-height: 490px; }
.map-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 0 14px; border-bottom: 1px solid var(--line); background: linear-gradient(180deg, #fff, #f8fbff); }
.map-head > div { display: grid; gap: 2px; }
.map-head strong { color: #173052; font-size: 13px; }
.map-head div span, .map-sub { color: #6a7c95; font-size: 11px; }
.map-body { min-height: 0; }
.cell-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; color: #63758d; font-size: 12px; }
.cell-head strong { color: #0b57c7; }
.snow-state-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; margin-bottom: 6px; border: 1px solid #d8e6eb; background: #d8e6eb; }
.snow-state-grid div { display: grid; gap: 2px; min-width: 0; padding: 6px 7px; background: #fff; }
.snow-state-grid div:last-child { grid-column: 1 / -1; }
.snow-state-grid span { color: #617783; font-size: 10px; }
.snow-state-grid b { color: #17435a; font-size: 11px; font-variant-numeric: tabular-nums; }
.trend-head { display: flex; justify-content: space-between; margin: 8px 0 4px; color: #42546f; font-size: 12px; font-weight: 700; }
.trend-head b { color: #0b57c7; }
.mini-chart { height: 148px; }
.empty-state { display: grid; place-content: center; gap: 8px; height: calc(100% - 42px); color: #7a8ba3; text-align: center; line-height: 1.5; }
.empty-state strong { color: #42546f; font-size: 14px; }
.empty-state span { max-width: 210px; font-size: 12px; }
.ok { color: var(--green) !important; } .warn { color: var(--orange) !important; } .hot { color: var(--orange) !important; }

@media (max-width: 1400px) {
  .view-state { height: auto; min-height: 100%; grid-template-rows: auto; }
  .body {
    grid-template-columns: minmax(248px, 270px) minmax(0, 1fr);
    grid-template-areas:
      'left map'
      'left right';
    align-items: start;
  }
  .left { grid-area: left; align-self: stretch; overflow: visible; }
  .map-panel { grid-area: map; min-height: 520px; }
  .right { grid-area: right; overflow: visible; }
  .mini-chart { height: 170px; }
}

@media (max-width: 1200px) {
  .state-task-panel { grid-template-columns: 1fr; }
  .state-task-form { grid-template-columns: minmax(220px, 1fr) 210px auto; }
  .node-workbench {
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      'heading action'
      'list list'
      'means means';
  }
  .time-toolbar { grid-template-columns: 1fr; }
  .body {
    grid-template-columns: minmax(238px, 260px) minmax(0, 1fr);
    grid-template-areas:
      'left map'
      'right right';
  }
  .left { grid-area: left; }
  .map-panel { grid-area: map; min-height: 520px; }
  .right { grid-area: right; }
  .left, .right { overflow: visible; }
}

@media (max-width: 760px) {
  .metric-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-strip > :deep(.metric:last-child) { grid-column: 1 / -1; }
  .state-task-form { grid-template-columns: 1fr; }
  .node-workbench {
    grid-template-columns: 1fr;
    grid-template-areas:
      'heading'
      'list'
      'means'
      'action';
  }
  .node-list { grid-template-columns: 1fr; }
  .node-means,
  .node-means.node-means-snow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .node-means div { border-bottom: 1px solid #e0e8ec; }
  .use-node { width: 100%; }
  .body {
    grid-template-columns: 1fr;
    grid-template-areas:
      'left'
      'map'
      'right';
  }
  .left, .map-panel, .right { grid-column: auto; }
  .time-tabs { overflow-x: auto; }
  .map-panel { min-height: 480px; }
}
</style>
