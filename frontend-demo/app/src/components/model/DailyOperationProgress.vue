<template>
  <div v-if="operation" class="operation-progress" role="status">
    <div><b>{{ operationStatuses[operation.status] || operation.status }}</b><span v-if="activeOperation(operation)">{{ operation.stageLabel || operation.stage || '' }}</span><button v-if="activeOperation(operation)" type="button" class="btn" :disabled="operation.status === 'cancelling'" @click="$emit('cancel')">停止</button></div>
    <progress v-if="activeOperation(operation)" :value="Number(operation.progress || 0)" max="100" />
  </div>
</template>
<script setup>
import { activeOperation, operationStatuses } from '@/services/dailyOperationService'
defineProps({ operation: Object })
defineEmits(['cancel'])
</script>
<style scoped>
.operation-progress{border-top:1px solid #dce6e1;margin-top:18px;padding-top:14px}.operation-progress>div{display:flex;align-items:center;gap:12px;font-size:12px;color:#58736a}.operation-progress b{color:#285a4b}.operation-progress span{flex:1}.operation-progress progress{width:100%;height:5px;accent-color:#357e68;margin-top:12px}.failure{color:#a34238;font-size:12px;line-height:1.7}
</style>
