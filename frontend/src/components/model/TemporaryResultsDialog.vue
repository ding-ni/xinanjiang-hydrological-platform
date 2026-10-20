<template>
  <HydrologyDialog ref="dialog" width="min(760px,94vw)" @closed="completeLeave(false)">
    <h2>处理本轮临时结果</h2><p>保存成果后可长期查找；丢弃会移入回收站，已保存参数和计算依赖保留。</p>
    <div v-for="row in leaveRequest?.rows || []" :key="row.id" class="pending-row"><span>{{ row.name }}</span><select v-model="choices[row.id]" class="input" :aria-label="row.name+'处理方式'"><option value="save">保存成果</option><option value="discard">丢弃</option></select></div>
    <p v-if="error" role="alert">{{ error }}</p><footer><button class="btn" :disabled="busy" @click="finish(false)">取消离开</button><button class="btn primary" :disabled="busy" @click="apply">确认并离开</button></footer>
  </HydrologyDialog>
</template>
<script setup>
import { ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import HydrologyDialog from './HydrologyDialog.vue'
import { leaveRequest, completeLeave, changeRetention, hasPendingResults } from '@/services/resultRetentionService'
const dialog=ref(null), choices=ref({}), busy=ref(false), error=ref('')
watch(leaveRequest, async value => { if(value){choices.value=Object.fromEntries(value.rows.map(row=>[row.id,'save']));error.value='';await nextTick();dialog.value.showModal()} })
function finish(value){dialog.value.close();completeLeave(value)}
async function apply(){busy.value=true;error.value='';try{for(const row of [...leaveRequest.value.rows]){await changeRetention(row.id,choices.value[row.id],row.retentionRevision);leaveRequest.value.rows=leaveRequest.value.rows.filter(item=>item.id!==row.id)}finish(true)}catch(failure){error.value=failure.message}finally{busy.value=false}}
function warn(event){if(hasPendingResults()){event.preventDefault();event.returnValue=''}}
onMounted(()=>{window.addEventListener('beforeunload',warn);dialog.value?.$el?.addEventListener('cancel',()=>completeLeave(false))})
onBeforeUnmount(()=>window.removeEventListener('beforeunload',warn))
</script>
<style scoped>.pending-row{display:flex;align-items:center;gap:20px;padding:12px 0;border-bottom:1px solid #e0e9e3}.pending-row span{flex:1}footer{display:flex;justify-content:flex-end;gap:12px;margin-top:20px}p{color:#6c8178;line-height:1.7}</style>
