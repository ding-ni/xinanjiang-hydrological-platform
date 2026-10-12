import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { title: '工作区与系统' } },
  { path: '/', redirect: '/realtime' },
  { path: '/data', name: 'data', component: () => import('@/views/DataView.vue'), meta: { title: '水文资料库' } },
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
    meta: { title: '起算状态' }
  },
  {
    path: '/calibration',
    name: 'calibration',
    component: () => import('@/views/CalibrationView.vue'),
    meta: { title: '率定与模拟' }
  },
  {
    path: '/diagnostics',
    name: 'diagnostics',
    component: () => import('@/views/DiagnosticView.vue'),
    meta: { title: '模型诊断' }
  },
  {
    path: '/archive',
    name: 'archive',
    component: () => import('@/views/ArchiveView.vue'),
    meta: { title: '成果管理' }
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
