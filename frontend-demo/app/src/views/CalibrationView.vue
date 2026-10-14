<template>
  <section class="calibration-page">
    <ModelSchemeBar ref="schemeBar" @opened="schemeOpened = $event" @loaded="restoreSchemeView" />
    <template v-if="schemeOpened">
      <header class="workbench-toolbar">
        <div class="period-switch" aria-label="率定时段">
          <button :class="{ active: cal.calibrationMode === 'event' }" :disabled="cal.running" @click="cal.setCalibrationMode('event')">洪水场次</button>
          <button :class="{ active: cal.calibrationMode === 'continuous' }" :disabled="cal.running" @click="cal.setCalibrationMode('continuous')">连续时段</button>
        </div>
        <div class="work-switch" aria-label="调参方式">
          <button :class="{ active: trialMode === 'automatic' }" :disabled="cal.running" @click="trialMode = 'automatic'">自动率定</button>
          <button :class="{ active: trialMode === 'manual' }" :disabled="cal.running" @click="trialMode = 'manual'">人工试调</button>
        </div>
        <button class="btn" :disabled="cal.running" @click="settings.showModal()">率定设置</button>
        <button v-if="cal.running" class="btn" @click="cal.cancelCalibration()">停止</button>
        <button v-else-if="cal.activeOperationId" class="btn primary" @click="reconnectOperation">查看计算</button>
        <button v-else-if="trialMode === 'automatic'" class="btn primary" :disabled="!canRun" @click="startCalculation">开始率定</button>
      </header>
      <p v-if="!cal.running && calculationBlockReason && !calculationError" class="field-error" role="status">{{ calculationBlockReason }}</p>

      <section v-if="cal.running || calculationError || preparationIssues.length || cal.activeOperationId" class="calculation-status" aria-live="polite">
        <div v-if="cal.running" class="progress-row"><b>{{ cal.operationProgress?.stageLabel || '正在准备资料' }}</b><span>{{ Math.round(cal.operationProgress?.progress || 0) }}%</span></div>
        <progress v-if="cal.running" :value="cal.operationProgress?.progress || 0" max="100" />
        <p v-if="cal.running && cal.operationProgress?.searchProgress" class="search-count">已评价 {{ cal.operationProgress.searchProgress.evaluations || 0 }} 次 · 最佳目标值 {{ finiteMetric(cal.operationProgress.searchProgress.bestLoss ?? cal.operationProgress.searchProgress.best_loss, 4) }}</p>
        <details v-if="cal.running && cal.operationProgress?.searchProgress?.history?.length" class="search-trace"><summary>参数搜索过程</summary><SearchProgress :progress="cal.operationProgress.searchProgress" /></details>
        <p v-if="calculationError" role="alert">{{ calculationError }}</p>
        <div v-if="preparationIssues.length" class="issue-list">
          <div v-for="(issue, index) in preparationIssues" :key="issue.eventId || index">
            <b>{{ eventName(issue.eventId) }}</b><span>{{ preparationIssueText({ ...issue, eventId: '', eventName: '' }) }}</span>
            <small v-for="gap in issue.missingIntervals || []" :key="'rain-' + gap.start">降雨缺测 {{ shortTime(gap.start) }} 至 {{ shortTime(gap.end) }}</small>
            <small v-for="gap in issue.petMissingIntervals || []" :key="'pet-' + gap.start">蒸散发缺测 {{ shortTime(gap.start) }} 至 {{ shortTime(gap.end) }}</small>
            <small v-for="gap in issue.flowMissingIntervals || []" :key="'flow-' + gap.start">流量缺测 {{ shortTime(gap.start) }} 至 {{ shortTime(gap.end) }}</small>
          </div>
          <button v-if="cal.calibrationMode === 'event'" class="btn" @click="excludeIssues">排除这些洪水</button>
          <button v-if="cal.rainfallMethod === 'reuse'" class="btn" :disabled="cal.running" @click="prepareFromStations">采用当前站点资料重新准备</button>
        </div>
      </section>

      <div class="workbench" :class="{ 'manual-mode': trialMode === 'manual' }">
        <aside class="panel flood-panel">
          <template v-if="cal.calibrationMode === 'event'">
            <div class="panel-heading"><h2>洪水目录</h2><span>{{ cal.events.length }} 场<span v-if="cal.schemeWindows.length"> · 方案时段 {{ cal.schemeWindows.length }}</span></span></div>
            <div class="event-browser-bar">
              <div class="event-scope" role="group" aria-label="洪水目录范围">
                <button type="button" :class="{ active: eventScope === 'selected' }" @click="eventScope = 'selected'">已选洪水</button>
                <button type="button" :class="{ active: eventScope === 'all' }" @click="eventScope = 'all'">全部洪水</button>
              </div>
              <span class="event-scope-count">显示 {{ filteredEvents.length }} 场</span>
            </div>
            <div class="event-filters">
              <input v-model="eventQuery" class="input" type="search" placeholder="洪水编号或时间" aria-label="搜索洪水" :disabled="cal.running" />
              <select v-model="eventYear" class="input" aria-label="洪水年份" :disabled="cal.running"><option value="all">全部年份</option><option v-for="year in eventYears" :key="year" :value="year">{{ year }}年</option></select>
              <select v-model="purposeFilter" class="input" aria-label="洪水用途筛选" :disabled="cal.running"><option value="all">全部用途</option><option v-for="purpose in purposes" :key="purpose" :value="purpose">{{ purpose === '未使用' ? '未选洪水' : purpose }}</option></select>
              <input v-model.number="peakMin" class="input" type="number" min="0" step="any" placeholder="洪峰下限" aria-label="洪峰下限" :disabled="cal.running" />
              <input v-model.number="peakMax" class="input" type="number" min="0" step="any" placeholder="洪峰上限" aria-label="洪峰上限" :disabled="cal.running" />
              <select v-model="eventSort" class="input" aria-label="洪水排序" :disabled="cal.running"><option value="time">按时间</option><option value="peak-desc">洪峰从大到小</option><option value="peak-asc">洪峰从小到大</option></select>
            </div>
            <div class="selection-counts"><span>率定 <b>{{ eventCounts.calibration }}</b></span><span>验证 <b>{{ eventCounts.validation }}</b></span><span>独立检验 <b>{{ eventCounts.independent }}</b></span></div>
            <div class="batch-actions">
              <label><input type="checkbox" :checked="allFilteredSelected" :disabled="cal.running || !filteredEvents.length" @change="selectFiltered($event.target.checked)" /> 选用当前筛选结果</label>
              <button class="btn" :disabled="cal.running || !selectedEventRows.length" @click="prepareAutoSplit">自动划分</button>
            </div>
            <div v-if="autoSplitPreview" class="split-preview">
              <div><b>自动划分预览</b><span>率定 {{ autoSplitPreview.counts.calibration }} · 验证 {{ autoSplitPreview.counts.validation }} · 独立检验 {{ autoSplitPreview.counts.independent }}</span></div>
              <p>{{ autoSplitPreview.summary }}</p>
              <div v-for="purpose in ['率定', '验证', '独立检验']" :key="purpose" class="split-names"><b>{{ purpose }}</b><span>{{ autoSplitPreview.assignments.filter(row => row.purpose === purpose).map(row => eventName(row.id)).join('、') }}</span></div>
              <button class="btn primary mini" :disabled="cal.running" @click="applyAutoSplit">采用划分</button>
            </div>
            <div class="flood-table" role="table" aria-label="洪水场次">
              <div class="flood-table-head" role="row"><span>选用</span><span>洪水与时段</span><span>洪峰 m³/s</span><span>用途</span></div>
              <div v-for="event in pagedEvents" :key="event.id" class="flood-row" :class="{ current: event.id === cal.eventId }" role="row">
                <input type="checkbox" :checked="event.purpose !== '未使用'" :aria-label="eventName(event.id) + ' 选用'" :disabled="cal.running" @change="cal.setEventPurpose(event.id, $event.target.checked ? '率定' : '未使用')" />
                <button class="flood-process" :aria-pressed="event.id === cal.eventId" @click="selectEvent(event)"><b>{{ eventName(event.id) }}</b><small>{{ eventPeriod(event) }}</small><em v-if="issueForEvent(event.id)">{{ issueForEvent(event.id) }}</em></button>
                <span>{{ event.observedPeak == null || event.observedPeak === '' ? '缺洪峰' : finiteMetric(event.observedPeak, 1) }}</span>
                <select class="input purpose-select" :value="event.purpose" :aria-label="eventName(event.id) + ' 用途'" :disabled="cal.running" @change="cal.setEventPurpose(event.id, $event.target.value)"><option v-for="purpose in purposes" :key="purpose" :value="purpose">{{ purpose === '未使用' ? '不选' : purpose }}</option></select>
              </div>
              <div v-if="!pagedEvents.length" class="empty">没有符合条件的洪水</div>
            </div>
            <footer v-if="eventScope === 'all'" class="pagination"><span>{{ eventPage }} / {{ pageCount }} 页</span><button class="btn mini" :disabled="eventPage === 1" @click="eventPage--">上一页</button><button class="btn mini" :disabled="eventPage >= pageCount" @click="eventPage++">下一页</button></footer>
            <p v-if="physicalConflict" class="field-error" role="alert">{{ physicalConflict }}</p>
            <details class="custom-window"><summary>添加洪水时段</summary><div class="field"><label>洪水名称</label><input v-model.trim="windowDraft.id" class="input" :disabled="cal.running" /></div><div class="field"><label>开始</label><input v-model="windowDraft.start" class="input" type="datetime-local" step="3600" :disabled="cal.running" /></div><div class="field"><label>结束</label><input v-model="windowDraft.end" class="input" type="datetime-local" step="3600" :disabled="cal.running" /></div><button class="btn" :disabled="cal.running" @click="registerWindow">添加</button></details>
          </template>

          <template v-else>
            <div class="panel-heading"><h2>评价时段</h2></div>
            <section v-for="period in periodFields" :key="period.key" class="period-block">
              <h3>{{ period.label }}{{ period.optional ? '（可选）' : '' }}</h3>
              <div class="field"><label :for="period.key + '-start'">开始</label><input :id="period.key + '-start'" class="input" type="datetime-local" step="3600" :value="cal[period.start]" :disabled="cal.running" @change="cal.setPeriod(period.start, $event.target.value)" /></div>
              <div class="field"><label :for="period.key + '-end'">结束</label><input :id="period.key + '-end'" class="input" type="datetime-local" step="3600" :value="cal[period.end]" :disabled="cal.running" @change="cal.setPeriod(period.end, $event.target.value)" /></div>
            </section>
            <p v-if="continuousError" role="alert" class="field-error">{{ continuousError }}</p>
          </template>
        </aside>

        <main class="process-workspace">
          <section class="panel process-panel">
            <div class="panel-heading process-heading"><div><h2>{{ activeProcessTitle }}</h2><span v-if="activeEvent && cal.calibrationMode === 'event'">{{ fullPeriod(activeEvent) }}</span></div><select v-if="cal.calibrationMode === 'continuous' && continuousPeriods.length" class="input period-select" v-model="selectedContinuousPeriod" aria-label="查看评价期"><option v-for="period in continuousPeriods" :key="period.id" :value="period.id">{{ period.label || period.id }}</option></select><button v-if="displayResult" class="btn mini" @click="openState">状态分布</button></div>
            <p v-if="previewLoading && !currentHydrograph" class="process-status">读取雨流过程</p>
            <div v-if="displayResult" class="result-view-actions"><span>流量过程</span><button class="btn" @click="openRiverFlow">河道流量</button></div>
            <p v-else-if="previewError && !currentHydrograph" class="process-status field-error">{{ previewError }}</p>
            <CalibrationProcessChart v-if="displayHydrograph.times?.length || displayHydrograph.obs?.length || displayHydrograph.forecast?.length" :hydrograph="displayHydrograph" :forcing="displayForcing" :visible-components="{}" :observed-only="!currentHydrograph?.forecast?.length" :comparisons="comparisonLines" :current-curve="{ label: '本次模拟', color: '#ca6e30' }" />
            <div v-else class="process-empty">{{ cal.calibrationMode === 'event' ? '选择洪水查看雨流过程' : '暂无计算结果' }}</div>
          </section>

          <section class="panel result-panel">
            <div class="panel-heading"><h2>{{ currentHydrograph ? '计算结果' : '洪水资料' }}</h2><button v-if="displayResult?.bestParameters" class="btn primary" :disabled="cal.running" @click="openSaveParameters">保存参数</button></div>
            <div v-if="currentHydrograph" class="metric-row">
              <div v-for="metric in processMetrics" :key="metric.label"><span>{{ metric.label }}</span><b>{{ metric.value }}</b></div>
            </div>
            <div v-else-if="activeEvent && cal.calibrationMode === 'event'" class="metric-row"><div><span>实测洪峰</span><b>{{ finiteMetric(activeEvent.observedPeak, 1) }} m³/s</b></div><div><span>洪水历时</span><b>{{ activeEvent.durationHours || '—' }} h</b></div><div><span>资料检查</span><b>{{ issueForEvent(activeEvent.id) || '随计算检查' }}</b></div></div>
            <table v-if="displayResult && trialMode !== 'manual'" class="evaluation-table"><thead><tr><th>用途</th><th>NSE</th><th>洪峰误差</th><th>峰现误差</th><th>水量误差</th></tr></thead><tbody><tr v-for="group in evaluationRows" :key="group.label"><th>{{ group.label }}</th><td>{{ finiteMetric(group.metrics.nse, 3) }}</td><td>{{ percentMetric(group.metrics.peak) }}</td><td>{{ hourMetric(group.metrics.time) }}</td><td>{{ percentMetric(group.metrics.volume) }}</td></tr></tbody></table>
            <p v-if="terminationLabel" class="result-stamp">{{ terminationLabel }}</p>
            <details v-if="displayResult?.optimizer?.history?.length && trialMode !== 'manual'" class="calculation-history"><summary>参数搜索过程</summary><SearchProgress :optimizer="displayResult.optimizer" /></details>
            <p v-if="savedParameterName" class="result-stamp">参数已保存：{{ savedParameterName }} <button class="text-button" @click="goSimulation">进入洪水模拟 →</button></p>
            <details v-if="cal.trialRecords.length" class="calculation-history"><summary>历次计算（{{ cal.trialRecords.length }}）</summary><button v-for="record in [...cal.trialRecords].reverse()" :key="record.taskId" class="history-row" @click="viewCalculation(record)"><span>{{ record.manual ? '人工试调' : '自动率定' }} · {{ shortTime(record.completedAt) }}</span><b>{{ record.taskId === displayResult?.taskId ? '正在查看' : '查看' }}</b></button></details>
          </section>
        </main>

        <aside v-if="trialMode === 'manual'" class="panel parameter-panel">
          <div class="panel-heading"><h2>人工试调</h2><button class="text-button" :disabled="cal.running" @click="cal.resetParams()">恢复</button></div>
          <fieldset :disabled="cal.running"><ParamTuner :params="cal.params" :defs="parameterDefinitions" :dirty="cal.dirty" :live-preview="false" :show-reset="false" title="" @update="(key, value) => cal.setParam(key, value)" /></fieldset>
          <fieldset v-if="manualRouting" :disabled="cal.running" class="routing-tuner"><h3>坡面汇流</h3><label v-for="(label,key) in {RS:'地表径流',RI:'壤中流',RG:'地下径流'}" :key="key">{{ label }} K · h<input class="input" type="number" min="0.05" max="720" step="any" v-model.number="manualRouting.default_slope_k_hours[key]" /></label><details><summary>河道汇流</summary><label v-for="(reach,id) in manualRouting.reach_muskingum" :key="id"><b>{{ id }}</b><span>K · h<input class="input" type="number" min="0.5" max="720" step="any" v-model.number="reach.k_hours" /></span><span>X<input class="input" type="number" min="0" max="0.5" step="0.01" v-model.number="reach.x" /></span></label></details></fieldset>
          <p v-if="parameterConstraint" class="field-error">{{ parameterConstraint }}</p>
          <button class="btn primary block" :disabled="!canRun" @click="startCalculation">{{ cal.running ? '正在计算' : '试算' }}</button>
        </aside>
      </div>

      <dialog ref="settings" class="settings-dialog" aria-labelledby="cal-settings-title" @click="closeBackdrop($event, settings)">
        <header class="dialog-heading"><h2 id="cal-settings-title">率定设置</h2><button class="btn" @click="settings.close()">完成</button></header>
        <div class="settings-summary"><span>{{ cal.section }}</span><span>新安江产流 · 坡面线性水库 · 马斯京根汇流</span></div>
        <div class="settings-grid">
          <div class="field"><label for="reference-parameters">参考参数</label><select id="reference-parameters" class="input" :value="referenceParameterId" :disabled="cal.running || parameterLoading" @change="applyReference($event.target.value)"><option value="" disabled>选用参考参数</option><option value="model-default">模型默认值</option><option v-for="row in namedParameters" :key="row.id" :value="row.id">{{ row.name }}</option></select></div>
          <div class="field"><label for="cal-target">率定参数组</label><select id="cal-target" class="input" :value="cal.calibrationTarget" :disabled="cal.running" @change="cal.setCalibrationTarget($event.target.value)"><option value="runoff">产流参数</option><option v-if="app.backendCase?.routingSearchParameterDefinitions" value="routing">坡面汇流参数</option><option v-if="app.backendCase?.jointSearchParameterDefinitions" value="joint">产流与汇流联合率定</option></select></div>
        </div>
        <template v-if="trialMode === 'automatic'">
          <SearchSpaceEditor v-if="activeSearchDefinitions.length" :model-value="activeSearchSelection" :definitions="activeSearchDefinitions" :params="activeSearchParameters" :routing="cal.calibrationTarget === 'routing'" :joint="cal.calibrationTarget === 'joint'" :disabled="cal.running" :error="searchError" @update:model-value="updateSearchSelection" />
          <SearchSpaceEditor v-if="cal.calibrationTarget === 'joint'" title="坡面与河道参数" routing joint :model-value="cal.routingSearchParameters" :definitions="jointRoutingDefinitions" :params="routingCoordinates" :disabled="cal.running" :error="routingSearchError" @update:model-value="cal.setRoutingSearchParameters" />
          <h3 class="settings-section-title">搜索设置</h3>
          <div class="settings-grid">
            <div class="field"><label for="algorithm">调参算法</label><select id="algorithm" class="input" :value="cal.algorithm" :disabled="cal.running" @change="cal.setOptimizerConfig('algorithm', $event.target.value)"><option v-for="row in algorithms" :key="row.id" :value="row.id">{{ row.label }}</option></select></div>
            <div class="field"><label for="max-evals">评价次数</label><input id="max-evals" class="input" type="number" min="1" step="1" :value="cal.maxEvaluations" :disabled="cal.running" @change="cal.setOptimizerConfig('maxEvaluations', $event.target.value)" /></div>
          </div>
          <h3 class="settings-section-title">评价指标</h3>
          <div class="objective-list"><label v-for="objective in objectives" :key="objective.key"><span><input type="checkbox" :checked="cal.criteria.metrics[objective.key]" :disabled="cal.running" @change="cal.setObjectiveMetric(objective.key, $event.target.checked)" /> {{ objective.label }}</span><input type="number" min="0" max="1" step="0.05" :value="cal.criteria.weights[objective.key]" :aria-label="objective.label + ' 权重'" :disabled="cal.running" @change="cal.setObjectiveWeight(objective.key, $event.target.value)" /></label></div>
          <p v-if="!objectiveWeightValid" class="field-error">指标权重合计须为1，当前为{{ objectiveWeightTotal.toFixed(2) }}</p>
        </template>
        <details class="advanced-settings"><summary>预热与搜索条件</summary>
          <div class="settings-grid">
            <div class="field"><label for="cal-state-policy">状态衔接</label><select id="cal-state-policy" class="input" :value="cal.statePolicy" :disabled="cal.running || Boolean(cal.scienceVersionId)" @change="cal.setScienceField('statePolicy', $event.target.value)"><option value="independent-warmup">{{ cal.calibrationMode === 'continuous' ? '各期独立预热' : '分场预热' }}</option><option value="continuous-origin">连续演算</option></select></div>
            <div class="field"><label for="cal-warmup">预热天数</label><input id="cal-warmup" class="input" type="number" min="0" step="1" :value="cal.warmupDays" :disabled="cal.running || Boolean(cal.scienceVersionId)" @change="cal.setSimulationField('warmupDays', $event.target.value)" /></div>
            <div v-if="cal.statePolicy === 'continuous-origin'" class="field"><label for="continuous-origin">连续起点</label><input id="continuous-origin" class="input" type="datetime-local" step="3600" :value="cal.continuousOrigin" :disabled="cal.running" @change="cal.setScienceField('continuousOrigin', $event.target.value)" /></div>
            <div class="field"><label for="cal-pet-mode">蒸散发</label><select id="cal-pet-mode" class="input" :value="cal.evaporationMode" :disabled="cal.running || Boolean(cal.scienceVersionId)" @change="cal.setScienceField('evaporationMode', $event.target.value)"><option value="monthly-climatology-assumption">逐月给定值</option><option value="constant-zero">给定值 0 mm/h</option><option v-if="cal.scienceVersionId" value="science-version">参考参数保存值</option></select></div>
            <template v-if="trialMode === 'automatic'"><div class="field"><label for="population-size">种群规模</label><input id="population-size" class="input" type="number" min="4" step="4" :value="cal.populationSize" :disabled="cal.running" @change="cal.setOptimizerConfig('populationSize', $event.target.value)" /></div><div class="field"><label for="search-seed">随机种子</label><input id="search-seed" class="input" type="number" min="0" :value="cal.searchSeed" :disabled="cal.running" @change="cal.setOptimizerConfig('searchSeed', $event.target.value)" /></div><div v-if="cal.calibrationTarget === 'runoff'" class="field"><label for="search-strategy">搜索顺序</label><select id="search-strategy" class="input" :value="cal.searchStrategy" :disabled="cal.running" @change="cal.setOptimizerConfig('searchStrategy', $event.target.value)"><option value="joint">联合搜索</option><option value="staged">蓄水产流 → 水源划分</option></select></div><div v-if="cal.calibrationTarget === 'joint'" class="field"><label for="joint-profile">联合范围</label><select id="joint-profile" class="input" :value="cal.jointProfile" :disabled="cal.running" @change="cal.setJointProfile($event.target.value)"><option v-for="profile in jointProfiles" :key="profile.id" :value="profile.id">{{ profile.label }}</option></select></div></template>
          </div>
        </details>
      </dialog>

      <dialog ref="parameterDialog" class="parameter-dialog" aria-labelledby="save-parameter-title" @click="closeBackdrop($event, parameterDialog)">
        <form @submit.prevent="saveParameters"><header class="dialog-heading"><h2 id="save-parameter-title">保存参数</h2><button type="button" class="btn" :disabled="parameterSaving" @click="parameterDialog.close()">取消</button></header><label for="parameter-name">参数名称</label><input id="parameter-name" class="input" v-model.trim="parameterName" maxlength="80" required :disabled="parameterSaving" /><p v-if="parameterSaveError" class="field-error" role="alert">{{ parameterSaveError }}</p><button class="btn primary block" :disabled="parameterSaving || !parameterName">{{ parameterSaving ? '保存中' : '保存' }}</button></form>
      </dialog>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import ModelSchemeBar from '@/components/model/ModelSchemeBar.vue'
import CalibrationProcessChart from '@/components/charts/CalibrationProcessChart.vue'
import ParamTuner from '@/components/params/ParamTuner.vue'
import SearchSpaceEditor from '@/components/params/SearchSpaceEditor.vue'
import SearchProgress from '@/components/params/SearchProgress.vue'
import { useAppStore } from '@/stores/app'
import { useCalibrationStore } from '@/stores/calibration'
import { useTasksStore } from '@/stores/tasks'
import { buildCalibrationRequest, physicalFloodGroups, stratifiedRandomEventSplit } from '@/services/calibrationSubmissionService'
import { saveModelScheme, makeSchemeEditable } from '@/services/modelSchemeService'
import { modelApi, uniqueRequestId } from '@/services/modelBackendService'
import { observedModelProcess, modelEventStatistics } from '@/services/modelInputPresentation'
import { loadModelInputWindow } from '@/services/modelInputService'
import { loadNananxiObservationWindow } from '@/services/historicalObservationService'
import { latestComparableTrial, trialConditions, resultProcessMatches, calibrationSummaryMetrics } from '@/services/manualCalibrationService'
import { visibleWorkspaceResult } from '@/services/savedModelResultService'
import { preparationIssueText } from '@/services/dailyOperationService'
import { routingSearchCoordinates, searchSelectionError, selectedSearchParameters } from '@/services/calibrationSpaceService'

const app = useAppStore(), cal = useCalibrationStore(), tasks = useTasksStore(), router = useRouter()
const schemeBar = ref(null), schemeOpened = ref(false), trialMode = ref('automatic'), settings = ref(null)
const eventQuery = ref(''), eventYear = ref('all'), purposeFilter = ref('all'), eventPage = ref(1)
const eventScope = ref('selected'), peakMin = ref(null), peakMax = ref(null), eventSort = ref('time')
const autoSplitPreview = ref(null)
const purposes = ['未使用', '率定', '验证', '独立检验']
const pageSize = 20
const windowDraft = reactive({ id: '', start: '', end: '' })
const preparationIssues = ref([]), calculationError = ref(''), inspectedResult = ref(null)
const preview = ref(null), previewLoading = ref(false), previewError = ref('')
const trialResults = reactive({}), referenceParameterId = ref(''), parameterLoading = ref(false), namedParameters = ref([])
const parameterDialog = ref(null), parameterName = ref(''), parameterSaving = ref(false), parameterSaveError = ref('')
const savedParameterId = ref(null), savedParameterName = ref('')
const routingRecord = ref(null), routingError = ref(''), selectedContinuousPeriod = ref('CALIBRATION')
const manualRouting = ref(null)
let previewSequence = 0
const allEvents = computed(() => cal.schemeEvents)
const eventYears = computed(() => [...new Set(allEvents.value.map(row => String(row.start).slice(0, 4)))].sort().reverse())
const selectedEventRows = computed(() => allEvents.value.filter(row => row.purpose !== '未使用' && row.status !== 'inactive'))
const browserEvents = computed(() => eventScope.value === 'selected' ? selectedEventRows.value : allEvents.value)
const filteredEvents = computed(() => {
  const query = eventQuery.value.trim()
  const rows = browserEvents.value.filter(row => {
    const peak = row.observedPeak == null || row.observedPeak === '' ? NaN : Number(row.observedPeak)
    return (eventYear.value === 'all' || String(row.start).startsWith(eventYear.value))
      && (purposeFilter.value === 'all' || row.purpose === purposeFilter.value)
      && (peakMin.value == null || peakMin.value === '' || (Number.isFinite(peak) && peak >= Number(peakMin.value)))
      && (peakMax.value == null || peakMax.value === '' || (Number.isFinite(peak) && peak <= Number(peakMax.value)))
      && (!query || [row.name, row.sourceFloodNo, row.id, row.start, row.end].some(value => String(value || '').includes(query)))
  })
  return [...rows].sort((left, right) => {
    if (eventSort.value === 'peak-desc' || eventSort.value === 'peak-asc') {
      const a = left.observedPeak == null || left.observedPeak === '' ? NaN : Number(left.observedPeak), b = right.observedPeak == null || right.observedPeak === '' ? NaN : Number(right.observedPeak)
      if (Number.isFinite(a) && !Number.isFinite(b)) return -1
      if (!Number.isFinite(a) && Number.isFinite(b)) return 1
      if (Number.isFinite(a) && Number.isFinite(b) && a !== b) return eventSort.value === 'peak-desc' ? b - a : a - b
    }
    return String(left.start || '').localeCompare(String(right.start || ''))
  })
})
const pageCount = computed(() => eventScope.value === 'selected' ? 1 : Math.max(1, Math.ceil(filteredEvents.value.length / pageSize)))
const pagedEvents = computed(() => eventScope.value === 'selected' ? filteredEvents.value : filteredEvents.value.slice((eventPage.value - 1) * pageSize, eventPage.value * pageSize))
const selectedIds = computed(() => filteredEvents.value.filter(row => row.purpose !== '未使用').map(row => row.id))
const allFilteredSelected = computed(() => filteredEvents.value.length > 0 && selectedIds.value.length === filteredEvents.value.length)
const eventCounts = computed(() => allEvents.value.reduce((counts, event) => {
  const key = { '率定': 'calibration', '验证': 'validation', '独立检验': 'independent' }[event.purpose]
  if (key) counts[key]++
  return counts
}, { calibration: 0, validation: 0, independent: 0 }))
const activeEvent = computed(() => allEvents.value.find(row => row.id === cal.eventId) || allEvents.value[0])
const physicalGrouping = computed(() => physicalFloodGroups(allEvents.value))
const physicalConflict = computed(() => physicalGrouping.value.conflicts.length ? '重叠洪水须设为同一用途：' + physicalGrouping.value.conflicts.map(row => row.memberEventIds.map(eventName).join('、')).join('；') : '')
const periodFields = [
  { key: 'cal', label: '率定期', start: 'calibrationStart', end: 'calibrationEnd' },
  { key: 'val', label: '验证期', start: 'validationStart', end: 'validationEnd' },
  { key: 'ind', label: '独立检验期', start: 'independentStart', end: 'independentEnd', optional: true }
]
const continuousError = computed(() => cal.calibrationMode === 'continuous' ? cal.validateCalibration({ manual: trialMode.value === 'manual' }).errors.find(value => !value.includes('次评价')) || '' : '')
const algorithms = computed(() => app.backendCase?.algorithms || [])
const jointProfiles = computed(() => app.backendCase?.jointSearchProfiles || [])
const jointDefinitions = computed(() => jointProfiles.value.find(row => row.id === cal.jointProfile)?.definitions || {})
const jointRoutingDefinitions = computed(() => (jointDefinitions.value.routing || []).map(row => ({ ...row, ...(routingRecord.value?.searchReferenceOnly && !cal.sourcePlan && row.key === 'muskingum_k_scale' ? { unavailable: '请设置河道基准K后使用河道K倍率' } : {}) })))
const routingCoordinates = computed(() => routingSearchCoordinates(routingRecord.value?.parameters))
const activeSearchSelection = computed(() => cal.calibrationTarget === 'routing' ? cal.routingSearchParameters : cal.searchParameters)
const activeSearchDefinitions = computed(() => cal.calibrationTarget === 'routing' ? app.backendCase?.routingSearchParameterDefinitions || [] : cal.calibrationTarget === 'joint' ? jointDefinitions.value.runoff || [] : app.backendCase?.searchParameterDefinitions || [])
const activeSearchParameters = computed(() => cal.calibrationTarget === 'routing' ? routingCoordinates.value : cal.params)
const searchError = computed(() => searchSelectionError(activeSearchSelection.value, activeSearchDefinitions.value, activeSearchParameters.value, cal.calibrationTarget === 'runoff' ? cal.searchStrategy : 'joint'))
const routingSearchError = computed(() => cal.calibrationTarget === 'joint' ? searchSelectionError(cal.routingSearchParameters, jointRoutingDefinitions.value, routingCoordinates.value, 'joint') : '')
const parameterDefinitions = computed(() => cal.paramDefs.filter(row => row.key in cal.params))
const parameterConstraint = computed(() => {
  if (cal.params.WM <= cal.params.WUM + cal.params.WLM) return 'WM须大于WUM与WLM之和'
  if (cal.params.KI + cal.params.KG >= 1) return 'KI与KG之和须小于1'
  return ''
})
const objectives = [{ key: 'nse', label: 'NSE' }, { key: 'kge', label: 'KGE' }, { key: 'peak', label: '洪峰误差' }, { key: 'time', label: '峰现误差' }, { key: 'volume', label: '水量误差' }]
const objectiveWeightTotal = computed(() => Object.entries(cal.criteria.metrics).reduce((sum, [key, enabled]) => sum + (enabled ? Number(cal.criteria.weights[key] || 0) : 0), 0))
const objectiveWeightValid = computed(() => Math.abs(objectiveWeightTotal.value - 1) < 0.001)
const canRun = computed(() => {
  if (!cal.modelSchemeId || cal.running || cal.activeOperationId || !app.backendCase || parameterLoading.value || parameterConstraint.value) return false
  if (cal.calibrationMode === 'event' && (physicalConflict.value || physicalGrouping.value.errors.length)) return false
  if (trialMode.value === 'automatic' && (searchError.value || routingSearchError.value || !objectiveWeightValid.value || routingError.value)) return false
  const errors = cal.validateCalibration({ manual: trialMode.value === 'manual' }).errors
  return !errors.some(value => trialMode.value !== 'manual' || !value.includes('次评价'))
})
const calculationBlockReason = computed(() => {
  if (!cal.modelSchemeId || cal.running) return ''
  if (!app.backendCase) return app.backendError || '模型服务尚未连接'
  if (parameterLoading.value) return '正在读取参考参数'
  if (cal.activeOperationId) return '请先查看当前计算'
  if (parameterConstraint.value) return parameterConstraint.value
  if (physicalConflict.value && cal.calibrationMode === 'event') return physicalConflict.value
  if (trialMode.value === 'automatic') {
    if (routingError.value) return routingError.value
    if (searchError.value || routingSearchError.value) return searchError.value || routingSearchError.value
    if (!objectiveWeightValid.value) return '评价指标权重合计须为1'
  }
  return cal.validateCalibration({ manual: trialMode.value === 'manual' }).errors.find(value => trialMode.value !== 'manual' || !value.includes('次评价')) || ''
})
const displayResult = computed(() => {
  if (inspectedResult.value) return inspectedResult.value
  const result = visibleWorkspaceResult(cal.runResult, app.activeContext)
  const periodId = cal.calibrationMode === 'event' ? activeEvent.value?.id : selectedContinuousPeriod.value
  return resultProcessMatches(cal, result, periodId) ? result : null
})
const continuousPeriods = computed(() => displayResult.value?.periods || [])
const currentPeriodId = computed(() => cal.calibrationMode === 'event' ? activeEvent.value?.id : selectedContinuousPeriod.value)
const currentHydrograph = computed(() => {
  if (trialMode.value === 'manual' && cal.calibrationMode === 'event' && activeEvent.value?.purpose === '独立检验') return null
  if (!inspectedResult.value && !resultProcessMatches(cal, displayResult.value, currentPeriodId.value)) return null
  return displayResult.value?.hydrographs?.[currentPeriodId.value] || null
})
const emptyHydrograph = { times: [], forecast: [], obs: [], metrics: null }
const displayHydrograph = computed(() => currentHydrograph.value || preview.value?.hydrograph || emptyHydrograph)
const displayForcing = computed(() => currentHydrograph.value?.forcing || (!currentHydrograph.value ? preview.value?.forcing : null) || { precipitation: [], temperature: [], components: {} })
const previousTrial = computed(() => latestComparableTrial(cal.trialRecords, displayResult.value, currentPeriodId.value))
const comparisonLines = computed(() => {
  const previous = previousTrial.value
  const hydrograph = previous ? trialResults[previous.taskId]?.hydrographs?.[currentPeriodId.value] : null
  return currentHydrograph.value && hydrograph ? [{ taskId: previous.taskId, label: '上次模拟', color: '#94a3aa', hydrograph }] : []
})
const processMetrics = computed(() => {
  const metrics = currentHydrograph.value?.metrics || {}
  return [
    { label: 'NSE', value: finiteMetric(metrics.nse ?? metrics.NSE, 3) },
    { label: '洪峰误差', value: percentMetric(metrics.peakErr ?? metrics.peakErrorPercent ?? metrics.peak_error_percent) },
    { label: '峰现误差', value: hourMetric(metrics.timeErrH ?? metrics.peakTimeErrorHours ?? metrics.peak_time_error_hours) },
    { label: '水量误差', value: percentMetric(metrics.volErr ?? metrics.volumeErrorPercent ?? metrics.volume_error_percent) }
  ]
})
const evaluationRows = computed(() => [{ label: '率定', metrics: calibrationSummaryMetrics(displayResult.value?.calibrationMetrics || {}) }, { label: '验证', metrics: calibrationSummaryMetrics(displayResult.value?.validationMetrics || {}) }, { label: '独立检验', metrics: calibrationSummaryMetrics(displayResult.value?.independentTest || {}) }])
const terminationLabel = computed(() => {
  const result = displayResult.value
  if (!result || result.evaluationMode === 'manual') return ''
  const optimizer = result.optimizer || {}
  const evaluations = optimizer.evaluations ?? optimizer.validEvaluations
  const count = evaluations == null ? '' : '评价' + evaluations + '次 · '
  const reason = optimizer.terminationReason || result.terminationReason
  return count + (reason === 'BUDGET_EXHAUSTED' ? '达到评价次数上限' : reason === 'CONVERGED' ? '搜索达到终止条件' : reason === 'CANCELLED' ? '计算已停止' : '计算完成')
})
const activeProcessTitle = computed(() => cal.calibrationMode === 'event' ? activeEvent.value ? eventName(activeEvent.value.id) : '雨流过程' : continuousPeriods.value.find(row => row.id === selectedContinuousPeriod.value)?.label || '连续过程')

function shortTime(value) { return String(value || '').replace('T', ' ').slice(0, 16) }
function eventName(id) { const row = allEvents.value.find(event => event.id === id); return row?.name || row?.sourceFloodNo || id || '计算时段' }
function eventPeriod(event) { return dayjs(event.start).format('MM-DD HH:mm') + ' 至 ' + dayjs(event.end).format('MM-DD HH:mm') }
function fullPeriod(event) { return shortTime(event.start) + ' 至 ' + shortTime(event.end) }
function finiteMetric(value, digits = 2) { return value == null || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits) }
function percentMetric(value) { const text = finiteMetric(value, 1); return text === '—' ? text : text + '%' }
function hourMetric(value) { const text = finiteMetric(value, 1); return text === '—' ? text : text + ' h' }
function issueForEvent(id) { const issue = preparationIssues.value.find(row => row.eventId === id); return issue ? preparationIssueText({ ...issue, eventId: '', eventName: '' }) : '' }
function closeBackdrop(event, dialog) { if (event.target === dialog) dialog.close() }
function updateSearchSelection(value) { cal.calibrationTarget === 'routing' ? cal.setRoutingSearchParameters(value) : cal.setSearchParameters(value) }
function selectEvent(event) { cal.eventId = event.id; cal.eventStart = event.start; cal.eventEnd = event.end; cal.saveCurrentWorkspace() }
function selectFiltered(selected) { cal.setEventPurposes(filteredEvents.value.map(row => ({ id: row.id, purpose: selected ? row.purpose === '未使用' ? '率定' : row.purpose : '未使用' }))) }
function prepareAutoSplit() {
  const rows = selectedEventRows.value
  if (physicalFloodGroups(rows).groupCount < 3) { autoSplitPreview.value = null; ElMessage.warning('少于三个独立洪水组，请逐场设置用途'); return }
  const assignments = stratifiedRandomEventSplit(rows, cal.eventSplitSeed ?? 42)
  const counts = assignments.reduce((result, row) => {
    if (row.purpose === '率定') result.calibration++
    if (row.purpose === '验证') result.validation++
    if (row.purpose === '独立检验') result.independent++
    return result
  }, { calibration: 0, validation: 0, independent: 0 })
  autoSplitPreview.value = {
    assignments,
    counts,
    summary: `根据实测洪峰分层，共 ${assignments.length} 场；重叠窗口按同一洪水组划分。`
  }
}
function applyAutoSplit() {
  if (!autoSplitPreview.value) return
  cal.setEventPurposes(autoSplitPreview.value.assignments)
  autoSplitPreview.value = null
}
function excludeIssues() { cal.setEventPurposes(preparationIssues.value.filter(row => row.eventId).map(row => ({ id: row.eventId, purpose: '未使用' }))); preparationIssues.value = []; calculationError.value = '' }
function registerWindow() {
  if (!windowDraft.id || !windowDraft.start || !windowDraft.end || windowDraft.start >= windowDraft.end) { ElMessage.warning('请填写洪水名称和起止时间'); return }
  const previous = cal.schemeWindows.find(event => event.name === windowDraft.id)
  const id = previous?.id || uniqueRequestId('WINDOW')
  const row = { ...windowDraft, id, eventId: id, name: windowDraft.id, purpose: previous?.purpose || '未使用', status: 'active', runtimeRegistered: false, sourceBundleVersionId: cal.schemeSourceBundleVersionId }
  if (previous) Object.assign(previous, row); else cal.schemeWindows.push(row)
  selectEvent(row); cal.markNeedsRun(''); cal.saveCurrentWorkspace()
  windowDraft.id = ''; windowDraft.start = ''; windowDraft.end = ''
}
async function restoreSchemeView() {
  cal.operationMode = 'calibration'; cal.fixedEventSimulation = false
  trialMode.value = 'automatic'; preparationIssues.value = []; calculationError.value = ''; inspectedResult.value = null
  savedParameterId.value = null; savedParameterName.value = ''; referenceParameterId.value = ''
  eventScope.value = selectedEventRows.value.length ? 'selected' : 'all'
  autoSplitPreview.value = null
  if (cal.activeOperationId) await cal.awaitOperation(cal.activeOperationId, cal.activeOperationRequest)
  else if (cal.modelSchemeId) await loadSchemeCalculations()
}
async function loadSchemeCalculations() {
  const schemeId = cal.modelSchemeId
  try {
    const payload = await modelApi('operations')
    const operations = (payload.operations || []).filter(row => row.status === 'completed' && row.runConfig?.request?.scheme?.id === schemeId)
      .sort((left, right) => String(left.createdAt).localeCompare(String(right.createdAt)))
    const records = []
    for (const operation of operations) {
      const result = await modelApi('operations/' + (operation.operationId || operation.taskId) + '/result')
      if (cal.modelSchemeId !== schemeId) return
      const taskId = result.primaryTaskId || result.taskId
      const request = result.frozenRequest || operation.runConfig.request
      trialResults[taskId] = { ...result, taskId, frozenRequest: request }
      records.push({ taskId, conditions: trialConditions(request, result), parameters: result.bestParameters,
        completedAt: operation.completedAt, manual: result.evaluationMode === 'manual' })
    }
    if (cal.modelSchemeId === schemeId) { cal.trialRecords = records; cal.saveCurrentWorkspace() }
  } catch (failure) { calculationError.value = failure.message }
}
async function startCalculation() {
  if (!canRun.value) return
  cal.running = true
  calculationError.value = ''; preparationIssues.value = []; inspectedResult.value = null; savedParameterName.value = ''; savedParameterId.value = null
  let request
  try {
    if (trialMode.value === 'manual' && manualRouting.value && JSON.stringify(manualRouting.value) !== JSON.stringify(routingRecord.value?.parameters)) {
      const routing = await modelApi('routing-versions', { method: 'POST', body: { name: cal.modelSchemeName + ' · 人工试调', parentVersionId: cal.schemeRoutingVersionId || 'control-split-v2', parameters: manualRouting.value } })
      cal.schemeRoutingVersionId = routing.routingVersionId
    }
    request = buildCalibrationRequest({ app, calibration: { ...cal, requiredInputBundleVersionId: null,
      ...(cal.calibrationTarget === 'joint' && cal.routingSearchParameters == null ? { routingSearchParameters: selectedSearchParameters(jointRoutingDefinitions.value) } : {}) },
      operationMode: 'calibration', events: allEvents.value, eventAssignment: cal.eventAssignment })
    if (trialMode.value === 'manual') {
      delete request.sourcePlan
      request.calibration.evaluationMode = 'manual'
      request.calibration.optimizer = { algorithm: 'FIXED_PARAMETERS', maxEvaluations: 1 }
      for (const key of ['searchParameters', 'routingSearchParameters', 'target', 'jointProfile']) delete request.calibration[key]
    }
    const scheme = await saveModelScheme(cal, request)
    request.scheme = { id: scheme.id, revision: scheme.revision }
    await schemeBar.value?.flush()
    const outcome = await cal.runOperationRequest(request, trialMode.value === 'manual' ? 'manualTrial' : 'calibration')
    preparationIssues.value = outcome.issues || []
    if (!outcome.ok) { calculationError.value = outcome.errors?.join('；') || '计算未完成'; return }
    const primaryId = outcome.result.primaryTaskId || outcome.result.taskId
    if (primaryId) trialResults[primaryId] = outcome.result
  } catch (failure) { calculationError.value = failure.message }
  finally { cal.running = false; cal.saveCurrentWorkspace() }
}
async function reconnectOperation() {
  calculationError.value = ''
  const outcome = await cal.awaitOperation(cal.activeOperationId, cal.activeOperationRequest)
  if (!outcome.ok) calculationError.value = outcome.errors.join('；')
}
async function prepareFromStations() {
  try {
    const defaults = await modelApi('preparation-defaults')
    cal.rainfallMethod = 'stationIdw'
    cal.schemeSourceBundleVersionId = defaults.sourceBundleVersionId
    cal.stationSourceId = defaults.stationSourceId
    cal.idwPower = defaults.idwPower
    cal.minimumStations = defaults.minimumStations
    makeSchemeEditable(cal)
    preparationIssues.value = []; calculationError.value = ''
    await schemeBar.value?.flush()
    await startCalculation()
  } catch (failure) { calculationError.value = failure.message }
}
async function refreshNamedParameters() {
  try { namedParameters.value = ((await modelApi('parameter-schemes')).schemes || []).filter(row => !row.contextKey || row.contextKey === cal.contextKey) }
  catch (failure) { calculationError.value = failure.message }
}
async function applyReference(id) {
  parameterLoading.value = true
  try {
    if (id === 'model-default') {
      cal.params = { ...app.backendCase.parameters }; cal.baseline = { ...cal.params }; cal.paramVersion = app.backendCase.parameterVersionId
      cal.scienceVersionId = null; cal.scienceDescriptor = null; cal.candidateWarmupHours = null; cal.evaporationMode = 'monthly-climatology-assumption'
    } else {
      const row = await modelApi('parameter-schemes/' + encodeURIComponent(id))
      cal.params = { ...row.parameters }; cal.baseline = { ...row.parameters }; cal.paramVersion = row.id
      cal.schemeRoutingVersionId = row.routing?.versionId || cal.schemeRoutingVersionId
      cal.scienceVersionId = row.science?.versionId || null; cal.scienceDescriptor = row.scienceSnapshot?.manifest || null
      cal.statePolicy = row.statePolicy || cal.statePolicy; cal.warmupDays = row.warmupDays ?? cal.warmupDays; cal.evaporationMode = row.science?.versionId ? 'science-version' : row.evaporation?.mode || 'monthly-climatology-assumption'
    }
    referenceParameterId.value = id; cal.requiredInputBundleVersionId = null
    cal.detachOriginalPlan(); cal.markNeedsRun(''); cal.saveCurrentWorkspace()
  } catch (failure) { calculationError.value = failure.message }
  finally { parameterLoading.value = false }
}
function openSaveParameters() { parameterName.value = cal.modelSchemeName; parameterSaveError.value = ''; parameterDialog.value.showModal() }
async function saveParameters() {
  if (!parameterName.value.trim() || !displayResult.value) return
  parameterSaving.value = true; parameterSaveError.value = ''
  try {
    const row = await modelApi('parameter-schemes', { method: 'POST', body: { name: parameterName.value.trim(), sourceTaskId: displayResult.value.primaryTaskId || displayResult.value.taskId } })
    savedParameterId.value = row.id; savedParameterName.value = row.name
    parameterDialog.value.close(); await refreshNamedParameters()
  } catch (failure) { parameterSaveError.value = failure.message }
  finally { parameterSaving.value = false }
}
function goSimulation() { router.push({ path: '/simulation', query: savedParameterId.value ? { parameter: savedParameterId.value } : {} }) }
function openState() {
  const result = displayResult.value
  const eventState = result.eventStates?.find(row => row.eventId === currentPeriodId.value)
  const sourceTaskId = eventState?.taskId || result.stateCalculationTaskId || result.primaryTaskId || result.taskId
  router.push({ path: '/state', query: result.operationId
    ? { operation: result.operationId, label: cal.modelSchemeName, event: currentPeriodId.value }
    : { source: result.primaryTaskId || result.taskId || sourceTaskId, event: currentPeriodId.value, label: cal.modelSchemeName } })
}
function openRiverFlow() {
  const result = displayResult.value
  const state = result.eventStates?.find(row => row.eventId === currentPeriodId.value)
  router.push({ path: '/diagnostics', query: { source: state?.taskId || result.stateCalculationTaskId || result.primaryTaskId || result.taskId, label: cal.modelSchemeName } })
}
async function viewCalculation(record) {
  try {
    const result = trialResults[record.taskId] || await tasks.getResult(record.taskId)
    const task = tasks.taskById(record.taskId) || await modelApi('calibration-tasks/' + record.taskId)
    trialResults[record.taskId] = result
    inspectedResult.value = { ...result, taskId: record.taskId, frozenRequest: result.frozenRequest || task.request || task.runConfig }
  } catch (failure) { calculationError.value = failure.message }
}
async function readPreview() {
  const sequence = ++previewSequence
  preview.value = null; previewError.value = ''; previewLoading.value = false
  if (!schemeOpened.value || cal.calibrationMode !== 'event' || !activeEvent.value || currentHydrograph.value) return
  const event = activeEvent.value
  previewLoading.value = true
  try {
    if (cal.rainfallMethod === 'reuse') {
      const source = { ...app.activeContext, selectedInputBundleVersionId: cal.schemeSourceBundleVersionId || app.selectedInputBundleVersionId, inputBundleVersionId: cal.schemeSourceBundleVersionId || app.selectedInputBundleVersionId }
      const input = await loadModelInputWindow(event.start, event.end, { source })
      if (sequence === previewSequence) preview.value = { ...observedModelProcess(input, event.start, event.end), statistics: modelEventStatistics(input, event.start, event.end) }
    } else {
      const data = await loadNananxiObservationWindow(event.start, event.end, { source: app.activeContext })
      if (sequence !== previewSequence) return
      const flows = new Map((data.river || []).map(row => [String(row.time).replace(' ', 'T').slice(0, 16), row.flowM3s]))
      const rain = new Map()
      for (const row of data.rain || []) {
        if (row.rainMm == null || !Number.isFinite(Number(row.rainMm))) continue
        const time = String(row.time).replace(' ', 'T').slice(0, 16)
        if (!rain.has(time)) rain.set(time, [])
        rain.get(time).push(Number(row.rainMm))
      }
      const times = []
      for (let time = dayjs(event.start); !time.isAfter(dayjs(event.end)); time = time.add(1, 'hour')) times.push(time.format('YYYY-MM-DDTHH:mm'))
      preview.value = { hydrograph: { times, forecast: [], obs: times.map(time => ({ time, value: flows.get(time) ?? null })), metrics: null },
        forcing: { precipitation: times.map(time => { const values = rain.get(time); return values?.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null }), temperature: [], components: {}, rainfallLabel: '站均降雨' } }
    }
  } catch (failure) { if (sequence === previewSequence) previewError.value = failure.message }
  finally { if (sequence === previewSequence) previewLoading.value = false }
}
watch([eventQuery, eventYear, purposeFilter, eventScope, peakMin, peakMax, eventSort], () => { eventPage.value = 1 })
watch(() => JSON.stringify([cal.modelSchemeId, cal.eventSplitSeed, selectedEventRows.value.map(row => [row.id, row.start, row.end, row.purpose, row.observedPeak])]), () => { autoSplitPreview.value = null }, { flush: 'sync' })
watch(pageCount, count => { eventPage.value = Math.min(eventPage.value, count) })
watch(continuousPeriods, periods => { if (!periods.some(row => row.id === selectedContinuousPeriod.value)) selectedContinuousPeriod.value = periods[0]?.id || 'CALIBRATION' })
watch(() => [schemeOpened.value, cal.calibrationMode, activeEvent.value?.id, activeEvent.value?.start, activeEvent.value?.end, cal.schemeSourceBundleVersionId, cal.rainfallMethod, currentHydrograph.value?.forecast?.length], readPreview)
watch(() => [app.backendCase, cal.schemeRoutingVersionId], async (_, __, onCleanup) => {
  let current = true
  onCleanup(() => { current = false })
  routingError.value = ''
  if (!app.backendCase) return
  try {
    const id = cal.schemeRoutingVersionId || app.selectedRoutingVersionId || 'control-split-v2'
    const payload = await modelApi('routing-versions')
    if (!current) return
    routingRecord.value = (payload.versions || []).find(row => row.routingVersionId === id || row.id === id)
    if (!routingRecord.value) throw new Error('当前汇流参数不可用，请在模型与资料中检查')
    manualRouting.value = JSON.parse(JSON.stringify(routingRecord.value.parameters))
  }
  catch (failure) { if (current) routingError.value = failure.message }
}, { immediate: true })
watch(() => cal.runResult, result => {
  if (result?.taskId) trialResults[result.taskId] = result
  inspectedResult.value = null
  const record = latestComparableTrial(cal.trialRecords, result, currentPeriodId.value)
  if (record && !trialResults[record.taskId]) tasks.getResult(record.taskId).then(value => { trialResults[record.taskId] = value }).catch(() => {})
})
watch(() => [app.basinId, app.modelScale, app.modelConfigId], ([basinId, modelScale, configId]) => { cal.setContext(basinId, modelScale, configId); refreshNamedParameters() })
onMounted(refreshNamedParameters)
</script>

<style scoped>
.split-names{display:grid;grid-template-columns:70px 1fr;gap:8px;font-size:14px;line-height:1.7}.split-names span{overflow-wrap:anywhere}
.result-view-actions{display:flex;align-items:center;gap:12px;margin:12px 0;color:#365c4b}.result-view-actions>span{margin-right:auto}
.routing-tuner{padding:0;border:0;display:grid;gap:12px;margin:20px 0}.routing-tuner>label{display:flex;justify-content:space-between;align-items:center;gap:10px}.routing-tuner>label .input{width:95px}.routing-tuner details{margin-top:12px}.routing-tuner details label{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}.routing-tuner details label>b{grid-column:1/-1}.routing-tuner details input{width:100%}
.calibration-page :is(.btn,.input,.text-button){min-height:36px;font-size:14px}
.calibration-page :is(label,p,small,summary,td,th,.flood-table-head,.selection-counts,.pagination){font-size:14px}
.parameter-panel :deep(label),.parameter-panel :deep(input),.parameter-panel :deep(button){font-size:14px}
.event-scope button{min-height:36px;font-size:14px}
.event-browser-bar{display:flex;align-items:center;gap:10px;padding:10px 14px 0}.event-scope{display:flex;gap:4px}.event-scope button{border:1px solid #cddbd3;background:#fff;border-radius:4px;padding:7px 12px;color:#58776a;font-size:12px;cursor:pointer}.event-scope button.active{background:#eaf4ee;color:#236452;border-color:#8db4a1;font-weight:600}.event-scope-count{margin-left:auto;color:#83958b;font-size:12px}.event-filters{grid-template-columns:repeat(3,minmax(0,1fr))}.event-filters input:first-child{grid-column:1/-1}.event-filters input,.event-filters select{min-height:36px;font-size:13px}.batch-actions{font-size:13px}.batch-actions .btn{min-height:34px}.split-preview{margin:0 14px 10px;padding:10px 12px;border:1px solid #cfe1d6;border-radius:4px;background:#f4faf6;color:#4a6b5b;font-size:12px}.split-preview>div{display:flex;justify-content:space-between;gap:10px}.split-preview span{color:#70897c}.split-preview p{margin:7px 0 9px;line-height:1.5}.flood-table-head{font-size:12px}.flood-row{font-size:13px}.flood-process b{font-size:14px}.flood-process small,.flood-process em{font-size:12px}.purpose-select{font-size:13px;min-height:34px}.btn.mini{min-height:34px;font-size:12px}
.calibration-page{display:grid;gap:14px;min-height:0;color:#344e47}.workbench-toolbar{display:flex;align-items:center;gap:10px;padding:12px 16px;border:1px solid #d8e4df;background:#fff;border-radius:4px}.period-switch,.work-switch{display:flex;gap:3px;padding:3px;background:#eff4f1;border-radius:4px}.period-switch button,.work-switch button{padding:8px 14px;border:0;background:none;border-radius:3px;color:#698178;font-size:13px;cursor:pointer}.period-switch button.active,.work-switch button.active{background:#fff;color:#236452;box-shadow:0 1px 3px #1a453014;font-weight:600}.work-switch{margin-right:auto}.workbench{display:grid;grid-template-columns:390px minmax(0,1fr);gap:14px;align-items:start}.workbench.manual-mode{grid-template-columns:360px minmax(0,1fr) 280px}.panel{border:1px solid #d8e4df;background:#fff;border-radius:4px;overflow:hidden}.panel-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:15px 16px;border-bottom:1px solid #e5ede9}.panel-heading h2{font-size:15px;font-weight:600;margin:0;color:#294f43}.panel-heading>span{font-size:12px;color:#82918b}.event-filters{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px 14px 10px}.event-filters input{grid-column:1/-1}.selection-counts{display:flex;gap:18px;padding:0 14px 12px;font-size:12px;color:#83928b}.selection-counts b{font-size:14px;color:#325d4d;padding-left:3px}.batch-actions{display:flex;align-items:center;gap:7px;padding:10px 14px;border-top:1px solid #e4ece8;background:#f8fbf9;font-size:11px}.batch-actions label{margin-right:auto;white-space:nowrap;display:flex;align-items:center;gap:5px}.batch-actions select{max-width:82px;font-size:11px;height:29px;padding:3px 7px}.flood-table-head,.flood-row{display:grid;grid-template-columns:30px minmax(0,1fr) 68px 78px;align-items:center;gap:5px;padding:0 12px}.flood-table-head{height:31px;background:#f6f9f7;border-top:1px solid #e0e9e4;font-size:10px;color:#789087}.flood-row{min-height:67px;border-top:1px solid #e8eeea;font-size:11px}.flood-row.current{background:#edf5f0;border-left:3px solid #3b8267;padding-left:9px}.flood-process{padding:10px 0;background:none;border:0;text-align:left;cursor:pointer;min-width:0;display:grid;gap:4px;color:#355e4d}.flood-process b{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.flood-process small{font-size:10px;color:#84958c}.flood-process em{font-size:10px;color:#a54a37;font-style:normal}.purpose-select{font-size:10px;padding:4px 5px;height:28px}.pagination{display:flex;align-items:center;gap:6px;padding:12px 14px;border-top:1px solid #e1e9e5}.pagination span{margin-right:auto;font-size:11px;color:#87968f}.custom-window{padding:12px 14px;font-size:12px;border-top:1px solid #e1e9e5}.custom-window summary{cursor:pointer;color:#70877b}.custom-window .field{margin-top:10px}.process-workspace{display:grid;gap:14px;min-width:0}.process-panel{height:470px;display:flex;flex-direction:column;min-width:0}.process-heading{padding-bottom:10px}.process-heading h2{font-size:16px}.process-heading div>span{display:block;font-size:11px;color:#7f9389;margin-top:6px}.process-heading .period-select{max-width:150px}.process-panel :deep(.hydrology-figure){flex:1;min-height:0;padding:4px 8px}.process-empty{display:grid;place-content:center;flex:1;color:#a0ada6;font-size:14px}.process-status{padding:0 16px;font-size:12px;color:#839b8d}.result-panel{padding-bottom:6px}.metric-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:18px 16px}.metric-row>div{display:grid;gap:8px}.metric-row span{font-size:11px;color:#8a9c91}.metric-row b{font-size:19px;font-weight:600;color:#335b49}.evaluation-table{width:calc(100% - 32px);margin:0 16px 10px;border-collapse:collapse;font-size:12px;text-align:right}.evaluation-table th,.evaluation-table td{padding:10px 7px;border-top:1px solid #e7eeea;font-weight:400}.evaluation-table th:first-child{text-align:left}.evaluation-table thead{color:#81968a}.result-stamp{margin:8px 16px;color:#82928a;font-size:12px}.calculation-history{padding:12px 16px;font-size:12px}.calculation-history summary{cursor:pointer;color:#70857a}.history-row{display:flex;justify-content:space-between;gap:18px;width:100%;margin-top:7px;border:0;border-bottom:1px solid #edf1ef;background:none;padding:10px 0;cursor:pointer;text-align:left;color:#6e8578;font-size:11px}.history-row b{font-weight:500;color:#2e795b}.parameter-panel{padding:0 14px 14px}.parameter-panel .panel-heading{padding-left:0;padding-right:0;margin-bottom:12px}.parameter-panel fieldset{border:0;padding:0;margin:0}.parameter-panel :deep(.param-list){max-height:630px}.parameter-panel :deep(.param-row){border-color:#dfe8e2;background:#fbfdfb}.parameter-panel .block{margin-top:14px}.block{display:block;width:100%}.field{display:grid;gap:6px}.field label{font-size:12px;color:#647e6d}.field-error{margin:10px 14px;font-size:12px;color:#a44735;line-height:1.6}.period-block{padding:16px;border-bottom:1px solid #e6ede8}.period-block h3{font-size:13px;font-weight:600;margin:0 0 12px;color:#426a55}.period-block .field+.field{margin-top:10px}.calculation-status{padding:14px 18px;background:#fff;border:1px solid #dbe6df;border-radius:4px;font-size:13px}.progress-row{display:flex;justify-content:space-between;font-size:12px;color:#4e7962}.progress-row b{font-weight:500}.calculation-status progress{width:100%;height:5px;margin-top:10px;accent-color:#3e8b69}.calculation-status p{color:#a44a36;margin:4px 0}.issue-list{display:grid;gap:8px;margin-top:10px}.issue-list>div{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;color:#8b6151;font-size:12px}.issue-list small{grid-column:2}.issue-list .btn{justify-self:start}.empty{text-align:center;padding:30px;color:#97a99d;font-size:12px}.text-button{border:0;background:none;color:#397b5e;font-size:12px;cursor:pointer;padding:0}.settings-dialog,.parameter-dialog{border:1px solid #bdcfc2;border-radius:5px;padding:22px;color:#355644;width:min(740px,92vw);max-height:85vh;background:#fff}.settings-dialog::backdrop,.parameter-dialog::backdrop{background:#173e2d50}.dialog-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.dialog-heading h2{margin:0;font-size:17px}.settings-summary{display:flex;justify-content:space-between;gap:16px;font-size:11px;color:#8d9f92;margin-bottom:18px}.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-bottom:18px}.settings-section-title{font-size:13px;font-weight:600;margin:16px 0 8px}.objective-list{display:grid;grid-template-columns:1fr 1fr;gap:8px 28px}.objective-list label{display:flex;align-items:center;justify-content:space-between;font-size:12px}.objective-list input[type=number]{width:65px;padding:5px;border:1px solid #cbd9cf;border-radius:3px}.advanced-settings{margin-top:20px;border-top:1px solid #e2ebe5;padding-top:14px}.advanced-settings summary{font-size:12px;color:#819485;cursor:pointer;margin-bottom:14px}.parameter-dialog{width:min(430px,92vw)}.parameter-dialog label{display:block;font-size:12px;margin-bottom:7px}.parameter-dialog .block{margin-top:16px}
@media(max-width:1300px){.workbench.manual-mode{grid-template-columns:340px minmax(0,1fr)}.manual-mode .parameter-panel{grid-column:1/-1}.manual-mode .parameter-panel :deep(.param-list){display:grid;grid-template-columns:repeat(4,minmax(0,1fr));max-height:none}.workbench{grid-template-columns:340px minmax(0,1fr)}}
@media(max-width:900px){.workbench,.workbench.manual-mode{grid-template-columns:1fr}.workbench-toolbar{flex-wrap:wrap}.work-switch{margin-right:0}.process-panel{height:420px}.manual-mode .parameter-panel :deep(.param-list){grid-template-columns:1fr 1fr}.settings-grid{grid-template-columns:1fr}.metric-row{grid-template-columns:1fr 1fr}.evaluation-table{font-size:11px}.issue-list>div{grid-template-columns:1fr}.issue-list small{grid-column:auto}.settings-summary{display:block}}
.calibration-page :is(.event-scope button,.period-switch button,.work-switch button,.flood-process b,.flood-process small,.flood-process em,.flood-row,.event-scope-count,.panel-heading>span,.selection-counts,.pagination span,.metric-row span,.result-view-actions,.split-preview,.history-row,.settings-summary,.settings-section-title){font-size:14px}
.calibration-page :is(.btn,.input,.text-button,.period-switch button,.work-switch button){min-height:36px}
.flood-table-head,.flood-row{grid-template-columns:28px minmax(0,1fr) 82px 100px}
.workbench{grid-template-columns:450px minmax(0,1fr)}
.workbench.manual-mode{grid-template-columns:420px minmax(0,1fr) 300px}
.event-filters{grid-template-columns:1fr 1fr;}.event-filters input:not(:first-child){grid-column:auto}
.split-preview .split-names{display:grid;justify-content:normal;grid-template-columns:70px minmax(0,1fr)}
@media(max-width:1250px){.workbench.manual-mode{grid-template-columns:420px minmax(0,1fr)}.manual-mode .parameter-panel{grid-column:1/-1}.manual-mode .parameter-panel :deep(.param-list){display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}.routing-tuner{grid-template-columns:repeat(3,minmax(0,1fr))}.routing-tuner h3,.routing-tuner details{grid-column:1/-1}}
@media(max-width:900px){.workbench,.workbench.manual-mode{grid-template-columns:1fr}.manual-mode .parameter-panel :deep(.param-list){grid-template-columns:1fr 1fr}}
</style>
