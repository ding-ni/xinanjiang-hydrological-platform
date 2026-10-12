<template>
  <section class="system-view">
    <header><div><span class="eyebrow">平台管理</span><h2>工作区与系统</h2></div><button class="btn" :disabled="busy" @click="refresh">刷新</button></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <template v-if="info">
      <div class="overview panel"><div><span>当前工作区</span><h3>{{ info.workspace.name }}</h3><small>{{ info.workspace.id }}</small></div><div><span>程序版本</span><b>{{ info.version }}</b></div><div><span>资料库版本</span><b>{{ info.schemaVersion }}</b></div><div><span>磁盘剩余</span><b>{{ size(info.freeBytes) }}</b></div></div>
      <section class="panel directories"><div class="section-title"><h3>目录位置</h3><span>配置文件 <code>{{ info.configFile }}</code></span></div>
        <div v-for="row in info.directories" :key="row.id" class="directory"><b>{{ row.label }}</b><code>{{ row.path }}</code><button class="btn" @click="copy(row.path)">复制路径</button></div>
      </section>
      <div class="lower">
        <section class="panel backup"><div class="section-title"><h3>工作区备份</h3><button class="btn primary" :disabled="busy" @click="backup">{{ busy ? '正在备份…' : '备份工作区' }}</button></div><p class="subtle">保存资料库、计算资料、参数、起算状态与计算成果。</p>
          <p v-if="message" role="status" class="message">{{ message }}</p>
          <div v-if="!backups.length" class="empty">暂无备份记录</div>
          <div v-for="row in backups" :key="row.name" class="backup-row"><div><b>{{ date(row.createdAt) }}</b><small>{{ row.files }} 个文件 · {{ size(row.bytes) }}</small><code>{{ row.path }}</code></div><button class="btn" @click="copy(row.path)">复制路径</button></div>
        </section>
        <section class="panel drafts"><h3>率定方案与场次草稿</h3><b :class="{ error: workspaceSync.error }">{{ workspaceSync.error || (workspaceSync.saving ? '正在保存' : workspaceSync.pending ? '有待保存修改' : workspaceSync.ready ? '已保存到工作区' : '本机草稿') }}</b><p class="subtle">场次用途、备注与率定草稿随工作区保存。</p><div class="actions"><button class="btn" :disabled="workspaceSync.saving" @click="retryDrafts">重新保存</button><button class="btn" @click="exportDrafts">导出本机草稿</button><button v-if="workspaceSync.error && workspaceSync.ready" class="btn" :disabled="workspaceSync.saving" @click="loadSharedDrafts">读取工作区版本</button></div><details><summary>运行环境</summary><p>Python {{ info.pythonVersion }}</p><p>工作区路径在平台配置中设置，重启后生效。</p></details></section>
      </div>
    </template>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { modelApi } from '@/services/modelBackendService'
import { workspaceSync, flushBusinessDrafts, exportBusinessDrafts, initializeBusinessDrafts, reloadWorkspaceDrafts } from '@/services/workspaceService'
const info = ref(null), backups = ref([]), busy = ref(false), error = ref(''), message = ref('')
const size = value => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`
const date = value => new Date(value).toLocaleString('zh-CN', { hour12: false })
async function refresh() {
  error.value = ''
  try { const [settings, saved] = await Promise.all([modelApi('workspace'), modelApi('workspace/backups')]); info.value = settings; backups.value = saved.backups }
  catch (failure) { error.value = failure.message }
}
async function backup() {
  busy.value = true; error.value = ''; message.value = ''
  try { await flushBusinessDrafts(); if (workspaceSync.pending) throw new Error('请先保存或导出待同步草稿'); const saved = await modelApi('workspace/backups', { method: 'POST', body: {}, timeoutMs: 600000 }); message.value = `备份完成 · ${saved.files} 个文件`; await refresh() }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function copy(path) { try { await navigator.clipboard.writeText(path); message.value = '路径已复制' } catch { error.value = '请选中路径后复制' } }
async function retryDrafts() { await (workspaceSync.ready ? flushBusinessDrafts() : initializeBusinessDrafts()) }
async function loadSharedDrafts() {
  if (!window.confirm('将本机草稿留存后，读取工作区中已保存的版本？')) return
  try { await reloadWorkspaceDrafts(); window.location.reload() } catch (failure) { error.value = failure.message }
}
function exportDrafts() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(exportBusinessDrafts(), null, 2)], { type: 'application/json' }))
  const a = document.createElement('a'); a.href = url; a.download = '水文工作区草稿.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
onMounted(refresh)
</script>

<style scoped>
.system-view{max-width:1500px;margin:0 auto;display:grid;gap:16px;padding:16px;color:#29483f}header,.section-title{display:flex;align-items:center;justify-content:space-between;gap:16px}h2{font-size:24px;margin:5px 0}h3{font-size:15px;margin:0 0 12px}.eyebrow,.subtle,small,.section-title>span{color:#71847b;font-size:12px}.panel{padding:22px;border:1px solid #dce5df;box-shadow:none}.overview{display:grid;grid-template-columns:2fr repeat(3,1fr);gap:24px}.overview>div{display:grid;align-content:start;gap:10px}.overview h3{font-size:19px;margin:0}.overview span{font-size:12px;color:#71847b}.overview b{font-size:21px;font-variant-numeric:tabular-nums}.overview small,code{overflow-wrap:anywhere}code{font-size:12px;font-family:Consolas,monospace}.section-title{margin-bottom:14px;flex-wrap:wrap}.section-title h3{margin:0}.directory{display:grid;grid-template-columns:160px minmax(0,1fr) auto;align-items:center;gap:20px;padding:11px 0;border-top:1px solid #edf0ed}.directory b{font-size:13px}.directory code{color:#51645b}.lower{display:grid;grid-template-columns:1.4fr 1fr;gap:16px}.backup-row{display:flex;justify-content:space-between;align-items:center;gap:12px;border-top:1px solid #edf0ed;padding:14px 0}.backup-row>div{display:grid;gap:7px;min-width:0}.backup-row b,.drafts>b{font-size:13px}.backup-row .btn{flex-shrink:0}.actions{display:flex;gap:8px;flex-wrap:wrap}.error{color:#a5463b}.message{font-size:12px;color:#28724e}.empty{padding:24px 0;color:#829087;font-size:13px}details{margin-top:24px;font-size:12px;color:#71847b}summary{cursor:pointer}@media(max-width:900px){.overview{grid-template-columns:1fr 1fr}.lower{grid-template-columns:1fr}.directory{grid-template-columns:1fr auto;gap:8px}.directory code{grid-column:1;grid-row:2}.directory .btn{grid-column:2;grid-row:1/3}.system-view{padding:8px}.panel{padding:16px}}
</style>
