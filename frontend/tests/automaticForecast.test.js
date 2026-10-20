import test from 'node:test'
import assert from 'node:assert/strict'
import { beijingHourInput, canStartForecast, forecastSources, latestForecastProduct, pendingForecastCount, sameForecastSettings, sampleForecastProduct } from '../src/services/automaticForecastService.js'

test('forecast uses Beijing time regardless of browser timezone',()=>{
  assert.equal(beijingHourInput(new Date('2026-10-19T17:35:00Z')),'2026-10-20T01:00')
})
test('issue interval and forecast horizon are separate; saved parameters suffice',()=>{
  const form={parameterSchemeId:'PARAMSET-saved',sourceId:'weather',firstIssueTime:'2026-10-19T12:00',issueIntervalHours:1,leadHours:24}
  assert.equal(canStartForecast(form),true)
  for(const change of [{parameterSchemeId:''},{sourceId:''},{firstIssueTime:'2026-10-19T12:30'},{issueIntervalHours:0},{leadHours:73},{leadHours:1.5}])assert.equal(canStartForecast({...form,...change}),false)
  assert.equal(canStartForecast({...form,issueIntervalHours:100}),true)
})
test('products grouped only by source and context, never shift valid times',()=>{
  const row={contextKey:'basin',sourceId:'weather',sourceLabel:'来源',times:['2026-10-19T13:00'],createdAt:'2026-10-19'}
  const sources=forecastSources([{...row,issueTime:'2026-10-19T11:00'},{...row,issueTime:'2026-10-19T12:00'},{...row,contextKey:'other',issueTime:'2026-10-20T00:00'}],'basin')
  assert.equal(sources.length,1)
  assert.equal(sources[0].id,'weather')
  assert.equal(sources[0].issueTime,'2026-10-19T12:00')
  assert.deepEqual(sources[0].times,row.times)
})
test('sample clearly declares uniform rainfall, units and hour-ending convention',()=>{
  const sample=sampleForecastProduct('basin','2026-10-19T12:00')
  assert.equal(sample.hourlyRainfallMm.length,24)
  assert.match(sample.sourceLabel,/示例.*均匀/)
  assert.equal(sample.timeConvention,'hour-ending')
  assert.equal(sample.unit,'mm')
})

test('catch-up counts the next uncompleted issue through the current hour, no future or duplicate',()=>{
  const row={nextIssueTime:'2026-10-19T12:00:00',issueIntervalHours:1}
  assert.equal(pendingForecastCount(row,'2026-10-19T14:00'),3)
  assert.equal(pendingForecastCount(row,'2026-10-19T11:00'),0)
  assert.equal(pendingForecastCount({...row,issueIntervalHours:2},'2026-10-19T15:00'),2)
  assert.equal(pendingForecastCount(null),0)
  assert.equal(pendingForecastCount({...row,issueIntervalHours:0}),0)
})
test('unchanged settings resume the original schedule; edits explicitly start a new one',()=>{
  const form={parameterSchemeId:'saved',sourceId:'weather',firstIssueTime:'2026-10-19T12:00',issueIntervalHours:1,leadHours:24}
  const row={...form,firstIssueTime:form.firstIssueTime+':00',observationStateVersionId:'observed'}
  assert.equal(sameForecastSettings(form,row),true)
  assert.equal(sameForecastSettings({...form,leadHours:12},row),false)
  assert.equal(sameForecastSettings({...form,parameterSchemeId:'other'},row),false)
  assert.equal(sameForecastSettings({...form,firstIssueTime:'2026-10-19T13:00'},row),false)
})
test('manual forecast selects only an actual product window, never a future issue or shifted scenario',()=>{
  const first={schemaVersion:'2.0',sourceId:'weather',versionId:'first',issueTime:'2026-10-19T11:00:00',leadHours:4}
  const future={...first,versionId:'future',issueTime:'2026-10-19T13:00:00'}
  assert.equal(latestForecastProduct([first,future],'weather','2026-10-19T12:00',2).versionId,'first')
  assert.equal(latestForecastProduct([first],'weather','2026-10-19T14:00',2),null)
  assert.equal(latestForecastProduct([{...first,schemaVersion:'1.0',sourceId:undefined}],'first','2026-10-19T12:00',2),null)
})
