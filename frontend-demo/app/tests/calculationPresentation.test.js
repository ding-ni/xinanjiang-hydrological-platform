import {test} from 'node:test'
import assert from 'node:assert/strict'
import {evaluationPresentation,peakDetails,mergedPeakDetails,parameterAtBound,searchScore} from '../src/services/calculationPresentation.js'

test('daily evaluation averages the original windows and leaves the 549h merged result intact',()=>{
  const result={eventMetrics:[
    {eventId:'a',group:'VALIDATION',NSE:.2806920370080618,peak_error_percent:-73.10062758295733,peak_time_error_hours:2,volume_error_percent:-68.08958398297122},
    {eventId:'b',group:'VALIDATION',NSE:.8430298227772463,peak_error_percent:-26.391952016650738,peak_time_error_hours:1,volume_error_percent:-57.073828654947754},
    {eventId:'c',group:'VALIDATION',NSE:.8476334544849352,peak_error_percent:-26.391952016650738,peak_time_error_hours:1,volume_error_percent:-37.620917482938}],
    validationMetrics:{mean_NSE:.4924869169873619,median_peak_time_error_hours:549},overlapGroupMetrics:[{peak_time_error_hours:549}]}
  const original=JSON.stringify(result),summary=evaluationPresentation(result).summaries.find(row=>row.purpose==='验证')
  assert.equal(summary.count,3);assert.equal(summary.time,1)
  assert.equal(summary.nse.toFixed(3),'0.657');assert.equal(summary.peak.toFixed(1),'42.0');assert.equal(summary.volume.toFixed(1),'54.3')
  assert.equal(JSON.stringify(result),original)
})
test('missing scores are omitted, real zero is retained, counts show available observations',()=>{
  const result={frozenRequest:{period:{events:[{id:'a',purpose:'率定'},{id:'b',purpose:'率定'},{id:'c',purpose:'率定'}]}},eventMetrics:[{eventId:'a',NSE:0,peak_error_percent:0,peak_time_error_hours:0},{eventId:'b',NSE:null,peak_error_percent:20,peak_time_error_hours:2}]}
  const row=evaluationPresentation(result).summaries[0]
  assert.equal(row.count,3);assert.equal(row.nse,0);assert.equal(row.peak,10);assert.equal(row.time,1)
  assert.deepEqual(row.validCounts,{nse:1,peak:2,time:2,volume:0});assert.equal(row.volume,null)
})
test('old results derive scores only from paired saved observations and keep earliest tied peaks',()=>{
  const graph={obs:[{time:'2022-03-21T21:00:00',value:56.9},{time:'2022-03-21T22:00:00',value:30},{time:'2022-03-21T23:00:00',value:null}],forecast:[{time:'2022-03-21T21:00:00',value:20},{time:'2022-03-21T22:00:00',value:20},{time:'2022-03-21T23:00:00',value:90}]}
  const peaks=peakDetails(graph)
  assert.equal(peaks.hours,0);assert.equal(peaks.simulatedPeak,20)
  const presentation=evaluationPresentation({frozenRequest:{period:{events:[{id:'a',purpose:'验证'}]}},hydrographs:{a:graph}})
  assert.equal(presentation.summaries[1].count,1);assert.equal(presentation.summaries[1].time,0)
})
test('changing display sign preserves search order and missing values',()=>{
  assert.equal(searchScore(-.22893157767633815),.22893157767633815)
  assert.equal(searchScore(null),null);assert.equal(searchScore(undefined),null);assert.equal(searchScore(0),0)
  assert.ok(searchScore(-.5)>searchScore(.2))
})
test('partial old metrics keep saved values and derive only missing values',()=>{
  const row=evaluationPresentation({eventMetrics:[{eventId:'a',group:'VALIDATION',NSE:.7}],hydrographs:{a:{obs:[{time:'2022-03-21T21:00:00',value:20},{time:'2022-03-21T22:00:00',value:10}],forecast:[{time:'2022-03-21T21:00:00',value:10},{time:'2022-03-21T22:00:00',value:20}]}}}).events[0]
  assert.equal(row.nse,.7);assert.equal(row.time,1);assert.equal(row.volume,0);assert.equal(row.peak,0)
})
test('merged peak detail preserves the two peaks in different floods',()=>{
  const graph=(time,observed,simulated)=>({obs:[{time,value:observed}],forecast:[{time,value:simulated}]})
  const peaks=mergedPeakDetails({hydrographs:{a:graph('2022-03-21T21:00:00',100,20),b:graph('2022-04-13T18:00:00',50,80)}},{memberEventIds:['a','b']})
  assert.equal(peaks.observedTime,'2022-03-21T21:00:00');assert.equal(peaks.simulatedTime,'2022-04-13T18:00:00');assert.equal(peaks.hours,549)
})
test('bound markers concern searched parameters and never clamp results',()=>{
  const rows=[{active:true,value:2.5,minimum:.5,maximum:2.5},{active:true,value:.05,minimum:.05,maximum:.5},{active:false,value:8,minimum:.5,maximum:8},{active:true,value:.4,minimum:.1,maximum:.8}]
  assert.deepEqual(rows.map(parameterAtBound),['达到上限','达到下限','',''])
  assert.equal(rows[0].value,2.5)
})
