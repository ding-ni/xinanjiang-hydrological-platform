<template>
  <details class="search-editor" :open="modelValue !== null">
    <summary>自动率定参数 <span>{{ activeCount }} 项参与搜索</span></summary>
    <fieldset :disabled="disabled">
      <div class="scope-actions"><button type="button" class="btn" @click="emit('update:modelValue', null)">预设范围</button><template v-if="!routing"><button type="button" class="btn" @click="choose('storage')">仅蓄水产流</button><button type="button" class="btn" @click="choose('sources')">仅水源划分</button></template><button type="button" class="btn" @click="emit('update:modelValue', {})">全部固定</button></div>
      <div class="search-rows">
        <div v-for="row in definitions" :key="row.key" class="search-row" :class="{ fixed: !enabled(row.key) }">
          <label class="select-parameter"><input type="checkbox" :checked="enabled(row.key)" :aria-label="`${row.key} 参与率定`" @change="toggle(row, $event.target.checked)" /><span><b>{{ row.symbol }}</b> {{ row.label }}</span></label>
          <div class="search-bounds"><span :title="String(current[row.key])">当前 {{ number(current[row.key]) }}</span><template v-if="enabled(row.key)"><input type="number" class="input" step="any" :min="row.limitMin" :max="row.limitMax" :value="value(row).minimum" :aria-label="`${row.key} 搜索下限`" @change="bound(row, 'minimum', $event.target.value)" /><span>—</span><input type="number" class="input" step="any" :min="row.limitMin" :max="row.limitMax" :value="value(row).maximum" :aria-label="`${row.key} 搜索上限`" @change="bound(row, 'maximum', $event.target.value)" /></template><b v-else class="fixed-label">固定</b><small>{{ row.unit === '1' ? '' : row.unit }}</small></div>
        </div>
      </div>
      <p class="space-error" v-if="error" role="status">{{ error }}</p>
      <p v-if="routing" class="space-note">调整全局坡面汇流时间；产流、河道及单元单列参数保持不变。</p>
      <p v-else class="space-note">未勾选项采用当前参数；KC、C及汇流参数固定。KΣ,d 与 fG 由当前 KI、KG 换算，搜索后再转换为小时系数。</p>
    </fieldset>
  </details>
</template>

<script setup>
import { computed } from 'vue'
import { searchCoordinates, selectedSearchParameters } from '@/services/calibrationSpaceService'
const props = defineProps({ modelValue: { type: Object, default: null }, definitions: { type: Array, required: true }, params: { type: Object, required: true }, routing: Boolean, disabled: Boolean, error: String })
const emit = defineEmits(['update:modelValue'])
const current = computed(() => props.routing ? props.params : searchCoordinates(props.params))
const enabled = key => props.modelValue === null || Object.hasOwn(props.modelValue, key)
const value = row => props.modelValue?.[row.key] || row
const activeCount = computed(() => props.modelValue === null ? props.definitions.length : Object.keys(props.modelValue).length)
const number = value => Number.isFinite(value) ? Number(value.toPrecision(6)) : '—'
function choose(group) { emit('update:modelValue', selectedSearchParameters(props.definitions, props.definitions.filter(row => row.group === group).map(row => row.key))) }
function toggle(row, active) {
  const next = props.modelValue === null ? selectedSearchParameters(props.definitions) : JSON.parse(JSON.stringify(props.modelValue))
  if (active) next[row.key] = { minimum: row.minimum, maximum: row.maximum }
  else delete next[row.key]
  emit('update:modelValue', next)
}
function bound(row, key, text) {
  const next = props.modelValue === null ? selectedSearchParameters(props.definitions) : JSON.parse(JSON.stringify(props.modelValue))
  next[row.key][key] = text.trim() && Number.isFinite(Number(text)) ? Number(text) : null
  emit('update:modelValue', next)
}
</script>

<style scoped>
.search-editor{border:1px solid #ceded8;border-radius:6px;background:#f7faf7;margin:14px 0;color:#365c4a}.search-editor summary{cursor:pointer;padding:12px;font-size:12px;font-weight:600}.search-editor summary span{float:right;color:#7b8c7b;font-weight:400;font-size:11px}.search-editor fieldset{border:0;padding:0 12px 12px;margin:0;min-width:0}.scope-actions{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}.scope-actions .btn{font-size:10px;padding:4px 7px}.search-row{padding:10px 0;border-top:1px solid #e0e8df}.select-parameter{display:flex;gap:6px;align-items:center;font-size:11px}.select-parameter b{font-size:12px;margin-right:5px;color:#276c5a}.search-bounds{display:flex;align-items:center;gap:5px;margin:8px 0 0;font-size:10px;color:#7c897a;font-variant-numeric:tabular-nums}.search-bounds>span:first-child{margin-right:auto}.search-bounds .input{width:64px;min-width:0;font-size:11px;padding:4px;height:26px}.fixed-label{font-size:10px;font-weight:400;color:#798878}.fixed .select-parameter{color:#929c90}.space-note{font-size:10px;line-height:1.65;color:#7b8a78;margin-bottom:0}.space-error{font-size:11px;line-height:1.5;color:#b34d39}
</style>

<style scoped>
.search-row:not(.fixed) .search-bounds { flex-wrap: wrap; }
.search-row:not(.fixed) .search-bounds > span:first-child { flex-basis: 100%; margin-bottom: 2px; }
</style>
