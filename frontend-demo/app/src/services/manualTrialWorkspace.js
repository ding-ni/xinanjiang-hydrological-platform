const clone=value=>JSON.parse(JSON.stringify(value))
export function trialStorageKey(context,scheme){return `hydrology-manual-trial:${context}:${scheme}`}
export function readTrialWorkspace(context,scheme){try{return JSON.parse(sessionStorage.getItem(trialStorageKey(context,scheme))||'null')}catch{return null}}
export function saveTrialWorkspace(context,scheme,value){sessionStorage.setItem(trialStorageKey(context,scheme),JSON.stringify(value))}
export function fixedTrialRequest(request,{params,scope='current',processId,scheme,source,draftRevision}){
  const result=clone(request)
  result.requestId='TRIAL-'+crypto.randomUUID()
  delete result.sourcePlan;delete result.scheme
  result.calibration={...result.calibration,initialParameters:clone(params),evaluationMode:'manual',optimizer:{algorithm:'FIXED_PARAMETERS',maxEvaluations:1}}
  for(const key of ['target','jointProfile','searchParameters','routingSearchParameters','searchParameterOrder'])delete result.calibration[key]
  if(scope==='current'&&result.period.mode==='event'){
    const row=result.period.events.find(row=>row.id===processId)
    if(!row||row.purpose==='未使用')throw new Error('请先选择方案内的一场洪水')
    result.period.events=[row]
    result.period.eventSet={...result.period.eventSet,selectedEventIds:[row.id],snapshot:[row]}
    result.period.eventAssignment=null
  }
  if(scope==='current'&&result.period.mode==='continuous'){
    const prefix={CALIBRATION:'calibration',VALIDATION:'validation',INDEPENDENT:'independent'}[processId]
    if(!prefix||!result.period[prefix+'Start']||!result.period[prefix+'End'])throw new Error('请选择有效评价期')
    result.period.manualProcessId=processId
    delete result.period.windows
    if(!result.calibration.continuousOrigin){
      const time=new Date(result.period.calibrationStart)
      time.setHours(time.getHours()-Number(result.period.warmupDays||0)*24)
      const pad=value=>String(value).padStart(2,'0')
      result.calibration.continuousOrigin=`${time.getFullYear()}-${pad(time.getMonth()+1)}-${pad(time.getDate())}T${pad(time.getHours())}:00:00`
    }
    result.calibration.statePolicy='continuous-origin'
  }
  if(result.inputs?.rainfallMethod!=='stationIdw')delete result.inputs?.stationNetwork
  return {request:result,trialContext:{scheme,scope,processId,parameterSource:source,draftRevision:draftRevision??null}}
}

export function processComparisonIdentity(request,result,processId){
  if(!request)return null
  const period=request.period||{}
  const row=(period.events||period.windows||[]).find(row=>row.id===processId)
  if(!row&&period.mode==='event')return null
  const lineage={...request.lineage,...result?.lineage}
  // This digest covers the actual antecedent rain, PET, observations and grid
  // order for this flood. A full replay may omit the preparation parent ID
  // while retaining exactly the same saved arrays.
  const driving=result?.processDrivingSHA256?.[processId]||null
  const normalize=value=>{if(Array.isArray(value))return value.map(normalize);if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,normalize(value[key])]));return value}
  return JSON.stringify(normalize({context:request.context?.contextKey,process:row?{id:row.id,start:row.start,end:row.end}:period,
    driving,
    inputs:driving?null:request.inputs?.sourceBundleVersionId||request.inputs?.bundleVersionId,network:request.inputs?.stationNetwork,warmup:period.warmupDays,
    statePolicy:request.calibration?.statePolicy,origin:request.calibration?.continuousOrigin||period.continuousOrigin,
    science:lineage.petConfigurationSHA256||request.science?.versionId,evaporation:request.inputs?.evaporation,
    interpolation:{method:request.inputs?.rainfallMethod,power:request.inputs?.idwPower,minimum:request.inputs?.minimumStations},
    program:lineage.modelImplementationSHA256,topology:lineage.topologySHA256}))
}
