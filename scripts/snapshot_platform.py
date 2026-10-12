"""Source snapshot plus a verified backup of the selected workspace."""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from platform_config import SETTINGS
from common import write_json
from workspace_backup import create_backup, digest, verify_backup
from workspace_service import WorkspaceLock


def snapshot(label):
    if not label or any(ch not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for ch in label):
        raise ValueError('Label must contain lowercase letters, digits or hyphens')
    destination = ROOT / '历史归档/统一系统迭代快照' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + label)
    destination.mkdir(parents=True, exist_ok=False)
    sources = []
    for part in ('frontend-demo/app/src', 'frontend-demo/app/public', 'frontend-demo/app/tests', 'frontend-demo/app/scripts', 'backend/tests', 'scripts', 'config'):
        sources += [p for p in (ROOT / part).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    for part in ('', 'backend', 'frontend-demo/app'):
        sources += [p for p in (ROOT / part).iterdir() if p.is_file()]
    records = []
    for source in sorted(set(sources)):
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        checksum = digest(source)
        if digest(target) != checksum: raise RuntimeError('Source snapshot verification failed')
        records.append({'path': relative.as_posix(), 'bytes': target.stat().st_size, 'sha256': checksum})
    # Only use the live service if it explicitly identifies the selected workspace.
    endpoint = f'http://127.0.0.1:{SETTINGS["port"]}/api/model/workspace'
    live = False
    try:
        with urlopen(endpoint, timeout=3) as reply:
            info = json.load(reply)
        live = any(row['id'] == 'workspace' and Path(row['path']).resolve() == SETTINGS['workspace'] for row in info['directories'])
    except HTTPError as error:
        raise RuntimeError('The running service does not support workspace backup. Stop it before taking an offline snapshot.') from error
    except URLError:
        # No reachable service; WorkspaceLock still checks other ports.
        pass
    except (ValueError, KeyError) as error:
        raise RuntimeError('Could not identify the running workspace.') from error
    if live:
        request = Request(endpoint + '/backups', data=b'{}', headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=600) as reply: backup = json.load(reply)
        shutil.copytree(backup['path'], destination / 'workspace')
        verify_backup(destination / 'workspace')
    else:
        with WorkspaceLock(SETTINGS['workspace']):
            backup = create_backup(SETTINGS['workspace'], destination / 'workspace')
    manifest = {'files': records, 'workspaceBackup': backup, 'workspaceSource': str(SETTINGS['workspace'])}
    write_json(destination / 'snapshot-manifest.json', manifest)
    return {'path': str(destination), 'sourceFiles': len(records), 'workspaceFiles': backup['files']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='platform')
    args = parser.parse_args()
    print(json.dumps(snapshot(args.label), ensure_ascii=False))
