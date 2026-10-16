"""Copy only the records needed for process-workbench acceptance; never alter source."""
import argparse
import json
import shutil
import sqlite3
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace',required=True)
parser.add_argument('--source',default='runtime-20261012')
parser.add_argument('--scheme',default='SCHEME-8a5eea9752cf4cf79ffec638f98cf086')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
source=(root/args.source).resolve();target=(root/args.workspace).resolve()
if not target.is_relative_to(root/'开发产物') or target.exists():
    raise ValueError('Choose a new isolated workspace under 开发产物')
target.mkdir(parents=True)
load=lambda path:json.loads(path.read_text(encoding='utf-8-sig'))
(target/'database').mkdir()
with sqlite3.connect(source.joinpath('database/hydrology.sqlite3').as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(target/'database/hydrology.sqlite3') as copy:
    original.backup(copy)
for name in ('parameter-schemes','parameters','routing-versions','science-versions','inputs/registry','inputs/sources'):
    if (source/name).exists():shutil.copytree(source/name,target/name)
tasks=set();products=set();state_ids=set();operations=[]
for path in source.glob('operations/*/status.json'):
    status=load(path);request=status.get('runConfig',{}).get('request',{})
    if (request.get('scheme',{}).get('id') or request.get('lineage',{}).get('schemeId') or status.get('lineage',{}).get('schemeId'))==args.scheme:
        shutil.copytree(path.parent,target/'operations'/path.parent.name)
        operations.append(status['taskId']);tasks.update(status.get('childTaskIds',[]))
        if (path.parent/'result.json').is_file():
            result=load(path.parent/'result.json')
            tasks.update(row['taskId'] for row in result.get('eventStates',[]) if row.get('taskId'))
for task in tasks:
    folder=source/'tasks'/task
    if not folder.is_dir():continue
    shutil.copytree(folder,target/'tasks'/task)
    request=load(folder/'request.json')
    product=request.get('inputs',{}).get('bundleVersionId')
    if product:products.add(product)
    result=load(folder/'result.json') if (folder/'result.json').is_file() else {}
    if result.get('endStateVersionId'):state_ids.add(result['endStateVersionId'])
for identifier in products:
    folder=source/'inputs/products'/identifier
    if folder.is_dir():shutil.copytree(folder,target/'inputs/products'/identifier)
    if (folder/'manifest.json').is_file():
        preparation=load(folder/'manifest.json').get('sourceTaskId')
        if preparation and (source/'input-tasks'/preparation).is_dir():
            shutil.copytree(source/'input-tasks'/preparation,target/'input-tasks'/preparation,dirs_exist_ok=True)
for identifier in state_ids:
    path=source/'states'/(identifier+'.json')
    if path.is_file():
        (target/'states').mkdir(exist_ok=True);shutil.copy2(path,target/'states'/path.name)
print(json.dumps({'workspace':str(target),'scheme':args.scheme,'operations':operations,'tasks':len(tasks),'products':len(products)},ensure_ascii=False),flush=True)
