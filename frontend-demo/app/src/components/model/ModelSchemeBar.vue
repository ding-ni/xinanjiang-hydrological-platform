<template>
  <section class="scheme-bar" aria-label="计算方案">
    <div class="scheme-fields">
      <label>已保存方案<select class="input" :disabled="cal.running || busy" :value="cal.modelSchemeId || ''" @change="load($event.target.value)"><option value="">新方案草稿</option><option v-for="row in rows" :key="row.id" :value="row.id">{{ row.name }} · 修订 {{ row.revision }}</option></select></label>
      <label>方案名称<input class="input" v-model.trim="cal.modelSchemeName" :disabled="cal.running" maxlength="80" placeholder="请为本次水文方案命名" /></label>
      <button class="btn" :disabled="cal.running || busy" @click="create">新建方案</button>
      <button class="btn" :disabled="cal.running || busy || !cal.modelSchemeName" @click="$emit('save')">保存方案</button>
      <label v-if="revisions.length > 1">历史修订<select class="input" :value="cal.modelSchemeRevision" :disabled="cal.running || busy" @change="load(cal.modelSchemeId, Number($event.target.value))"><option v-for="revision in revisions" :key="revision.revision" :value="revision.revision">修订 {{ revision.revision }} · {{ revision.created_at }}</option></select></label>
    </div>
    <details class="source-settings"><summary>资料来源与降雨处理</summary>
      <div class="scheme-fields"><label>方案资料来源<select class="input" :value="app.selectedInputBundleVersionId" :disabled="cal.running" @change="source($event.target.value)"><option v-for="row in app.inputProducts" :key="row.bundleVersionId" :value="row.bundleVersionId">{{ row.name }}</option></select></label>
        <label>降雨输入<select class="input" v-model="cal.rainfallMethod" :disabled="cal.running"><option value="reuse">直接使用已有网格降雨</option><option value="stationIdw">站点雨量自动插值</option></select></label>
        <label v-if="cal.rainfallMethod === 'stationIdw'">站点资料来源<select class="input" v-model="cal.stationSourceId" :disabled="cal.running"><option :value="null">沿用当前资料的原始来源</option><option value="LOCAL_OBSERVATIONS">本地实测资料库</option><option v-for="row in sources" :key="row.sourceId" :value="row.sourceId">{{ row.name }}</option></select></label>
      </div>
      <details v-if="cal.rainfallMethod === 'stationIdw'"><summary>本方案插值条件</summary><div class="scheme-fields"><label>距离幂次<input class="input" type="number" min="1" max="4" step="0.1" v-model.number="cal.idwPower" /></label><label>最少有效雨量站<input class="input" type="number" min="1" max="32" v-model.number="cal.minimumStations" /></label></div></details>
    </details>
    <p v-if="error" role="alert" class="scheme-error">{{ error }}</p>
  </section>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { modelApi } from '@/services/modelBackendService'
import { restoreModelScheme } from '@/services/modelSchemeService'
import { useAppStore } from '@/stores/app'
import { useCalibrationStore } from '@/stores/calibration'
const app = useAppStore(), cal = useCalibrationStore(), rows = ref([]), revisions = ref([]), busy = ref(false), error = ref('')
const sources = ref([])
const emit = defineEmits(['save', 'loaded'])
async function refresh() { try { const [schemes, defaults] = await Promise.all([modelApi('model-schemes'), modelApi('preparation-defaults')]); rows.value = schemes.schemes; if (cal.modelSchemeId) revisions.value = (await modelApi(`model-schemes/${cal.modelSchemeId}`)).revisions; cal.idwPower ??= defaults.idwPower; cal.minimumStations ??= defaults.minimumStations } catch (failure) { error.value = failure.message } }
async function load(id, revision) { if (!id) return; busy.value = true; error.value = ''; try { const row = await modelApi(`model-schemes/${id}${revision ? `?revision=${revision}` : ''}`); revisions.value = row.revisions; await restoreModelScheme(cal, app, row); emit('loaded') } catch (failure) { error.value = failure.message } finally { busy.value = false } }
async function create() {
  cal.modelSchemeId = null; cal.modelSchemeRevision = 0; cal.modelSchemeLatestRevision = 0; cal.modelSchemeName = ''
  cal.schemeWindows = []; cal.stationSourceId = null; cal.runResult = null; cal.simulationResult = null; cal.activeTaskId = null
  cal.dirty = true; cal.running = false; cal.runStatus = 'needs-run'; cal.runMessage = ''
  cal.events = cal.events.map(event => ({ ...event, purpose: '未使用' }))
  cal.requiredInputBundleVersionId = null; cal.eventAssignment = null; cal.trialRecords = []
  cal.scienceVersionId = null; cal.scienceDescriptor = null; cal.candidateWarmupHours = null
  cal.statePolicy = cal.calibrationMode === 'continuous' && cal.operationMode !== 'simulation' ? 'continuous-origin' : 'independent-warmup'
  cal.continuousOrigin = null; cal.initialStateVersionId = null; cal.sourcePlan = null; cal.sourcePlanRequestTemplate = null
  if (cal.evaporationMode === 'science-version') cal.evaporationMode = 'monthly-climatology-assumption'
  revisions.value = []; cal.idwPower = null; cal.minimumStations = null
  await refresh(); cal.saveCurrentWorkspace(); error.value = ''; emit('loaded')
}
async function source(id) { error.value = ''; try { await app.selectInputBundleVersion(id); cal.requiredInputBundleVersionId = null; cal.sourcePlan = null; cal.saveCurrentWorkspace() } catch (failure) { error.value = failure.message } }
onMounted(async () => { await refresh(); try { sources.value = (await modelApi('input-sources')).sources } catch (failure) { error.value = failure.message } })
defineExpose({ refresh })
</script>
<style scoped>
.scheme-bar{margin:14px 0;padding:18px 20px;border:1px solid #d7e2de;background:#f4f8f6}.scheme-fields{display:flex;align-items:end;gap:12px;flex-wrap:wrap}.scheme-fields label{display:grid;gap:6px;flex:1 1 230px;font-size:12px;color:#486058}.scheme-fields .input{width:100%;min-width:0}.scheme-fields small{color:#65796d}.source-settings{margin-top:14px;font-size:12px}.source-settings summary{cursor:pointer;color:#477365}.source-settings .scheme-fields{margin-top:12px}.source-settings p{color:#6b7c74;line-height:1.7}.scheme-error{color:#ac3d3d}
</style>
