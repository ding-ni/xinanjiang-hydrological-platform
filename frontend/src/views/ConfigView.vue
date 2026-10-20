<template>
  <section class="view-config">
    <div class="metric-strip config-metrics">
      <div class="metric config-scope"><span>配置内容</span><b>流域 · 站网 · 模型能力 · 参数库</b></div>
      <div class="metric"><span>{{ app.backendCase ? '参数库' : '模型方案' }}</span><b>{{ activeConfig.version }}</b></div>
      <div class="metric">
        <span>{{ app.hasLakeBalance ? '保存状态' : '配置来源' }}</span>
        <b :class="app.hasLakeBalance ? (cfg.dirty ? 'warn' : 'ok') : 'ok'">{{ app.hasLakeBalance ? (cfg.dirty ? '有未保存修改' : '已保存') : '项目资料目录' }}</b>
      </div>
      <div class="metric"><span>{{ app.hasLakeBalance ? '最近保存' : '当前尺度' }}</span><b>{{ app.hasLakeBalance ? cfg.savedAt : app.activeScaleMeta.label }}</b></div>
      <ContextStatusMetric />
    </div>

    <div class="body">
      <aside class="panel panel-pad nav">
        <h3 class="panel-title">配置分类</h3>
        <button
          v-for="t in tabs"
          :key="t"
          type="button"
          class="nav-item"
          :class="{ active: tab === t }"
          @click="tab = t"
        >
          {{ t }}
        </button>
        <button v-if="app.hasLakeBalance" class="btn primary block" type="button" style="margin-top: 12px" @click="save">
          保存配置
        </button>
        <p class="hint">{{ app.hasLakeBalance ? '保存当前试算方案的业务规则。' : '当前项目配置来自已接入的资料目录。' }}</p>
      </aside>

      <main class="panel panel-pad">
        <RoutingDefaults v-if="tab==='河网与断面'" />
        <section v-if="tab==='状态库'"><h2>状态库</h2><p class="hint">保存计算成果的完整末状态，用于继续模拟。</p><RouterLink class="btn primary" to="/state?tab=library">打开状态库</RouterLink></section>
        <template v-if="tab === '水雨情资料'">
          <div class="panel-title-row">
            <h3 class="panel-title">水雨情资料</h3>
            <button v-if="app.basinId === 'nananxi-xufan'" class="btn" type="button" :disabled="refreshingProjectData" @click="refreshProjectData">{{ refreshingProjectData ? '正在更新' : '更新资料目录' }}</button>
          </div>
          <template v-if="app.basinId === 'nananxi-xufan'">
            <template v-if="isModelInputWorkspace">
            <table class="table"><thead><tr><th>水文资料</th><th>覆盖时段</th><th>记录</th></tr></thead><tbody><tr><td>测站雨量</td><td>{{ observationRangeLabel('rain') }}</td><td>{{ nananxiRainStationCount }} 站 · {{ recordCountLabel('rain') }}</td></tr><tr><td>徐畈水位与流量</td><td>{{ observationRangeLabel('river') }}</td><td>{{ recordCountLabel('river') }}</td></tr></tbody></table>
            <RouterLink class="btn primary" to="/data">查询水文资料</RouterLink>
            </template>
            <template v-else>
            <p class="hint">以下为离线原始资料目录，仅供来源浏览；不代表真实 整编资料 服务或模型计算已接入。</p>
            <div class="pipeline-wide preparation-pipeline">
              <div><span>1</span><b>实测资料</b><small>逐小时雨量 / 水位 / 流量</small></div>
              <i>→</i><div><span>2</span><b>1 km 降雨栅格</b><small>逐小时 NC / 110 个有效栅格</small></div>
              <i>→</i><div><span>3</span><b>洪水场次</b><small>226 场降雨与出口流量过程</small></div>
              <i>→</i><div><span>4</span><b>产汇流计算</b><small>参数率定 / 验证 / 洪水模拟</small></div>
            </div>
            <table class="table status-table data-inventory">
              <thead><tr><th>资料项</th><th>资料范围</th><th>规模</th><th>状态</th></tr></thead>
              <tbody>
                <tr><td>逐小时雨量</td><td>{{ observationRangeLabel('rain') }}</td><td>{{ recordCountLabel('rain') }} · {{ nananxiRainStationCount }} 站有记录</td><td><span class="tag">已接入</span></td></tr>
                <tr><td>徐畈水位流量</td><td>{{ observationRangeLabel('river') }}</td><td>{{ recordCountLabel('river') }}</td><td><span class="tag">已接入</span></td></tr>
                <tr><td>洪水场次数据包</td><td>逐场关联雨量与出口实测过程</td><td>{{ floodEvents.length }} 场</td><td><span class="tag">已生成</span></td></tr>
                <tr><td>{{ app.activeScaleMeta.label }} 空间包</td><td>边界、DEM 栅格、河网、站点、出口</td><td>{{ activeConfig.gridCount }} 个有效栅格</td><td><span class="tag">已接入</span></td></tr>
                <tr><td>1 km 实测降雨栅格</td><td>与 1 km DEM 有效栅格对应的逐小时 NC</td><td>110 个有效栅格</td><td><span class="tag">已接入</span></td></tr>
                <tr><td>0.1°降雨资产</td><td>离线空间展示目录</td><td>模型整编 契约未登记</td><td><span class="tag gray">仅浏览</span></td></tr>
              </tbody>
            </table>
            <div class="readiness-cards">
              <div class="ready"><span>历史洪水资料</span><b>{{ floodEvents.length }} 场</b><small>供目录浏览；计算以已登记的雨量、流量和预热时段为准。</small></div>
              <div class="ready"><span>场次蒸散发</span><b>按 0 mm/h</b><small>当前小时尺度洪水场次按 0 mm/h 计算。</small></div>
              <div class="missing"><span>预报降雨</span><b>未接入</b><small>缺少起报时刻之后的逐小时降雨产品。</small></div>
              <div class="pending"><span>起算状态</span><b>待模型生成</b><small>用场次前降雨预热得到。</small></div>
              <div class="pending"><span>产流汇流服务</span><b>{{ app.backendCase?.ready && !app.backendError ? '已接入 · 诊断模式' : '当前尺度未就绪' }}</b><small>仅南安溪 1 km 验收；不在浏览器内计算。</small></div>
            </div>
            <div class="data-contract-card">
              <div><b>标准资料字段清单</b><span>{{ projectDataValidation.ok ? '目录结构和核心空间资料已通过检查' : '目录仍有必填项待补充' }}</span></div>
              <strong>{{ app.projectData?.fieldMapping?.standardProducts?.length || 0 }} 项标准产品</strong>
            </div>
            </template>
          </template>
          <template v-else>
            <p class="hint">接收雨量、水位、流量和预报降水，整编质控后形成模型所需的时序资料和空间降水场。</p>
            <div class="pipeline-wide">
              <div><span>1</span><b>外部资料源</b><small>雨量 / 水位 / 流量 / 预报降水</small></div>
              <i>→</i><div><span>2</span><b>水雨情资料库</b><small>整编、质控、分批保存</small></div>
              <i>→</i><div><span>3</span><b>降水空间化</b><small>站点插值、尺度转换、空间校验</small></div>
              <i>→</i><div><span>4</span><b>模型降水资料</b><small>供场次率定、模拟和业务预报使用</small></div>
            </div>
            <div class="config-grid">
              <div class="field"><label>外部资料源连接</label><input class="input" value="hydro_source_prod · 只读" /></div>
              <div class="field"><label>整编资料库</label><input class="input" :value="activeConfig.sql" /></div>
              <div class="field"><label>实测网格资料目录</label><input class="input" :value="activeConfig.nc" /></div>
              <div class="field"><label>更新 NC 输出命名</label><input class="input" :value="activeConfig.ncPattern" /></div>
            </div>
          </template>
        </template>

        <template v-else-if="tab === '资料与对象'">
          <h3 class="panel-title">资料与对象</h3>
          <p class="hint">统一设置率定和场次模拟所用的资料、控制断面与预热期。这里的资料范围随目录更新，任务提交时沿用当前设置。</p>
          <div class="config-grid two">
            <div class="field"><label>出口水文资料</label><input class="input" :value="activeDataset.hydrology" readonly /></div>
            <div class="field"><label>降雨资料</label><input class="input" :value="activeDataset.precipitation" readonly /></div>
            <div class="field"><label for="config-section">控制断面</label><select id="config-section" class="input" :value="calibration.section" @change="calibration.setSection($event.target.value)"><option v-for="section in configSectionOptions" :key="section" :value="section">{{ section }}</option></select></div>
            <div class="field"><label for="config-warmup">预热期（天）</label><input id="config-warmup" class="input" type="number" min="1" :value="calibration.warmupDays" @change="calibration.setSimulationField('warmupDays', $event.target.value)" /></div>
            <div class="field"><label>蒸散发与初态</label><input class="input" :value="isModelInputWorkspace ? '按率定与模拟所选条件' : app.basinId === 'nananxi-xufan' ? '0 mm/h（小时尺度）' : '按资料目录读取'" readonly /></div>
            <div class="field"><label>资料共同覆盖</label><input class="input" :value="commonObservationRangeLabel" readonly /></div>
          </div>
          <div class="data-object-note"><b>设置说明</b><span>{{ isModelInputWorkspace ? '蒸散发、初态、预热或连续起点在率定与模拟中选定，实际计算条件随任务保存。' : '预热期只用于模型起算状态，不改变降雨和出口水文原始资料。选择场次后，系统按场次起始时刻向前读取对应预热资料。' }}</span></div>
          <div class="data-object-status">
            <span>当前率定对象</span><b>{{ calibration.section }}</b><span>当前预热期</span><b>{{ calibration.warmupDays }} 天</b><span class="data-object-saved">修改后自动保存到当前工作区</span>
          </div>
        </template>

        <template v-else-if="tab === '模型方案'">
          <h3 class="panel-title">分布式模型方案</h3>
          <p v-if="isModelInputWorkspace" class="hint"><button class="link" @click="tab='河网与断面'">查看河网与保存常用断面</button> · <RouterLink to="/calibration">编辑率定方案参数</RouterLink></p>
          <div class="scheme-summary">
            <div><span>方案</span><b>{{ activeConfig.name }}</b><small>{{ app.backendCase ? '参数库：' : '' }}{{ activeConfig.version }} · {{ activeConfig.status }}</small></div>
            <div><span>计算尺度</span><b>{{ activeConfig.grid }} / {{ activeConfig.gridCount.toLocaleString() }} 有效网格</b><small>{{ activeConfig.subbasins }} 个{{ app.hasLakeBalance ? '入湖流域' : '流域空间单元' }} · {{ activeConfig.dem }}</small></div>
          </div>
          <div class="config-grid two">
            <div class="field"><label>产流模型</label><input class="input" :value="activeConfig.runoffMethod" readonly /></div>
            <div class="field"><label>水源划分</label><input class="input" :value="activeConfig.separation" readonly /></div>
            <div class="field"><label>坡面汇流</label><input class="input" :value="activeConfig.hillslope" readonly /></div>
            <div class="field"><label>坡面至河道汇流</label><input class="input" :value="activeConfig.gridToRiver" readonly /></div>
            <div class="field"><label>河网汇流</label><input class="input" :value="activeConfig.channel" readonly /></div>
            <div class="field"><label>参数分区</label><input class="input" :value="isModelInputWorkspace ? '产流参数 · 单元坡面参数 · 河段参数' : '离线方案目录'" readonly /></div>
          </div>
          <div v-if="app.backendCase" class="scheme-note"><b>计算分区</b><span>1 km 网格产流，按固定面积权重汇入汇流单元</span><span>汇流单元三分量线性水库 → 河段 Muskingum 汇流 → 徐畈流量</span><span>30 m 是提取河网与断面分区的地形分辨率</span></div>
          <div v-else class="scheme-note"><b>计算分区</b><span>网格：承接降水并计算产流</span><span>子流域：参数分区、坡面汇流和河网组织</span><span>面雨量：按控制区面积加权</span></div>
          <details v-if="isModelInputWorkspace"><summary>模型功能目录</summary><table class="table module-status" aria-label="模型功能目录">
            <thead><tr><th>功能</th><th>状态</th><th>说明</th></tr></thead>
            <tbody><tr v-for="module in app.backendCase?.modules || []" :key="module.id"><td>{{ module.label }}</td><td>{{ module.status === 'integrated' ? '可用' : '待开发' }}</td><td>{{ module.reason }}</td></tr><tr v-if="!app.backendCase?.modules?.length"><td colspan="3">暂无功能目录</td></tr></tbody>
          </table></details>
        </template>

        <template v-else-if="tab === '流域与站网'">
          <h3 class="panel-title">流域与站网</h3>
          <template v-if="app.basinId === 'nananxi-xufan'">
            <RouterLink class="btn" to="/data">查询实测资料</RouterLink>
            <details v-if="app.backendCase" class="workspace-input-settings" open><summary>站网与默认处理规则</summary>
              <p>使用水文资料库 · 新方案按所选洪水自动准备雨量、流量与网格。已保存方案保留自己的站网和输入。</p>
              <p v-if="networkError" role="alert" class="warn-text">{{ networkError }}</p>
              <p>{{ commonObservationRangeLabel }} · {{ databaseStations.length }} 条站点要素记录。起止日期表示覆盖边界，计算前逐时检查缺测。</p>
              <template v-if="network"><label>出口水文站 <span>{{ stationDisplay(network.flowStation) }}</span><details><summary>站号与MDB别名</summary><input class="input" v-model.trim="network.flowStation" /></details></label>
                <table class="table"><thead><tr><th>雨量站</th><th>经度</th><th>纬度</th><th></th></tr></thead><tbody><tr v-for="(station, index) in network.stations" :key="index"><td><b>{{ stationDisplay(station.id) }}</b><details><summary>站号 / 别名 {{ station.id }}</summary><input class="input" v-model.trim="station.id" /></details></td><td><input class="input" type="number" step="any" v-model.number="station.lon" /></td><td><input class="input" type="number" step="any" v-model.number="station.lat" /></td><td><button class="btn mini" @click="network.stations.splice(index, 1)">移除</button></td></tr></tbody></table>
                <div class="btn-row"><button class="btn" @click="network.stations.push({ id: '', lon: null, lat: null })">添加雨量站</button><button class="btn primary" :disabled="networkBusy" @click="saveNetwork">保存站网</button></div>
              </template>
              <h4>降雨插值</h4><div class="form-grid"><div class="field"><label>距离幂次<input class="input" type="number" min="1" max="4" step="0.1" v-model.number="preparationDefaults.idwPower" /></label></div><div class="field"><label>最少有效雨量站<input class="input" type="number" min="1" max="32" v-model.number="preparationDefaults.minimumStations" /></label></div></div><button class="btn" @click="savePreparationDefaults">保存处理规则</button>
            </details>
            <div class="station-summary">
              <span><b>{{ nananxiStationRows.length }}</b> 个登记站点</span>
              <span><b>{{ nananxiRainStationCount }}</b> 个站有原始雨量记录</span>
              <span><b>1</b> 个出口水文站</span>
            </div>
            <table class="table station-table">
              <thead>
                <tr><th>站名</th><th>站码</th><th>站别</th><th>实测资料</th><th>模型用途</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in nananxiStationRows" :key="r.code">
                  <td><b>{{ r.name }}</b></td>
                  <td>{{ r.code }}</td>
                  <td>{{ r.type }}</td>
                  <td><span class="station-status" :class="r.ready ? 'ready' : 'missing'">{{ r.data }}</span></td>
                  <td>{{ r.use }}</td>
                </tr>
              </tbody>
            </table>
          </template>
          <table v-else class="table">
            <thead>
              <tr><th>站名</th><th>类型</th><th>所属分区</th><th>插值权重</th><th>参与降雨插值</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in cfg.stationWeights" :key="r.code">
                <td>{{ r.name }}</td>
                <td>{{ r.type }}</td>
                <td>{{ r.basin }}</td>
                <td>
                  <input
                    v-if="r.weight != null"
                    class="cell-input sm"
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                    :value="r.weight"
                    @input="onWeight(r.code, $event.target.value)"
                  />
                  <span v-else>—</span>
                </td>
                <td>
                  <label class="toggle-cell">
                    <input type="checkbox" :checked="r.use" @change="onToggleUse(r.code)" />
                    <span>{{ r.use ? '启用' : '停用' }}</span>
                  </label>
                </td>
              </tr>
            </tbody>
          </table>
        </template>

        <template v-else-if="tab === '参数库'">
          <h3 class="panel-title">参数库与状态目录</h3>
          <p v-if="isModelInputWorkspace" class="hint">
            发布版本：<b>{{ parameterLibrary?.automaticForecastLibrary?.productionVersion || '暂无发布版本' }}</b>
          </p>
          <p v-else class="hint">
            生产版本：<b>{{ cfg.productionParamVersion }}</b>
            （率定「发布为生产」后更新）
          </p>
          <template v-if="app.basinId === 'nananxi-xufan'">
            <div class="parameter-library-switch" role="tablist" aria-label="参数库类型">
              <button type="button" :class="{ active: parameterLibraryMode === 'simulation' }" @click="parameterLibraryMode = 'simulation'">洪水模拟与率定参数库</button>
              <button type="button" :class="{ active: parameterLibraryMode === 'automatic' }" @click="parameterLibraryMode = 'automatic'">自动预报参数库</button>
            </div>
            <template v-if="parameterLibraryMode === 'simulation'">
              <p class="hint">命名参数保存产流、汇流参数和配套起算规则，可作为率定初值或直接用于洪水模拟。</p>
              <p v-if="namedParameterError" class="hint bad">{{ namedParameterError }}</p>
              <table class="table parameter-library-table"><thead><tr><th>参数名称</th><th>起算规则</th><th>来源成果</th><th>操作</th></tr></thead><tbody>
              <tr v-for="row in namedParameters" :key="row.id"><td>{{ row.name }} <button class="link" :aria-label="'修改参数名称 '+row.name" @click="renameNamedParameter(row)">✎</button></td><td>{{ row.statePolicy === 'continuous-origin' ? '连续演算' : '分场预热' }} · {{ row.warmupDays }}天</td><td><RouterLink :to="{path:'/archive',query:{task:row.sourceTaskId}}">查看原计算</RouterLink></td><td><RouterLink :to="{path:'/calibration',query:{parameter:row.id}}">用作初值</RouterLink> · <RouterLink :to="{path:'/simulation',query:{parameter:row.id}}">洪水模拟</RouterLink></td></tr>
                <tr v-if="!namedParameters.length"><td colspan="4">暂无命名参数，请从计算成果中保存。</td></tr>
              </tbody></table>
              <p class="hint"><RouterLink to="/simulation">洪水模拟</RouterLink> · <RouterLink to="/state?tab=library">状态库</RouterLink> · <RouterLink to="/archive">查看计算成果</RouterLink></p>
              <details><summary>历史参数记录</summary><div class="parameter-library-note"><b>率定参数</b><strong>{{ parameterLibrary?.simulationRateLibrary?.status || '正在读取' }}</strong></div>
              <table class="table parameter-library-table">
                <thead><tr><th>参数版本</th><th>状态</th><th>对应场次集</th><th>独立检验</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="row in parameterLibrary?.simulationRateLibrary?.versions || []" :key="row.id"><td><b>{{ row.id }}</b></td><td>{{ row.status }}</td><td>{{ row.eventSetVersion || '—' }}</td><td>{{ row.independentTest?.status || (row.independentTest?.passed ? '通过' : '待返回') }}</td><td><button class="link" type="button" @click="loadVer(row.id)">载入试算</button></td></tr>
                  <tr v-if="!(parameterLibrary?.simulationRateLibrary?.versions || []).length"><td colspan="5">暂无率定参数 <RouterLink to="/calibration">率定与模拟</RouterLink></td></tr>
                </tbody>
              </table>
              </details>
            </template>
            <template v-else>
              <div class="parameter-library-note production"><b>自动预报参数</b><strong>{{ parameterLibrary?.automaticForecastLibrary?.status || '暂无发布版本' }}</strong></div>
              <table class="table parameter-library-table">
                <thead><tr><th>生产版本</th><th>状态</th><th>发布日期</th><th>独立检验</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="row in parameterLibrary?.automaticForecastLibrary?.versions || []" :key="row.id"><td><b>{{ row.id }}</b></td><td>{{ row.status }}</td><td>{{ row.releasedAt || '—' }}</td><td>{{ row.independentTest?.status || (row.independentTest?.passed ? '通过' : '待返回') }}</td><td><button class="link" type="button" @click="loadVer(row.id)">查看</button></td></tr>
                  <tr v-if="!(parameterLibrary?.automaticForecastLibrary?.versions || []).length"><td colspan="5">暂无发布版本</td></tr>
                </tbody>
              </table>
            </template>
          </template>
          <template v-else>
          <table class="table">
            <thead>
              <tr>
                <th>版本</th>
                <th>发布日期</th>
                <th>率定断面</th>
                <th>NSE</th>
                <th>批准人</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in paramRows"
                :key="row.ver"
                :class="{ active: row.ver === cfg.productionParamVersion || row.ver === baseProd(cfg.productionParamVersion) }"
              >
                <td>{{ row.ver }}</td>
                <td>{{ row.date }}</td>
                <td>{{ row.section }}</td>
                <td>{{ row.nse }}</td>
                <td>{{ row.by }}</td>
                <td>
                  <span class="tag" :class="isProd(row.ver) ? '' : 'gray'">
                    {{ isProd(row.ver) ? '生产' : '归档' }}
                  </span>
                </td>
                <td><button class="link" type="button" @click="loadVer(row.ver)">载入预报</button></td>
              </tr>
              <tr v-if="!paramRows.length"><td colspan="7">当前流域与分辨率尚未登记参数版本</td></tr>
            </tbody>
          </table>
          </template>
          <p class="hint">当前状态版本：<b>{{ app.stateVersion }}</b> · 当前流域与尺度：{{ app.contextKey }}</p>
        </template>

        <template v-else-if="tab === '洪水场次'">
          <h3 class="panel-title">洪水场次</h3>
          <p class="hint">{{ isModelInputWorkspace ? '整编场次目录' : '本地场次草稿' }}</p>
          <div class="flood-ledger-summary">
            <div><span>目录场次</span><b>{{ floodEventSummary.total }}</b></div>
            <div><span>当前启用</span><b>{{ floodEventSummary.active }}</b></div>
            <div><span>待复核</span><b>{{ floodEventSummary.draft }}</b></div>
            <div><span>待核查</span><b>{{ floodEventSummary.pending }}</b></div>
            <div><span>最大 Qmax</span><b>{{ floodEventSummary.maxPeak != null ? `${floodEventSummary.maxPeak.toLocaleString()} m³/s` : '—' }}</b></div>
            <div><span>缺少 Qmax</span><b>{{ floodEventSummary.missingPeak }}</b></div>
          </div>
          <div class="flood-ledger-toolbar">
            <label><span>检索</span><input v-model.trim="floodEventQuery" class="input" placeholder="洪号、名称或日期" /></label>
            <label><span>状态</span><select v-model="floodEventStatus" class="input"><option value="all">全部</option><option value="active">启用</option><option value="draft">草稿</option><option value="pending">待核查</option><option value="inactive">停用</option></select></label>
            <button v-if="!isModelInputWorkspace" class="btn primary" type="button" @click="beginCreateFloodEvent">新增洪水场次</button>
          </div>
          <div v-if="floodEventEditorOpen" class="flood-event-editor">
            <div class="flood-editor-head"><b>{{ floodEventEditId ? '修改洪水场次' : '新增洪水场次' }}</b><button v-if="floodEventEditId" class="link" type="button" @click="cancelFloodEventEdit">取消修改</button></div>
            <div class="config-grid flood-editor-grid">
              <div class="field"><label>名称</label><input v-model.trim="floodEventDraft.name" class="input" placeholder="例如：2024年6月洪水" /></div>
              <div class="field"><label>开始时间</label><input v-model="floodEventDraft.start" class="input" type="datetime-local" /></div>
              <div class="field"><label>结束时间</label><input v-model="floodEventDraft.end" class="input" type="datetime-local" /></div>
              <div class="field"><label>控制断面</label><select v-model="floodEventDraft.sectionId" class="input"><option v-for="section in sectionOptions" :key="section.id" :value="section.id">{{ section.name }}（{{ section.id }}）</option></select></div>
              <div class="field"><label>实测 Qmax（m³/s）</label><input :value="eventPeakLabel(floodEventDraft)" class="input derived-field" readonly /></div>
              <div class="field"><label>峰现时间</label><input :value="eventTimeLabel(floodEventDraft.peakTime)" class="input derived-field" readonly /></div>
              <div class="field field-wide"><label>备注</label><input v-model.trim="floodEventDraft.note" class="input" placeholder="资料来源或复核说明" /></div>
            </div>
            <div class="flood-editor-actions"><span>原始洪号来自资料目录；新增场次使用稳定的系统事件编号，后续资料更新只追加或修订。</span><button class="btn" type="button" @click="cancelFloodEventEdit">清空</button><button class="btn primary" type="button" @click="saveFloodEvent">{{ floodEventEditId ? '保存修改' : '登记场次' }}</button></div>
          </div>
          <div class="table-scroll flood-ledger-table-wrap">
            <table class="table flood-ledger-table">
              <thead><tr>
                <th><button type="button" class="table-sort" :aria-sort="floodSortAria('floodNo')" @click="toggleFloodSort('floodNo')">洪号 <i>{{ floodSortIndicator('floodNo') }}</i></button></th>
                <th><button type="button" class="table-sort" :aria-sort="floodSortAria('period')" @click="toggleFloodSort('period')">洪水过程 <i>{{ floodSortIndicator('period') }}</i></button></th>
                <th><button type="button" class="table-sort" :aria-sort="floodSortAria('peak')" @click="toggleFloodSort('peak')">Qmax <i>{{ floodSortIndicator('peak') }}</i></button></th>
                <th><button type="button" class="table-sort" :aria-sort="floodSortAria('peakTime')" @click="toggleFloodSort('peakTime')">峰现 <i>{{ floodSortIndicator('peakTime') }}</i></button></th>
                <th><button type="button" class="table-sort" :aria-sort="floodSortAria('duration')" @click="toggleFloodSort('duration')">历时 <i>{{ floodSortIndicator('duration') }}</i></button></th>
                <th><button type="button" class="table-sort" :aria-sort="floodSortAria('volume')" @click="toggleFloodSort('volume')">洪量 <i>{{ floodSortIndicator('volume') }}</i></button></th>
                <th><button type="button" class="table-sort" :aria-sort="floodSortAria('quality')" @click="toggleFloodSort('quality')">资料完整率 <i>{{ floodSortIndicator('quality') }}</i></button></th>
                <th>来源/版本</th><th>状态</th><th>操作</th>
              </tr></thead>
              <tbody>
                <tr v-for="event in pagedFloodEvents" :key="event.eventId || event.id" :class="{ 'row-inactive': event.status === 'inactive' }">
                  <td><b>{{ event.sourceFloodNo || event.eventId || event.id }}</b><small>{{ event.eventId || event.id }}</small></td>
                  <td>{{ eventTimeLabel(event.start) }}<br /><span class="table-subtle">至 {{ eventTimeLabel(event.end) }}</span></td>
                  <td class="peak-cell">{{ eventPeakLabel(event) }}</td>
                  <td>{{ eventTimeLabel(event.peakTime) }}</td>
                  <td>{{ event.durationHours != null ? `${event.durationHours} h` : '—' }}</td>
                  <td>{{ event.volumeM3 != null ? `${(Number(event.volumeM3) / 1000000).toFixed(2)} 百万m³` : '待计算' }}</td>
                  <td><span :class="['status-text', eventDataQuality(event)]">{{ eventCompletenessLabel(event) }} · {{ eventDataQuality(event) }}</span></td>
                  <td><span class="table-subtle">{{ event.source || '—' }}</span><br /><span class="table-subtle">r{{ event.revision || 1 }}</span></td>
                  <td><span :class="['tag', event.status === 'inactive' ? 'gray' : '']">{{ event.status === 'active' ? '启用' : event.status === 'draft' ? '草稿' : event.status === 'pending' ? '待核查' : '停用' }}</span></td>
                  <td class="flood-actions"><button v-if="!isModelInputWorkspace" class="link" type="button" @click="beginEditFloodEvent(event)">修改</button><button class="link" type="button" @click="viewFloodEventHistory(event)">{{ event.localDraft ? '原人工修订' : '变更记录' }}</button><button v-if="event.status !== 'inactive'" class="link danger" type="button" @click="setFloodEventStatus(event, 'inactive')">停用</button><button v-else class="link" type="button" @click="setFloodEventStatus(event, 'active')">恢复</button></td>
                </tr>
                <tr v-if="!pagedFloodEvents.length"><td colspan="10">没有符合条件的洪水场次</td></tr>
              </tbody>
            </table>
          </div>
          <div class="flood-ledger-pagination"><span>第 {{ floodEventPage }} / {{ floodEventPageCount }} 页，共 {{ filteredFloodEvents.length }} 场</span><div><button class="btn" type="button" :disabled="floodEventPage <= 1" @click="floodEventPage -= 1">上一页</button><button class="btn" type="button" :disabled="floodEventPage >= floodEventPageCount" @click="floodEventPage += 1">下一页</button></div></div>
          <div v-if="floodEventHistoryEvent" class="flood-history-panel">
            <div class="flood-editor-head"><b>{{ floodEventHistoryEvent.sourceFloodNo || floodEventHistoryEvent.eventId }} 变更记录</b><button class="link" type="button" @click="floodEventHistoryEvent = null">关闭</button></div>
            <div v-if="floodEventHistoryEvent.history?.length" class="flood-history-list"><div v-for="item in [...floodEventHistoryEvent.history].reverse()" :key="`${item.revision}-${item.changedAt}`"><span>r{{ item.revision }} · {{ item.changedAt?.replace('T', ' ').slice(0, 16) }}</span><b>{{ item.reason }}</b></div></div>
            <p v-else class="hint">暂无修改快照。</p>
            <p v-if="floodEventHistoryEvent.localDraft" class="hint">保留的人工窗口：{{ eventTimeLabel(floodEventHistoryEvent.localDraft.start) }} 至 {{ eventTimeLabel(floodEventHistoryEvent.localDraft.end) }}。登记窗口仍以整编资料目录为准。</p>
          </div>
        </template>

      </main>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useConfigStore } from '@/stores/config'
import { useForecastStore } from '@/stores/forecast'
import { useMonitorStore } from '@/stores/monitor'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { useCalibrationStore } from '@/stores/calibration'
import { PARAMETER_VERSION_CATALOG } from '@/fixtures/catalogs/parameter-versions'
import ContextStatusMetric from '@/components/common/ContextStatusMetric.vue'
import RoutingDefaults from '@/components/config/RoutingDefaults.vue'
import { loadNananxiEventPackages, loadNananxiEventStatistics, loadNananxiFloodEvents, loadNananxiObservationCatalog } from '@/services/historicalObservationService'
import { formatObservationCoverage, formatObservationDate } from '@/services/observationCoverageService'
import { eventDataQuality } from '@/services/floodEventService'
import { parameterLibraryFor } from '@/services/parameterLibraryService'
import { validateProjectDataContext } from '@/services/projectDataValidationService'
import { selectObservedHydrologyDataset, selectObservedPrecipitationDataset } from '@/services/datasetSelectionService'
import { loadJsonResource } from '@/services/projectDataService'
import { finiteInputValue, loadHistoricalDatabaseCatalog, usesModelInputService } from '@/services/modelInputService'
import { modelApi } from '@/services/modelBackendService'

const cfg = useConfigStore()
const forecast = useForecastStore()
const monitor = useMonitorStore()
const app = useAppStore()
const router = useRouter()
const calibration = useCalibrationStore()
const preparationDefaults = ref({ idwPower: 2, minimumStations: 1 })
const network = ref(null), networkError = ref(''), networkBusy = ref(false)
const stationDirectory = ref({stations:[],aliases:[]})
function stationDisplay(id) {
  const alias=stationDirectory.value.aliases.find(row=>String(row.alias)===String(id))
  const canonical=alias?.station_id || id
  return stationDirectory.value.stations.find(row=>String(row.id)===String(canonical))?.name || (String(id)==='70115800'?'徐畈（二）':'未命名站点（'+id+'）')
}
const networkPath = () => 'database/network'
async function loadNetwork() {
  const source = networkPath()
  networkError.value = ''; network.value = null
  try { const [row,directory] = await Promise.all([modelApi(source),modelApi('database/station-directory')]); stationDirectory.value=directory; if (source === networkPath()) network.value = row }
  catch (failure) { if (source === networkPath()) { networkError.value = failure.message; network.value = { stations: [], flowStation: '' } } }
}
async function saveNetwork() {
  networkBusy.value = true; networkError.value = ''
  try { network.value = await modelApi(networkPath(), { method: 'POST', body: network.value }); ElMessage.success('站网已保存') }
  catch (failure) { networkError.value = failure.message }
  finally { networkBusy.value = false }
}
async function savePreparationDefaults() {
  try { preparationDefaults.value = await modelApi('database/preparation-defaults', { method: 'POST', body: { idwPower: preparationDefaults.value.idwPower, minimumStations: preparationDefaults.value.minimumStations } }); ElMessage.success('默认处理规则已保存，下次新建方案时采用') }
  catch (failure) { ElMessage.error(failure.message) }
}
const tabs = ['流域与站网', '河网与断面', '模型方案', '参数库', '状态库']
const route = useRoute()
const tab = ref(route.query.tab==='routing'?'河网与断面':tabs[0])
const algorithmCatalog = ref([])
const projectObservationCatalog = ref(null)
const databaseStations = ref([])
const inputCatalogError = ref('')
let inputCatalogRequestId = 0
const floodEventQuery = ref('')
const floodEventStatus = ref('all')
const floodEventPage = ref(1)
const floodEventPageSize = 12
const floodSortKey = ref('period')
const floodSortDirection = ref('asc')
const floodEventEditId = ref(null)
const floodEventEditorOpen = ref(false)
const floodEventDraft = ref(createFloodEventDraft())
const floodEventHistoryEvent = ref(null)
const parameterLibrary = ref(null)
const parameterLibraryMode = ref('simulation')
const namedParameters = ref([]), namedParameterError = ref('')
async function loadNamedParameters() {
  try { namedParameters.value = ((await modelApi('parameter-schemes')).schemes || []).filter(row=>row.contextKey === app.contextKey); namedParameterError.value='' }
  catch(failure) { namedParameterError.value=failure.message }
}
async function renameNamedParameter(row) {
  try {
    const {value}=await ElMessageBox.prompt('填写参数名称','修改参数名称',{inputValue:row.name,inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80})
    await modelApi('parameter-schemes/'+row.id+'/rename',{method:'POST',body:{name:value.trim()}})
    await loadNamedParameters()
  } catch(failure) { if(failure instanceof Error) namedParameterError.value=failure.message }
}
const refreshingProjectData = ref(false)
const projectDataValidation = computed(() => validateProjectDataContext(app.projectData))
const isModelInputWorkspace = computed(() => usesModelInputService(app.activeContext))
const displayedAlgorithms = computed(() => isModelInputWorkspace.value ? app.backendCase?.algorithms || [] : algorithmCatalog.value)

function createFloodEventDraft(event = null) {
  return {
    name: event?.name || '',
    start: event?.start || '',
    end: event?.end || '',
    sectionId: event?.sectionId || '70115800',
    observedPeak: event?.observedPeak ?? '',
    peakTime: event?.peakTime || '',
    note: event?.note || ''
  }
}

async function loadProjectObservationCatalog({ refresh = false } = {}) {
  const requestId = ++inputCatalogRequestId
  const contextKey = app.contextKey
  const inputKey = app.inputSelectionKey
  inputCatalogError.value = ''
  projectObservationCatalog.value = null
  databaseStations.value = []
  if (app.basinId !== 'nananxi-xufan') {
    projectObservationCatalog.value = null
    return
  }
  try {
    if (isModelInputWorkspace.value) {
      const stationData = await modelApi('database/stations')
      if (requestId !== inputCatalogRequestId || contextKey !== app.contextKey) return
      databaseStations.value = stationData.stations
      const catalog = await loadHistoricalDatabaseCatalog({ source: app.activeContext })
      if (requestId !== inputCatalogRequestId || contextKey !== app.contextKey || inputKey !== app.inputSelectionKey) return
      projectObservationCatalog.value = catalog
      calibration.setObservationRange(catalog?.river)
      // The selected product supplies authoritative statistics, not the fixed
      // default case in /catalog and never an obsolete static event package.
      calibration.loadFullFloodCatalog(catalog.events)
      return
    }
    projectObservationCatalog.value = refresh || !monitor.historicalCatalog
      ? await loadNananxiObservationCatalog({ refresh, source: app.activeContext })
      : monitor.historicalCatalog
    calibration.setObservationRange(projectObservationCatalog.value?.river)
    const [eventCatalog, packages] = await Promise.all([
      loadNananxiFloodEvents({ refresh, source: app.activeContext }),
      loadNananxiEventPackages({ refresh, source: app.activeContext })
    ])
    if (requestId !== inputCatalogRequestId || contextKey !== app.contextKey) return
    calibration.syncEventCatalogEvents(eventCatalog.events || [])
    calibration.enrichEvents(packages.packages || [])
  } catch (error) {
    if (requestId === inputCatalogRequestId && inputKey === app.inputSelectionKey) { projectObservationCatalog.value = null; inputCatalogError.value = error.message }
  }
}

const activeDataset = computed(() => isModelInputWorkspace.value
  ? { hydrology: app.backendCase ? `徐畈 整编流量 · ${app.backendCase.observationVersion}` : '当前尺度未接入 整编流量', precipitation: app.backendCase ? `整编网格雨量 · ${app.backendCase.forcingVersion}` : '当前尺度未接入 整编网格雨量' }
  : app.hasLakeBalance
  ? { hydrology: '逐时入湖断面流量与湖位资料', precipitation: app.activeModelScheme.forcing?.name || '逐时降水资料' }
  : {
      hydrology: selectObservedHydrologyDataset(app.projectData)?.name || '出口水文资料未接入',
      precipitation: selectObservedPrecipitationDataset(app.projectData, app.modelScale)?.name || '当前尺度降雨场未接入'
    })

const configSectionOptions = computed(() => {
  if (app.hasLakeBalance) return [...new Set([calibration.section, '色林错全湖', '扎加藏布入湖口', '扎根藏布入湖口', '波曲藏布入湖口', '阿里藏布入湖口'].filter(Boolean))]
  const sections = app.projectData?.sections?.sections || []
  return sections.length ? [...new Set([calibration.section, ...sections.map((section) => section.name)].filter(Boolean))] : [calibration.section || app.basinName]
})

function formatCatalogDate(value) {
  return formatObservationDate(value)
}

const commonObservationRangeLabel = computed(() => {
  const catalog = observationCatalog.value
  return formatObservationCoverage(catalog)
})

const observationCatalog = computed(() => {
  if (isModelInputWorkspace.value) {
    if (app.modelScale !== '1km' || app.backendError) return null
    return projectObservationCatalog.value?.sourceType === 'database-observed' ? projectObservationCatalog.value
      : monitor.historicalCatalog?.sourceType === 'database-observed' ? monitor.historicalCatalog : null
  }
  return monitor.historicalCatalog?.sourceType === 'model-qc' ? projectObservationCatalog.value : monitor.historicalCatalog || projectObservationCatalog.value
})

function syncParameterLibrary() {
  parameterLibrary.value = parameterLibraryFor(app.projectData, app.basinId, app.modelScale, app.activeContext)
}

function observationRangeLabel(kind) {
  const range = observationCatalog.value?.[kind]
  if (!range?.start || !range?.end) return '待目录读取'
  return `${formatCatalogDate(range.start)} 至 ${formatCatalogDate(range.end)}`
}

function recordCountLabel(kind) {
  if (observationCatalog.value?.[kind]?.totalRecords == null) return '—'
  const count = Number(observationCatalog.value?.[kind]?.totalRecords)
  return Number.isFinite(count) ? `${count.toLocaleString()} 条` : '—'
}

onMounted(async () => {
  if(app.basinId === 'nananxi-xufan') await loadNamedParameters()
  if (app.backendCase) {
    try { preparationDefaults.value = await modelApi('database/preparation-defaults') } catch (failure) { ElMessage.warning(failure.message) }
    await loadNetwork()
  }
  await loadProjectObservationCatalog()
  try {
    const data = await loadJsonResource('/data/catalogs/algorithms.json', '算法目录')
    algorithmCatalog.value = data?.algorithms || []
  } catch {
    algorithmCatalog.value = []
  }
  syncParameterLibrary()
})

watch(() => [app.inputSelectionKey, app.backendError], () => loadProjectObservationCatalog())
watch(() => app.basinId, syncParameterLibrary)
watch(() => app.projectData, syncParameterLibrary)
watch(() => app.modelScale, syncParameterLibrary)
watch(() => [app.contextKey, app.projectData?.backendParameterVersions], syncParameterLibrary)
watch(() => [app.basinId, app.modelScale, app.modelConfigId], ([basinId, modelScale, modelConfigId]) => {
  calibration.setContext(basinId, modelScale, modelConfigId)
}, { immediate: true })

const paramRows = computed(() => PARAMETER_VERSION_CATALOG
  .filter((row) => row.basinProjectId === app.basinId)
  .filter((row) => row.resolutionId === app.modelScale)
  .map((row) => ({ ver: row.id, date: row.publishedAt, section: row.section, nse: row.nse, by: row.approvedBy })))

const activeConfig = computed(() => app.hasLakeBalance ? {
  name: `色林错 ${app.activeModelScheme.label}`,
  version: app.activeModelScheme.paramVersion,
  grid: `${app.activeScaleMeta.label} 网格`,
  gridCount: app.activeScaleMeta.cellCount,
  subbasins: 4,
  dem: `${app.activeScaleMeta.label} DEM`,
  sql: `selinco_hydro_hourly_2021_2025_${app.modelScale}`,
  nc: app.activeModelScheme.forcing?.name,
  ncPattern: `selinco_forcing_{issue}_${app.modelScale}.nc`,
  hillslope: '线性水库坡面汇流',
  gridToRiver: '沿地形汇流路径汇入河道',
  channel: 'Strahler 分级河网 + 入湖口汇集',
  status: '试算目录',
  runoffMethod: '分布式新安江蓄满产流',
  separation: '自由水蓄水库三水源划分'
} : {
  name: `${app.basinName} · 分布式新安江模型`,
  version: app.backendCase?.parameterVersionId || app.activeModelScheme.paramVersion,
  grid: `${app.activeScaleMeta.label} 栅格`,
  gridCount: app.projectData?.spatialMetadata?.terrain?.[app.modelScale]?.cellCount || 0,
  subbasins: app.projectData?.spatialMetadata?.featureCounts?.basin || 1,
  dem: app.projectData?.spatialMetadata?.terrain?.[app.modelScale]?.displayResolution || `${app.activeScaleMeta.label} DEM`,
  sql: selectObservedHydrologyDataset(app.projectData)?.name || '待接入出口水文资料目录',
  nc: selectObservedPrecipitationDataset(app.projectData, app.modelScale)?.name || '待接入当前尺度降雨场目录',
  ncPattern: 'forcing_{issue}_{source}_1km.nc',
  hillslope: app.backendCase ? '汇流单元三分量线性水库' : '离线目录 · 未确认执行方案',
  gridToRiver: app.backendCase ? '固定面积权重 · 控制断面切分 v2' : '离线目录 · 未确认映射',
  channel: app.backendCase ? '30 m 地形提取河网 · 河段 Muskingum 汇流' : '离线目录 · 未确认执行方案',
  status: app.backendCase ? '模型服务可用' : app.dataStatus,
  runoffMethod: app.backendCase ? '共享新安江液态降雨网格内核' : '分布式新安江目录',
  separation: '自由水蓄水库三水源划分'
})

const nananxiStationRows = computed(() => {
  if (app.basinId !== 'nananxi-xufan') return []
  const spatialStations = app.projectData?.stations?.stations || []
  const catalog = observationCatalog.value
  const rainStations = catalog?.rain?.stations || []
  const rainBySpatialCode = new Map(rainStations.map((station) => [String(station.spatialCode), station]))
  const riverStation = catalog?.river?.station
  const riverRecords = Number(catalog?.river?.totalRecords || 0)

  return spatialStations.map((station) => {
    if (isModelInputWorkspace.value) {
      const rows = databaseStations.value.filter(row => row.id === String(station.code || station.id))
      const labels = { rain: '雨量', level: '水位', flow: '流量' }
      return { code: station.code || station.id, name: station.name, type: station.typeLabel || station.type,
        data: rows.length ? rows.map(row => `${labels[row.variable]} ${Number(row.valid).toLocaleString()} 条`).join('；') : '空间站点登记',
        use: rows.some(row => row.variable === 'flow') ? '出口过程检验 / 站点降雨' : rows.length ? '降雨空间化' : '站网定位',
        ready: rows.some(row => row.valid > 0), hasRain: rows.some(row => row.variable === 'rain' && row.valid > 0) }
    }
    const rain = rainBySpatialCode.get(String(station.id || station.code))
    const rainRecords = Number(rain?.records || 0)
    const isOutlet = String(riverStation?.spatialCode) === String(station.id || station.code)
    const data = []
    const uses = []
    if (rainRecords > 0) {
      data.push(`雨量 ${rainRecords.toLocaleString()} 条`)
      uses.push('降雨空间化')
    }
    if (isOutlet && riverRecords > 0) {
      data.push(`${isModelInputWorkspace.value ? '整编流量' : '原始水位流量'} ${riverRecords.toLocaleString()} 条`)
      uses.push('出口过程校验')
    }
    return {
      code: station.code || station.id,
      name: station.name,
      type: station.typeLabel || station.type,
      data: data.length ? data.join('；') : '空间站点登记',
      use: uses.length ? uses.join('、') : '站网定位',
      ready: data.length > 0,
      hasRain: rainRecords > 0
    }
  })
})

const nananxiRainStationCount = computed(() => nananxiStationRows.value.filter((station) => station.hasRain).length)
const sectionOptions = computed(() => {
  const sections = app.projectData?.sections?.sections || []
  if (sections.length) return sections.map((section) => ({ id: section.id, name: section.name }))
  return [{ id: '70115800', name: '徐畈出口' }]
})

const floodEvents = computed(() => calibration.events)
const filteredFloodEvents = computed(() => {
  const query = floodEventQuery.value.trim().toLowerCase()
  return floodEvents.value.filter((event) => {
    const matchesStatus = floodEventStatus.value === 'all' || event.status === floodEventStatus.value
    const text = `${event.sourceFloodNo || ''} ${event.eventId || event.id} ${event.name || ''} ${event.start || ''}`.toLowerCase()
    return matchesStatus && (!query || text.includes(query))
  })
})
const sortedFloodEvents = computed(() => {
  const rows = [...filteredFloodEvents.value]
  const direction = floodSortDirection.value === 'desc' ? -1 : 1
  const valueFor = (event) => {
    if (floodSortKey.value === 'floodNo') return String(event.sourceFloodNo || event.eventId || event.id || '')
    if (floodSortKey.value === 'peak') {
      const value = finiteInputValue(event.observedPeak)
      return value != null ? { missing: false, value } : { missing: true, value: 0 }
    }
    if (floodSortKey.value === 'peakTime') return { missing: !event.peakTime, value: new Date(event.peakTime || 0).getTime() || 0 }
    if (floodSortKey.value === 'duration') {
      const value = finiteInputValue(event.durationHours)
      return value != null ? { missing: false, value } : { missing: true, value: 0 }
    }
    if (floodSortKey.value === 'volume') {
      const value = finiteInputValue(event.volumeM3)
      return value != null ? { missing: false, value } : { missing: true, value: 0 }
    }
    if (floodSortKey.value === 'quality') {
      const rain = Number(event.rainCompleteness)
      const flow = Number(event.flowCompleteness)
      const missing = !Number.isFinite(rain) && !Number.isFinite(flow)
      return { missing, value: (Number.isFinite(rain) ? rain : 0) + (Number.isFinite(flow) ? flow : 0) }
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
const floodEventPageCount = computed(() => Math.max(1, Math.ceil(filteredFloodEvents.value.length / floodEventPageSize)))
const pagedFloodEvents = computed(() => sortedFloodEvents.value.slice((floodEventPage.value - 1) * floodEventPageSize, floodEventPage.value * floodEventPageSize))
const floodEventSummary = computed(() => {
  const active = floodEvents.value.filter((event) => event.status === 'active')
  const peaks = active.map((event) => finiteInputValue(event.observedPeak)).filter((value) => value != null)
  return {
    total: floodEvents.value.length,
    active: active.length,
    draft: floodEvents.value.filter((event) => event.status === 'draft').length,
    pending: floodEvents.value.filter((event) => event.status === 'pending').length,
    inactive: floodEvents.value.filter((event) => event.status === 'inactive').length,
    maxPeak: peaks.length ? Math.max(...peaks) : null,
    missingPeak: active.filter((event) => finiteInputValue(event.observedPeak) == null).length
  }
})

watch([floodEventQuery, floodEventStatus], () => { floodEventPage.value = 1 })
watch(floodEventPageCount, (count) => { floodEventPage.value = Math.min(floodEventPage.value, count) })

function eventTimeLabel(value) {
  return value ? String(value).replace('T', ' ') : '—'
}

function eventPeakLabel(event) {
  return event.observedPeak != null && event.observedPeak !== '' && Number.isFinite(Number(event.observedPeak)) ? `${Number(event.observedPeak).toLocaleString()} m³/s` : '待计算'
}

function eventCompletenessLabel(event) {
  const values = [event.rainCompleteness, event.flowCompleteness].filter((value) => value != null).map((value) => `${Math.round(Number(value) * 100)}%`)
  return values.length ? values.join(' / ') : '待检查'
}

function toggleFloodSort(key) {
  if (floodSortKey.value === key) floodSortDirection.value = floodSortDirection.value === 'asc' ? 'desc' : 'asc'
  else {
    floodSortKey.value = key
    floodSortDirection.value = key === 'peak' ? 'desc' : 'asc'
  }
  floodEventPage.value = 1
}

function floodSortIndicator(key) {
  return floodSortKey.value === key ? (floodSortDirection.value === 'asc' ? '↑' : '↓') : '↕'
}

function floodSortAria(key) {
  if (floodSortKey.value !== key) return 'none'
  return floodSortDirection.value === 'asc' ? 'ascending' : 'descending'
}

function beginCreateFloodEvent() {
  floodEventEditId.value = null
  floodEventEditorOpen.value = true
  floodEventDraft.value = createFloodEventDraft()
}

function beginEditFloodEvent(event) {
  floodEventEditId.value = event.id
  floodEventEditorOpen.value = true
  floodEventDraft.value = createFloodEventDraft(event)
}

function viewFloodEventHistory(event) {
  floodEventHistoryEvent.value = event
}

function cancelFloodEventEdit() {
  floodEventEditId.value = null
  floodEventEditorOpen.value = false
  floodEventDraft.value = createFloodEventDraft()
}

async function saveFloodEvent() {
  if (isModelInputWorkspace.value) return ElMessage.warning('整编场次目录为只读。')
  const payload = { ...floodEventDraft.value }
  if (app.basinId === 'nananxi-xufan') {
    try {
      ElMessage.info('正在读取出口水文资料并计算Qmax、峰现时间和洪量…')
      payload.derivedStats = await loadNananxiEventStatistics(payload.start, payload.end, { source: app.activeContext })
    } catch (error) {
      ElMessage.warning(error instanceof Error ? error.message : '出口水文资料读取失败，场次将保留为待核查。')
    }
  }
  delete payload.observedPeak
  delete payload.peakTime
  const result = floodEventEditId.value
    ? calibration.updateEvent(floodEventEditId.value, payload)
    : calibration.addEvent(payload)
  if (!result?.ok) {
    ElMessage.warning(result?.error || '洪水场次资料不完整')
    return
  }
  if (result.warnings?.length) result.warnings.forEach((warning) => ElMessage.warning(warning))
  ElMessage.success(floodEventEditId.value ? '洪水场次已更新，统计值已重算' : '已新增洪水场次，统计值已生成')
  cancelFloodEventEdit()
}

function setFloodEventStatus(event, status) {
  if (calibration.setEventStatus(event.id, status)) {
    ElMessage.success(status === 'inactive' ? '洪水场次已停用，原记录保留' : '洪水场次已恢复启用')
  }
}

async function refreshProjectData() {
  if (refreshingProjectData.value || app.basinId !== 'nananxi-xufan') return
  refreshingProjectData.value = true
  try {
    if (isModelInputWorkspace.value) await app.refreshBackend()
    await loadProjectObservationCatalog({ refresh: true })
    if (inputCatalogError.value) throw new Error(inputCatalogError.value)
    await monitor.loadLatestHistoricalWindow(72, { refresh: true })
    ElMessage.success('资料目录已更新，最新共同覆盖时段已载入')
  } catch (error) {
    ElMessage.warning(error instanceof Error ? error.message : '资料目录更新失败')
  } finally {
    refreshingProjectData.value = false
  }
}

function baseProd(v) {
  return String(v || '').replace(/-tune.*$/, '')
}

function isProd(ver) {
  const p = cfg.productionParamVersion || ''
  return ver === p || ver === baseProd(p) || (p.includes('-tune') && baseProd(p) === ver && ver === 'XAJ-ZJ-v3.2')
}

function save() {
  cfg.save()
  monitor.recomputeArealFromConfig()
  ElMessage.success('配置已保存，模型方案与业务规则已更新')
}

async function loadVer(ver) {
  if (isModelInputWorkspace.value) {
    const version = parameterLibrary.value?.simulationRateLibrary?.versions?.find((row) => row.id === ver)
    if (!app.backendCase || app.backendError || !version?.parameters) return ElMessage.warning('当前真实参数或模型服务不可用，不能用示意参数替代。')
    if (await calibration.loadVersion(ver) === false) return ElMessage.warning(calibration.runMessage)
    calibration.setOperationMode('simulation')
    router.push('/calibration')
    ElMessage.success(`已载入诊断参数 ${ver} 到连续模拟`)
    return
  }
  forecast.loadParamVersion(ver)
  ElMessage.success(`已载入参数 ${ver} 到预报台`)
}

function onWeight(code, v) {
  cfg.setWeight(code, v)
  monitor.recomputeArealFromConfig()
}

function onToggleUse(code) {
  cfg.toggleUse(code)
  monitor.recomputeArealFromConfig()
}
</script>

<style scoped>
.view-config {
  display: grid;
  grid-template-rows: 46px minmax(0, 1fr);
  gap: 10px;
  height: 100%;
  min-height: 0;
}
.config-metrics { grid-auto-flow: initial; grid-template-columns: minmax(320px, 1.55fr) repeat(4, minmax(150px, 1fr)); }
.config-metrics .metric { white-space: nowrap; }
.panel-title-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.panel-title-row .panel-title { margin: 0; }

.body {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 10px;
  min-height: 0;
}
.body > main { overflow: auto; }
.body > main .table { width: 100%; min-width: 640px; white-space: nowrap; }
.pipeline-wide {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: stretch;
  gap: 8px;
  margin: 18px 0;
}
.pipeline-wide > div {
  display: grid;
  gap: 5px;
  min-height: 92px;
  padding: 12px;
  border: 1px solid #cfe0f5;
  border-radius: 6px;
  background: #f8fbff;
}
.pipeline-wide > div span { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: #0b57c7; color: #fff; font-size: 11px; font-weight: 900; }
.pipeline-wide > div b { color: #173052; font-size: 13px; }
.pipeline-wide > div small { color: #6a7c95; font-size: 11px; line-height: 1.4; }
.pipeline-wide > i { align-self: center; color: #8aa0bb; font-style: normal; }
.config-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 18px 0; max-width: 900px; }
.status-table { max-width: 900px; }
.data-inventory { max-width: none; }
.station-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 14px 0 10px;
}
.station-summary span {
  min-width: 150px;
  padding: 8px 10px;
  border-left: 3px solid #168276;
  background: #f2f8f7;
  color: #536b74;
  font-size: 12px;
}
.station-summary b { margin-right: 3px; color: #126d66; font-size: 15px; }
.station-table td { height: 42px; }
.station-status { font-weight: 700; }
.station-status.ready { color: #14776e; }
.station-status.missing { color: #a85a17; }
.preparation-pipeline > div { border-color: #c8ded9; background: #f6fbf9; }
.preparation-pipeline > div span { background: #147d73; }
.readiness-cards {
  display: grid;
  grid-template-columns: repeat(5, minmax(150px, 1fr));
  gap: 8px;
  margin-top: 14px;
}
.readiness-cards > div {
  display: grid;
  align-content: start;
  gap: 5px;
  min-height: 104px;
  padding: 11px 12px;
  border: 1px solid #d7e2e8;
  border-top-width: 3px;
  background: #fbfdfe;
}
.readiness-cards span { color: #607580; font-size: 10px; font-weight: 800; }
.readiness-cards b { color: #254450; font-size: 13px; }
.readiness-cards small { color: #718590; font-size: 10px; line-height: 1.45; }
.readiness-cards .ready { border-top-color: #168276; }
.readiness-cards .ready b { color: #14776e; }
.readiness-cards .missing { border-top-color: #d07a28; }
.readiness-cards .missing b { color: #a85a17; }
.readiness-cards .pending { border-top-color: #7b91a0; }
.data-contract-card { display: flex; justify-content: space-between; gap: 10px; margin-top: 10px; padding: 9px 10px; border: 1px solid #d5e3df; border-left: 3px solid #2c8275; background: #f6fbf9; color: #4b6969; font-size: 10px; }
.data-contract-card div { display: grid; gap: 3px; }
.data-contract-card b { color: #1d5753; font-size: 11px; }
.data-contract-card strong { align-self: center; color: #26766c; white-space: nowrap; }
.input-summary { display: grid; gap: 7px; max-width: 820px; margin: 16px 0; padding: 16px; border: 1px solid #d5e3df; background: #f8fbfa; color: #526e76; font-size: 12px; overflow-wrap: anywhere; }
.input-summary>b { color: #20584f; font-size: 15px; }
.input-summary small { color: #71868c; line-height: 1.6; }
.input-summary dl { display: grid; grid-template-columns: minmax(120px, .7fr) minmax(0, 1.3fr); gap: 10px; margin: 10px 0; }
.input-summary dt { color: #6b7f84; }
.input-summary dd { margin: 0; color: #2e575c; }
.input-summary .btn { justify-self: start; text-decoration: none; }
.tag.neutral { background: #edf3f5; color: #536d78; }
.business-defaults { margin-top: 18px; }
.scheme-summary { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 18px 0; }
.scheme-summary > div { display: grid; gap: 5px; padding: 14px; border: 1px solid #cfe0f5; border-radius: 6px; background: #f8fbff; }
.scheme-summary span { color: #6a7c95; font-size: 11px; }
.scheme-summary b { color: #173052; font-size: 14px; }
.scheme-summary small { color: #0b57c7; font-size: 11px; }
.scheme-note { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 18px; padding: 12px 14px; border-left: 3px solid #0b57c7; background: #f2f7fd; color: #52647d; font-size: 12px; }
.scheme-note b { color: #173052; }
.data-object-note { display: flex; align-items: baseline; gap: 12px; margin-top: 8px; padding: 11px 13px; border-left: 3px solid #2a8178; background: #f5faf9; color: #587178; font-size: 12px; line-height: 1.5; }
.data-object-note b { flex: 0 0 auto; color: #1c5754; }
.data-object-status { display: flex; flex-wrap: wrap; align-items: center; gap: 7px 12px; margin-top: 12px; color: #637a80; font-size: 11px; }
.data-object-status b { padding: 4px 8px; border: 1px solid #cde0dd; background: #f6fbfa; color: #1e6661; font-variant-numeric: tabular-nums; }
.data-object-saved { margin-left: auto; color: #2d8176; font-size: 10px; }
.parameter-library-switch { display: inline-flex; gap: 3px; margin: 12px 0 8px; padding: 3px; border: 1px solid #cbdcdd; border-radius: 5px; background: #f1f7f6; }
.parameter-library-switch button { min-height: 30px; padding: 0 10px; border: 0; border-radius: 3px; background: transparent; color: #56727a; font-size: 11px; font-weight: 800; cursor: pointer; }
.parameter-library-switch button.active { background: #fff; color: #14776e; box-shadow: 0 1px 3px rgba(24, 76, 78, .12); }
.parameter-library-note { display: flex; align-items: center; gap: 10px; margin: 4px 0 10px; padding: 9px 11px; border-left: 3px solid #4a8f87; background: #f2faf8; color: #547078; font-size: 11px; }
.parameter-library-note b { color: #1c5357; }
.parameter-library-note span { flex: 1; }
.parameter-library-note strong { color: #a56120; font-size: 10px; white-space: nowrap; }
.parameter-library-note.production { border-left-color: #a57b3a; background: #fffaf0; }
.parameter-library-table { min-width: 640px !important; }

.nav {
  display: grid;
  gap: 6px;
  align-content: start;
}

.nav-item {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: #34445e;
  text-align: left;
  font-weight: 700;
  cursor: pointer;
}

.nav-item:hover {
  background: #edf5ff;
  border-color: #cfe0f5;
}

.nav-item.active {
  background: linear-gradient(180deg, #0a62dc, #064fb7);
  color: #fff;
}
.toggle-cell { display: inline-flex; align-items: center; gap: 7px; color: #24526b; font-weight: 700; cursor: pointer; }
.toggle-cell input { width: 16px; height: 16px; accent-color: var(--teal); }

.hint {
  margin: 10px 0 0;
  color: #5a6d88;
  font-size: 12px;
  line-height: 1.5;
}

.hint.inline {
  margin: 6px 0 0;
}

.cell-input {
  width: 88px;
  height: 30px;
  padding: 0 8px;
  border: 1px solid #c8d8ec;
  border-radius: 6px;
  text-align: right;
  font-weight: 700;
  color: #0b57c7;
}

.cell-input.sm {
  width: 64px;
}

.narrow {
  max-width: 460px;
}

.tag.gray {
  background: #eef2f7;
  color: #6a7c95;
}

.link {
  border: 0;
  background: none;
  color: var(--blue);
  cursor: pointer;
  font-weight: 700;
}

.ok {
  color: var(--green);
}
.warn {
  color: var(--orange);
}
.bad {
  color: var(--red);
}
.hot {
  color: var(--orange);
}

.btn.block {
  width: 100%;
}

.flood-ledger-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(110px, 1fr));
  gap: 7px;
  margin: 14px 0 10px;
}
.flood-ledger-summary > div {
  display: grid;
  gap: 3px;
  min-height: 54px;
  padding: 8px 10px;
  border: 1px solid #d5e2e6;
  border-top: 3px solid #2a8178;
  background: #f8fbfb;
}
.flood-ledger-summary span { color: #6b7f86; font-size: 10px; }
.flood-ledger-summary b { color: #1b4d55; font-size: 16px; font-variant-numeric: tabular-nums; }
.flood-ledger-toolbar { display: grid; grid-template-columns: minmax(210px, 1fr) 130px auto; gap: 8px; align-items: end; margin: 10px 0; }
.flood-ledger-toolbar label { display: grid; gap: 4px; color: #64777d; font-size: 10px; }
.flood-ledger-toolbar .btn { min-height: 32px; }
.flood-event-editor { margin: 10px 0 12px; padding: 10px 12px; border: 1px solid #cfe1e2; border-left: 3px solid #2a8178; background: #f6fbfa; }
.flood-editor-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: #1d4b53; font-size: 12px; }
.flood-editor-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 10px 0; max-width: none; }
.flood-editor-grid .field { margin: 0; }
.derived-field { background: #f4f8f8; color: #4f6c72; cursor: not-allowed; }
.flood-editor-grid .field-wide { grid-column: 1 / -1; }
.flood-editor-actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; }
.flood-editor-actions > span { flex: 1; color: #678087; font-size: 10px; }
.table-scroll { overflow-x: auto; }
.flood-ledger-table-wrap { border: 1px solid #dce6e8; }
.flood-ledger-table { min-width: 1020px !important; margin: 0; }
.flood-ledger-table th { padding: 0; }
.table-sort { display: inline-flex; align-items: center; justify-content: space-between; gap: 4px; width: 100%; min-height: 34px; padding: 0 8px; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.table-sort:hover { background: #e5efee; color: #164e57; }
.table-sort i { color: #6d8388; font-size: 11px; font-style: normal; font-weight: 900; }
.table-sort:focus-visible { outline: 2px solid #178276; outline-offset: -2px; }
.flood-ledger-table td { height: 47px; vertical-align: middle; }
.flood-ledger-table td:first-child { display: grid; align-content: center; gap: 2px; }
.flood-ledger-table td:first-child small { color: #78909a; font-size: 9px; }
.flood-ledger-table .table-subtle { color: #70858c; font-size: 10px; }
.flood-ledger-table .peak-cell { color: #b35d1e; font-weight: 800; font-variant-numeric: tabular-nums; }
.status-text { font-size: 10px; white-space: nowrap; }
.status-text.完整 { color: #168276; }
.status-text.有缺测 { color: #b36a1d; }
.status-text.缺资料 { color: #a64d4d; }
.status-text.待检查 { color: #6c818a; }
.flood-ledger-table .row-inactive { opacity: .6; background: #f5f7f8; }
.flood-history-panel { display: grid; gap: 7px; margin-top: 10px; padding: 10px; border: 1px solid #d9e5e6; background: #f8fbfb; }
.flood-history-list { display: grid; gap: 5px; }
.flood-history-list div { display: flex; justify-content: space-between; gap: 10px; padding: 6px 8px; background: #fff; color: #587076; font-size: 10px; }
.flood-actions { display: flex; gap: 7px; white-space: nowrap; }
.flood-actions .danger { color: #ae5a43; }
.flood-ledger-pagination { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; color: #667d84; font-size: 10px; }
.flood-ledger-pagination > div { display: flex; gap: 6px; }
.flood-ledger-pagination .btn { min-height: 27px; padding: 0 9px; }

@media (max-width: 900px) {
  .view-config { height: auto; min-height: 100%; grid-template-rows: auto 1fr; }
  .config-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .body { grid-template-columns: 1fr; }
  .nav { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .nav .panel-title, .nav .btn, .nav .hint { grid-column: 1 / -1; }
  .body > main { overflow-x: auto; overflow-y: visible; }
  .pipeline-wide { grid-template-columns: 1fr; }
  .pipeline-wide > i { display: none; }
  .readiness-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .flood-ledger-summary { grid-template-columns: repeat(3, minmax(110px, 1fr)); }
  .flood-ledger-toolbar { grid-template-columns: 1fr 120px; }
  .flood-ledger-toolbar .btn { grid-column: 1 / -1; }
  .flood-editor-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 600px) {
  .config-metrics { grid-template-columns: 1fr; }
  .config-metrics .metric { white-space: normal; }
  .nav, .config-grid, .scheme-summary { grid-template-columns: 1fr; }
  .readiness-cards { grid-template-columns: 1fr; }
  .flood-ledger-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .flood-editor-grid { grid-template-columns: 1fr; }
  .flood-editor-grid .field-wide { grid-column: auto; }
  .flood-editor-actions { align-items: flex-start; flex-wrap: wrap; }
  .flood-editor-actions > span { flex-basis: 100%; }
  .panel-title-row { align-items: flex-start; flex-direction: column; }
  .data-object-note { align-items: flex-start; flex-direction: column; gap: 4px; }
}
.view-config :is(label,p,small,summary,td,th,.hint,.nav button,.field label,.status-text,.config-grid span){font-size:14px}
.view-config :is(.btn,.input,.nav button){min-height:36px;font-size:14px}
</style>
