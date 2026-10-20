import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const appRoot = path.resolve(scriptDir, '..')
const projectId = process.env.XAJ_PROJECT_ID || 'nananxi-xufan'
const projectRoot = path.join(appRoot, 'public', 'data', 'projects', projectId)
const observationRoot = path.join(projectRoot, 'observations')
const eventCatalogPath = path.join(observationRoot, 'flood-events.json')
const outputPath = path.join(observationRoot, 'event-data-packages.json')

function portableUrl(targetPath) {
  const relative = path.relative(observationRoot, targetPath).split(path.sep).join('/')
  return relative.startsWith('.') ? relative : `./${relative}`
}

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''))
const events = readJson(eventCatalogPath).events.map((event) => ({
  ...event,
  startMs: new Date(event.start).getTime(),
  endMs: new Date(event.end).getTime(),
  rainByStation: new Map(),
  rainRecords: 0,
  maximumHourlyStationRainMm: null,
  riverRecords: 0,
  observedPeakFlowM3s: null,
  observedPeakLevelM: null,
  observedPeakTime: null,
  volumeM3: 0,
  validFlowRecords: 0
}))

const eventsByYear = new Map()
for (const event of events) {
  const startYear = new Date(event.startMs).getFullYear()
  const endYear = new Date(event.endMs).getFullYear()
  for (let year = startYear; year <= endYear; year += 1) {
    eventsByYear.set(year, [...(eventsByYear.get(year) || []), event])
  }
}

for (const [year, yearEvents] of eventsByYear) {
  const yearStartMs = new Date(year, 0, 1).getTime()
  const rainPath = path.join(observationRoot, 'rain', `${year}.json`)
  if (fs.existsSync(rainPath)) {
    const rain = readJson(rainPath)
    for (const [hour, stationIndex, value] of rain.rows) {
      if (value == null) continue
      const timeMs = yearStartMs + Number(hour) * 3600000
      for (const event of yearEvents) {
        if (timeMs < event.startMs || timeMs > event.endMs) continue
        const stationCode = rain.stationCodes[stationIndex]
        const station = event.rainByStation.get(stationCode) || { records: 0, total: 0 }
        station.records += 1
        station.total += Number(value)
        event.rainByStation.set(stationCode, station)
        event.rainRecords += 1
        event.maximumHourlyStationRainMm = Math.max(event.maximumHourlyStationRainMm ?? -Infinity, Number(value))
      }
    }
  }

  const riverPath = path.join(observationRoot, 'river', `${year}.json`)
  if (fs.existsSync(riverPath)) {
    const river = readJson(riverPath)
    for (const [hour, level, flow] of river.rows) {
      const timeMs = yearStartMs + Number(hour) * 3600000
      for (const event of yearEvents) {
        if (timeMs < event.startMs || timeMs > event.endMs) continue
        event.riverRecords += 1
        if (flow != null && Number.isFinite(Number(flow))) {
          event.validFlowRecords += 1
          event.volumeM3 += Number(flow) * 3600
        }
        if (flow != null && (event.observedPeakFlowM3s == null || Number(flow) > event.observedPeakFlowM3s)) {
          event.observedPeakFlowM3s = Number(flow)
          event.observedPeakLevelM = level == null ? null : Number(level)
          event.observedPeakTime = new Date(timeMs - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)
        }
      }
    }
  }
}

const round = (value, digits = 2) => value == null || !Number.isFinite(value) ? null : Number(value.toFixed(digits))
const packages = events.map((event) => {
  const expectedHours = Math.max(1, Math.floor((event.endMs - event.startMs) / 3600000) + 1)
  const stationTotals = [...event.rainByStation.values()].map((station) => station.total)
  const stationCount = stationTotals.length
  const startYear = new Date(event.startMs).getFullYear()
  const endYear = new Date(event.endMs).getFullYear()
  const years = Array.from({ length: endYear - startYear + 1 }, (_, index) => startYear + index)
  return {
    eventId: event.id,
    floodCode: String(event.name).replace(/\s*号洪水$/, ''),
    start: event.start,
    end: event.end,
    durationHours: expectedHours,
    status: event.rainRecords > 0 && event.riverRecords > 0 ? 'ready' : 'partial',
    observedInputs: {
      rainDatasetId: 'XF-OBS-RAIN-HOURLY',
      riverDatasetId: 'XF-OBS-RIVER-HOURLY',
      reportingRainStations: stationCount,
      rainRecords: event.rainRecords,
      rainCompleteness: stationCount ? round(Math.min(1, event.rainRecords / (expectedHours * stationCount)), 4) : 0,
      stationAverageTotalRainMm: stationCount ? round(stationTotals.reduce((sum, value) => sum + value, 0) / stationCount) : null,
      maximumStationTotalRainMm: stationCount ? round(Math.max(...stationTotals)) : null,
      maximumHourlyStationRainMm: round(event.maximumHourlyStationRainMm),
      riverRecords: event.riverRecords,
      flowRecords: event.validFlowRecords,
      riverCompleteness: round(Math.min(1, event.riverRecords / expectedHours), 4),
      volumeM3: event.validFlowRecords ? round(event.volumeM3, 0) : null,
      observedPeakFlowM3s: round(event.observedPeakFlowM3s),
      observedPeakLevelM: round(event.observedPeakLevelM, 3),
      observedPeakTime: event.observedPeakTime
    },
    references: {
      rain: years.map((year) => portableUrl(path.join(observationRoot, 'rain', `${year}.json`))),
      river: years.map((year) => portableUrl(path.join(observationRoot, 'river', `${year}.json`))),
      stationCatalog: portableUrl(path.join(observationRoot, 'catalog.json')),
      spatialPackage: portableUrl(path.join(projectRoot, 'input-preparation.json'))
    }
  }
})

const output = {
  schemaVersion: '1.0',
  generatedAt: new Date().toISOString(),
  source: {
    projectId,
    eventCatalog: portableUrl(eventCatalogPath),
    rainDirectory: portableUrl(path.join(observationRoot, 'rain')),
    riverDirectory: portableUrl(path.join(observationRoot, 'river')),
    mode: 'read-only summary'
  },
  count: packages.length,
  packages
}

fs.writeFileSync(outputPath, JSON.stringify(output), 'utf8')
const ready = packages.filter((item) => item.status === 'ready').length
process.stdout.write(JSON.stringify({ events: packages.length, ready, output: outputPath }))
