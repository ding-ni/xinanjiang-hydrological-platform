#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sqlite3
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]); args=parser.parse_args()
    root=args.root.resolve(); manifest=json.loads((root/'release-manifest.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        path=root/row['path'];
        if not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']: raise SystemExit(f'校验失败: {row["path"]}')
    seed=root/'seed/runtime-final'
    db=seed/'database/hydrology.sqlite3'
    conn=sqlite3.connect(db)
    tables={r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    required={'observations','source_files','observation_coverage','flood_events','stations','station_aliases','station_networks','river_sections','preparation_defaults'}
    forbidden={'task_index','result_metadata','state_metadata','schemes','model_schemes','model_scheme_revisions','model_scheme_editors','model_scheme_parameters','model_scheme_prepared_inputs'}
    if not required <= tables: raise SystemExit(f'种子库缺少表: {sorted(required-tables)}')
    if forbidden & tables: raise SystemExit(f'种子库含成果/方案表: {sorted(forbidden&tables)}')
    counts={table:conn.execute(f'select count(*) from {table}').fetchone()[0] for table in ('observations','source_files','observation_coverage','flood_events','stations')}
    if not counts['observations'] or not counts['source_files']: raise SystemExit('观测或来源为空')
    setup=next((root/row['path'] for row in manifest['files'] if row['path'].endswith('Setup.exe')),None)
    if setup is None: raise SystemExit('Setup.exe 未纳入校验清单')
    print(json.dumps({'verified':True,'files':len(manifest['files']),'counts':counts,'setup':str(setup),'seed':'seed/runtime-final'},ensure_ascii=False))

if __name__=='__main__': main()
