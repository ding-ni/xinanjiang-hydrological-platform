// Presentation derived from saved results. Never changes the search objective.
const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null
export const purposeNames = { CALIBRATION:'率定', VALIDATION:'验证', INDEPENDENT_TEST:'独立检验', INDEPENDENT:'独立检验', '率定':'率定', '验证':'验证', '独立检验':'独立检验' }
export function processMetrics(metrics = {}) {
  return { nse:finite(metrics.nse ?? metrics.NSE), peak:finite(metrics.peak ?? metrics.peakErr ?? metrics.peak_error_percent ?? metrics.peak_error_pct),
    time:finite(metrics.time ?? metrics.timeErrH ?? metrics.peak_time_error_hours ?? metrics.peak_time_error_h), volume:finite(metrics.volume ?? metrics.volErr ?? metrics.volume_error_percent ?? metrics.volume_error_pct) }
}
export function peakDetails(graph = {}) {
  const obs = new Map((graph.obs || []).map(row => [String(row.time).replace(' ','T'), finite(row.value)]))
  const pairs = (graph.forecast || []).map(row => ({time:row.time, simulated:finite(row.value), observed:obs.get(String(row.time).replace(' ','T'))})).filter(row => row.observed != null && row.simulated != null)
  if (!pairs.length) return null
  const observed = pairs.reduce((a,b) => b.observed > a.observed ? b : a)
  const simulated = pairs.reduce((a,b) => b.simulated > a.simulated ? b : a)
  return { observedTime:observed.time, simulatedTime:simulated.time, observedPeak:observed.observed, simulatedPeak:simulated.simulated,
    hours:Math.abs(Date.parse(simulated.time)-Date.parse(observed.time))/3600000 }
}
export function mergedPeakDetails(result,group) {
  const members=group.memberEventIds || result.overlapGroups?.find(row=>row.id===group.groupId)?.memberEventIds || []
  const graphs=members.map(id=>result.hydrographs?.[id]).filter(Boolean)
  return peakDetails({obs:graphs.flatMap(graph=>graph.obs || []),forecast:graphs.flatMap(graph=>graph.forecast || [])})
}
function metricsFromGraph(graph) {
  const saved = processMetrics(graph.metrics)
  if (Object.values(saved).every(value => value != null)) return saved
  const obs = new Map((graph.obs || []).map(row => [String(row.time).replace(' ','T'), finite(row.value)]))
  const pairs = (graph.forecast || []).map(row => [obs.get(String(row.time).replace(' ','T')),finite(row.value)]).filter(row => row.every(value => value != null))
  if (!pairs.length) return saved
  const sum = pairs.reduce((n,row) => n+row[0],0), mean=sum/pairs.length
  const denominator=pairs.reduce((n,row) => n+(row[0]-mean)**2,0), peaks=peakDetails(graph)
  const derived={nse:denominator ? 1-pairs.reduce((n,row)=>n+(row[1]-row[0])**2,0)/denominator:null,
    peak:peaks?.observedPeak ? 100*(peaks.simulatedPeak-peaks.observedPeak)/peaks.observedPeak:null,
    time:peaks?.hours ?? null,volume:sum ? 100*(pairs.reduce((n,row)=>n+row[1],0)-sum)/sum:null}
  return Object.fromEntries(Object.keys(saved).map(key=>[key,saved[key] ?? derived[key]]))
}
const average = values => values.length ? values.reduce((a,b)=>a+b,0)/values.length : null
const median = values => { const sorted=[...values].sort((a,b)=>a-b),n=sorted.length;return n ? (sorted[Math.floor((n-1)/2)]+sorted[Math.floor(n/2)])/2:null }
export function evaluationPresentation(result = {}) {
  const period=result.frozenRequest?.period || {}, windows=period.events || period.windows || []
  const saved=new Map((result.eventMetrics || []).map(row => [row.eventId,row]))
  const graphs=result.hydrographs || {}
  const ids=[...new Set([...windows.map(row=>row.id),...saved.keys(),...Object.keys(graphs)])]
  const events=ids.map(id => {const window=windows.find(row=>row.id===id)||{},metrics=saved.get(id);return {id,name:window.name || id,start:window.start,end:window.end,
    purpose:purposeNames[metrics?.group || window.purpose] || window.purpose || '未划分',...metricsFromGraph({...graphs[id],metrics:metrics || graphs[id]?.metrics})}}).filter(row => row.purpose!=='未使用')
  const summaries=['率定','验证','独立检验'].map(purpose => {
    const rows=events.filter(row=>row.purpose===purpose),key=Object.keys(purposeNames).find(key=>purposeNames[key]===purpose)
    const stored=result.windowSummary?.[key]
    const metricValues=name=>rows.map(row=>row[name]).filter(value=>value!=null)
    const fallback=purpose==='率定'?result.calibrationMetrics:purpose==='验证'?result.validationMetrics:result.independentTest
    return {purpose,count:rows.length || stored?.event_count || fallback?.event_count || 0,
      nse:rows.length?average(metricValues('nse')):finite(stored?.mean_NSE ?? fallback?.mean_NSE ?? fallback?.nse),
      peak:rows.length?average(metricValues('peak').map(Math.abs)):finite(stored?.mean_abs_peak_error_percent ?? fallback?.mean_abs_peak_error_percent),
      time:rows.length?median(metricValues('time')):finite(stored?.median_peak_time_error_hours ?? fallback?.median_peak_time_error_hours),
      volume:rows.length?average(metricValues('volume').map(Math.abs)):finite(stored?.mean_abs_volume_error_percent ?? fallback?.mean_abs_volume_error_percent),
      validCounts:Object.fromEntries(['nse','peak','time','volume'].map(name=>[name,metricValues(name).length])),
      basis:rows.length||stored?'逐场汇总':'合并评价'}
  })
  return {events,summaries}
}
export function searchScore(value) { const number=finite(value);return number==null?null:number===0?0:-number }
export function parameterAtBound(row) {
  if (!row.active || finite(row.value)==null) return ''
  const near=value=>finite(value)!=null && Math.abs(Number(row.value)-Number(value)) <= 1e-9*Math.max(1,Math.abs(Number(value)))
  return near(row.minimum)?'达到下限':near(row.maximum)?'达到上限':''
}
export function recordType(result = {}) { return result.evaluationMode==='manual'?'人工试调':result.optimizer?'自动率定':'模拟' }
