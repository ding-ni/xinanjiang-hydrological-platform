import test from 'node:test'
import assert from 'node:assert/strict'
import { createDailyClient, rememberedParameter, historicalParameterId, resultEntries, belongsToOperationContext, operationContext, preparationIssueText, dailyOperationSelection } from '../src/services/dailyOperationService.js'

const contextKey='nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km'
test('new saved parameters never replace the staff selection',()=>{
  const schemes=[{id:'new',name:'第二套'},{id:'old',name:'第一套'}]
  assert.equal(rememberedParameter(schemes,'old'),'old')
  assert.equal(rememberedParameter(schemes,''),'')
  assert.equal(rememberedParameter([{id:'only'}],''),'only')
  assert.equal(rememberedParameter(schemes,'missing'),'')
})
test('ordinary navigation keeps the last explicit parameter and drops an incompatible old process',()=>{
  const old={status:'completed',runConfig:{parameterSchemeId:'A'}}
  assert.deepEqual(dailyOperationSelection(old,'B'),{restore:false,parameterId:'B'})
  assert.deepEqual(dailyOperationSelection(old,'A'),{restore:true,parameterId:'A'})
  assert.deepEqual(dailyOperationSelection(old,'B',{explicitHistory:true}),{restore:true,parameterId:'A'})
  assert.deepEqual(dailyOperationSelection({...old,status:'running'},'B'),{restore:true,parameterId:'A'})
})
test('old calculation drafts select only saved parameters associated with their actual source',()=>{
  const schemes=[{id:'current',sourceTaskId:'JOB-current'},{id:'original',sourceTaskId:'JOB-original'}]
  assert.equal(historicalParameterId(schemes,{taskId:'JOB-legacy',runConfig:{lineage:{parameterVersionId:'original'}}}),'original')
  assert.equal(historicalParameterId(schemes,{taskId:'JOB-original',runConfig:{lineage:{}}}),'original')
  assert.equal(historicalParameterId(schemes,{taskId:'JOB-legacy',runConfig:{lineage:{sourceTaskId:'JOB-current'}}}),'')
  assert.equal(historicalParameterId([{id:'only'}],{taskId:'JOB-unrelated',runConfig:{}}),'')
  assert.equal(historicalParameterId([{id:'only'}],{runConfig:{}}),'')
  assert.equal(historicalParameterId(schemes,{taskId:'JOB-legacy'},'original'),'original')
  assert.equal(historicalParameterId(schemes,{taskId:'JOB-original'},'missing'),'')
})
test('daily context contains the basin and model without current input or calibration state',()=>{
  assert.deepEqual(operationContext({basinId:'nananxi-xufan',modelScale:'1km',configId:'nananxi-xufan-1km',contextKey,selectedInputBundleVersionId:'private',selectedRoutingVersionId:'edited'}),{projectId:'nananxi-xufan',resolutionId:'1km',modelConfigId:'nananxi-xufan-1km',contextKey})
  assert.equal(belongsToOperationContext({runConfig:{context:{contextKey}}},contextKey),true)
  assert.equal(belongsToOperationContext({contextKey:'other'},contextKey),false)
})
test('event state view opens the corresponding saved simulation, not the calibration parent',()=>{
  const first={forecast:[{time:'2024-07-01T01:00:00',value:10}]},second={forecast:[{time:'2024-07-03T01:00:00',value:20}]}
  const rows=resultEntries({hydrographs:{'flood-1':first,'flood-2':second},primaryTaskId:'JOB-calibration',eventStates:[{eventId:'flood-1',taskId:'JOB-state-1',endStateVersionId:'STATE-1'},{eventId:'flood-2',taskId:'JOB-state-2',endStateVersionId:'STATE-2'}]},'JOB-parent')
  assert.deepEqual(rows.map(row=>[row.id,row.taskId,row.endStateVersionId]),[['flood-1','JOB-state-1','STATE-1'],['flood-2','JOB-state-2','STATE-2']])
  assert.equal(rows[0].hydrograph,first)
  assert.equal(rows[1].hydrograph,second)
})
test('missing data issues use flood and gap text rather than object serialization',()=>{
  const text=preparationIssueText({eventId:'20240701',reasons:[{message:'缺少降雨'}],missingIntervals:[{start:'2024-07-01T01:00:00',end:'2024-07-01T03:00:00'}]})
  assert.match(text,/20240701.*缺少降雨.*2024-07-01 01:00.*2024-07-01 03:00/)
  assert.doesNotMatch(text,/\[object Object\]/)
})
test('preparation quality rules are presented as hydrological issues',()=>{
  const text=preparationIssueText({eventId:'20240701',reasons:['WARMUP_EVENT_RAIN_BELOW_MINIMUM','OBSERVED_PHASE_COVERAGE_BELOW_MINIMUM']})
  assert.match(text,/预热与洪水时段累计降雨不足/);assert.match(text,/涨水、洪峰或退水段实测流量不足/)
  assert.doesNotMatch(text,/WARMUP|OBSERVED_PHASE/)
})
test('rolling results remain separate by issue time even with overlapping forecasts',()=>{
  const first={forecast:[{time:'2024-07-01T02:00:00',value:10}]},second={forecast:[{time:'2024-07-01T02:00:00',value:12}]}
  const rows=resultEntries({issues:[{issueTime:'2024-07-01T00:00:00',taskId:'JOB-one',hydrograph:first},{issueTime:'2024-07-01T01:00:00',taskId:'JOB-two',hydrograph:second}]})
  assert.equal(rows.length,2);assert.notEqual(rows[0].id,rows[1].id);assert.equal(rows[0].hydrograph,first);assert.equal(rows[1].hydrograph,second)
})
test('one staff submission delegates preparation to backend and does not submit separate rainfall or state tasks',async()=>{
  const calls=[],api=async(path,options)=>{calls.push({path,...options});return {operationId:'OP-one'}}
  const client=createDailyClient(api)
  await client.submit({operation:'eventSimulation',parameterSchemeId:'PARAMSET-one',context:{contextKey},events:[{id:'flood-1',start:'2024-07-01T00:00',end:'2024-07-06T08:00'}]})
  assert.equal(calls.length,1);assert.equal(calls[0].path,'operations');assert.equal(calls[0].method,'POST');assert.match(calls[0].body.requestId,/^OP-/)
  assert.equal(calls[0].body.operation,'eventSimulation');assert.equal(calls[0].body.parameterSchemeId,'PARAMSET-one')
  assert.equal('inputs' in calls[0].body,false);assert.equal('calibration' in calls[0].body,false)
})
test('historical hindcast requests preserve dates beyond the old 72 hour rainfall product limit',async()=>{
  let saved
  const client=createDailyClient(async(_path,options)=>{saved=options.body;return {operationId:'OP-one'}})
  await client.submit({operation:'hindcast',parameterSchemeId:'PARAMSET-one',issueTime:'2024-07-01T00:00',endTime:'2024-07-06T08:00',context:{contextKey}})
  assert.equal(saved.endTime,'2024-07-06T08:00');assert.equal(saved.issueTime,'2024-07-01T00:00');assert.equal('hourlyRainfall' in saved,false)
})
