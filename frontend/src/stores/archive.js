import { defineStore } from 'pinia'
import { simulateHydrograph, sectionBasePeak, DEFAULT_PARAMS } from '@/models/xajParamEngine'
import { DEFAULT_BASIN_ID, normalizeBasinId, resolveDomainContext } from '@/data/domainContext'
import { FORECAST_PRODUCT_FIXTURES } from '@/fixtures/runs/forecast-products'

function normalizedDomainId(value) {
  return normalizeBasinId(value)
}

function normalizedModelScale(value) {
  return value === '0p1deg' ? '0p1deg' : '1km'
}

/**
 * 依据流域与空间尺度确定模型配置键。
 */
export function modelConfigIdFor(domainId = 'xinanjiang', modelScale = '1km') {
  return resolveDomainContext(normalizedDomainId(domainId), { modelScale: normalizedModelScale(modelScale) }).configId
}

export function modelConfigLabelFor(modelConfigId, domainId = 'xinanjiang', modelScale = '1km') {
  const context = resolveDomainContext(normalizedDomainId(domainId), {
    configId: modelConfigId,
    modelScale: normalizedModelScale(modelScale)
  })
  return context.modelConfig.label
}

function domainLabelFor(domainId) {
  return resolveDomainContext(normalizedDomainId(domainId)).basinName
}

function firstValue(...values) {
  return values.find((value) => value != null && String(value).trim() !== '') || ''
}

function inferDomainId(record = {}) {
  if (record.domainId) return normalizedDomainId(record.domainId)
  const hint = `${record.id || ''} ${record.runId || ''} ${record.param || ''} ${record.modelVersion || ''}`.toUpperCase()
  if (hint.includes('SEL')) return 'selinco'
  if (hint.includes('LLPQ')) return 'llpq'
  return 'nananxi-xufan'
}

function productKey(record) {
  return record?.productId || record?.id || ''
}

function normalizeLineage(record, domainId, modelConfigId) {
  const sources = record.inputSources || record.lineage || {}
  const state = sources.state || {}
  const precipitation = sources.precipitation || sources.forcing || {}
  const observation = sources.observation || {}
  const parameter = sources.parameter || {}

  return {
    state: {
      id: firstValue(state.id, record.state, '未采用'),
      label: firstValue(state.label, '已采用起报状态')
    },
    precipitation: {
      id: firstValue(precipitation.id, record.data, '未提供'),
      label: firstValue(precipitation.label, record.rainQpfSource, '预报降水资料')
    },
    observation: {
      id: firstValue(observation.id, '未提供'),
      label: firstValue(observation.label, '已质控雨水情资料')
    },
    parameter: {
      id: firstValue(parameter.id, record.param, '未采用'),
      configId: modelConfigId,
      label: firstValue(parameter.label, record.modelVersion, modelConfigLabelFor(modelConfigId, domainId, record.modelScale))
    }
  }
}

/** 将预报成果归入对应的流域和模型配置。 */
function normalizeRecord(record = {}) {
  const resolvedContext = resolveDomainContext(inferDomainId(record), {
    projectId: record.projectId,
    configId: record.modelConfigId,
    modelScale: normalizedModelScale(record.modelScale)
  })
  const domainId = resolvedContext.basinId
  const projectId = firstValue(record.projectId, resolvedContext.projectId)
  const modelScale = resolvedContext.modelScale
  const modelConfigId = resolvedContext.configId
  const runId = firstValue(record.runId, record.id, 'FCST-UNASSIGNED')
  const productId = firstValue(record.productId, record.id, `${runId}::${record.sectionId || 'default'}`)

  return {
    ...record,
    id: productId,
    productId,
    runId,
    domainId,
    domainName: firstValue(record.domainName, domainLabelFor(domainId)),
    projectId,
    modelScale,
    modelConfigId,
    modelConfigLabel: firstValue(record.modelConfigLabel, modelConfigLabelFor(modelConfigId, domainId, modelScale)),
    status: firstValue(record.status, '待复核'),
    lineage: normalizeLineage(record, domainId, modelConfigId),
    repro: record.repro !== false
  }
}

const initialRows = FORECAST_PRODUCT_FIXTURES
  .filter((record) => record.fixtureStatus !== 'legacy-compatibility')
  .map(normalizeRecord)

function sectionIdOf(name) {
  if (name === '兰江口') return 'SEC-LANJIANG'
  if (name === '寿昌控制断面') return 'SEC-SHOUCHANG'
  return 'SEC-33000200'
}

function hydroFor(row) {
  if (!row) return simulateHydrograph()
  if (row.hydrograph) return row.hydrograph
  const selinco = row.domainId === 'selinco'
  const hydrograph = simulateHydrograph(row.params || DEFAULT_PARAMS, {
    basePeak: selinco ? row.peak : sectionBasePeak(sectionIdOf(row.section)),
    targetPeak: selinco,
    domainId: row.domainId,
    baseFlow: selinco ? row.currentFlow : undefined,
    issueFlow: selinco ? row.currentFlow : undefined,
    hours: row.leadHours || 72,
    obsHours: selinco ? 48 : 6,
    observedBeforeStart: selinco,
    start: (row.start || '2026-06-02 14:00').replace(' ', 'T') + ':00',
    seed: 1,
    wetness: row.wetness ?? 0.61
  })
  if (!selinco) return hydrograph

  const issueFlow = Number(row.currentFlow ?? hydrograph.forecast?.[0]?.value ?? 0)
  const simulatedPeak = Number(hydrograph.peak ?? issueFlow)
  const targetPeak = Number(row.peak ?? simulatedPeak)
  const scale = (targetPeak - issueFlow) / Math.max(1, simulatedPeak - issueFlow)
  hydrograph.forecast = (hydrograph.forecast || []).map((item) => ({
    ...item,
    value: item.value == null ? null : Math.round(issueFlow + (Number(item.value) - issueFlow) * scale)
  }))
  hydrograph.peak = targetPeak
  hydrograph.peakTime = row.peakTime || hydrograph.peakTime
  return hydrograph
}

function isInContext(row, state) {
  if (!row || row.domainId !== state.activeDomainId) return false
  return !state.activeModelConfigId || row.modelConfigId === state.activeModelConfigId
}

export const useArchiveStore = defineStore('archive', {
  state: () => ({
    rows: initialRows.map((row) => ({ ...row, params: { ...row.params } })),
    activeDomainId: DEFAULT_BASIN_ID,
    activeModelConfigId: modelConfigIdFor(DEFAULT_BASIN_ID),
    filter: '',
    filterSection: '',
    filterStatus: '',
    filterType: '',
    filterOperator: '',
    filterDateFrom: '',
    filterDateTo: '',
    selectedId: initialRows.find((row) => row.domainId === 'xinanjiang')?.productId || null
  }),
  getters: {
    contextRows(state) {
      return state.rows.filter((row) => isInContext(row, state))
    },
    activeDomainLabel(state) {
      return domainLabelFor(state.activeDomainId)
    },
    activeModelConfigLabel(state) {
      return modelConfigLabelFor(state.activeModelConfigId, state.activeDomainId)
    },
    filtered(state) {
      const keyword = state.filter.trim().toLowerCase()
      return state.rows.filter((row) => {
        if (!isInContext(row, state)) return false
        if (state.filterSection && row.section !== state.filterSection) return false
        if (state.filterStatus && row.status !== state.filterStatus) return false
        if (state.filterType && row.type !== state.filterType) return false
        if (state.filterOperator && row.operator !== state.filterOperator) return false
        const start = String(row.start || '').replace(' ', 'T')
        if (state.filterDateFrom && start < state.filterDateFrom) return false
        if (state.filterDateTo && start > state.filterDateTo) return false
        if (keyword) {
          const searchable = [
            row.productId,
            row.runId,
            row.section,
            row.operator,
            row.modelConfigLabel
          ].join(' ').toLowerCase()
          if (!searchable.includes(keyword)) return false
        }
        return true
      })
    },
    selected(state) {
      const row = state.rows.find((item) => productKey(item) === state.selectedId) || null
      return isInContext(row, state) ? row : null
    },
    selectedHydrograph(state) {
      const row = state.rows.find((item) => productKey(item) === state.selectedId)
      return isInContext(row, state) ? hydroFor(row) : simulateHydrograph()
    }
  },
  actions: {
    setContext(domainId, modelConfigId) {
      const nextDomainId = normalizedDomainId(domainId)
      this.activeDomainId = nextDomainId
      this.activeModelConfigId = modelConfigId || modelConfigIdFor(nextDomainId)

      const validSections = new Set(this.contextRows.map((row) => row.section))
      const validOperators = new Set(this.contextRows.map((row) => row.operator))
      if (this.filterSection && !validSections.has(this.filterSection)) this.filterSection = ''
      if (this.filterOperator && !validOperators.has(this.filterOperator)) this.filterOperator = ''

      const visibleSelected = this.filtered.some((row) => productKey(row) === this.selectedId)
      if (!visibleSelected) this.selectedId = this.filtered[0] ? productKey(this.filtered[0]) : null
    },
    select(id) {
      const row = this.rows.find((item) => productKey(item) === id)
      if (!isInContext(row, this)) return false
      this.selectedId = productKey(row)
      return true
    },
    addRun(record) {
      const normalized = normalizeRecord(record)
      const key = productKey(normalized)
      const existing = this.rows.findIndex((row) => productKey(row) === key)
      if (existing >= 0) this.rows.splice(existing, 1)
      this.rows.unshift(normalized)
      if (isInContext(normalized, this)) this.selectedId = key
      return normalized
    },
    comparisonReason(a, b) {
      if (!a || !b) return '请选择两份成果后再进行对比'
      if (a.domainId !== b.domainId) return '不支持跨流域对比'
      if (a.modelConfigId !== b.modelConfigId) return '不支持跨模型配置对比'
      if (a.section !== b.section) return '仅支持同一预报对象的过程对比'
      return ''
    },
    canCompare(a, b) {
      return !this.comparisonReason(a, b)
    },
    hydroFor
  }
})
