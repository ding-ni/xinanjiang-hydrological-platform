import test from 'node:test'
import assert from 'node:assert/strict'
import { sameResultWorkspace, trackTemporary, resultsPurged, hasPendingResults, retentionChanged } from '../src/services/resultRetentionService.js'

test('forecast state and section viewing stay inside the current result workspace',()=>{
  globalThis.localStorage={getItem:()=> 'test-workspace'}
  globalThis.sessionStorage={getItem:()=> JSON.stringify([{id:'OP-forecast',sourceTaskIds:['JOB-forecast']}])}
  const forecast={path:'/forecast',query:{},params:{}}
  const state={path:'/state',query:{source:'JOB-forecast',operation:'OP-forecast'},params:{}}
  const sections={path:'/results/OP-forecast/sections',name:'result-sections',params:{id:'OP-forecast'},query:{}}
  assert.equal(sameResultWorkspace(state,forecast),true)
  assert.equal(sameResultWorkspace(forecast,state),true)
  assert.equal(sameResultWorkspace(sections,state),true)
  assert.equal(sameResultWorkspace({...forecast,query:{operation:'OP-forecast'}},forecast),true)
  assert.equal(sameResultWorkspace({path:'/simulation',query:{}},forecast),false)
  assert.equal(sameResultWorkspace({path:'/archive',query:{}},forecast),false)
  delete globalThis.localStorage
  delete globalThis.sessionStorage
})

test('purge notification forgets only removed pending results and refreshes all mounted result views',()=>{
  const storage=new Map()
  globalThis.localStorage={getItem:()=> 'test-workspace'}
  globalThis.sessionStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)}
  try{
    trackTemporary({id:'OP-removed',retention:'temporary'})
    trackTemporary({id:'OP-keep',retention:'temporary'})
    const revision=retentionChanged.value
    resultsPurged({purgedIds:['OP-removed']})
    assert.equal(retentionChanged.value,revision+1)
    assert.deepEqual(JSON.parse([...storage.values()][0]).map(row=>row.id),['OP-keep'])
    assert.equal(hasPendingResults(),true)
  }finally{delete globalThis.localStorage;delete globalThis.sessionStorage}
})
