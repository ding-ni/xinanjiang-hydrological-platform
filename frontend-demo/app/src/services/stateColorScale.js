const fields={soil:'W_WM',wu:'WU',wl:'WL',wd:'WD',freeWater:'S',RS:'RS',RI:'RI',RG:'RG'}
const fixed={soil:1,RS:10,RI:2,RG:1}
export function stateScale(field, mode, ranges={}, parameters={}) {
  const raw=fields[field],range=ranges[raw]
  const capacity={wu:parameters.WUM,wl:parameters.WLM,wd:parameters.WM-parameters.WUM-parameters.WLM,freeWater:parameters.SM}[field]
  const minimum=mode==='event'?range?.min:0, maximum=mode==='event'?range?.max:capacity??fixed[field]
  if(!Number.isFinite(minimum)||!Number.isFinite(maximum))return null
  const equal=Math.abs(maximum-minimum)<=1e-10*Math.max(1,Math.abs(maximum))
  return {min:minimum,max:maximum,equal,stops:equal?[minimum]:Array.from({length:5},(_,i)=>minimum+(maximum-minimum)*i/4),colors:['#f0f9e8','#bae4bc','#7bccc4','#43a2ca','#0868ac'],label:mode==='event'?'本场全部时刻范围':'固定范围'}
}
export function stateNumber(value){if(value==null||!Number.isFinite(Number(value)))return '—';return Number(Number(value).toPrecision(5)).toString()}
