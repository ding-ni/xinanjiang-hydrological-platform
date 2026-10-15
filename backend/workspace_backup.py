"""Verified full-workspace backup and restore to a new directory."""
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from common import RUNTIME, now, read_json, write_json
from workspace_service import WorkspaceLock, workspace_identity
from platform_config import APP_VERSION

PARTS = ('tasks', 'states', 'parameters', 'parameter-schemes', 'operations', 'inputs', 'input-tasks', 'routing-versions', 'science-versions', 'implementations', 'projects', 'exports', 'forecast-rain')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_reference(root, raw):
    if not isinstance(raw, str) or not raw or '\\' in raw or ':' in raw:
        raise ValueError('备份文件路径无效')
    relative = Path(raw)
    target = (root / relative).resolve()
    if relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(root.resolve()):
        raise ValueError('备份文件路径超出目录')
    return target


def create_backup(root, destination=None):
    root = Path(root).resolve()
    identity = workspace_identity(root)
    destination = Path(destination).resolve() if destination else root / 'backups' / ('BACKUP-' + uuid4().hex)
    if destination.exists():
        raise ValueError('备份目录已存在，请使用新目录')
    if destination == root or destination in root.parents or any(destination.is_relative_to(root / part) for part in PARTS + ('database',)):
        raise ValueError('备份位置不能位于计算资料目录内')
    # Offline callers hold the workspace lock; live callers hold both queue locks.
    for part in ('tasks', 'input-tasks', 'operations'):
        for status in (root / part).glob('*/status.json'):
            if read_json(status).get('status') in {'running', 'queued', 'cancelling'}:
                raise RuntimeError('请等待当前计算完成后备份')
    sources = [root / 'workspace.json']
    for part in PARTS:
        base = root / part
        if base.is_symlink():
            raise ValueError('备份目录包含外部链接')
        if base.exists():
            for path in base.rglob('*'):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError('备份目录包含外部链接')
                if path.is_file() and path.suffix not in {'.tmp', '.pyc'}:
                    sources.append(path)
    destination.mkdir(parents=True)
    records = []
    for source in sources:
        relative = source.relative_to(root).as_posix()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = digest(source)
        shutil.copy2(source, target)
        if digest(target) != before or digest(source) != before:
            raise RuntimeError('备份过程中资料发生变化，请重新备份')
        records.append({'path': relative, 'sha256': before, 'bytes': target.stat().st_size})
    source_db = root / 'database/hydrology.sqlite3'
    if source_db.exists():
        target = destination / 'database/hydrology.sqlite3'
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(target) as backup:
            source.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('备份资料库检查未通过')
        records.append({'path': 'database/hydrology.sqlite3', 'sha256': digest(target), 'bytes': target.stat().st_size})
    manifest = {'schemaVersion': 1, 'workspaceId': identity['id'], 'appVersion': APP_VERSION,
                'createdAt': now(), 'files': records, 'bytes': sum(r['bytes'] for r in records)}
    write_json(destination / 'backup-manifest.json', manifest)
    return {'name': destination.name, 'path': str(destination), 'files': len(records), 'bytes': manifest['bytes'], 'createdAt': manifest['createdAt']}


def verify_backup(folder):
    folder = Path(folder).resolve()
    manifest = read_json(folder / 'backup-manifest.json')
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('files'), list) or not manifest['files']:
        raise ValueError('备份清单格式无效')
    seen = set()
    for row in manifest['files']:
        path = safe_reference(folder, row['path'])
        key = row['path'].casefold()
        if key in seen or path.is_symlink() or not path.is_file() or digest(path) != row['sha256'] or path.stat().st_size != row['bytes']:
            raise ValueError('备份文件检查未通过：' + row['path'])
        if row['path'] != 'workspace.json' and row['path'] != 'database/hydrology.sqlite3' and Path(row['path']).parts[0] not in PARTS:
            raise ValueError('备份包含未登记文件')
        seen.add(key)
    if 'workspace.json' not in seen:
        raise ValueError('备份缺少工作区标识')
    if workspace_identity(folder)['id'] != manifest.get('workspaceId'):
        raise ValueError('备份工作区标识不一致')
    if sum(row['bytes'] for row in manifest['files']) != manifest.get('bytes'):
        raise ValueError('备份文件总大小不一致')
    if 'database/hydrology.sqlite3' in seen:
        with sqlite3.connect((folder / 'database/hydrology.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('备份资料库检查未通过')
    return manifest


def restore_backup(folder, destination):
    folder, destination = Path(folder).resolve(), Path(destination).resolve()
    manifest = verify_backup(folder)
    if destination.exists() or destination.is_relative_to(folder) or folder.is_relative_to(destination):
        raise ValueError('恢复目标必须为新的独立目录')
    destination.mkdir(parents=True)
    # Write identity last: an interrupted restore is not a complete workspace.
    for row in sorted(manifest['files'], key=lambda r: r['path'] == 'workspace.json'):
        source = safe_reference(folder, row['path'])
        target = safe_reference(destination, row['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != row['sha256']:
            raise RuntimeError('恢复文件检查未通过')
    return {'path': str(destination), 'workspaceId': workspace_identity(destination)['id'], 'files': len(manifest['files'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'verify', 'restore'])
    parser.add_argument('--workspace', default=str(RUNTIME))
    parser.add_argument('--source')
    parser.add_argument('--destination')
    args = parser.parse_args()
    if args.operation == 'backup':
        with WorkspaceLock(args.workspace):
            result = create_backup(args.workspace, args.destination)
    elif args.operation == 'verify':
        if not args.source: parser.error('--source is required')
        result = verify_backup(args.source)
    else:
        if not args.source or not args.destination: parser.error('--source and --destination are required')
        result = restore_backup(args.source, args.destination)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
