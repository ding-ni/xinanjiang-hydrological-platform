<template>
  <div ref="el" class="chart-host" />
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch, shallowRef } from 'vue'
import * as echarts from 'echarts'
import { responsiveChartOption } from '@/services/chartLayout'

const props = defineProps({
  option: { type: Object, required: true },
  autoresize: { type: Boolean, default: true }
})

const el = ref(null)
const emit = defineEmits(['select-time'])
const chart = shallowRef(null)
let ro = null
let zoom = null, identity = '', updating = false

function render() {
  if (!chart.value) return
  const axes=Array.isArray(props.option.xAxis)?props.option.xAxis:[props.option.xAxis]
  const nextIdentity=JSON.stringify(axes.map(axis=>[axis?.data?.length,axis?.data?.[0],axis?.data?.at(-1)]))
  if(nextIdentity!==identity){zoom=null;identity=nextIdentity}
  updating=true
  chart.value.setOption(responsiveChartOption(props.option,el.value.clientWidth,zoom), { notMerge: true })
  updating=false
}

onMounted(() => {
  chart.value = echarts.init(el.value)
  chart.value.on('click', event => emit('select-time', event.dataIndex))
  chart.value.on('datazoom', event=>{if(updating)return;const selected=event.batch?.[0]||event;const current=chart.value.getOption().dataZoom?.[0];zoom={start:selected.start??current?.start??0,end:selected.end??current?.end??100};render()})
  render()
  if (props.autoresize) {
    ro = new ResizeObserver(() => {chart.value?.resize();render()})
    ro.observe(el.value)
  }
})

onBeforeUnmount(() => {
  ro?.disconnect()
  chart.value?.dispose()
  chart.value = null
})

watch(() => props.option, render, { deep: true })
</script>

<style scoped>
.chart-host {
  width: 100%;
  height: 100%;
  min-height: 120px;
}
</style>
