"""Exercise bounded queries against the user's immutable MDB snapshot."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from input_products import InputProducts
from common import read_json,write_json
from scheme_inputs import query_mdb_window
from mdb_import import query_mdb_copy

products=InputProducts(runtime_root=Path('runtime').resolve())
source=products.source('SRC-MDB-dcc25a7aab37d70ee2cd4259a3fa3cc15edae30da1b7b9101b6fd03aa8543893')
snapshot=products._path(read_json(source['path'])['databaseFile'])
output=Path('开发产物/mdb-query-acceptance').resolve()
parser=argparse.ArgumentParser();parser.add_argument('--single-hour',action='store_true');parser.add_argument('--probe',action='store_true');options=parser.parse_args()
end='2024-06-19T00:00' if options.single_hour else '2024-06-19T23:00'
if options.probe:
    print(query_mdb_copy(snapshot,output,'SELECT TOP 1 HS_Code,YMDHM,DTRN FROM ReFactorDT',timeout=60))
    raise SystemExit(0)
try:
    rain=query_mdb_window(snapshot,output,"SELECT HS_Code,YMDHM,DTRN FROM ReFactorDT WHERE HS_Code=87001 AND YMDHM{window} ORDER BY YMDHM",'2024-06-19T00:00',end)
    flow=query_mdb_window(snapshot,output,"SELECT stcd,ymdhm,q FROM st_river_r WHERE stcd='CGB087012' AND ymdhm{window} ORDER BY ymdhm",'2024-06-19T00:00',end)
except (ValueError,RuntimeError) as error:
    write_json(output/'acceptance.json',{'sourceId':source['sourceId'],'status':'driver-query-failed','error':str(error),'singleQueryTimeoutSeconds':60})
    raise
assert rain and flow, 'chosen source lacks the acceptance day'
result={'sourceId':source['sourceId'],'start':'2024-06-19T00:00','end':end,'rainRows':len(rain),'flowRows':len(flow),'singleQueryTimeoutSeconds':60}
write_json(output/'acceptance.json',result)
print(result)
