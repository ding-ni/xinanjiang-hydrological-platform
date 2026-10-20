<template>
  <div class="param-panel">
    <div class="panel-head" v-if="title">
      <h3 class="panel-title">{{ title }}</h3>
      <div class="head-actions">
        <span v-if="dirty" class="dirty-tag">已调整</span>
        <button v-if="showReset" class="btn" type="button" @click="$emit('reset')">重置</button>
      </div>
    </div>
    <div class="param-list">
      <div v-for="d in visibleDefs" :key="d.key" class="param-row">
        <div class="param-meta">
          <label :title="d.desc" :for="`parameter-${d.key}`"><code>{{ d.symbol || d.key }}</code>{{ d.label.replace(/^[^·]+·\s*/, '') }}</label>
          <div class="val-box">
            <input
              class="num"
              :id="`parameter-${d.key}`"
              :aria-label="`${d.label} ${d.symbol || d.key}`"
              type="number"
              :min="d.min"
              :max="d.max"
              :step="d.step"
              :value="valueFor(d.key)"
              @input="drafts[d.key] = $event.target.value"
              @change="commit(d.key, $event.target.value)"
              @keydown.enter="$event.target.blur()"
            />
            <span class="unit" v-if="d.unit">{{ d.unit }}</span>
          </div>
        </div>
        <input
          class="range"
          type="range"
          :min="d.min"
          :max="d.max"
          :step="d.step"
          :aria-label="`${d.label} ${d.symbol || d.key} 滑块`"
          :value="valueFor(d.key)"
          @input="drafts[d.key] = $event.target.value; livePreview && onNum(d.key, $event.target.value)"
          @change="commit(d.key, $event.target.value)"
        />
        <p class="hint-line"><span>{{ d.min }} — {{ d.max }}</span></p>
      </div>
    </div>
    <button
      v-if="dutyOnly && defs.length > visibleDefs.length"
      class="btn block more"
      type="button"
      @click="$emit('toggle-advanced')"
    >
      {{ advanced ? '收起高级参数' : `高级参数（全部 ${defs.length} 项）` }}
    </button>
    <p v-if="livePreview" class="engine-note">演示过程线随参数调整</p>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { PARAM_DEFS } from '@/models/xajParamEngine'

const props = defineProps({
  params: { type: Object, required: true },
  defs: { type: Array, default: () => PARAM_DEFS },
  title: { type: String, default: '可调参数' },
  dirty: { type: Boolean, default: false },
  showReset: { type: Boolean, default: true },
  /** 仅显示值班关键参，配合 advanced */
  dutyOnly: { type: Boolean, default: false },
  advanced: { type: Boolean, default: true },
  livePreview: { type: Boolean, default: true }
})

const emit = defineEmits(['update', 'reset', 'toggle-advanced'])
const drafts = reactive({})
const valueFor = key => drafts[key] ?? props.params[key]
watch(() => props.params, () => Object.keys(drafts).forEach(key => delete drafts[key]), { deep: true })
function commit(key, value) {
  if (String(value).trim() && Number.isFinite(Number(value))) emit('update', key, value)
  delete drafts[key]
}

const visibleDefs = computed(() => {
  if (!props.dutyOnly || props.advanced) return props.defs
  return props.defs.filter((d) => d.duty)
})

function onNum(key, val) {
  emit('update', key, val)
}
</script>

<style scoped>
.param-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.panel-head .panel-title {
  margin: 0;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dirty-tag {
  padding: 2px 8px;
  border-radius: 999px;
  background: #fff3e8;
  color: #c45a10;
  font-size: 11px;
  font-weight: 800;
  white-space: nowrap;
}

.param-list {
  display: grid;
  gap: 10px;
  max-height: 100%;
  overflow: auto;
  padding-right: 4px;
}

.param-row {
  padding: 8px 10px;
  border: 1px solid #e0eaf6;
  border-radius: 8px;
  background: #fbfdff;
}

.param-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.param-meta label {
  color: #173052;
  font-size: 12px;
  font-weight: 800;
  cursor: help;
}
.param-meta label code { display: block; color: #176c75; font-size: 14px; letter-spacing: .04em; margin-bottom: 3px; }
.hint-line span { font-variant-numeric: tabular-nums; }

.val-box {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.num {
  width: 72px;
  height: 28px;
  padding: 0 6px;
  border: 1px solid #c8d8ec;
  border-radius: 6px;
  text-align: right;
  font-weight: 700;
  color: #0b57c7;
  background: #fff;
}

.unit {
  color: #7a8ba3;
  font-size: 11px;
}

.range {
  width: 100%;
  accent-color: #0b57c7;
  cursor: pointer;
}

.hint-line {
  margin: 4px 0 0;
  color: #7a8ba3;
  font-size: 10px;
  line-height: 1.35;
}

.more {
  margin-top: 10px;
}

.engine-note {
  margin: 8px 0 0;
  color: #9aabbf;
  font-size: 10px;
  text-align: center;
}

.btn.block {
  width: 100%;
}
</style>
