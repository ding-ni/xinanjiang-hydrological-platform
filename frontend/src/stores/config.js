import { defineStore } from 'pinia'
import {
  DEFAULT_BASIN_ID,
  DOMAIN_CONTEXT_SELECTIONS_KEY,
  resolveDomainContext
} from '@/data/domainContext'

const LS_KEY = 'xaj_config_v1'
const LS_CONTEXTS_KEY = 'hydro_config_contexts_v2'
const LS_BASIN = 'hydro_basin_id'
const LS_SCALE = 'hydro_model_scale'

function readStorage(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Browser storage can be unavailable in privacy-restricted environments.
  }
}

function parseJson(raw, fallback) {
  if (!raw) return fallback
  try {
    return JSON.parse(raw)
  } catch {
    return fallback
  }
}

function initialContext() {
  const basinId = readStorage(LS_BASIN) || DEFAULT_BASIN_ID
  const selections = parseJson(readStorage(DOMAIN_CONTEXT_SELECTIONS_KEY), {})
  const selected = selections?.[basinId] || {}
  return resolveDomainContext(basinId, {
    ...selected,
    modelScale: selected.modelScale || readStorage(LS_SCALE)
  })
}

function xinanjiangDefaults(context) {
  return {
    thresholds: {
      'SEC-33000200': { name: '\u65b0\u5b89\u6c5f\u5927\u6865', warnLevel: 34.5, ensureLevel: 36.0, warnFlow: 2800 },
      'SEC-LANJIANG': { name: '\u5170\u6c5f\u53e3', warnLevel: 28.2, ensureLevel: 29.8, warnFlow: 1900 },
      'SEC-SHOUCHANG': { name: '\u5bff\u660c\u63a7\u5236\u65ad\u9762', warnLevel: 22.5, ensureLevel: 24.0, warnFlow: 1200 }
    },
    stationWeights: [
      { code: 'R-33068110', name: '\u6885\u57ce\u96e8\u91cf\u7ad9', type: '\u96e8\u91cf', basin: '\u660c\u6eaa', weight: 0.18, use: true },
      { code: 'R-33068112', name: '\u5a01\u576a\u96e8\u91cf\u7ad9', type: '\u96e8\u91cf', basin: '\u68a7\u6850', weight: 0.22, use: true },
      { code: 'R-33068125', name: '\u5efa\u5fb7\u96e8\u91cf\u7ad9', type: '\u96e8\u91cf', basin: '\u5bff\u660c', weight: 0.25, use: true },
      { code: 'R-33068122', name: '\u6df1\u6e21\u96e8\u91cf\u7ad9', type: '\u96e8\u91cf', basin: '\u660c\u6eaa', weight: 0.12, use: false },
      { code: 'H-33000200', name: '\u65b0\u5b89\u6c5f\u5927\u6865\u6c34\u6587\u7ad9', type: '\u6c34\u6587', basin: '\u51fa\u53e3', weight: null, use: false },
      { code: 'W-33000205', name: '\u65b0\u5b89\u6c5f\u6c34\u4f4d\u7ad9', type: '\u6c34\u4f4d', basin: '\u51fa\u53e3', weight: null, use: false }
    ],
    prefs: {
      defaultLead: 24,
      defaultSection: 'SEC-33000200',
      mapStyle: 'dem',
      autoOpenHydro: true,
      lockVersions: true,
      blockOnAbnormal: true,
      showGridDefault: false
    },
    stateInitStrategy: 'last_success',
    productionParamVersion: context.modelConfig.paramVersion,
    savedAt: '2026-06-02 12:00'
  }
}

function nananxiDefaults(context) {
  return {
    thresholds: {},
    stationWeights: [],
    prefs: {
      defaultLead: 24,
      defaultSection: '70115800',
      mapStyle: 'dem',
      autoOpenHydro: true,
      lockVersions: true,
      blockOnAbnormal: true,
      showGridDefault: true
    },
    stateInitStrategy: 'last_success',
    productionParamVersion: context.modelConfig.paramVersion,
    savedAt: '尚未保存'
  }
}

function spatialOnlyDefaults(context) {
  return {
    thresholds: {},
    stationWeights: [],
    prefs: {
      defaultLead: 24,
      defaultSection: null,
      mapStyle: 'dem',
      autoOpenHydro: false,
      lockVersions: true,
      blockOnAbnormal: true,
      showGridDefault: true
    },
    stateInitStrategy: 'last_success',
    productionParamVersion: context.modelConfig.paramVersion,
    savedAt: '尚未保存'
  }
}

function selincoDefaults(context) {
  return {
    thresholds: {
      'SEL-LAKE-ALL': { name: '\u8272\u6797\u9519\u5168\u6e56', warnLevel: 4531.1, ensureLevel: 4531.6, warnFlow: 900 },
      'SEL-SEC-001': { name: '\u624e\u52a0\u85cf\u5e03\u5165\u6e56\u53e3', warnLevel: 0, ensureLevel: 0, warnFlow: 360 },
      'SEL-SEC-002': { name: '\u624e\u6839\u85cf\u5e03\u5165\u6e56\u53e3', warnLevel: 0, ensureLevel: 0, warnFlow: 240 },
      'SEL-SEC-003': { name: '\u6ce2\u66f2\u85cf\u5e03\u5165\u6e56\u53e3', warnLevel: 0, ensureLevel: 0, warnFlow: 180 },
      'SEL-SEC-004': { name: '\u963f\u91cc\u85cf\u5e03\u5165\u6e56\u53e3', warnLevel: 0, ensureLevel: 0, warnFlow: 150 }
    },
    stationWeights: [
      { code: 'SEL-R-001', name: '\u5df4\u52d2\u9519', type: '\u964d\u6c34', basin: '\u624e\u52a0\u85cf\u5e03\u5165\u6e56\u6d41\u57df', weight: 0.35, use: true },
      { code: 'SEL-R-002', name: '115\u9053\u73ed', type: '\u964d\u6c34', basin: '\u624e\u52a0\u85cf\u5e03\u5165\u6e56\u6d41\u57df', weight: 0.25, use: true },
      { code: 'SEL-R-003', name: '\u675c\u52a02', type: '\u964d\u6c34', basin: '\u6ce2\u66f2\u85cf\u5e03\u5165\u6e56\u6d41\u57df', weight: 0.20, use: true },
      { code: 'SEL-R-004', name: '\u8272\u6797\u9519', type: '\u964d\u6c34', basin: '\u8272\u6797\u9519\u6e56\u533a', weight: 0.20, use: true },
      { code: 'SEL-W-001', name: '\u8272\u6797\u9519\u6e56\u4f4d', type: '\u6c34\u4f4d', basin: '\u8272\u6797\u9519\u6e56\u533a', weight: null, use: false }
    ],
    prefs: {
      defaultLead: 24,
      defaultSection: 'SEL-LAKE-ALL',
      mapStyle: 'dem',
      autoOpenHydro: true,
      lockVersions: true,
      blockOnAbnormal: true,
      showGridDefault: true
    },
    stateInitStrategy: 'last_success',
    productionParamVersion: context.modelConfig.paramVersion,
    savedAt: '2026-06-02 12:00'
  }
}

function defaultsFor(context) {
  const base = context.basinId === 'selinco'
    ? selincoDefaults(context)
    : context.basinId === 'nananxi-xufan'
      ? nananxiDefaults(context)
      : context.basinId === 'llpq'
        ? spatialOnlyDefaults(context)
        : spatialOnlyDefaults(context)
  return { ...base, dirty: false }
}

function normalizeLeadHours(value, fallback) {
  const hours = Number(value)
  if (!Number.isFinite(hours)) return fallback
  return Math.min(72, Math.max(6, Math.round(hours / 6) * 6))
}

function mergeContextConfig(context, saved) {
  const base = defaultsFor(context)
  if (!saved || typeof saved !== 'object') return base
  // 旧版本曾把通用新安江示例站网写入南安溪和里龙普曲上下文。
  // 这两个项目尚无可保存的业务规则，直接采用各自项目默认值完成迁移。
  if (context.basinId === 'nananxi-xufan' || context.basinId === 'llpq') return base
  // Older generic workspaces inherited the unrelated Xin'anjiang sample.
  // A new registered case has no business thresholds/station weights until
  // they are supplied for that case; do not restore those sample records.
  if (context.basinId !== 'selinco') {
    const prefs = { ...base.prefs, ...(saved.prefs || {}) }
    prefs.defaultLead = normalizeLeadHours(prefs.defaultLead, base.prefs.defaultLead)
    prefs.defaultSection = base.prefs.defaultSection
    return { ...base, prefs, savedAt: saved.savedAt || base.savedAt }
  }
  const prefs = { ...base.prefs, ...(saved.prefs || {}) }
  prefs.defaultLead = normalizeLeadHours(prefs.defaultLead, base.prefs.defaultLead)
  return {
    ...base,
    ...saved,
    thresholds: { ...base.thresholds, ...(saved.thresholds || {}) },
    stationWeights: Array.isArray(saved.stationWeights) ? saved.stationWeights.map((row) => ({ ...row })) : base.stationWeights,
    prefs,
    dirty: false
  }
}

function readContextRecords() {
  const stored = parseJson(readStorage(LS_CONTEXTS_KEY), {})
  return stored?.contexts && typeof stored.contexts === 'object' ? stored.contexts : stored
}

function loadContextConfig(context) {
  const records = readContextRecords()
  const saved = records?.[context.contextKey]

  // Migrate the old unscoped configuration only into its original basin.
  const legacy = !saved && context.basinId === 'xinanjiang'
    ? parseJson(readStorage(LS_KEY), null)
    : null
  return mergeContextConfig(context, saved || legacy)
}

function stateFor(context) {
  const config = loadContextConfig(context)
  return {
    basinId: context.basinId,
    projectId: context.projectId,
    modelConfigId: context.configId,
    contextKey: context.contextKey,
    ...config
  }
}

export const useConfigStore = defineStore('config', {
  state: () => stateFor(initialContext()),
  getters: {
    thresholdList(state) {
      return Object.entries(state.thresholds).map(([id, threshold]) => ({ id, ...threshold }))
    }
  },
  actions: {
    setContext(contextOrBasinId, options = {}) {
      const context = typeof contextOrBasinId === 'object' && contextOrBasinId?.contextKey
        ? contextOrBasinId
        : resolveDomainContext(contextOrBasinId || this.basinId, options)
      const next = stateFor(context)
      // Pinia object patches merge nested objects.  These three records must be
      // replaced wholesale so keys from a different basin cannot survive.
      this.basinId = next.basinId
      this.projectId = next.projectId
      this.modelConfigId = next.modelConfigId
      this.contextKey = next.contextKey
      this.thresholds = next.thresholds
      this.stationWeights = next.stationWeights
      this.prefs = next.prefs
      this.stateInitStrategy = next.stateInitStrategy
      this.productionParamVersion = next.productionParamVersion
      this.savedAt = next.savedAt
      this.dirty = false
      return context
    },
    setThreshold(sectionId, field, value) {
      if (!this.thresholds[sectionId]) return
      this.thresholds[sectionId][field] = Number(value)
      this.dirty = true
    },
    setWeight(code, weight) {
      const row = this.stationWeights.find((item) => item.code === code)
      if (row) {
        row.weight = Number(weight)
        this.dirty = true
      }
    },
    toggleUse(code) {
      const row = this.stationWeights.find((item) => item.code === code)
      if (row) {
        row.use = !row.use
        this.dirty = true
      }
    },
    setPref(key, value) {
      if (key in this.prefs) {
        this.prefs[key] = value
        this.dirty = true
      }
    },
    setProductionVersion(version) {
      this.productionParamVersion = version
      this.dirty = true
    },
    save() {
      this.dirty = false
      this.savedAt = new Date().toISOString().slice(0, 16).replace('T', ' ')
      const records = readContextRecords()
      records[this.contextKey] = {
        thresholds: this.thresholds,
        stationWeights: this.stationWeights,
        prefs: this.prefs,
        stateInitStrategy: this.stateInitStrategy,
        productionParamVersion: this.productionParamVersion,
        savedAt: this.savedAt
      }
      writeStorage(LS_CONTEXTS_KEY, JSON.stringify({ version: 2, contexts: records }))
      return true
    }
  }
})
