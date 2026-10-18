import test from 'node:test'
import assert from 'node:assert/strict'
import {stateScale,stateNumber} from '../src/services/stateColorScale.js'
test('event color range is stable and capacity range uses the actual parameter',()=>{
  stateScale('freeWater','event',{S:{min:.01,max:23}}).stops.forEach((value,index)=>assert.ok(Math.abs(value-[.01,5.7575,11.505,17.2525,23][index])<1e-12))
  assert.equal(stateScale('freeWater','fixed',{}, {SM:23.47}).max,23.47)
  assert.equal(stateScale('wd','fixed',{}, {WM:120,WUM:20,WLM:70}).max,30)
})
test('equal states do not invent differences and numeric formatting preserves zeroes',()=>{
  assert.equal(stateScale('wl','event',{WL:{min:72,max:72+1e-13}}).equal,true)
  assert.equal(stateScale('RG','event',{RG:{min:1e-8,max:1e-5}}).equal,false)
  assert.equal(stateNumber(10000),'10000')
  assert.equal(stateNumber(null),'—')
})
