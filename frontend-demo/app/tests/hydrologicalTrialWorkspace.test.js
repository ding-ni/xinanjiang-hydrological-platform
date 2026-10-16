import test from 'node:test'
import assert from 'node:assert/strict'
import {fixedTrialRequest,processComparisonIdentity} from '../src/services/manualTrialWorkspace.js'
import {sortFloodRows,filterFloodRows} from '../src/services/floodTablePresentation.js'
import {responsiveChartOption,timeAxisLabel} from '../src/services/chartLayout.js'

test('manual trial freezes only selected flood without changing purpose, search settings or scheme',()=>{
  const request={scheme:{id:'S',revision:2},sourcePlan:{operation:'search'},period:{mode:'event',events:[{id:'a',start:'2020-01-01',end:'2020-01-02',purpose:'率定'},{id:'b',start:'2020-02-01',end:'2020-02-02',purpose:'独立检验'}],eventSet:{},eventAssignment:{}},calibration:{initialParameters:{SM:20},searchParameters:{SM:{minimum:8,maximum:60}},optimizer:{maxEvaluations:120}}}
  const before=structuredClone(request),trial=fixedTrialRequest(request,{params:{SM:30},processId:'b',scheme:request.scheme})
  assert.deepEqual(request,before)
  assert.equal(trial.request.scheme,undefined)
  assert.equal(trial.request.sourcePlan,undefined)
  assert.equal(trial.request.calibration.searchParameters,undefined)
  assert.deepEqual(trial.request.period.events,[before.period.events[1]])
  assert.equal(trial.request.calibration.initialParameters.SM,30)
  assert.equal(trial.request.calibration.optimizer.algorithm,'FIXED_PARAMETERS')
  assert.deepEqual(trial.trialContext.scheme,{id:'S',revision:2})
})
test('current continuous window retains original origin rather than skipping antecedent simulation',()=>{
  const trial=fixedTrialRequest({calibration:{},period:{mode:'continuous',calibrationStart:'2020-01-08T00:00',calibrationEnd:'2020-01-09T00:00',validationStart:'2020-02-01T00:00',validationEnd:'2020-02-02T00:00',warmupDays:7}},{params:{},processId:'VALIDATION'})
  assert.equal(trial.request.period.manualProcessId,'VALIDATION')
  assert.equal(trial.request.calibration.continuousOrigin,'2020-01-01T00:00:00')
})
test('curve compatibility ignores searched parameters and other floods, includes PET, input and origin',()=>{
  const base={context:{contextKey:'X'},inputs:{sourceBundleVersionId:'A',bundleVersionId:'prepared-one'},period:{mode:'event',warmupDays:7,events:[{id:'a',start:'2020-01-01',end:'2020-01-02'}]},calibration:{initialParameters:{SM:20},statePolicy:'independent-warmup'}}
  const other=structuredClone(base);other.inputs.bundleVersionId='prepared-two';other.period.events.push({id:'b'});other.calibration.initialParameters.SM=30
  assert.equal(processComparisonIdentity(base,{},'a'),processComparisonIdentity(other,{},'a'))
  other.inputs.evaporation={valueMmPerHour:0.1}
  assert.notEqual(processComparisonIdentity(base,{},'a'),processComparisonIdentity(other,{},'a'))
})
test('flood sorting is numeric, stable, empty last in both directions, before pagination',()=>{
  const rows=[{id:'a',start:'2007-01-01',observedPeak:'9'},{id:'b',start:'2007-01-02',observedPeak:'100'},{id:'c',start:'2007-01-03',observedPeak:null},{id:'d',start:'2008-01-01',observedPeak:10}]
  assert.deepEqual(sortFloodRows(rows,'peak','asc').map(row=>row.id),['a','d','b','c'])
  assert.deepEqual(sortFloodRows(rows,'peak','desc').map(row=>row.id),['b','d','a','c'])
  assert.deepEqual(filterFloodRows(rows,{year:'2007',minimum:10}).map(row=>row.id),['b'])
  assert.equal(rows[0].id,'a')
})
test('time ticks respond to width and zoom without changing non-time axes',()=>{
  const option={xAxis:{type:'category',data:Array.from({length:240},(_,i)=>'2007-01-01T'+String(i%24).padStart(2,'0')+':00')},dataZoom:[{type:'slider'}]}
  const narrow=responsiveChartOption(option,360),wide=responsiveChartOption(option,1000),zoomed=responsiveChartOption(option,360,{start:50,end:60})
  assert.ok(narrow.xAxis.axisLabel.interval>wide.xAxis.axisLabel.interval)
  assert.ok(zoomed.xAxis.axisLabel.interval<narrow.xAxis.axisLabel.interval)
  assert.equal(zoomed.dataZoom[0].start,50)
  assert.equal(timeAxisLabel('2007-01-01T07:00'),'2007-01-01\n07:00')
  const categorical={type:'category',data:['A','B'],axisLabel:{rotate:45}}
  assert.deepEqual(responsiveChartOption({xAxis:categorical},300).xAxis,categorical)
})
