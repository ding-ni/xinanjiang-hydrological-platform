<template>
  <div v-if="resultId" class="result-save-bar" aria-label="成果整理"><div><span>计算记录已保存</span><b>{{ saved.name || suggestion }}</b></div><button class="btn" :disabled="loading" @click="open">✎ 修改名称</button><button class="btn" :disabled="loading" :aria-pressed="Boolean(saved.favorite)" @click="toggleFavorite">{{ saved.favorite?'★ 已收藏':'☆ 收藏' }}</button><RouterLink v-if="!hideRecordLink" class="btn" :to="{path:'/archive',query:{result:resultId}}">查看计算记录</RouterLink><span v-if="error" class="error" role="alert">{{ error }}</span>
    <HydrologyDialog ref="dialog" width="min(540px, 94vw)"><form class="save-result-form" @submit.prevent="save"><header><h3>{{ saved.name ? '修改计算记录' : '命名计算记录' }}</h3><button type="button" class="btn" :disabled="loading" @click="dialog.close()">取消</button></header><p class="dialog-note">计算过程和数值已经保存；这里仅整理显示名称、备注和收藏状态。</p><label>计算记录名称<input v-model.trim="name" class="input" maxlength="80" required aria-label="计算记录名称" /></label><label>备注（可不填）<textarea v-model="notes" class="input" maxlength="2000" rows="3" aria-label="计算记录备注" /></label><label class="favorite"><input v-model="favorite" type="checkbox" /> 收藏到“我的成果”</label><p v-if="error" class="error" role="alert">{{ error }}</p><button class="btn primary" :disabled="loading || !name">保存整理</button></form></HydrologyDialog>
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import { modelApi } from '@/services/modelBackendService'
import HydrologyDialog from './HydrologyDialog.vue'
const props = defineProps({ resultId: String, hideRecordLink:Boolean, suggestion: { type: String, default: '水文计算成果' } })
const emit = defineEmits(['saved'])
const saved = ref({}), name = ref(''), notes = ref(''), favorite = ref(false), loading = ref(false), error = ref(''), dialog = ref(null)
let sequence = 0
watch(() => props.resultId, async id => { const token = ++sequence; saved.value = {}; error.value = ''; if (!id) return; loading.value = true; try { const row = await modelApi('result-metadata/' + encodeURIComponent(id)); if (sequence === token) saved.value = row } catch (failure) { if (sequence === token) error.value = failure.message } finally { if (sequence === token) loading.value = false } }, { immediate: true })
function open() { name.value = saved.value.name || props.suggestion; notes.value = saved.value.notes || ''; favorite.value = Boolean(saved.value.favorite); error.value = ''; dialog.value.showModal() }
async function toggleFavorite(){name.value=saved.value.name||props.suggestion;notes.value=saved.value.notes||'';favorite.value=!saved.value.favorite;await save()}
async function save() {
  const id = props.resultId
  loading.value = true; error.value = ''
  try { const row = await modelApi('result-metadata/' + encodeURIComponent(id), { method: 'POST', body: { name: name.value, notes: notes.value, collection: saved.value.collection || '', favorite: favorite.value, revision: saved.value.revision || 0 } }); if (id === props.resultId) { saved.value = row; dialog.value.close(); emit('saved', row) } }
  catch (failure) { error.value = failure.message }
  finally { loading.value = false }
}
</script>
<style scoped>
.result-save-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 0;font-size:14px}.result-save-bar>div{display:grid;gap:6px;margin-right:auto}.result-save-bar span{color:#71877d}.result-save-bar b{font-size:18px;color:#2a6059}.save-result-form,.save-result-form label{display:grid;gap:12px;color:#34574e}.save-result-form header{display:flex;align-items:center;justify-content:space-between}.save-result-form input{min-height:38px}.error{color:#a34636!important}
.dialog-note{color:#71877d;font-size:13px;line-height:1.6}.favorite{display:flex!important;align-items:center;gap:8px!important}.favorite input{min-height:auto!important}
</style>
