<template>
  <section class="spatial-results" aria-label="空间成果">
    <div class="product-heading"><div><span class="eyebrow">南安溪 / 徐畈</span><h3>地形与汇流分区</h3></div><a class="btn primary" :href="packageUrl" download>下载 GIS 成果包</a></div>
    <div class="product-metrics"><div><span>地形分辨率</span><b>{{ product.summary.cellSizeM }} <small>m</small></b></div><div><span>汇流单元</span><b>{{ product.summary.unitCount }} <small>个</small></b></div><div><span>地形覆盖</span><b>{{ number(product.summary.areaKm2) }} <small>km²</small></b></div><div><span>汇流单元面积</span><b>{{ number(product.summary.assignedAreaKm2) }} <small>km²</small></b></div></div>
    <div class="figure-tools"><nav aria-label="空间图件"><button v-for="row in figures" :key="row.id" type="button" :aria-pressed="selected === row.id" :class="{ selected: selected === row.id }" @click="selected = row.id">{{ row.label }}</button></nav><a class="btn" :href="imageUrl" :download="selected">下载当前图 PNG</a></div>
    <div class="spatial-figure"><img :key="imageUrl" :src="imageUrl" :alt="figureLabel" @error="imageError = true" @load="imageError = false" /><p v-if="imageError" role="alert">图件读取失败，请刷新后重试。</p></div>
    <details><summary>成果清单与空间参考</summary><dl><dt>栅格坐标</dt><dd>{{ product.summary.crs }} · 30 m · 北向首行</dd><dt>河网提取</dt><dd>D8 · 起始汇水面积 {{ product.summary.streamThresholdKm2 }} km²</dd><dt>有效地形</dt><dd>{{ product.summary.validCells.toLocaleString() }} 栅格，其中 {{ product.summary.unassignedCells }} 栅格未归属提取河网</dd></dl><p>GIS 包含原始高程、流向、汇水面积、河网和汇流分区栅格、河段及控制断面矢量、单元对照表、网格面积权重与校验清单。</p><p>栅格采用 ASC / PRJ；PNG 为带图例的排版图。</p></details>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
const props = defineProps({ taskId: { type: String, required: true }, product: { type: Object, required: true } })
const figures = [{ id: 'terrain-network.png', label: '地形与河网' }, { id: 'routing-units.png', label: '汇流单元分区' }]
const selected = ref(figures[0].id), imageError = ref(false)
const base = computed(() => `${String(import.meta.env.VITE_MODEL_API_BASE).replace(/\/$/, '')}/tasks/${encodeURIComponent(props.taskId)}`)
const packageUrl = computed(() => `${base.value}/spatial-package`)
const imageUrl = computed(() => `${base.value}/spatial-preview?name=${encodeURIComponent(selected.value)}`)
const figureLabel = computed(() => figures.find(row => row.id === selected.value)?.label)
const number = value => Number.isFinite(value) ? value.toFixed(3) : '—'
watch(imageUrl, () => { imageError.value = false })
</script>

<style scoped>
.spatial-results{min-width:0;border:1px solid #dce5d8;border-radius:6px;margin:16px 0;overflow:hidden;background:#fff}.product-heading{padding:20px 22px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.eyebrow{font-size:11px;letter-spacing:1px;color:#72866c}.product-heading h3{margin:5px 0 0;font-size:19px;color:#304f3b}.product-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:#e2e9de;border-block:1px solid #e2e9de}.product-metrics>div{background:#f6f8f3;padding:14px 20px}.product-metrics span{font-size:11px;color:#74826c;display:block;margin-bottom:7px}.product-metrics b{font-size:22px;color:#36513d;font-variant-numeric:tabular-nums;font-weight:600}.product-metrics small{font-size:11px;font-weight:400}.figure-tools{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 20px}.figure-tools nav{display:flex;gap:4px}.figure-tools nav button{border:0;background:none;color:#7c8776;padding:8px 10px;cursor:pointer;border-bottom:2px solid transparent}.figure-tools nav .selected{color:#286849;border-color:#286849;font-weight:600}.spatial-figure{background:#f1f4ed;padding:12px;text-align:center}.spatial-figure img{width:100%;max-width:1000px;height:auto;display:block;margin:auto}.spatial-figure p{color:#b43745}details{padding:16px 20px;font-size:12px;color:#71806b;line-height:1.7}summary{cursor:pointer}dl{display:grid;grid-template-columns:90px 1fr;gap:6px;margin-top:14px}dd{margin:0;color:#435e44}p{margin-bottom:0}@media(max-width:640px){.product-heading{padding:16px}.product-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.product-metrics>div{padding:12px 16px}.figure-tools{padding:12px}.spatial-figure{padding:0}.product-metrics b{font-size:20px}}
</style>
