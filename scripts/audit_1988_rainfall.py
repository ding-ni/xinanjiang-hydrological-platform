"""Read-only audit of the 1988 event in original year data and MDB."""
import json
from pathlib import Path
import sys
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
sys.stdout.reconfigure(encoding='utf-8')
from common import read_json,write_json,WORKSPACE
from mdb_import import query_mdb_copy
from input_products import file_sha256
public=WORKSPACE/'frontend-demo/app/public/data/projects/nananxi-xufan'
catalog=read_json(public/'observations/catalog.json')
year=read_json(public/'observations/rain/1988.json')
first=datetime(1988,8,8,7);last=datetime(1988,8,9,20)
lo=int((first-datetime(1988,1,1)).total_seconds()/3600);hi=int((last-datetime(1988,1,1)).total_seconds()/3600)
stations={str(row['legacyCode']):row for row in catalog['rain']['stations']}
report={'period':{'start':first.isoformat(),'end':last.isoformat()},'yearFileSHA256':file_sha256(public/'observations/rain/1988.json'),'stations':[],'conversion':catalog['source']}
for index,code in enumerate(year['stationCodes']):
    values=[row[2] for row in year['rows'] if row[1]==index and lo<=row[0]<=hi]
    if values:
        report['stations'].append({'code':str(code),'name':stations[str(code)]['name'],'records':len(values),'totalMm':sum(value for value in values if value is not None),'maximumMm':max(value for value in values if value is not None),'zeroCount':values.count(0),'missing':values.count(None)})
source=WORKSPACE/catalog['source']['database']
target=WORKSPACE/'开发产物/rain1988-audit'
try:
    rows=query_mdb_copy(source,target,"SELECT HS_Code,YMDHM,DTRN FROM ReFactorDT WHERE YMDHM>=#08/08/1988 07:00:00# AND YMDHM<=#08/09/1988 20:00:00# ORDER BY HS_Code,YMDHM",timeout=60)
    report['mdbStatus']='read';report['mdbRows']=len(rows);report['mdbSHA256']=file_sha256(source)
    summary={}
    for row in rows:
        code=str(row['HS_Code']);summary.setdefault(code,[]).append(float(row['DTRN']) if row.get('DTRN') not in (None,'') else None)
    report['mdbStations']={code:{'records':len(values),'totalMm':sum(value for value in values if value is not None),'maximumMm':max((value for value in values if value is not None),default=None),'zeroCount':values.count(0),'missing':values.count(None)} for code,values in summary.items()}
except (ValueError,RuntimeError,FileNotFoundError) as error:
    report['mdbStatus']='unavailable';report['mdbError']=str(error)
write_json(target/'report.json',report)
print(json.dumps(report,ensure_ascii=False),flush=True)
