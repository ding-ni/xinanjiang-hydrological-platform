<template>
  <div v-if="resultId" class="result-save-bar" aria-label="成果命名保存"><div><span>{{ saved.name ? '已保存成果' : '计算已完成，尚未命名保存' }}</span><b v-if="saved.name">{{ saved.name }}</b></div><button class="btn" :disabled="loading" @click="open">{{ saved.name ? '修改成果名称' : '保存成果' }}</button><RouterLink class="btn" :to="{path:'/archive',query:{result:resultId}}">查看成果</RouterLink><span v-if="error" class="error" role="alert">{{ error }}</span>
    <dialog ref="dialog" class="save-result-dialog"><form @submit.prevent="save"><header><h3>{{ saved.name ? '修改成果名称' : '保存成果' }}</h3><button type="button" class="btn" :disabled="loading" @click="dialog.close()">取消</button></header><label>成果名称<input v-model.trim="name" class="input" maxlength="80" required aria-label="成果名称" /></label><label>备注（可不填）<textarea v-model="notes" class="input" maxlength="2000" rows="3" aria-label="成果备注" /></label><p v-if="error" class="error" role="alert">{{ error }}</p><button class="btn primary" :disabled="loading || !name">保存成果</button></form></dialog>
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import { modelApi } from '@/services/modelBackendService'
const props = defineProps({ resultId: String, suggestion: { type: String, default: '水文计算成果' } })
const emit = defineEmits(['saved'])
const saved = ref({}), name = ref(''), notes = ref(''), loading = ref(false), error = ref(''), dialog = ref(null)
let sequence = 0
watch(() => props.resultId, async id => { const token = ++sequence; saved.value = {}; error.value = ''; if (!id) return; loading.value = true; try { const row = await modelApi('result-metadata/' + encodeURIComponent(id)); if (sequence === token) saved.value = row } catch (failure) { if (sequence === token) error.value = failure.message } finally { if (sequence === token) loading.value = false } }, { immediate: true })
function open() { name.value = saved.value.name || props.suggestion; notes.value = saved.value.notes || ''; error.value = ''; dialog.value.showModal() }
async function save() {
  const id = props.resultId
  loading.value = true; error.value = ''
  try { const row = await modelApi('result-metadata/' + encodeURIComponent(id), { method: 'POST', body: { name: name.value, notes: notes.value, collection: saved.value.collection || '', favorite: saved.value.favorite || false, revision: saved.value.revision || 0 } }); if (id === props.resultId) { saved.value = row; dialog.value.close(); emit('saved', row) } }
  catch (failure) { error.value = failure.message }
  finally { loading.value = false }
}
</script>
<style scoped>
.result-save-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 0;font-size:14px}.result-save-bar>div{display:grid;gap:6px;margin-right:auto}.result-save-bar span{color:#71877d}.result-save-bar b{font-size:18px;color:#2a6059}.save-result-dialog{width:min(500px,92vw);border:1px solid #b6ccc6;border-radius:8px;padding:24px;color:#34574e}.save-result-dialog::backdrop{background:#123d3555}.save-result-dialog form,.save-result-dialog label{display:grid;gap:12px}.save-result-dialog header{display:flex;align-items:center;justify-content:space-between}.save-result-dialog input{min-height:38px}.error{color:#a34636!important}
</style>
