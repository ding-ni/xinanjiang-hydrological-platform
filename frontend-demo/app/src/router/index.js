import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { title: '工作区与系统' } },
  { path: '/', redirect: '/realtime' },
  { path: '/data', name: 'data', component: () => import('@/views/DataView.vue'), meta: { title: '水文资料库' } },
  { path: '/simulation', name: 'simulation', component: () => import('@/views/SimulationView.vue'), meta: { title: '洪水模拟' } },
  { path: '/state/advanced', name: 'state-advanced', component: () => import('@/views/AdvancedStateView.vue'), meta: { title: '独立状态初始化' } },
  {
    path: '/realtime',
    name: 'realtime',
    component: () => import('@/views/RealtimeView.vue'),
    meta: { title: '雨水情' }
  },
  {
    path: '/forecast',
    name: 'forecast',
    component: () => import('@/views/ForecastView.vue'),
    meta: { title: '洪水预报' }
  },
  {
    path: '/state',
    name: 'state',
    component: () => import('@/views/StateView.vue'),
    meta: { title: '模拟状态' }
  },
  {
    path: '/calibration',
    name: 'calibration',
    component: () => import('@/views/CalibrationView.vue'),
    meta: { title: '参数率定' }
  },
  {
    path: '/diagnostics',
    name: 'diagnostics',
    component: () => import('@/views/DiagnosticView.vue'),
    meta: { title: '进阶诊断' }
  },
  {
    path: '/archive',
    name: 'archive',
    component: () => import('@/views/ArchiveView.vue'),
    meta: { title: '计算成果' }
  },
  {
    path: '/config',
    name: 'config',
    component: () => import('@/views/ConfigView.vue'),
    meta: { title: '模型与资料' }
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
