import { selectObservedHydrologyDataset, selectObservedPrecipitationDataset } from './datasetSelectionService.js'
import { projectResourceRoot } from './projectResourceService.js'
import { selectedInputBundleVersionId } from './inputProductService.js'
import { modelApi, uniqueRequestId } from './modelBackendService.js'
import dayjs from 'dayjs'

const STORAGE_KEY = 'hydro_prepared_forecast_requests_v1'

function requestId(contextKey) {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14)
  const context = String(contextKey || 'forecast').replace(/[^A-Za-z0-9]/g, '').slice(-12).toUpperCase()
  return `FCST-REQ-${context}-${stamp}`
}

function persistPreparedRequest(request) {
  if (typeof window === 'undefined') return
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    localStorage.setItem(STORAGE_KEY, JSON.stringify([request, ...saved].slice(0, 30)))
  } catch {
    // 浏览器存储不可用时仍返回当前请求，不影响用户查看提交内容。
  }
}

export function buildForecastRequest({ app, forecast, rainProduct, interpolation = {} }) {
  if (app.usesModelBackend && app.basinId === 'nananxi-xufan') {
    const issue = dayjs(forecast.startTime)
    const end = issue.add(Number(forecast.leadHours), 'hour').format('YYYY-MM-DDTHH:mm:ss')
    return {
      schemaVersion: '2.0', requestId: uniqueRequestId('FCST-REQ'), runType: 'forecast',
      context: { contextKey: forecast.contextKey, projectId: app.basinId,
        resolutionId: app.modelScale, modelConfigId: forecast.modelConfigId },
      issue: { mode: forecast.forecastMode, sectionId: forecast.targetSectionId,
        sectionName: forecast.targetSection?.name, issueTime: issue.format('YYYY-MM-DDTHH:mm:ss'),
        leadHours: Number(forecast.leadHours), forecastEndTime: end },
      period: { startTime: issue.add(1, 'hour').format('YYYY-MM-DDTHH:mm:ss'), endTime: end,
        warmupDays: 1, initialStateVersionId: forecast.stateVersion },
      parameters: { ...forecast.effectiveParams },
      routing: { versionId: app.selectedRoutingVersionId },
      inputs: { bundleVersionId: selectedInputBundleVersionId(app),
        forecastPrecipitation: { versionId: rainProduct?.versionId, manifestSHA256: rainProduct?.manifestSHA256 },
        evaporation: { mode: 'monthly-climatology-assumption' }, parameterVersionId: forecast.paramVersion || null },
      lineage: { inputBundleVersionId: selectedInputBundleVersionId(app), stateVersionId: forecast.stateVersion }
    }
  }
  const id = requestId(forecast.contextKey)
  const scale = app.modelScale
  const griddedPrecipitationDatasetId = selectObservedPrecipitationDataset(app.projectData, scale)?.id || null
  const projectRoot = projectResourceRoot(app, app.basinId) || './data/projects/'
  const observedHydrologyDatasetId = selectObservedHydrologyDataset(app.projectData)?.id || null
  return {
    schemaVersion: '1.1',
    requestId: id,
    runType: 'forecast',
    createdAt: new Date().toISOString(),
    context: {
      contextKey: forecast.contextKey,
      projectId: app.basinId,
      projectName: app.basinName,
      resolutionId: scale,
      modelFrameworkId: 'distributed-xinanjiang',
      modelConfigId: forecast.modelConfigId
    },
    issue: {
      mode: forecast.forecastMode === 'manual' ? 'manual' : 'automatic',
      sectionId: forecast.targetSectionId,
      sectionName: forecast.targetSection?.name,
      issueTime: forecast.startTime,
      leadHours: Number(forecast.leadHours),
      forecastEndTime: forecast.forecastEndTime
    },
    inputs: {
      ...(app.usesModelBackend && app.basinId === 'nananxi-xufan' ? { bundleVersionId: selectedInputBundleVersionId(app) } : {}),
      observation: {
        datasetId: forecast.observationDataId || observedHydrologyDatasetId,
        griddedPrecipitationDatasetId,
        endTime: forecast.rainObsEnd,
        catalog: `${projectRoot}observations/catalog.json`
      },
      forecastPrecipitation: {
        datasetId: forecast.forecastPrecipitationId || rainProduct?.id,
        name: rainProduct?.name,
        resource: rainProduct?.nc || rainProduct?.file || null
      },
      forecastGridMapping: {
        method: interpolation.method || 'product-native-or-conservative-remap',
        parameters: interpolation.parameters || {},
        targetGrid: `${projectRoot}spatial/terrain-grid-${scale}.geojson`
      },
      spatialPackage: {
        preparationCatalog: `${projectRoot}input-preparation.json`,
        basin: `${projectRoot}spatial/basin.geojson`,
        demRaster: `${projectRoot}spatial/terrain-${scale}.tif`,
        rivers: `${projectRoot}spatial/rivers.geojson`,
        sections: `${projectRoot}spatial/sections.json`
      },
      evaporation: app.basinId === 'nananxi-xufan'
        ? { mode: 'constant-zero', valueMmPerHour: 0, scope: 'event-forecast' }
        : { mode: 'configured-dataset' },
      parameterLibrary: forecast.parameterLibrary || (forecast.forecastMode === 'manual' ? 'simulation-rate' : 'automatic-forecast'),
      parameterVersionId: forecast.paramVersion,
      parameterVersionStatus: forecast.parameterVersionStatus || null,
      stateVersionId: forecast.stateVersion
    },
    execution: {
      owner: 'model-backend',
      operations: ['forecast-grid-mapping', 'runoff-generation', 'hillslope-routing', 'channel-routing'],
      frontEndCalculatesModelResult: false
    },
    lineage: {
      ...(app.usesModelBackend && app.basinId === 'nananxi-xufan' ? { inputBundleVersionId: selectedInputBundleVersionId(app) } : {}),
      dataVersionId: forecast.observationDataId || null,
      observationDataVersionId: forecast.observationDataId || null,
      eventSetVersionId: null,
      parameterVersionId: forecast.paramVersion || null,
      stateVersionId: forecast.stateVersion || null,
      modelConfigVersionId: forecast.modelConfigId || null,
      resultVersionId: null,
      sourceTaskId: forecast.committed?.taskId || forecast.activeTaskId || null
    }
  }
}

export async function submitForecastRequest(request) {
  const backendEnabled = String(import.meta.env?.VITE_ENABLE_MODEL_BACKEND || '').toLowerCase() === 'true'
  const apiBase = String(import.meta.env?.VITE_MODEL_API_BASE || '').replace(/\/$/, '')
  if (!backendEnabled || !apiBase) {
    persistPreparedRequest(request)
    return {
      status: 'prepared',
      backendEnabled,
      requestId: request.requestId,
      request,
      message: backendEnabled ? '未配置模型后端地址，已保存请求草稿' : '当前为前端 Demo，模型后端未启用，已保存请求草稿'
    }
  }

  return { status: 'submitted', requestId: request.requestId,
    task: await modelApi('forecast-tasks', { method: 'POST', body: request }), request }
}

export function parseHourlyRainfall(text, leadHours) {
  const tokens = String(text).trim().split(/[\s,，;；]+/).filter(Boolean)
  if (tokens.length !== Number(leadHours)) throw new Error(`请填写 ${leadHours} 项逐小时雨量`)
  const values = tokens.map(token => {
    if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(token)) throw new Error('雨量须为非负数，每小时一项')
    const value = Number(token)
    if (!Number.isFinite(value) || value > 1000) throw new Error('逐小时雨量须在 0–1000 mm 之间')
    return value
  })
  return values
}

export async function listForecastRain() {
  return (await modelApi('forecast-rain')).products || []
}

export function registerForecastRain(body) {
  return modelApi('forecast-rain', { method: 'POST', body })
}

export function forecastCommit(result, request, taskId) {
  if (result?.resultSource !== 'model-backend' || result.taskId !== taskId || result.runType !== 'forecast'
      || result.contextKey !== request?.context?.contextKey || !result.hydrograph?.forecast?.length
      || ['contextKey', 'projectId', 'resolutionId', 'modelConfigId'].some(key => result.context?.[key] !== request.context?.[key])
      || result.issue?.issueTime !== request.issue?.issueTime
      || result.issue?.leadHours !== request.issue?.leadHours
      || result.issue?.sectionId !== request.issue?.sectionId
      || result.hydrograph.forecast.length !== request.issue?.leadHours
      || result.lineage?.forecastRainVersionId !== request.inputs?.forecastPrecipitation?.versionId
      || result.lineage?.forecastRainManifestSHA256 !== request.inputs?.forecastPrecipitation?.manifestSHA256
      || result.lineage?.inputBundleVersionId !== request.inputs?.bundleVersionId
      || result.lineage?.routingVersionId !== request.routing?.versionId
      || result.lineage?.parameterVersionId !== request.inputs?.parameterVersionId
      || result.parentStateVersionId !== request.period?.initialStateVersionId) throw new Error('预报成果与作业条件不一致')
  const hydrograph = result.hydrograph
  const rain = hydrograph.forcing?.precipitation
  if (!Array.isArray(rain) || rain.length !== request.issue.leadHours
      || rain.some(value => !Number.isFinite(value) || value < 0)
      || hydrograph.forecast.some((row, index) => !Number.isFinite(row.value) || row.value < 0
        || row.time !== dayjs(request.issue.issueTime).add(index + 1, 'hour').format('YYYY-MM-DDTHH:mm:ss'))
      || !Number.isFinite(hydrograph.peak) || !Number.isFinite(hydrograph.volume)
      || hydrograph.forcing?.versionId !== request.inputs.forecastPrecipitation.versionId) throw new Error('预报逐小时成果不完整')
  const peak = Math.max(...hydrograph.forecast.map(row => row.value))
  const volume = hydrograph.forecast.reduce((sum, row) => sum + row.value * 3600, 0)
  if (Math.abs(hydrograph.peak - peak) > 1e-8 * Math.max(1, peak)
      || Math.abs(hydrograph.volume - volume) > 1e-8 * Math.max(1, volume)
      || hydrograph.peakTime !== hydrograph.forecast.find(row => row.value === peak).time) throw new Error('预报摘要与逐小时过程不一致')
  const summary = { name: '降雨情景预报', peak: hydrograph.peak, peakTime: hydrograph.peakTime,
    volume: hydrograph.volume, peakLevel: null, level: '—' }
  const output = { sectionId: request.issue.sectionId, productId: `${taskId}::${request.issue.sectionId}`, hydrograph, summary }
  return { runId: taskId, taskId, resultVersionId: result.resultVersionId, resultSource: result.resultSource,
    focusSectionId: request.issue.sectionId, productId: output.productId,
    hydrograph, summary, outputs: [output], request, lineage: result.lineage }
}
