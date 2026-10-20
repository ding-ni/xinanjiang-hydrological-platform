<template>
  <section class="source-records"><h2>历史库入库记录</h2><RouterLink class="btn" to="/config">流域与站网</RouterLink>
    <p>这里只记录实际入库的原文件、记录数与导入时间。同事优选洪水在项目方案中管理；计算输入快照随方案与成果保存。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="records-scroll"><table class="table"><thead><tr><th>原始文件</th><th>内容</th><th>记录数</th><th>导入时间</th><th>详情</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td>{{ row.path }}</td><td>{{ ({rain:'雨量',river:'水位与流量',events:'洪水目录',stations:'站点'})[row.category] || row.category }}</td><td>{{ row.records?.toLocaleString() }}</td><td>{{ row.importedAt?.replace('T',' ').slice(0,19) }}</td><td><button class="btn mini" @click="selected = selected === row ? null : row">文件详情</button></td></tr></tbody></table></div>
    <details v-if="selected" open><summary>{{ selected.path }}</summary><p>原文件编号：{{ selected.id }}</p><small>SHA256：{{ selected.sha256 }}</small></details>
  </section>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { modelApi } from '@/services/modelBackendService'
const rows = ref([]), selected = ref(null), error = ref('')
onMounted(async () => { try { rows.value = (await modelApi('database/sources')).sources } catch (failure) { error.value = failure.message } })
</script>
<style scoped>.source-records{min-width:0}.records-scroll{overflow:auto;max-height:55vh}.table th{position:sticky;top:0;background:#f2f6f4}.source-records h2{font-size:18px}.source-records p{font-size:13px;line-height:1.8;color:#66786e}.source-records details{margin-top:18px;font-size:12px}.source-records small{overflow-wrap:anywhere}</style>
