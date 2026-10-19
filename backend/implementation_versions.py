"""Verified executable platform snapshots for replaying historical calculations.

Snapshots are runtime artifacts extracted from explicit local Git commits. They
never relax a hash check: the original vendor files and all recorded platform
modules must still produce the requested implementation identity.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
from common import ROOT, MODEL, RUNTIME, read_json, write_json
from platform_config import SETTINGS

LEGACY_PLATFORM_FILES = ('worker.py', 'routing_adapter.py', 'state_initialization_adapter.py', 'science_context.py',
                  'strict_forcing.py', 'calibration_groups.py', 'calibration_objectives.py', 'calibration_search.py',
                  'joint_calibration.py', 'routing_calibration.py', 'original_calibration_plan.py')
PLATFORM_FILES = LEGACY_PLATFORM_FILES + ('forecast_products.py',)
SNAPSHOT_LOCK=threading.RLock()

def digest(records):
    return hashlib.sha256(json.dumps(records,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()

def implementation_records(platform_root=ROOT, model=MODEL, platform_files=PLATFORM_FILES):
    paths = sorted((model/'code').rglob('*.py')) + [model/'scripts/run_formal_multievent_calibration.py']
    return ([{'path':path.relative_to(model).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
            + [{'path':'platform/'+name,'sha256':hashlib.sha256((platform_root/name).read_bytes()).hexdigest()} for name in platform_files])

def frozen_worker(expected, runtime=RUNTIME):
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):
        raise ValueError('历史计算程序版本号无效')
    folder=Path(runtime)/'implementations'/expected
    try: manifest=read_json(folder/'manifest.json')
    except FileNotFoundError: raise ValueError('模型实现或科学条件已更新且原计算程序快照缺失，请恢复原版本后复算') from None
    backend=folder/'backend'
    for row in manifest['platformFiles']:
        name=row['path']
        if Path(name).name!=name or not name.endswith('.py'):
            raise ValueError('历史程序文件路径无效')
        path=backend/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('历史计算程序文件缺失或摘要改变：'+name)
    identity_files = manifest.get('identityPlatformFiles', LEGACY_PLATFORM_FILES)
    if not isinstance(identity_files, (list, tuple)) or any(Path(name).name != name or not name.endswith('.py') for name in identity_files):
        raise ValueError('历史程序身份文件清单无效')
    actual=digest(implementation_records(backend, platform_files=identity_files))
    if manifest.get('modelImplementationSHA256')!=expected or actual!=expected:
        raise ValueError('历史计算程序或原模型文件已改变，不能按原程序复算')
    return backend/'worker.py'

def register_git_implementation(runtime, ref):
    repository=ROOT.parent
    def git(*args): return subprocess.check_output(['git','-C',str(repository),*args])
    commit=git('rev-parse','--verify',ref+'^{commit}').decode().strip()
    files=git('ls-tree','-r','--name-only',commit,'--','backend').decode().splitlines()
    blobs={Path(name).name:git('show',commit+':'+name) for name in files if name.startswith('backend/') and name.count('/')==1 and name.endswith('.py')}
    identity_files = PLATFORM_FILES if b'identityPlatformFiles' in blobs.get('implementation_versions.py', b'') else LEGACY_PLATFORM_FILES
    records=implementation_records(platform_files=identity_files)
    for row in records:
        if row['path'].startswith('platform/'):
            row['sha256']=hashlib.sha256(blobs[row['path'].split('/')[-1]]).hexdigest()
        else:
            original=git('show',commit+':backend/vendor/model/'+row['path'])
            if hashlib.sha256(original).hexdigest()!=row['sha256']:
                raise ValueError('模型源文件已变化，无法仅从平台模块恢复原版本：'+row['path'])
    identity=digest(records)
    folder=Path(runtime)/'implementations'/identity
    if (folder/'manifest.json').is_file():
        frozen_worker(identity,runtime)
        return {'modelImplementationSHA256':identity,'commit':commit,'existing':True}
    backend=folder/'backend';backend.mkdir(parents=True,exist_ok=True)
    for name,data in blobs.items(): (backend/name).write_bytes(data)
    config={key:str(SETTINGS[key]) for key in ('model','projects','frontend')}
    config.update(schemaVersion=1,workspace=str(Path(runtime).resolve()),port=8878)
    write_json(folder/'config/platform.json',config)
    write_json(folder/'manifest.json',{'modelImplementationSHA256':identity,'gitCommit':commit,'identityPlatformFiles':list(identity_files),
        'platformFiles':[{'path':name,'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(blobs.items())],
        'vendorFiles':[row for row in records if not row['path'].startswith('platform/')],
        'purpose':'exact historical executable replay; vendor data and source files remain at their registered paths'})
    frozen_worker(identity,runtime)
    return {'modelImplementationSHA256':identity,'commit':commit,'existing':False}

def snapshot_current(runtime):
    with SNAPSHOT_LOCK:
        records=implementation_records()
        identity=digest(records)
        folder=Path(runtime)/'implementations'/identity
        if (folder/'manifest.json').is_file():
            frozen_worker(identity,runtime)
            return identity
        blobs={path.name:path.read_bytes() for path in ROOT.glob('*.py')}
        backend=folder/'backend';backend.mkdir(parents=True,exist_ok=True)
        for name,data in blobs.items(): (backend/name).write_bytes(data)
        config={key:str(SETTINGS[key]) for key in ('model','projects','frontend')}
        config.update(schemaVersion=1,workspace=str(Path(runtime).resolve()),port=8878)
        write_json(folder/'config/platform.json',config)
        write_json(folder/'manifest.json',{'modelImplementationSHA256':identity,'identityPlatformFiles':list(PLATFORM_FILES),
            'platformFiles':[{'path':name,'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(blobs.items())],
            'vendorFiles':[row for row in records if not row['path'].startswith('platform/')],
            'purpose':'frozen executable platform snapshot for future replay'})
        frozen_worker(identity,runtime)
        return identity

def execution_environment(worker, runtime):
    if worker.resolve()==(ROOT/'worker.py').resolve(): return {}
    folder=worker.parent.parent
    config=folder/'config/runtime-context.json'
    # Paths relocate with a restored workspace. Scientific configuration and
    # executable bytes remain verified separately and are never replaced here.
    settings={key:str(SETTINGS[key]) for key in ('model','projects','frontend')}
    settings.update(schemaVersion=1,workspace=str(Path(runtime).resolve()),port=8878)
    write_json(config,settings)
    return {'XAJ_CONFIG':str(config)}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--git-ref',required=True)
    options=parser.parse_args()
    print(json.dumps(register_git_implementation(Path(options.workspace).resolve(),options.git_ref)))
