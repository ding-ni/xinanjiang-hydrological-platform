<template>
  <header class="topbar">
    <div class="brand">
      <div class="logo" aria-hidden="true">
        <Pouring />
      </div>
      <div class="titles">
        <div class="sys">流域水文预报工作台</div>
        <div class="sub">分布式水文预报平台</div>
      </div>
    </div>

    <div class="view-title">{{ title }}</div>

    <div class="meta">
      <div class="chip basin-chip">
        <span>流域</span>
        <select class="meta-select" :value="app.basinId" @change="app.setBasin($event.target.value)">
          <option v-for="basin in basins" :key="basin.id" :value="basin.id">{{ basin.name }}</option>
        </select>
      </div>
      <div class="chip scale-chip">
        <span>空间分辨率</span>
        <select class="meta-select" :value="app.modelConfigId" @change="app.setModelConfig($event.target.value)">
          <option v-for="config in modelConfigs" :key="config.id" :value="config.id">{{ config.label }}</option>
        </select>
      </div>
      <div class="chip project-chip" :title="app.activeContext.projectName"><span>模型</span><b>{{ app.activeContext.projectName }}</b></div>
      <div v-if="route.name === 'realtime'" class="chip clock-chip"><span>回放时刻</span><b>{{ clockValue }}</b></div>
      <div class="chip status" :class="`status-${inputStatus.status}`">
        <span>资料状态</span>
        <b>{{ inputStatus.label }}</b>
        <i class="dot" />
      </div>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { Pouring } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useMonitorStore } from '@/stores/monitor'
import { useForecastStore } from '@/stores/forecast'
import { listBasinConfigs } from '@/data/domainContext'
import { usesModelInputService } from '@/services/modelInputService'
import { modelInputDisplayStatus } from '@/services/modelInputPresentation'

const route = useRoute()
const app = useAppStore()
const monitor = useMonitorStore()
const forecast = useForecastStore()
const clockValue = computed(() => route.name === 'realtime'
  ? usesModelInputService(app.activeContext) && !monitor.hasHistoricalObservations ? '—' : monitor.currentTimeLabel || '—'
  : route.name === 'forecast' ? forecast.startTime || '—' : app.dataTime || '—')
const basins = computed(() => app.availableBasins)
const modelConfigs = computed(() => listBasinConfigs(app.basinId, app.projectId))
const inputStatus = computed(() => usesModelInputService(app.activeContext)
  ? modelInputDisplayStatus({ resolution: app.modelScale, registered: app.hasModelInput,
    bundleVersionId: 'HISTORICAL_DATABASE',
    loaded: monitor.hasHistoricalObservations, loading: monitor.historicalLoading,
    backendError: app.backendError, inputError: monitor.historicalError || app.inputCatalogError })
  : { label: app.dataStatus, status: app.availabilityStatus })

const titleMap = {
  data: '水文资料库',
  diagnostics: '进阶诊断',
  realtime: '雨水情',
  forecast: '洪水预报',
  state: '过程与状态',
  calibration: '参数率定',
  simulation: '洪水模拟',
  archive: '计算成果',
  config: '模型与资料',
  system: '工作区与系统',
  advancedState: '独立状态初始化'
}

const title = computed(() => titleMap[route.name] || '业务平台')
</script>

<style scoped>
.topbar {
  display: grid;
  grid-template-columns: minmax(250px, 294px) minmax(130px, 160px) minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  height: var(--topbar);
  padding: 0 12px;
  background: #ffffff;
  border-bottom: 1px solid var(--line);
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.8) inset;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.logo {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 6px;
  background: #edf4ee;
  color: #287156;
}
.logo svg { width: 26px; height: 26px; }

.titles {
  min-width: 0;
}

.sys {
  color: #17343e;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sub {
  margin-top: 2px;
  color: #6a7c95;
  font-size: 10px;
  font-weight: 600;
}

.view-title {
  color: #17343e;
  font-size: 16px;
  font-weight: 800;
  text-align: center;
  letter-spacing: 0;
  white-space: nowrap;
}

.meta {
  display: flex;
  flex-wrap: nowrap;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  min-height: 29px;
  padding: 0 7px;
  border: 1px solid #dce4dc;
  border-radius: 5px;
  background: #fff;
  color: #526957;
  font-size: 11px;
  white-space: nowrap;
}

.chip span {
  color: #7f8b7e;
  font-weight: 600;
}

.chip b {
  color: #334e3d;
  font-weight: 700;
}

.chip.status .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 0 3px rgba(20, 131, 62, 0.15);
}
.chip.status-spatial-only .dot {
  background: #8a97a8;
  box-shadow: 0 0 0 3px rgba(111, 126, 145, 0.16);
}
.chip.status-frontend-fixture .dot {
  background: #52718f;
  box-shadow: 0 0 0 3px rgba(82, 113, 143, 0.16);
}
.chip.status-input-loading .dot,
.chip.status-qc-registered .dot {
  background: #c58b19;
  box-shadow: 0 0 0 3px rgba(197, 139, 25, 0.15);
}

.meta-select {
  border: 0;
  background: transparent;
  color: #173052;
  font-weight: 700;
  font-size: 12px;
  outline: none;
  cursor: pointer;
}
.basin-chip .meta-select { width: 116px; }
.scale-chip .meta-select { width: 72px; }
.project-chip { min-width: 0; max-width: 210px; flex: 1 1 160px; }
.project-chip b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 1760px) {
  .project-chip {
    display: none;
  }
}

@media (max-width: 1200px) {
  .topbar {
    grid-template-columns: 1fr auto;
  }
  .view-title {
    display: none;
  }
  .clock-chip span {
    display: none;
  }
}

@media (max-width: 1100px) {
  .topbar {
    grid-template-columns: 1fr;
    height: auto;
    min-height: var(--topbar);
    padding: 10px 14px;
  }
  .meta {
    justify-content: flex-start;
    flex-wrap: wrap;
    overflow: visible;
  }
  .clock-chip, .project-chip { display: none; }
  .chip { flex: 0 1 auto; }
  .status { border: 0; margin-left: auto; padding-right: 4px; }
  .status > span { display: none; }
}
</style>
