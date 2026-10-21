#!/usr/bin/env python3
"""Write a SHA256 inventory for the app, seed and installer inputs."""
from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXCLUDE={'build','node_modules','.git','runtime','__pycache__'}

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def main():
    records=[]
    installer_dirs=sorted((path for path in (ROOT/'dist').glob('install-path-*') if any(path.glob('*Setup.exe'))), key=lambda path:path.stat().st_mtime, reverse=True)
    if not installer_dirs:
        raise SystemExit('No compiled installer directory found under dist/.')
    targets=[installer_dirs[0],ROOT/'app_entry.py',ROOT/'backend/check_runtime.py',
             ROOT/'seed/runtime-final',ROOT/'installer',ROOT/'使用说明.md']
    targets += [ROOT/'scripts'/name for name in (
        'build_seed.py','package_release.ps1','stage_app.py','verify_release.py','write_manifest.py')]
    targets += [ROOT/path for path in ('backend/artifact_cleanup.py', 'backend/result_lifecycle.py',
        'backend/server.py', 'backend/tests/test_artifact_cleanup.py',
        'frontend/src/components/model/ArtifactCleanup.vue', 'frontend/src/services/artifactCleanupService.js',
        'frontend/src/views/ResultWorkspace.vue', 'frontend/src/components/model/ResultSaveBar.vue',
        'frontend/src/components/model/ModelSchemeBar.vue')]
    candidates=[]
    for target in targets:
        if target.is_file(): candidates.append(target)
        elif target.is_dir(): candidates.extend(target.rglob('*'))
    for path in sorted(set(candidates)):
        if not path.is_file() or any(part in EXCLUDE for part in path.relative_to(ROOT).parts): continue
        if path.name in {'release-manifest.json'}: continue
        records.append({'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'bytes':path.stat().st_size})
    manifest={'schemaVersion':1,'package':'nananxi-xufan-standalone','platform':'Windows x64','files':records,'bytes':sum(r['bytes'] for r in records)}
    target=ROOT/'release-manifest.json'
    target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'path':str(target),'files':len(records),'bytes':manifest['bytes']},ensure_ascii=False))

if __name__=='__main__': main()
