<template>
  <section class="calibration-page">
    <ModelSchemeBar ref="schemeBar" :commit="commitScheme" @editing="editing = $event" @opened="schemeOpened = $event" @loaded="restoreSchemeView" />
    <p v-if="route.query.parameter" class="settings-note">已从参数库选择参数。打开并编辑方案后，<button class="text-button" :disabled="!editing || cal.running" @click="applyReference(String(route.query.parameter))">采用该参数作为初值</button>。</p>
    <template v-if="schemeOpened">
      <section class="adopted-parameters"><b>方案采用参数</b><span>{{ namedParameters.find(row=>row.id===adoptedParameter?.parameter_id)?.name || (adoptedParameter?'正在读取名称':'尚未选定；从完成的计算中命名保存参数') }}</span><template v-if="adoptedParameter"><button class="btn" @click="router.push({path:'/simulation',query:{parameter:adoptedParameter.parameter_id}})">用于洪水模拟</button><button class="btn" :disabled="!editing || cal.running" @click="applyReference(adoptedParameter.parameter_id)">用于自动率定初值</button></template></section>
      <header class="workbench-toolbar">
        <NumericReadiness v-if="app.backendCase" />
        <div class="period-switch" aria-label="率定时段">
          <button :class="{ active: cal.calibrationMode === 'event' }" :disabled="cal.running || !editing" @click="cal.setCalibrationMode('event')">洪水场次</button>
          <button :class="{ active: cal.calibrationMode === 'continuous' }" :disabled="cal.running || !editing" @click="cal.setCalibrationMode('continuous')">连续时段</button>
        </div>
        <div class="work-switch" aria-label="调参方式">
          <button :class="{ active: trialMode === 'automatic' }" :disabled="cal.running" @click="trialMode = 'automatic'">自动率定</button>
          <button :class="{ active: trialMode === 'manual' }" :disabled="cal.running" @click="trialMode = 'manual'">人工试调</button>
        </div>
        <button class="btn" :disabled="cal.running" @click="settings.showModal()">率定设置</button>
        <button v-if="cal.running" class="btn" @click="cal.cancelCalibration()">停止</button>
        <button v-else-if="cal.activeOperationId" class="btn primary" @click="reconnectOperation">查看计算</button>
        <button v-else-if="trialMode === 'automatic'" class="btn primary" :disabled="!canRun" @click="startCalculation">{{ editing ? '保存设置并开始率定' : '开始率定' }}</button>
      </header>
      <p v-if="!cal.running && calculationBlockReason && !calculationError" class="field-error" role="status">{{ calculationBlockReason }}</p>
      <div v-if="routingValidityError && editing && trialMode==='automatic'" class="routing-repair"><button v-if="cal.originalCalibrationPlan?.parameterVersionId" class="btn" :disabled="parameterLoading" @click="applyOriginalBest">载入同事交付参数</button><button class="btn" :disabled="parameterLoading" @click="useDefaultRouting">使用默认汇流参数</button><button class="btn" @click="settings.showModal(); showRiverInitial=true">手动设置K与X</button></div>

      <section v-if="cal.running || calculationError || preparationIssues.length || cal.activeOperationId" class="calculation-status" aria-live="polite">
        <div v-if="cal.running" class="progress-row"><b>{{ cal.operationProgress?.stageLabel || '正在准备资料' }}</b><span>{{ Math.round(cal.operationProgress?.progress || 0) }}%</span></div>
        <progress v-if="cal.running" :value="cal.operationProgress?.progress || 0" max="100" />
        <p v-if="cal.running && cal.operationProgress?.searchProgress" class="search-count">已评价 {{ cal.operationProgress.searchProgress.evaluations || 0 }} 次 · 最佳目标值 {{ finiteMetric(cal.operationProgress.searchProgress.bestLoss ?? cal.operationProgress.searchProgress.best_loss, 4) }}</p>
        <p v-if="cal.running && cal.operationProgress?.searchProgress?.remainingSeconds != null" class="search-count">已耗时 {{ cal.operationProgress.searchProgress.elapsedSeconds }} 秒 · 按已完成评价估计剩余 {{ cal.operationProgress.searchProgress.remainingSeconds }} 秒（最终评价与状态保存另计）</p>
        <details v-if="cal.running && cal.operationProgress?.searchProgress?.history?.length" class="search-trace"><summary>参数搜索过程</summary><SearchProgress :progress="cal.operationProgress.searchProgress" /></details>
        <p v-if="calculationError" role="alert">{{ calculationError }}</p>
        <div v-if="preparationIssues.length" class="issue-list">
          <div v-for="(issue, index) in preparationIssues" :key="issue.eventId || index">
            <b>{{ eventName(issue.eventId) }}</b><span>{{ preparationIssueText({ ...issue, eventId: '', eventName: '' }) }}</span>
            <small v-for="gap in issue.missingIntervals || []" :key="'rain-' + gap.start">降雨缺测 {{ shortTime(gap.start) }} 至 {{ shortTime(gap.end) }}</small>
            <small v-for="gap in issue.petMissingIntervals || []" :key="'pet-' + gap.start">蒸散发缺测 {{ shortTime(gap.start) }} 至 {{ shortTime(gap.end) }}</small>
            <small v-for="gap in issue.flowMissingIntervals || []" :key="'flow-' + gap.start">流量缺测 {{ shortTime(gap.start) }} 至 {{ shortTime(gap.end) }}</small>
          </div>
          <button v-if="cal.calibrationMode === 'event'" class="btn" :disabled="!editing" @click="excludeIssues">排除这些洪水</button>
          <button v-if="cal.rainfallMethod === 'reuse'" class="btn" :disabled="cal.running || !editing" @click="prepareFromStations">采用当前站点资料重新准备</button>
        </div>
      </section>

      <div class="workbench" :class="{ 'manual-mode': trialMode === 'manual' }">
        <aside class="panel flood-panel">
          <template v-if="cal.calibrationMode === 'event'">
            <div class="panel-heading"><h2>洪水目录 {{ cal.events.length }} 场</h2><span>已选 {{ eventCounts.calibration + eventCounts.validation + eventCounts.independent }} 场</span></div>
            <div class="event-browser-bar">
              <div class="event-scope" role="group" aria-label="洪水目录范围">
                <button type="button" :class="{ active: eventScope === 'selected' }" @click="eventScope = 'selected'">已选洪水</button>
                <button type="button" :class="{ active: eventScope === 'all' }" @click="eventScope = 'all'">全部洪水</button>
                <button v-if="cal.deliverySelection.length" type="button" :class="{ active: eventScope === 'delivery' }" @click="eventScope = 'delivery'">交付选取</button>
              </div>
              <span class="event-scope-count">显示 {{ filteredEvents.length }} 场</span>
            </div>
            <div class="event-filters">
              <input v-model="eventQuery" class="input" type="search" placeholder="洪水编号或时间" aria-label="搜索洪水" />
              <select v-model="eventYear" class="input" aria-label="洪水年份"><option value="all">全部年份</option><option v-for="year in eventYears" :key="year" :value="year">{{ year }}年</option></select>
              <select v-model="purposeFilter" class="input" aria-label="洪水用途筛选"><option value="all">全部用途</option><option v-for="purpose in purposes" :key="purpose" :value="purpose">{{ purpose === '未使用' ? '未选洪水' : purpose }}</option></select>
              <input v-model.number="peakMin" class="input" type="number" min="0" step="any" placeholder="洪峰下限" aria-label="洪峰下限" />
              <input v-model.number="peakMax" class="input" type="number" min="0" step="any" placeholder="洪峰上限" aria-label="洪峰上限" />
              <button v-if="eventQuery || eventYear!=='all' || purposeFilter!=='all' || peakMin!=='' || peakMax!==''" class="btn" @click="eventQuery='';eventYear='all';purposeFilter='all';peakMin='';peakMax=''">清除筛选</button>
            </div>
            <div class="selection-counts"><span>率定 <b>{{ eventCounts.calibration }}</b></span><span>验证 <b>{{ eventCounts.validation }}</b></span><span>独立检验 <b>{{ eventCounts.independent }}</b></span></div>
            <div class="batch-actions">
              <label><input type="checkbox" :checked="allFilteredSelected" :disabled="cal.running || !editing || !filteredEvents.length" @change="selectFiltered($event.target.checked)" /> 选用当前筛选结果</label>
              <button class="btn" :disabled="cal.running || !editing || !selectedEventRows.length" @click="prepareAutoSplit">自动划分</button>
            </div>
            <div v-if="autoSplitPreview" class="split-preview">
              <div><b>自动划分预览</b><span>率定 {{ autoSplitPreview.counts.calibration }} · 验证 {{ autoSplitPreview.counts.validation }} · 独立检验 {{ autoSplitPreview.counts.independent }}</span></div>
              <p>{{ autoSplitPreview.summary }}</p>
              <div v-for="purpose in ['率定', '验证', '独立检验']" :key="purpose" class="split-names"><b>{{ purpose }}</b><span>{{ autoSplitPreview.assignments.filter(row => row.purpose === purpose).map(row => eventName(row.id)).join('、') }}</span></div>
              <button class="btn primary mini" :disabled="cal.running || !editing" @click="applyAutoSplit">采用划分</button>
            </div>
            <div class="flood-table" role="table" aria-label="洪水场次">
              <div class="flood-table-head" role="row"><span>选用</span><span class="time-column-head"><FloodColumnHeader label="开始" column="start" :sort-key="eventSortKey" :direction="eventSortDirection" filter @sort="sortEvents"><select v-model="eventYear" class="input"><option value="all">全部年份</option><option v-for="year in eventYears" :key="year">{{ year }}</option></select></FloodColumnHeader><FloodColumnHeader label="结束" column="end" :sort-key="eventSortKey" :direction="eventSortDirection" @sort="sortEvents" /><FloodColumnHeader label="历时" column="duration" :sort-key="eventSortKey" :direction="eventSortDirection" @sort="sortEvents" /></span><FloodColumnHeader label="洪峰 m³/s" column="peak" :sort-key="eventSortKey" :direction="eventSortDirection" filter @sort="sortEvents"><input v-model="peakMin" class="input" type="number" placeholder="洪峰下限" /><input v-model="peakMax" class="input" type="number" placeholder="洪峰上限" /></FloodColumnHeader><FloodColumnHeader label="用途" column="purpose" :sort-key="eventSortKey" :direction="eventSortDirection" filter @sort="sortEvents"><select v-model="purposeFilter" class="input"><option value="all">全部用途</option><option v-for="purpose in purposes" :key="purpose">{{ purpose }}</option></select></FloodColumnHeader></div>
              <div v-for="event in pagedEvents" :key="event.id" class="flood-row" :class="{ current: event.id === cal.eventId }" role="row">
                <input type="checkbox" :checked="event.purpose !== '未使用'" :aria-label="eventName(event.id) + ' 选用'" :disabled="cal.running || !editing" @change="cal.setEventPurpose(event.id, $event.target.checked ? '率定' : '未使用')" />
                <button class="flood-process" :aria-pressed="event.id === cal.eventId" @click="selectEvent(event)"><b>{{ eventName(event.id) }}</b><small>{{ eventPeriod(event) }}<template v-if="event.catalogueBoundaryOverride || cal.schemeWindows.some(row=>row.id===event.id)"> · 自定义时段</template></small><em v-if="issueForEvent(event.id)">{{ issueForEvent(event.id) }}</em></button>
                <span>{{ event.observedPeak == null || event.observedPeak === '' ? '缺洪峰' : finiteMetric(event.observedPeak, 1) }}</span>
                <select class="input purpose-select" :value="event.purpose" :aria-label="eventName(event.id) + ' 用途'" :disabled="cal.running || !editing" @change="cal.setEventPurpose(event.id, $event.target.value)"><option v-for="purpose in purposes" :key="purpose" :value="purpose">{{ purpose === '未使用' ? '不选' : purpose }}</option></select>
              </div>
              <div v-if="!pagedEvents.length" class="empty">没有符合条件的洪水</div>
            </div>
            <footer class="pagination"><span>{{ eventPage }} / {{ pageCount }} 页</span><button class="btn mini" :disabled="eventPage === 1" @click="eventPage--">上一页</button><button class="btn mini" :disabled="eventPage >= pageCount" @click="eventPage++">下一页</button></footer>
            <p v-if="physicalConflict" class="field-error" role="alert">{{ physicalConflict }}</p>
            <details class="custom-window"><summary>添加洪水时段</summary><div class="field"><label>洪水名称</label><input v-model.trim="windowDraft.id" class="input" :disabled="cal.running || !editing" /></div><div class="field"><label>开始</label><input v-model="windowDraft.start" class="input" type="datetime-local" step="3600" :disabled="cal.running || !editing" /></div><div class="field"><label>结束</label><input v-model="windowDraft.end" class="input" type="datetime-local" step="3600" :disabled="cal.running || !editing" /></div><button class="btn" :disabled="cal.running || !editing" @click="registerWindow">添加</button></details>
          </template>

          <template v-else>
            <div class="panel-heading"><h2>评价时段</h2></div>
            <section v-for="period in periodFields" :key="period.key" class="period-block">
              <h3>{{ period.label }}{{ period.optional ? '（可选）' : '' }}</h3>
              <div class="field"><label :for="period.key + '-start'">开始</label><input :id="period.key + '-start'" class="input" type="datetime-local" step="3600" :value="cal[period.start]" :disabled="cal.running || !editing" @change="cal.setPeriod(period.start, $event.target.value)" /></div>
              <div class="field"><label :for="period.key + '-end'">结束</label><input :id="period.key + '-end'" class="input" type="datetime-local" step="3600" :value="cal[period.end]" :disabled="cal.running || !editing" @change="cal.setPeriod(period.end, $event.target.value)" /></div>
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
            <div v-if="trialMode==='manual'" class="curve-controls"><label><input v-model="showObserved" type="checkbox" /> 实测流量</label><select v-model="automaticReference" class="input" aria-label="自动率定参考"><option value="">不显示自动参考</option><option v-for="row in automaticCurves" :key="row.taskId" :value="row.taskId">自动率定 · {{ shortTime(row.completedAt) }}</option></select><div v-for="row in curveOptions" :key="row.taskId" class="curve-entry"><label><input type="checkbox" :checked="curveSelection[row.taskId]!==false" @change="curveSelection[row.taskId]=$event.target.checked" /><i :style="{background:row.color}" />{{ row.label }}</label><small>{{ shortTime(row.completedAt) }} · NSE {{ finiteMetric(trialResults[row.taskId]?.hydrographs?.[currentPeriodId]?.metrics?.nse,3) }} · 洪峰误差 {{ percentMetric(trialResults[row.taskId]?.hydrographs?.[currentPeriodId]?.metrics?.peakErr ?? trialResults[row.taskId]?.hydrographs?.[currentPeriodId]?.metrics?.peak_error_percent) }}</small><span v-if="!trialResults[row.taskId]?.hydrographs?.[currentPeriodId]">本次尚未计算此场</span><span v-else-if="!sameProcessConditions(trialResults[row.taskId])">计算条件不同</span><details><summary>参数变化</summary><p>{{ curveParameterChanges(row) }}</p></details><button class="text-button" @click="viewCalculation(row)">查看并保存参数</button></div></div>
            <CalibrationProcessChart v-if="displayHydrograph.times?.length || displayHydrograph.obs?.length || displayHydrograph.forecast?.length" :hydrograph="displayHydrograph" :forcing="displayForcing" :visible-components="{}" :show-observed="showObserved" :show-current="trialMode!=='manual'" :observed-only="!currentHydrograph?.forecast?.length && !comparisonLines.length" :comparisons="comparisonLines" :current-curve="{ label: '本次模拟', color: '#ca6e30' }" />
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
            <ResultSaveBar v-if="displayResult" :result-id="displayResult.operationId || displayResult.primaryTaskId || displayResult.taskId" :suggestion="cal.modelSchemeName+' · '+new Date().toLocaleDateString('zh-CN')" />
            <div v-if="displayResult" class="scope-actions"><button class="btn" @click="conditionsDialog.showModal()">查看原条件</button><button class="btn" :disabled="cal.running" @click="rerunResult">按原条件复算</button><button v-if="displayResult.bestParameters" class="btn" :disabled="cal.running" @click="simulateBest">采用最佳参数模拟</button></div>
            <p v-if="displayResult?.timings" class="result-stamp">资料准备 {{ displayResult.timings.inputPreparationSeconds ?? displayResult.timings.preparationSeconds }} 秒 · 搜索 {{ displayResult.timings.searchSeconds }} 秒 · 最终评价 {{ displayResult.timings.finalEvaluationSeconds }} 秒 · 状态保存 {{ displayResult.timings.stateSavingSeconds }} 秒</p>
            <details v-if="displayResult?.optimizer?.history?.length && trialMode !== 'manual'" class="calculation-history"><summary>参数搜索过程</summary><SearchProgress :optimizer="displayResult.optimizer" /></details>
            <p v-if="savedParameterName" class="result-stamp">参数已保存：{{ savedParameterName }} <button class="text-button" @click="goSimulation">进入洪水模拟 →</button></p>
            <details v-if="cal.trialRecords.length" class="calculation-history"><summary>历次计算（{{ cal.trialRecords.length }}）</summary><button v-for="record in [...cal.trialRecords].reverse()" :key="record.taskId" class="history-row" @click="viewCalculation(record)"><span>{{ record.manual ? '人工试调' : '自动率定' }} · {{ shortTime(record.completedAt) }}</span><b>{{ record.taskId === displayResult?.taskId ? '正在查看' : '查看' }}</b></button></details>
          </section>
        </main>

        <aside v-if="trialMode === 'manual'" class="panel parameter-panel">
          <div class="panel-heading"><h2>人工试调</h2><button class="text-button" :disabled="cal.running" @click="resetTrial">恢复来源参数</button></div>
          <p class="settings-note">临时试调，尚未保存为参数。来源：{{ trialSource }}</p>
          <select class="input" aria-label="试调参数来源" :disabled="cal.running" @change="loadTrialSource($event.target.value)"><option value="">选择参数来源</option><option value="initial">方案初值</option><option value="default">模型默认参数</option><optgroup label="本方案自动率定"><option v-for="row in automaticCurves" :key="row.taskId" :value="'result:'+row.taskId">{{ shortTime(row.completedAt) }} 自动率定参数</option></optgroup><optgroup label="参数库"><option v-for="row in namedParameters" :key="row.id" :value="'parameter:'+row.id">{{ row.name }}</option></optgroup></select>
          <label class="field">试算范围<select v-model="trialScope" class="input"><option value="current">当前洪水／评价期</option><option value="all">全部已选洪水／评价期</option></select></label>
          <div class="parameter-kind"><button v-for="item in parameterKinds" :key="item.id" class="btn" :class="{primary:manualKind===item.id}" @click="manualKind=item.id">{{ item.label }}</button></div>
          <fieldset v-if="manualKind==='runoff'" :disabled="cal.running"><ParamTuner :params="trialParams" :defs="parameterDefinitions" :live-preview="false" :show-reset="false" title="产流参数" @update="(key, value) => trialParams[key]=Number(value)" /></fieldset>
          <RoutingParameterFields v-else :parameters="trialRouting" :baseline="trialBaseline?.routing" :kind="manualKind" :disabled="cal.running" :error="routingError" />
          <details v-if="routingProblems.length && manualKind==='channel'" class="routing-problems"><summary>查看 {{ routingProblems.length }} 条问题河段</summary><p>1小时时步要求：2KX ≤ 1 ≤ 2K(1−X)。固定X时，K须在以下区间。</p><p v-for="row in routingProblems" :key="row.id">{{ row.id }}：K={{ row.k_hours }}h，X={{ row.x }}；可用K {{ finiteMetric(row.minimum,3) }}～{{ Number.isFinite(row.maximum)?finiteMetric(row.maximum,3):'不限' }}h</p></details>
          <p v-if="parameterConstraint" class="field-error">{{ parameterConstraint }}</p>
          <button class="btn block" :disabled="cal.running || !trialBaseline" @click="resetTrial">恢复本次载入参数</button>
          <button class="btn primary block" :disabled="!canRun" @click="startCalculation">{{ cal.running ? '正在计算' : trialScope==='current'?'试算当前洪水':'试算全部已选洪水' }}</button>
        </aside>
      </div>

      <HydrologyDialog ref="conditionsDialog" class="settings-dialog"><header class="dialog-heading"><h2>本次成果的原始计算条件</h2><button class="btn" @click="conditionsDialog.close()">关闭</button></header><p>方案 {{ displayResult?.lineage?.schemeName || cal.modelSchemeName }} · 预热 {{ displayResult?.frozenRequest?.period?.warmupDays }} 天</p><table class="evaluation-table"><thead><tr><th>洪水 / 评价期</th><th>开始</th><th>结束</th><th>用途</th></tr></thead><tbody><tr v-for="row in displayResult?.frozenRequest?.period?.events || displayResult?.frozenRequest?.period?.windows || []" :key="row.id"><td>{{ row.name || row.id }}</td><td>{{ shortTime(row.start) }}</td><td>{{ shortTime(row.end) }}</td><td>{{ row.purpose }}</td></tr></tbody></table><p>起算规则：{{ displayResult?.frozenRequest?.calibration?.statePolicy === 'continuous-origin' ? '连续演算，自 '+shortTime(displayResult?.frozenRequest?.calibration?.continuousOrigin) : '各场独立预热' }}</p><p>算法 {{ displayResult?.frozenRequest?.calibration?.optimizer?.algorithm }} · 预算 {{ displayResult?.frozenRequest?.calibration?.optimizer?.maxEvaluations }} 次</p><table class="evaluation-table"><thead><tr><th>参数</th><th>初值</th><th>下限</th><th>上限</th></tr></thead><tbody><tr v-for="row in displayResult?.searchPlan?.rows || []" :key="row.key"><td>{{ row.label || row.key }}</td><td>{{ finiteMetric(row.initial,4) }}</td><td>{{ row.minimum ?? '固定' }}</td><td>{{ row.maximum ?? '固定' }}</td></tr></tbody></table></HydrologyDialog>
      <HydrologyDialog ref="settings" class="settings-dialog" aria-labelledby="cal-settings-title" @click="closeBackdrop($event, settings)">
        <header class="dialog-heading"><h2 id="cal-settings-title">率定设置</h2><button class="btn" :disabled="parameterLoading" @click="settings.close()">完成</button></header>
        <div class="settings-summary"><span>{{ cal.section }}</span><span>新安江产流 · 坡面线性水库 · 马斯京根汇流</span></div>
        <div class="settings-grid">
          <div class="field"><label for="reference-parameters">载入参数初值 <button v-if="referenceParameterId && referenceParameterId !== 'model-default'" class="text-button" title="修改参数名称" :disabled="cal.running" @click="renameParameter">✎</button></label><select id="reference-parameters" class="input" :value="referenceParameterId" :disabled="cal.running || !editing || parameterLoading" @change="applyReference($event.target.value)"><option value="" disabled>从参数库选择</option><option v-for="row in namedParameters" :key="row.id" :value="row.id">{{ row.name }}</option></select></div><div class="field"><label>默认初值</label><button class="btn" :disabled="cal.running || !editing || parameterLoading" @click="applyReference('model-default')">使用模型默认值</button></div>
        </div>
        <label class="settings-note"><input v-model="loadPairedConditions" type="checkbox" /> 载入参数时同时采用该记录的蒸散发与预热条件（默认只载入参数数值）</label><p v-if="cal.originalCalibrationPlan?.parameterVersionId" class="settings-note">原交付最佳参数可用作新计算初值。<button class="text-button" :disabled="cal.running || !editing || parameterLoading" @click="applyOriginalBest">载入同事交付参数作为初值</button>（替换产流与汇流初值，保留选用场次、搜索范围和算法预算）</p>
        <template v-if="trialMode === 'automatic'">
          <h3 class="settings-section-title">参与搜索的参数</h3>
          <div class="scope-actions"><button v-for="group in [{id:'runoff',name:'仅产流'},{id:'routing',name:'仅汇流'},{id:'joint',name:'联合率定'}]" :key="group.id" class="btn" :disabled="cal.running || !editing" @click="chooseTarget(group.id)">{{ group.name }}</button></div>
          <label v-if="cal.calibrationTarget !== 'routing'" class="field">范围模板<select v-model="rangeTemplate" class="input" :disabled="cal.running || !editing" @change="applyRangeTemplate($event.target.value)"><option value="custom">自定义</option><option value="project">新安江常用范围</option><option value="extended">扩大搜索范围</option></select></label>
          <p class="settings-note">{{ rangeTemplateNote }}</p>
          <SearchSpaceEditor v-if="combinedDefinitions.length" :model-value="combinedSelection" :definitions="combinedDefinitions" :params="combinedCoordinates" :ranges="cal.searchRangeOverrides" routing :disabled="cal.running || !editing" :error="[searchError,routingSearchError].filter(Boolean).join('；')" @update:model-value="updateCombinedSelection" @update:ranges="updateRanges" @initial-value="updateInitialValue" />
          <p v-if="cal.calibrationTarget==='joint'" class="settings-note">河道传播时间整体调整：1表示采用当前各河段K。实际K初值 {{ channelKSummary }}；{{ channelKRangeSummary }}。各河段保持自身差异，X可统一搜索。</p>
          <button class="btn block" @click="showRiverInitial=!showRiverInitial">{{ showRiverInitial?'收起':'查看或修改' }}各河段K、X初值</button><RoutingParameterFields v-if="showRiverInitial" :parameters="manualRouting" :baseline="routingRecord?.parameters" kind="channel" :disabled="cal.running || !editing" :error="routingError" @changed="routingEdited" />
          <h3 class="settings-section-title">算法与预算</h3><div class="btn-row"><button v-for="budget in [{n:120,p:12},{n:480,p:24},{n:960,p:48}]" :key="budget.n" class="btn" :disabled="cal.running || !editing" @click="applyBudget(budget)">{{ budget.n }} 次 / 种群 {{ budget.p }}</button></div>
          <div class="settings-grid">
            <div class="field"><label for="algorithm">调参算法</label><select id="algorithm" class="input" :value="cal.algorithm" :disabled="cal.running || !editing" @change="cal.setOptimizerConfig('algorithm', $event.target.value)"><option v-for="row in algorithms" :key="row.id" :value="row.id">{{ row.label }}</option></select></div>
            <div class="field"><label for="max-evals">评价次数</label><input id="max-evals" class="input" type="number" min="1" step="1" :value="cal.maxEvaluations" :disabled="cal.running || !editing" @change="cal.setOptimizerConfig('maxEvaluations', $event.target.value)" /></div>
          </div>
          <h3 class="settings-section-title">评价指标</h3>
          <div class="objective-list"><label v-for="objective in objectives" :key="objective.key"><span><input type="checkbox" :checked="cal.criteria.metrics[objective.key]" :disabled="cal.running || !editing" @change="cal.setObjectiveMetric(objective.key, $event.target.checked)" /> {{ objective.label }}</span><input type="number" min="0" max="1" step="0.05" :value="cal.criteria.weights[objective.key]" :aria-label="objective.label + ' 权重'" :disabled="cal.running || !editing" @change="cal.setObjectiveWeight(objective.key, $event.target.value)" /></label></div>
          <p v-if="!objectiveWeightValid" class="field-error">指标权重合计须为1，当前为{{ objectiveWeightTotal.toFixed(2) }}</p>
        </template>
        <ScientificConditionsEditor :model-value="cal.scienceConditionsDraft" :science-version-id="cal.scienceVersionId" :state-policy="cal.statePolicy" :warmup-days="cal.warmupDays" :continuous="cal.calibrationMode==='continuous'" :disabled="cal.running || !editing" @update:model-value="changeConditions" />
        <label v-if="cal.statePolicy==='continuous-origin'" class="field continuous-origin-field">连续演算开始时间<input id="continuous-origin" class="input" type="datetime-local" step="3600" :value="cal.continuousOrigin" :disabled="cal.running || !editing" @change="cal.setScienceField('continuousOrigin',$event.target.value)" /><small>{{ originDuration }}</small></label>
        <details class="advanced-settings"><summary>算法细项与资料准备</summary>
          <p class="settings-note">本站网与处理规则随方案保存。<button class="text-button" :disabled="cal.running || !editing || parameterLoading" @click="applyCurrentPreparation">采用当前站网与处理规则</button>（从“流域与站网”读取，保存新版本时确认重建全部选用输入）</p>
          <div class="settings-grid">
            <template v-if="trialMode === 'automatic'"><div class="field"><label for="population-size">种群规模</label><input id="population-size" class="input" type="number" min="4" step="4" :value="cal.populationSize" :disabled="cal.running || !editing" @change="cal.setOptimizerConfig('populationSize', $event.target.value)" /></div><div class="field"><label for="search-seed">随机种子</label><input id="search-seed" class="input" type="number" min="0" :value="cal.searchSeed" :disabled="cal.running || !editing" @change="cal.setOptimizerConfig('searchSeed', $event.target.value)" /></div><div v-if="cal.calibrationTarget === 'runoff'" class="field"><label for="search-strategy">搜索顺序</label><select id="search-strategy" class="input" :value="cal.searchStrategy" :disabled="cal.running || !editing" @change="cal.setOptimizerConfig('searchStrategy', $event.target.value)"><option value="joint">联合搜索</option><option value="staged">蓄水产流 → 水源划分</option></select></div></template>
          </div>
        </details>
      </HydrologyDialog>

      <HydrologyDialog ref="parameterDialog" class="parameter-dialog" aria-labelledby="save-parameter-title" @click="closeBackdrop($event, parameterDialog)">
        <form @submit.prevent="saveParameters"><header class="dialog-heading"><h2 id="save-parameter-title">保存参数</h2><button type="button" class="btn" :disabled="parameterSaving" @click="parameterDialog.close()">取消</button></header><label for="parameter-name">参数名称</label><input id="parameter-name" class="input" v-model.trim="parameterName" maxlength="80" required :disabled="parameterSaving" /><p v-if="parameterSaveError" class="field-error" role="alert">{{ parameterSaveError }}</p><button class="btn primary block" :disabled="parameterSaving || !parameterName">{{ parameterSaving ? '保存中' : '保存' }}</button></form>
      </HydrologyDialog>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import ModelSchemeBar from '@/components/model/ModelSchemeBar.vue'
import CalibrationProcessChart from '@/components/charts/CalibrationProcessChart.vue'
import ParamTuner from '@/components/params/ParamTuner.vue'
import SearchSpaceEditor from '@/components/params/SearchSpaceEditor.vue'
import SearchProgress from '@/components/params/SearchProgress.vue'
import RoutingParameterFields from '@/components/params/RoutingParameterFields.vue'
import ScientificConditionsEditor from '@/components/params/ScientificConditionsEditor.vue'
import ResultSaveBar from '@/components/model/ResultSaveBar.vue'
import NumericReadiness from '@/components/model/NumericReadiness.vue'
import HydrologyDialog from '@/components/model/HydrologyDialog.vue'
import FloodColumnHeader from '@/components/model/FloodColumnHeader.vue'
import { sortFloodRows } from '@/services/floodTablePresentation'
import { fixedTrialRequest, readTrialWorkspace, saveTrialWorkspace, processComparisonIdentity } from '@/services/manualTrialWorkspace'
import { routingIsStable } from '@/services/routingPresentation'
import { useAppStore } from '@/stores/app'
import { useCalibrationStore } from '@/stores/calibration'
import { useTasksStore } from '@/stores/tasks'
import { buildCalibrationRequest, physicalFloodGroups, stratifiedRandomEventSplit } from '@/services/calibrationSubmissionService'
import { saveModelScheme, prepareTrialInputs, makeSchemeEditable, schemeWindowCounts, adoptOriginalBestParameters, continuousOriginError, adoptHistoricalPreparation } from '@/services/modelSchemeService'
import { modelApi, uniqueRequestId } from '@/services/modelBackendService'
import { observedModelProcess, modelEventStatistics } from '@/services/modelInputPresentation'
import { loadModelInputWindow, loadHistoricalDatabaseProcess } from '@/services/modelInputService'
import { latestComparableTrial, trialConditions, resultProcessMatches, calibrationSummaryMetrics } from '@/services/manualCalibrationService'
import { visibleWorkspaceResult } from '@/services/savedModelResultService'
import { preparationIssueText } from '@/services/dailyOperationService'
import { routingSearchCoordinates, searchCoordinates, searchSelectionError, selectedSearchParameters, applySearchRangeTemplate } from '@/services/calibrationSpaceService'

const app = useAppStore(), cal = useCalibrationStore(), tasks = useTasksStore(), router = useRouter(), route = useRoute()
const editing = ref(false), rangeTemplateNote = ref('未覆盖的参数沿用项目范围；模板不改变初值及勾选项。')
const schemeBar = ref(null), schemeOpened = ref(false), trialMode = ref('automatic'), settings = ref(null)
const conditionsDialog = ref(null), simulateAfterSave = ref(false)
const eventQuery = ref(''), eventYear = ref('all'), purposeFilter = ref('all'), eventPage = ref(1)
const eventScope = ref('selected'), peakMin = ref(''), peakMax = ref(''), eventSort = ref('time')
const autoSplitPreview = ref(null)
const purposes = ['未使用', '率定', '验证', '独立检验']
const pageSize = 10
const windowDraft = reactive({ id: '', start: '', end: '' })
const preparationIssues = ref([]), calculationError = ref(''), inspectedResult = ref(null)
const preview = ref(null), previewLoading = ref(false), previewError = ref('')
const trialResults = reactive({}), referenceParameterId = ref(''), parameterLoading = ref(false), namedParameters = ref([])
const parameterDialog = ref(null), parameterName = ref(''), parameterSaving = ref(false), parameterSaveError = ref('')
const savedParameterId = ref(null), savedParameterName = ref('')
const routingRecord = ref(null), routingError = ref(''), selectedContinuousPeriod = ref('CALIBRATION')
const manualRouting = ref(null)
const trialParams=ref({}), trialRouting=ref(null), trialSource=ref('方案初值'), trialScope=ref('current')
const trialReady=ref(false), adoptedParameter=ref(null), loadPairedConditions=ref(false)
const curveSelection=reactive({}), automaticReference=ref(''), showObserved=ref(true)
const parameterSaveRequest=ref(null), eventSortKey=ref('start'), eventSortDirection=ref('asc')
const clone=value=>JSON.parse(JSON.stringify(value))
const trialBaseline=ref(null)
async function loadTrialSource(value){
  if(!value)return
  parameterLoading.value=true
  try{
    let params,routing,label
    if(value==='initial'){params=cal.params;const row=await modelApi('routing-versions');routing=cal.manualRoutingPayload||(row.versions||[]).find(row=>row.routingVersionId===cal.schemeRoutingVersionId)?.parameters;label='方案初值'}
    else if(value==='default'){params=app.backendCase.parameters;routing=(await modelApi('database/routing-defaults')).parameters;label='模型默认参数'}
    else if(value.startsWith('parameter:')){const row=await modelApi('parameter-schemes/'+encodeURIComponent(value.slice(10)));params=row.parameters;const routes=await modelApi('routing-versions');routing=row.routingParameters||row.routingSnapshot?.parameters||(routes.versions||[]).find(item=>item.routingVersionId===row.routing?.versionId)?.parameters;label='参数库 · '+row.name}
    else if(value.startsWith('result:')){const result=trialResults[value.slice(7)];params=result.bestParameters;const version=result.routingCandidate?.routingVersionId||result.routing?.versionId||result.routingVersionId||result.lineage?.routingVersionId||result.frozenRequest?.routing?.versionId;const routes=await modelApi('routing-versions');routing=result.bestRoutingParameters||result.routingParameters||(routes.versions||[]).find(row=>row.routingVersionId===version)?.parameters;label='本方案自动率定 · '+shortTime(cal.trialRecords.find(row=>row.taskId===value.slice(7))?.completedAt)}
    if(!params||!routing)throw new Error('该记录缺少完整产汇流参数，请选择其他记录')
    trialParams.value=clone(params);trialRouting.value=clone(routing);trialSource.value=label;trialBaseline.value={params:clone(params),routing:clone(routing)}
  }catch(failure){calculationError.value=failure.message}finally{parameterLoading.value=false}
}
function resetTrial(){if(trialBaseline.value){trialParams.value=clone(trialBaseline.value.params);trialRouting.value=clone(trialBaseline.value.routing)}}
function sortEvents(key,direction){eventSortKey.value=key;eventSortDirection.value=direction;eventPage.value=1}
const effectiveRouting=computed(()=>trialMode.value==='manual'?trialRouting.value:manualRouting.value)
const effectiveParams=computed(()=>trialMode.value==='manual'?trialParams.value:cal.params)
const routingProblems=computed(()=>Object.entries(effectiveRouting.value?.reach_muskingum||{}).filter(([,row])=>!routingIsStable(row)).map(([id,row])=>({id,...row,minimum:1/(2*(1-row.x)),maximum:row.x>0?1/(2*row.x):Infinity})))
const manualKind = ref('runoff'), showRiverInitial = ref(false), rangeTemplate = ref('custom')
const parameterKinds = [{id:'runoff',label:'产流参数'},{id:'slope',label:'坡面汇流'},{id:'channel',label:'河道汇流'}]
const routingValidityError = computed(() => {
  if (cal.sourcePlan && trialMode.value==='automatic') return ''
  if (!effectiveRouting.value) return routingError.value || '正在读取汇流参数'
  return routingProblems.value.length ? `当前${trialMode.value==='manual'?'试调':'初始'}参数中有${routingProblems.value.length}条河段无法按1小时时步直接计算；请在河道参数区查看并调整。` : ''
})
const channelKSummary = computed(() => {
  const values=Object.values(manualRouting.value?.reach_muskingum || {}).map(row=>row.k_hours).filter(Number.isFinite)
  return values.length ? `${Number(Math.min(...values).toPrecision(4))}–${Number(Math.max(...values).toPrecision(4))} 小时` : '正在读取'
})
const channelKRangeSummary = computed(() => {
  const range=cal.routingSearchParameters?.muskingum_k_scale
  const values=Object.values(manualRouting.value?.reach_muskingum||{}).map(row=>row.k_hours).filter(Number.isFinite)
  return range && values.length ? `整体搜索后K覆盖 ${Number((Math.min(...values)*range.minimum).toPrecision(4))}–${Number((Math.max(...values)*range.maximum).toPrecision(4))} 小时（每条河段K乘同一系数）` : 'K保持各河段初值'
})
const originDuration = computed(() => {
  const start=cal.calibrationMode==='continuous'?cal.calibrationStart:selectedEventRows.value.map(row=>row.start).sort()[0]
  const hours=dayjs(start).diff(dayjs(cal.continuousOrigin),'hour')
  return Number.isFinite(hours) ? `最早评价时间 ${shortTime(start)}；此前连续预热 ${hours} 小时` : '请填写连续演算开始时间'
})
const slopeTunerDefinitions = ['RS','RI','RG'].map((key,i)=>({key,symbol:key+' K',label:['地表径流','壤中流','地下径流'][i],unit:'h',min:.05,max:720,step:.05}))
const channelTunerDefinitions = [{key:'k_hours',symbol:'K',label:'河道传播时间',unit:'h',min:.5,max:720,step:.1},{key:'x',symbol:'X',label:'马斯京根权重',unit:'',min:0,max:.5,step:.01}]
watch([trialParams,trialRouting,trialSource,trialScope],()=>{if(trialReady.value&&cal.modelSchemeId)saveTrialWorkspace(cal.contextKey,cal.modelSchemeId,{params:trialParams.value,routing:trialRouting.value,source:trialSource.value,scope:trialScope.value,baseline:trialBaseline.value})},{deep:true})
let previewSequence = 0
const allEvents = computed(() => cal.schemeEvents)
const eventYears = computed(() => [...new Set(allEvents.value.map(row => String(row.start).slice(0, 4)))].sort().reverse())
const selectedEventRows = computed(() => allEvents.value.filter(row => row.purpose !== '未使用' && row.status !== 'inactive'))
const browserEvents = computed(() => eventScope.value === 'selected' ? selectedEventRows.value : eventScope.value === 'delivery'
  ? allEvents.value.filter(row => cal.deliverySelection.some(saved => saved.id === row.id)) : allEvents.value)
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
  return sortFloodRows(rows,eventSortKey.value,eventSortDirection.value)
})
const pageCount = computed(() => Math.max(1, Math.ceil(filteredEvents.value.length / pageSize)))
const pagedEvents = computed(() => filteredEvents.value.slice((eventPage.value - 1) * pageSize, eventPage.value * pageSize))
const selectedIds = computed(() => filteredEvents.value.filter(row => row.purpose !== '未使用').map(row => row.id))
const allFilteredSelected = computed(() => filteredEvents.value.length > 0 && selectedIds.value.length === filteredEvents.value.length)
const eventCounts = computed(() => allEvents.value.reduce((counts, event) => {
  const key = { '率定': 'calibration', '验证': 'validation', '独立检验': 'independent' }[event.purpose]
  if (key) counts[key]++
  return counts
}, { calibration: 0, validation: 0, independent: 0 }))
const windowCounts = computed(() => schemeWindowCounts(cal.schemeWindows))
const activeEvent = computed(() => allEvents.value.find(row => row.id === cal.eventId) || allEvents.value[0])
const physicalGrouping = computed(() => physicalFloodGroups(allEvents.value))
const physicalConflict = computed(() => physicalGrouping.value.conflicts.length ? '重叠洪水须设为同一用途：' + physicalGrouping.value.conflicts.map(row => row.memberEventIds.map(eventName).join('、')).join('；') : '')
const periodFields = [
  { key: 'cal', label: '率定期', start: 'calibrationStart', end: 'calibrationEnd' },
  { key: 'val', label: '验证期', start: 'validationStart', end: 'validationEnd' },
  { key: 'ind', label: '独立检验期', start: 'independentStart', end: 'independentEnd', optional: true }
]
const continuousError = computed(() => cal.calibrationMode === 'continuous' ? cal.validateCalibration({ manual: trialMode.value === 'manual' }).errors.find(value => !value.includes('次评价')) || '' : '')
const originError = computed(() => continuousOriginError(cal.statePolicy, cal.continuousOrigin, cal.calibrationMode === 'continuous' ? cal.calibrationStart : selectedEventRows.value.map(row => row.start).sort()[0]))
const algorithms = computed(() => app.backendCase?.algorithms || [])
const jointProfiles = computed(() => app.backendCase?.jointSearchProfiles || [])
const jointDefinitions = computed(() => jointProfiles.value.find(row => row.id === cal.jointProfile)?.definitions || {})
const presentRoutingDefinition = row => ({...row, ...(row.key==='muskingum_k_scale'?{label:'河道传播时间整体调整',symbol:'K整体调整',initialReadOnly:true}:row.key==='muskingum_x'?{label:'马斯京根权重',symbol:'X'}:{})})
const jointRoutingDefinitions = computed(() => (jointDefinitions.value.routing || []).map(presentRoutingDefinition))
const routingCoordinates = computed(() => ({...routingSearchCoordinates(manualRouting.value || routingRecord.value?.parameters), muskingum_k_scale: 1}))
const activeSearchSelection = computed(() => cal.calibrationTarget === 'routing' ? cal.routingSearchParameters : cal.searchParameters)
const activeSearchDefinitions = computed(() => cal.calibrationTarget === 'routing' ? (app.backendCase?.routingSearchParameterDefinitions || []).map(presentRoutingDefinition) : cal.calibrationTarget === 'joint' ? jointDefinitions.value.runoff || [] : app.backendCase?.searchParameterDefinitions || [])
const activeSearchParameters = computed(() => cal.calibrationTarget === 'routing' ? routingCoordinates.value : cal.params)
const searchError = computed(() => searchSelectionError(activeSearchSelection.value, activeSearchDefinitions.value, activeSearchParameters.value, cal.calibrationTarget === 'runoff' ? cal.searchStrategy : 'joint', cal.calibrationTarget === 'joint' && cal.routingSearchParameters !== null && Object.keys(cal.routingSearchParameters).length > 0))
const routingSearchError = computed(() => cal.calibrationTarget === 'joint' ? searchSelectionError(cal.routingSearchParameters, jointRoutingDefinitions.value, routingCoordinates.value, 'joint', cal.searchParameters === null || Object.keys(cal.searchParameters).length > 0) : '')
const combinedDefinitions = computed(() => cal.calibrationTarget === 'joint' ? [...activeSearchDefinitions.value, ...jointRoutingDefinitions.value] : activeSearchDefinitions.value)
const combinedCoordinates = computed(() => ({...searchCoordinates(cal.params), ...routingCoordinates.value}))
const combinedSelection = computed(() => cal.calibrationTarget === 'joint' ? {...(cal.searchParameters ?? selectedSearchParameters(activeSearchDefinitions.value)), ...(cal.routingSearchParameters ?? selectedSearchParameters(jointRoutingDefinitions.value))} : activeSearchSelection.value)
function updateCombinedSelection(value) {
  rangeTemplate.value='custom'
  if(value) cal.searchRangeOverrides = {...cal.searchRangeOverrides, ...value}
  if (cal.calibrationTarget !== 'joint') { updateSearchSelection(value); return }
  if(value === null) {cal.setSearchParameters(null);cal.setRoutingSearchParameters(null);return}
  const routingKeys = new Set(jointRoutingDefinitions.value.map(row=>row.key))
  cal.setSearchParameters(Object.fromEntries(Object.entries(value).filter(([key])=>!routingKeys.has(key))))
  cal.setRoutingSearchParameters(Object.fromEntries(Object.entries(value).filter(([key])=>routingKeys.has(key))))
}
const parameterDefinitions = computed(() => cal.paramDefs.filter(row => row.key in cal.params))
const parameterConstraint = computed(() => {
  const values=effectiveParams.value
  if (values.WM <= values.WUM + values.WLM) return 'WM须大于WUM与WLM之和'
  if (values.KI + values.KG >= 1) return 'KI与KG之和须小于1'
  return ''
})
const objectives = [{ key: 'nse', label: 'NSE' }, { key: 'kge', label: 'KGE' }, { key: 'peak', label: '洪峰误差' }, { key: 'time', label: '峰现误差' }, { key: 'volume', label: '水量误差' }]
const objectiveWeightTotal = computed(() => Object.entries(cal.criteria.metrics).reduce((sum, [key, enabled]) => sum + (enabled ? Number(cal.criteria.weights[key] || 0) : 0), 0))
const objectiveWeightValid = computed(() => Math.abs(objectiveWeightTotal.value - 1) < 0.001)
const canRun = computed(() => {
  if (!cal.modelSchemeId || cal.running || cal.activeOperationId || !app.backendCase || parameterLoading.value || parameterConstraint.value || originError.value || routingValidityError.value) return false
  if(trialMode.value==='manual')return trialReady.value && (cal.calibrationMode==='continuous' ? Boolean(continuousPeriods.value.find(row=>row.id===currentPeriodId.value)) : (trialScope.value==='all'?selectedEventRows.value.length>0:Boolean(activeEvent.value&&activeEvent.value.purpose!=='未使用'&&activeEvent.value.status!=='inactive')))
  if (cal.calibrationMode === 'event' && (physicalConflict.value || physicalGrouping.value.errors.length)) return false
  if (trialMode.value === 'automatic' && (searchError.value || routingSearchError.value || !objectiveWeightValid.value || routingError.value)) return false
  const errors = cal.validateCalibration({ manual: trialMode.value === 'manual' }).errors
  return !errors.some(value => trialMode.value !== 'manual' || !value.includes('次评价'))
})
const calculationBlockReason = computed(() => {
  if (!cal.modelSchemeId || cal.running) return ''
  if (!app.backendCase) return app.backendError || '模型服务尚未连接'
  if (parameterLoading.value) return '正在读取初始参数'
  if (cal.activeOperationId) return '请先查看当前计算'
  if (parameterConstraint.value) return parameterConstraint.value
  if (originError.value) return originError.value
  if (routingValidityError.value) return routingValidityError.value
  if(trialMode.value==='manual')return canRun.value?'':'请选择方案内洪水并载入有效试调参数'
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
const continuousPeriods = computed(() => periodFields.filter(row=>cal[row.start]&&cal[row.end]).map(row=>({id:{cal:'CALIBRATION',val:'VALIDATION',ind:'INDEPENDENT'}[row.key],label:row.label})))
const currentPeriodId = computed(() => cal.calibrationMode === 'event' ? activeEvent.value?.id : selectedContinuousPeriod.value)
const currentHydrograph = computed(() => {
  if (!inspectedResult.value && !resultProcessMatches(cal, displayResult.value, currentPeriodId.value)) return null
  return displayResult.value?.hydrographs?.[currentPeriodId.value] || null
})
const emptyHydrograph = { times: [], forecast: [], obs: [], metrics: null }
const displayHydrograph = computed(() => currentHydrograph.value || preview.value?.hydrograph || emptyHydrograph)
const displayForcing = computed(() => currentHydrograph.value?.forcing || (!currentHydrograph.value ? preview.value?.forcing : null) || { precipitation: [], temperature: [], components: {} })
const manualCurves=computed(()=>cal.trialRecords.filter(row=>row.manual&&trialResults[row.taskId]).slice(-2))
const automaticCurves=computed(()=>cal.trialRecords.filter(row=>!row.manual&&trialResults[row.taskId]))
const curveOptions=computed(()=>[...(automaticReference.value?[{...automaticCurves.value.find(row=>row.taskId===automaticReference.value),label:'自动率定参考',color:'#187f84',lineType:'dashed'}]:[]),...manualCurves.value.map((row,index)=>({...row,label:'人工试调 '+(cal.trialRecords.filter(row=>row.manual).findIndex(item=>item.taskId===row.taskId)+1),color:['#bf6832','#7952a1'][index],lineType:index?'dotted':'solid'}))].filter(row=>row.taskId))
const comparisonLines = computed(() => {
  if(trialMode.value!=='manual')return []
  return curveOptions.value.filter(row=>curveSelection[row.taskId]!==false).flatMap(row=>{const result=trialResults[row.taskId],hydrograph=result?.hydrographs?.[currentPeriodId.value];return hydrograph&&sameProcessConditions(result)?[{...row,hydrograph}]:[]})
})
function sameProcessConditions(result){const base=displayResult.value;if(!base)return true;return processComparisonIdentity(result.frozenRequest,result,currentPeriodId.value)===processComparisonIdentity(base.frozenRequest,base,currentPeriodId.value)}
function curveParameterChanges(row){const result=trialResults[row.taskId],reference=trialResults[automaticReference.value]||trialResults[manualCurves.value[0]?.taskId];const before=reference?.bestParameters||cal.params;const changes=Object.entries(result?.bestParameters||{}).filter(([key,value])=>value!==before[key]).map(([key,value])=>`${key}：${before[key]} → ${value}`);if(JSON.stringify(result?.bestRoutingParameters)!==JSON.stringify(reference?.bestRoutingParameters))changes.push('汇流参数有调整，可在原条件中查看');return changes.join('；')||'与参考产汇流参数相同'}
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
  trialReady.value=false; trialMode.value = 'automatic'; preparationIssues.value = []; calculationError.value = ''; inspectedResult.value = null
  savedParameterId.value = null; savedParameterName.value = ''; referenceParameterId.value = ''
  eventScope.value = selectedEventRows.value.length ? 'selected' : cal.deliverySelection.length ? 'delivery' : 'all'
  autoSplitPreview.value = null
  if (cal.activeOperationId) await cal.awaitOperation(cal.activeOperationId, cal.activeOperationRequest)
  else if (cal.modelSchemeId) await loadSchemeCalculations()
  const saved=await modelApi('model-schemes/'+cal.modelSchemeId)
  adoptedParameter.value=saved.adoptedParameter
  const temporary=readTrialWorkspace(cal.contextKey,cal.modelSchemeId)
  const comparable=automaticCurves.value.filter(row=>resultProcessMatches(cal,trialResults[row.taskId],currentPeriodId.value)).at(-1)
  await loadTrialSource(adoptedParameter.value?'parameter:'+adoptedParameter.value.parameter_id:comparable?'result:'+comparable.taskId:'initial')
  if(temporary){trialParams.value=temporary.params;trialRouting.value=temporary.routing;trialSource.value=temporary.source;trialScope.value=temporary.scope||'current';trialBaseline.value=temporary.baseline||{params:clone(temporary.params),routing:clone(temporary.routing)}}
  trialReady.value=true
}
async function loadSchemeCalculations() {
  const schemeId = cal.modelSchemeId
  try {
    const payload = await modelApi('operations')
    const operations = (payload.operations || []).filter(row => row.status === 'completed' && (row.runConfig?.request?.scheme?.id === schemeId || row.runConfig?.request?.lineage?.schemeId===schemeId || row.lineage?.schemeId === schemeId))
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
    if (cal.modelSchemeId === schemeId) { cal.trialRecords = records; if(!automaticReference.value)automaticReference.value=records.filter(row=>!row.manual).at(-1)?.taskId||''; const last = records.at(-1); if (last) inspectedResult.value = trialResults[last.taskId]; cal.saveCurrentWorkspace() }
  } catch (failure) { calculationError.value = failure.message }
}
async function startCalculation() {
  if (!canRun.value) return
  calculationError.value = ''; preparationIssues.value = []; savedParameterName.value = ''; savedParameterId.value = null
  let request
  try {
    let options={}
    if(trialMode.value==='manual'){
      const rows=allEvents.value
      const source={...app, selectedRoutingVersionId:cal.schemeRoutingVersionId, selectedInputBundleVersionId:cal.schemeSourceBundleVersionId||app.selectedInputBundleVersionId}
      const editor={...cal,sourcePlan:null,sourcePlanRequestTemplate:null,params:trialParams.value,requiredInputBundleVersionId:null}
      request=buildCalibrationRequest({app:source,calibration:editor,operationMode:'calibration',events:rows,eventAssignment:null})
      request.routing={versionId:cal.schemeRoutingVersionId}
      request.inputs.bundleVersionId=cal.schemeSourceBundleVersionId||request.inputs.bundleVersionId
      request.inputs.rainfallMethod=cal.rainfallMethod
      request.inputs.dataMode=cal.schemeDataMode
      request.inputs.stationNetwork=cal.schemeStationNetwork
      request.inputs.idwPower=cal.idwPower??2;request.inputs.minimumStations=cal.minimumStations??1;if(cal.rainfallMethod==='stationIdw')request.inputs.stationSourceId=cal.stationSourceId;else delete request.inputs.stationNetwork
      request=await prepareTrialInputs(cal,request)
      if(cal.scienceConditionsDraft){const science=await modelApi('science-conditions',{method:'POST',body:{...cal.scienceConditionsDraft,baseVersionId:cal.scienceVersionId||null}});request.science={versionId:science.scienceVersionId};request.inputs.evaporation={mode:'science-version',scope:'registered-scientific-configuration'};for(const key of ['scienceConfigurationSHA256','petConfigurationSHA256','scienceVersionId','modelImplementationSHA256'])delete request.lineage[key]}
      const fixed=fixedTrialRequest(request,{params:trialParams.value,scope:trialScope.value,processId:currentPeriodId.value,scheme:{id:cal.modelSchemeId,revision:cal.modelSchemeRevision},source:trialSource.value,draftRevision:cal.modelSchemeDraftRevision})
      request=fixed.request;options={rainfallMethod:request.inputs.rainfallMethod,trialContext:{...fixed.trialContext,usesDraft:Boolean(schemeBar.value?.hasChanges())},routingParameters:trialRouting.value}
    }else{
      if (!await schemeBar.value?.ensureSaved()) return
      const saved = await modelApi('model-schemes/' + cal.modelSchemeId + '?revision=' + cal.modelSchemeRevision)
      request=clone(saved.calculationDraft);request.requestId='CAL-'+crypto.randomUUID();request.scheme={id:saved.id,revision:saved.revision}
    }
    const outcome = await cal.runOperationRequest(request, trialMode.value === 'manual' ? 'manualTrial' : 'calibration',options)
    preparationIssues.value = outcome.issues || []
    if (!outcome.ok) { calculationError.value = outcome.errors?.join('；') || '计算未完成'; return }
    const primaryId = outcome.result.primaryTaskId || outcome.result.taskId
    if (primaryId) {trialResults[primaryId] = {...outcome.result,frozenRequest:outcome.result.frozenRequest||request};inspectedResult.value=trialResults[primaryId]}
    await loadSchemeCalculations()
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
    cal.rainfallMethod = 'stationIdw'
    makeSchemeEditable(cal)
    preparationIssues.value = []; calculationError.value = ''
    await startCalculation()
  } catch (failure) { calculationError.value = failure.message }
}
async function refreshNamedParameters() {
  try { namedParameters.value = ((await modelApi('parameter-schemes')).schemes || []).filter(row => !row.contextKey || row.contextKey === cal.contextKey) }
  catch (failure) { calculationError.value = failure.message }
}
async function applyOriginalBest() {
  parameterLoading.value = true
  try {
    if(trialMode.value==='manual'){
      const id=cal.originalCalibrationPlan.parameterVersionId
      const row=await modelApi('parameter-versions/'+encodeURIComponent(id)+'/original-plan?operation=fixed-replay')
      const routes=await modelApi('routing-versions')
      const routing=routes.versions.find(item=>item.routingVersionId===row.request.routing.versionId)?.parameters
      if(!routing)throw new Error('同事交付参数缺少汇流配置')
      trialParams.value=clone(row.request.calibration.initialParameters);trialRouting.value=clone(routing);trialSource.value='同事交付参数';trialBaseline.value={params:clone(trialParams.value),routing:clone(routing)}
    }else await adoptOriginalBestParameters(cal)
    calculationError.value = ''
  }
  catch (failure) { calculationError.value = failure.message }
  finally { parameterLoading.value = false }
}
async function applyCurrentPreparation() {
  parameterLoading.value = true
  try { await adoptHistoricalPreparation(cal); calculationError.value = '' }
  catch (failure) { calculationError.value = failure.message }
  finally { parameterLoading.value = false }
}
async function applyReference(id) {
  parameterLoading.value = true
  try {
    if (id === 'model-default') {
      cal.params = { ...app.backendCase.parameters }; cal.baseline = { ...cal.params }; cal.paramVersion = app.backendCase.parameterVersionId
      await useDefaultRouting(false)
    } else {
      const row = await modelApi('parameter-schemes/' + encodeURIComponent(id))
      cal.params = { ...row.parameters }; cal.baseline = { ...row.parameters }; cal.paramVersion = row.id
      cal.manualRoutingPayload=null
      cal.schemeRoutingVersionId = row.routing?.versionId || cal.schemeRoutingVersionId
      if (row.science?.versionId) {
        const adopt = loadPairedConditions.value
        if (adopt) {cal.scienceVersionId=row.science.versionId;cal.scienceDescriptor=row.scienceSnapshot?.manifest||null;cal.scienceConditionsDraft=null;cal.statePolicy=row.statePolicy||cal.statePolicy;cal.warmupDays=row.warmupDays??cal.warmupDays;cal.evaporationMode='science-version'}
      }
    }
    referenceParameterId.value = id; cal.requiredInputBundleVersionId = null
    cal.detachOriginalPlan(); cal.markNeedsRun(''); cal.saveCurrentWorkspace()
  } catch (failure) { calculationError.value = failure.message }
  finally { parameterLoading.value = false }
}
function applyBudget(budget) { cal.setOptimizerConfig('maxEvaluations', budget.n); cal.setOptimizerConfig('populationSize', budget.p) }
function applyRangeTemplate(id) {
  if (!id || id==='custom') return
  if (cal.calibrationTarget === 'routing') return
  const definitions = activeSearchDefinitions.value
  const ranges = applySearchRangeTemplate(null, definitions, id)
  cal.searchRangeOverrides = {...cal.searchRangeOverrides, ...ranges}
  cal.setSearchParameters(applySearchRangeTemplate(cal.searchParameters, definitions, id))
  rangeTemplate.value=id
  rangeTemplateNote.value='已填入搜索范围；初值和参与率定的勾选项保持不变。初值超界时请明确调整。'
}
function updateRanges(value) {cal.searchRangeOverrides=value;rangeTemplate.value='custom'}
function chooseTarget(id) {
  cal.setCalibrationTarget('joint')
  const bounds=rows=>Object.fromEntries(rows.filter(row=>!['KC','C'].includes(row.key)).map(row=>[row.key,cal.searchRangeOverrides[row.key]||{minimum:row.minimum,maximum:row.maximum}]))
  cal.setSearchParameters(id==='routing'?{}:bounds(jointDefinitions.value.runoff||[]))
  cal.setRoutingSearchParameters(id==='runoff'?{}:bounds(jointRoutingDefinitions.value))
}
function routingEdited() {cal.manualRoutingPayload=clone(manualRouting.value);cal.detachOriginalPlan();cal.markNeedsRun('汇流初值已修改');cal.saveCurrentWorkspace()}
async function useDefaultRouting(manageLoading=true) {
  if(manageLoading)parameterLoading.value=true
  try {const row=await modelApi('database/routing-defaults');if(trialMode.value==='manual'){trialRouting.value=clone(row.parameters);trialSource.value+=' · 默认汇流';return}cal.schemeRoutingVersionId=row.routingVersionId;manualRouting.value=clone(row.parameters);routingRecord.value=row;cal.manualRoutingPayload=clone(row.parameters);routingEdited()}
  catch(failure){calculationError.value=failure.message;throw failure}
  finally{if(manageLoading)parameterLoading.value=false}
}
function changeConditions(value) {
  cal.detachOriginalPlan();cal.scienceConditionsDraft=value;cal.statePolicy=value.statePolicy.strategy;cal.warmupDays=value.statePolicy.warmupHours/24;cal.candidateWarmupHours=null;cal.markNeedsRun('蒸散发与起算条件已修改');cal.saveCurrentWorkspace()
}
function updateInitialValue(key, text) {
  if(!String(text).trim() || !Number.isFinite(Number(text))) return
  const value=Number(text)
  if(key in cal.params) { cal.setParam(key,value); return }
  if(key === 'K_total_day' || key === 'groundwater_fraction') {
    const coordinates=searchCoordinates(cal.params)
    const daily=key === 'K_total_day' ? value : coordinates.K_total_day
    const fraction=key === 'groundwater_fraction' ? value : coordinates.groundwater_fraction
    if(daily<0 || daily>=1 || fraction<0 || fraction>1) { calculationError.value='日总出流系数须在0至1之间（不含1），地下水比例须在0至1之间'; return }
    const total=1-(1-daily)**(1/24)
    cal.setParam('KI', total*(1-fraction)); cal.setParam('KG',total*fraction); return
  }
  if(!manualRouting.value) return
  if(key.endsWith('_k_hours')) manualRouting.value.default_slope_k_hours[key.split('_')[0]]=value
  else if(key === 'muskingum_x') Object.values(manualRouting.value.reach_muskingum).forEach(row=>row.x=value)
  routingEdited()
}
async function renameParameter() {
  try {
    const selected = namedParameters.value.find(row=>row.id===referenceParameterId.value)
    const {value} = await ElMessageBox.prompt('填写参数名称', '修改参数名称', {inputValue:selected?.name || '',inputValidator:value=>Boolean(value?.trim()) && value.trim().length<=80})
    await modelApi('parameter-schemes/'+referenceParameterId.value+'/rename',{method:'POST',body:{name:value.trim()}})
    await refreshNamedParameters()
  } catch(failure) { if(failure instanceof Error) calculationError.value=failure.message }
}
async function commitScheme() {
  if (parameterLoading.value) throw new Error('正在读取初始参数，请稍候再保存')
  if (cal.scienceConditionsDraft) {
    const descriptor=await modelApi('science-conditions',{method:'POST',body:{...cal.scienceConditionsDraft,baseVersionId:cal.scienceVersionId||null}})
    cal.scienceVersionId=descriptor.scienceVersionId;cal.scienceDescriptor=descriptor;cal.evaporationMode='science-version';cal.scienceConditionsDraft=null
  }
  if (manualRouting.value && JSON.stringify(manualRouting.value) !== JSON.stringify(routingRecord.value?.parameters)) {
    const routing = await modelApi('routing-versions', {method:'POST',body:{name:cal.modelSchemeName+' · 人工试调',parentVersionId:cal.schemeRoutingVersionId || 'control-split-v2',parameters:manualRouting.value}})
    cal.schemeRoutingVersionId = routing.routingVersionId
    routingRecord.value = routing
  }
  const request = buildCalibrationRequest({app,calibration:{...cal,requiredInputBundleVersionId:null},operationMode:'calibration',events:allEvents.value,eventAssignment:cal.eventAssignment})
  return saveModelScheme(cal,request)
}
async function openSaveParameters() { parameterName.value=cal.modelSchemeName+' · '+(displayResult.value?.evaluationMode==='manual'?'试调参数':'率定参数');parameterSaveError.value=''; const scheme=await modelApi('model-schemes/'+cal.modelSchemeId);adoptedParameter.value=scheme.adoptedParameter;parameterSaveRequest.value={requestId:uniqueRequestId('PARAM'),sourceTaskId:displayResult.value.primaryTaskId||displayResult.value.taskId,schemeId:cal.modelSchemeId,adoptionRevision:adoptedParameter.value?.revision||0};parameterDialog.value.showModal() }
function simulateBest(){simulateAfterSave.value=true;openSaveParameters()}
async function rerunResult(){
  try {
    const source=displayResult.value.primaryTaskId || displayResult.value.taskId
    const operation=await modelApi('operations',{method:'POST',body:{operation:'rerun',sourceTaskId:source,name:cal.modelSchemeName+' · 原条件复算'}})
    cal.activeOperationId=operation.taskId
    const outcome=await cal.awaitOperation(operation.taskId,displayResult.value.frozenRequest)
    if(!outcome.ok)calculationError.value=outcome.errors.join('；')
    else await loadSchemeCalculations()
  }catch(failure){calculationError.value=failure.message}
}
async function saveParameters() {
  if (!parameterName.value.trim() || !displayResult.value) return
  parameterSaving.value = true; parameterSaveError.value = ''
  try {
    const row = await modelApi('parameter-schemes', { method: 'POST', body: { ...parameterSaveRequest.value,name: parameterName.value.trim() } })
    savedParameterId.value = row.id; savedParameterName.value = row.name
    if(row.adoptionError){parameterSaveError.value=row.adoptionError;const scheme=await modelApi('model-schemes/'+cal.modelSchemeId);parameterSaveRequest.value.adoptionRevision=scheme.adoptedParameter?.revision||0;return}
    adoptedParameter.value=row.adoptedParameter; parameterDialog.value.close(); await refreshNamedParameters()
    if(simulateAfterSave.value){simulateAfterSave.value=false;goSimulation()}
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
  router.push({ path: '/results/'+(result.operationId || result.primaryTaskId || result.taskId)+'/sections', query:{process:currentPeriodId.value} })
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
    if (cal.schemeDataMode !== 'historical-database' && cal.rainfallMethod === 'reuse') {
      const source = { ...app.activeContext, selectedInputBundleVersionId: cal.schemeSourceBundleVersionId || app.selectedInputBundleVersionId, inputBundleVersionId: cal.schemeSourceBundleVersionId || app.selectedInputBundleVersionId }
      const input = await loadModelInputWindow(event.start, event.end, { source })
      if (sequence === previewSequence) preview.value = { ...observedModelProcess(input, event.start, event.end), statistics: modelEventStatistics(input, event.start, event.end) }
    } else {
      const input = await loadHistoricalDatabaseProcess(event.start, event.end, { source: app.activeContext })
      if (sequence === previewSequence) {
        preview.value = { ...observedModelProcess(input, event.start, event.end), statistics: modelEventStatistics(input, event.start, event.end) }
        if (input.quality?.gridError) previewError.value = '实测已读取；网格尚未生成：' + input.quality.gridError
      }
    }
  } catch (failure) { if (sequence === previewSequence) previewError.value = failure.message }
  finally { if (sequence === previewSequence) previewLoading.value = false }
}
watch(eventSort,value=>{eventSortKey.value=value.startsWith('peak')?'peak':'start';eventSortDirection.value=value.endsWith('desc')?'desc':'asc'})
watch([eventQuery, eventYear, purposeFilter, eventScope, peakMin, peakMax, eventSort,eventSortKey,eventSortDirection], () => { eventPage.value = 1 })
watch(() => JSON.stringify([cal.modelSchemeId, cal.eventSplitSeed, selectedEventRows.value.map(row => [row.id, row.start, row.end, row.purpose, row.observedPeak])]), () => { autoSplitPreview.value = null }, { flush: 'sync' })
watch(pageCount, count => { eventPage.value = Math.min(eventPage.value, count) })
watch(continuousPeriods, periods => { if (!periods.some(row => row.id === selectedContinuousPeriod.value)) selectedContinuousPeriod.value = periods[0]?.id || 'CALIBRATION' })
watch(() => [schemeOpened.value, cal.calibrationMode, activeEvent.value?.id, activeEvent.value?.start, activeEvent.value?.end, cal.schemeSourceBundleVersionId, cal.rainfallMethod, currentHydrograph.value?.forecast?.length], readPreview)
watch(() => [app.backendCase, cal.schemeRoutingVersionId], async (_, __, onCleanup) => {
  let current = true
  onCleanup(() => { current = false })
  routingError.value = ''
  routingRecord.value=null;manualRouting.value=null
  if (!app.backendCase) return
  try {
    const id = cal.schemeRoutingVersionId || (await modelApi('database/routing-defaults')).routingVersionId
    const payload = await modelApi('routing-versions')
    if (!current) return
    routingRecord.value = (payload.versions || []).find(row => row.routingVersionId === id || row.id === id)
    if (!routingRecord.value) throw new Error('本方案缺少汇流参数，请使用默认参数或采用原率定最佳参数')
    manualRouting.value = JSON.parse(JSON.stringify(cal.manualRoutingPayload || routingRecord.value.parameters))
  }
  catch (failure) { if (current) routingError.value = failure.message }
}, { immediate: true })
watch(() => cal.runResult, result => {
  if (result?.taskId) trialResults[result.taskId] = result
  inspectedResult.value = null
  if(result)inspectedResult.value=result
})
watch(() => [app.basinId, app.modelScale, app.modelConfigId], ([basinId, modelScale, configId]) => { cal.setContext(basinId, modelScale, configId); refreshNamedParameters() })
onMounted(refreshNamedParameters)
</script>

<style scoped>
.calibration-page{display:grid;gap:14px;min-height:0;color:#344e47;font-size:14px}
.calibration-page :is(.btn,.input,.text-button){min-height:36px;font-size:14px}
.calibration-page :is(label,p,small,summary,td,th){font-size:14px}
.workbench-toolbar{display:flex;align-items:center;gap:10px;padding:12px 16px;border:1px solid #d8e4df;background:#fff;border-radius:4px}
.period-switch,.work-switch{display:flex;gap:3px;padding:3px;background:#eff4f1;border-radius:4px}
.period-switch button,.work-switch button{min-height:36px;padding:8px 14px;border:0;background:none;border-radius:3px;color:#698178;font-size:14px;cursor:pointer}
.period-switch button.active,.work-switch button.active{background:#fff;color:#236452;box-shadow:0 1px 3px #1a453014;font-weight:600}
.work-switch{margin-right:auto}
.workbench{display:grid;grid-template-columns:450px minmax(0,1fr);gap:14px;align-items:start}
.workbench.manual-mode{grid-template-columns:390px minmax(0,1fr) minmax(310px,360px)}
.panel{border:1px solid #d8e4df;background:#fff;border-radius:4px;overflow:hidden}
.panel-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:15px 16px;border-bottom:1px solid #e5ede9}
.panel-heading h2{font-size:16px;font-weight:600;margin:0;color:#294f43}
.panel-heading>span{font-size:14px;color:#82918b}
.event-browser-bar{display:flex;align-items:center;gap:10px;padding:10px 14px 0}
.event-scope{display:flex;gap:4px}
.event-scope button{min-height:36px;border:1px solid #cddbd3;background:#fff;border-radius:4px;padding:7px 12px;color:#58776a;font-size:14px;cursor:pointer}
.event-scope button.active{background:#eaf4ee;color:#236452;border-color:#8db4a1;font-weight:600}
.event-scope-count{margin-left:auto;color:#83958b;font-size:14px}
.event-filters{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px 14px 10px}
.event-filters input:first-child{grid-column:1/-1}
.selection-counts{display:flex;gap:18px;padding:0 14px 12px;color:#83928b}
.selection-counts b{color:#325d4d;padding-left:3px}
.batch-actions{display:flex;align-items:center;gap:7px;padding:10px 14px;border-top:1px solid #e4ece8;background:#f8fbf9;font-size:14px;flex-wrap:wrap}
.batch-actions label{margin-right:auto;display:flex;align-items:center;gap:5px}
.batch-actions select{max-width:100px}
.split-preview{margin:0 14px 10px;padding:10px 12px;border:1px solid #cfe1d6;border-radius:4px;background:#f4faf6;color:#4a6b5b}
.split-preview>div{display:flex;justify-content:space-between;gap:10px}
.split-preview span{color:#70897c}
.split-preview p{margin:7px 0 9px;line-height:1.5}
.split-preview .split-names{display:grid;justify-content:normal;grid-template-columns:70px minmax(0,1fr)}
.split-names{gap:8px;line-height:1.7}
.split-names span{overflow-wrap:anywhere}
.flood-table{max-height:min(570px,55vh);overflow:auto;overscroll-behavior:contain}
.flood-table-head,.flood-row{display:grid;grid-template-columns:28px minmax(0,1fr) 82px 100px;align-items:center;gap:5px;padding:0 12px;font-size:14px}
.flood-table-head{position:sticky;top:0;z-index:2;min-height:46px;background:#edf4ef;border-top:1px solid #e0e9e4;color:#617d70}
.time-column-head{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;padding:8px 0}
.flood-row{min-height:78px;border-top:1px solid #e8eeea}
.flood-row.current{background:#edf5f0;border-left:3px solid #3b8267;padding-left:9px}
.flood-process{padding:10px 0;background:none;border:0;text-align:left;cursor:pointer;min-width:0;display:grid;gap:4px;color:#355e4d}
.flood-process b{font-size:14px;font-weight:600;overflow-wrap:anywhere}
.flood-process small{color:#718578;font-size:14px}
.flood-process em{color:#a54a37;font-style:normal;font-size:14px}
.purpose-select{padding:4px 5px;min-height:36px}
.pagination{display:flex;align-items:center;gap:6px;padding:12px 14px;border-top:1px solid #e1e9e5}
.pagination span{margin-right:auto;color:#718578}
.custom-window{padding:12px 14px;border-top:1px solid #e1e9e5}
.custom-window summary{cursor:pointer;color:#70877b}
.custom-window .field{margin-top:10px}
.process-workspace{display:grid;gap:14px;min-width:0}
.process-panel{height:470px;display:flex;flex-direction:column;min-width:0}
.process-heading{padding-bottom:10px}
.process-heading div>span{display:block;color:#7f9389;margin-top:6px}
.process-heading .period-select{max-width:180px}
.process-panel :deep(.hydrology-figure){flex:1;min-height:0;padding:4px 8px}
.process-empty{display:grid;place-content:center;flex:1;color:#7f9389}
.process-status{padding:0 16px;color:#718578}
.result-panel{padding-bottom:6px}
.metric-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:18px 16px}
.metric-row>div{display:grid;gap:8px}
.metric-row span{color:#718578}
.metric-row b{font-size:19px;font-weight:600;color:#335b49}
.evaluation-table{width:calc(100% - 32px);margin:0 16px 10px;border-collapse:collapse;text-align:right}
.evaluation-table th,.evaluation-table td{padding:10px 7px;border-top:1px solid #e7eeea;font-weight:400}
.evaluation-table th:first-child{text-align:left}
.evaluation-table thead{color:#718578}
.result-stamp{margin:8px 16px;color:#718578}
.result-view-actions{display:flex;align-items:center;gap:12px;margin:12px 0;color:#365c4b;flex-wrap:wrap}
.result-view-actions>span{margin-right:auto}
.calculation-history{padding:12px 16px}
.calculation-history summary{cursor:pointer;color:#70857a}
.history-row{display:flex;justify-content:space-between;gap:18px;width:100%;margin-top:7px;border:0;border-bottom:1px solid #edf1ef;background:none;padding:10px 0;cursor:pointer;text-align:left;color:#6e8578;font-size:14px}
.history-row b{font-weight:500;color:#2e795b}
.parameter-panel{padding:0 14px 14px}
.parameter-panel .panel-heading{padding-left:0;padding-right:0;margin-bottom:12px}
.parameter-panel fieldset{border:0;padding:0;margin:0;min-width:0}
.parameter-panel :deep(.param-list){max-height:630px}
.parameter-panel :deep(.param-row){background:#f8fbf8;border-color:#d7e4db;border-radius:4px}
.parameter-panel :deep(.num){color:#285f49;border-color:#bdcfc3;font-size:14px;min-height:36px}
.parameter-panel :deep(.range){accent-color:#357e68}
.parameter-panel :deep(.param-meta label){color:#365b49;font-size:14px}
.parameter-panel :deep(label),.parameter-panel :deep(input),.parameter-panel :deep(button){font-size:14px}
.parameter-panel .block{margin-top:14px}
.routing-tuner{padding:0;border:0;display:grid;gap:12px;margin:20px 0}
.routing-tuner>label{display:flex;justify-content:space-between;align-items:center;gap:10px}
.routing-tuner>label .input{width:95px}
.routing-tuner details{margin-top:12px}
.routing-tuner details label{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}
.routing-tuner details label>b{grid-column:1/-1}
.routing-tuner details input{width:100%}
.routing-tuner h4{font-size:14px;color:#52715f}
.routing-repair,.scope-actions{display:flex;gap:10px;flex-wrap:wrap}
.routing-problems{font-size:14px;max-height:300px;overflow:auto}
.block{display:block;width:100%}
.field{display:grid;gap:6px}
.field label{color:#647e6d}
.field-error{margin:10px 14px;color:#a44735;line-height:1.6}
.period-block{padding:16px;border-bottom:1px solid #e6ede8}
.period-block h3{font-size:14px;font-weight:600;margin:0 0 12px;color:#426a55}
.period-block .field+.field{margin-top:10px}
.calculation-status{padding:14px 18px;background:#fff;border:1px solid #dbe6df;border-radius:4px}
.progress-row{display:flex;justify-content:space-between;color:#4e7962}
.progress-row b{font-weight:500}
.calculation-status progress{width:100%;height:5px;margin-top:10px;accent-color:#3e8b69}
.calculation-status p{color:#a44a36;margin:4px 0}
.issue-list{display:grid;gap:8px;margin-top:10px}
.issue-list>div{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;color:#8b6151}
.issue-list small{grid-column:2}
.issue-list .btn{justify-self:start}
.empty{text-align:center;padding:30px;color:#718578}
.text-button{border:0;background:none;color:#397b5e;cursor:pointer;padding:0}
.dialog-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}
.dialog-heading h2{margin:0;font-size:20px}
.settings-summary{display:flex;justify-content:space-between;gap:16px;color:#718578;margin-bottom:18px}
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-bottom:18px}
.settings-grid .btn-row{display:flex;flex-wrap:wrap;gap:6px}
.settings-section-title{font-size:14px;font-weight:600;margin:16px 0 8px}
.objective-list{display:grid;grid-template-columns:1fr 1fr;gap:8px 28px}
.objective-list label{display:flex;align-items:center;justify-content:space-between}
.objective-list input[type=number]{width:65px;padding:5px;border:1px solid #cbd9cf;border-radius:3px}
.advanced-settings{margin-top:20px;border-top:1px solid #e2ebe5;padding-top:14px}
.advanced-settings summary{color:#718578;cursor:pointer;margin-bottom:14px}
.parameter-dialog label{display:block;margin-bottom:7px}
.parameter-dialog .block{margin-top:16px}
.parameter-kind{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px}
.settings-note{color:#667d70;font-size:14px;line-height:1.7}
.adopted-parameters{display:flex;align-items:center;flex-wrap:wrap;gap:12px;padding:14px 16px;background:#f2f8f5;border:1px solid #d8e4df}
.adopted-parameters span{margin-right:auto;color:#5e786a}
.curve-controls{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:12px 0;font-size:14px}
.curve-controls label{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.curve-controls i{display:inline-block;width:20px;height:3px}
.curve-controls small{color:#647c74}
.curve-controls select{max-width:300px}
.curve-entry{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:8px 0;border-top:1px solid #e5ede9;width:100%}
.curve-entry p{max-width:600px;overflow-wrap:anywhere}
@media(max-width:1450px){
  .workbench.manual-mode{grid-template-columns:390px minmax(0,1fr)}
  .manual-mode .parameter-panel{grid-column:1/-1}
  .manual-mode .parameter-panel :deep(.param-list){display:grid;grid-template-columns:repeat(3,minmax(0,1fr));max-height:450px}
}
@media(max-width:1100px){.workbench{grid-template-columns:390px minmax(0,1fr)}}
@media(max-width:900px){
  .workbench,.workbench.manual-mode{grid-template-columns:1fr}
  .workbench-toolbar{flex-wrap:wrap}
  .work-switch{margin-right:0}
  .process-panel{height:420px}
  .manual-mode .parameter-panel :deep(.param-list){grid-template-columns:1fr 1fr}
  .settings-grid{grid-template-columns:1fr}
  .metric-row{grid-template-columns:1fr 1fr}
  .issue-list>div{grid-template-columns:1fr}
  .issue-list small{grid-column:auto}
  .settings-summary{display:block}
}
</style>
