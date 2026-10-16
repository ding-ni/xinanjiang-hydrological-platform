<template>
  <section class="routing-defaults"><header><div><h2>河网与汇流配置</h2><p>这里保存新方案的默认值。已有方案和成果使用各自保存的参数；修改方案请进入参数率定。</p></div><RouterLink to="/calibration" class="btn">打开率定方案</RouterLink></header>
    <p v-if="error" class="error" role="alert">{{ error }} <button class="btn" @click="load">重新读取</button></p><p v-if="loading" role="status">正在读取默认汇流参数…</p>
    <div class="routing-defaults-layout"><div><RoutingWorkbench v-if="spatial" :spatial="spatial" query-only visible /><p v-else>{{ mapError || '正在读取河网…' }}</p></div><section v-if="parameters"><div class="kinds"><button class="btn" :class="{primary:kind==='slope'}" @click="kind='slope'">坡面汇流</button><button class="btn" :class="{primary:kind==='channel'}" @click="kind='channel'">河道汇流</button></div><RoutingParameterFields :parameters="parameters" :baseline="record?.parameters" :kind="kind" :disabled="saving" /><div class="actions"><button class="btn primary" :disabled="saving || !dirty" @click="save">保存新方案默认值</button><button class="btn" :disabled="saving || !dirty" @click="reset">放弃修改</button></div><p v-if="message" role="status">{{ message }}</p></section></div>
  </section>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { modelApi } from '@/services/modelBackendService'
import RoutingWorkbench from '@/components/model/RoutingWorkbench.vue'
import RoutingParameterFields from '@/components/params/RoutingParameterFields.vue'
const record=ref(null),parameters=ref(null),spatial=ref(null),kind=ref('slope'),loading=ref(false),saving=ref(false),error=ref(''),mapError=ref(''),message=ref('')
const dirty=computed(()=>record.value&&JSON.stringify(parameters.value)!==JSON.stringify(record.value.parameters))
function reset(){parameters.value=JSON.parse(JSON.stringify(record.value.parameters))}
async function load(){loading.value=true;error.value='';try{record.value=await modelApi('database/routing-defaults');reset()}catch(failure){error.value=failure.message}finally{loading.value=false}}
async function save(){saving.value=true;error.value='';try{record.value=await modelApi('database/routing-defaults',{method:'POST',body:{parameters:parameters.value,revision:record.value.revision}});reset();message.value='已保存，之后新建方案采用此值。'}catch(failure){error.value=failure.message}finally{saving.value=false}}
onMounted(()=>{load();modelApi('spatial-products?bundleVersionId=HISTORICAL_SPATIAL_V1',{timeoutMs:60000}).then(row=>spatial.value=row).catch(failure=>mapError.value=failure.message)})
</script>
<style scoped>
.routing-defaults header{display:flex;justify-content:space-between;gap:20px}.routing-defaults h2{font-size:20px;margin:0 0 12px}.routing-defaults p{font-size:14px;line-height:1.7;color:#647f75}.routing-defaults-layout{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(300px,1fr);gap:24px;margin-top:20px}.kinds,.actions{display:flex;gap:8px;margin:0 0 20px}.actions{margin-top:20px}.error{color:#a74435!important}@media(max-width:1200px){.routing-defaults-layout{grid-template-columns:1fr}}
</style>
