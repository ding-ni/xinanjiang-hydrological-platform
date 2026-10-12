<template>
  <details class="submission-plan">
    <summary>提交条件与初始方案 <span>{{ rows.filter(row => row.active).length }} 项搜索</span></summary>
    <dl><dt>评价预算</dt><dd>{{ budget }} 次 · {{ originalSource ? '沿用原始种群；无效参数组计入预算' : '当前方案重新评价计入预算' }}</dd><dt>实际预热</dt><dd>{{ actualWarmupHours }} 小时<span v-if="science?.statePolicy?.warmupHours != null && science.statePolicy.warmupHours !== actualWarmupHours"> · 科学预设 {{ science.statePolicy.warmupHours }} 小时，沿用原任务实际值</span></dd><dt>状态起点</dt><dd>{{ statePolicy === 'continuous-origin' ? '连续原点，一条完整轨迹' : '每个物理洪水组独立预热' }}</dd><dt v-if="statePolicy === 'continuous-origin'">连续原点</dt><dd v-if="statePolicy === 'continuous-origin'">{{ continuousOrigin || '按所选资料目录首年与预热期解析' }}</dd><dt v-if="requiredInput">候选资料要求</dt><dd v-if="requiredInput">{{ requiredInput }}</dd><dt>蒸散发</dt><dd>{{ petLabel }}</dd><dt>科学版本</dt><dd>{{ science?.scienceVersionId || '提交时登记当前明确条件' }}</dd><dt>模型摘要</dt><dd>{{ science?.modelImplementationSHA256 || '提交时冻结' }}</dd><dt>科学摘要</dt><dd>{{ science?.scienceConfigurationSHA256 || '提交时冻结' }}</dd><dt>PET 摘要</dt><dd>{{ science?.petConfigurationSHA256 || '提交时冻结' }}</dd><dt>汇流方案</dt><dd>{{ routingVersion }}</dd><dt v-if="groups">物理洪水组</dt><dd v-if="groups">{{ groups.groupCount }} 组 · {{ groups.windowCount }} 个窗口 · {{ groups.uniqueHours }} 个唯一小时</dd></dl>
    <p>先合并重叠窗口，每组使用一条轨迹；目标按组内唯一小时计算，各组等权。</p>
    <div class="plan-table"><table aria-label="提交前参数搜索范围"><thead><tr><th>参数</th><th>方式</th><th>起始值</th><th>搜索范围</th></tr></thead><tbody><tr v-for="row in rows" :key="`${row.scope}:${row.key}`"><td>{{ row.symbol || row.key }}</td><td>{{ row.active ? '搜索' : '固定' }}</td><td>{{ number(row.initial) }}</td><td>{{ row.active ? `${number(row.minimum)}–${number(row.maximum)}` : '当前方案' }}</td></tr></tbody></table></div>
  </details>
</template>
<script setup>
defineProps({ rows: { type: Array, default: () => [] }, budget: Number, statePolicy: String,
  petLabel: String, science: Object, routingVersion: String, groups: Object, continuousOrigin: String, requiredInput: String, actualWarmupHours: Number, originalSource: Boolean })
const number = value => Number.isFinite(value) ? Number(value.toPrecision(6)) : '各河段原值'
</script>
<style scoped>
.submission-plan{margin-top:12px;border:1px solid #cfdfdd;border-radius:5px;background:#f5faf8;padding:9px;color:#2d5f59;font-size:11px}.submission-plan summary{cursor:pointer;font-weight:700}.submission-plan summary span{float:right;font-size:10px;font-weight:400}dl{display:grid;grid-template-columns:62px minmax(0,1fr);gap:7px;margin:12px 0}dt{color:#6b817c}dd{margin:0;overflow-wrap:anywhere;line-height:1.4}.submission-plan p{font-size:10px;color:#647e75;line-height:1.5}.plan-table{overflow:auto;max-height:260px}table{width:100%;border-collapse:collapse;font-size:10px;font-variant-numeric:tabular-nums}th,td{padding:6px;text-align:right;white-space:nowrap;border-bottom:1px solid #dce7e2}th:first-child,td:first-child{text-align:left}th{background:#eaf3ed}
</style>
