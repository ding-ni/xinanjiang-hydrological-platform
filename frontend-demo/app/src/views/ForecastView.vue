<template>
  <section class="view-forecast">
    <div class="metric-strip forecast-metrics" aria-label="当前预报概览">
      <div class="metric"><span>预报对象</span><b>{{ forecast.targetSection?.name || '断面未登记' }}</b></div>
      <div class="metric"><span>起报时刻</span><b>{{ forecast.startTime }}</b></div>
      <div class="metric"><span>预见期</span><b>{{ forecast.leadHours }} h</b></div>
      <div class="metric"><span>预报降雨</span><b>{{ forecastRainLabel }}</b></div>
      <div class="metric emphasis">
        <span>{{ forecast.isPreviewMode ? '预报状态' : '预报洪峰' }}</span>
        <b :class="forecast.isPreviewMode ? 'warn' : 'hot'">
          {{ forecast.isPreviewMode ? forecast.modeLabel : `${forecast.currentScheme.peak.toLocaleString()} m³/s` }}
        </b>
      </div>
      <ContextStatusMetric />
    </div>

    <div v-if="!app.hasLakeBalance" class="data-status-bar" aria-label="资料接入状态">
      <div class="ds-item" :class="observedRainDataset ? 'ok' : 'warn'">
        <span>实测资料</span><b>{{ observedRainDataset ? '已接入' : '待接入' }}</b>
      </div>
      <div class="ds-item" :class="parameterReady ? 'ok' : 'warn'">
        <span>预报参数</span><b>{{ forecast.forecastMode === 'manual' ? '沿用起报状态' : productionParameterVersion?.id || '待发布' }}</b>
      </div>
      <div class="ds-item" :class="eligibleStateNodes.length ? 'ok' : 'warn'">
        <span>起算状态</span><b>{{ eligibleStateNodes.length ? `${eligibleStateNodes.length} 个可用` : '待生成' }}</b>
      </div>
      <div class="ds-item" :class="activeRainfallProducts.length ? 'ok' : 'warn'">
        <span>降雨情景</span><b>{{ activeRainfallProducts.length ? `${activeRainfallProducts.length} 个可用` : '待给定' }}</b>
      </div>
    </div>

    <div class="forecast-layout" :class="{ 'preparation-mode': !showModelOutputs }">
      <aside class="left-col">
        <section class="panel panel-pad settings-panel">
          <h3 class="panel-title"><span>预报条件</span><span class="latest-badge">当前作业</span></h3>
          <div class="forecast-mode-switch" role="tablist" aria-label="预报方式">
            <button type="button" :class="{ active: forecast.forecastMode === 'automatic' }" @click="onForecastMode('automatic')"><b>自动预报</b><small>调用已确认生产参数</small></button>
            <button type="button" :class="{ active: forecast.forecastMode === 'manual' }" @click="onForecastMode('manual')"><b>人工预报</b><small>沿用起报状态参数</small></button>
          </div>
          <div class="parameter-source-strip"><span>参数来源</span><b>{{ forecast.parameterSourceLabel }}</b></div>
          <div v-if="!app.hasLakeBalance" class="forecast-parameter-card">
            <template v-if="forecast.forecastMode === 'automatic'">
              <span>自动预报生产参数</span>
              <b>{{ productionParameterVersion?.id || '尚未发布' }}</b>
              <small>{{ productionParameterVersion ? '只读调用已审核生产版本' : '需先完成独立检验、人工审核和生产发布' }}</small>
            </template>
            <template v-else>
              <label for="manual-parameter-version">产流参数</label>
              <select id="manual-parameter-version" class="input" :value="selectedCandidateId" @change="onParameterCandidate($event.target.value)">
                <option v-if="app.backendCase" value="__STATE__">沿用起报状态参数</option>
                <option v-else-if="!candidateParameterVersions.length" value="">尚无率定候选版本</option>
                <option v-for="version in candidateParameterVersions" :key="version.id" :value="version.id">{{ version.id }} · {{ version.review?.status || '候选' }}</option>
              </select>
              <small>产流参数和汇流方案与起报状态一致。</small>
            </template>
          </div>
          <div class="form-stack">
            <div class="field">
              <label>预报断面</label>
              <select class="input" :value="forecast.targetSectionId" @change="onTarget($event.target.value)">
                <option v-for="section in forecast.sections" :key="section.id" :value="section.id">
                  {{ section.name }}（{{ section.river }}）
                </option>
              </select>
              <div class="field-value">
                <b>{{ forecast.targetSection.name }}</b>
                <span>{{ forecast.targetSection.river }} · {{ forecast.targetSection.controlArea || (app.hasLakeBalance ? '全湖' : '流域出口') }} {{ forecast.targetSection.controlArea ? 'km²' : '' }}</span>
              </div>
              <button v-if="app.hasLakeBalance && !isWholeLake" class="btn block lake-focus" type="button" @click="focusWholeLake">查看全湖来水</button>
            </div>

            <div class="field">
              <label>起报时刻</label>
              <input id="forecast-issue-time" class="input" type="datetime-local" step="3600" :value="startTimeInput" @change="onStart($event.target.value)" />
              <small class="field-note">实测资料截止时刻、状态预热终点与预报起点保持一致。</small>
            </div>

            <div class="continuity-card" aria-label="起报时刻衔接关系">
              <div><span>实测截止</span><b>{{ forecast.rainObsEnd }}</b></div>
              <i>＝</i>
              <div class="issue"><span>起报时刻</span><b>{{ forecast.startTime }}</b></div>
              <i>＝</i>
              <div><span>预报起点</span><b>{{ forecast.startTime }}</b></div>
            </div>

            <div class="field-group-title">起算状态</div>
            <div class="field">
              <label>续算时刻</label>
              <select id="forecast-state" class="input" :value="forecast.stateVersion || ''" :disabled="!eligibleStateNodes.length" @change="loadStateNode($event.target.value)">
                <option v-if="!eligibleStateNodes.length" value="">尚无正式状态版本</option>
                <option v-for="node in eligibleStateNodes" :key="node.id" :value="node.id">
                  {{ node.time }} · {{ node.status }}
                </option>
              </select>
              <div class="field-value">
                <b>{{ activeStateNode.time }} · {{ app.backendCase ? (stateCatchup.hours === 0 ? '已衔接起报时刻' : `待补算 ${stateCatchup.hours ?? '—'} 小时`) : '起算状态' }}</b>
                <span>{{ activeStateNode.status }}</span>
              </div>
              <button v-if="app.backendCase && stateCatchup.hours !== 0" class="btn block" type="button"
                :disabled="!stateCatchup.ready || state.taskRunning || forecast.running" :title="stateCatchup.reason" @click="catchUpState">
                {{ state.taskRunning ? '正在补算状态…' : '补算至起报时刻' }}
              </button>
              <p v-if="stateCatchupMessage" class="condition-note" role="status">{{ stateCatchupMessage }}</p>
            </div>

            <div class="field">
              <label>预见期</label>
              <select id="forecast-lead" class="input" :value="forecast.leadHours" @change="onLead($event.target.value)">
                <option :value="6">6 小时</option>
                <option :value="12">12 小时</option>
                <option :value="24">24 小时</option>
                <option :value="48">48 小时</option>
                <option :value="72">72 小时</option>
              </select>
              <small class="field-note">常规值班预报以 24 小时为主，按雨情和会商需要延长。</small>
            </div>

            <div class="field-group-title">降雨输入</div>
            <div class="field">
              <label>{{ app.backendCase ? '降雨情景' : '降雨预报产品' }}</label>
              <select id="forecast-rain-product" class="input" v-model="rainProductId" :disabled="!activeRainfallProducts.length || forecast.running" @change="onRainProduct">
                <option v-if="!activeRainfallProducts.length" value="">请给定逐小时雨量</option>
                <option v-for="product in activeRainfallProducts" :key="product.id" :value="product.id">
                  {{ product.name }} · {{ product.cycle }}
                </option>
              </select>
              <div class="field-value rain-value">
                <b>{{ currentRainProduct.name }}</b>
                <span v-if="activeRainfallProducts.length">预见期面雨量 {{ forecastArealRain }} mm</span>
                <span v-else>起报下一小时至预见期终点</span>
              </div>
              <details v-if="app.backendCase" class="rain-editor" :open="!activeRainfallProducts.length">
                <summary>给定逐小时雨量</summary>
                <label for="forecast-rain-name">情景名称</label>
                <input id="forecast-rain-name" class="input" v-model="rainName" maxlength="80" :disabled="forecast.running || savingRain" />
                <label for="forecast-hourly-rainfall">小时雨量 P（mm） · {{ forecast.leadHours }} 项</label>
                <textarea id="forecast-hourly-rainfall" class="input" v-model="hourlyRainText" rows="4" placeholder="每小时一项，以空格或逗号分隔" :disabled="forecast.running || savingRain" />
                <div class="rain-fill"><input class="input" type="number" min="0" max="1000" step="0.1" v-model="uniformRainMm" aria-label="等量小时雨量" :disabled="forecast.running || savingRain" /><button class="btn sm" type="button" :disabled="forecast.running || savingRain" @click="fillUniformRain">填入等量降雨</button></div>
                <small class="field-note">逐小时雨量均匀分配至有效网格。</small>
                <button class="btn block" type="button" :disabled="forecast.running || savingRain" @click="saveRainScenario">{{ savingRain ? '正在保存…' : '保存并采用情景' }}</button>
                <p v-if="rainMessage" class="condition-note" role="status">{{ rainMessage }}</p>
              </details>
            </div>

            <div v-if="app.hasLakeBalance" class="field">
              <label>降水预报时次</label>
              <input class="input" type="datetime-local" v-model="rainIssueInput" step="3600" @change="onRainIssue" />
              <small class="field-note">起报后按所选时次的逐小时降水场驱动入湖流域预报。</small>
            </div>

            <div class="forcing-chain" aria-label="降水资料衔接">
              <div><span>起报前</span><b>实测降水</b></div>
              <i>→</i>
              <div><span>起报后</span><b>预报降水</b></div>
            </div>

            <div class="input-summary">
              <div><span>模型配置</span><b>{{ activeModelVersion }}</b></div>
              <div><span>计算范围</span><b>{{ activeSpatial.grid }} · {{ activeSpatial.subbasins }} 个流域单元</b></div>
              <div><span>预报终止</span><b>{{ forecast.forecastEndTime }}</b></div>
            </div>
          </div>

          <div class="action-dock">
            <p v-if="forecastHandoff" class="condition-note">条件来自：{{ forecastHandoff.sourceLabel }} · {{ app.workContext.issueTime }}</p>
            <p v-if="forecast.dirtySinceCommit" class="condition-note">预报条件已调整，请重新预报以更新成果。</p>
            <p v-if="!pageCanRun && !forecast.running" class="condition-note">{{ forecastBlockReason }}</p>
            <button class="btn primary block run-button" type="button" :disabled="!pageCanRun" @click="confirmAndRun">
              {{ forecast.running ? '正在计算…' : forecast.dirtySinceCommit ? '重新预报' : '开始预报' }}
            </button>
            <p v-if="submissionStatus" class="submission-status">{{ submissionStatus }}</p>
            <button v-if="forecast.running" class="btn block" type="button" @click="forecast.cancelForecast()">停止任务</button>
            <TaskStatusPanel :task="activeTask" />
            <div class="secondary-actions">
              <button class="btn" type="button" :disabled="forecast.isPreviewMode" @click="archiveForecast">归档本次预报</button>
              <button class="btn" type="button" :disabled="forecast.isPreviewMode" @click="openReport">查看预报单</button>
              <button class="btn" type="button" :disabled="forecast.isPreviewMode" @click="copyAsNextOperation">复制为下一次作业</button>
            </div>
          </div>
        </section>
      </aside>

      <main v-if="showModelOutputs" class="center-col">
        <section class="panel panel-pad hydro-panel">
          <h3 class="panel-title">
            <span>流量过程 · {{ forecast.targetSection.name }}</span>
            <span class="hydro-title-tools">
              <span class="hydro-mode-tabs" aria-label="过程图显示方式">
                <button type="button" :class="{ active: chartMode === 'flow' }" @click="chartMode = 'flow'">流量过程</button>
                <button type="button" :class="{ active: chartMode === 'combined' }" @click="chartMode = 'combined'">雨量—流量</button>
              </span>
              <span class="sub-hint">{{ forecast.startTime }} 起报</span>
            </span>
          </h3>
          <div class="hydro-chart">
            <HydrographChart
              :hydrograph="forecast.displayHydrograph"
              :rainfall="forecastRainSeries"
              :show-prev="true"
              :show-thresholds="false"
              :display-mode="chartMode"
              height="100%"
            />
          </div>
        </section>

        <div class="result-row">
          <section class="panel panel-pad result-panel">
            <h3 class="panel-title">预报摘要</h3>
            <div class="detail-grid">
              <div class="forecast-peak">
                <b>{{ forecast.currentScheme.peak.toLocaleString() }}</b>
                {{ app.hasLakeBalance ? (isWholeLake ? '总入湖峰值 m³/s' : '入湖峰值 m³/s') : '洪峰流量 m³/s' }}
              </div>
              <div><b>{{ forecast.currentScheme.peakTime }}</b>峰现时间</div>
              <div><b>{{ forecast.currentScheme.peakLevel }} m</b>{{ app.hasLakeBalance ? '预报峰值湖位' : '预报峰水位' }}</div>
              <div v-if="app.hasLakeBalance"><b :class="levelClass">{{ forecast.currentScheme.level }}</b>湖位等级</div>
              <div><b>{{ forecast.peakCountdown }}</b>距峰现</div>
              <div><b>{{ (forecast.currentScheme.volume / 1e8).toFixed(2) }} 亿 m³</b>{{ app.hasLakeBalance ? '预报期入湖水量' : '预报洪量' }}</div>
            </div>
            <p v-if="app.hasLakeBalance" class="threshold-note">
              {{ app.hasLakeBalance ? '警戒湖位' : '警戒水位' }} {{ forecast.currentScheme.warnLevel }} m ·
              {{ app.hasLakeBalance ? '保证湖位' : '保证水位' }} {{ forecast.currentScheme.ensureLevel }} m
            </p>
          </section>

          <section class="panel panel-pad method-panel">
            <template v-if="app.hasLakeBalance">
              <h3 class="panel-title">入湖预报范围</h3>
              <div class="method-flow">
                <div><span>网格产流</span><b>{{ app.activeScaleMeta.label }} 分布式计算</b></div>
                <i>→</i>
                <div><span>河道汇流</span><b>入湖控制断面</b></div>
                <i>→</i>
                <div><span>成果汇总</span><b>全湖来水过程</b></div>
              </div>
              <p class="method-note">各入湖流域独立计算，汇总展示全湖来水过程及各断面预报成果。</p>
            </template>
            <template v-else>
              <h3 class="panel-title">产汇流计算</h3>
              <div class="method-flow">
                <div><span>网格产流</span><b>{{ activeRouting.runoff }}</b></div>
                <i>→</i>
                <div><span>坡面汇流</span><b>{{ activeRouting.hillslope }}</b></div>
                <i>→</i>
                <div><span>河道汇流</span><b>{{ activeRouting.channel }}</b></div>
              </div>
              <p class="method-note">{{ activeSpatial.note }}</p>
            </template>
          </section>
        </div>
      </main>

      <aside v-if="showModelOutputs" class="right-col">
        <section class="panel map-panel">
          <div class="map-head">
            <div><b>分布式产流</b><span>{{ mapTimeLabel }}</span></div>
            <div class="grid-switches">
              <label class="grid-toggle"><input type="checkbox" v-model="showModelGrid" /> {{ app.hasLakeBalance ? `${app.activeScaleMeta.label} 计算网格` : '1 km 计算网格' }}</label>
              <label v-if="!app.hasLakeBalance" class="grid-toggle"><input type="checkbox" v-model="showQpfGrid" /> 降雨预报网格</label>
            </div>
          </div>
          <div class="map-body">
            <BasinMap
              :stations="activeStations"
              :layers="mapLayers"
              :grid-cells="mapGridCells"
              :qpf-grid-cells="qpfGridCells"
              grid-paint-field="runoff"
              :interactive-grid="showGrid"
              legend-mode="runoff"
              :show-tools="false"
              @select-grid="onGridSelect"
              @select-station="onStation"
            />
          </div>
          <div class="map-time-tabs" aria-label="预报时次">
            <button v-for="hour in mapHours" :key="hour" type="button" :class="{ active: mapHour === hour }" @click="mapHour = hour">
              {{ hour === 0 ? '起报' : `+${hour} h` }}
            </button>
          </div>
          <p class="map-note">显示所选时次的网格产流深及河网汇流位置。</p>
        </section>

        <section class="panel panel-pad contribution-panel">
          <template v-if="app.hasLakeBalance">
            <h3 class="panel-title"><span>入湖来水汇总</span><b class="total-flow">{{ lakeTotal.toLocaleString() }} m³/s</b></h3>
            <table class="mini-table">
              <thead><tr><th>入湖流域</th><th>预报峰值</th><th>占比</th></tr></thead>
              <tbody>
                <tr v-for="row in lakeRows" :key="row.name">
                  <td>{{ row.name }}</td>
                  <td><b>{{ row.peak.toLocaleString() }} m³/s</b></td>
                  <td>{{ row.share }}</td>
                </tr>
              </tbody>
            </table>
          </template>
          <template v-else>
            <h3 class="panel-title">汇流成果</h3>
            <p class="map-note">后端返回栅格产流与汇流过程后，在此展示对徐畈出口流量的贡献。</p>
          </template>
        </section>
      </aside>

      <div class="preparation-column">
      <main v-if="app.backendCase" class="panel panel-pad forecast-results" aria-label="预报成果">
        <h3 class="panel-title"><span>雨量—流量过程 · {{ forecast.targetSection.name }}</span><span class="latest-badge">{{ forecast.resultStatus }}</span></h3>
        <div v-if="completedForecasts.length" class="forecast-history">
          <label for="forecast-history">已有预报</label>
          <select id="forecast-history" class="input" :value="selectedResultId" :disabled="forecast.running || loadingResult" @change="loadForecastResult($event.target.value)">
            <option value="">选择已完成预报</option>
            <option v-for="task in completedForecasts" :key="task.taskId" :value="task.taskId">{{ task.runConfig.issueTime || task.runConfig.startTime }} · {{ task.runConfig.leadHours }} h · {{ task.taskId.slice(-8) }}</option>
          </select>
        </div>
        <template v-if="forecast.committed?.resultSource === 'model-backend'">
          <p v-if="forecast.dirtySinceCommit" class="condition-note">当前保留上次成果，按新条件重新预报。</p>
          <div class="backend-forecast-chart"><HydrographChart :hydrograph="forecast.committed.hydrograph" :rainfall="forecast.committed.hydrograph.forcing.precipitation" :show-prev="false" :show-thresholds="false" display-mode="combined" height="100%" /></div>
          <div class="forecast-summary" aria-label="预报摘要">
            <div><span>洪峰流量</span><b>{{ forecast.committed.summary.peak.toFixed(2) }} <small>m³/s</small></b></div>
            <div><span>峰现时间</span><b>{{ dayjs(forecast.committed.summary.peakTime).format('MM-DD HH:mm') }}</b></div>
            <div><span>预报时段水量</span><b>{{ (forecast.committed.summary.volume / 1e4).toFixed(2) }} <small>万 m³</small></b></div>
            <div><span>累计面雨量</span><b>{{ forecast.committed.hydrograph.forcing.precipitation.reduce((sum, value) => sum + value, 0).toFixed(1) }} <small>mm</small></b></div>
          </div>
          <details class="forecast-ledger"><summary>逐小时成果</summary><table class="mini-table"><thead><tr><th>时刻</th><th>雨量 P（mm）</th><th>流量 Q（m³/s）</th></tr></thead><tbody><tr v-for="(row, index) in forecast.committed.hydrograph.forecast" :key="row.time"><td>{{ dayjs(row.time).format('MM-DD HH:mm') }}</td><td>{{ forecast.committed.hydrograph.forcing.precipitation[index].toFixed(2) }}</td><td>{{ row.value.toFixed(3) }}</td></tr></tbody></table></details>
        </template>
        <div v-else class="forecast-empty"><span>徐畈洪水预报</span><p>选定起报状态，给定逐小时降雨，计算出口流量过程。</p></div>
        <div class="forecast-method"><span>分布式新安江产流</span><i>→</i><span>坡面汇流</span><i>→</i><span>河道汇流</span><small>蒸散发按逐月给定值</small></div>
      </main>
      <main v-else-if="!showModelOutputs" class="request-workspace panel panel-pad">
        <div class="request-head">
          <div>
            <span class="request-kicker">预报任务</span>
            <h3>预报任务输入清单</h3>
            <p>本页整理当前案例的计算条件。模型计算服务尚未接入时，不显示虚构的洪峰、过程线、产流深或汇流贡献。</p>
          </div>
          <b>待确认</b>
        </div>
        <div class="request-grid">
          <section>
            <h4>水文资料</h4>
            <div><span>实测降雨</span><b>{{ observedRainDataset?.name || '当前案例实测降雨未接入' }}</b><small>{{ observedRainDataset ? `资料截止 ${forecast.rainObsEnd || '待确认'}` : '等待资料登记' }}</small></div>
            <div><span>预报降雨</span><b>{{ currentRainProduct.name }}</b><small>{{ currentRainProduct.cycle }}</small></div>
            <div><span>起算状态</span><b>{{ activeStateNode.time }}</b><small>{{ activeStateNode.status }}</small></div>
            <div><span>蒸散发</span><b>{{ app.basinId === 'nananxi-xufan' ? '0 mm/h' : '待登记' }}</b><small>{{ app.basinId === 'nananxi-xufan' ? '当前案例小时尺度场次洪水约定' : '按当前案例输入清单登记' }}</small></div>
          </section>
          <section>
            <h4>空间资料</h4>
            <div><span>计算尺度</span><b>{{ app.activeScaleMeta.label }}</b><small>{{ activeSpatial.grid }}</small></div>
            <div><span>空间对象</span><b>{{ caseData?.spatialAssets?.basin ? '边界 · DEM · 河网 · 站点 · 出口' : '当前案例空间资料未接入' }}</b><small>{{ caseData?.spatial?.note || `读取 ${app.basinName} 当前尺度资料清单` }}</small></div>
            <div><span>预报降雨网格</span><b>按产品网格接入</b><small>必要时重映射至 {{ app.activeScaleMeta.label }} 有效栅格</small></div>
          </section>
          <section>
            <h4>产汇流计算</h4>
            <div><span>预报断面</span><b>{{ forecast.targetSection.name }}</b><small>{{ forecast.startTime }} 起报</small></div>
            <div><span>模型方案</span><b>{{ activeModelVersion }}</b><small>分布式新安江模型</small></div>
            <div><span>计算内容</span><b>产流、坡面汇流、河道汇流</b><small>模型服务返回流量过程和状态场</small></div>
          </section>
        </div>
        <div class="request-flow"><span>检查资料</span><i>→</i><span>确认预报</span><i>→</i><span>提交模型服务</span><i>→</i><span>接收任务与成果</span></div>
      </main>

      <section class="panel panel-pad quality-panel">
        <h3 class="panel-title">
          <span>起报资料检查</span>
          <button class="btn sm" type="button" @click="syncMonitor">更新检查</button>
        </h3>
        <div class="quality-grid">
          <div v-for="(check, key) in activeChecks" :key="key">
            <span>{{ checkLabels[key] }}</span>
            <b :class="check.status">{{ check.text }}</b>
          </div>
        </div>
        <p v-if="!pageCanRun && !forecast.running" class="quality-note">{{ forecastBlockReason }}</p>
      </section>
      </div>
    </div>

    <div v-if="gridDialog" class="grid-mask" @click.self="gridDialog = null">
      <section class="grid-dialog panel panel-pad" role="dialog" aria-modal="true" aria-label="计算网格详情">
        <h3 class="panel-title">
          <span>计算网格 {{ gridDialog.id }}</span>
          <button class="icon-button" type="button" aria-label="关闭计算网格详情" title="关闭" @click="gridDialog = null"><Close /></button>
        </h3>
        <p class="map-note">预报时刻：{{ mapTimeLabel }}</p>
        <div class="input-summary">
          <div><span>产流深 R</span><b class="hot">{{ gridDialog.runoff }} mm</b></div>
          <div><span>蓄水量 W/WM</span><b>{{ gridDialog.soil }}</b></div>
          <div><span>自由水 S</span><b>{{ gridDialog.freeWater }} mm</b></div>
          <div><span>地表径流 RS</span><b>{{ gridDialog.surface }} m³/s</b></div>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { useForecastStore } from '@/stores/forecast'
import { catchupWindow, compatibleCatchupState } from '@/services/stateCatchupService'
import { stateScienceBlockReason } from '@/services/scientificSelectionService'
import { useTasksStore } from '@/stores/tasks'
import { useMonitorStore } from '@/stores/monitor'
import { useAppStore } from '@/stores/app'
import { useArchiveStore } from '@/stores/archive'
import { useStateStore } from '@/stores/state'
import BasinMap from '@/components/map/BasinMap.vue'
import HydrographChart from '@/components/charts/HydrographChart.vue'
import TaskStatusPanel from '@/components/tasks/TaskStatusPanel.vue'
import ContextStatusMetric from '@/components/common/ContextStatusMetric.vue'
import { loadSelincoGridCells, selincoStations } from '@/data/selinco'
import { scaleGridByParams } from '@/models/xajParamEngine'
import { buildForecastRequest, submitForecastRequest, parseHourlyRainfall, listForecastRain, registerForecastRain, forecastCommit } from '@/services/forecastSubmissionService'
import { parameterLibraryFor, productionParameterVersion as getProductionParameterVersion } from '@/services/parameterLibraryService'
import { projectDataForContext } from '@/services/caseAvailabilityService'

const forecast = useForecastStore()
const tasks = useTasksStore()
const monitor = useMonitorStore()
const app = useAppStore()
const archive = useArchiveStore()
const state = useStateStore()
const router = useRouter()
const route = useRoute()
const restoredTaskId = ref('')

const showModelGrid = ref(true)
const showQpfGrid = ref(true)
const gridDialog = ref(null)
const selincoGridCells = ref([])
const rainProductId = ref('')
const rainIssueInput = ref('2026-06-02T12:00')
const mapHour = ref(0)
const chartMode = ref('flow')
const submissionStatus = ref('')
const selectedCandidateId = ref('')
const backendRainProducts = ref([])
const rainName = ref('人工降雨情景')
const hourlyRainText = ref('')
const uniformRainMm = ref(0)
const savingRain = ref(false)
const rainMessage = ref('')
const loadingResult = ref(false)
const selectedResultId = ref('')
let rainRequest = 0
let gridRequest = 0
const activeTask = computed(() => forecast.activeTaskId ? tasks.taskById(forecast.activeTaskId) : null)
const caseData = computed(() => projectDataForContext(app.activeContext, app.projectData, { loading: app.projectDataLoading, error: app.projectDataError }))
const observedRainDataset = computed(() => (caseData.value?.datasets?.datasets || []).find((item) => item.role === 'observation' && /rain|precipitation/i.test(item.type || item.id)))
const parameterLibrary = computed(() => parameterLibraryFor(caseData.value, app.basinId, app.modelScale, app.activeContext))
const productionParameter = computed(() => getProductionParameterVersion(parameterLibrary.value))
const candidateParameterVersions = computed(() => parameterLibrary.value?.simulationRateLibrary?.versions || [])
const productionParameterVersion = computed(() => productionParameter.value)
const parameterReady = computed(() => {
  if (app.hasLakeBalance) return forecast.executionAvailability.ready
  if (app.backendCase) {
    const actual = activeStateNode.value.parameters
    if (!actual || !Object.keys(actual).length) return false
    if (forecast.forecastMode === 'automatic' && !productionParameter.value) return false
    return Object.keys(actual).length === Object.keys(forecast.effectiveParams).length
      && Object.entries(actual).every(([key, value]) => Number(forecast.effectiveParams[key]) === Number(value))
  }
  return forecast.forecastMode === 'automatic'
    ? Boolean(productionParameter.value)
    : candidateParameterVersions.value.some((version) => version.id === selectedCandidateId.value)
})
const pageCanRun = computed(() => app.backendCase
  ? forecast.executionAvailability.ready && !forecast.running && !loadingResult.value && !savingRain.value
    && forecast.targetSectionId === '70115800' && parameterReady.value && stateCatchup.value.hours === 0
    && activeStateNode.value.id === forecast.stateVersion && Boolean(currentRainProduct.value.versionId)
  : forecast.canRun && parameterReady.value && activeRainfallProducts.value.length > 0 && eligibleStateNodes.value.length > 0)
const forecastBlockReason = computed(() => {
  if (!forecast.executionAvailability.ready) return forecast.executionAvailability.reason
  if (app.backendCase && state.domainInitialStates.length && !eligibleStateNodes.value.length
    && state.domainInitialStates.some(node => stateScienceBlockReason(node, app.backendCase?.modelImplementationSHA256))) return '历史状态需重新初始化后起报，请在状态页生成新的初始状态。'
  if (!activeRainfallProducts.value.length) return '请给定本次逐小时降雨情景'
  if (!caseData.value?.datasets?.datasets?.length) return '当前流域没有可用于预报的小时资料。'
  if (!parameterReady.value) return forecast.forecastMode === 'automatic' ? '请选择已发布生产参数' : '请沿用起报状态参数；更换参数后先重新初始化状态'
  if (!app.hasLakeBalance && !eligibleStateNodes.value.length) return '没有可用于起报时刻的模型状态版本。'
  if (app.backendCase) return stateCatchup.value.hours === 0 ? '请选择预报断面' : stateCatchup.value.ready ? '请补算状态至起报时刻' : stateCatchup.value.reason
  if (forecast.hasBlockingIssue) return '起报资料检查存在阻断项。'
  return '请补充预报对象、起报时刻和预见期。'
})
const forecastHandoff = computed(() => app.workContext.handoff?.targetPage === 'forecast' ? app.workContext.handoff : null)

const activeStations = computed(() => {
  if (!caseData.value) return []
  if (app.hasLakeBalance) return selincoStations
  const rows = [
    ...(caseData.value?.stations?.stations || []),
    ...(caseData.value?.sections?.sections || [])
  ]
  return [...new Map(rows.map((row) => [row.id, row])).values()]
})
const startTimeInput = computed(() => forecast.startTime.replace(' ', 'T'))
const activeRainfallProducts = computed(() => !caseData.value ? [] : app.backendCase
  ? backendRainProducts.value.filter(row => row.contextKey === forecast.contextKey
      && dayjs(row.issueTime).isSame(dayjs(forecast.startTime)) && row.leadHours >= forecast.leadHours)
    .map(row => ({ ...row, id: row.versionId, cycle: dayjs(row.issueTime).format('MM-DD HH:mm') }))
  : app.hasLakeBalance
  ? [{
      id: 'selinco',
      name: `色林错 ${app.activeScaleMeta.label} 逐小时降水场`,
      cycle: '2026-06-02 14:00',
      nc: app.activeModelScheme.forcing?.name
    }]
  : (caseData.value?.datasets?.datasets || [])
      .filter((item) => item.role === 'forecast')
      .map((item) => ({ id: item.id, name: item.name, cycle: '已登记', nc: item.file || item.id })))
const showModelOutputs = computed(() => forecast.executionAvailability.ready && app.hasLakeBalance)
const unavailableHydroLabel = computed(() => app.basinId === 'llpq'
  ? '缺少小时降水、流量、气温、蒸发和冰雪冻土驱动'
  : app.basinId === 'nananxi-xufan'
    ? '逐小时雨量 2,944,497 条；徐畈水位流量 515,422 条'
    : '小时雨量、水位和流量未接入')
const currentRainProduct = computed(() => activeRainfallProducts.value.find((item) => item.id === rainProductId.value) || activeRainfallProducts.value[0] || {
  id: '',
  name: '请选择降雨情景',
  cycle: '',
  nc: null
})
const forecastRainLabel = computed(() => app.hasLakeBalance
  ? `${app.activeScaleMeta.label} 逐小时降水`
  : currentRainProduct.value?.name || '预报降雨')
const isWholeLake = computed(() => app.hasLakeBalance && forecast.targetSectionId === 'SEL-LAKE-ALL')
const activeModelVersion = computed(() => app.activeModelScheme.paramVersion)
const activeSpatial = computed(() => app.hasLakeBalance
  ? {
      grid: `${app.activeScaleMeta.label} 网格`,
      subbasins: 4,
      note: '四个入湖流域分别计算产流和河道汇流，形成各入湖口及全湖来水过程。'
    }
  : {
      grid: `${app.activeScaleMeta.label} 有效栅格`,
      subbasins: caseData.value?.spatialMetadata?.featureCounts?.basin ?? 0,
      note: caseData.value?.spatial?.note || '当前案例空间资料未接入。'
    })
const activeRouting = computed(() => ({
  runoff: '分布式新安江蓄满产流',
  hillslope: '网格线性水库坡面汇流',
  channel: app.hasLakeBalance ? 'Strahler 分级河网 + 入湖口汇集' : '按当前河网分段汇流'
}))
const eligibleStateNodes = computed(() => {
  const issue = dayjs(forecast.startTime)
  return state.domainInitialStates.filter((node) => !dayjs(node.time).isAfter(issue)
    && (!app.backendCase || compatibleCatchupState(node, app.selectedInputBundleVersionId, app.selectedRoutingVersionId, app.backendCase.modelImplementationSHA256)))
})
const activeStateNode = computed(() => eligibleStateNodes.value.find((node) => node.id === forecast.stateVersion) || eligibleStateNodes.value[0] || {
  time: '—',
  status: '无可用状态'
})
const stateCatchup = computed(() => catchupWindow(activeStateNode.value.time, forecast.startTime))
const activeChecks = computed(() => app.backendCase ? {
  rain: { status: activeStateNode.value.id ? 'ok' : 'warn', text: activeStateNode.value.id ? '已用于状态推算' : '请选择起报状态' },
  hydro: forecast.dataCheck.hydro,
  areal: { status: currentRainProduct.value.versionId ? 'ok' : 'warn', text: currentRainProduct.value.versionId ? `情景雨量 ${forecastArealRain.value} mm` : '待给定降雨' },
  state: { status: stateCatchup.value.hours === 0 ? 'ok' : 'warn', text: stateCatchup.value.hours === 0 ? '已衔接起报时刻' : activeStateNode.value.id ? '待补算至起报时刻' : '请选择起报状态' }
} : forecast.dataCheck)
const completedForecasts = computed(() => tasks.tasks.filter(task => task.contextKey === forecast.contextKey
  && task.resultSource === 'model-backend' && task.runType === 'forecast' && ['completed', 'archived'].includes(task.status)))
const operationKey = computed(() => JSON.stringify([forecast.contextKey, app.inputSelectionKey,
  app.selectedRoutingVersionId, forecast.startTime, forecast.leadHours, forecast.targetSectionId,
  forecast.stateVersion, forecast.forecastMode, forecast.effectiveParams, currentRainProduct.value.id]))
const stateCatchupMessage = ref('')
async function catchUpState() {
  if (!stateCatchup.value.ready || state.taskRunning || forecast.running) return
  const context = app.contextKey
  const issue = forecast.startTime
  const inputKey = app.inputSelectionKey
  const routing = app.selectedRoutingVersionId
  state.loadInitialState(activeStateNode.value.id)
  stateCatchupMessage.value = ''
  const result = await state.runStateTask('continuation', stateCatchup.value.endTime)
  if (app.contextKey !== context || app.inputSelectionKey !== inputKey || app.selectedRoutingVersionId !== routing || forecast.startTime !== issue) return
  if (!result.ok) { stateCatchupMessage.value = result.error; return }
  forecast.setStateVersion(result.state.id, 'state-catchup')
  app.setStateVersion(result.state.id)
  stateCatchupMessage.value = `已补算至 ${result.state.time}`
  ElMessage.success('起报状态已衔接')
}

watch(() => app.workContext, (workContext) => {
  if (workContext.handoff?.targetPage !== 'forecast') return
  if (workContext.issueTime) {
    forecast.startTime = workContext.issueTime
    forecast.rainObsEnd = workContext.issueTime
  }
  if (workContext.stateVersionId) forecast.setStateVersion(workContext.stateVersionId, workContext.handoff.sourcePage)
  if (workContext.parameterVersionId) forecast.loadParamVersion(workContext.parameterVersionId)
  if (workContext.inputBundleVersionId) {
    forecast.dataVersion = workContext.inputBundleVersionId
    forecast.observationDataId = workContext.inputBundleVersionId
  }
  if (workContext.sectionId && forecast.sections.some((item) => item.id === workContext.sectionId)) forecast.setTarget(workContext.sectionId)
}, { immediate: true, deep: true })
const mapHours = computed(() => [0, 6, 12, 24, 48, 72].filter((hour) => hour <= forecast.leadHours))
const mapTimeLabel = computed(() => `${dayjs(forecast.startTime).add(mapHour.value, 'hour').format('MM-DD HH:mm')} · +${mapHour.value} h`)
const forecastArealRain = computed(() => {
  if (app.backendCase) return (currentRainProduct.value.hourlyRainfallMm || []).slice(0, forecast.leadHours).reduce((sum, value) => sum + value, 0).toFixed(1)
  const issueHour = dayjs(rainIssueInput.value).hour()
  const scaleFactor = app.modelScale === '0p1deg' ? 0.94 : 1
  const cycleFactor = 0.92 + (issueHour % 12) / 100
  return (Math.sqrt(forecast.leadHours) * 2.35 * scaleFactor * cycleFactor).toFixed(1)
})
const forecastRainSeries = computed(() => {
  if (app.backendCase) return forecast.committed?.hydrograph?.forcing?.precipitation || []
  const hydrograph = forecast.displayHydrograph
  const times = hydrograph?.forecast?.map((item) => item.time) || []
  const startIndex = Math.max(0, times.indexOf(hydrograph?.startMark))
  const issueFactor = 0.9 + (dayjs(rainIssueInput.value).hour() % 12) / 45
  const scaleFactor = app.modelScale === '0p1deg' ? 0.92 : 1
  return times.map((_, index) => {
    const observed = 2.1 * Math.exp(-Math.pow((index - Math.max(2, startIndex - 15)) / 7, 2))
    const firstPeak = 5.4 * Math.exp(-Math.pow((index - (startIndex + 7)) / 6, 2))
    const secondPeak = 3.1 * Math.exp(-Math.pow((index - (startIndex + 25)) / 9, 2))
    const value = (observed + firstPeak + secondPeak) * issueFactor * scaleFactor
    return +(value < 0.08 ? 0 : value).toFixed(1)
  })
})
const mapGridCells = computed(() => {
  if (!forecast.executionAvailability.ready || !app.hasLakeBalance) return []
  const factor = 1 + Math.sin((mapHour.value / Math.max(1, forecast.leadHours)) * Math.PI) * 0.55
  const cells = app.hasLakeBalance
    ? scaleGridByParams(selincoGridCells.value, forecast.effectiveParams, forecast.wetness)
    : forecast.gridCells
  return cells.map((cell) => ({ ...cell, runoff: +(cell.runoff * factor).toFixed(2), surface: Math.round(cell.surface * factor) }))
})
const qpfGridCells = computed(() => [])
const showGrid = computed(() => showModelGrid.value || (!app.hasLakeBalance && showQpfGrid.value))
const mapLayers = computed(() => ({
  rain: false,
  hydro: true,
  water: false,
  section: true,
  river: true,
  subbasin: true,
  rainField: false,
  dem: true,
  grid: showModelGrid.value,
  realGrid: app.hasLakeBalance && showModelGrid.value,
  qpfGrid: !app.hasLakeBalance && showQpfGrid.value
}))
const levelClass = computed(() => {
  const level = forecast.currentScheme.level
  if (level === '警戒' || level === '保证' || level === '高水位') return 'bad'
  if (level === '关注') return 'warn'
  return 'ok'
})
const lakeRows = computed(() => {
  const rows = forecast.outputSections.filter((section) => !section.wholeLake)
  const total = rows.reduce((sum, section) => sum + Number(section.result?.summary?.peak || 0), 0)
  return rows.map((section) => {
    const peak = Number(section.result?.summary?.peak || 0)
    return {
      name: section.river || section.name.replace('入湖口', ''),
      peak,
      share: total ? `${Math.round((peak / total) * 100)}%` : '—'
    }
  })
})
const lakeTotal = computed(() => Number(forecast.outputSections.find((section) => section.wholeLake)?.result?.summary?.peak || 0))
const zoneContrib = computed(() => [])
const checkLabels = {
  rain: '雨量站',
  hydro: '水文站',
  areal: '降水场',
  state: '起算状态',
  level: '水位',
  late: '迟报'
}

watch(() => [app.basinId, app.modelScale, app.modelConfigId], ([domainId, modelScale]) => {
  mapHour.value = 0
  gridDialog.value = null
  selectedCandidateId.value = domainId === 'nananxi-xufan' ? '__STATE__' : ''
  submissionStatus.value = ''
  selectedResultId.value = ''
  rainMessage.value = ''
  rainProductId.value = domainId === 'selinco' ? 'selinco' : ''
  forecast.setContext(domainId, modelScale, app.modelConfigId)
  forecast.setProjectSections(caseData.value?.sections?.sections || [])
  state.setContext(domainId, modelScale, app.modelConfigId)
  refreshSelincoGrid(domainId, modelScale)
  syncMonitor(false)
}, { immediate: true })

watch([() => forecast.forecastMode, candidateParameterVersions, productionParameter], () => {
  if (app.basinId !== 'nananxi-xufan') return
  if (forecast.forecastMode === 'automatic') {
    if (productionParameter.value) {
      forecast.loadParameterVersion(productionParameter.value)
    } else {
      forecast.parameterVersionStatus = '生产参数待发布'
    }
    return
  }
  if (app.backendCase && (!selectedCandidateId.value || selectedCandidateId.value === '__STATE__')) {
    selectedCandidateId.value = '__STATE__'
    adoptStateParameters()
    return
  }
  const selected = candidateParameterVersions.value.find((version) => version.id === selectedCandidateId.value)
    || candidateParameterVersions.value[0]
  selectedCandidateId.value = selected?.id || ''
  if (selected) forecast.loadParameterVersion(selected)
  else forecast.parameterVersionStatus = '可选择率定候选参数'
}, { immediate: true, deep: true })

watch([eligibleStateNodes, () => app.selectedInputBundleVersionId, () => app.selectedRoutingVersionId], () => {
  if (!app.backendCase) return
  const selected = eligibleStateNodes.value.find(node => node.id === forecast.stateVersion) || eligibleStateNodes.value[0]
  if (forecast.stateVersion !== (selected?.id || null)) forecast.setStateVersion(selected?.id || null)
  if (selectedCandidateId.value === '__STATE__') adoptStateParameters()
}, { immediate: true })

watch([() => app.contextKey, () => forecast.executionAvailability.ready], async () => {
  const serial = ++rainRequest
  if (!app.backendCase || !forecast.executionAvailability.ready) { backendRainProducts.value = []; return }
  try {
    const rows = await listForecastRain()
    if (serial === rainRequest) backendRainProducts.value = rows
  } catch (error) {
    if (serial === rainRequest) rainMessage.value = error.message
  }
}, { immediate: true })

watch(activeRainfallProducts, rows => {
  if (!app.backendCase) return
  const id = rows.some(row => row.id === rainProductId.value) ? rainProductId.value : rows[0]?.id || ''
  if (rainProductId.value !== id) { rainProductId.value = id; onRainProduct() }
})

watch(() => operationKey.value, (next, previous) => {
  if (previous && next !== previous) forecast.markDirty()
})

watch([() => route.query.task, completedForecasts, () => app.backendCase], ([id, rows]) => {
  if (!id || id === restoredTaskId.value || !app.backendCase || !rows.some(row => row.taskId === id)) return
  restoredTaskId.value = id
  loadForecastResult(id)
}, { immediate: true })

watch(() => caseData.value?.sections?.sections, (rows) => {
  forecast.setProjectSections(rows || [])
}, { immediate: true })

watch(() => forecast.leadHours, (hours) => {
  if (mapHour.value > hours) mapHour.value = 0
})

async function refreshSelincoGrid(domainId, modelScale) {
  const request = ++gridRequest
  if (domainId !== 'selinco') {
    selincoGridCells.value = []
    return
  }
  const cells = await loadSelincoGridCells(modelScale)
  if (request === gridRequest) selincoGridCells.value = cells
}

function syncMonitor(toast = true) {
  const payload = typeof monitor.qualityPayloadFor === 'function'
    ? monitor.qualityPayloadFor(app.basinId)
    : monitor.qualityPayload
  forecast.syncFromMonitor(payload)
  if (toast) ElMessage.success('起报资料检查已更新')
}

function onTarget(id) {
  forecast.setTarget(id)
  mapHour.value = 0
}

function onForecastMode(mode) {
  forecast.setForecastMode(mode)
  selectedCandidateId.value = app.backendCase && mode === 'manual' ? '__STATE__' : ''
  if (selectedCandidateId.value === '__STATE__') adoptStateParameters()
  submissionStatus.value = ''
  ElMessage.success(mode === 'automatic' ? '已切换到自动预报参数库' : '已切换到人工预报')
}

function onParameterCandidate(id) {
  if (id === '__STATE__') { selectedCandidateId.value = id; adoptStateParameters(); return }
  const version = candidateParameterVersions.value.find((item) => item.id === id)
  if (!version) return
  selectedCandidateId.value = version.id
  forecast.loadParameterVersion(version)
  ElMessage.success(`已载入率定候选参数 ${version.id}`)
}

function adoptStateParameters() {
  if (!app.backendCase || forecast.forecastMode !== 'manual' || selectedCandidateId.value !== '__STATE__') return
  forecast.baseParams = { ...(activeStateNode.value.parameters || {}) }
  forecast.paramTweaks = {}
  forecast.paramVersion = null
  forecast.parameterVersionStatus = '沿用起报状态参数'
}

function fillUniformRain() {
  const value = Number(uniformRainMm.value)
  if (!Number.isFinite(value) || value < 0 || value > 1000) { rainMessage.value = '逐小时雨量须在 0–1000 mm 之间'; return }
  hourlyRainText.value = Array(Number(forecast.leadHours)).fill(value).join(' ')
  rainMessage.value = ''
}

async function saveRainScenario() {
  if (savingRain.value || forecast.running) return
  const context = forecast.contextKey, issue = forecast.startTime, lead = forecast.leadHours
  savingRain.value = true
  rainMessage.value = ''
  try {
    const hourlyRainfallMm = parseHourlyRainfall(hourlyRainText.value, lead)
    const row = await registerForecastRain({ name: rainName.value, contextKey: context,
      issueTime: dayjs(issue).format('YYYY-MM-DDTHH:mm:ss'), hourlyRainfallMm })
    if (forecast.contextKey !== context || forecast.startTime !== issue || forecast.leadHours !== lead) return
    backendRainProducts.value = [row, ...backendRainProducts.value.filter(item => item.versionId !== row.versionId)]
    rainProductId.value = row.versionId
    onRainProduct()
    rainMessage.value = `已采用 · 累计 ${row.totalMm.toFixed(1)} mm`
  } catch (error) { if (forecast.contextKey === context) rainMessage.value = error.message }
  finally { savingRain.value = false }
}

function onStart(value) {
  const normalized = value.replace('T', ' ')
  forecast.startTime = normalized
  forecast.rainObsEnd = normalized
  const selected = state.domainInitialStates.find((node) => node.id === forecast.stateVersion)
  const latest = eligibleStateNodes.value[0]
  if (selected && dayjs(selected.time).isAfter(dayjs(normalized)) && latest) {
    forecast.setStateVersion(latest.id, 'continuation')
    forecast.setWetness(latest.means.soil, 'continuation')
  }
  forecast.markDirty()
  app.setDataTime(normalized)
  mapHour.value = 0
}

function onLead(value) {
  forecast.leadHours = Number(value)
  forecast.markDirty()
}

function loadStateNode(id) {
  const node = state.loadInitialState(id)
  if (!node) return
  forecast.setStateVersion(node.id, 'continuation')
  forecast.setWetness(node.means.soil, 'continuation')
  adoptStateParameters()
  ElMessage.success(`已采用 ${node.time} 的起算状态`)
}

function onRainProduct() {
  if (app.backendCase) {
    forecast.forecastPrecipitationId = currentRainProduct.value.versionId || null
    forecast.dataVersion = currentRainProduct.value.versionId || null
    forecast.rainQpfSource = currentRainProduct.value.sourceLabel || ''
    forecast.markDirty()
    return
  }
  if (!currentRainProduct.value.nc) return
  forecast.rainQpfSource = currentRainProduct.value.nc
  forecast.markDirty()
}

function onRainIssue() {
  forecast.dataVersion = `SEL-QPF-${dayjs(rainIssueInput.value).format('YYYYMMDDHH')}-${app.modelScale.toUpperCase()}`
  forecast.forecastPrecipitationId = forecast.dataVersion
  forecast.markDirty()
}

function focusWholeLake() {
  forecast.setTarget('SEL-LAKE-ALL')
  mapHour.value = 0
}

async function run() {
  const id = await forecast.runForecast()
  if (!id) {
    ElMessage.warning(forecast.hasBlockingIssue ? '请先处理起报资料检查中的异常' : '请补充预报对象、起报时刻和预见期')
    return
  }
  const record = forecast.createArchiveRecord(id)
  app.setRunId(id)
  app.updateLatestProduct({
    runId: id,
    section: record.section,
    peak: record.peak,
    peakTime: record.peakTime,
    leadHours: record.leadHours,
    status: '待复核',
    contextKey: forecast.contextKey
  })
  ElMessage.success('预报计算完成')
}

async function confirmAndRun() {
  if (!pageCanRun.value) {
    ElMessage.warning(forecastBlockReason.value)
    return
  }
  if (app.hasLakeBalance) {
    await run()
    return
  }
  const request = buildForecastRequest({ app, forecast, rainProduct: currentRainProduct.value, parentState: app.backendCase ? activeStateNode.value : null })
  const key = operationKey.value
  const context = forecast.contextKey
  let taskId = null
  try {
    await ElMessageBox.confirm(
      `将提交 ${forecast.targetSection.name} ${forecast.startTime} 起报、${forecast.leadHours} 小时预见期的预报任务。产流与汇流由后端模型服务计算。`,
      '确认预报条件',
      { confirmButtonText: '确认提交', cancelButtonText: '返回检查', type: 'info' }
    )
    if (operationKey.value !== key || !pageCanRun.value) return
    forecast.running = true
    const result = await submitForecastRequest(request)
    if (result.task?.taskId || result.task?.id) {
      const tracked = tasks.trackRemoteTask({ runType: 'forecast', request, task: result.task })
      taskId = tracked?.taskId
      if (forecast.contextKey === context) forecast.activeTaskId = taskId
    }
    if (result.status === 'prepared') {
      submissionStatus.value = `请求 ${result.requestId} 已完成组装；后端接口接入后可直接提交。`
      ElMessage.warning('预报请求已准备，模型后端尚未接入，未生成任何模拟结果')
    } else {
      if (forecast.contextKey === context) submissionStatus.value = '正在计算产流与汇流…'
      const task = await tasks.waitForTask(taskId)
      if (!['completed', 'archived'].includes(task?.status)) throw new Error(task?.errorMessage || (task?.status === 'cancelled' ? '预报已停止' : '预报计算未完成'))
      const output = await tasks.getResult(taskId)
      if (operationKey.value !== key) {
        if (forecast.contextKey === context) submissionStatus.value = '计算完成，可从已有预报载入成果'
        return
      }
      const frozenRequest = task.request || task.runConfig
      applyForecastResult(output, frozenRequest, taskId)
      submissionStatus.value = '预报计算完成'
      ElMessage.success('预报计算完成')
    }
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    if (forecast.contextKey === context) {
      submissionStatus.value = error?.message || String(error)
      ElMessage.error(submissionStatus.value)
    }
  } finally {
    if (forecast.contextKey === context) forecast.running = false
    else if (forecast.workspaces[context]) forecast.workspaces[context].running = false
  }
}

function applyForecastResult(result, request, taskId) {
  const committed = forecastCommit(result, request, taskId)
  forecast.committed = committed
  forecast.resultBySection = Object.fromEntries(committed.outputs.map(row => [row.sectionId, row]))
  forecast.paramVersion = request.inputs.parameterVersionId
  forecast.dirtySinceCommit = false
  forecast.resultStatus = '计算完成'
  forecast.activeTaskId = taskId
  selectedResultId.value = taskId
  app.setRunId(taskId)
}

async function loadForecastResult(taskId) {
  const task = completedForecasts.value.find(row => row.taskId === taskId)
  const request = task?.request || task?.runConfig
  if (!request?.issue || loadingResult.value || forecast.running) return
  const context = forecast.contextKey
  const key = operationKey.value
  loadingResult.value = true
  submissionStatus.value = ''
  try {
    const [result, products] = await Promise.all([tasks.getResult(taskId), listForecastRain()])
    if (operationKey.value !== key) return
    forecastCommit(result, request, taskId)
    if (request.inputs.bundleVersionId !== app.selectedInputBundleVersionId || request.routing.versionId !== app.selectedRoutingVersionId) {
      throw new Error('请先在资料和汇流设置中选回本次预报使用的版本')
    }
    forecast.startTime = dayjs(request.issue.issueTime).format('YYYY-MM-DD HH:mm')
    forecast.rainObsEnd = forecast.startTime
    forecast.leadHours = request.issue.leadHours
    forecast.forecastMode = request.issue.mode
    forecast.targetSectionId = request.issue.sectionId
    forecast.setStateVersion(request.period.initialStateVersionId)
    selectedCandidateId.value = '__STATE__'
    backendRainProducts.value = products
    rainProductId.value = request.inputs.forecastPrecipitation.versionId
    onRainProduct()
    await nextTick()
    if (forecast.contextKey !== context) return
    forecast.baseParams = { ...request.parameters }
    forecast.paramTweaks = {}
    await nextTick()
    if (forecast.contextKey !== context) return
    applyForecastResult(result, request, taskId)
  } catch (error) { if (forecast.contextKey === context) submissionStatus.value = error.message }
  finally { loadingResult.value = false }
}

async function archiveForecast() {
  if (forecast.isPreviewMode) {
    ElMessage.warning('请先完成预报计算')
    return
  }
  const record = forecast.createArchiveRecord()
  record.status = '已归档'
  archive.addRun(record)
  if (forecast.activeTaskId) await tasks.archiveTask(forecast.activeTaskId)
  forecast.resultStatus = '已归档'
  ElMessage.success('本次预报已归档')
}

function openReport() {
  if (forecast.isPreviewMode) {
    ElMessage.warning('请先完成预报计算')
    return
  }
  app.openReport(forecast.createArchiveRecord())
}

function copyAsNextOperation() {
  if (forecast.isPreviewMode) return
  const next = forecast.prepareNextRun(6)
  app.prepareHandoff('forecast', {
    sourcePage: 'forecast',
    sourceLabel: '上一时次洪水预报',
    issueTime: next.nextStart,
    sectionId: forecast.targetSectionId,
    stateVersionId: forecast.stateVersion,
    parameterVersionId: forecast.paramVersion,
    inputBundleVersionId: forecast.observationDataId,
    sourceTaskId: next.sourceTaskId
  })
  ElMessage.success(`已复制为 ${next.nextStart} 的下一次作业，请复核输入后执行`)
}

function onStation(id) {
  const stations = activeStations.value
  const station = stations.find((item) => item.id === id)
  if (station && (station.type === 'section' || station.sectionId)) {
    forecast.prepareFromStation(station, app.dataTime)
    syncMonitor(false)
  }
}

function onGridSelect(id, props) {
  const cell = mapGridCells.value.find((item) => item.id === id) || props
  if (!cell) return
  gridDialog.value = {
    id: cell.id || id,
    runoff: cell.runoff,
    soil: cell.soil,
    freeWater: cell.freeWater,
    surface: cell.surface
  }
  forecast.selectGrid(id)
}
</script>

<style scoped>
.view-forecast {
  display: grid;
  gap: 12px;
  min-width: 0;
}

.data-status-bar { display: flex; flex-wrap: wrap; gap: 8px 24px; padding: 10px 14px; border: 1px solid #d6e3df; border-radius: 5px; background: #f8fbfa; }
.ds-item { display: flex; gap: 8px; align-items: baseline; font-size: 11px; }
.ds-item span { color: #70877f; }
.ds-item b { font-weight: 500; }
.ds-item.ok b { color: #286e60; }
.ds-item.warn b { color: #a57031; }

.forecast-metrics {
  grid-auto-flow: unset;
  grid-template-columns:
    minmax(150px, 1fr)
    minmax(232px, 1.4fr)
    minmax(90px, 0.65fr)
    minmax(150px, 1fr)
    minmax(140px, 0.9fr)
    minmax(146px, 0.9fr);
  min-width: 0;
}

.metric {
  min-width: 0;
}

.metric span,
.metric b {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.forecast-metrics .metric span {
  flex: 0 0 auto;
}

.forecast-metrics .metric b {
  min-width: 0;
}

.forecast-layout {
  display: grid;
  grid-template-columns: minmax(264px, 292px) minmax(360px, 1.35fr) minmax(300px, 0.9fr);
  gap: 12px;
  align-items: start;
  min-width: 0;
}
.preparation-column { display: contents; }
.forecast-layout.preparation-mode .preparation-column {
  display: grid;
  grid-column: 2 / 4;
  grid-row: 1;
  align-content: start;
  gap: 12px;
  min-width: 0;
}
.forecast-layout.preparation-mode .request-workspace,
.forecast-layout.preparation-mode .quality-panel {
  grid-column: auto;
  grid-row: auto;
}
.forecast-layout.preparation-mode .request-workspace { min-height: 0; }
.forecast-results { display: grid; gap: 14px; min-width: 0; }
.forecast-history { display: flex; align-items: center; gap: 12px; min-width: 0; }
.forecast-history label { flex: 0 0 auto; color: #667d88; font-size: 12px; }
.forecast-history .input { min-width: 0; flex: 1; }
.backend-forecast-chart { height: 390px; min-width: 0; }
.forecast-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; padding: 16px 0; border-block: 1px solid #d7e4e9; }
.forecast-summary > div { display: grid; gap: 6px; }
.forecast-summary span { color: #667d88; font-size: 11px; }
.forecast-summary b { color: #1b5e64; font-size: 19px; font-variant-numeric: tabular-nums; }
.forecast-summary small { font-size: 11px; font-weight: 400; }
.forecast-empty { display: grid; place-content: center; min-height: 260px; text-align: center; background: #f4f8f8; }
.forecast-empty span { color: #1b5e64; font-size: 22px; }
.forecast-empty p { color: #667d88; font-size: 12px; }
.forecast-method { display: flex; flex-wrap: wrap; gap: 10px; color: #46636f; font-size: 11px; }
.forecast-method i { color: #87a0a9; font-style: normal; }
.forecast-method small { margin-left: auto; color: #667d88; }
.rain-editor { display: grid; padding: 10px; border: 1px solid #cadcdd; background: #f4faf8; }
.rain-editor summary, .forecast-ledger summary { cursor: pointer; color: #176c67; font-size: 12px; padding: 4px 0; }
.rain-editor > .input, .rain-editor > button, .rain-editor > label, .rain-fill { margin-top: 8px; }
.rain-editor textarea { width: 100%; resize: vertical; line-height: 1.6; }
.rain-editor label { display: block; font-size: 10px; }
.rain-fill { display: flex; gap: 6px; align-items: center; }
.rain-fill .input { width: 65px; min-width: 0; }
.rain-fill .btn { flex: 1; padding-inline: 4px; }
@media (max-width: 700px) { .forecast-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } .backend-forecast-chart { height: 310px; } }

.forecast-readiness { display: flex; align-items: center; gap: 18px; min-width: 0; padding: 10px 14px; border-color: #d6e2e7; }
.unavailable-head { flex: 0 0 230px; padding-right: 16px; border-right: 1px solid #dce6eb; }
.unavailable-head h3 { margin: 0 0 3px; color: #233f4d; font-size: 13px; }
.unavailable-head p { margin: 0; color: #7b532f; font-size: 12px; }
.unavailable-head p { color: #6b7f89; font-size: 10px; line-height: 1.4; }
.workflow-steps { display: grid; grid-template-columns: minmax(90px, 1fr) auto minmax(90px, 1fr) auto minmax(90px, 1fr) auto minmax(90px, 1fr) auto minmax(90px, 1fr); align-items: center; flex: 1; min-width: 0; }
.workflow-steps > div { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 1px 7px; align-items: center; }
.workflow-steps span { grid-row: 1 / 3; display: grid; width: 24px; height: 24px; place-items: center; border: 1px solid #cbd9df; background: #f5f8f9; color: #607580; font-size: 10px; font-weight: 900; }
.workflow-steps b { color: #294753; font-size: 11px; }
.workflow-steps small { color: #7a8d96; font-size: 9px; }
.workflow-steps i { color: #9aadb5; font-style: normal; }
.workflow-steps .done span { border-color: #80b7a2; background: #eaf5f0; color: #176548; }
.workflow-steps .blocked span { border-color: #d9a68a; background: #fff1e9; color: #a64f21; }

.left-col {
  grid-column: 1;
  grid-row: 1 / span 2;
  min-width: 0;
}

.center-col {
  display: grid;
  grid-column: 2;
  grid-row: 1;
  grid-template-rows: minmax(410px, auto) auto;
  gap: 12px;
  min-width: 0;
}

.right-col {
  display: grid;
  grid-column: 3;
  grid-row: 1;
  grid-template-rows: minmax(420px, auto) auto;
  gap: 12px;
  min-width: 0;
}
.request-workspace {
  display: grid;
  grid-column: 2 / 4;
  grid-row: 1;
  align-content: start;
  gap: 18px;
  min-height: 420px;
}
.request-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding-bottom: 14px; border-bottom: 1px solid #d7e4e9; }
.request-head h3 { margin: 4px 0 6px; color: #213f4b; font-size: 18px; }
.request-head p { max-width: 720px; margin: 0; color: #667d88; font-size: 11px; line-height: 1.55; }
.request-head > b { padding: 6px 10px; border: 1px solid #d8b273; background: #fff7e9; color: #9c5c18; font-size: 11px; white-space: nowrap; }
.request-kicker { color: #147d73; font-size: 9px; font-weight: 900; letter-spacing: .16em; }
.request-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.request-grid section { display: grid; align-content: start; gap: 1px; border: 1px solid #d6e2e7; background: #d6e2e7; }
.request-grid h4 { margin: 0; padding: 10px 11px; background: #edf5f4; color: #245650; font-size: 12px; }
.request-grid section > div { display: grid; grid-template-columns: minmax(92px, .75fr) minmax(0, 1.25fr); gap: 3px 10px; padding: 10px 11px; background: #fff; }
.request-grid span { color: #718590; font-size: 10px; }
.request-grid b { overflow-wrap: anywhere; color: #294753; font-size: 11px; text-align: right; }
.request-grid small { grid-column: 1 / -1; color: #82939b; font-size: 9px; text-align: right; }
.request-flow { display: grid; grid-template-columns: repeat(7, auto); justify-content: center; align-items: center; gap: 10px; padding: 13px; background: #f5f9fa; color: #46636f; font-size: 11px; font-weight: 800; }
.request-flow i { color: #87a0a9; font-style: normal; }

.quality-panel {
  display: grid;
  grid-column: 2 / 4;
  grid-row: 2;
  gap: 8px;
  min-width: 0;
}

.settings-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.forecast-mode-switch { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; margin: 0 0 8px; padding: 3px; border: 1px solid #cadcdd; border-radius: 5px; background: #f1f7f6; }
.forecast-mode-switch button { display: grid; gap: 2px; min-height: 40px; padding: 5px 7px; border: 0; border-radius: 3px; background: transparent; color: #5a747a; text-align: left; cursor: pointer; }
.forecast-mode-switch button.active { background: #fff; color: #14776e; box-shadow: 0 1px 3px rgba(24, 76, 78, .12); }
.forecast-mode-switch b { font-size: 11px; }
.forecast-mode-switch small { color: #7b8d91; font-size: 9px; }
.parameter-source-strip { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; padding: 6px 8px; border-left: 3px solid #2a8178; background: #f4faf8; color: #6b8083; font-size: 10px; }
.parameter-source-strip b { color: #275a5a; font-size: 10px; text-align: right; }
.forecast-parameter-card { display: grid; gap: 5px; margin: -2px 0 10px; padding: 8px 9px; border: 1px solid #d8e3e6; border-left: 3px solid #a47732; background: #fffaf1; }
.forecast-parameter-card > span,
.forecast-parameter-card > label { color: #6b7c80; font-size: 10px; }
.forecast-parameter-card > b { overflow-wrap: anywhere; color: #6d4a18; font-size: 12px; }
.forecast-parameter-card small { color: #7b8587; font-size: 10px; line-height: 1.4; }
.forecast-parameter-card .input { min-height: 30px; }

.form-stack {
  display: grid;
  gap: 12px;
  min-width: 0;
}

.field {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.field label,
.field-group-title {
  color: #355061;
  font-size: 12px;
  font-weight: 800;
}

.field-group-title {
  margin-top: 2px;
  padding-top: 10px;
  border-top: 1px solid #dce7ed;
  color: #0f766e;
}

.field-note {
  color: #718596;
  font-size: 11px;
  line-height: 1.45;
}

.field-value {
  display: grid;
  gap: 2px;
  min-width: 0;
  padding: 7px 8px;
  border-left: 2px solid #8dbdb7;
  background: #f4faf8;
}

.field-value b,
.field-value span {
  overflow-wrap: anywhere;
}

.field-value b {
  color: #244650;
  font-size: 11px;
}

.field-value span {
  color: #647b87;
  font-size: 10px;
  line-height: 1.4;
}

.rain-value {
  border-left-color: #7da9cf;
  background: #f4f9ff;
}

.continuity-card {
  display: grid;
  grid-template-columns: 1fr;
  gap: 4px;
  padding: 8px;
  border: 1px solid #c8ded9;
  border-radius: 5px;
  background: #f5fbf9;
}

.continuity-card > div {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.continuity-card > i {
  display: none;
}

.continuity-card span {
  color: #637b84;
  font-size: 10px;
}

.continuity-card b {
  overflow: hidden;
  color: #173b48;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.continuity-card .issue b {
  color: var(--teal);
}

.forcing-chain {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 6px;
  padding: 8px;
  border: 1px solid #d5e4eb;
  background: #f9fbfc;
}

.forcing-chain div {
  display: grid;
  gap: 2px;
}

.forcing-chain span {
  color: #718596;
  font-size: 10px;
}

.forcing-chain b {
  color: #28485a;
  font-size: 11px;
}

.forcing-chain i {
  color: #7493a5;
  font-style: normal;
}

.input-summary {
  display: grid;
  gap: 1px;
  border: 1px solid #d6e2e8;
  background: #d6e2e8;
}

.input-summary > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
  padding: 7px 8px;
  background: #fff;
}

.input-summary span {
  color: #6b8090;
  font-size: 10px;
}

.input-summary b {
  overflow-wrap: anywhere;
  color: #294353;
  font-size: 11px;
  text-align: right;
}

.action-dock {
  display: grid;
  gap: 8px;
  margin: 14px -12px -12px;
  padding: 12px;
  border-top: 1px solid #cfdee7;
  background: #f8fbfd;
}

.run-button {
  min-height: 40px;
  font-size: 14px;
}

.secondary-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.secondary-actions .btn {
  min-width: 0;
}

.condition-note {
  margin: 0;
  color: #a45c16;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.4;
}
.submission-status {
  margin: 0;
  padding: 8px 9px;
  border-left: 3px solid #147d73;
  background: #eef8f5;
  color: #315c5a;
  font-size: 11px;
  line-height: 1.45;
}

.latest-badge {
  color: #148276;
  font-size: 10px;
  font-weight: 800;
}

.lake-focus {
  margin-top: 2px;
  border-color: #8dbfce;
  background: #f0f8fa;
  color: #17647c;
  font-weight: 800;
}

.hydro-panel {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 410px;
}

.hydro-chart {
  min-height: 0;
  height: 100%;
}

.hydro-title-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.hydro-mode-tabs {
  display: inline-flex;
  padding: 2px;
  border: 1px solid #c7d7df;
  border-radius: 5px;
  background: #eef4f6;
}

.hydro-mode-tabs button {
  min-height: 26px;
  padding: 0 8px;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: #536d7b;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
}

.hydro-mode-tabs button.active {
  background: #fff;
  color: #0f766e;
  box-shadow: 0 1px 3px rgba(27, 65, 76, 0.14);
}

.sub-hint {
  color: #718596;
  font-size: 11px;
  font-weight: 600;
}

.result-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 9px 10px;
}

.detail-grid > div {
  display: grid;
  align-content: start;
  gap: 3px;
  color: #637989;
  font-size: 10px;
  line-height: 1.35;
}

.detail-grid b {
  overflow-wrap: anywhere;
  color: #244354;
  font-size: 12px;
}

.detail-grid .forecast-peak {
  grid-row: span 2;
}

.detail-grid .forecast-peak b {
  color: #d86412;
  font-size: 23px;
  line-height: 1;
}

.threshold-note,
.method-note {
  margin: 9px 0 0;
  color: #718596;
  font-size: 10px;
  line-height: 1.45;
}

.method-flow {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: stretch;
  gap: 5px;
}

.method-flow div {
  display: grid;
  align-content: center;
  gap: 4px;
  min-height: 70px;
  padding: 7px;
  border: 1px solid #d5e3ea;
  background: #f8fbfc;
}

.method-flow span {
  color: #6c8391;
  font-size: 10px;
}

.method-flow b {
  overflow-wrap: anywhere;
  color: #264555;
  font-size: 11px;
  line-height: 1.4;
}

.method-flow i {
  align-self: center;
  color: #7596a4;
  font-style: normal;
}

.map-panel {
  display: grid;
  grid-template-rows: auto minmax(300px, 1fr) auto auto;
  min-height: 420px;
  overflow: hidden;
}

.map-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 46px;
  padding: 7px 10px;
  border-bottom: 1px solid #d8e4e9;
  background: #fbfdfe;
}

.map-head > div:first-child {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.map-head b {
  color: #264453;
  font-size: 12px;
}

.map-head span {
  color: #718596;
  font-size: 10px;
}

.grid-switches {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}

.grid-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #436172;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
}

.grid-toggle input {
  width: 14px;
  height: 14px;
  accent-color: #0f766e;
}

.map-body {
  min-height: 300px;
}

.map-time-tabs {
  display: flex;
  gap: 4px;
  padding: 5px 8px;
  border-top: 1px solid #d7e4ea;
  background: #f8fbfc;
}

.map-time-tabs button {
  flex: 1;
  min-width: 0;
  min-height: 27px;
  padding: 0 4px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: #587082;
  font-size: 10px;
  font-weight: 800;
  cursor: pointer;
}

.map-time-tabs button.active {
  border-color: #b5d5d0;
  background: #eaf7f5;
  color: #0f766e;
}

.map-note {
  margin: 0;
  padding: 6px 10px 9px;
  color: #718596;
  font-size: 10px;
  line-height: 1.4;
}

.contribution-panel {
  min-width: 0;
}

.total-flow {
  color: #d86412;
  font-size: 12px;
}

.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.mini-table th,
.mini-table td {
  padding: 6px 4px;
  border-bottom: 1px solid #e3ebef;
  color: #526e7d;
  text-align: left;
}

.mini-table th {
  color: #718596;
  font-size: 10px;
  font-weight: 700;
}

.mini-table b {
  color: #1b6d88;
  font-size: 11px;
}

.quality-panel .panel-title {
  align-items: center;
  margin-bottom: 0;
}

.quality-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  border: 1px solid #d7e4e8;
  background: #d7e4e8;
}

.quality-grid > div {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 8px;
  border-right: 1px solid #d7e4e8;
  background: #fff;
}

.quality-grid > div:last-child {
  border-right: 0;
}

.quality-grid span {
  color: #718596;
  font-size: 10px;
}

.quality-grid b {
  overflow-wrap: anywhere;
  color: #264452;
  font-size: 11px;
}

.quality-note {
  margin: 0;
  padding: 7px 9px;
  border-left: 3px solid var(--orange);
  background: #fff7ed;
  color: #8d4d14;
  font-size: 11px;
  line-height: 1.45;
}

.grid-mask {
  position: fixed;
  inset: 0;
  z-index: 800;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(13, 37, 51, 0.45);
}

.grid-dialog {
  width: min(360px, 92vw);
  background: #fff;
}

.icon-button {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 1px solid #d3e0e6;
  border-radius: 4px;
  background: #fff;
  color: #456272;
  cursor: pointer;
}

b.ok { color: var(--green); }
b.warn { color: var(--orange); }
b.bad { color: var(--red); }

@media (min-width: 1181px) {
  .left-col {
    align-self: start;
    max-height: calc(100vh - var(--topbar) - 94px);
    overflow-y: auto;
    scrollbar-gutter: stable;
  }

  .map-panel :deep(.map-legend) {
    right: 8px;
    bottom: 8px;
    min-width: 116px;
    padding: 6px 8px;
    border-radius: 5px;
    font-size: 10px;
  }

  .map-panel :deep(.legend-title) { margin-bottom: 3px; }
  .map-panel :deep(.legend-row) { gap: 5px; min-height: 14px; }
  .map-panel :deep(.swatch) { width: 10px; height: 7px; }
}

@media (max-width: 1180px) {
  .forecast-layout {
    grid-template-columns: minmax(260px, 300px) minmax(0, 1fr);
  }

  .left-col {
    grid-column: 1;
    grid-row: 1 / span 3;
  }

  .center-col {
    grid-column: 2;
    grid-row: 1;
  }

  .right-col {
    grid-column: 2;
    grid-row: 2;
    grid-template-rows: minmax(420px, auto) auto;
  }

  .forecast-layout.preparation-mode .preparation-column { grid-column: 2; }
  .request-workspace { grid-column: 2; grid-row: 1 / span 2; }
  .request-grid { grid-template-columns: 1fr; }

  .quality-panel {
    grid-column: 1 / -1;
    grid-row: 3;
  }
}

@media (max-width: 820px) {
  .forecast-unavailable { width: 100%; }
  .readiness-list { grid-template-columns: 1fr; }
  .readiness-list > div { border-right: 0; border-bottom: 1px solid #d7e2e8; }
  .readiness-list > div:nth-last-child(2) { border-bottom: 1px solid #d7e2e8; }
  .readiness-list > div:last-child { border-bottom: 0; }

  .forecast-metrics {
    grid-auto-flow: row;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .forecast-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .forecast-layout.preparation-mode .preparation-column {
    grid-column: 1;
    grid-row: auto;
  }

  .left-col,
  .center-col,
  .right-col,
  .request-workspace,
  .quality-panel {
    grid-column: 1;
    grid-row: auto;
  }

  .left-col {
    max-height: none;
    overflow: visible;
  }

  .center-col {
    grid-template-rows: 400px auto;
  }
  .request-flow { grid-template-columns: 1fr; justify-items: center; }
  .request-flow i { transform: rotate(90deg); }
}

@media (max-width: 560px) {
  .readiness-list > div { grid-template-columns: 1fr auto; }
  .readiness-list small { grid-column: 1 / -1; }
  .unavailable-actions { display: grid; grid-template-columns: 1fr; }

  .forecast-metrics,
  .result-row,
  .detail-grid,
  .quality-grid {
    grid-template-columns: 1fr;
  }

  .metric-action {
    min-height: 50px;
  }

  .hydro-title-tools,
  .map-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .grid-switches {
    justify-content: flex-start;
  }

  .method-flow {
    grid-template-columns: 1fr;
  }

  .method-flow i {
    display: none;
  }

  .detail-grid .forecast-peak {
    grid-row: auto;
  }

  .quality-grid > div {
    border-right: 0;
    border-bottom: 1px solid #d7e4e8;
  }

  .quality-grid > div:last-child {
    border-bottom: 0;
  }

  .map-time-tabs {
    overflow-x: auto;
  }

  .map-time-tabs button {
    min-width: 50px;
  }
}
</style>
