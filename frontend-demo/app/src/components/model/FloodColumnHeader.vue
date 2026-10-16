<template>
  <span class="flood-column"><button type="button" class="sort-button" :aria-label="`${label}排序`" @click="cycle">{{ label }} <span aria-hidden="true">{{ active ? direction === 'asc' ? '↑' : '↓' : '↕' }}</span></button><el-popover v-if="filter" placement="bottom" trigger="click" :width="250" teleported><template #reference><button type="button" class="filter-button" :aria-label="`${label}筛选`"><Filter /></button></template><div class="column-filter"><slot /></div></el-popover></span>
</template>
<script setup>
import { computed } from 'vue'
import { Filter } from '@element-plus/icons-vue'
const props=defineProps({label:String,column:String,sortKey:String,direction:String,filter:Boolean})
const emit=defineEmits(['sort'])
const active=computed(()=>props.sortKey===props.column&&props.direction)
function cycle(){emit('sort',props.column,!active.value?'asc':props.direction==='asc'?'desc':'')}
</script>
<style scoped>
.flood-column{display:inline-flex;align-items:center;gap:3px;max-width:100%}.sort-button,.filter-button{border:0;background:none;color:inherit;cursor:pointer;padding:5px 2px;font:inherit;min-height:36px}.sort-button{text-align:left}.sort-button span{color:#257c70}.filter-button{width:26px;flex-shrink:0}.filter-button svg{width:15px;height:15px}.column-filter{display:grid;gap:10px;font-size:14px}.column-filter :deep(label){display:grid;gap:7px}.column-filter :deep(.input){width:100%;min-height:36px}
</style>
