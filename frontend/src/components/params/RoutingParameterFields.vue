<template>
  <section class="routing-parameter-fields">
    <p v-if="!parameters" class="loading-parameters" role="status">{{ error || '正在读取汇流参数…' }}</p>
    <template v-else>
      <fieldset :disabled="disabled">
        <ParamTuner v-if="kind==='slope'" :params="parameters.default_slope_k_hours" :defs="slopeDefs" title="坡面汇流参数" :live-preview="false" @update="updateSlope" @reset="resetSlope" />
        <template v-else>
          <label v-if="unstable.length" class="routing-warning"><input v-model="problemsOnly" type="checkbox" /> 仅看问题河段（{{ unstable.length }}）</label>
          <label class="reach-picker">河段<select v-model="reachId" class="input" aria-label="查看河段参数"><option v-for="id in visibleReaches" :key="id" :value="id">{{ reachName(id) }}</option></select></label>
          <ParamTuner v-if="reachId" :params="parameters.reach_muskingum[reachId]" :defs="channelDefs" title="马斯京根汇流参数" :live-preview="false" @update="updateChannel" @reset="resetChannel" />
          <div class="river-parameter-table"><table class="table"><thead><tr><th>河段</th><th>K · h</th><th>X</th><th>1小时计算</th></tr></thead><tbody><tr v-for="id in visibleReaches" :key="id" :class="{selected:reachId===id}" @click="reachId=id"><td>{{ reachName(id) }}</td><td>{{ format(parameters.reach_muskingum[id].k_hours) }}</td><td>{{ format(parameters.reach_muskingum[id].x) }}</td><td>{{ routingIsStable(parameters.reach_muskingum[id])?'可用':violation(parameters.reach_muskingum[id]) }}</td></tr></tbody></table></div>
          <p class="routing-note">K为河道传播时间，X为马斯京根权重。自动率定可整体调整各河段K并搜索统一X；这里可逐段修改。</p>
        </template>
      </fieldset>
    </template>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import ParamTuner from './ParamTuner.vue'
import { routingIsStable } from '@/services/routingPresentation'
const props=defineProps({parameters:Object,baseline:Object,kind:{type:String,default:'channel'},disabled:Boolean,error:String})
const emit=defineEmits(['changed'])
const reachId=ref(''),reaches=computed(()=>Object.keys(props.parameters?.reach_muskingum||{}))
const problemsOnly=ref(false)
const visibleReaches=computed(()=>problemsOnly.value?reaches.value.filter(id=>!routingIsStable(props.parameters.reach_muskingum[id])):reaches.value)
watch(visibleReaches,ids=>{if(!ids.includes(reachId.value))reachId.value=ids.includes('R0020_UP')?'R0020_UP':ids[0]||''},{immediate:true})
function reachName(id){return id==='R0020_UP'?'徐畈出口上游河段':'河段 '+String(reaches.value.indexOf(id)+1).padStart(2,'0')}
function violation(row){return 2*row.k_hours*row.x>1?'2KX > 1':2*row.k_hours*(1-row.x)<1?'2K(1−X) < 1':'参数值无效'}
const slopeDefs=['RS','RI','RG'].map((key,i)=>({key,symbol:key,label:['地表径流滞时','壤中流滞时','地下径流滞时'][i],unit:'h',min:.05,max:720,step:.05}))
const channelDefs=[{key:'k_hours',symbol:'K',label:'河道传播时间',unit:'h',min:.5,max:720,step:.01},{key:'x',symbol:'X',label:'马斯京根权重',unit:'',min:0,max:.5,step:.01}]
const unstable=computed(()=>Object.values(props.parameters?.reach_muskingum||{}).filter(row=>!routingIsStable(row)))
const format=value=>Number.isFinite(value)?Number(value.toPrecision(6)):'缺失'
function updateSlope(key,value){props.parameters.default_slope_k_hours[key]=Number(value);emit('changed')}
function updateChannel(key,value){props.parameters.reach_muskingum[reachId.value][key]=Number(value);emit('changed')}
function resetSlope(){if(props.baseline?.default_slope_k_hours)Object.assign(props.parameters.default_slope_k_hours,JSON.parse(JSON.stringify(props.baseline.default_slope_k_hours)));emit('changed')}
function resetChannel(){if(props.baseline?.reach_muskingum?.[reachId.value])Object.assign(props.parameters.reach_muskingum[reachId.value],JSON.parse(JSON.stringify(props.baseline.reach_muskingum[reachId.value])));emit('changed')}
</script>
<style scoped>
.routing-parameter-fields fieldset{border:0;padding:0;margin:0;min-width:0}.reach-picker{display:grid;gap:8px;font-size:14px;margin-bottom:16px}.reach-picker select{min-height:36px;font-size:14px}.river-parameter-table{max-height:230px;overflow:auto;margin-top:18px}.table{font-size:14px}.table th{position:sticky;top:0;background:#f0f6f3}.table tr{cursor:pointer}.table tr.selected{background:#e7f2ee}.routing-note,.routing-warning,.loading-parameters{font-size:14px;line-height:1.7;color:#70867b}.routing-warning{color:#a15c30}.routing-parameter-fields :deep(.num){min-height:36px;font-size:14px}.routing-parameter-fields :deep(.param-meta label),.routing-parameter-fields :deep(.hint-line),.routing-parameter-fields :deep(.unit){font-size:14px}
</style>
