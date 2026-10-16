export function timeAxisLabel(value) {
  const text=String(value).replace(' ','T')
  return /^\d{4}-\d{2}-\d{2}T/.test(text) ? `${text.slice(0,10)}\n${text.slice(11,16)}` : String(value)
}

export function responsiveChartOption(option, width, zoom) {
  const copy={...option}
  const slots=Math.max(2,Math.min(9,Math.floor((width-90)/110)))
  const axes=Array.isArray(option.xAxis)?option.xAxis:[option.xAxis]
  copy.xAxis=axes.filter(Boolean).map(axis=>{
    if(axis.type!=='category'||!axis.data?.some(value=>/^\d{4}-\d{2}-\d{2}[ T]/.test(String(value))))return axis
    const count=axis.data?.length||0
    const shown=Math.max(1,Math.ceil(count*((zoom?.end??100)-(zoom?.start??0))/100))
    return {...axis,axisLabel:{...axis.axisLabel,interval:Math.max(0,Math.ceil(shown/slots)-1),hideOverlap:true,showMinLabel:false,showMaxLabel:false,fontSize:12,margin:12,formatter:timeAxisLabel}}
  })
  if(!Array.isArray(option.xAxis))copy.xAxis=copy.xAxis[0]
  if(option.dataZoom)copy.dataZoom=option.dataZoom.map(row=>({...row,...(zoom?{start:zoom.start,end:zoom.end}:{}),...(row.type==='slider'?{showDetail:false}:{} )}))
  return copy
}
