<template>
  <details class="search-editor" open>
    <summary>{{ title || '率定参数' }} <span>{{ activeCount }} 项参与率定</span></summary>
    <fieldset :disabled="disabled">
      <div class="scope-actions"><button type="button" class="btn" @click="choose()">全选</button><template v-if="!routing"><button type="button" class="btn" @click="choose('storage')">仅蓄水产流</button><button type="button" class="btn" @click="choose('sources')">仅水源划分</button></template><button type="button" class="btn" @click="emit('update:modelValue', {})">全部固定</button></div>
      <div class="search-rows">
        <div class="search-head"><span>参数 · 参与率定</span><span>初值</span><span>下限</span><span>上限</span><span>单位</span></div>
        <div v-for="row in definitions" :key="row.key" class="search-row" :class="{ fixed: !enabled(row.key) }">
          <label class="select-parameter"><input type="checkbox" :checked="enabled(row.key)" :disabled="Boolean(row.unavailable)" :aria-label="`${row.key} 参与率定`" @change="toggle(row, $event.target.checked)" /><span><b>{{ row.symbol }}</b> {{ row.label }}</span></label>
          <div class="search-bounds"><input type="number" class="input" step="any" :value="current[row.key]" :disabled="row.initialReadOnly" :title="row.initialReadOnly ? '1表示当前各河段K；实际初值请在各河段K、X中修改' : ''" :placeholder="row.key==='muskingum_x'?'分河段设置':''" :aria-label="`${row.key} 初值`" @change="emit('initial-value', row.key, $event.target.value)" /><template v-if="enabled(row.key)"><input type="number" class="input" step="any" :min="row.limitMin" :max="row.limitMax" :value="value(row).minimum" :aria-label="`${row.key} 搜索下限`" @change="bound(row, 'minimum', $event.target.value)" /><input type="number" class="input" step="any" :min="row.limitMin" :max="row.limitMax" :value="value(row).maximum" :aria-label="`${row.key} 搜索上限`" @change="bound(row, 'maximum', $event.target.value)" /></template><span v-else class="fixed-label">保持初值</span><small>{{ row.unit === '1' ? '—' : row.unit }}</small></div>
        </div>
      </div>
      <p v-for="row in definitions.filter(item => item.unavailable)" :key="row.key" class="space-note">{{ row.unavailable }}</p>
      <p class="space-error" v-if="error" role="status">{{ error }}</p>
    </fieldset>
  </details>
</template>

<script setup>
import { computed } from 'vue'
import { searchCoordinates, selectedSearchParameters } from '@/services/calibrationSpaceService'
const props = defineProps({ modelValue: { type: Object, default: null }, definitions: { type: Array, required: true }, params: { type: Object, required: true }, ranges: { type: Object, default: () => ({}) }, routing: Boolean, joint: Boolean, title: String, disabled: Boolean, error: String })
const emit = defineEmits(['update:modelValue', 'update:ranges', 'initial-value'])
const current = computed(() => props.routing ? props.params : searchCoordinates(props.params))
const enabled = key => !props.definitions.find(row => row.key === key)?.unavailable && (props.modelValue === null || Object.hasOwn(props.modelValue, key))
const value = row => props.ranges[row.key] || props.modelValue?.[row.key] || row
const activeCount = computed(() => props.definitions.filter(row => enabled(row.key)).length)
const number = value => Number.isFinite(value) ? Number(value.toPrecision(6)) : '—'
function choose(group) { emit('update:modelValue', Object.fromEntries(props.definitions.filter(row => !row.unavailable && (!group || row.group === group)).map(row => [row.key, { minimum: value(row).minimum, maximum: value(row).maximum }]))) }
function toggle(row, active) {
  const next = props.modelValue === null ? selectedSearchParameters(props.definitions) : JSON.parse(JSON.stringify(props.modelValue))
  if (active) next[row.key] = { minimum: value(row).minimum, maximum: value(row).maximum }
  else {
    emit('update:ranges', { ...props.ranges, [row.key]: { minimum: value(row).minimum, maximum: value(row).maximum } })
    delete next[row.key]
  }
  emit('update:modelValue', next)
}
function bound(row, key, text) {
  const next = props.modelValue === null ? selectedSearchParameters(props.definitions) : JSON.parse(JSON.stringify(props.modelValue))
  const bounds = { minimum: value(row).minimum, maximum: value(row).maximum, [key]: text.trim() && Number.isFinite(Number(text)) ? Number(text) : null }
  emit('update:ranges', { ...props.ranges, [row.key]: bounds })
  if (enabled(row.key)) { next[row.key] = bounds; emit('update:modelValue', next) }
}
</script>

<style scoped>
.search-editor{border:1px solid #ceded8;border-radius:6px;background:#f7faf7;margin:14px 0;color:#365c4a}.search-editor summary{cursor:pointer;padding:12px;font-size:12px;font-weight:600}.search-editor summary span{float:right;color:#7b8c7b;font-weight:400;font-size:11px}.search-editor fieldset{border:0;padding:0 12px 12px;margin:0;min-width:0}.scope-actions{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}.scope-actions .btn{font-size:10px;padding:4px 7px}.search-row{padding:10px 0;border-top:1px solid #e0e8df}.select-parameter{display:flex;gap:6px;align-items:center;font-size:11px}.select-parameter b{font-size:12px;margin-right:5px;color:#276c5a}.search-bounds{display:flex;align-items:center;gap:5px;margin:8px 0 0;font-size:10px;color:#7c897a;font-variant-numeric:tabular-nums}.search-bounds>span:first-child{margin-right:auto}.search-bounds .input{width:64px;min-width:0;font-size:11px;padding:4px;height:26px}.fixed-label{font-size:10px;font-weight:400;color:#798878}.fixed .select-parameter{color:#929c90}.space-note{font-size:10px;line-height:1.65;color:#7b8a78;margin-bottom:0}.space-error{font-size:11px;line-height:1.5;color:#b34d39}
</style>

<style scoped>
.search-editor summary,.search-editor summary span,.select-parameter,.select-parameter b,.scope-actions .btn,.space-note,.space-error{font-size:14px}
.search-head,.search-row{display:grid;grid-template-columns:minmax(180px,1.8fr) minmax(280px,3fr);align-items:center;gap:12px}
.search-head{grid-template-columns:minmax(180px,1.8fr) repeat(3,minmax(68px,1fr)) 44px;font-size:14px;padding:12px 0;color:#60796b}
.search-bounds{display:grid;grid-template-columns:repeat(3,minmax(68px,1fr)) 44px;align-items:center;margin:0;gap:10px;font-size:14px}
.search-bounds>span:first-child{margin:0}.search-bounds .input{width:100%;height:36px;font-size:14px;padding:6px}.fixed-label{grid-column:2/4;font-size:14px}.search-bounds small{font-size:14px}
@media(max-width:650px){.search-head{display:none}.search-row{grid-template-columns:1fr}.search-bounds{margin-top:8px}.search-bounds .input{min-width:0}}
</style>
