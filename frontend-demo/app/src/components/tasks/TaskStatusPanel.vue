<template>
  <section v-if="task" class="task-status" :class="task.status">
    <div class="task-head">
      <div>
        <span class="task-kicker">{{ task.resultSource === 'frontend-fixture' ? '试算任务' : '模型任务' }}</span>
        <b>{{ task.runTypeLabel }} · {{ task.taskId }}</b>
      </div>
      <span class="task-state">{{ statusLabel }}</span>
    </div>
    <div class="task-progress"><i :style="{ width: `${task.progress || 0}%` }" /></div>
    <div class="task-foot">
      <span>{{ task.stageLabel }}</span>
      <b>{{ task.progress || 0 }}%</b>
    </div>
    <p v-if="task.errorMessage" class="task-error">{{ task.errorMessage }}</p>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ task: { type: Object, default: null } })
const labels = {
  draft: '草稿', queued: '排队', running: '运行中', completed: '已完成',
  archived: '已归档', failed: '失败', cancelled: '已停止'
}
const statusLabel = computed(() => labels[props.task?.status] || props.task?.status || '')
</script>

<style scoped>
.task-status { padding: 10px 12px; border: 1px solid #cbd9e5; border-left: 3px solid #4b7898; border-radius: 7px; background: #f8fbfd; }
.task-status.completed,.task-status.archived { border-left-color: #27835b; background: #f4faf7; }
.task-status.failed { border-left-color: #bd443e; background: #fff7f6; }
.task-status.cancelled { border-left-color: #8b7464; background: #faf8f6; }
.task-head,.task-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.task-head > div { display: grid; gap: 2px; min-width: 0; }
.task-head b { overflow: hidden; color: #18334d; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.task-kicker { color: #61798c; font-size: 10px; font-weight: 800; letter-spacing: .05em; }
.task-state { flex: 0 0 auto; color: #35566f; font-size: 11px; font-weight: 800; }
.task-progress { height: 5px; margin: 8px 0 6px; overflow: hidden; border-radius: 999px; background: #dce6ed; }
.task-progress i { display: block; height: 100%; border-radius: inherit; background: #23769a; transition: width .18s ease; }
.completed .task-progress i,.archived .task-progress i { background: #27835b; }
.failed .task-progress i { background: #bd443e; }
.task-foot { color: #617487; font-size: 10px; }
.task-error { margin: 7px 0 0; color: #a23530; font-size: 11px; line-height: 1.45; }
</style>
