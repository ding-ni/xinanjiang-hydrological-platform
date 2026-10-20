<template>
  <section class="grid-routing" aria-label="1 km产流网格汇流关系">
    <div class="relation-stats">
      <div><span>产流网格</span><b>{{ summary.gridCount }}<small>格</small></b></div>
      <div><span>D8 流向展示</span><b>{{ summary.d8EdgeCount }}<small>条</small></b></div>
      <div><span>固定面积权重</span><b>{{ summary.mappingRowCount }}<small>条</small></b></div>
      <div><span>局地汇点</span><b>{{ summary.localTerminalCount }}<small>格</small></b></div>
    </div>

    <div class="relation-layout">
      <div class="map-panel">
        <div class="map-tools">
          <label><input v-model="showD8" type="checkbox" /> 展示 D8 下游方向</label>
          <span class="legend-item"><i class="legend-cell" />产流网格</span>
          <span class="legend-item"><i class="legend-terminal" />无下游邻格</span>
          <span v-if="summary.fallbackGridCount" class="fallback-count">{{ summary.fallbackGridCount }} 格含最近邻挂接</span>
        </div>
        <svg class="grid-map" :viewBox="`0 0 ${width} ${height}`" role="group" aria-label="南安溪1公里格点及D8下游关系">
          <defs>
            <marker id="grid-flow-arrow" markerWidth="7" markerHeight="7" refX="5.5" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 z" fill="#285f52" />
            </marker>
          </defs>
          <g v-for="grid in plottedGrids" :key="grid.id" class="grid-feature" :class="{
            selected: selectedId === grid.id,
            terminal: !grid.downstreamGridId,
            fallback: hasFallback(grid),
          }" role="button" tabindex="0" :aria-pressed="selectedId === grid.id" :aria-label="`${grid.id}，高程 ${format(grid.elevationM, 1)} 米`"
            @click="selectedId = grid.id" @keydown.enter="selectedId = grid.id" @keydown.space.prevent="selectedId = grid.id">
            <polygon :points="grid.points" />
            <title>{{ grid.id }} · {{ format(grid.elevationM, 1) }} m</title>
          </g>
          <g v-if="showD8" class="flow-arrows" aria-hidden="true">
            <line v-for="edge in edges" :key="`${edge.from.id}-${edge.to.id}`"
              :class="{ 'selected-flow': edge.from.id === selectedId }"
              :x1="edge.from.x" :y1="edge.from.y" :x2="edge.to.x" :y2="edge.to.y" />
          </g>
          <g v-if="selectedPoint" class="selected-marker" aria-hidden="true">
            <circle :cx="selectedPoint.x" :cy="selectedPoint.y" r="3.5" />
            <text :x="selectedPoint.x + 7" :y="selectedPoint.y - 7">{{ selectedGrid.id }}</text>
          </g>
        </svg>
      </div>

      <aside class="grid-detail" aria-label="网格汇流详情">
        <label class="grid-picker">产流网格
          <select v-model="selectedId" class="input" aria-label="选择产流网格">
            <option v-for="grid in product.grids" :key="grid.id" :value="grid.id">{{ grid.id }}</option>
          </select>
        </label>
        <dl v-if="selectedGrid" class="grid-facts">
          <dt>网格高程</dt><dd>{{ format(selectedGrid.elevationM, 1) }} m</dd>
          <dt>网格面积</dt><dd>{{ format(selectedGrid.areaKm2, 4) }} km²</dd>
          <dt>D8 展示下游</dt><dd><button v-if="downstreamGrid" class="text-link" type="button" @click="selectedId = downstreamGrid.id">{{ downstreamGrid.id }} ↗</button><span v-else>无更低相邻格点</span></dd>
          <dt>面积权重合计</dt><dd>{{ format(selectedGrid.mapping.totalFraction, 6) }}</dd>
        </dl>
        <div class="mapping-list">
          <h4>挂接河段 <small>{{ selectedGrid?.mapping.links.length || 0 }}</small></h4>
          <div v-for="link in selectedGrid?.mapping.links || []" :key="`${link.reachId}-${link.unitId}`" class="mapping-row">
            <div><button type="button" class="text-link reach-link" :aria-label="`查看 ${link.reachId} 汇流单元参数`" @click="$emit('reach', link)">{{ link.reachId }} ↗</button><span>{{ link.unitId }}</span></div>
            <strong>{{ format(link.fraction, 4) }}</strong>
            <small>{{ format(link.areaKm2, 4) }} km²<span v-if="link.method === 'NEAREST_FALLBACK'"> · 最近邻</span></small>
          </div>
          <p v-if="!selectedGrid?.mapping.links.length" class="empty">暂无河段挂接</p>
        </div>
      </aside>
    </div>
    <p class="method-note">D8 箭头展示 1 km 相邻网格的下坡方向；模型实际按登记的固定面积权重将网格产流分配至汇流单元，再沿河段汇流。</p>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ product: { type: Object, required: true } })
defineEmits(['reach'])
const width = 780, height = 500, padding = 30
const selectedId = ref(props.product.grids[0]?.id || '')
const showD8 = ref(true)
const summary = computed(() => props.product.summary || {})
const selectedGrid = computed(() => props.product.grids.find(grid => grid.id === selectedId.value))
const downstreamGrid = computed(() => props.product.grids.find(grid => grid.id === selectedGrid.value?.downstreamGridId))
const projected = computed(() => {
  const points = props.product.grids.flatMap(grid => grid.polygon)
  if (!points.length) return {}
  const meanLat = points.reduce((sum, point) => sum + point[1], 0) / points.length
  const cosLat = Math.cos(meanLat * Math.PI / 180)
  const project = ([lon, lat]) => [lon * cosLat, lat]
  const xy = points.map(project)
  const minX = Math.min(...xy.map(point => point[0])), maxX = Math.max(...xy.map(point => point[0]))
  const minY = Math.min(...xy.map(point => point[1])), maxY = Math.max(...xy.map(point => point[1]))
  const scale = Math.min((width - padding * 2) / Math.max(maxX - minX, 1e-9), (height - padding * 2) / Math.max(maxY - minY, 1e-9))
  const offsetX = (width - (maxX - minX) * scale) / 2
  const offsetY = (height - (maxY - minY) * scale) / 2
  const convert = point => {
    const [x, y] = project(point)
    return [offsetX + (x - minX) * scale, height - offsetY - (y - minY) * scale]
  }
  return Object.fromEntries(props.product.grids.map(grid => [grid.id, {
    ...grid,
    points: grid.polygon.map(point => convert(point).map(value => value.toFixed(2)).join(',')).join(' '),
    point: convert(grid.center),
  }]))
})
const plottedGrids = computed(() => Object.values(projected.value))
const selectedPoint = computed(() => {
  const point = projected.value[selectedId.value]?.point
  return point ? { x: point[0], y: point[1] } : null
})
const edges = computed(() => props.product.grids.flatMap(grid => {
  const from = projected.value[grid.id]
  const to = projected.value[grid.downstreamGridId]
  return from && to ? [{ from: { id: grid.id, x: from.point[0], y: from.point[1] }, to: { id: to.id, x: to.point[0], y: to.point[1] } }] : []
}))
const hasFallback = grid => grid.mapping.links.some(link => link.method === 'NEAREST_FALLBACK')
const format = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—'

watch(() => props.product, product => {
  if (!product.grids.some(grid => grid.id === selectedId.value)) selectedId.value = product.grids[0]?.id || ''
})
</script>

<style scoped>
.flow-arrows,.selected-marker{pointer-events:none}.flow-arrows line.selected-flow{stroke-opacity:1;stroke-width:2.2}.grid-feature:focus-visible polygon{stroke:#193d2c;stroke-width:2.5;fill-opacity:.85}.grid-feature:focus{outline:none}.text-link:focus-visible{outline:2px solid #286849;outline-offset:3px}.mapping-row .reach-link{white-space:nowrap}
.grid-routing{min-width:0;border:1px solid #d8e2d8;background:#fff;color:#34483a}.relation-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:#dfe8df;border-bottom:1px solid #dfe8df}.relation-stats>div{padding:10px 12px;background:#f3f6f0;min-width:0}.relation-stats span{display:block;font-size:10px;color:#718174;white-space:nowrap}.relation-stats b{display:block;margin-top:4px;color:#315b43;font-size:17px;font-variant-numeric:tabular-nums}.relation-stats small{margin-left:4px;font-size:10px;font-weight:400;color:#738176}.relation-layout{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(190px,.75fr);min-width:0}.map-panel{min-width:0;border-right:1px solid #e1e8e1;background:#f8faf6}.map-tools{min-height:37px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:7px 10px;font-size:10px;color:#68776a}.map-tools label{display:flex;align-items:center;gap:5px;cursor:pointer}.map-tools input{accent-color:#357557}.legend-item{display:flex;align-items:center;gap:5px}.legend-cell,.legend-terminal{width:10px;height:10px;background:#a9c6a5;border:1px solid #5c8b6e}.legend-terminal{background:#e9cf9a;border-color:#b78a39}.fallback-count{margin-left:auto;color:#9a6923}.grid-map{display:block;width:100%;height:auto;max-height:390px;background:linear-gradient(145deg,#f8faf6,#eef3ed)}.flow-arrows line{stroke:#285f52;stroke-width:1.25;stroke-opacity:.62;marker-end:url(#grid-flow-arrow)}.grid-feature{cursor:pointer}.grid-feature polygon{fill:#9fbea0;fill-opacity:.45;stroke:#668a6b;stroke-width:.85;vector-effect:non-scaling-stroke;transition:fill .15s,stroke .15s}.grid-feature:hover polygon{fill:#8eb398;fill-opacity:.72}.grid-feature.terminal polygon{fill:#e5c985;fill-opacity:.52;stroke:#aa8645}.grid-feature.fallback polygon{stroke:#b97937;stroke-dasharray:2 2}.grid-feature.selected polygon{fill:#c37b3c;fill-opacity:.78;stroke:#874b1e;stroke-width:1.8}.selected-marker circle{fill:#813f1b;stroke:#fff;stroke-width:1.5}.selected-marker text{fill:#244331;font-size:12px;font-weight:700;paint-order:stroke;stroke:#f8faf6;stroke-width:3px}.grid-detail{min-width:0;padding:11px 12px;background:#fff}.grid-picker{display:grid;gap:5px;color:#778579;font-size:10px}.grid-picker select{height:32px;font-size:11px}.grid-facts{display:grid;grid-template-columns:1fr auto;gap:8px 10px;margin:12px 0;padding-bottom:12px;border-bottom:1px solid #e6ebe5;font-size:10px}.grid-facts dt{color:#758176}.grid-facts dd{margin:0;text-align:right;color:#3e5845;font-weight:600;font-variant-numeric:tabular-nums}.text-link{padding:0;border:0;background:none;color:#286849;font:inherit;font-weight:700;cursor:pointer}.mapping-list h4{display:flex;justify-content:space-between;margin:0 0 7px;font-size:11px;color:#405644}.mapping-list h4 small{color:#859087;font-weight:400}.mapping-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 8px;padding:7px 0;border-bottom:1px solid #edf0eb;font-size:10px}.mapping-row div{display:flex;gap:6px;align-items:baseline;min-width:0}.mapping-row b{color:#315b43}.mapping-row div span{color:#849087;overflow-wrap:anywhere}.mapping-row strong{grid-column:2;grid-row:1;color:#365b42;font-variant-numeric:tabular-nums}.mapping-row>small{grid-column:1 / -1;color:#849087;font-variant-numeric:tabular-nums}.empty{font-size:10px;color:#89948a}.method-note{margin:0;padding:7px 10px;border-top:1px solid #e1e8e1;color:#7a887b;font-size:10px;line-height:1.5;background:#f8faf6}
@media(max-width:640px){.relation-stats>div{padding:8px}.relation-stats span{font-size:9px}.relation-stats b{font-size:15px}.relation-layout{grid-template-columns:1fr}.map-panel{border-right:0;border-bottom:1px solid #e1e8e1}.grid-map{max-height:320px}.grid-detail{padding:10px}.mapping-list{max-height:170px;overflow:auto}.map-tools{gap:8px}}
</style>
