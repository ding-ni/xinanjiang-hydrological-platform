import { modelApi, uniqueRequestId } from './modelBackendService.js'

export const automaticForecastClient = {
  list: () => modelApi('automatic-forecasts'),
  products: async () => (await modelApi('forecast-rain')).products || [],
  importProduct: body => modelApi('forecast-rain', { method: 'POST', body }),
  create: body => modelApi('automatic-forecasts', { method: 'POST', body: { ...body, requestId: uniqueRequestId('AUTO') } }),
  control: (id, action) => modelApi(`automatic-forecasts/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: {} }),
  issues: async id => (await modelApi(`automatic-forecasts/${encodeURIComponent(id)}/issues`)).issues || []
}

export function forecastSources(products, contextKey) {
  const sources = new Map()
  for (const row of products.filter(row => row.contextKey === contextKey)) {
    const id = row.sourceId || row.versionId
    const old = sources.get(id)
    if (!old || row.issueTime > old.issueTime || (row.issueTime === old.issueTime && row.createdAt > old.createdAt)) sources.set(id, { ...row, id })
  }
  return [...sources.values()].sort((a, b) => b.issueTime.localeCompare(a.issueTime))
}

export function beijingHourInput(date = new Date()) {
  return new Date(date.getTime() + 8 * 3600000).toISOString().slice(0, 13) + ':00'
}

export function canStartForecast({ parameterSchemeId, sourceId, firstIssueTime, issueIntervalHours, leadHours }) {
  return Boolean(parameterSchemeId && sourceId && /^\d{4}-\d{2}-\d{2}T\d{2}:00$/.test(firstIssueTime)
    && Number.isInteger(issueIntervalHours) && issueIntervalHours > 0 && Number.isInteger(leadHours) && leadHours >= 1 && leadHours <= 72)
}

export function sampleForecastProduct(contextKey, issueTime = beijingHourInput()) {
  return { name: '逐小时降雨产品', contextKey, sourceId: 'rain-product', sourceLabel: '请填写真实产品来源（示例为全域均匀雨量）',
    issueTime, unit: 'mm', timeConvention: 'hour-ending', hourlyRainfallMm: Array(24).fill(0) }
}

// Calendar hours, independent of the browser's timezone or daylight saving.
const hourValue = value => Date.parse(String(value).replace(' ', 'T').slice(0, 16) + ':00Z')
export function pendingForecastCount(schedule, current = beijingHourInput()) {
  if (!schedule?.nextIssueTime || !Number.isInteger(schedule.issueIntervalHours) || schedule.issueIntervalHours < 1) return 0
  return Math.max(0, Math.floor((hourValue(current) - hourValue(schedule.nextIssueTime)) / (schedule.issueIntervalHours * 3600000)) + 1) || 0
}

export function sameForecastSettings(form, schedule) {
  return Boolean(schedule && ['parameterSchemeId', 'sourceId', 'issueIntervalHours', 'leadHours'].every(key => form[key] === schedule[key])
    && form.firstIssueTime === schedule.firstIssueTime.slice(0, 16))
}

export function latestForecastProduct(products, sourceId, issueTime, leadHours) {
  const issue = hourValue(issueTime)
  return products.filter(row => {
    const offset = (issue - hourValue(row.issueTime)) / 3600000
    return (row.sourceId || row.versionId) === sourceId && Number.isInteger(offset) && offset >= 0
      && (row.schemaVersion !== '1.0' || offset === 0) && offset + leadHours <= row.leadHours
  }).sort((a, b) => b.issueTime.localeCompare(a.issueTime) || String(b.createdAt).localeCompare(String(a.createdAt)))[0] || null
}
