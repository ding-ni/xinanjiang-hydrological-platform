<template>
  <div class="routing-workbench" :class="{ 'query-only': queryOnly }">
    <form v-if="!queryOnly" class="version-bar" @submit.prevent="$emit('register')">
      <label class="version-field">当前方案的汇流参数<select class="input" :value="editVersion" :disabled="disabled" @change="requestVersion"><option v-for="row in versions" :key="row.routingVersionId" :value="row.routingVersionId">{{ row.name }}</option></select></label>
      <button type="button" class="btn" :disabled="disabled || dirty || editVersion === selectedVersionId || !parameters" @click="$emit('select')"><Check class="tool-icon" />选用此版本</button>
      <label class="name-field">新版本名称<input class="input" :value="name" :disabled="disabled" maxlength="120" required placeholder="例如：徐畈洪水汇流方案" @input="$emit('update:name', $event.target.value)" /></label>
      <button class="btn primary" :disabled="disabled || !parameters"><Plus class="tool-icon" />登记新版本</button>
    </form>
    <div v-if="!queryOnly" class="workbench-status"><span :class="dirty ? 'draft' : 'saved'">{{ dirty ? '参数已修改 · 待登记' : editVersion === selectedVersionId ? '当前计算方案' : '已登记方案' }}</span><span>{{ units.length }} 个汇流单元</span><span>{{ reaches.length }} 条河段</span><span>{{ overrideCount }} 个单元采用独立 K</span><div class="status-tools"><button class="btn icon-button" title="恢复当前版本参数" aria-label="恢复当前版本参数" :disabled="disabled || !dirty" @click="$emit('reset')"><RefreshLeft /></button><button class="btn icon-button" title="导出汇流参数 JSON" aria-label="导出汇流参数 JSON" :disabled="!parameters" @click="$emit('export', dirty ? { name, parentVersionId: editVersion, parameters, draft: true } : versions.find(row => row.routingVersionId === editVersion))"><Download /></button><button class="btn icon-button" title="导出空间产品 JSON" aria-label="导出空间产品 JSON" :disabled="!spatial" @click="$emit('spatial-export')"><MapLocation /></button></div></div>
    <div class="routing-layout">
      <div class="map-workspace">
          <div ref="mapHost" class="topology-map" aria-label="真实流域拓扑地图"></div>
          <div class="map-caption"><span><i class="legend river" />南安溪河网</span><span><i class="legend unit" />汇流单元出口</span><span><i class="legend control" />徐畈断面</span><button class="btn sm" :disabled="!pointCoordinates" @click="$emit('point', pointCoordinates)"><Location class="tool-icon" />{{ queryOnly ? '选用此位置' : '查询选定点' }}</button></div>
          <div v-if="pointCoordinates" class="point-coordinate" role="status">选定点 {{ pointCoordinates[0].toFixed(6) }}° E / {{ pointCoordinates[1].toFixed(6) }}° N</div>
      </div>
      <div v-if="!queryOnly" class="parameter-workspace">
        <div class="parameter-heading"><h3>汇流参数</h3><span>逐小时</span></div>
        <div class="parameter-modes" role="tablist" aria-label="汇流参数分区"><button v-for="mode in modes" :key="mode.id" role="tab" :aria-selected="parameterMode === mode.id" :class="{ active: parameterMode === mode.id }" @click="parameterMode = mode.id">{{ mode.label }}</button></div>
        <fieldset v-if="parameters" :disabled="disabled" class="parameter-fields">
          <div v-show="parameterMode === 'global'" class="global-parameters">
            <h4>全流域默认坡面滞时</h4>
            <label v-for="component in slopeComponents" :key="component.key" class="slope-row"><span>{{ component.label }}<small>{{ component.key }}</small></span><div class="numeric-field"><input v-model.number="parameters.default_slope_k_hours[component.key]" class="input" type="number" min="0.05" max="720" step="0.05" :aria-label="`${component.key} 坡面 K（h）`" required /><span>h</span></div></label>
            <div class="parameter-facts"><div><span>控制断面</span><b>徐畈</b></div><div><span>产流网格</span><b>{{ spatial?.geometry.features.length || 0 }} 个</b></div><div><span>模型尺度</span><b>1 km</b></div><div><span>独立单元参数</span><b>{{ overrideCount }} / {{ units.length }}</b></div></div>
          </div>
          <div v-show="parameterMode === 'units'" class="unit-parameters">
            <label class="search-field"><Search class="tool-icon" /><input v-model="unitSearch" class="input" aria-label="检索汇流单元" placeholder="单元或河段编号" /></label>
            <div class="unit-selector"><select v-model="selectedUnitId" class="input" size="5" aria-label="选择汇流单元"><option v-for="row in filteredUnits" :key="row.unit_id" :value="row.unit_id">{{ row.reach_id }} · {{ format(row.area_km2) }} km²{{ parameters.unit_component_slope_k_hours[row.unit_id] ? ' · 独立 K' : '' }}</option></select></div>
            <div v-if="selectedUnit" class="unit-editor">
              <div class="unit-heading"><h4>{{ selectedUnit.reach_id }}</h4><span>{{ format(selectedUnit.area_km2) }} km²</span><button type="button" class="btn icon-button" aria-label="单元恢复全域参数" title="单元恢复全域参数" :disabled="!parameters.unit_component_slope_k_hours[selectedUnitId]" @click="resetUnit"><RefreshLeft /></button></div>
              <code class="unit-id">{{ selectedUnitId }}</code>
              <div v-for="component in slopeComponents" :key="component.key" class="unit-component">
                <label class="override-toggle"><input type="checkbox" :checked="hasOverride(component.key)" :aria-label="`${component.label}独立 K`" @change="setUnitOverride(parameters, selectedUnitId, component.key, $event.target.checked)" /><span>{{ component.label }}<small>{{ hasOverride(component.key) ? '独立 K' : '采用全域 K' }}</small></span></label>
                <div class="numeric-field"><input class="input" type="number" min="0.05" max="720" step="0.05" :value="effectiveSlopeK(parameters, selectedUnitId, component.key)" :disabled="!hasOverride(component.key)" :aria-label="`${selectedUnitId} ${component.key} K`" @input="updateUnitK(component.key, $event.target.value)" /><span>h</span></div>
              </div>
            </div><p v-else class="empty">没有匹配的汇流单元</p>
          </div>
          <div v-show="parameterMode === 'reaches'" class="reach-parameters">
            <label class="search-field"><Search class="tool-icon" /><input v-model="reachSearch" class="input" aria-label="检索河段" placeholder="河段或下游编号" /></label>
            <div class="table-scroll"><table class="table" aria-label="河段汇流参数"><thead><tr><th>河段 / 下游</th><th>K（h）</th><th>X</th><th></th></tr></thead><tbody><tr v-for="row in filteredReaches" :key="row.reach_id" :class="{ selected: selectedReach === row.reach_id, unstable: !routingIsStable(parameters.reach_muskingum[row.reach_id]) }"><td><button type="button" class="reach-name" @click="focusReach(row.reach_id)">{{ row.reach_id }}</button><small>→ {{ row.downstream_reach_id || '出口' }} · {{ format(row.length_km) }} km</small></td><td><input v-model.number="parameters.reach_muskingum[row.reach_id].k_hours" class="input compact" type="number" min="0.5" max="720" step="0.01" :aria-label="`${row.reach_id} K`" /></td><td><input v-model.number="parameters.reach_muskingum[row.reach_id].x" class="input compact" type="number" min="0" max="0.5" step="0.01" :aria-label="`${row.reach_id} X`" /></td><td><button type="button" class="btn icon-button" :aria-label="`${row.reach_id} 末端点查询`" title="末端点查询" @click="$emit('point', row.endLonLat)"><Location /></button></td></tr><tr v-if="!filteredReaches.length"><td colspan="4">没有匹配的河段</td></tr></tbody></table></div>
            <p v-if="unstableCount" role="status" class="stability-warning">{{ unstableCount }} 条河段不满足逐小时稳定条件：2KX ≤ 1 ≤ 2K(1−X)</p>
          </div>
        </fieldset>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Check, Download, Location, MapLocation, Plus, RefreshLeft, Search } from '@element-plus/icons-vue'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { effectiveSlopeK, filterRoutingRows, routingIsStable, routingMapCoordinates, setUnitOverride, slopeComponents } from '@/services/routingPresentation'

const props = defineProps({ spatial: Object, queryOnly: Boolean, queriedPoint: Array, parameters: Object, versions: { type: Array, default: () => [] }, editVersion: String, selectedVersionId: String, name: String, disabled: Boolean, visible: Boolean })
const emit = defineEmits(['version', 'update:name', 'register', 'select', 'reset', 'export', 'spatial-export', 'point'])
const modes = [{ id: 'global', label: '全域默认' }, { id: 'units', label: '汇流单元' }, { id: 'reaches', label: '河段参数' }]
const parameterMode = ref('global'), unitSearch = ref(''), reachSearch = ref(''), selectedUnitId = ref(''), selectedReach = ref(''), mapHost = ref(null), pointCoordinates = ref(null)
let map = null, disposed = false, drawRevision = 0
const units = computed(() => props.spatial?.topology.units || [])
const reaches = computed(() => props.spatial?.topology.reaches || [])
const filteredUnits = computed(() => filterRoutingRows(units.value, unitSearch.value, ['unit_id', 'reach_id']))
const filteredReaches = computed(() => filterRoutingRows(reaches.value, reachSearch.value, ['reach_id', 'downstream_reach_id']))
const selectedUnit = computed(() => filteredUnits.value.find(row => row.unit_id === selectedUnitId.value))
const dirty = computed(() => !!props.parameters && JSON.stringify(props.parameters) !== JSON.stringify(props.versions.find(row => row.routingVersionId === props.editVersion)?.parameters))
const overrideCount = computed(() => Object.keys(props.parameters?.unit_component_slope_k_hours || {}).length)
const unstableCount = computed(() => Object.values(props.parameters?.reach_muskingum || {}).filter(row => !routingIsStable(row)).length)
const format = value => Number.isFinite(value) ? value.toFixed(2) : '—'
function hasOverride(component) { return Object.hasOwn(props.parameters?.unit_component_slope_k_hours?.[selectedUnitId.value] || {}, component) }
function updateUnitK(component, value) { if (hasOverride(component)) props.parameters.unit_component_slope_k_hours[selectedUnitId.value][component] = value === '' ? null : Number(value) }
function resetUnit() { delete props.parameters.unit_component_slope_k_hours[selectedUnitId.value] }
function requestVersion(event) { emit('version', event.target.value); event.target.value = props.editVersion }
function drawSelectedPoint() {
  map?.getSource('selected')?.setData(pointCoordinates.value
    ? { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: pointCoordinates.value } }
    : { type: 'FeatureCollection', features: [] })
}
function focusReach(id) {
  selectedReach.value = id
  const row = reaches.value.find(item => item.reach_id === id)
  if (!row) return
  pointCoordinates.value = row.endLonLat
  drawSelectedPoint()
}
function focusGridReach(link) {
  unitSearch.value = ''
  selectedUnitId.value = link.unitId
  parameterMode.value = 'units'
  focusReach(link.reachId)
}
watch(pointCoordinates, drawSelectedPoint)
watch(() => props.queriedPoint, coordinates => {
  if (props.queryOnly || coordinates) pointCoordinates.value = coordinates || null
})
watch(filteredUnits, rows => { if (!rows.some(row => row.unit_id === selectedUnitId.value)) selectedUnitId.value = rows[0]?.unit_id || '' }, { immediate: true })
watch(selectedUnit, row => { if (row && !props.queryOnly) focusReach(row.reach_id) })
async function drawMap() {
  const token = ++drawRevision
  map?.remove()
  map = null
  await nextTick()
  if (disposed || token !== drawRevision || !props.spatial || !mapHost.value) return
  const product = props.spatial
  map = new maplibregl.Map({ container: mapHost.value, style: { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#f3f6f3' } }] }, center: product.topology.reaches[0].startLonLat, zoom: 11 })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  const current = map
  current.on('load', () => {
    if (disposed || map !== current) return
    for (const [name, data, color] of [['basin', product.basin, '#759b79'], ['rivers', product.rivers, '#2797ba'], ['grid', product.geometry, '#c2cbc2']]) {
      current.addSource(name, { type: 'geojson', data })
      current.addLayer({ id: name, type: 'line', source: name, paint: { 'line-color': color, 'line-width': name === 'rivers' ? 2 : 1 } })
    }
    current.addSource('selected', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
    const nodes = { type: 'FeatureCollection', features: product.topology.reaches.map(row => ({ type: 'Feature', properties: { reach: row.reach_id }, geometry: { type: 'Point', coordinates: row.endLonLat } })) }
    current.addSource('nodes', { type: 'geojson', data: nodes })
    current.addLayer({ id: 'nodes', type: 'circle', source: 'nodes', paint: { 'circle-color': ['case', ['==', ['get', 'reach'], 'R0020_UP'], '#bc4149', '#376b57'], 'circle-radius': ['case', ['==', ['get', 'reach'], 'R0020_UP'], 7, 4], 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' } })
    current.addLayer({ id: 'selected', type: 'circle', source: 'selected', paint: { 'circle-color': '#fff', 'circle-opacity': 0.3, 'circle-radius': 10, 'circle-stroke-width': 3, 'circle-stroke-color': '#bd821f' } })
    const bounds = new maplibregl.LngLatBounds()
    routingMapCoordinates(product).forEach(point => bounds.extend(point))
    const fitExtent = () => current.fitBounds(bounds, { padding: 32, duration: 0 })
    fitExtent()
    current.on('resize', fitExtent)
    current.on('click', event => {
      const hits = current.queryRenderedFeatures(event.point, { layers: ['nodes'] })
      if (hits.length) {
        const id = hits[0].properties.reach
        unitSearch.value = ''
        selectedUnitId.value = units.value.find(row => row.reach_id === id)?.unit_id || ''
        focusReach(id)
        parameterMode.value = 'units'
      } else pointCoordinates.value = [Number(event.lngLat.lng.toFixed(7)), Number(event.lngLat.lat.toFixed(7))]
      drawSelectedPoint()
      if (props.queryOnly) emit('point', pointCoordinates.value)
    })
    current.on('mouseenter', 'nodes', () => { current.getCanvas().style.cursor = 'pointer' })
    current.on('mouseleave', 'nodes', () => { current.getCanvas().style.cursor = '' })
    if (!props.queryOnly) focusReach(selectedReach.value || selectedUnit.value?.reach_id)
    drawSelectedPoint()
  })
}
watch(() => props.spatial, drawMap, { immediate: true })
watch(() => props.visible, () => nextTick(() => map?.resize()))
onBeforeUnmount(() => { disposed = true; drawRevision++; map?.remove() })
</script>

<style scoped>
.query-only .routing-layout{grid-template-columns:1fr}.query-only .workbench-status{display:none}
.point-coordinate{font-size:11px;color:#786638;font-variant-numeric:tabular-nums;line-height:1.5;overflow-wrap:anywhere}
.routing-workbench{min-width:0}.version-bar{display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin-bottom:14px}.version-field{flex:1 1 230px}.name-field{flex:1 1 210px}label{display:flex;flex-direction:column;gap:7px;font-size:12px;color:#58655b;min-width:0}.input{border-color:#d1dad2;color:#28382d}.tool-icon{width:16px;height:16px;flex-shrink:0}.workbench-status{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:12px 0;font-size:12px;color:#69766c;border-top:1px solid #e1e7e1}.saved{color:#287156;font-weight:700}.draft{color:#a16514;font-weight:700}.status-tools{display:flex;gap:6px;margin-left:auto}.icon-button{width:32px;height:32px;min-height:32px;padding:7px;flex-shrink:0}.icon-button svg{width:16px;height:16px}.routing-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,440px);gap:24px;min-width:0}.map-workspace,.parameter-workspace{min-width:0}.map-workspace{display:flex;flex-direction:column}.topology-map{width:100%;height:478px;border:1px solid #d4ddd4;background:#f3f6f3}.map-caption{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:10px 0;color:#637066;font-size:11px}.map-caption .btn{margin-left:auto}.legend{display:inline-block;width:8px;height:8px;margin-right:6px}.legend.river{background:#2797ba;height:3px;vertical-align:middle}.legend.unit{background:#376b57;border-radius:50%}.legend.control{background:#bc4149;border-radius:50%}.parameter-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.parameter-heading h3{font-size:15px;margin:0;color:#263b2e}.parameter-heading>span{font-size:11px;color:#7d897f}.parameter-modes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));background:#eef2ed;padding:3px;gap:3px;border-radius:5px;margin-bottom:16px}.parameter-modes button{padding:8px 4px;border:0;background:transparent;color:#5c6b5f;cursor:pointer;font-size:12px;border-radius:3px}.parameter-modes button.active{background:#fff;color:#216a4b;font-weight:700;box-shadow:0 1px 3px #243c2915}.parameter-fields{margin:0;padding:0;border:0;min-width:0}.global-parameters h4,.unit-heading h4{font-size:13px;margin:0 0 16px;color:#344c3a}.slope-row{display:flex;flex-direction:row;align-items:center;justify-content:space-between;padding:13px 0;border-bottom:1px solid #e8ede8;font-size:13px;color:#344c3a}.slope-row small{margin-left:10px;font-size:11px;color:#809087}.numeric-field{display:flex;align-items:center;gap:8px;width:142px}.numeric-field>span{color:#819086;font-size:11px}.numeric-field .input{font-variant-numeric:tabular-nums;width:118px}.input:disabled{background:#f4f6f3;color:#7b867e}.parameter-facts{display:grid;grid-template-columns:1fr 1fr;gap:20px 16px;padding-top:30px;font-size:12px}.parameter-facts>div{display:grid;gap:8px}.parameter-facts span{color:#7c8a7e}.parameter-facts b{font-weight:600;color:#364c3d}.search-field{position:relative;margin-bottom:12px}.search-field .input{padding-left:32px}.search-field .tool-icon{position:absolute;top:10px;left:10px;color:#869789}.unit-selector select{height:110px;padding:4px}.unit-selector option{padding:6px;font-size:12px}.unit-heading{display:flex;align-items:center;gap:14px;margin-top:16px}.unit-heading h4{margin:0}.unit-heading>span{font-size:12px;color:#7b887e}.unit-heading .btn{margin-left:auto}.unit-id{display:block;font-size:10px;color:#8b968d;margin:5px 0 14px;overflow-wrap:anywhere}.unit-component{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid #e8ede8}.override-toggle{flex-direction:row;align-items:center;gap:8px;font-size:12px;color:#34513e}.override-toggle input{accent-color:#297658;margin:0}.override-toggle small{display:block;font-size:10px;color:#859288;margin-top:4px}.table-scroll{max-height:365px;overflow:auto}.table{font-size:12px}.table th{position:sticky;top:0;background:#f0f4ee;padding:10px 6px;color:#586b5c;z-index:1}.table td{padding:8px 6px}.table small{display:block;font-size:10px;color:#8a958d;margin-top:4px;white-space:nowrap}.table .compact{width:70px;height:32px;padding:0 5px;font-size:12px}.reach-name{border:0;padding:0;background:none;color:#325e48;font-weight:600;font-size:12px;cursor:pointer}.table tr.selected td{background:#edf5ee}.table tr.unstable td{background:#fff4e3}.stability-warning{font-size:11px;color:#a15f18;line-height:1.6}.empty{font-size:12px;color:#8a948a;padding:20px 0}@media(min-width:1650px){.routing-layout{grid-template-columns:minmax(0,1fr) 460px}.topology-map{height:520px}}@media(max-width:1000px){.routing-layout{grid-template-columns:1fr}.topology-map{height:340px}.parameter-workspace{padding-top:14px;border-top:1px solid #e1e7e1}.table-scroll{max-height:360px}}@media(max-width:600px){.version-bar{gap:10px}.version-bar label{flex-basis:100%}.version-bar>.btn{flex:1 1 auto}.workbench-status{gap:8px 12px}.status-tools{margin-left:0}.topology-map{height:300px}.map-caption{gap:8px;font-size:10px}.map-caption .btn{margin-left:0}.routing-layout{gap:12px}.table .compact{width:63px}.numeric-field{width:124px}.numeric-field .input{width:100px}}
.map-view-switch{display:flex;gap:3px;align-self:flex-start;margin:0 0 9px;padding:3px;border:1px solid #dce5dc;background:#edf2ec;border-radius:4px}.map-view-switch button{padding:7px 11px;border:0;border-radius:3px;background:transparent;color:#617164;font-size:11px;cursor:pointer}.map-view-switch button.active{background:#fff;color:#25674a;font-weight:700;box-shadow:0 1px 3px #263d2c18}.map-view-switch button:disabled{opacity:.5;cursor:not-allowed}@media(max-width:600px){.map-view-switch{max-width:100%}.map-view-switch button{padding:7px 8px;font-size:10px}}
</style>
