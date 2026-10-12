import { defineStore } from 'pinia'
import { selincoProfile } from '@/data/selinco'
import {
  DEFAULT_BASIN_ID,
  DOMAIN_CONTEXT_SELECTIONS_KEY,
  WORK_CONTEXTS_KEY,
  listBasins,
  normalizeBasinId,
  resolveDomainContext
} from '@/data/domainContext'
import { useConfigStore } from '@/stores/config'
import { useForecastStore } from '@/stores/forecast'
import { useStateStore } from '@/stores/state'
import { useCalibrationStore } from '@/stores/calibration'
import { useArchiveStore } from '@/stores/archive'
import { useMonitorStore } from '@/stores/monitor'
import { loadProjectContextData } from '@/services/projectDataService'
import { loadAndRegisterProjectCatalog } from '@/services/projectCatalogService'
import { isCaseFixtureContext } from '@/services/caseAvailabilityService'
import { modelApi, modelBackendEnabled } from '@/services/modelBackendService'
import { loadModelInputCatalog } from '@/services/modelInputService'
import { DEFAULT_INPUT_BUNDLE_VERSION_ID, listInputProducts } from '@/services/inputProductService'
import dayjs from 'dayjs'
import { FORECAST_PRODUCT_FIXTURES } from '@/fixtures/runs/forecast-products'

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

function readContextSelections() {
  try {
    return JSON.parse(readStorage(DOMAIN_CONTEXT_SELECTIONS_KEY) || '{}')
  } catch {
    return {}
  }
}

function initialContext() {
  const basinId = normalizeBasinId(readStorage(LS_BASIN) || (modelBackendEnabled() ? 'nananxi-xufan' : DEFAULT_BASIN_ID))
  const selection = readContextSelections()[basinId] || {}
  return resolveDomainContext(basinId, {
    ...selection,
    modelScale: selection.modelScale || readStorage(LS_SCALE)
  })
}

function initialRunIds() {
  return Object.fromEntries(FORECAST_PRODUCT_FIXTURES
    .filter((record) => record.domainId === 'selinco')
    .map((record) => [`selinco:selinco-inflow-lake:selinco-${record.modelScale}`, record.id]))
}

function initialLatestProducts() {
  return FORECAST_PRODUCT_FIXTURES.filter((record) => record.domainId === 'selinco').reduce((contexts, record) => {
    const contextKey = `selinco:selinco-inflow-lake:selinco-${record.modelScale}`
    contexts[contextKey] ||= {}
    contexts[contextKey][record.section] = {
      runId: record.id, section: record.section, peak: record.peak, peakTime: record.peakTime,
      leadHours: record.leadHours, status: record.status, publishedAt: record.publishedAt
    }
    return contexts
  }, {})
}

function initialStateVersion(context) {
  if (!isCaseFixtureContext(context)) return null
  return `${context.modelConfig.statePrefix}-20260602-0800`
}

function readWorkContexts() {
  try {
    return JSON.parse(readStorage(WORK_CONTEXTS_KEY) || '{}')
  } catch {
    return {}
  }
}

function createWorkContext(context, saved = {}) {
  return {
    basinProjectId: context.basinProjectId,
    spatialResolutionId: context.spatialResolutionId,
    basinConfigVersionId: context.basinConfigVersionId,
    dataSourceId: saved.dataSourceId || null,
    inputBundleVersionId: saved.inputBundleVersionId || null,
    selectedInputBundleVersionId: saved.selectedInputBundleVersionId || (context.basinId === 'nananxi-xufan' && context.modelScale === '1km' ? DEFAULT_INPUT_BUNDLE_VERSION_ID : null),
    selectedRoutingVersionId: saved.selectedRoutingVersionId || 'control-split-v2',
    parameterVersionId: saved.parameterVersionId || (isCaseFixtureContext(context) ? context.modelConfig.paramVersion : null),
    stateVersionId: isCaseFixtureContext(context) ? (saved.stateVersionId || initialStateVersion(context)) : saved.stateVersionId?.startsWith('STATE-JOB-') ? saved.stateVersionId : null,
    sectionId: saved.sectionId || null,
    issueTime: saved.issueTime || (context.basinId === 'nananxi-xufan' ? '2024-07-06 08:00' : isCaseFixtureContext(context) ? '2026-06-02 14:00' : null),
    sourceTaskId: saved.sourceTaskId || null,
    handoff: saved.handoff || null,
    contextKey: context.contextKey
  }
}

export const useAppStore = defineStore('app', {
  state: () => {
    const context = initialContext()
    const runIdsByContext = initialRunIds()
    const workContextsByKey = readWorkContexts()
    const workContext = createWorkContext(context, workContextsByKey[context.contextKey])
    return {
    basinId: context.basinId,
    basinName: context.basinName,
    projectId: context.projectId,
    modelConfigId: context.configId,
    contextKey: context.contextKey,
    // Kept for current views; the source of truth is modelConfigId/contextKey.
    modelScale: context.modelScale,
    dataStatus: context.statusLabel,
    availabilityStatus: context.status,
    // The selected run belongs to a complete model context, never to a basin
    // alone. This prevents a run from one grid/configuration appearing in
    // another configuration after the top-bar selection changes.
    runId: runIdsByContext[context.contextKey] || null,
    runIdsByContext,
    paramVersion: context.modelConfig.paramVersion,
    stateVersion: initialStateVersion(context),
    clock: '2026-06-02 14:00',
    now: dayjs('2026-06-02 14:00'),
    dataTime: '2026-06-02 14:00',
    /** 预报单弹层数据 */
    reportData: null,
    reportOpen: false,
    latestByContext: initialLatestProducts(),
    workContext,
    workContextsByKey,
    projectData: null,
    backendCatalog: null,
    backendLoading: false,
    backendError: '',
    inputProducts: [],
    selectedInputCatalog: null,
    inputSelectionRevision: 0,
    inputCatalogRequestRevision: 0,
    backendRequestRevision: 0,
    inputCatalogLoading: false,
    inputCatalogError: '',
    projectDataLoading: false,
      projectDataError: '',
      // Catalog-backed case options.  The static domain registry remains the
      // first-paint fallback; this list is replaced when catalog.json and its
      // manifests have been read successfully.
      availableBasins: listBasins(),
      catalogLoading: false,
      catalogLoaded: false,
      catalogError: '',
      catalogWarnings: [],
      catalogRevision: 0
    }
  },
  getters: {
    usesModelBackend() {
      return modelBackendEnabled()
    },
    hasModelInput(state) {
      return modelBackendEnabled() && Boolean(state.projectData?.backend?.inputServiceReady) && !state.backendError && !state.inputCatalogError
    },
    backendCase(state) {
      const backend = state.projectData?.backend
      const input = state.selectedInputCatalog
      if (!backend) return null
      return input?.bundleVersionId === this.selectedInputBundleVersionId ? { ...backend,
        events: input.events, forcingVersion: input.forcingVersion, observationVersion: input.observationVersion,
        inputBundleVersionId: input.bundleVersionId, qualityNotes: input.qualityNotes || backend.qualityNotes
      } : backend
    },
    selectedInputBundleVersionId(state) {
      return state.basinId === 'nananxi-xufan' && state.modelScale === '1km'
        ? state.workContext.selectedInputBundleVersionId || DEFAULT_INPUT_BUNDLE_VERSION_ID : null
    },
    inputSelectionKey(state) {
      return `${state.contextKey}:${this.selectedInputBundleVersionId || 'unsupported'}:${state.inputSelectionRevision}`
    },
    selectedRoutingVersionId(state) {
      return state.workContext.selectedRoutingVersionId || 'control-split-v2'
    },
    selectedInputProduct(state) {
      return state.inputProducts.find((row) => row.bundleVersionId === this.selectedInputBundleVersionId) || null
    },
    clockText(state) {
      return state.now.format('YYYY-MM-DD HH:mm')
    },
    latestForecastProduct(state) {
      const products = state.latestByContext[state.contextKey] || {}
      if (state.basinId === 'selinco') return products['扎加藏布入湖口'] || Object.values(products)[0] || {
        runId: '—',
        section: '—',
        peak: 0,
        peakTime: '—',
        status: '无',
        publishedAt: '—',
      }
      return Object.values(products)[0] || {
          runId: '—',
          section: '—',
          peak: 0,
          peakTime: '—',
          status: '无',
          publishedAt: '—',
        }
    },
    isSelinco(state) {
      return state.basinId === 'selinco'
    },
    isColdRegion() {
      return Boolean(this.activeContext.capabilities.coldRegion)
    },
    hasLakeBalance() {
      return Boolean(this.activeContext.capabilities.lakeBalance)
    },
    hasObservationCatalog() {
      return Boolean(this.activeContext.capabilities.observationCatalog)
    },
    hasEventCatalog() {
      return Boolean(this.activeContext.capabilities.eventCatalog)
    },
    activeContext(state) {
      // The registry is synchronous and not reactive. Reading this revision
      // invalidates the cached context after catalog-backed definitions load.
      void state.catalogRevision
      const context = resolveDomainContext(state.basinId, {
        projectId: state.projectId,
        configId: state.modelConfigId,
        modelScale: state.modelScale
      })
      return { ...context, selectedInputBundleVersionId: this.selectedInputBundleVersionId, selectedRoutingVersionId: this.selectedRoutingVersionId, inputSelectionKey: this.inputSelectionKey }
    },
    activeModelConfig() {
      return this.activeContext.modelConfig
    },
    activeScaleMeta(state) {
      void state.catalogRevision
      const context = resolveDomainContext(state.basinId, {
        projectId: state.projectId,
        configId: state.modelConfigId,
        modelScale: state.modelScale
      })
      if (context.basinId === 'selinco') return selincoProfile.scales[context.modelScale] || selincoProfile.scales['1km']
      return context.modelConfig.spatial || { label: context.spatialResolutionId, cellCount: null }
    },
    activeModelScheme(state) {
      void state.catalogRevision
      const context = resolveDomainContext(state.basinId, {
        projectId: state.projectId,
        configId: state.modelConfigId,
        modelScale: state.modelScale
      })
      const config = context.modelConfig
      return {
        id: config.id,
        label: config.label,
        paramVersion: config.paramVersion,
        statePrefix: config.statePrefix,
        forcing: context.basinId === 'selinco' ? selincoProfile.forcing[config.modelScale] : config.forcing,
        projectId: context.projectId,
        contextKey: context.contextKey,
        modelScale: config.modelScale
      }
    }
  },
  actions: {
    applyDomainContext(context, persist = true, { loadData = true } = {}) {
      this.basinId = context.basinId
      this.basinName = context.basinName
      this.projectId = context.projectId
      this.modelConfigId = context.configId
      this.contextKey = context.contextKey
      this.modelScale = context.modelScale
      this.paramVersion = context.modelConfig.paramVersion
      this.runId = this.runIdsByContext[context.contextKey] || null
      this.dataStatus = context.statusLabel
      this.availabilityStatus = context.status

      const savedWorkContext = this.workContextsByKey[context.contextKey]
      this.workContext = createWorkContext(context, savedWorkContext)
      this.inputSelectionRevision += 1
      this.selectedInputCatalog = null
      this.inputCatalogError = ''
      this.inputCatalogRequestRevision += 1
      if (modelBackendEnabled()) useMonitorStore().invalidateBackendInput('', { clearCatalog: true })
      this.stateVersion = this.workContext.stateVersionId
      this.dataTime = this.workContext.issueTime || ''

      if (persist) {
        const selections = readContextSelections()
        selections[context.basinId] = {
          projectId: context.projectId,
          configId: context.configId,
          modelScale: context.modelScale
        }
        writeStorage(DOMAIN_CONTEXT_SELECTIONS_KEY, JSON.stringify(selections))
        writeStorage(LS_BASIN, context.basinId)
        // Legacy consumers still read this key.  It is always normalized here.
        writeStorage(LS_SCALE, context.modelScale)
      }

      this.syncDomainWorkspaces(context)
      this.persistWorkContext()
      if (loadData) this.loadActiveProjectData()
      return context
    },
    setBasin(id) {
      const basinId = normalizeBasinId(id)
      const saved = readContextSelections()[basinId] || {}
      return this.applyDomainContext(resolveDomainContext(basinId, saved))
    },
    setModelConfig(configId, projectId = this.projectId) {
      const context = resolveDomainContext(this.basinId, {
        projectId,
        configId
      })
      return this.applyDomainContext(context)
    },
    setModelScale(scale) {
      const context = resolveDomainContext(this.basinId, {
        projectId: this.projectId,
        modelScale: scale
      })
      return this.applyDomainContext(context)
    },
    setNow(value) {
      this.now = dayjs(value)
      this.clock = this.now.format('YYYY-MM-DD HH:mm')
    },
    setDataTime(value, { persist = true } = {}) {
      this.dataTime = dayjs(value).format('YYYY-MM-DD HH:mm')
      this.setNow(this.dataTime)
      if (persist) this.updateWorkContext({ issueTime: this.dataTime })
    },
    setStateVersion(v) {
      this.stateVersion = v
      this.updateWorkContext({ stateVersionId: v })
    },
    setRunId(id) {
      this.runId = id
      if (id) this.runIdsByContext[this.contextKey] = id
    },
    updateLatestProduct(partial) {
      const section = partial.section || this.projectData?.sections?.sections?.[0]?.name
      if (!section) return
      const contextKey = partial.contextKey || this.contextKey
      const products = this.latestByContext[contextKey] || {}
      this.latestByContext[contextKey] = {
        ...products,
        [section]: {
        ...(products[section] || {}),
        ...partial,
        section
        }
      }
    },
    updateWorkContext(partial = {}) {
      this.workContext = { ...this.workContext, ...partial, contextKey: this.contextKey }
      this.persistWorkContext()
    },
    prepareHandoff(targetPage, payload = {}) {
      const handoff = {
        targetPage,
        sourcePage: payload.sourcePage || 'unknown',
        sourceLabel: payload.sourceLabel || '跨页面带入',
        preparedAt: dayjs().format('YYYY-MM-DD HH:mm:ss')
      }
      this.updateWorkContext({ ...payload, handoff })
      return handoff
    },
    persistWorkContext() {
      if (!this.contextKey || !this.workContext) return
      this.workContextsByKey[this.contextKey] = { ...this.workContext }
      writeStorage(WORK_CONTEXTS_KEY, JSON.stringify(this.workContextsByKey))
    },
    async loadActiveProjectData() {
      const requestedContextKey = this.contextKey
      this.projectDataLoading = true
      this.projectDataError = ''
      this.projectData = null
      try {
        const data = await loadProjectContextData(this.activeContext)
        if (this.contextKey !== requestedContextKey) return null
        this.projectData = data
        this.dataStatus = data.resolution.statusLabel || this.dataStatus
        this.availabilityStatus = data.resolution.status || this.availabilityStatus
        useForecastStore().setProjectSections(data.sections?.sections || [])
        await this.refreshBackend()
        return data
      } catch (error) {
        if (this.contextKey !== requestedContextKey) return null
        this.projectData = null
        this.projectDataError = error instanceof Error ? error.message : String(error)
        this.dataStatus = '目录加载失败'
        this.availabilityStatus = 'error'
        return null
      } finally {
        if (this.contextKey === requestedContextKey) this.projectDataLoading = false
      }
    },
    markBackendOffline(message) {
      if (!modelBackendEnabled()) return
      this.backendError = message
      if (this.projectData) this.projectData.backendError = message
    },
    async refreshInputProducts() {
      const key = this.contextKey
      const { versions } = await listInputProducts()
      if (key === this.contextKey) this.inputProducts = Array.isArray(versions) ? versions : []
      return versions || []
    },
    async refreshSelectedInputCatalog() {
      if (!modelBackendEnabled() || !this.selectedInputBundleVersionId) return null
      const revision = ++this.inputCatalogRequestRevision
      const key = this.inputSelectionKey
      const source = this.activeContext
      this.inputCatalogLoading = true
      this.inputCatalogError = ''
      try {
        const catalog = await loadModelInputCatalog({ source })
        if (key !== this.inputSelectionKey || revision !== this.inputCatalogRequestRevision) return null
        this.selectedInputCatalog = catalog
        if (this.backendCase) useCalibrationStore().configureBackend(this.backendCase)
        useCalibrationStore().setObservationRange(catalog.river)
        return catalog
      } catch (error) {
        if (key === this.inputSelectionKey && revision === this.inputCatalogRequestRevision) {
          this.selectedInputCatalog = null
          this.inputCatalogError = error.message
          useMonitorStore().invalidateBackendInput(error.message, { clearCatalog: true })
        }
        return null
      } finally {
        if (key === this.inputSelectionKey && revision === this.inputCatalogRequestRevision) this.inputCatalogLoading = false
      }
    },
    async selectInputBundleVersion(bundleVersionId) {
      if (!this.selectedInputBundleVersionId) throw new Error('当前流域或尺度不开放输入产品选用。')
      const key = this.inputSelectionKey
      const revision = ++this.inputCatalogRequestRevision
      const versions = await this.refreshInputProducts()
      if (!versions.some((row) => row.bundleVersionId === bundleVersionId)) throw new Error('该输入产品尚未登记，不能选用。')
      // Verify before committing the selection; an unavailable product leaves
      // the existing workspace and its frozen task/result records untouched.
      const catalog = await loadModelInputCatalog({ source: { ...this.activeContext, selectedInputBundleVersionId: bundleVersionId } })
      if (key !== this.inputSelectionKey || revision !== this.inputCatalogRequestRevision) throw new Error('工作区已变更，本次输入选择未写入。')
      this.updateWorkContext({ selectedInputBundleVersionId: bundleVersionId })
      this.inputSelectionRevision += 1
      this.selectedInputCatalog = catalog
      this.inputCatalogError = ''
      this.inputCatalogLoading = false
      useMonitorStore().invalidateBackendInput('', { clearCatalog: true })
      const calibration = useCalibrationStore()
      calibration.markInputSelectionChanged()
      if (this.backendCase) calibration.configureBackend(this.backendCase)
      calibration.setObservationRange(catalog.river)
      return catalog
    },
    async refreshBackend() {
      if (!modelBackendEnabled()) return null
      const requestedContextKey = this.contextKey
      const requestedInputKey = this.inputSelectionKey
      const revision = ++this.backendRequestRevision
      this.backendLoading = true
      try {
        const catalog = await modelApi('catalog')
        if (this.contextKey !== requestedContextKey || revision !== this.backendRequestRevision) return null
        this.backendCatalog = catalog
        this.backendError = ''
        if (!this.projectData) return null
        this.projectData.backendError = ''
        const backend = catalog.cases[`${this.basinId}:${this.modelScale}`] || null
        this.projectData.backend = backend
        if (backend) {
          if (this.selectedInputBundleVersionId) {
            await Promise.all([this.refreshInputProducts(), this.refreshSelectedInputCatalog()])
          } else useCalibrationStore().configureBackend(backend)
          const [{ states }, { versions }] = await Promise.all([modelApi('states'), modelApi('parameter-versions')])
          if (this.contextKey !== requestedContextKey || revision !== this.backendRequestRevision || requestedInputKey !== this.inputSelectionKey) return null
          useStateStore().hydrateBackendStates(states)
          this.projectData.backendParameterVersions = versions
          await useCalibrationStore().recoverBackendTasks()
        }
        return backend
      } catch (error) {
        if (this.contextKey === requestedContextKey && revision === this.backendRequestRevision) this.markBackendOffline(error.message)
        return null
      } finally {
        if (this.contextKey === requestedContextKey && revision === this.backendRequestRevision) this.backendLoading = false
      }
    },
    async loadProjectCatalog({ force = false } = {}) {
      if (this.catalogLoading) return null
      if (this.catalogLoaded && !force) {
        return { domains: this.availableBasins, errors: this.catalogWarnings }
      }

      this.catalogLoading = true
      this.catalogError = ''
      try {
        const result = await loadAndRegisterProjectCatalog({ force })
        this.availableBasins = listBasins()
        this.catalogWarnings = result.errors || []
        this.catalogLoaded = true
        this.catalogRevision += 1

        // A case added after the previous run may have been persisted in
        // localStorage before it existed in the offline fallback registry.
        // Restore it now that the catalog has registered the definition.
        const persistedBasinId = readStorage(LS_BASIN)
        const selectedBasinId = persistedBasinId && normalizeBasinId(persistedBasinId) === persistedBasinId
          ? persistedBasinId
          : this.basinId
        const saved = readContextSelections()[selectedBasinId] || {}
        this.applyDomainContext(resolveDomainContext(selectedBasinId, {
          ...saved,
          modelScale: saved.modelScale || this.modelScale || readStorage(LS_SCALE)
        }), false, { loadData: force })

        return result
      } catch (error) {
        this.catalogError = error instanceof Error ? error.message : String(error)
        return {
          domains: this.availableBasins,
          errors: [{ id: 'catalog', error: this.catalogError }]
        }
      } finally {
        this.catalogLoading = false
      }
    },
    syncDomainWorkspaces(context) {
      // Views also observe the context for view-only data (map assets, chart
      // selections). Stores are synchronized here so a background workspace
      // cannot retain a different basin/configuration while its page is closed.
      useConfigStore().setContext(context)
      useForecastStore().setContext(context.basinId, context.modelScale, context.configId)
      useStateStore().setContext(context.basinId, context.modelScale, context.configId)
      useCalibrationStore().setContext(context.basinId, context.modelScale, context.configId)
      useArchiveStore().setContext(context.basinId, context.configId)
    },
    openReport(data) {
      this.reportData = data
      this.reportOpen = true
    },
    closeReport() {
      this.reportOpen = false
    },
  }
})
