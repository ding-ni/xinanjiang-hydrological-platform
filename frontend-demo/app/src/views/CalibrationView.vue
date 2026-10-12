<template>
  <section class="view-cal">
    <div class="metric-strip" aria-label="率定与模拟概况">
      <div class="metric"><span>当前参数方案</span><b :title="cal.paramVersion">{{ parameterVersionLabel(cal.paramVersion) }}</b></div>
      <div class="metric"><span>{{ workMode === 'simulation' ? '模拟对象' : '率定断面' }}</span><b>{{ cal.section }}</b></div>
      <div class="metric"><span>计算尺度</span><b>{{ spatialMetricLabel }}</b></div>
      <div class="metric emphasis"><span>{{ isNananxiHistory ? '实测洪峰' : '纳什效率系数（NSE）' }}</span><b class="hot">{{ isNananxiHistory ? `${activeFlood?.observedPeak ?? '—'} m³/s` : nseLabel }}</b></div>
      <div class="metric"><span>{{ workMode === 'simulation' ? '模拟时段' : calMode === 'continuous' ? '率定时段' : '洪水场次' }}</span><b>{{ workMode === 'simulation' ? `${cal.simulationStart.slice(0, 10)} 至 ${cal.simulationEnd.slice(0, 10)}` : calMode === 'continuous' ? `${cal.calibrationStart.slice(0, 10)} 至 ${cal.calibrationEnd.slice(0, 10)}` : eventSetSummary }}</b></div>
      <div class="metric"><span>作业状态</span><b :class="statusClass">{{ isNananxiHistory && !app.backendCase ? '率定条件准备' : cal.calibrationStatusLabel }}</b></div>
      <ContextStatusMetric />
    </div>

    <div class="operation-tabs panel" aria-label="率定与模拟类型">
      <button type="button" :class="{ active: workMode === 'simulation' }" @click="setWorkMode('simulation')"><b>连续模拟</b><span>按时段运行并生成末状态</span></button>
      <button type="button" :class="{ active: workMode === 'calibration' }" @click="setWorkMode('calibration')"><b>参数率定</b><span>连续时段或洪水事件率定</span></button>
      <p v-if="calculationHandoff" class="handoff-note">条件来自：{{ calculationHandoff.sourceLabel }}，已恢复原作业设置，需复核后重新执行。</p>
    </div>

    <div class="calibration-layout" :class="{ 'simulation-mode': workMode === 'simulation' }">
      <aside class="panel panel-pad setting-panel">
        <h2 class="panel-title">{{ workMode === 'simulation' ? '连续模拟设置' : '率定设置' }}</h2>
        <p class="intro">{{ workMode === 'simulation' ? '设置时段、初始状态和输出步长。' : calMode === 'continuous' ? '设置评价时段与参数搜索方案。' : '选择断面、洪水场次和评价指标。' }}</p>

        <template v-if="workMode === 'calibration'">
        <div class="mode-tabs" aria-label="率定方式">
          <button type="button" :class="{ active: calMode === 'event' }" @click="calMode = 'event'">洪水场次</button>
          <button type="button" :class="{ active: calMode === 'continuous' }" @click="calMode = 'continuous'">连续时段</button>
        </div>

        <div class="form-group-title">资料与对象</div>
        <div class="data-list" aria-label="率定资料">
          <div><span>水文资料</span><b>{{ activeDataset.hydrology }}</b></div>
          <div><span>降水资料</span><b>{{ activeDataset.precipitation }}</b></div>
          <div><span>预热期</span><span class="data-inline-control"><input class="data-inline-input" type="number" min="1" :value="cal.warmupDays" aria-label="预热期天数" @change="cal.setSimulationField('warmupDays', $event.target.value)" /> 天</span></div>
          <div v-if="app.basinId === 'nananxi-xufan'"><span>蒸散发</span><b>{{ calMode === 'event' ? '0 mm/h' : '逐月给定值' }}</b></div>
        </div>

        <div class="field">
          <label for="cal-section">率定对象</label>
          <select id="cal-section" class="input" :value="cal.section" @change="cal.setSection($event.target.value)">
            <option v-for="name in calSections" :key="name" :value="name">{{ name }}</option>
          </select>
        </div>
        <button v-if="app.hasLakeBalance && cal.section !== '色林错全湖'" class="btn lake-shortcut" type="button" @click="selectWholeLake">采用全湖水量平衡</button>
        <div v-if="app.hasLakeBalance" class="field">
          <label for="cal-basis">拟合依据</label>
          <select id="cal-basis" class="input" :value="cal.targetMode" @change="cal.setTargetMode($event.target.value)">
            <option value="section-flow">入湖断面流量</option>
            <option value="lake-balance">湖位反演入湖水量</option>
            <option value="joint">断面流量与湖位联合评价</option>
          </select>
          <small class="field-note">湖位评价以全湖水量平衡为对象，兼顾各入湖断面过程。</small>
        </div>
        <div class="field">
          <label for="cal-version">参数方案</label>
          <select id="cal-version" class="input" :value="cal.paramVersion" @change="cal.loadVersion($event.target.value)">
            <option v-for="version in versionOptions" :key="version" :value="version">{{ parameterVersionLabel(version) }}</option>
          </select>
        </div>

        <template v-if="calMode === 'event'">
          <div class="form-group-title">洪水场次</div>
          <div class="event-counts" aria-label="洪水场次统计">
            <div><span>率定</span><b>{{ calibrationEvents.length }} 场</b></div>
            <div><span>验证</span><b>{{ validationEvents.length }} 场</b></div>
            <div><span>独立检验</span><b>{{ independentEvents.length }} 场</b></div>
          </div>
          <p class="section-note">先在下方洪水台账设置用途，再执行率定。</p>
          <div class="field">
            <label for="cal-event">当前过程</label>
            <select id="cal-event" class="input" :value="cal.eventId" @change="selectFloodById($event.target.value)">
              <option v-for="event in activeEvents" :key="event.id" :value="event.id">{{ floodDisplayName(event) }}</option>
            </select>
          </div>
          <div v-if="activeFlood" class="selected-event">
            <span>{{ formatEventPeriod(activeFlood) }}</span>
            <b>{{ activeFlood.note }}</b>
          </div>
        </template>

        <template v-else>
          <div class="form-group-title">连续时段</div>
          <div class="period-fields">
            <div class="field"><label for="cal-start">率定期开始</label><input id="cal-start" class="input" type="datetime-local" :value="cal.calibrationStart" @change="cal.setPeriod('calibrationStart', $event.target.value)" /></div>
            <div class="field"><label for="cal-end">率定期结束</label><input id="cal-end" class="input" type="datetime-local" :value="cal.calibrationEnd" @change="cal.setPeriod('calibrationEnd', $event.target.value)" /></div>
            <div class="field"><label for="val-start">验证期开始</label><input id="val-start" class="input" type="datetime-local" :value="cal.validationStart" @change="cal.setPeriod('validationStart', $event.target.value)" /></div>
            <div class="field"><label for="val-end">验证期结束</label><input id="val-end" class="input" type="datetime-local" :value="cal.validationEnd" @change="cal.setPeriod('validationEnd', $event.target.value)" /></div>
            <div class="field"><label for="ind-start">独立检验开始（可选）</label><input id="ind-start" class="input" type="datetime-local" :value="cal.independentStart" @change="cal.setPeriod('independentStart', $event.target.value)" /></div>
            <div class="field"><label for="ind-end">独立检验结束</label><input id="ind-end" class="input" type="datetime-local" :value="cal.independentEnd" @change="cal.setPeriod('independentEnd', $event.target.value)" /></div>
          </div>
          <p class="section-note">各时段独立预热，预热段不参与评价；仅率定期参与参数寻优。</p>
        </template>

        <div v-if="showLakeBalance" class="lake-balance">
          <div class="lake-head"><b>湖泊水量平衡</b><span>当前过程</span></div>
          <div class="lake-values">
            <div><span>四口入湖总流量</span><b>451 m³/s</b></div>
            <div><span>时段湖位变化</span><b>+0.06 m</b></div>
            <div><span>湖面降水</span><b>4.6 mm</b></div>
            <div><span>水量平衡误差</span><b>-2.8%</b></div>
          </div>
          <p>用于检验入湖水量与湖位变化是否协调。</p>
        </div>

        <div class="form-group-title">拟合评价</div>
        <div class="objective-list">
          <label class="objective-row"><span><input type="checkbox" :checked="cal.criteria.metrics.nse" @change="cal.setObjectiveMetric('nse', $event.target.checked)" /> 纳什效率系数（NSE）</span><span>权重 <input :value="cal.criteria.weights.nse" type="number" min="0" max="1" step="0.05" @change="cal.setObjectiveWeight('nse', $event.target.value)" /></span></label>
          <label class="objective-row"><span><input type="checkbox" :checked="cal.criteria.metrics.peak" @change="cal.setObjectiveMetric('peak', $event.target.checked)" /> 洪峰相对误差</span><span>权重 <input :value="cal.criteria.weights.peak" type="number" min="0" max="1" step="0.05" @change="cal.setObjectiveWeight('peak', $event.target.value)" /></span></label>
          <label class="objective-row"><span><input type="checkbox" :checked="cal.criteria.metrics.time" @change="cal.setObjectiveMetric('time', $event.target.checked)" /> 峰现时间误差</span><span>权重 <input :value="cal.criteria.weights.time" type="number" min="0" max="1" step="0.05" @change="cal.setObjectiveWeight('time', $event.target.value)" /></span></label>
          <label class="objective-row"><span><input type="checkbox" :checked="cal.criteria.metrics.volume" @change="cal.setObjectiveMetric('volume', $event.target.checked)" /> 水量相对误差</span><span>权重 <input :value="cal.criteria.weights.volume" type="number" min="0" max="1" step="0.05" @change="cal.setObjectiveWeight('volume', $event.target.value)" /></span></label>
        </div>
        <p class="weight-status" :class="{ warn: !objectiveWeightValid }">已选指标权重合计 {{ objectiveWeightTotal.toFixed(2) }}，应为 1.00。</p>
        <div class="threshold-grid">
          <div class="field"><label for="peak-tolerance">洪峰误差阈值</label><input id="peak-tolerance" class="input" type="number" :value="cal.criteria.tolerances.peakPercent" @change="cal.setObjectiveTolerance('peakPercent', $event.target.value)" /></div>
          <div class="field"><label for="time-tolerance">峰现误差阈值</label><input id="time-tolerance" class="input" type="number" :value="cal.criteria.tolerances.peakTimeHours" @change="cal.setObjectiveTolerance('peakTimeHours', $event.target.value)" /></div>
        </div>
        <details v-if="app.backendCase && cal.calibrationTarget !== 'routing'" class="method-note"><summary>评价与参数换算</summary>合格率按 NSE≥0.5 统计；洪峰与峰时误差单列。搜索中 KC、C 与河网参数固定；KI/KG 由日退水参数换算。</details>

        <div class="form-group-title">搜索设置</div>
        <div v-if="app.backendCase?.routingSearchParameterDefinitions" class="field">
          <label for="calibration-target">率定参数组</label>
          <select id="calibration-target" class="input" :value="cal.calibrationTarget" :disabled="cal.running" @change="cal.setCalibrationTarget($event.target.value)"><option value="runoff">产流参数</option><option value="routing">坡面汇流参数</option></select>
        </div>
        <div v-if="app.backendCase && cal.calibrationTarget !== 'routing'" class="field">
          <label for="search-strategy">搜索安排</label>
          <select id="search-strategy" class="input" :value="cal.searchStrategy" @change="cal.setOptimizerConfig('searchStrategy', $event.target.value)"><option value="joint">所选参数联合搜索</option><option value="staged">分阶段：蓄水产流 → 水源划分</option></select>
          <small v-if="cal.searchStrategy === 'staged'" class="field-note">总评价预算分配至两个阶段，后一阶段承接前一阶段最优参数。</small>
        </div>
        <div class="field">
          <label for="algorithm">搜索方法</label>
          <select id="algorithm" class="input" :value="cal.algorithm" @change="cal.setOptimizerConfig('algorithm', $event.target.value)">
            <option v-for="item in algorithms" :key="item.id" :value="item.id">{{ item.label }}</option>
          </select>
        </div>
        <div class="optimizer-grid">
          <div v-if="app.backendCase" class="field"><label for="search-seed">搜索随机种子</label><input id="search-seed" class="input" type="number" min="0" max="4294967295" :value="cal.searchSeed" @change="cal.setOptimizerConfig('searchSeed', $event.target.value)" /></div>
          <div class="field"><label for="max-evals">评价次数</label><input id="max-evals" class="input" type="number" :min="app.backendCase ? 1 : 200" :step="app.backendCase ? 1 : 100" :value="cal.maxEvaluations" @change="cal.setOptimizerConfig('maxEvaluations', $event.target.value)" /></div>
          <div class="field"><label for="population-size">种群规模</label><input id="population-size" class="input" type="number" :min="app.backendCase ? 4 : 8" step="4" :value="cal.populationSize" @change="cal.setOptimizerConfig('populationSize', $event.target.value)" /></div>
        </div>
        <p class="method-note">{{ activeAlgorithm?.note }}</p>

        <div class="action-row">
          <button class="btn primary" type="button" :disabled="!calculationCanRun" @click="startCalibration">{{ cal.running ? '正在提交' : (app.basinId === 'nananxi-xufan' ? '确认率定任务' : '开始率定') }}</button>
          <button v-if="cal.running" class="btn" type="button" @click="cal.cancelCalibration()">停止任务</button>
          <button v-else class="btn" type="button" @click="cal.resetParams()">恢复参数方案</button>
        </div>
        <p v-if="!calculationCanRun && !cal.running" class="run-message warn">{{ calculationBlockReason }}</p>
        <p v-if="requestStatus" class="run-message request-status">{{ requestStatus }}</p>
        </template>

        <template v-else>
          <div class="form-group-title">资料与对象</div>
          <div class="data-list">
            <div><span>输入资料</span><b>{{ activeDataset.hydrology }}</b></div>
            <div><span>降水驱动</span><b>{{ activeDataset.precipitation }}</b></div>
            <div><span>蒸散发</span><b>{{ app.backendCase ? '逐月蒸散发假设 · 非实测' : app.basinId === 'nananxi-xufan' ? '连续资料待补充' : '随资料目录读取' }}</b></div>
          </div>
          <div class="field"><label for="simulation-section">模拟对象</label><select id="simulation-section" class="input" :value="cal.section" @change="cal.setSection($event.target.value)"><option v-for="name in calSections" :key="name" :value="name">{{ name }}</option></select></div>
          <div class="field"><label for="simulation-start">开始时间</label><input id="simulation-start" class="input" type="datetime-local" step="3600" :value="cal.simulationStart" @change="cal.setSimulationField('simulationStart', $event.target.value)" /></div>
          <div class="field"><label for="simulation-end">结束时间</label><input id="simulation-end" class="input" type="datetime-local" step="3600" :value="cal.simulationEnd" @change="cal.setSimulationField('simulationEnd', $event.target.value)" /></div>
          <div class="field"><label for="simulation-warmup">预热期（天）</label><input id="simulation-warmup" class="input" type="number" min="1" :value="cal.warmupDays" @change="cal.setSimulationField('warmupDays', $event.target.value)" /></div>
          <div class="field"><label for="simulation-state">初始状态版本</label><input id="simulation-state" class="input" :value="cal.initialStateVersionId" @change="cal.setSimulationField('initialStateVersionId', $event.target.value)" /></div>
          <div class="field"><label for="simulation-step">输出时间步长</label><select id="simulation-step" class="input" :value="cal.outputStepHours" @change="cal.setSimulationField('outputStepHours', $event.target.value)"><option :value="1">1小时</option><option v-if="!app.backendCase" :value="3">3小时</option><option v-if="!app.backendCase" :value="6">6小时</option></select></div>
          <div v-if="cal.visibleSimulationResult" class="simulation-result-card">
            <span>末时刻状态版本</span><b>{{ cal.visibleSimulationResult.endStateVersionId }}</b>
            <small>{{ cal.visibleSimulationResult.completedAt }} · 可作为下一时段起算状态</small>
            <small v-if="cal.visibleSimulationResult.isCurrent === false">条件已修改：此状态和结果对应上次冻结请求，不是当前方案。</small>
            <small v-if="cal.visibleSimulationResult.resultSource === 'model-backend'">计算资料：{{ frozenInputBundleVersionId(cal.visibleSimulationResult) }}</small>
            <button class="btn" type="button" @click="saveSimulationState">保存为起算状态</button>
          </div>
          <div class="action-row">
            <button class="btn primary" type="button" :disabled="!calculationCanRun" @click="startSimulation">{{ cal.running ? '正在提交' : (app.basinId === 'nananxi-xufan' ? '确认模拟任务' : '开始连续模拟') }}</button>
            <button v-if="cal.running" class="btn" type="button" @click="cal.cancelCalibration()">停止任务</button>
          </div>
          <p v-if="!calculationCanRun && !cal.running" class="run-message warn">{{ calculationBlockReason }}</p>
        </template>
        <TaskStatusPanel :task="activeTask" />
        <p class="run-message" :class="{ warn: cal.runStatus === 'invalid' }" aria-live="polite">{{ cal.runMessage }}</p>
      </aside>

      <main class="analysis-column">
        <section class="panel panel-pad process-panel" :class="{ 'with-components': chartMode === 'components' && app.hasLakeBalance, 'with-quality': isModelInputWorkspace && isNananxiHistory }">
          <div class="process-head">
            <div>
              <h2 class="panel-title">{{ isNananxiHistory && !hasModelHydrograph ? '实测洪水过程' : '过程拟合检验' }}</h2>
              <p class="process-subtitle">{{ processTitle }}</p>
            </div>
            <div v-if="app.hasLakeBalance" class="process-tools">
              <div class="process-tabs" aria-label="过程图显示方式">
                <button type="button" :class="{ active: chartMode === 'process' }" @click="chartMode = 'process'">流量过程</button>
                <button type="button" :class="{ active: chartMode === 'components' }" @click="chartMode = 'components'">径流组成</button>
              </div>
            </div>
          </div>
          <div v-if="workMode === 'calibration' && calMode === 'continuous'" class="continuous-process-tabs" aria-label="连续率定过程">
            <button v-for="period in continuousResultPeriods" :key="period.id" type="button" :class="{ active: selectedContinuousPeriod === period.id }" @click="selectedContinuousPeriod = period.id">{{ period.purpose }}期</button>
          </div>
          <div v-if="app.hasLakeBalance && chartMode === 'components'" class="component-switches" aria-label="径流组成显示">
            <span>显示分量</span>
            <label><input type="checkbox" v-model="componentVisibility.rainfall" /> 降雨径流</label>
            <label><input type="checkbox" v-model="componentVisibility.snowmelt" /> 融雪径流</label>
            <label><input type="checkbox" v-model="componentVisibility.glacier" /> 冰川融水</label>
            <label><input type="checkbox" v-model="componentVisibility.baseflow" /> 地下径流</label>
          </div>
          <p v-if="isModelInputWorkspace && isNananxiHistory" class="input-quality-note" :class="{ warn: inputProcessError || eventQuality?.model_input_ready === false }" aria-live="polite">
            {{ inputProcessLoading ? '正在核对所选资料版本与洪水场次…' : inputProcessError || eventQualityLabel }}
          </p>
          <details v-if="backendHydrograph && processResult" class="result-provenance" :class="{ warn: frozenInputBundleVersionId(processResult) !== app.selectedInputBundleVersionId }">
            <summary>{{ frozenInputBundleVersionId(processResult) === app.selectedInputBundleVersionId ? '计算资料与当前选择一致' : '历史成果 · 计算资料不同于当前选择' }}</summary>
            <dl><dt>任务编号</dt><dd>{{ processResult.taskId }}</dd><dt>计算输入</dt><dd>{{ frozenInputBundleVersionId(processResult) }}</dd><dt>当前选择</dt><dd>{{ app.selectedInputBundleVersionId }}</dd></dl>
          </details>
          <div class="hydro">
            <CalibrationProcessChart
              :hydrograph="displayHydrograph"
              :forcing="displayForcing"
              :visible-components="componentVisibility"
              :display-mode="chartMode"
              :observed-only="isNananxiHistory && !hasModelHydrograph"
              :comparisons="trialComparisons"
              :current-curve="currentTrialCurve"
            />
          </div>
          <div v-if="workMode === 'calibration' && trialRows.length" class="trial-ledger" aria-label="人工试调记录">
            <div class="trial-ledger-head"><b>过程线对照</b><span>同资料 · 同时段 · 同汇流方案</span></div>
            <div v-for="(trial, index) in trialRows" :key="trial.taskId" class="trial-entry">
              <label><input type="checkbox" :checked="trial.visible" :disabled="trial.taskId === processResult?.taskId" @change="setTrialVisible(trial.taskId, $event.target.checked)" /><i :style="{ background: trial.color }" />{{ trial.label }}</label>
              <span class="trial-change">{{ index ? changedParameters(trialRows[index - 1].parameters, trial.parameters) || '参数未变' : '对照参数' }}</span>
              <span class="trial-score">NSE {{ hydrologyNumber(trial.hydrograph?.metrics?.nse, 3) }}</span>
              <button class="btn mini" type="button" :disabled="cal.running" @click="cal.loadVersion(trial.parameterVersionId)">采用参数</button>
            </div>
            <p v-if="trialLoadError" role="status">{{ trialLoadError }}</p>
          </div>
        </section>

        <div class="below-grid" :class="{ 'single-result': workMode === 'simulation' || calMode === 'continuous' }">
          <section v-if="workMode === 'calibration' && calMode === 'event'" class="panel events-panel">
            <div class="event-panel-head">
              <div>
                <h2>洪水场次台账</h2>
                <p>共 {{ activeEvents.length }} 场 · 可用候选 {{ candidateEvents.length }} · 率定 {{ calibrationEvents.length }} · 验证 {{ validationEvents.length }} · 独检 {{ independentEvents.length }}</p>
              </div>
              <span class="event-range">{{ eventRangeLabel }}</span>
            </div>
            <div class="event-toolbar">
              <label class="event-search">
                <span>检索</span>
                <input v-model.trim="eventQuery" type="search" placeholder="洪号或日期" />
              </label>
              <label>
                <span>年份</span>
                <select v-model="eventYear">
                  <option value="all">全部</option>
                  <option v-for="year in eventYears" :key="year" :value="String(year)">{{ year }}</option>
                </select>
              </label>
              <label>
                <span>用途</span>
                <select v-model="eventPurposeFilter">
                  <option value="all">全部</option>
                  <option v-for="role in eventRoleOptions" :key="role.value" :value="role.value">{{ role.value }}</option>
                </select>
              </label>
              <b class="event-match">{{ filteredEvents.length }} 场</b>
            </div>
            <div v-if="isNananxiHistory" class="event-selection-panel">
              <div class="event-selection-fields">
                <label><span>候选范围</span><select v-model="eventSelectionMethod">
                  <option value="all-valid">全部有效场次</option>
                  <option value="top-n">按 Qmax 前 N 场</option>
                  <option value="peak-threshold">Qmax 不低于</option>
                  <option value="year">按起始年份</option>
                  <option value="date-range">按时间段</option>
                  <option value="manual">人工勾选</option>
                </select></label>
                <label v-if="eventSelectionMethod === 'top-n'"><span>场数</span><input v-model.number="eventTopN" type="number" min="1" max="999" /></label>
                <label v-if="eventSelectionMethod === 'peak-threshold'"><span>Qmax（m³/s）</span><input v-model.number="eventPeakThreshold" type="number" min="0" step="1" /></label>
                <label v-if="eventSelectionMethod === 'year'"><span>年份</span><select v-model="eventSelectionYear"><option value="all">全部</option><option v-for="year in eventYears" :key="year" :value="String(year)">{{ year }}</option></select></label>
                <label v-if="eventSelectionMethod === 'date-range'"><span>开始</span><input v-model="eventSelectionStart" type="date" /></label>
                <label v-if="eventSelectionMethod === 'date-range'"><span>结束</span><input v-model="eventSelectionEnd" type="date" /></label>
                <div class="event-selection-count"><b>{{ candidateEvents.length }}</b><span>/ {{ eligibleEvents.length }} 场可用</span></div>
              </div>
              <div class="event-selection-note">先确定本次率定候选集，再按洪峰大小分层随机划分；未入选场次不会被删除。</div>
              <div class="event-selection-stats">
                <span>洪峰范围 <b>{{ candidatePeakStats.min != null ? `${candidatePeakStats.min.toFixed(1)}–${candidatePeakStats.max.toFixed(1)} m³/s` : '—' }}</b></span>
                <span>洪峰中位数 <b>{{ candidatePeakStats.median != null ? `${candidatePeakStats.median.toFixed(1)} m³/s` : '—' }}</b></span>
                <span>年份覆盖 <b>{{ candidatePeakStats.years.length ? candidatePeakStats.years.join('、') : '—' }}</b></span>
                <span>雨量完整率 <b>{{ candidatePeakStats.rainCompleteness != null ? `${(candidatePeakStats.rainCompleteness * 100).toFixed(1)}%` : '待检查' }}</b></span>
                <span>流量完整率 <b>{{ candidatePeakStats.flowCompleteness != null ? `${(candidatePeakStats.flowCompleteness * 100).toFixed(1)}%` : '待检查' }}</b></span>
              </div>
            </div>
            <div v-if="isNananxiHistory" class="event-batch-actions">
              <label><span>随机种子</span><input v-model.trim="eventSplitSeed" type="text" inputmode="numeric" aria-label="随机划分种子" /></label>
              <button type="button" @click="assignEventsByRatio">随机按 7∶2∶1 划分</button>
              <button type="button" @click="assignAllCalibration">候选场次全部率定</button>
              <button type="button" @click="clearEventPurposes">清空</button>
            </div>
            <div class="event-table" :class="{ 'with-pick': isNananxiHistory }" role="table" aria-label="洪水场次台账">
              <div class="event-table-row event-table-header" role="row">
                <span v-if="isNananxiHistory" class="event-pick-head">候选</span>
                <button type="button" class="event-sort" :aria-sort="eventSortAria('floodNo')" @click="toggleEventSort('floodNo')"><span>场次</span><i>{{ eventSortIndicator('floodNo') }}</i></button>
                <button type="button" class="event-sort" :aria-sort="eventSortAria('period')" @click="toggleEventSort('period')"><span>洪水过程</span><i>{{ eventSortIndicator('period') }}</i></button>
                <button type="button" class="event-sort" :aria-sort="eventSortAria('peak')" @click="toggleEventSort('peak')"><span>洪峰</span><i>{{ eventSortIndicator('peak') }}</i></button>
                <span>用途</span>
              </div>
              <div v-for="event in pagedEvents" :key="event.id" class="event-table-row" :class="{ active: event.id === cal.eventId }" role="row">
                <label v-if="isNananxiHistory" class="event-pick" :title="candidateIds.has(String(event.eventId || event.id)) ? '已纳入候选' : '未纳入候选'">
                  <input type="checkbox" :checked="candidateIds.has(String(event.eventId || event.id))" @click.stop @change="toggleManualCandidate(event, $event.target.checked)" />
                </label>
                <button type="button" class="event-code" :aria-pressed="event.id === cal.eventId" @click="selectFlood(event)">{{ compactEventName(event) }}</button>
                <button type="button" class="event-period" @click="selectFlood(event)">{{ formatEventPeriod(event) }}</button>
                <span class="event-peak">{{ eventPeak(event) }}</span>
                <select class="event-purpose" :value="event.purpose" :aria-label="`${event.name} 用途`" @change="setEventPurposeManually(event.id, $event.target.value)">
                  <option v-for="role in eventRoleOptions" :key="role.value" :value="role.value">{{ role.value }}</option>
                </select>
              </div>
              <div v-if="!pagedEvents.length" class="event-empty">没有符合条件的洪水场次</div>
            </div>
            <div class="event-pagination">
              <span>第 {{ eventPage }} / {{ eventPageCount }} 页</span>
              <div>
                <button type="button" :disabled="eventPage <= 1" @click="eventPage -= 1">上一页</button>
                <button type="button" :disabled="eventPage >= eventPageCount" @click="eventPage += 1">下一页</button>
              </div>
            </div>
          </section>

          <section class="panel panel-pad result-panel">
            <h2 class="panel-title">{{ isNananxiHistory ? (backendHydrograph ? '当前场次拟合指标' : isModelInputWorkspace ? '洪水资料 · 整编资料' : '洪水资料 · 离线原始预览') : (workMode === 'simulation' ? '连续模拟结果' : '率定结果') }}</h2>
            <template v-if="workMode === 'simulation' && cal.visibleSimulationResult">
              <div class="result-summary">
                <div><span>模拟洪峰</span><b>{{ simulationPeakLabel }}</b><em>m³/s</em></div>
                <p>连续试算完成。</p>
              </div>
              <p class="result-time">完成时间：{{ cal.visibleSimulationResult.completedAt }}</p>
            </template>
            <template v-else-if="matchingCalibrationResult">
              <div class="result-summary" :class="{ stale: !matchingCalibrationResult.isCurrent }">
                <div><span>{{ matchingCalibrationResult.resultSource === 'model-backend' ? '率定平均 NSE' : '综合评分' }}</span><b>{{ matchingCalibrationResult.resultSource === 'model-backend' ? hydrologyNumber(matchingCalibrationResult.calibrationMetrics?.nse, 3) : (matchingCalibrationResult.score?.toFixed(1) ?? '—') }}</b><em v-if="matchingCalibrationResult.resultSource !== 'model-backend'">分</em></div>
                <p>{{ matchingCalibrationResult.isCurrent ? matchingCalibrationResult.grade : '计算条件已变更，请重新率定。' }}</p>
              </div>
              <p class="result-time">完成时间：{{ dayjs(matchingCalibrationResult.completedAt).format('YYYY-MM-DD HH:mm:ss') }}</p>
            </template>
            <template v-else>
              <div class="result-summary pending">
                <div><span>{{ isNananxiHistory ? '实测洪峰' : '当前过程' }}</span><b>{{ isNananxiHistory ? (activeFlood?.observedPeak ?? '—') : nseLabel }}</b><em>{{ isNananxiHistory ? 'm³/s' : 'NSE' }}</em></div>
                <p>{{ !cal.executionAvailability.ready ? cal.executionAvailability.reason : '暂无计算结果' }}</p>
              </div>
            </template>
            <div class="result-metrics">
              <div v-for="metric in currentMetricRows" :key="metric.name"><span>{{ metric.name }}</span><b :class="metric.cls">{{ metric.value }}</b></div>
            </div>
            <div v-if="workMode === 'calibration' && matchingCalibrationResult?.optimizer?.stages" class="search-stage-results" aria-label="参数搜索阶段">
              <div v-for="stage in matchingCalibrationResult.optimizer.stages" :key="stage.id"><b>{{ stage.label }}</b><span>{{ stage.evaluations }} 次评价</span><strong>{{ Number(stage.bestLoss).toFixed(4) }}</strong><small>目标函数值{{ stage.retainedInitial ? ' · 保留阶段起始方案' : '' }}</small></div>
            </div>
            <SearchPlanResult v-if="workMode === 'calibration' && matchingCalibrationResult?.searchPlan" :plan="matchingCalibrationResult.searchPlan" />
            <section v-if="workMode === 'calibration' && app.hasEventCatalog" class="parameter-result-card" aria-label="率定参数结果">
              <div class="parameter-result-head">
                <div><b>率定参数版本</b><span>{{ activeParameterVersion?.id || '暂无参数版本' }}</span></div>
                <span class="tag" :class="activeParameterVersion?.status === 'approved' ? 'ok' : 'warn'">{{ activeParameterVersion?.review?.status || '候选版本' }}</span>
              </div>
              <div v-if="activeParameterVersion" class="parameter-result-grid">
                <div><span>率定评价</span><b>{{ metricText(activeParameterVersion.metrics?.calibration) }}</b></div>
                <div><span>验证评价</span><b>{{ metricText(activeParameterVersion.metrics?.validation) }}</b></div>
                <div><span>独立检验</span><b :class="releaseReadiness.independentPassed ? 'ok-text' : 'warn-text'">{{ independentTestText }}</b></div>
                <div><span>{{ calMode === 'continuous' ? '评价时段' : '对应场次集' }}</span><b>{{ calMode === 'continuous' ? `${continuousResultPeriods.length} 段` : activeParameterVersion.eventSetVersion || '—' }}</b></div>
              </div>
              <div v-if="independentEventRows.length" class="independent-event-results">
                <div class="independent-event-title">{{ calMode === 'continuous' ? '独立检验期结果' : '独立检验逐场结果' }}</div>
                <div class="independent-event-row independent-event-head"><span>洪号/事件</span><span>NSE</span><span>洪峰误差</span><span>峰现误差</span><span>洪量误差</span></div>
                <div v-for="row in independentEventRows" :key="row.eventId || row.id" class="independent-event-row"><span>{{ row.sourceFloodNo || row.eventId || row.id }}</span><span>{{ (row.nse ?? row.NSE) != null ? Number(row.nse ?? row.NSE).toFixed(3) : '—' }}</span><span>{{ (row.peakErrorPercent ?? row.peak_error_percent) != null ? `${Number(row.peakErrorPercent ?? row.peak_error_percent).toFixed(1)}%` : '—' }}</span><span>{{ (row.peakTimeErrorHours ?? row.peak_time_error_hours) != null ? `${Number(row.peakTimeErrorHours ?? row.peak_time_error_hours).toFixed(1)} h` : '—' }}</span><span>{{ (row.volumeErrorPercent ?? row.volume_error_percent) != null ? `${Number(row.volumeErrorPercent ?? row.volume_error_percent).toFixed(1)}%` : '—' }}</span></div>
              </div>
              <p v-if="!activeParameterVersion">暂无候选参数</p>
              <p v-else-if="activeParameterVersion.productionEligible !== true" class="parameter-result-note">候选参数</p>
              <p v-else-if="!releaseReadiness.independentPassed" class="parameter-result-note warn-text">独立检验未通过</p>
              <p v-else-if="!releaseReadiness.reviewed" class="parameter-result-note">待审核</p>
              <p v-else class="parameter-result-note ok-text">可发布</p>
              <div v-if="activeParameterVersion" class="parameter-result-actions">
                <button class="btn mini" type="button" :disabled="cal.running" @click="cal.loadVersion(activeParameterVersion.id)">{{ activeParameterVersion.routingCandidate ? '采用产汇流方案' : '采用该参数' }}</button>
                <button v-if="activeParameterVersion.productionEligible === true && !releaseReadiness.reviewed && releaseReadiness.independentPassed" class="btn mini" type="button" @click="reviewCandidate">审核通过</button>
                <button v-if="releaseReadiness.canPublish" class="btn mini primary" type="button" @click="publishCandidate">发布到自动预报参数库</button>
              </div>
            </section>
          </section>
        </div>
      </main>

      <aside v-if="workMode === 'calibration'" class="panel panel-pad parameter-panel">
        <p class="parameter-intro">{{ app.backendCase ? '人工试调 / 自动率定起始参数' : '参数调整后，过程线同步更新。' }}</p>
        <div v-if="app.backendCase?.manualCalibration" class="manual-trial-actions">
          <button class="btn primary block" type="button" :disabled="!manualCanRun" @click="startManualTrial">{{ cal.running ? '正在计算…' : '试算并叠加' }}</button>
          <p v-if="parameterConstraint" role="status">{{ parameterConstraint }}</p>
          <span v-else>固定当前参数计算，保留历次过程线</span>
        </div>
        <SearchSpaceEditor v-if="app.backendCase?.searchParameterDefinitions" :key="cal.calibrationTarget" :model-value="activeSearchSelection" :definitions="activeSearchDefinitions" :params="activeSearchParameters" :routing="cal.calibrationTarget === 'routing'" :disabled="cal.running" :error="searchError" @update:model-value="updateSearchSelection" />
        <div v-if="app.hasLakeBalance" class="param-groups" aria-label="参数分组">
          <button v-for="group in parameterGroups" :key="group.key" type="button" :class="{ active: paramGroup === group.key }" @click="paramGroup = group.key">{{ group.label }}</button>
        </div>
        <ParamTuner
          :params="cal.params"
          :defs="shownParamDefs"
          :dirty="cal.dirty"
          :live-preview="cal.executionAvailability.mode === 'frontend-fixture'"
          :show-reset="false"
          :title="app.backendCase ? '产流参数' : app.hasLakeBalance ? '模型参数' : '产流与汇流参数'"
          @update="(key, value) => cal.setParam(key, value)"
        />
      </aside>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { useCalibrationStore } from '@/stores/calibration'
import { useTasksStore } from '@/stores/tasks'
import { PARAM_DEFS, formatMetric } from '@/models/xajParamEngine'
import CalibrationProcessChart from '@/components/charts/CalibrationProcessChart.vue'
import ParamTuner from '@/components/params/ParamTuner.vue'
import SearchSpaceEditor from '@/components/params/SearchSpaceEditor.vue'
import SearchPlanResult from '@/components/params/SearchPlanResult.vue'
import { searchSelectionError } from '@/services/calibrationSpaceService'
import { PARAMETER_VERSION_CATALOG } from '@/fixtures/catalogs/parameter-versions'
import { useAppStore } from '@/stores/app'
import { useStateStore } from '@/stores/state'
import { selincoSections } from '@/data/selinco'
import { loadNananxiEventPackages, loadNananxiObservationCatalog, loadNananxiObservationWindow } from '@/services/historicalObservationService'
import {
  buildCalibrationRequest,
  normalizeCalibrationResult,
  selectCalibrationCandidates,
  stratifiedRandomEventSplit,
  submitCalibrationRequest
} from '@/services/calibrationSubmissionService'
import { selectObservedHydrologyDataset, selectObservedPrecipitationDataset } from '@/services/datasetSelectionService'
import {
  independentTestPassed,
  independentTestConclusion,
  parameterLibraryFor,
  publishAutomaticParameter,
  registerCalibrationResult,
  releaseReadiness as getReleaseReadiness,
  reviewSimulationParameter
} from '@/services/parameterLibraryService'
import TaskStatusPanel from '@/components/tasks/TaskStatusPanel.vue'
import ContextStatusMetric from '@/components/common/ContextStatusMetric.vue'
import { projectDataForContext } from '@/services/caseAvailabilityService'
import { finiteInputValue, loadModelInputCatalog, loadModelInputEvent, usesModelInputService } from '@/services/modelInputService'
import { observedModelProcess, modelEventStatistics } from '@/services/modelInputPresentation'
import { frozenInputBundleVersionId } from '@/services/inputProductService'
import { hydrologyText, hydrologyNumber } from '@/services/hydrologyPresentation'
import { trialConditions, changedParameters, TRIAL_COLORS } from '@/services/manualCalibrationService'
import { modelApi } from '@/services/modelBackendService'

const cal = useCalibrationStore()
const tasks = useTasksStore()
const app = useAppStore()
const state = useStateStore()
const router = useRouter()
const trialResults = reactive({})
const trialLoadError = ref('')
const routingRecord = ref(null), routingError = ref('')
let routingReadRevision = 0
watch(() => [app.backendCase, app.selectedRoutingVersionId], async () => {
  const revision = ++routingReadRevision
  routingRecord.value = null; routingError.value = ''
  if (!app.backendCase) return
  try {
    const { versions } = await modelApi('routing-versions')
    if (revision !== routingReadRevision) return
    routingRecord.value = versions.find(row => row.routingVersionId === app.selectedRoutingVersionId)
    if (!routingRecord.value) routingError.value = '请选择已登记的汇流方案'
  } catch (error) { if (revision === routingReadRevision) routingError.value = error.message }
}, { immediate: true })
const activeSearchSelection = computed(() => cal.calibrationTarget === 'routing' ? cal.routingSearchParameters : cal.searchParameters)
const activeSearchDefinitions = computed(() => (cal.calibrationTarget === 'routing' ? app.backendCase?.routingSearchParameterDefinitions : app.backendCase?.searchParameterDefinitions) || [])
const activeSearchParameters = computed(() => cal.calibrationTarget === 'routing'
  ? Object.fromEntries(['RS', 'RI', 'RG'].map(key => [key + '_k_hours', routingRecord.value?.parameters.default_slope_k_hours[key]])) : cal.params)
const searchError = computed(() => {
  if (!app.backendCase) return ''
  if (cal.calibrationTarget === 'routing' && !routingRecord.value) return routingError.value || '正在读取汇流方案'
  return searchSelectionError(activeSearchSelection.value, activeSearchDefinitions.value, activeSearchParameters.value, cal.calibrationTarget === 'routing' ? 'joint' : cal.searchStrategy)
})
function updateSearchSelection(value) { cal.calibrationTarget === 'routing' ? cal.setRoutingSearchParameters(value) : cal.setSearchParameters(value) }
const parameterConstraint = computed(() => {
  if (!app.backendCase) return ''
  const p = cal.params
  if (p.WM <= p.WUM + p.WLM) return 'WM 须大于 WUM + WLM'
  if (p.KI + p.KG >= 0.98) return 'KI + KG 须小于 0.98'
  return ''
})
const manualCanRun = computed(() => app.backendCase?.manualCalibration && !cal.running && !parameterConstraint.value
  && cal.executionAvailability.ready && cal.validateCalibration().errors.every(error => error.includes('次评价')))
const calMode = ref(cal.calibrationMode)
const workMode = ref(cal.operationMode || 'calibration')
const calculationHandoff = computed(() => app.workContext.handoff?.targetPage === 'calibration' ? app.workContext.handoff : null)
const chartMode = ref('process')
const paramGroup = ref('runoff')
const eventQuery = ref('')
const eventYear = ref('all')
const eventPurposeFilter = ref('all')
const eventSplitSeed = ref('42')
const eventAssignment = ref(null)
const eventSelectionMethod = ref('all-valid')
const eventTopN = ref(50)
const eventPeakThreshold = ref(100)
const eventSelectionYear = ref('all')
const eventSelectionStart = ref('')
const eventSelectionEnd = ref('')
const manualCandidateIds = ref([])
const eventPage = ref(1)
const eventPageSize = 8
const eventSortKey = ref('period')
const eventSortDirection = ref('asc')
const componentVisibility = reactive({ rainfall: false, snowmelt: true, glacier: true, baseflow: false })
const historicalEventProcess = ref(null)
const inputProcessError = ref('')
const inputProcessLoading = ref(false)
const eventQuality = ref(null)
const eventPackageCatalog = ref(null)
const requestStatus = ref('')
const registeredParameterVersionId = ref(null)
let historicalEventRequestId = 0
let observationCatalogRequestId = 0
const activeTask = computed(() => cal.activeTaskId ? tasks.taskById(cal.activeTaskId) : null)
const caseData = computed(() => projectDataForContext(app.activeContext, app.projectData, { loading: app.projectDataLoading, error: app.projectDataError }))
const isModelInputWorkspace = computed(() => usesModelInputService(app.activeContext))
const eventQualityLabel = computed(() => {
  const quality = eventQuality.value
  if (!quality) return '尚无资料检查结论；未生成模拟过程。'
  const reasons = [...(quality.exclusion_reasons || quality.reject_reasons || []), ...(quality.warning_reasons || [])]
  return `${quality.model_input_ready ? '计算资料通过 · 可提交诊断任务' : '计算资料未通过 · 请核查资料'}${reasons.length ? ` · ${reasons.join('、')}` : ''}。场次蒸散发取零，结果不得作为生产预报。`
})
const nseLabel = computed(() => {
  const nse = backendHydrograph.value?.metrics?.nse ?? (cal.executionAvailability.mode === 'frontend-fixture' ? cal.peakSummary.nse : null)
  return nse == null ? '—' : nse.toFixed(3)
})
const simulationPeakLabel = computed(() => hydrologyNumber(cal.visibleSimulationResult?.hydrograph?.peak ?? cal.visibleSimulationResult?.peak, 2))
const parameterLibrary = computed(() => parameterLibraryFor(caseData.value, app.basinId, app.modelScale, app.activeContext))
const simulationParameterVersions = computed(() => parameterLibrary.value?.simulationRateLibrary?.versions || [])
const activeParameterVersion = computed(() => {
  if (matchingCalibrationResult.value?.parameterVersionId) return simulationParameterVersions.value.find(version => version.id === matchingCalibrationResult.value.parameterVersionId) || null
  if (app.backendCase && workMode.value === 'calibration') return null
  if (registeredParameterVersionId.value) {
    return simulationParameterVersions.value.find((version) => version.id === registeredParameterVersionId.value) || null
  }
  return simulationParameterVersions.value[0] || null
})
const releaseReadiness = computed(() => releaseReadinessFor(activeParameterVersion.value))
const independentTestText = computed(() => {
  const metrics = activeParameterVersion.value?.independentTest
  return independentTestConclusion(metrics)
})
const independentEventRows = computed(() => {
  const rows = activeParameterVersion.value?.eventMetrics
  if (Array.isArray(rows)) return rows.filter((row) => !row.group || row.group === 'INDEPENDENT_TEST')
  if (rows && typeof rows === 'object') return Object.entries(rows).map(([eventId, metrics]) => ({ eventId, ...metrics }))
  return []
})
const calculationCanRun = computed(() => {
  if (cal.running || !cal.executionAvailability.ready || !cal.sectionId) return false
  if (['queued', 'running', 'cancelling'].includes(activeTask.value?.status)) return false
  if (workMode.value === 'calibration' && (searchError.value || parameterConstraint.value)) return false
  if (workMode.value === 'calibration' && !objectiveWeightValid.value) return false
  if (workMode.value === 'calibration' && calMode.value === 'event') return calibrationEvents.value.length > 0 && validationEvents.value.length > 0
  if (workMode.value === 'calibration') return cal.validateCalibration().ok
  return true
})
const calculationBlockReason = computed(() => {
  if (!cal.executionAvailability.ready) return cal.executionAvailability.reason
  if (workMode.value === 'calibration' && (parameterConstraint.value || searchError.value)) return parameterConstraint.value || searchError.value
  if (workMode.value === 'calibration' && !objectiveWeightValid.value) return '已选评价指标的权重合计需为 1.00。'
  if (workMode.value === 'calibration' && calMode.value === 'event' && (!calibrationEvents.value.length || !validationEvents.value.length)) return '请至少各登记一场率定洪水和验证洪水。'
  if (workMode.value === 'calibration' && !cal.validateCalibration().ok) return cal.validateCalibration().errors[0]
  return '当前案例未登记计算断面或完整计算条件。'
})

const eventRoleOptions = [
  { value: '未使用', label: '不选' },
  { value: '率定', label: '率定' },
  { value: '验证', label: '验证' },
  { value: '独立检验', label: '独检' }
]

const parameterGroups = [
  { key: 'runoff', label: '产流蓄水', keys: ['K', 'B', 'C', 'WM', 'SM', 'EX'] },
  { key: 'routing', label: '坡面河网', keys: ['KI', 'KG', 'CS', 'L', 'KH', 'MSK_K', 'MSK_X'] },
  { key: 'snow', label: '雪冰冻土', keys: ['TSNOW', 'DDM', 'ICE_MELT', 'FROST_WM'] },
  { key: 'lake', label: '湖泊平衡', keys: ['LAKE_K', 'LAKE_A'] }
]

const fixtureAlgorithms = [
  { id: 'SCE-UA', label: 'SCE-UA 复形进化法', note: '适合水文模型多参数全局搜索，兼顾收敛稳定性与搜索范围。' },
  { id: 'DDS', label: 'DDS 动态维数搜索', note: '评价次数受限时效率较高，适用于较多参数的快速率定。' },
  { id: 'DE', label: '差分进化法', note: '适用于参数边界明确的全局搜索。' },
  { id: 'PSO', label: '粒子群算法', note: '适用于快速寻找可用参数组合，并可与其他方法交叉检验。' }
]

const algorithms = computed(() => isModelInputWorkspace.value ? app.backendCase?.algorithms || [] : fixtureAlgorithms)
const activeDataset = computed(() => isModelInputWorkspace.value
  ? { hydrology: app.modelScale === '1km' ? '徐畈控制断面 · 整编逐时流量' : '该尺度不提供本次计算', precipitation: app.modelScale === '1km' ? hydrologyText(app.selectedInputProduct?.name || '徐畈控制区 · 登记网格雨量') : '该尺度不提供本次计算' }
  : app.hasLakeBalance
  ? { hydrology: '逐时入湖断面流量与湖位资料', precipitation: app.activeModelScheme.forcing?.name || '逐时降水资料' }
  : {
      hydrology: selectObservedHydrologyDataset(caseData.value)?.name || '出口水文资料未接入',
      precipitation: selectObservedPrecipitationDataset(caseData.value, app.modelScale)?.name || '当前尺度降雨场未接入'
    })
const activeSpatial = computed(() => app.hasLakeBalance
  ? { grid: app.activeModelScheme.label, subbasins: 4 }
  : { grid: app.activeScaleMeta.label, subbasins: caseData.value?.spatialMetadata?.featureCounts?.basin ?? 0 })
const spatialMetricLabel = computed(() => app.basinId === 'nananxi-xufan'
  ? `${activeSpatial.value.grid} · ${caseData.value?.spatialMetadata?.terrain?.[app.modelScale]?.cellCount ?? '待登记'} 个有效栅格`
  : `${activeSpatial.value.grid} · ${activeSpatial.value.subbasins} ${app.hasLakeBalance ? '个入湖流域' : '个流域单元'}`)
const calSections = computed(() => {
  if (!caseData.value) return []
  if (app.hasLakeBalance) return selincoSections.map((section) => section.name)
  const sections = caseData.value?.sections?.sections || []
  return sections.map((section) => section.name)
})
const versionOptions = computed(() => {
  if (app.backendCase) return [...new Set([app.backendCase.parameterVersionId, ...simulationParameterVersions.value.map((row) => row.id), cal.paramVersion])]
  if (cal.backendContractVersion) return [...new Set([cal.paramVersion, ...simulationParameterVersions.value.map((row) => row.id)])]
  const versions = PARAMETER_VERSION_CATALOG
    .filter((version) => version.basinProjectId === app.basinId && version.resolutionId === app.modelScale)
    .map((version) => version.id)
  return versions.length ? versions : [app.activeModelScheme.paramVersion]
})
const activeParamDefs = computed(() => (app.hasLakeBalance ? cal.paramDefs : cal.paramDefs.filter((item) => !item.group)).filter((item) => item.key in cal.params))
const shownParamDefs = computed(() => {
  if (!app.hasLakeBalance) return activeParamDefs.value
  const group = parameterGroups.find((item) => item.key === paramGroup.value)
  return group ? activeParamDefs.value.filter((item) => group.keys.includes(item.key)) : activeParamDefs.value
})
const activeEvents = computed(() => cal.events.filter((event) => event.status !== 'inactive'))
const eventYears = computed(() => [...new Set(activeEvents.value.map((event) => dayjs(event.start).year()))].sort((a, b) => b - a))
const registeredRuntimeEvents = computed(() => isModelInputWorkspace.value ? activeEvents.value.filter((event) => event.runtimeRegistered === true) : activeEvents.value)
const eligibleEvents = computed(() => selectCalibrationCandidates(registeredRuntimeEvents.value, { method: 'all-valid' }))
const candidateEvents = computed(() => selectCalibrationCandidates(registeredRuntimeEvents.value, {
  method: eventSelectionMethod.value,
  topN: eventTopN.value,
  peakThreshold: eventPeakThreshold.value,
  year: eventSelectionYear.value,
  startDate: eventSelectionStart.value,
  endDate: eventSelectionEnd.value,
  manualIds: manualCandidateIds.value
}))
const candidateIds = computed(() => new Set(candidateEvents.value.map((event) => String(event.eventId || event.id))))
const eventSelectionPayload = computed(() => ({
  method: eventSelectionMethod.value,
  topN: eventSelectionMethod.value === 'top-n' ? Math.max(0, Number(eventTopN.value) || 0) : null,
  peakThreshold: eventSelectionMethod.value === 'peak-threshold' ? Number(eventPeakThreshold.value) || 0 : null,
  year: eventSelectionMethod.value === 'year' ? eventSelectionYear.value : null,
  startDate: eventSelectionMethod.value === 'date-range' ? eventSelectionStart.value || null : null,
  endDate: eventSelectionMethod.value === 'date-range' ? eventSelectionEnd.value || null : null,
  selectedEventIds: candidateEvents.value.map((event) => event.eventId || event.id),
  candidateCount: candidateEvents.value.length
}))
const candidatePeakStats = computed(() => {
  const peaks = candidateEvents.value.map((event) => finiteInputValue(event.observedPeak)).filter((value) => value != null).sort((a, b) => a - b)
  const median = peaks.length ? (peaks.length % 2 ? peaks[(peaks.length - 1) / 2] : (peaks[peaks.length / 2 - 1] + peaks[peaks.length / 2]) / 2) : null
  const years = [...new Set(candidateEvents.value.map((event) => String(event.start || '').slice(0, 4)).filter(Boolean))].sort()
  const rain = candidateEvents.value.map((event) => finiteInputValue(event.rainCompleteness)).filter((value) => value != null)
  const flow = candidateEvents.value.map((event) => finiteInputValue(event.flowCompleteness ?? event.riverCompleteness)).filter((value) => value != null)
  return {
    min: peaks.length ? peaks[0] : null,
    max: peaks.length ? peaks[peaks.length - 1] : null,
    median,
    years,
    rainCompleteness: rain.length ? rain.reduce((sum, value) => sum + value, 0) / rain.length : null,
    flowCompleteness: flow.length ? flow.reduce((sum, value) => sum + value, 0) / flow.length : null
  }
})
const filteredEvents = computed(() => {
  const query = eventQuery.value.toLowerCase()
  return activeEvents.value.filter((event) => {
    const matchesQuery = !query || `${event.name} ${event.id} ${event.start} ${event.end}`.toLowerCase().includes(query)
    const matchesYear = eventYear.value === 'all' || String(dayjs(event.start).year()) === eventYear.value
    const matchesPurpose = eventPurposeFilter.value === 'all' || event.purpose === eventPurposeFilter.value
    return matchesQuery && matchesYear && matchesPurpose
  })
})
const sortedEvents = computed(() => {
  const rows = [...filteredEvents.value]
  const direction = eventSortDirection.value === 'desc' ? -1 : 1
  const valueFor = (event) => {
    if (eventSortKey.value === 'floodNo') return String(event.sourceFloodNo || event.eventId || event.id || '')
    if (eventSortKey.value === 'peak') {
      const value = Number(event.observedPeak)
      return Number.isFinite(value) ? { missing: false, value } : { missing: true, value: 0 }
    }
    return { missing: !event.start, value: new Date(event.start || 0).getTime() || 0 }
  }
  return rows
    .map((event, index) => ({ event, index }))
    .sort((a, b) => {
      const left = valueFor(a.event)
      const right = valueFor(b.event)
      if (typeof left === 'object' && typeof right === 'object') {
        if (left.missing !== right.missing) return left.missing ? 1 : -1
        if (left.value < right.value) return -1 * direction
        if (left.value > right.value) return 1 * direction
        return a.index - b.index
      }
      if (left < right) return -1 * direction
      if (left > right) return 1 * direction
      return a.index - b.index
    })
    .map(({ event }) => event)
})
const eventPageCount = computed(() => Math.max(1, Math.ceil(filteredEvents.value.length / eventPageSize)))
const pagedEvents = computed(() => sortedEvents.value.slice((eventPage.value - 1) * eventPageSize, eventPage.value * eventPageSize))
const eventRangeLabel = computed(() => {
  if (!activeEvents.value.length) return '无记录'
  const years = activeEvents.value.map((event) => dayjs(event.start).year())
  return `${Math.min(...years)}—${Math.max(...years)}`
})
const activeFlood = computed(() => activeEvents.value.find((event) => event.id === cal.eventId) || activeEvents.value[0])
const activeEventPackage = computed(() => eventPackageCatalog.value?.packages?.find((item) => item.eventId === activeFlood.value?.id) || null)
const isNananxiHistory = computed(() => app.hasEventCatalog && workMode.value === 'calibration' && calMode.value === 'event')
const matchingCalibrationResult = computed(() => {
  const result = cal.visibleRunResult
  return (result?.calibrationMode || result?.frozenRequest?.period?.mode || 'event') === calMode.value ? result : null
})
const selectedContinuousPeriod = ref('CALIBRATION')
const continuousResultPeriods = computed(() => matchingCalibrationResult.value?.periods || [])
watch(continuousResultPeriods, rows => {
  if (!rows.some(row => row.id === selectedContinuousPeriod.value)) selectedContinuousPeriod.value = rows[0]?.id || 'CALIBRATION'
})
const calibrationEvents = computed(() => eligibleEvents.value.filter((event) => event.purpose === '率定'))
const validationEvents = computed(() => eligibleEvents.value.filter((event) => event.purpose === '验证'))
const independentEvents = computed(() => eligibleEvents.value.filter((event) => event.purpose === '独立检验'))
const eventSetSummary = computed(() => isNananxiHistory.value
  ? `候选 ${candidateEvents.value.length} · 率定 ${calibrationEvents.value.length} · 验证 ${validationEvents.value.length} · 独检 ${independentEvents.value.length}`
  : `率定 ${calibrationEvents.value.length} · 验证 ${validationEvents.value.length}`)
const objectiveWeightTotal = computed(() => Object.entries(cal.criteria.metrics)
  .filter(([, enabled]) => enabled)
  .reduce((sum, [key]) => sum + Number(cal.criteria.weights[key] || 0), 0))
const objectiveWeightValid = computed(() => Math.abs(objectiveWeightTotal.value - 1) < 0.001)
const activeAlgorithm = computed(() => algorithms.value.find((item) => item.id === cal.algorithm))
const showLakeBalance = computed(() => cal.executionAvailability.ready && app.hasLakeBalance && cal.targetMode !== 'section-flow')
const statusClass = computed(() => ({
  hot: cal.runStatus === 'complete',
  warn: cal.runStatus === 'needs-run' || cal.runStatus === 'invalid'
}))
const processTitle = computed(() => {
  if (workMode.value === 'simulation') {
    const result = cal.visibleSimulationResult
    const start = result?.startTime || cal.simulationStart
    const end = result?.endTime || cal.simulationEnd
    return `${start.replace('T', ' ')} 至 ${end.replace('T', ' ')} · ${cal.section}${result ? ' · 计算时段' : ''}`
  }
  if (calMode.value === 'continuous') {
    const period = continuousResultPeriods.value.find(row => row.id === selectedContinuousPeriod.value)
    return period ? `${period.purpose}期 · ${period.start.replace('T', ' ')} 至 ${period.end.replace('T', ' ')} · ${cal.section}` : `${cal.section} · 连续时段率定`
  }
  const frozenEvent = cal.visibleRunResult?.frozenRequest?.period?.events?.find((row) => row.id === cal.eventId)
  if (backendHydrograph.value && frozenEvent) return `${frozenEvent.start.replace('T', ' ')} 至 ${frozenEvent.end.replace('T', ' ')} · ${cal.section} · 场次时段`
  const process = activeFlood.value ? floodDisplayName(activeFlood.value) : '当前过程'
  return `${process} · ${cal.section}`
})
const backendHydrograph = computed(() => {
  if (workMode.value === 'simulation') return cal.visibleSimulationResult?.resultSource === 'model-backend' ? cal.visibleSimulationResult.hydrograph : null
  return matchingCalibrationResult.value?.resultSource === 'model-backend' ? matchingCalibrationResult.value.hydrographs?.[calMode.value === 'continuous' ? selectedContinuousPeriod.value : cal.eventId] || null : null
})
const processResult = computed(() => workMode.value === 'simulation' ? cal.visibleSimulationResult : matchingCalibrationResult.value)
const trialRows = computed(() => {
  if (workMode.value !== 'calibration' || !processResult.value?.frozenRequest || processResult.value.calibrationTarget === 'routing') return []
  const key = trialConditions(processResult.value.frozenRequest)
  const period = calMode.value === 'continuous' ? selectedContinuousPeriod.value : cal.eventId
  return cal.trialRecords.filter(row => row.conditions === key).map((row, index) => ({
    ...row, label: index === 0 ? '基准曲线' : `方案 ${index}${row.manual ? ' · 人工试调' : ' · 自动率定'}`,
    color: TRIAL_COLORS[index % TRIAL_COLORS.length],
    hydrograph: (row.taskId === processResult.value.taskId ? processResult.value : trialResults[row.taskId])?.hydrographs?.[period]
  }))
})
const trialComparisons = computed(() => trialRows.value.filter(row => row.visible && row.hydrograph && row.taskId !== processResult.value?.taskId))
const currentTrialCurve = computed(() => trialRows.value.find(row => row.taskId === processResult.value?.taskId) || { label: '模拟流量', color: '#D55E00' })
watch(() => cal.trialRecords.map(row => row.taskId).join('|'), async () => {
  trialLoadError.value = ''
  for (const row of cal.trialRecords) {
    if (trialResults[row.taskId]) continue
    try { trialResults[row.taskId] = await tasks.getResult(row.taskId) }
    catch (error) { trialLoadError.value = `过程线读取失败：${error.message}` }
  }
}, { immediate: true })
function setTrialVisible(taskId, visible) {
  const row = cal.trialRecords.find(item => item.taskId === taskId)
  if (row) { row.visible = visible; cal.saveCurrentWorkspace() }
}
const currentMetricRows = computed(() => {
  if (backendHydrograph.value) return formatMetric(backendHydrograph.value.metrics).map((metric) => ({
    ...metric,
    name: metric.name === 'NSE' ? '纳什效率系数（NSE）' : metric.name
  }))
  if (isNananxiHistory.value && !hasModelHydrograph.value) {
    if (isModelInputWorkspace.value) {
      const statistics = historicalEventProcess.value?.statistics
      return [
        { name: '洪水历时', value: statistics ? `${statistics.durationHours} h` : '—' },
        { name: '控制区有效面积', value: statistics?.controlAreaKm2 != null ? `${statistics.controlAreaKm2.toFixed(3)} km²` : '—' },
        { name: '控制区累计雨量', value: statistics?.arealRainfallTotalMm != null ? `${statistics.arealRainfallTotalMm.toFixed(2)} mm` : '—' },
        { name: '网格雨量完整率', value: statistics ? `${(statistics.rainCompleteness * 100).toFixed(1)}%` : '—' },
        { name: '出口流量完整率', value: statistics ? `${(statistics.flowCompleteness * 100).toFixed(1)}%` : '—' },
        { name: '实测峰现时间', value: statistics?.observedPeakTime ? dayjs(statistics.observedPeakTime).format('YYYY-MM-DD HH:mm') : '—' },
        { name: '场次降雨流量检查', value: eventQuality.value ? (eventQuality.value.model_input_ready ? '通过 · 仅诊断' : '未通过') : '待读取' }
      ]
    }
    const prepared = activeEventPackage.value?.observedInputs
    return [
      { name: '洪水历时', value: `${activeFlood.value?.durationHours ?? '—'} h` },
      { name: '参与雨量站', value: prepared ? `${prepared.reportingRainStations} 站` : '载入中' },
      { name: '站均过程雨量', value: prepared?.stationAverageTotalRainMm != null ? `${prepared.stationAverageTotalRainMm} mm` : '—' },
      { name: '最大站过程雨量', value: prepared?.maximumStationTotalRainMm != null ? `${prepared.maximumStationTotalRainMm} mm` : '—' },
      { name: '雨量记录完整率', value: prepared ? `${Math.round(prepared.rainCompleteness * 100)}%` : '—' },
      { name: '流量记录完整率', value: prepared ? `${Math.round(prepared.riverCompleteness * 100)}%` : '—' },
      { name: '实测峰现时间', value: prepared?.observedPeakTime ? dayjs(prepared.observedPeakTime).format('YYYY-MM-DD HH:mm') : '—' },
      { name: '资料状态', value: activeEventPackage.value?.status === 'ready' ? '雨量与流量齐全' : '存在缺项' }
    ]
  }
  return cal.liveMetrics.map((metric) => ({
    ...metric,
    name: metric.name === 'NSE' ? '纳什效率系数（NSE）' : metric.name
  }))
})

const calibrationForcing = computed(() => {
  if (cal.executionAvailability.mode !== 'frontend-fixture') return { precipitation: [], temperature: [], components: {} }
  const simulated = cal.hydrograph.forecast || []
  const eventFactor = (activeEvents.value.findIndex((event) => event.id === cal.eventId) + 1) * 0.13
  const precipitation = simulated.map((_, index) => {
    const pulse1 = Math.max(0, Math.sin((index - 8) / 7)) * (2.2 + eventFactor)
    const pulse2 = Math.max(0, Math.sin((index - 38) / 5)) * (1.4 + eventFactor)
    return +(pulse1 + pulse2).toFixed(1)
  })
  const temperature = simulated.map((_, index) => +(1.8 + Math.sin((index - 5) / 8) * 3.6 + index * 0.015).toFixed(1))
  const components = { rainfall: [], snowmelt: [], glacier: [], baseflow: [] }
  simulated.forEach((item, index) => {
    const value = Number(item.value || 0)
    const warm = Math.max(0, temperature[index])
    const rainShare = Math.min(0.58, 0.34 + precipitation[index] * 0.035)
    const snowShare = Math.min(0.24, 0.1 + warm * 0.018)
    const glacierShare = Math.min(0.18, 0.07 + warm * 0.012)
    const baseShare = Math.max(0.12, 1 - rainShare - snowShare - glacierShare)
    components.rainfall.push(+(value * rainShare).toFixed(1))
    components.snowmelt.push(+(value * snowShare).toFixed(1))
    components.glacier.push(+(value * glacierShare).toFixed(1))
    components.baseflow.push(+(value * baseShare).toFixed(1))
  })
  return { precipitation, temperature, components }
})
const hasModelHydrograph = computed(() => Boolean(backendHydrograph.value?.forecast?.length || (cal.executionAvailability.mode === 'frontend-fixture' && cal.hydrograph.forecast?.length)))
const emptyHydrograph = { times: [], forecast: [], obs: [], prev: [], metrics: null }
const displayHydrograph = computed(() => backendHydrograph.value || (cal.executionAvailability.mode === 'frontend-fixture' ? cal.hydrograph : isNananxiHistory.value ? historicalEventProcess.value?.hydrograph || emptyHydrograph : emptyHydrograph))
const displayForcing = computed(() => backendHydrograph.value
  ? displayHydrograph.value.forcing || { precipitation: [], temperature: [], components: {}, sourceLabel: '旧结果未保存驱动序列，不使用预览资料替代' }
  : (isNananxiHistory.value ? historicalEventProcess.value?.forcing : null) || calibrationForcing.value)

watch(calMode, (mode) => cal.setCalibrationMode(mode), { immediate: true })
watch([eventQuery, eventYear, eventPurposeFilter], () => { eventPage.value = 1 })
watch(eligibleEvents, (events) => {
  if (!manualCandidateIds.value.length) manualCandidateIds.value = events.map((event) => event.eventId || event.id)
})
watch([eventSelectionMethod, eventTopN, eventPeakThreshold, eventSelectionYear, eventSelectionStart, eventSelectionEnd, manualCandidateIds], () => {
  eventAssignment.value = null
}, { deep: true })
watch(eventPageCount, (count) => { eventPage.value = Math.min(eventPage.value, count) })
watch(() => [app.basinId, app.modelScale, app.modelConfigId], ([basinId, modelScale, modelConfigId]) => {
  cal.setContext(basinId, modelScale, modelConfigId)
  calMode.value = cal.calibrationMode
  workMode.value = cal.operationMode
  historicalEventProcess.value = null
  inputProcessError.value = ''
  eventQuality.value = null
  inputProcessLoading.value = false
  historicalEventRequestId += 1
  observationCatalogRequestId += 1
  eventPackageCatalog.value = null
  requestStatus.value = ''
  registeredParameterVersionId.value = null
  const selected = activeEvents.value.find((event) => event.id === cal.eventId) || activeEvents.value[0]
  if (selected) cal.setEvent(selected.id, selected.start, selected.end)
}, { immediate: true })

watch(calSections, (names) => {
  if (caseData.value && !names.includes(cal.section)) cal.setSection(names[0] || '')
}, { immediate: true })

async function refreshObservationRange() {
  const requestId = ++observationCatalogRequestId
  if (!app.hasObservationCatalog) return
  try {
    const catalog = isModelInputWorkspace.value
      ? await loadModelInputCatalog({ source: app.activeContext })
      : await loadNananxiObservationCatalog({ source: app.activeContext })
    if (requestId === observationCatalogRequestId) cal.setObservationRange(catalog?.river)
  } catch {
    // The workspace keeps its last known range when the catalog is unavailable.
  }
}

onMounted(refreshObservationRange)
watch(() => [app.inputSelectionKey, app.backendError], refreshObservationRange)
watch([() => app.inputSelectionKey, () => activeFlood.value?.id, () => activeFlood.value?.start, () => activeFlood.value?.end, () => cal.warmupDays, () => app.backendError, workMode, calMode], async () => {
  const requestId = ++historicalEventRequestId
  historicalEventProcess.value = null
  eventQuality.value = null
  inputProcessError.value = ''
  inputProcessLoading.value = false
  if (!isNananxiHistory.value || !activeFlood.value) {
    historicalEventProcess.value = null
    return
  }
  const event = activeFlood.value
  inputProcessLoading.value = true
  try {
    if (isModelInputWorkspace.value) {
      if (app.backendError) throw new Error(`计算资料暂不可用：${app.backendError}`)
      if (event.runtimeRegistered === false) throw new Error('此场次尚未登记到计算资料目录，请选择已登记的洪水场次。')
      const input = await loadModelInputEvent(event.id, { source: app.activeContext, warmupHours: Math.round(Number(cal.warmupDays) * 24) })
      if (requestId !== historicalEventRequestId) return
      if (dayjs(input.eventPeriod.start).valueOf() !== dayjs(event.start).valueOf() || dayjs(input.eventPeriod.end).valueOf() !== dayjs(event.end).valueOf()) throw new Error('洪水场次时段与计算资料不一致，请重新选择场次。')
      eventQuality.value = input.quality
      historicalEventProcess.value = { ...observedModelProcess(input, event.start, event.end), statistics: modelEventStatistics(input, event.start, event.end) }
      return
    }
    const [windowData, packages] = await Promise.all([
      loadNananxiObservationWindow(event.start, event.end, { source: app.activeContext }),
      eventPackageCatalog.value
        ? Promise.resolve(eventPackageCatalog.value)
        : loadNananxiEventPackages({ source: app.activeContext })
    ])
    if (requestId !== historicalEventRequestId) return
    eventPackageCatalog.value = packages
    cal.enrichEvents(packages.packages || [])
    const rainByTime = new Map()
    windowData.rain.forEach((row) => {
      if (!rainByTime.has(row.time)) rainByTime.set(row.time, [])
      rainByTime.get(row.time).push(Number(row.rainMm) || 0)
    })
    const flowByTime = new Map(windowData.river.map((row) => [row.time, row.flowM3s]))
    const times = [...new Set([...rainByTime.keys(), ...flowByTime.keys()])].sort()
    const labels = times.map((time) => dayjs(time).format('MM-DD HH:mm'))
    historicalEventProcess.value = {
      hydrograph: {
        times: labels,
        forecast: [],
        obs: labels.map((time, index) => ({ time, value: flowByTime.get(times[index]) ?? null }))
      },
      forcing: {
        precipitation: times.map((time) => {
          const values = rainByTime.get(time) || []
          return values.length ? +(values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(2) : null
        }),
        temperature: [],
        components: {}, sourceLabel: '离线原始资料预览 · 站均雨量不是模型整编 驱动'
      }
    }
  } catch (error) {
    if (requestId === historicalEventRequestId) { historicalEventProcess.value = null; inputProcessError.value = error.message }
  } finally {
    if (requestId === historicalEventRequestId) inputProcessLoading.value = false
  }
}, { immediate: true })

function formatEventPeriod(event) {
  return `${dayjs(event.start).format('YYYY-MM-DD HH:mm')} 至 ${dayjs(event.end).format('MM-DD HH:mm')}`
}

function compactEventName(event) {
  if (event.sourceFloodNo) return String(event.sourceFloodNo)
  return dayjs(event.start).format('YYYYMMDD')
}

function floodDisplayName(event) {
  if (!event) return '当前洪水'
  return app.basinId === 'nananxi-xufan' ? `${dayjs(event.start).format('YYYY-MM-DD')} 洪水` : event.name
}

function eventPeak(event) {
  if (event.observedPeak != null) return `${Number(event.observedPeak).toLocaleString()} m³/s`
  const match = String(event.note || '').match(/洪峰\s*([\d.]+)/)
  return match ? `${Number(match[1]).toLocaleString()} m³/s` : '—'
}

function toggleEventSort(key) {
  if (eventSortKey.value === key) eventSortDirection.value = eventSortDirection.value === 'asc' ? 'desc' : 'asc'
  else {
    eventSortKey.value = key
    eventSortDirection.value = key === 'peak' ? 'desc' : 'asc'
  }
  eventPage.value = 1
}

function eventSortIndicator(key) {
  return eventSortKey.value === key ? (eventSortDirection.value === 'asc' ? '↑' : '↓') : '↕'
}

function eventSortAria(key) {
  if (eventSortKey.value !== key) return 'none'
  return eventSortDirection.value === 'asc' ? 'ascending' : 'descending'
}

function metricText(metrics) {
  if (!metrics) return '待返回'
  const nse = metrics.nse == null ? NaN : Number(metrics.nse)
  const peakValue = metrics.peakErrorPercent ?? metrics.peakErr
  const peak = peakValue == null ? NaN : Number(peakValue)
  const parts = []
  if (Number.isFinite(nse)) parts.push(`NSE ${nse.toFixed(3)}`)
  if (Number.isFinite(peak)) parts.push(`峰值误差 ${peak.toFixed(1)}%`)
  return parts.join(' · ') || metrics.status || '已返回'
}

function parameterVersionLabel(version) {
  if (version === 'NANANXI_XAJ_REFERENCE_V1') return '新安江参考参数'
  return version.startsWith('PARAM-JOB-') ? `率定参数 · ${version.slice(-8)}` : version
}

function releaseReadinessFor(version) {
  return getReleaseReadiness(version)
}

function selectFlood(event) {
  if (event) cal.setEvent(event.id, event.start, event.end)
}

function selectFloodById(id) {
  selectFlood(activeEvents.value.find((event) => event.id === id))
}

function toggleManualCandidate(event, checked) {
  const id = String(event.eventId || event.id)
  const selected = new Set(manualCandidateIds.value.map(String))
  if (checked) selected.add(id)
  else selected.delete(id)
  manualCandidateIds.value = [...selected]
  eventSelectionMethod.value = 'manual'
}

function assignEventsByRatio() {
  const seed = eventSplitSeed.value || '42'
  const candidates = candidateEvents.value
  if (!candidates.length) {
    ElMessage.warning('当前筛选条件没有可用洪水场次。')
    return
  }
  const assignments = stratifiedRandomEventSplit(candidates, seed)
  const reset = activeEvents.value.map((event) => ({ id: event.id, purpose: '未使用' }))
  cal.setEventPurposes([...reset, ...assignments])
  eventAssignment.value = {
    method: 'stratified-random-by-observed-peak',
    seed,
    ratios: { calibration: 0.7, validation: 0.2, independentTest: 0.1 },
    selectedEventIds: candidates.map((event) => event.eventId || event.id),
    selection: eventSelectionPayload.value,
    snapshot: candidates.map((event) => ({
      id: event.eventId || event.id,
      sourceFloodNo: event.sourceFloodNo || null,
      start: event.start,
      end: event.end,
      observedPeak: event.observedPeak,
      volumeM3: event.volumeM3 ?? event.volume ?? null,
      rainCompleteness: event.rainCompleteness ?? null,
      flowCompleteness: event.flowCompleteness ?? event.riverCompleteness ?? null,
      revision: Number(event.revision) || 1
    })),
    counts: {
      calibration: assignments.filter((item) => item.purpose === '率定').length,
      validation: assignments.filter((item) => item.purpose === '验证').length,
      independentTest: assignments.filter((item) => item.purpose === '独立检验').length
    }
  }
  ElMessage.success(`已按洪峰分层随机划分：率定 ${eventAssignment.value.counts.calibration} 场、验证 ${eventAssignment.value.counts.validation} 场、独立检验 ${eventAssignment.value.counts.independentTest} 场；种子 ${seed}`)
}

function setEventPurposeManually(id, purpose) {
  eventAssignment.value = null
  cal.setEventPurpose(id, purpose)
}

function assignAllCalibration() {
  eventAssignment.value = null
  const candidates = candidateEvents.value
  const candidateIds = new Set(candidates.map((event) => event.id))
  cal.setEventPurposes(activeEvents.value.map((event) => ({ id: event.id, purpose: candidateIds.has(event.id) ? '率定' : '未使用' })))
  ElMessage.success(`已将 ${candidates.length} 场洪水设为率定用途`)
}

function clearEventPurposes() {
  eventAssignment.value = null
  cal.setEventPurposes(activeEvents.value.map((event) => ({ id: event.id, purpose: '未使用' })))
}

function selectWholeLake() {
  cal.setSection('色林错全湖')
  cal.setTargetMode('joint')
  ElMessage.success('已切换为色林错全湖水量平衡评价。')
}

function setWorkMode(mode) {
  workMode.value = mode
  cal.setOperationMode(mode)
}

async function startCalibration() {
  if (!calculationCanRun.value) {
    ElMessage.warning(calculationBlockReason.value)
    return
  }
  if (app.basinId === 'nananxi-xufan') {
    await prepareBackendTask('calibration')
    return
  }
  const result = await cal.runCalibration()
  if (!result.ok) {
    ElMessage.warning(result.errors.join('；'))
    return
  }
  ElMessage.success(`率定完成，综合评分 ${result.result.score.toFixed(1)} 分。`)
}

async function startSimulation() {
  if (!calculationCanRun.value) {
    ElMessage.warning(calculationBlockReason.value)
    return
  }
  if (app.basinId === 'nananxi-xufan') {
    await prepareBackendTask('simulation')
    return
  }
  const result = await cal.runContinuousSimulation()
  if (!result.ok) {
    ElMessage.warning(result.errors.join('；'))
    return
  }
  ElMessage.success(`连续模拟完成，末状态 ${result.result.endStateVersionId}`)
}

async function startManualTrial() {
  if (!manualCanRun.value) return
  const prior = cal.runResult
  if (prior?.frozenRequest && prior.calibrationTarget !== 'routing' && !cal.trialRecords.some(row => row.taskId === prior.taskId)) {
    cal.trialRecords.push({ taskId: prior.taskId, conditions: trialConditions(prior.frozenRequest),
      parameterVersionId: prior.parameterVersionId, parameters: { ...prior.bestParameters },
      completedAt: prior.completedAt, manual: prior.evaluationMode === 'manual', visible: true })
    trialResults[prior.taskId] = prior
    cal.saveCurrentWorkspace()
  }
  await prepareBackendTask('calibration', true)
}

async function prepareBackendTask(operationMode, manual = false) {
  const request = buildCalibrationRequest({
    app,
    calibration: cal,
    operationMode,
    events: registeredRuntimeEvents.value,
    eventAssignment: eventAssignment.value,
    eventSelection: eventSelectionPayload.value
  })
  if (manual) {
    request.calibration.evaluationMode = 'manual'
    delete request.calibration.searchParameters
    delete request.calibration.routingSearchParameters
    delete request.calibration.target
  }
  cal.running = true
  const requestedContextKey = cal.contextKey
  const requestedInputKey = app.inputSelectionKey
  requestStatus.value = ''
  try {
    const outcome = await cal.runBackendRequest(request)
    if (app.contextKey !== requestedContextKey || app.inputSelectionKey !== requestedInputKey) return
    if (!outcome.ok) throw new Error(outcome.errors.join('；'))
    registeredParameterVersionId.value = outcome.result.parameterVersionId || null
    requestStatus.value = `任务 ${outcome.task.taskId} 已完成。`
    ElMessage.success('计算完成')
  } catch (error) {
    if (app.contextKey !== requestedContextKey || app.inputSelectionKey !== requestedInputKey) return
    requestStatus.value = error?.message || String(error)
    ElMessage.error(requestStatus.value)
  } finally {
    if (cal.contextKey === requestedContextKey) cal.running = false
  }
}

function reviewCandidate() {
  if (!activeParameterVersion.value) return
  try {
    const version = reviewSimulationParameter({
      projectData: app.projectData,
      activeContext: app.activeContext,
      projectId: app.basinId,
      resolutionId: app.modelScale,
      versionId: activeParameterVersion.value.id,
      decision: 'approve'
    })
    registeredParameterVersionId.value = version.id
    ElMessage.success('率定参数已审核通过，可进入发布环节。')
  } catch (error) {
    ElMessage.warning(error?.message || String(error))
  }
}

function publishCandidate() {
  if (!activeParameterVersion.value) return
  try {
    publishAutomaticParameter({
      projectData: app.projectData,
      activeContext: app.activeContext,
      projectId: app.basinId,
      resolutionId: app.modelScale,
      versionId: activeParameterVersion.value.id
    })
    ElMessage.success('参数已发布到自动预报参数库。')
  } catch (error) {
    ElMessage.warning(error?.message || String(error))
  }
}

function saveSimulationState() {
  const result = cal.visibleSimulationResult
  if (!result?.endStateVersionId) return
  const saved = state.saveInitialState({
    id: result.endStateVersionId,
    time: String(result.stateTime || result.endTime || cal.simulationEnd).replace('T', ' '),
    domainId: app.basinId,
    modelScale: app.modelScale,
    modelConfigId: app.modelConfigId,
    sourceSection: cal.section,
    sourceRun: result.taskId,
    source: '连续模拟末时刻状态',
    analysisStatus: '可继续续算',
    parentStateVersionId: result.parentStateVersionId ?? null,
    inputBundleVersionId: result.resultSource === 'model-backend' ? frozenInputBundleVersionId(result) : app.workContext.inputBundleVersionId,
    parameterVersionId: result.lineage?.parameterVersionId || cal.paramVersion,
    sourceTaskId: result.taskId,
    resultSource: result.resultSource || 'frontend-fixture',
    means: result.means || { ...state.means },
    parameters: result.parameters,
    productionEligible: result.productionEligible
  })
  app.setStateVersion(saved.id)
  app.prepareHandoff('state', {
    sourcePage: 'calibration',
    sourceLabel: `连续模拟 ${result.taskId}`,
    issueTime: saved.time,
    inputBundleVersionId: saved.inputBundleVersionId,
    parameterVersionId: saved.parameterVersionId,
    stateVersionId: saved.id,
    sourceTaskId: result.taskId
  })
  ElMessage.success(`末状态已保存：${saved.id}`)
  router.push('/state')
}
</script>

<style scoped>
.manual-trial-actions { padding: 12px; margin-bottom: 12px; border: 1px solid #bad7d6; border-radius: 8px; background: #f1f8f7; }
.manual-trial-actions button { width: 100%; }
.manual-trial-actions span, .manual-trial-actions p { display: block; margin: 7px 0 0; color: #426c70; font-size: 11px; }
.trial-ledger { margin: 10px 0 0; padding: 10px 12px; border-top: 1px solid #dce7e9; background: #f8fbfb; max-height: 240px; overflow: auto; }
.trial-ledger-head { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; font-size: 12px; color: #24505a; margin-bottom: 8px; }
.trial-ledger-head span { color: #6c838c; font-size: 10px; }
.trial-entry { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px; padding: 7px 0; border-top: 1px solid #e5eded; font-size: 11px; }
.trial-entry label { display: flex; align-items: center; gap: 5px; color: #263e4e; }
.trial-entry i { width: 16px; height: 3px; }
.trial-change { flex: 1 1 130px; min-width: 0; overflow-wrap: anywhere; color: #586f7a; font-variant-numeric: tabular-nums; }
.trial-score { font-variant-numeric: tabular-nums; color: #176c75; }
.view-cal {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 10px;
  min-height: 100%;
}

.continuous-process-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.continuous-process-tabs button { padding: 7px 18px; border: 1px solid #cfdddf; background: #f5f9fa; color: #526b77; font: inherit; cursor: pointer; }
.continuous-process-tabs button.active { background: #e7f3ef; border-color: #258276; color: #155e55; font-weight: 600; }
.continuous-process-tabs button:focus-visible { outline: 2px solid #258276; outline-offset: 2px; }
.search-stage-results { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; margin: 12px 0; }
.search-stage-results > div { display: grid; grid-template-columns: 1fr auto; gap: 5px 10px; padding: 12px; border: 1px solid #dce6e8; border-left: 3px solid #258276; background: #f5f9f8; }
.search-stage-results b { color: #27574f; font-size: 12px; }
.search-stage-results span, .search-stage-results small { color: #637b7c; font-size: 11px; }
.search-stage-results strong { font: 600 19px ui-monospace, monospace; color: #21584e; }
.search-stage-results small { grid-column: 1 / -1; }

.handoff-note { grid-column: 1 / -1; margin: 0; padding: 6px 10px; background: #f4f8fb; color: #31506f; font-size: 12px; }

.operation-tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; padding: 4px; background: #d8e2e8; }
.operation-tabs button { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 10px; min-height: 40px; padding: 7px 14px; border: 0; background: #f8fafb; color: #65798a; text-align: left; cursor: pointer; }
.operation-tabs button b { color: #24445d; font-size: 13px; }
.operation-tabs button span { font-size: 11px; }
.operation-tabs button.active { background: #edf7f4; box-shadow: inset 3px 0 0 #237b70; }
.operation-tabs button.active b { color: #17665c; }

.calibration-layout {
  display: grid;
  grid-template-columns: minmax(236px, 260px) minmax(0, 1fr) minmax(236px, 260px);
  align-items: stretch;
  gap: 10px;
  min-height: 0;
}
.calibration-layout.simulation-mode { grid-template-columns: minmax(280px, 330px) minmax(0, 1fr); }
.simulation-result-card { display: grid; gap: 4px; margin-top: 12px; padding: 10px; border-left: 3px solid #27835b; background: #f2f8f5; }
.simulation-result-card span,.simulation-result-card small { color: #647b70; font-size: 10px; }
.simulation-result-card b { overflow-wrap: anywhere; color: #18543b; font-size: 12px; }

.setting-panel,
.parameter-panel,
.analysis-column {
  min-width: 0;
  min-height: 0;
}
.below-grid.single-result { grid-template-columns: 1fr; }

.setting-panel,
.parameter-panel {
  overflow: auto;
}

.analysis-column {
  display: grid;
  grid-template-rows: auto auto;
  align-content: start;
  gap: 8px;
  overflow: auto;
}

.intro,
.section-note,
.field-note,
.method-note,
.run-message,
.parameter-intro {
  color: #5b7080;
  font-size: 12px;
  line-height: 1.5;
}

.intro { margin: -2px 0 12px; }
.section-note { margin: 7px 0 9px; font-size: 11px; }
.field-note { margin-top: -1px; font-size: 11px; }
.method-note { margin: 7px 0 0; font-size: 11px; }
.run-message { min-height: 18px; margin: 8px 0 0; }
.run-message.warn { color: var(--orange); font-weight: 700; }
.request-status { padding: 8px 10px; border-left: 3px solid #2a7b70; background: #eef7f4; color: #245e57; }
.parameter-intro { margin: 0 0 10px; }
.parameter-result-card { display: grid; gap: 8px; margin-top: 12px; padding: 10px; border: 1px solid #d5e2e6; border-left: 3px solid #2b8278; background: #f7fbfa; }
.parameter-result-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.parameter-result-head div { display: grid; gap: 3px; min-width: 0; }
.parameter-result-head b { color: #194b50; font-size: 12px; }
.parameter-result-head span:not(.tag) { overflow-wrap: anywhere; color: #52717a; font-size: 10px; }
.parameter-result-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; background: #dbe8e8; }
.parameter-result-grid div { display: grid; gap: 3px; padding: 7px; background: #fff; }
.parameter-result-grid span { color: #71858a; font-size: 10px; }
.parameter-result-grid b { overflow-wrap: anywhere; color: #294f56; font-size: 11px; }
.parameter-result-card p { margin: 0; color: #60777c; font-size: 10px; line-height: 1.45; }
.independent-event-results { display: grid; gap: 1px; overflow-x: auto; border: 1px solid #dbe8e8; background: #dbe8e8; }
.independent-event-title { padding: 6px 7px; background: #eef7f4; color: #2d5f61; font-size: 10px; font-weight: 800; }
.independent-event-row { display: grid; grid-template-columns: minmax(92px, 1.2fr) repeat(4, minmax(60px, .8fr)); min-width: 380px; gap: 1px; background: #fff; color: #47656b; font-size: 9px; }
.independent-event-row span { padding: 5px 6px; font-variant-numeric: tabular-nums; }
.independent-event-head { background: #f6fbfa; color: #6b8386; font-weight: 800; }
.parameter-result-note { padding-top: 2px; }
.parameter-result-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.parameter-result-actions .mini { min-height: 27px; padding: 0 9px; font-size: 10px; }
.ok-text { color: #187653 !important; }
.warn-text { color: #a55d18 !important; }

.mode-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  margin-bottom: 12px;
  padding: 3px;
  border: 1px solid #cbd9e2;
  border-radius: 6px;
  background: #f3f6f8;
}

.mode-tabs button,
.process-tabs button,
.param-groups button {
  cursor: pointer;
  transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease;
}

.mode-tabs button {
  min-height: 32px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #52697c;
  font-size: 12px;
  font-weight: 800;
}

.mode-tabs button.active { background: var(--blue); color: #fff; }

.data-list {
  display: grid;
  gap: 0;
  margin-bottom: 10px;
  border-top: 1px solid #dce6ec;
  border-bottom: 1px solid #dce6ec;
}

.data-list div {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  gap: 8px;
  padding: 7px 0;
  border-top: 1px solid #edf2f4;
}

.data-list div:first-child { border-top: 0; }
.data-list span { color: #6a7d8b; font-size: 11px; }
.data-list b { overflow-wrap: anywhere; color: #244454; font-size: 11px; line-height: 1.35; }

.setting-panel :deep(.field) { margin-top: 10px; }
.lake-shortcut { width: 100%; margin-top: 8px; color: var(--blue); background: #f5faff; }

.event-counts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 7px 0 0;
  border: 1px solid #d8e4e9;
}

.event-counts div { display: grid; gap: 2px; padding: 7px; border-left: 1px solid #d8e4e9; }
.event-counts div:first-child { border-left: 0; }
.event-counts span { color: #6b7f8d; font-size: 10px; }
.event-counts b { color: #173f52; font-size: 12px; font-variant-numeric: tabular-nums; }
.event-batch-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; padding: 5px 10px; border-top: 1px solid #dce6ea; background: #f7fafb; }
.event-batch-actions label { display: inline-flex; align-items: center; gap: 5px; }
.event-batch-actions span { color: #6b7f8d; font-size: 10px; }
.event-batch-actions input { width: 70px; height: 24px; padding: 0 6px; border: 1px solid #c9d8df; background: #fff; color: #244454; font-size: 10px; font-variant-numeric: tabular-nums; }
.event-batch-actions button { min-height: 24px; padding: 0 8px; border: 1px solid #c9d8df; background: #fff; color: #365666; cursor: pointer; font-size: 10px; font-weight: 700; }
.event-batch-actions button:hover { border-color: #4b8e83; color: #17665c; }
.event-selection-panel { padding: 6px 10px; border-top: 1px solid #dce6ea; border-bottom: 1px solid #dce6ea; background: #fbfdfd; }
.event-selection-fields { display: flex; flex-wrap: wrap; align-items: end; gap: 7px; }
.event-selection-fields label { display: grid; gap: 3px; min-width: 130px; color: #64777d; font-size: 9px; }
.event-selection-fields select,
.event-selection-fields input { width: 100%; height: 28px; padding: 0 6px; border: 1px solid #bdccd2; border-radius: 3px; background: #fff; color: #284751; font-size: 10px; }
.event-selection-count { display: inline-flex; align-items: baseline; gap: 4px; min-height: 28px; padding: 0 8px; border-left: 3px solid #2a8178; background: #f1f8f6; color: #658087; font-size: 10px; }
.event-selection-count b { color: #14776e; font-size: 15px; font-variant-numeric: tabular-nums; }
.event-selection-note { margin-top: 4px; color: #6b7d82; font-size: 9px; line-height: 1.3; }
.event-selection-stats { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 7px; color: #657b82; font-size: 9px; }
.event-selection-stats b { color: #245b62; font-variant-numeric: tabular-nums; }
.data-inline-control { display: inline-flex; align-items: center; gap: 3px; color: #3d5f67; font-weight: 800; }
.data-inline-input { width: 54px; height: 24px; padding: 0 5px; border: 1px solid #bdccd2; border-radius: 3px; background: #fff; color: #1c4a54; font-size: 11px; font-weight: 800; text-align: right; }

.selected-event {
  display: grid;
  gap: 3px;
  margin-top: 7px;
  padding: 7px 8px;
  border-left: 3px solid #3c8f86;
  background: #f3faf8;
}

.selected-event span { color: #607886; font-size: 10px; }
.selected-event b { color: #214554; font-size: 11px; }

.event-create {
  margin-top: 9px;
  padding: 0 9px;
  border: 1px solid #d3e0e8;
  border-radius: 6px;
  background: #fafcfd;
}

.event-create summary { min-height: 36px; padding-top: 10px; color: #2b5e78; font-size: 12px; font-weight: 800; cursor: pointer; }
.event-create[open] { padding-bottom: 10px; }
.event-create .btn { margin-top: 10px; }
.period-fields { display: grid; gap: 8px; }

.lake-balance {
  margin-top: 12px;
  padding: 10px;
  border: 1px solid #bcd5d3;
  border-left: 3px solid var(--teal);
  border-radius: 6px;
  background: #f5fbfa;
}

.lake-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #173f46; font-size: 12px; }
.lake-head span { color: #5d7b80; font-size: 10px; }
.lake-values { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin-top: 9px; background: #d5e6e4; }
.lake-values div { display: grid; gap: 2px; padding: 7px; background: #fff; }
.lake-values span { color: #72878b; font-size: 9px; }
.lake-values b { color: #173f46; font-size: 11px; font-variant-numeric: tabular-nums; }
.lake-balance p { margin: 8px 0 0; color: #60777c; font-size: 10px; line-height: 1.45; }

.objective-list { display: grid; gap: 6px; margin-top: 8px; }
.objective-row { display: flex; align-items: center; justify-content: space-between; gap: 7px; color: #3a4d68; font-size: 11px; cursor: pointer; }
.objective-row > span:first-child { display: flex; align-items: center; gap: 5px; min-width: 0; }
.objective-row > span:last-child { display: inline-flex; align-items: center; gap: 4px; color: #667d90; white-space: nowrap; }
.objective-row input[type='number'] { width: 46px; height: 25px; padding: 2px 4px; border: 1px solid #cbd9e8; border-radius: 4px; color: #24435b; font: inherit; font-variant-numeric: tabular-nums; }
.weight-status { margin: 7px 0 0; color: #59718a; font-size: 11px; line-height: 1.4; }
.weight-status.warn { color: var(--orange); font-weight: 700; }

.threshold-grid,
.optimizer-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.threshold-grid .field label,
.optimizer-grid .field label { font-size: 11px; }

.action-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 7px; margin-top: 12px; }
.action-row .btn:disabled { cursor: not-allowed; opacity: 0.62; }

.process-panel { display: flex; flex-direction: column; height: max-content; min-height: 500px; }
.process-panel > :not(.hydro) { flex-shrink: 0; }
.input-quality-note { margin: 0 0 8px; padding: 7px 9px; background: #edf6f3; color: #285c53; font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }
.input-quality-note.warn { background: #fff6e8; color: #8c541e; }
.result-provenance { margin-bottom: 8px; padding: 7px 9px; background: #edf6f3; color: #285c53; font-size: 11px; line-height: 1.5; }.result-provenance summary { cursor: pointer; }.result-provenance.warn { background: #fff6e8; color: #8c541e; }.result-provenance dl { display: grid; grid-template-columns: 60px minmax(0, 1fr); gap: 4px 8px; margin: 8px 0 0; }.result-provenance dd { margin: 0; overflow-wrap: anywhere; }
.process-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.process-head .panel-title { margin-bottom: 2px; }
.process-subtitle { margin: 0 0 8px 13px; color: #5b7080; font-size: 12px; }
.process-tools { display: flex; align-items: center; gap: 8px; padding-top: 1px; }
.process-tabs { display: inline-flex; padding: 2px; border: 1px solid #cbd9e2; border-radius: 5px; background: #f3f6f8; }
.process-tabs button { min-height: 28px; padding: 0 9px; border: 0; border-radius: 3px; background: transparent; color: #52697c; font-size: 11px; font-weight: 800; white-space: nowrap; }
.process-tabs button.active { background: #fff; color: #126a64; box-shadow: 0 1px 3px rgba(28, 66, 79, 0.14); }
.component-switches { display: flex; flex-wrap: wrap; align-items: center; gap: 7px 13px; margin: 0 0 7px; padding: 7px 9px; border: 1px solid #dce6f2; border-radius: 5px; background: #f7faff; color: #40536d; font-size: 11px; }
.component-switches > span { color: #173052; font-weight: 800; }
.component-switches label { display: flex; align-items: center; gap: 5px; cursor: pointer; }
.hydro { display: grid; grid-template-rows: minmax(320px, 1fr); min-width: 0; min-height: 340px; height: 340px; flex: 0 0 340px; }

.below-grid { display: grid; grid-template-columns: minmax(320px, 1.35fr) minmax(240px, 0.9fr); gap: 10px; }
.below-grid.single-result { grid-template-columns: 1fr; }
.events-panel,
.result-panel { min-width: 0; }
.events-panel {
  display: grid;
  grid-template-rows: auto auto auto auto minmax(0, 1fr) auto;
  align-self: start;
  max-height: 500px;
  min-height: 0;
  overflow: hidden;
}
.event-panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 10px 12px 8px; border-bottom: 1px solid #d9e3e7; background: #f7faf9; }
.event-panel-head h2 { margin: 0; color: #183a42; font-size: 14px; }
.event-panel-head p { overflow: hidden; margin: 3px 0 0; color: #64777d; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.event-range { flex: 0 0 auto; padding: 3px 7px; border: 1px solid #bdd2d0; border-radius: 3px; background: #fff; color: #28645f; font-size: 10px; font-variant-numeric: tabular-nums; }
.event-toolbar { display: grid; grid-template-columns: minmax(150px, 1fr) 90px 100px auto; gap: 6px; align-items: end; padding: 8px 10px; border-bottom: 1px solid #dfe7ea; }
.event-toolbar label { display: grid; gap: 3px; color: #64777d; font-size: 9px; }
.event-toolbar input,
.event-toolbar select { width: 100%; height: 29px; padding: 0 7px; border: 1px solid #bdccd2; border-radius: 3px; background: #fff; color: #243f48; font-size: 11px; outline: none; }
.event-toolbar input:focus,
.event-toolbar select:focus { border-color: #237b70; box-shadow: 0 0 0 2px rgba(35, 123, 112, 0.1); }
.event-match { align-self: end; min-width: 48px; height: 29px; padding: 7px 6px 0; color: #28555e; font-size: 10px; text-align: right; white-space: nowrap; }
.event-table { min-width: 0; min-height: 0; overflow: auto; }
.event-table-row { display: grid; grid-template-columns: minmax(92px, .75fr) minmax(190px, 1.8fr) minmax(82px, .75fr) 92px; align-items: center; min-height: 29px; border-bottom: 1px solid #e5ebed; }
.event-table.with-pick .event-table-row { grid-template-columns: 34px minmax(82px, .7fr) minmax(180px, 1.7fr) minmax(82px, .7fr) 92px; }
.event-table-row > * { min-width: 0; }
.event-table-header { min-height: 24px; background: #eef3f3; color: #60747a; font-size: 9px; font-weight: 800; }
.event-table-header span { padding: 0 9px; }
.event-sort { display: inline-flex; align-items: center; justify-content: space-between; gap: 4px; width: 100%; height: 24px; padding: 0 9px; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.event-sort:hover { background: #e1eceb; color: #174e58; }
.event-sort i { color: #6e8589; font-size: 11px; font-style: normal; font-weight: 900; }
.event-sort:focus-visible { outline: 2px solid #178276; outline-offset: -2px; }
.event-table-header .event-pick-head { padding: 0; text-align: center; }
.event-table-row:not(.event-table-header):hover { background: #f6faf9; }
.event-table-row.active { background: #eaf6f2; box-shadow: inset 3px 0 0 #147d73; }
.event-code,
.event-period { overflow: hidden; height: 29px; padding: 0 9px; border: 0; background: transparent; color: #1f4653; font-size: 10px; text-align: left; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.event-code { color: #155e75; font-size: 11px; font-weight: 800; font-variant-numeric: tabular-nums; }
.event-peak { padding: 0 9px; color: #b65319; font-size: 10px; font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; }
.event-purpose { width: 82px; height: 25px; margin-right: 8px; padding: 0 4px; border: 1px solid #bdccd2; border-radius: 3px; background: #fff; color: #39535c; font-size: 10px; }
.event-pick { display: grid; place-items: center; height: 29px; cursor: pointer; }
.event-pick input { width: 14px; height: 14px; accent-color: #147d73; }
.event-empty { display: grid; min-height: 94px; place-items: center; color: #72848a; font-size: 11px; }
.event-pagination { display: flex; align-items: center; justify-content: space-between; min-height: 31px; padding: 4px 10px; background: #f7f9f9; color: #62767c; font-size: 10px; }
.event-pagination div { display: flex; gap: 5px; }
.event-pagination button { min-width: 54px; height: 25px; border: 1px solid #bdccd2; border-radius: 3px; background: #fff; color: #285b65; font-size: 10px; cursor: pointer; }
.event-pagination button:disabled { color: #9ba8ac; cursor: default; opacity: .6; }
.event-code:focus-visible,
.event-period:focus-visible,
.mode-tabs button:focus-visible,
.process-tabs button:focus-visible,
.param-groups button:focus-visible { outline: 2px solid #0b628c; outline-offset: 2px; }

.result-summary { padding: 9px 10px; border-left: 3px solid var(--teal); background: #f2faf8; }
.result-summary.stale { border-left-color: var(--orange); background: #fff8f1; }
.result-summary.pending { border-left-color: #7f99a9; background: #f4f8fa; }
.result-summary > div { display: flex; align-items: baseline; gap: 4px; }
.result-summary span { color: #607886; font-size: 11px; }
.result-summary b { color: #0f766e; font-size: 25px; font-variant-numeric: tabular-nums; }
.result-summary.stale b { color: var(--orange); }
.result-summary.pending b { color: #34586a; }
.result-summary em { color: #607886; font-size: 11px; font-style: normal; }
.result-summary p { margin: 5px 0 0; color: #42616d; font-size: 11px; line-height: 1.4; }
.result-time { margin: 7px 0; color: #6a7f8e; font-size: 10px; }
.result-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-top: 1px solid #dce6ec; border-left: 1px solid #dce6ec; }
.result-metrics div { display: grid; gap: 3px; min-width: 0; padding: 7px; border-right: 1px solid #dce6ec; border-bottom: 1px solid #dce6ec; }
.result-metrics span { color: #667c8a; font-size: 10px; }
.result-metrics b { color: #193b4d; font-size: 11px; font-variant-numeric: tabular-nums; line-height: 1.3; overflow-wrap: anywhere; }
.result-metrics b.ok { color: var(--green); }
.result-metrics b.warn { color: var(--orange); }
.result-metrics b.bad { color: var(--red); }

.param-groups { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; margin-bottom: 10px; padding: 3px; border: 1px solid #cbd9e2; border-radius: 6px; background: #f3f6f8; }
.param-groups button { min-height: 30px; border: 0; border-radius: 4px; background: transparent; color: #52697c; font-size: 11px; font-weight: 800; }
.param-groups button.active { background: #fff; color: #126a64; box-shadow: 0 1px 3px rgba(28, 66, 79, 0.14); }

@media (min-width: 1281px) {
  .view-cal { height: 100%; }
  .setting-panel,
  .parameter-panel { max-height: 100%; }
}

@media (max-width: 1280px) {
  .view-cal { height: auto; }
  .calibration-layout { grid-template-columns: minmax(256px, 292px) minmax(0, 1fr); align-items: start; }
  .parameter-panel { grid-column: 1 / -1; overflow: visible; }
  .parameter-panel :deep(.param-list) { max-height: 360px; }
  .analysis-column { grid-template-rows: auto auto; }
  .process-panel { min-height: 560px; }
  .setting-panel { overflow: visible; }
}

@media (max-width: 900px) {
  .calibration-layout,
  .calibration-layout.simulation-mode,
  .below-grid { grid-template-columns: 1fr; }
  .parameter-panel { grid-column: auto; }
  .process-panel { min-height: 500px; }
  .event-toolbar { grid-template-columns: minmax(140px, 1fr) repeat(2, minmax(84px, auto)) auto; }
}

@media (max-width: 640px) {
  .metric-strip { grid-auto-flow: row; grid-auto-columns: auto; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric { min-height: 46px; justify-content: flex-start; padding: 7px 9px; border-bottom: 1px solid var(--line); }
  .metric:nth-child(2n) { border-right: 0; }
  .process-head { display: grid; gap: 7px; }
  .process-tools { padding-left: 13px; }
  .event-toolbar { grid-template-columns: 1fr 1fr; }
  .event-search { grid-column: 1 / -1; }
  .event-match { display: none; }
  .event-table-row { grid-template-columns: minmax(82px, .8fr) minmax(145px, 1.5fr) 86px; }
  .event-table-row > :nth-child(3) { display: none; }
  .event-table.with-pick .event-table-row { grid-template-columns: 30px minmax(78px, .8fr) minmax(130px, 1.4fr) 82px; }
  .event-table.with-pick .event-table-row > :nth-child(3) { display: block; }
  .event-table.with-pick .event-table-row > :nth-child(4) { display: none; }
  .event-counts { grid-template-columns: 1fr; }
  .event-counts div { grid-template-columns: 1fr auto; align-items: center; border-top: 1px solid #d8e4e9; border-left: 0; }
  .event-counts div:first-child { border-top: 0; }
  .threshold-grid,
  .optimizer-grid,
  .result-metrics { grid-template-columns: 1fr; }
  .action-row { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .mode-tabs button,
  .process-tabs button,
  .param-groups button { transition: none; }
}
</style>
