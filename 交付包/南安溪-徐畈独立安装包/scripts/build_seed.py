#!/usr/bin/env python3
"""Build a clean runtime seed without overwriting an existing seed."""
from __future__ import annotations
import argparse, hashlib, json, shutil, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
PRODUCT = 'INPUT-MDB-4b0ebc43b1144d46a2a85b850e9a0528'
SOURCE = 'SRC-MDB-dcc25a7aab37d70ee2cd4259a3fa3cc15edae30da1b7b9101b6fd03aa8543893'
DATA_TASK = 'DATA-mdb-4bca7419b11a423eaad483240136d9a7'
KEEP = {'projects','stations','station_aliases','source_files','observations','observation_coverage','flood_events','schema_migrations','workspace_drafts','station_networks','river_sections','preparation_defaults'}

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()

def manifest_digest(value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

def sanitize_workspace(payload):
    document=json.loads(payload)
    current=document.get('workspaces',{}).get('nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km',{})
    allowed={'algorithm','backendContractVersion','calibrationMode','calibrationTarget','candidateWarmupHours','contextKey','criteria','domainId','evaporationMode','fixedEventSimulation','idwPower','minimumStations','modelConfigId','modelScale','operationMode','outputStepHours','rainfallMethod','searchStrategy','statePolicy','targetMode','warmupDays'}
    clean={key:value for key,value in current.items() if key in allowed}
    return json.dumps({'schemaVersion':1,'workspaces':{'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km':clean}},ensure_ascii=False,separators=(',',':'))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--output', type=Path, default=ROOT/'seed/runtime')
    args = parser.parse_args()
    repo, output = args.repo.resolve(), args.output.resolve()
    if output.exists() and any(output.iterdir()): raise FileExistsError(f'拒绝覆盖非空种子目录: {output}')
    output.mkdir(parents=True, exist_ok=True)
    target = output/'database/hydrology.sqlite3'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): raise FileExistsError(f'拒绝覆盖数据库: {target}')
    source = repo/'runtime/database/hydrology.sqlite3'
    src, dst = sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True), sqlite3.connect(target)
    try:
        src.backup(dst)
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not KEEP <= tables: raise RuntimeError(f'数据库缺少保留表: {sorted(KEEP-tables)}')
        dst.execute('PRAGMA foreign_keys=OFF')
        for table in sorted(tables-KEEP): dst.execute('DROP TABLE "'+table.replace('"','""')+'"')
        dst.execute('DELETE FROM projects WHERE id<>?', ('nananxi-xufan',))
        for table in ('stations','station_aliases','source_files','observations','observation_coverage','flood_events','river_sections'):
            dst.execute(f'DELETE FROM "{table}" WHERE project_id<>?', ('nananxi-xufan',))
        for key,payload in list(dst.execute('SELECT key,payload FROM workspace_drafts')):
            if key == 'hydro_calibration_workspaces_v1':
                dst.execute("UPDATE workspace_drafts SET payload=?,revision=0,updated_at='' WHERE key=?", (sanitize_workspace(payload),key))
            elif key == 'hydro_flood_event_overrides_v1:nananxi-xufan':
                dst.execute("UPDATE workspace_drafts SET revision=0,updated_at='' WHERE key=?", (key,))
            else: dst.execute('DELETE FROM workspace_drafts WHERE key=?', (key,))
        dst.commit(); dst.execute('VACUUM')
    finally: src.close(); dst.close()
    inputs=output/'inputs'
    for name in ('products','sources','registry'): (inputs/name).mkdir(parents=True, exist_ok=True)
    product_dst=inputs/f'products/{PRODUCT}'
    shutil.copytree(repo/f'runtime/inputs/products/{PRODUCT}', product_dst)
    product_manifest=json.loads((product_dst/'manifest.json').read_text(encoding='utf-8'))
    product_manifest['files']=[row for row in product_manifest.get('files',[]) if 'input-tasks/DATA-mdb-7a4f13a667724e709ad7b78876e8e9cc/' not in row.get('path','')]
    product_digest=manifest_digest(product_manifest)
    (product_dst/'manifest.json').write_text(json.dumps(product_manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    shutil.copytree(repo/f'runtime/inputs/sources/{SOURCE}', inputs/f'sources/{SOURCE}')
    shutil.copy2(repo/f'runtime/inputs/registry/{PRODUCT}.json', inputs/f'registry/{PRODUCT}.json')
    task_src=repo/f'runtime/input-tasks/{DATA_TASK}'; task_dst=output/f'input-tasks/{DATA_TASK}'; task_dst.mkdir(parents=True, exist_ok=True)
    for name in ('request.json','result.json','status.json','source-quality-config-snapshot.json'):
        shutil.copy2(task_src/name, task_dst/name)
    result_path=task_dst/'result.json'
    result=json.loads(result_path.read_text(encoding='utf-8'))
    result['manifestSHA256']=sha(product_dst/'manifest.json')
    result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    registry_path=inputs/f'registry/{PRODUCT}.json'
    registration=json.loads(registry_path.read_text(encoding='utf-8'))
    registration['manifestSHA256']=sha(product_dst/'manifest.json')
    registry_path.write_text(json.dumps(registration,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    db=sqlite3.connect(target)
    counts={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ('observations','source_files','observation_coverage','flood_events','stations')}
    forbidden={'task_index','result_metadata','state_metadata','schemes','model_schemes','model_scheme_revisions','model_scheme_editors'}
    leftovers=forbidden & {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    if leftovers: raise RuntimeError(f'结果/方案表仍存在: {sorted(leftovers)}')
    report={'schemaVersion':1,'projectId':'nananxi-xufan','resolutionId':'1km','database':{'path':'database/hydrology.sqlite3','sha256':sha(target),'bytes':target.stat().st_size,'counts':counts},'backupInputProduct':PRODUCT,'backupMdbSource':SOURCE,'sourceTaskMetadata':DATA_TASK}
    (output/'seed-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
