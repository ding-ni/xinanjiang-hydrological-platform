<template>
  <nav class="sidebar">
    <div class="group-label">业务流程</div>
    <RouterLink
      v-for="item in mainNav"
      :key="item.path"
      :to="item.path"
      class="nav-item"
      :class="{ active: route.path === item.path }"
    >
      <component :is="item.icon" class="icon" aria-hidden="true" />
      <span class="label">{{ item.label }}</span>
    </RouterLink>
  </nav>
</template>

<script setup>
import { useRoute } from 'vue-router'
import { DataAnalysis, FolderOpened, TrendCharts, Timer, SetUp, Operation, Coin } from '@element-plus/icons-vue'

const route = useRoute()

const mainNav = [
  { path: '/realtime', name: 'realtime', label: '雨水情', icon: DataAnalysis },
  { path: '/data', name: 'data', label: '水文资料库', icon: Coin },
  { path: '/calibration', name: 'calibration', label: '率定与模拟', icon: TrendCharts },
  { path: '/state', name: 'state', label: '模拟状态', icon: Timer },
  { path: '/forecast', name: 'forecast', label: '洪水预报', icon: TrendCharts },
  { path: '/diagnostics', name: 'diagnostics', label: '模型诊断', icon: Operation },
  { path: '/archive', name: 'archive', label: '成果管理', icon: FolderOpened },
  { path: '/config', name: 'config', label: '模型与资料', icon: SetUp },
  { path: '/system', name: 'system', label: '工作区与系统', icon: SetUp }
]
</script>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 10px;
  background: #f3f6f6;
  border-right: 1px solid #dce4e5;
  overflow: auto;
}
.group-label {
  margin: 10px 8px 4px;
  color: #7c8b90;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
}
.group-label:first-child {
  margin-top: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 0 12px;
  border-radius: 5px;
  color: #51676e;
  text-decoration: none;
  font-size: 14px;
  font-weight: 700;
}
.nav-item:hover {
  background: #e8eeee;
  color: #214e45;
}
.nav-item.active {
  background: #e2eeea;
  color: #1b6252;
  box-shadow: inset 3px 0 0 #357e68;
}

@media (max-width: 1100px) {
  .sidebar {
    flex-direction: row;
    gap: 6px;
    padding: 8px 10px;
    overflow-x: auto;
    border-right: 0;
    border-bottom: 1px solid #0a2c38;
  }
  .group-label {
    display: none;
  }
  .nav-item {
    flex: 0 0 auto;
    min-height: 42px;
  }
}
.icon {
  display: inline-flex;
  width: 18px;
  height: 18px;
}
.icon :deep(svg) {
  width: 18px;
  height: 18px;
}
</style>
