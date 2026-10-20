<template>
  <div class="metric context-status" :class="statusClass" :title="detail">
    <span>{{ app.backendCase ? '模型服务' : '资料状态' }}</span>
    <b>{{ label }}</b>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '@/stores/app'

const app = useAppStore()
const state = computed(() => {
  if (app.backendCase && app.backendError) return 'error'
  if (app.backendCase?.ready) return 'success'
  if (app.projectDataLoading) return 'loading'
  if (app.projectDataError) return 'error'
  if (!app.projectData) return 'empty'
  if (app.projectData.resolution?.inputStatus === 'missing' || ['draft', 'missing'].includes(app.availabilityStatus)) return 'empty'
  if (app.availabilityStatus === 'frontend-fixture') return 'fixture'
  if (!app.projectData?.datasets?.datasets?.length) return 'partial'
  if (['spatial-only', 'partial', 'missing'].includes(app.availabilityStatus)) return 'partial'
  return 'success'
})
const labels = { loading: '目录加载中', success: '资料可用', partial: '部分资料', empty: '资料待接入', fixture: '演示资料', error: '目录错误' }
const label = computed(() => app.backendError && app.backendCase ? '服务离线' : app.backendCase?.ready ? '服务正常' : ['loading', 'error'].includes(state.value)
  ? labels[state.value]
  : app.projectData?.resolution?.statusLabel || labels[state.value])
const statusClass = computed(() => `is-${state.value}`)
const detail = computed(() => app.backendError || app.backendCase?.qualityNotes?.join('；') || app.projectDataError || app.projectData?.resolution?.sourceSummary || app.dataStatus || label.value)
</script>

<style scoped>
.context-status b { color: #17665c; }
.context-status.is-loading b { color: #0b57c7; }
.context-status.is-partial b { color: #b75a10; }
.context-status.is-fixture b { color: #b75a10; }
.context-status.is-empty b { color: #6a7c95; }
.context-status.is-error b { color: #b33a34; }
</style>
