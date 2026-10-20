<template><span v-if="status" class="numeric-readiness" :class="status.status" role="status">{{ status.label }}</span></template>
<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { modelApi } from '@/services/modelBackendService'
const status=ref(null)
let timer,disposed=false
async function refresh(){try{const value=await modelApi('numeric-status');if(disposed)return;status.value=value;if(value.status==='preparing')timer=setTimeout(refresh,2000)}catch{/* Calculation progress still reports preparation if this optional status is unavailable. */}}
onMounted(refresh)
onBeforeUnmount(()=>{disposed=true;clearTimeout(timer)})
</script>
<style scoped>.numeric-readiness{font-size:14px;line-height:1.6;color:#70867b}.numeric-readiness.ready{color:#2d7a6d}.numeric-readiness.preparing{color:#a46f28}</style>
