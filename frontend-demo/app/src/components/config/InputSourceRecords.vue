<template>
  <section class="source-records"><h2>资料来源与处理记录</h2><p>这里供来源核查和历史追溯。选择资料、划分洪水及自动准备输入，在<RouterLink to="/calibration">率定与模拟</RouterLink>中完成。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <table class="table"><thead><tr><th>资料名称</th><th>内容</th><th>处理方式</th><th>查询</th></tr></thead><tbody><tr v-for="row in rows" :key="row.bundleVersionId"><td>{{ row.name }}</td><td>网格降雨 · 出口流量 · 洪水目录</td><td>{{ ({ fixedBaseline: '导入网格资料', mdbImport: 'MDB资料导入', schemeInput: '方案自动准备', stationIdw: '站点降雨插值', qcWindow: '时段提取' })[row.operation] || row.operation }}</td><td><button class="btn mini" @click="selected = selected === row ? null : row">{{ selected === row ? '收起来源' : '来源详情' }}</button></td></tr></tbody></table>
    <details v-if="selected" open><summary>{{ selected.name }} · 来源详情</summary><p>{{ selected.sourceLabel }}</p><p v-if="selected.recordQueryScope">提取时段：{{ selected.recordQueryScope.start }} 至 {{ selected.recordQueryScope.end }}</p><p v-if="selected.sourceOrigin">原始资料：{{ selected.sourceOrigin.originalName || selected.sourceOrigin.type }}</p><p v-for="note in selected.qualityNotes || []" :key="note">{{ note }}</p><small>资料编号：{{ selected.bundleVersionId }}</small></details>
  </section>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { modelApi } from '@/services/modelBackendService'
const rows = ref([]), selected = ref(null), error = ref('')
onMounted(async () => { try { rows.value = (await modelApi('input-products')).versions } catch (failure) { error.value = failure.message } })
</script>
<style scoped>.source-records{overflow:auto}.source-records h2{font-size:18px}.source-records p{font-size:13px;line-height:1.8;color:#66786e}.source-records details{margin-top:18px;font-size:12px}.source-records small{overflow-wrap:anywhere}</style>
