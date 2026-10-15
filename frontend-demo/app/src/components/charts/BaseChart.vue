<template>
  <div ref="el" class="chart-host" />
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, watch, shallowRef } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, required: true },
  autoresize: { type: Boolean, default: true }
})

const el = ref(null)
const emit = defineEmits(['select-time'])
const chart = shallowRef(null)
let ro = null

function render() {
  if (!chart.value) return
  chart.value.setOption(props.option, { notMerge: true })
}

onMounted(() => {
  chart.value = echarts.init(el.value)
  chart.value.on('click', event => emit('select-time', event.dataIndex))
  render()
  if (props.autoresize) {
    ro = new ResizeObserver(() => chart.value?.resize())
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
