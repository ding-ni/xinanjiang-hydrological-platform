<template>
  <section class="state-library panel" aria-label="状态库">
    <div class="library-header"><div><h3>状态库 <small>{{ visible.length }} / {{ states.length }}</small></h3></div><div class="selection-actions"><button v-if="canImport && !namedOnly" class="btn" :disabled="busy || disabled" @click="fileInput.click()">导入状态</button><input ref="fileInput" type="file" accept=".json,application/json" hidden @change="readImport" /><button class="btn" :disabled="busy" @click="refresh"><Refresh class="icon" />刷新</button></div></div>
    <form v-if="pendingImport" class="import-review" @submit.prevent="commitImport"><strong>状态文件 · {{ pendingImport.time?.replace('T', ' ') }}</strong><span>{{ pendingImport.gridIds?.length }} 个网格 · {{ pendingImport.context?.projectId }}</span><input v-model="importName" class="input" aria-label="导入状态名称" placeholder="状态名称（选填）" maxlength="80" :disabled="busy" /><div class="selection-actions"><button class="btn primary" :disabled="busy || disabled">{{ busy ? '正在导入…' : '确认导入' }}</button><button type="button" class="btn" :disabled="busy" @click="pendingImport = null">取消</button></div></form>
    <p v-if="importMessage" role="status">{{ importMessage }}</p>
    <div class="library-toolbar"><label v-if="namedOnly"><input v-model="allNodes" type="checkbox" /> 全部计算状态</label>
      <label class="search"><Search class="icon" /><input v-model="query" class="input" aria-label="检索状态" placeholder="名称、时间或编号" type="search" /></label>
      <select v-model="strategy" class="input" aria-label="状态来源"><option value="">全部来源</option><option value="MODEL_END_STATE">模型末状态</option><option value="CONTINUOUS">从连续起点补算</option><option value="FIXED_WARMUP">降雨预热</option><option value="OBSERVED_FLOW_ANCHORED">按实测流量初始化</option><option value="IMPORTED">导入状态</option></select>
      <button class="btn favorite-filter" :class="{ selected: favorites }" :aria-pressed="favorites" @click="favorites = !favorites"><StarFilled class="icon" />收藏</button>
    </div>
    <p v-if="error" class="library-error" role="alert">{{ error }}</p>
    <div class="library-layout">
      <div ref="nodeList" class="node-list">
        <button v-for="row in visible" :key="row.id" class="node-item" :class="{ active: row.id === activeId }" :title="row.id" :disabled="disabled" @click="$emit('select', row.id)">
          <span><b>{{ stateName(row, metadata) }}</b><small>{{ row.time.slice(0, 16) }} · {{ row.source }}<template v-if="stateScienceBlockReason(row)"> · 需重新初始化</template></small></span><StarFilled v-if="metadata[row.id]?.favorite" class="icon starred" />
        </button>
        <div v-if="!visible.length" class="library-empty">{{ states.length ? '没有匹配的状态' : '暂无状态' }}</div>
      </div>
      <div v-if="selected" class="state-selection">
        <p v-if="namedOnly">{{ selected.sourceResultName || selected.source || '计算末状态' }}<template v-if="selected.processName"> · {{ selected.processName }}</template></p>
        <button v-if="namedOnly" class="btn" @click="$emit('view')">查看过程与状态</button>
        <form class="name-editor" @submit.prevent="saveName"><input v-model="name" class="input" aria-label="状态名称" maxlength="80" :placeholder="stateName(selected, {})" :disabled="busy || disabled" /><button class="btn icon-btn" aria-label="保存状态名称" title="保存名称" :disabled="busy || disabled || name === savedName"><Check class="icon" /></button><button class="btn icon-btn" type="button" :aria-label="metadata[activeId]?.favorite ? '取消收藏状态' : '收藏状态'" :title="metadata[activeId]?.favorite ? '取消收藏' : '收藏'" :disabled="busy || disabled" @click="toggleFavorite"><StarFilled v-if="metadata[activeId]?.favorite" class="icon starred" /><Star v-else class="icon" /></button></form>
        <div class="selection-metrics"><div><span>状态时刻</span><b>{{ selected.time.slice(0, 16) }}</b></div><div><span>张力水 W/WM</span><b>{{ number(selected.means.soil, 3) }}</b></div><div><span>自由水 S</span><b>{{ number(selected.means.free, 2) }} <small>mm</small></b></div><div v-if="selected.checkpoint?.initialization?.anchor"><span>徐畈初始流量</span><b>{{ number(selected.checkpoint.initialization.anchor.flowCms, 2) }} <small>m³/s</small></b></div></div>
        <p v-if="stateScienceBlockReason(selected)" class="library-error" role="status">{{ stateScienceBlockReason(selected) }}</p>
        <div class="selection-actions"><button class="btn primary" :disabled="disabled || Boolean(stateScienceBlockReason(selected))" @click="$emit('continue')">用于续算</button><button class="btn" :disabled="busy" @click="download('json')"><Download class="icon" />状态文件</button><button class="btn" :disabled="busy" @click="download('csv')">网格 CSV</button></div>
        <details><summary>计算条件详情</summary><dl><dt>状态</dt><dd>{{ selected.id }}</dd><dt>参数</dt><dd>{{ selected.parameterVersionId }}</dd><dt>输入</dt><dd>{{ selected.inputBundleVersionId }}</dd><dt>汇流</dt><dd>{{ selected.checkpoint?.routingVersionId }}</dd></dl></details>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, Download, Refresh, Search, Star, StarFilled } from '@element-plus/icons-vue'
import { modelApi } from '@/services/modelBackendService'
import { checkpointCsv, filterStates, stateName } from '@/services/statePresentation'
import { stateScienceBlockReason as scientificStateBlockReason } from '@/services/scientificSelectionService'

const props = defineProps({ states: { type: Array, default: () => [] }, activeId: String, disabled: Boolean, modelHash: String, namedOnly:Boolean })
const emit = defineEmits(['select', 'continue', 'refresh', 'imported','view'])
const stateScienceBlockReason = state => scientificStateBlockReason(state, props.modelHash)
const fileInput = ref(null), canImport = ref(false), pendingImport = ref(null), importName = ref(''), importMessage = ref('')
async function readImport(event) {
  const file = event.target.files[0]
  event.target.value = ''; error.value = ''; importMessage.value = ''; pendingImport.value = null
  if (!file) return
  try {
    if (file.size > 1500000) throw new Error('状态文件不能超过 1.5 MB')
    const checkpoint = JSON.parse((await file.text()).replace(/^\uFEFF/, ''))
    if (checkpoint?.schemaVersion !== '2.0' || !Array.isArray(checkpoint.gridIds)) throw new Error('请选择平台导出的状态 JSON')
    pendingImport.value = checkpoint; importName.value = ''
  } catch (failure) { error.value = failure.message }
}
async function commitImport() {
  if (busy.value || props.disabled || !pendingImport.value) return
  busy.value = true; error.value = ''
  try {
    const result = await modelApi('states/import', { method: 'POST', body: { checkpoint: pendingImport.value, name: importName.value }, timeoutMs: 60000 })
    pendingImport.value = null; query.value = ''; strategy.value = ''; favorites.value = false
    metadata.value = (await modelApi('state-catalog')).metadata
    importMessage.value = result.reused ? '此状态已在库中，已选中' : '状态已导入，可用于续算'
    emit('imported', result.state.stateVersionId)
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
const metadata = ref({}), query = ref(''), strategy = ref(''), favorites = ref(false), busy = ref(false), error = ref(''), name = ref('')
const selected = computed(() => visible.value.find(row => row.id === props.activeId))
const nodeList = ref(null)
const savedName = computed(() => metadata.value[props.activeId]?.name || '')
const allNodes=ref(false)
const visible = computed(() => filterStates(props.states.filter(row=>!props.namedOnly||allNodes.value||Boolean(metadata.value[row.id]?.name?.trim())), metadata.value, { query: query.value, strategy: strategy.value, favorites: favorites.value }))
function revealSelection() {
  const list = nodeList.value, active = list?.querySelector('.active')
  if (!active) return
  const box = active.getBoundingClientRect(), frame = list.getBoundingClientRect()
  if (box.top < frame.top) list.scrollTop += box.top - frame.top
  else if (box.bottom > frame.bottom) list.scrollTop += box.bottom - frame.bottom
}
watch([() => props.activeId, visible], revealSelection, { flush: 'post' })
watch(visible,rows=>{if(props.namedOnly && rows.length && !rows.some(row=>row.id===props.activeId))emit('select',rows[0].id)})
watch([() => props.activeId, savedName], () => { name.value = savedName.value }, { immediate: true })
const number = (value, digits) => Number.isFinite(value) ? value.toFixed(digits) : '—'
async function refresh() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { const catalog = await modelApi('state-catalog'); metadata.value = catalog.metadata; canImport.value = catalog.checkpointImport === true; emit('refresh') }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function update(values) {
  if (busy.value || props.disabled || !selected.value) return
  const id = props.activeId, old = metadata.value[id] || { name: '', favorite: false, revision: 0 }
  busy.value = true; error.value = ''
  try { metadata.value = { ...metadata.value, [id]: await modelApi(`states/${encodeURIComponent(id)}/metadata`, { method: 'POST', body: { name: old.name, favorite: old.favorite, revision: old.revision, ...values } }) } }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
function saveName() { return update({ name: name.value }) }
function toggleFavorite() { return update({ favorite: !metadata.value[props.activeId]?.favorite }) }
async function download(format) {
  if (busy.value || !selected.value) return
  const id = props.activeId
  busy.value = true; error.value = ''
  try {
    const checkpoint = await modelApi(`states/${encodeURIComponent(id)}/checkpoint`)
    const contents = format === 'csv' ? checkpointCsv(checkpoint) : JSON.stringify(checkpoint, null, 2)
    const url = URL.createObjectURL(new Blob([contents], { type: format === 'csv' ? 'text/csv;charset=utf-8' : 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = `${id}.${format}`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
let listObserver
onMounted(() => {
  listObserver = new ResizeObserver(revealSelection)
  if (nodeList.value) listObserver.observe(nodeList.value)
  refresh()
})
onBeforeUnmount(() => listObserver?.disconnect())
</script>

<style scoped>
.import-review{display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#f0f6f2;border:1px solid #b8cebf;padding:14px;margin-bottom:14px;border-radius:5px;font-size:12px}.import-review .input{flex:1;min-width:180px}.import-review span{color:#597564}.state-library>p[role=status]{font-size:12px;color:#286849}
.state-library{padding:18px 20px;min-width:0;border-color:#dce4de;box-shadow:none}.library-header,.library-toolbar,.selection-actions,.name-editor{display:flex;align-items:center;gap:8px}.library-header{justify-content:space-between;margin-bottom:14px}.library-header h3{font-size:15px;margin:0;color:#254a3d}.library-header small{color:#7b8b82;font-size:11px;font-weight:400;margin-left:8px}.icon{width:15px;height:15px;flex-shrink:0}.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px}.library-toolbar{margin-bottom:14px;flex-wrap:wrap}.search{position:relative;flex:1;min-width:180px}.search .icon{position:absolute;top:50%;left:10px;transform:translateY(-50%);color:#839087}.search input{padding-left:32px;width:100%}.library-toolbar select{width:160px}.favorite-filter.selected{background:#edf5ef;border-color:#82a98f;color:#286849}.library-layout{display:grid;grid-template-columns:minmax(210px,0.85fr) minmax(0,1.4fr);gap:20px}.node-list{display:flex;flex-direction:column;gap:6px;max-height:220px;overflow:auto;min-width:0}.node-item{display:flex;align-items:center;justify-content:space-between;text-align:left;gap:8px;padding:10px 12px;min-height:58px;border:1px solid transparent;background:#f6f8f6;border-radius:4px;cursor:pointer;flex-shrink:0}.node-item.active{border-color:#76a18b;background:#eff6f0;box-shadow:inset 3px 0 #39795c}.node-item>span{display:grid;gap:5px;min-width:0}.node-item b{font-size:12px;color:#2b4739;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.node-item small{font-size:11px;color:#728277}.starred{color:#a78232}.state-selection{min-width:0;padding-left:20px;border-left:1px solid #e0e8e2}.name-editor .input{flex:1;min-width:0}.icon-btn{width:34px;padding:7px}.selection-metrics{display:flex;flex-wrap:wrap;gap:16px 28px;margin:20px 0}.selection-metrics>div{display:grid;gap:8px}.selection-metrics span{font-size:11px;color:#7a897f}.selection-metrics b{font-size:14px;color:#2c4a39;font-variant-numeric:tabular-nums}.selection-metrics b small{font-size:11px;font-weight:400}.selection-actions{flex-wrap:wrap}details{margin-top:14px;font-size:11px;color:#7a897f}summary{cursor:pointer}dl{display:grid;grid-template-columns:40px minmax(0,1fr);gap:6px}dd{margin:0;overflow-wrap:anywhere}.library-empty{padding:30px 12px;color:#87938b;font-size:12px;text-align:center}.library-error{font-size:12px;color:#a33d35}.input{max-width:100%}@media(max-width:760px){.state-library{padding:14px 12px}.library-layout{grid-template-columns:1fr;gap:16px}.state-selection{border-left:0;padding-left:0;border-top:1px solid #e0e8e2;padding-top:14px}.search{flex-basis:100%}.node-list{max-height:190px}}
</style>
