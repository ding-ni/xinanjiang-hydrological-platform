import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'maplibre-gl/dist/maplibre-gl.css'

import App from './App.vue'
import router from './router'
import { useTasksStore } from './stores/tasks'
import './styles/variables.css'
import './styles/global.css'
import { initializeBusinessDrafts } from './services/workspaceService'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(ElementPlus, { locale: zhCn, size: 'default' })
initializeBusinessDrafts().finally(() => {
  useTasksStore(pinia).hydrate().catch(() => {})
  app.mount('#app')
})
