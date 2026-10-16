<template><div><StateLibrary :states="state.domainInitialStates" :active-id="state.activeInitialState.id" :model-hash="app.backendCase?.modelImplementationSHA256" named-only @select="state.loadInitialState($event)" @continue="continueState" @view="view" @refresh="load" /><p v-if="error" role="alert">{{ error }}</p><details><summary>高级操作</summary><p>独立初始化、导入与全部状态节点。</p><RouterLink class="btn" to="/state/advanced">打开高级状态操作</RouterLink></details></div></template>
<script setup>
import {watch,ref} from 'vue'
import {useRouter} from 'vue-router'
import {useStateStore} from '@/stores/state'
import {useAppStore} from '@/stores/app'
import {modelApi} from '@/services/modelBackendService'
import StateLibrary from './StateLibrary.vue'
const state=useStateStore(),app=useAppStore(),router=useRouter(),error=ref('')
async function load(){try{
  const key=app.contextKey
  const [saved,catalog]=await Promise.allSettled([modelApi('states'),modelApi('result-catalog')])
  if(key!==app.contextKey)return
  if(saved.status==='rejected')throw saved.reason
  state.hydrateBackendStates(saved.value.states)
  const results=catalog.status==='fulfilled'?catalog.value.results:[]
  for(const row of state.initialStates||[]){const result=results.find(item=>item.id===row.sourceTaskId||item.sourceTaskIds?.includes(row.sourceTaskId));if(result)row.sourceResultName=result.name}
}catch(failure){error.value=failure.message}}
function continueState(){const node=state.activeInitialState;if(node.sourceTaskId)router.push({path:'/simulation',query:{source:node.sourceTaskId,continue:'1',label:node.name||'状态库末状态'}});else router.push({path:'/state/advanced',query:{state:node.id}})}
function view(){const node=state.activeInitialState;if(node.sourceTaskId)router.push({path:'/state',query:{source:node.sourceTaskId}});else router.push({path:'/state/advanced',query:{state:node.id}})}
watch(()=>app.contextKey,()=>{state.setContext(app.basinId,app.modelScale,app.modelConfigId);load()},{immediate:true})
</script>
<style scoped>details{margin:20px 0;font-size:14px;color:#607d70}summary{cursor:pointer}p{line-height:1.7}</style>
