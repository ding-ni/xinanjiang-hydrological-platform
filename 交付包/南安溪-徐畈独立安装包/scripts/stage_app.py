#!/usr/bin/env python3
"""Copy an onedir app while excluding all non-Xufan fixture data."""
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path

def ignore(directory, names):
    base=Path(directory).as_posix().lower()
    excluded=set()
    for name in names:
        lowered=name.lower()
        if lowered in {'selinco','other-basin','other-resolution'}:
            if '/data/' in base or base.endswith('/data') or '/projects' in base or base.endswith('/projects'):
                excluded.add(name)
    return excluded

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--source',type=Path,required=True); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    source=args.source.resolve(); output=args.output.resolve()
    if not source.is_dir(): raise FileNotFoundError(source)
    if output.exists(): raise FileExistsError(f'Refusing to overwrite app staging directory: {output}')
    shutil.copytree(source,output,ignore=ignore)
    catalogs=[output/'frontend/public/data/projects/catalog.json',
              output/'frontend/dist-unified/data/projects/catalog.json']
    for catalog in catalogs:
        if not catalog.is_file():
            continue
        data=json.loads(catalog.read_text(encoding='utf-8-sig'))
        data['projects']=[row for row in data.get('projects',[]) if row.get('id')=='nananxi-xufan']
        if len(data['projects']) != 1:
            raise RuntimeError(f'Expected one South-Anxi project in {catalog}')
        catalog.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    prohibited=[]
    for path in output.rglob('*'):
        lower=path.as_posix().lower()
        if '/data/selinco/' in lower or '/data/projects/selinco/' in lower or '/public/data/projects/selinco/' in lower:
            prohibited.append(path)
    if prohibited: raise RuntimeError('Non-Xufan project data remains in app stage')
    print(f'Staged app: {output}')

if __name__=='__main__': main()
