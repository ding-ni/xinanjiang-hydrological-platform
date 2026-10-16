<template><div><p>这里保存你命名的续算状态，可查看来源、改名、导出或用于继续模拟。</p><StateLibrary :states="state.domainInitialStates" :active-id="state.activeInitialState.id" :model-hash="app.backendCase?.modelImplementationSHA256" named-only @select="state.loadInitialState($event)" @continue="continueState" @view="view" @refresh="load" /><p v-if="error" role="alert">{{ error }}</p></div></template>
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
  const processCatalogs=new Map()
  await Promise.all((state.initialStates||[]).map(async row=>{
    const result=results.find(item=>item.id===row.sourceTaskId||item.sourceTaskIds?.includes(row.sourceTaskId))
    if(!result)return
    row.sourceResultName=result.name;row.sourceResultId=result.id
    if(!processCatalogs.has(result.id))processCatalogs.set(result.id,modelApi('result-catalog/'+result.id+'/processes'))
    const catalog=await processCatalogs.get(result.id)
    if(key!==app.contextKey)return
    const process=(catalog.processes||[]).find(item=>item.endStateVersionId===row.id&&item.end===row.time)||(catalog.processes||[]).find(item=>item.endStateVersionId===row.id)
    if(process){row.processName=process.name;row.sourceProcessId=process.id}
  }))
}catch(failure){error.value=failure.message}}
function continueState(){const node=state.activeInitialState;if(node.sourceTaskId)router.push({path:'/simulation',query:{source:node.sourceTaskId,continue:'1',label:node.name||'状态库末状态'}});else router.push({path:'/state/advanced',query:{state:node.id}})}
function view(){const node=state.activeInitialState;if(node.sourceResultId)router.push({path:'/state',query:{...(node.sourceResultId.startsWith('OP-')?{operation:node.sourceResultId}:{source:node.sourceResultId}),event:node.sourceProcessId,label:node.sourceResultName}});else if(node.sourceTaskId)router.push({path:'/state',query:{source:node.sourceTaskId}});else router.push({path:'/state/advanced',query:{state:node.id}})}
watch(()=>app.contextKey,()=>{state.setContext(app.basinId,app.modelScale,app.modelConfigId);load()},{immediate:true})
</script>
<style scoped>details{margin:20px 0;font-size:14px;color:#607d70}summary{cursor:pointer}p{line-height:1.7}</style>
