<template>
  <div v-if="resultId" class="result-save-bar" aria-label="成果整理">
    <div><span>{{ retention?.retention==='saved'?'成果已保存':retention?.retention==='trashed'?'已移入回收站':'临时计算结果' }}</span><b>{{ saved.name || suggestion }}</b></div>
    <button v-if="retention?.retention!=='saved' && retention?.retention!=='trashed'" class="btn primary" :disabled="loading" @click="retain('save')">保存成果</button>
    <button v-if="retention?.retention==='trashed'" class="btn" :disabled="loading" @click="retain('restore')">恢复计算</button><button v-if="retention?.retention==='trashed'" class="btn" :disabled="loading" @click="purge">从平台回收站移除</button>
    <template v-else><button class="btn" :disabled="loading" @click="open">修改名称</button><button class="btn" :disabled="loading || retention?.retention!=='saved'" :aria-pressed="Boolean(saved.favorite)" @click="toggleFavorite">{{ saved.favorite?'★ 已收藏':'☆ 收藏' }}</button><button class="btn" :disabled="loading" @click="discard">{{ retention?.retention==='saved'?'移入回收站':'丢弃本次计算' }}</button></template>
    <RouterLink v-if="!hideRecordLink" class="btn" :to="{path:'/archive',query:{result:resultId}}">查看计算记录</RouterLink><span v-if="error" class="error" role="alert">{{ error }}</span>
    <HydrologyDialog ref="dialog" width="min(540px,94vw)"><form class="save-result-form" @submit.prevent="save"><header><h3>整理计算记录</h3><button type="button" class="btn" @click="dialog.close()">取消</button></header><p>名称与备注不改变计算数值；保存参数、保存成果和收藏分别管理。</p><label>计算记录名称<input v-model.trim="name" class="input" maxlength="80" required /></label><label>备注<textarea v-model="notes" class="input" maxlength="2000" rows="3" /></label><button class="btn primary" :disabled="loading || !name">保存名称与备注</button></form></HydrologyDialog>
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { modelApi } from '@/services/modelBackendService'
import { changeRetention, trackTemporary, retentionChanged } from '@/services/resultRetentionService'
import HydrologyDialog from './HydrologyDialog.vue'
import { cleanupConfirmation, previewResultCleanup, executeResultCleanup } from '@/services/artifactCleanupService'
const props=defineProps({resultId:String,hideRecordLink:Boolean,suggestion:{type:String,default:'水文计算成果'}})
const emit=defineEmits(['saved','discarded'])
const saved=ref({}),retention=ref(null),name=ref(''),notes=ref(''),loading=ref(false),error=ref(''),dialog=ref(null)
let sequence=0
async function refresh(){const id=props.resultId,token=++sequence;if(!id)return;loading.value=true;error.value='';try{const [meta,state]=await Promise.all([modelApi('result-metadata/'+encodeURIComponent(id)),modelApi('result-retention/'+encodeURIComponent(id))]);if(token!==sequence)return;saved.value=meta;retention.value=state;if(state.retention==='temporary'&&!state.legacy){const payload=await modelApi('result-catalog');const row=payload.results.find(row=>row.id===id);if(row)trackTemporary(row)}}catch(failure){if(token===sequence)error.value=failure.message}finally{if(token===sequence)loading.value=false}}
watch(()=>props.resultId,()=>{saved.value={};retention.value=null;refresh()},{immediate:true})
watch(retentionChanged,refresh)
function open(){name.value=saved.value.name||props.suggestion;notes.value=saved.value.notes||'';dialog.value.showModal()}
async function retain(action){const id=props.resultId;loading.value=true;error.value='';try{const row=await changeRetention(id,action,retention.value?.revision||0);if(id!==props.resultId)return;retention.value=row;emit('saved',{...saved.value,retention:row.retention});if(action==='discard')emit('discarded',id)}catch(failure){error.value=failure.message}finally{loading.value=false}}
async function discard(){try{await ElMessageBox.confirm('将此计算移入回收站，可恢复。已保存参数和被后续计算引用的资料保留。','丢弃计算',{confirmButtonText:'移入回收站',cancelButtonText:'取消'});await retain('discard')}catch(failure){if(failure instanceof Error)error.value=failure.message}}
async function purge(){const id=props.resultId;loading.value=true;error.value='';try{const plan=await previewResultCleanup(id);await ElMessageBox.confirm(cleanupConfirmation(plan),'预览回收站清理',{confirmButtonText:'确认移入 Windows 回收站',cancelButtonText:'取消',customClass:'artifact-cleanup-confirm'});await executeResultCleanup(id,plan);emit('discarded',id)}catch(failure){if(failure instanceof Error)error.value=failure.message}finally{loading.value=false}}
async function toggleFavorite(){loading.value=true;try{saved.value=await modelApi('result-metadata/'+encodeURIComponent(props.resultId),{method:'POST',body:{name:saved.value.name||props.suggestion,notes:saved.value.notes||'',collection:saved.value.collection||'',favorite:!saved.value.favorite,revision:saved.value.revision||0}});emit('saved',saved.value)}catch(failure){error.value=failure.message}finally{loading.value=false}}
async function save(){loading.value=true;try{saved.value=await modelApi('result-metadata/'+encodeURIComponent(props.resultId),{method:'POST',body:{name:name.value,notes:notes.value,collection:saved.value.collection||'',favorite:Boolean(saved.value.favorite),revision:saved.value.revision||0}});dialog.value.close();emit('saved',saved.value)}catch(failure){error.value=failure.message}finally{loading.value=false}}
</script>
<style scoped>.result-save-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 0;font-size:14px}.result-save-bar>div{display:grid;gap:6px;margin-right:auto}.result-save-bar span{color:#71877d}.result-save-bar b{font-size:18px;color:#2a6059}.save-result-form,.save-result-form label{display:grid;gap:12px;color:#34574e}.save-result-form header{display:flex;justify-content:space-between}.error{color:#a34636!important}p{color:#71877d;line-height:1.7}</style>
