import test from 'node:test'
import assert from 'node:assert/strict'
import { sameResultWorkspace } from '../src/services/resultRetentionService.js'

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
