import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {processOverlayReason,trialStorageKey,preferredCalculationRecord} from '../src/services/manualTrialWorkspace.js'

const result=()=>({hydrographs:{A:{times:['2024-06-01T00:00','2024-06-01T01:00'],
  obs:[{time:'2024-06-01T00:00',value:10},{time:'2024-06-01T01:00',value:null}],
  forecast:[{time:'2024-06-01T00:00',value:9},{time:'2024-06-01T01:00',value:8}]}}})
test('trial drafts and successful curve IDs are scoped to the actual workspace as well as model context and scheme',()=>{
  assert.notEqual(trialStorageKey('context','same-scheme','workspace-A'),trialStorageKey('context','same-scheme','workspace-B'))
  assert.notEqual(trialStorageKey('context','scheme-A','workspace-A'),trialStorageKey('context','scheme-B','workspace-A'))
})
test('a fresh session selects the automatic curve rather than metrics from a hidden historical trial',()=>{
  const records=[{taskId:'AUTO',manual:false},{taskId:'OLD-MANUAL',manual:true},{taskId:'SESSION',manual:true}]
  assert.equal(preferredCalculationRecord(records,null,[]).taskId,'AUTO')
  assert.equal(preferredCalculationRecord(records,null,['SESSION']).taskId,'SESSION')
  assert.equal(preferredCalculationRecord(records,'OLD-MANUAL',[]).taskId,'OLD-MANUAL')
  assert.equal(preferredCalculationRecord(records.slice(1),null,[]),null)
})
test('overlay eligibility follows actual period and observations, independent of program and driving digests',()=>{
  const a=result(),b=result()
  a.lineage={modelImplementationSHA256:'old'};b.processDrivingSHA256={A:'new'}
  assert.equal(processOverlayReason(a,b,'A'),'')
  b.hydrographs.A.obs[0].value=11
  assert.match(processOverlayReason(a,b,'A'),/实测流量序列不同/)
  b.hydrographs.A.times[0]='2024-06-02T00:00'
  assert.match(processOverlayReason(a,b,'A'),/评价时段不同/)
  assert.match(processOverlayReason(a,b,'B'),/未计算当前洪水/)
})
test('mode switching isolates warmup/origin settings without carrying a continuous start into event requests',()=>{
  const store=readFileSync(new URL('../src/stores/calibration.js',import.meta.url),'utf8')
  const method=store.slice(store.indexOf('    setCalibrationMode('),store.indexOf('    setEvent(')).trim().replace(/,$/,'')
  const actions=new Function('return {'+method+'}')()
  const cal={...actions,calibrationMode:'event',operationMode:'calibration',statePolicy:'independent-warmup',warmupDays:11,
    continuousOrigin:null,initialStateVersionId:'STATE-test',calibrationStart:'2024-06-10T00:00',scienceConditionsDraft:{statePolicy:{}},markNeedsRun(){}}
  cal.setCalibrationMode('continuous')
  assert.equal(cal.initialStateVersionId,null)
  assert.equal(cal.warmupDays,7)
  cal.warmupDays=20;cal.continuousOrigin='2024-01-01T00:00'
  cal.setCalibrationMode('event')
  assert.equal(cal.warmupDays,11)
  assert.equal(cal.continuousOrigin,null)
  assert.equal(cal.statePolicy,'independent-warmup')
  cal.setCalibrationMode('continuous')
  assert.equal(cal.warmupDays,20)
  assert.equal(cal.continuousOrigin,'2024-01-01T00:00')
})
