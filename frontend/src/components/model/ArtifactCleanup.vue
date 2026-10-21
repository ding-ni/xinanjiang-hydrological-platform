<template>
  <button class="btn" :disabled="busy" @click="preview">{{ busy ? '正在核对文件…' : '检查旧版残留文件' }}</button>
  <HydrologyDialog ref="dialog" width="min(860px,94vw)">
    <h2>检查旧版残留文件</h2>
    <p>只检查父操作已不在平台中的内部计算及其自产状态。雨水情库、MDB、模型/GIS、输入产品和独立 GIS 图件任务不在清理范围。</p>
    <p v-if="plan">可移走：{{ cleanupSummary(plan) }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <details v-if="plan?.artifacts?.length" open><summary>将移入 Windows 回收站的文件清单</summary>
      <ul class="cleanup-files"><li v-for="row in plan.artifacts" :key="row.id"><code>{{ row.relativePath }}</code> · {{ (row.bytes / 1048576).toFixed(2) }} MiB</li></ul>
    </details>
    <details v-if="plan?.protected?.length" open><summary>需要保留（{{ plan.protected.length }} 项）</summary>
      <ul class="cleanup-files"><li v-for="row in plan.protected" :key="row.id"><code>{{ row.relativePath }}</code><br />保留原因：{{ row.reason }}</li></ul>
    </details>
    <p>确认后，上列文件会离开 workspace；可从 Windows 回收站恢复。磁盘空间仍由 Windows 回收站占用，是否清空系统回收站由你决定。</p>
    <div class="cleanup-actions"><button class="btn" :disabled="busy" @click="dialog.close()">关闭</button><button class="btn" :disabled="busy" @click="preview">重新检查</button><button class="btn primary" :disabled="busy || !plan?.artifacts?.length" @click="confirm">确认移走上列残留文件</button></div>
  </HydrologyDialog>
</template>
<script setup>
import { ref } from 'vue'
import { modelApi } from '@/services/modelBackendService'
import { cleanupSummary } from '@/services/artifactCleanupService'
import HydrologyDialog from './HydrologyDialog.vue'
const emit = defineEmits(['cleaned'])
const dialog=ref(null),plan=ref(null),busy=ref(false),error=ref(''),message=ref('')
async function preview() {
  dialog.value.showModal(); busy.value=true; error.value=''; message.value=''; plan.value=null
  try { plan.value=await modelApi('workspace/artifact-cleanup',{timeoutMs:60000}) }
  catch(failure) { error.value=failure.message }
  finally { busy.value=false }
}
async function confirm() {
  if(!plan.value?.artifacts?.length || busy.value)return
  busy.value=true;error.value=''
  try {
    const result=await modelApi('workspace/artifact-cleanup',{method:'POST',body:{previewSHA256:plan.value.previewSHA256},timeoutMs:60000})
    message.value=`已将 ${result.movedArtifacts.length} 项送入 Windows 回收站。`;plan.value=null;emit('cleaned',result)
  } catch(failure) { error.value=failure.message;plan.value=null }
  finally { busy.value=false }
}
</script>
<style scoped>
.cleanup-files{max-height:280px;overflow:auto;line-height:1.8;padding-left:24px}.cleanup-files li{margin-bottom:6px;overflow-wrap:anywhere}.cleanup-actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}.error{color:#a64835}p{line-height:1.7}details{margin:16px 0}
</style>
