<template>
  <div class="report-sheet" id="forecast-report-print">
    <header class="r-head">
      <div>
        <h1>洪水预报成果单</h1>
        <p class="sub">分布式新安江模型 · 预报作业成果</p>
      </div>
      <div class="r-meta">
        <div><span>编号</span><b>{{ data.id }}</b></div>
        <div><span>状态</span><b>{{ data.status }}</b></div>
      </div>
    </header>

    <section class="r-block">
      <h2>一、预报对象</h2>
      <div class="grid2">
        <div><span>控制断面</span><b>{{ data.section }}</b></div>
        <div><span>起报时间</span><b>{{ data.start }}</b></div>
        <div><span>预见期</span><b>{{ data.leadHours }} 小时</b></div>
        <div><span>预报类型</span><b>{{ data.type || '实时报' }}</b></div>
      </div>
    </section>

    <section class="r-block highlight">
      <h2>二、主要结果</h2>
      <div class="grid2 big">
        <div><span>预报洪峰流量</span><b class="hot">{{ Number(data.peak).toLocaleString() }} m³/s</b></div>
        <div><span>峰现时间</span><b>{{ data.peakTime }}</b></div>
        <template v-if="isModelResult">
          <div><span>预报时段水量</span><b>{{ (data.volume / 1e4).toFixed(2) }} 万 m³</b></div>
          <div><span>累计面雨量</span><b>{{ totalRain.toFixed(1) }} mm</b></div>
        </template>
        <template v-else>
          <div><span>峰水位（估）</span><b>{{ data.peakLevel ?? '—' }} m</b></div>
          <div><span>预警等级</span><b :class="levelCls">{{ data.level || '—' }}</b></div>
          <div><span>警戒水位</span><b>{{ data.warnLevel ?? '—' }} m</b></div>
          <div><span>保证水位</span><b>{{ data.ensureLevel ?? '—' }} m</b></div>
        </template>
      </div>
    </section>

    <section class="r-block">
      <h2>三、预报依据</h2>
      <div class="grid2">
        <template v-if="isModelResult">
          <div><span>起报实测资料</span><b>{{ data.inputSources?.observation?.id }}</b></div>
          <div><span>预报降雨</span><b>{{ data.inputSources?.precipitation?.label }}</b></div>
          <div><span>降雨版本</span><b>{{ data.inputSources?.precipitation?.id }}</b></div>
          <div><span>产流参数版本</span><b>{{ data.param }}</b></div>
          <div><span>汇流方案</span><b>{{ data.routingVersionId }}</b></div>
          <div><span>计算尺度</span><b>{{ data.modelScale }}</b></div>
          <div><span>蒸散发</span><b>逐月给定值</b></div>
        </template>
        <template v-else>
          <div><span>实测降水资料</span><b>{{ modelProject.forcing.observedNc }}</b></div>
          <div><span>预报降水资料</span><b>{{ data.rainQpfSource || modelProject.forcing.forecastNc }}</b></div>
          <div><span>模型方案</span><b>{{ modelProject.version }}</b></div>
          <div><span>计算尺度</span><b>{{ modelProject.spatial.grid }} · {{ modelProject.spatial.subbasins }} 子流域</b></div>
          <div><span>状态续算</span><b>{{ modelProject.checkpoint.catchup }}</b></div>
        </template>
        <div><span>起报状态</span><b>{{ data.state }}</b></div>
      </div>
    </section>

    <section class="r-block">
      <h2>四、作业记录</h2>
      <div class="grid2">
        <div><span>作业员</span><b>{{ data.operator || '—' }}</b></div>
        <div><span>发布时间</span><b>{{ data.publishedAt || '—' }}</b></div>
      </div>
      <p class="foot-note">本成果单由当前预报作业生成，时间均为北京时间。</p>
    </section>

    <footer class="r-actions no-print">
      <button class="btn primary" type="button" @click="print">打印 / 另存 PDF</button>
      <button class="btn" type="button" @click="$emit('close')">关闭</button>
    </footer>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { modelProject } from '@/data/modelProject'

const props = defineProps({
  data: { type: Object, required: true }
})

defineEmits(['close'])

const isModelResult = computed(() => props.data.resultSource === 'model-backend')
const totalRain = computed(() => (props.data.hydrograph?.forcing?.precipitation || []).reduce((sum, value) => sum + value, 0))

const levelCls = computed(() => {
  const lv = props.data.level
  if (lv === '警戒' || lv === '保证') return 'bad'
  if (lv === '关注') return 'warn'
  return 'ok'
})

function print() {
  window.print()
}
</script>

<style scoped>
.report-sheet {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px 28px;
  background: #fff;
  color: #173052;
  border: 1px solid #d4e0ef;
  border-radius: 12px;
  box-shadow: var(--shadow-soft);
}

.r-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 2px solid #0b57c7;
}

.r-head h1 {
  margin: 0;
  font-size: 22px;
  letter-spacing: 0.06em;
}

.sub {
  margin: 6px 0 0;
  color: #6a7c95;
  font-size: 12px;
}

.r-meta {
  text-align: right;
  font-size: 13px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.r-meta span {
  color: #6a7c95;
  margin-right: 8px;
}

.r-block {
  margin-top: 18px;
}

.r-block h2 {
  margin: 0 0 10px;
  font-size: 15px;
  color: #0b57c7;
}

.r-block.highlight {
  padding: 12px 14px;
  border-radius: 10px;
  background: linear-gradient(180deg, #fff8f0, #fff);
  border: 1px solid #f0d2b0;
}

.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 16px;
}

.grid2 > div {
  display: grid;
  gap: 2px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.grid2 span {
  color: #6a7c95;
  font-size: 12px;
}

.grid2 b {
  font-size: 14px;
}

.grid2.big b {
  font-size: 16px;
}

.hot {
  color: #f26c18;
  font-size: 20px !important;
}

.ok {
  color: #15924d;
}
.warn {
  color: #c45a10;
}
.bad {
  color: #d93025;
}

.force {
  margin: 0;
  padding: 10px 12px;
  background: #fff3e8;
  border-radius: 8px;
  color: #8a4b12;
  font-size: 13px;
}

.foot-note {
  margin: 14px 0 0;
  color: #9aabbf;
  font-size: 11px;
}

.r-actions {
  display: flex;
  gap: 10px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #e8f0fa;
}

@media (max-width: 600px) {
  .report-sheet { padding: 16px; }
  .r-head { flex-direction: column; }
  .r-meta { text-align: left; }
  .grid2 { grid-template-columns: 1fr; }
}

@media print {
  .no-print {
    display: none !important;
  }
  .report-sheet {
    box-shadow: none;
    border: 0;
    max-width: none;
  }
}
</style>
