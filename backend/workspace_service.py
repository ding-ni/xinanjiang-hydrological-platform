"""Workspace identity, process ownership and portable business drafts."""
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

from common import now, read_json, write_json
from platform_config import SETTINGS, APP_VERSION


class WorkspaceLock:
    def __init__(self, root):
        self.root, self.stream = Path(root).resolve(), None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.stream = (self.root / '.workspace.lock').open('a+b')
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError('此工作区已打开，请使用原服务或选择其他工作区') from exc
        return self

    def __exit__(self, *_):
        if self.stream:
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


def workspace_identity(root):
    path = Path(root) / 'workspace.json'
    if not path.exists():
        write_json(path, {'schemaVersion': 1, 'id': 'WS-' + uuid.uuid4().hex,
                          'name': '水文计算工作区', 'createdAt': now()})
    value = read_json(path)
    if value.get('schemaVersion') != 1 or not re.fullmatch(r'WS-[a-f0-9]{32}', str(value.get('id'))):
        raise ValueError('工作区标识文件无效')
    return value


def workspace_info(root):
    root = Path(root).resolve()
    directories = [('workspace', '工作区', root), ('database', '水文资料库', root / 'database'),
                   ('inputs', '整编计算资料', root / 'inputs'), ('tasks', '模型计算成果', root / 'tasks'),
                   ('states', '起算状态', root / 'states'), ('forecast-rain', '预报降雨情景', root / 'forecast-rain'),
                   ('science-versions', '冻结科学条件', root / 'science-versions'),
                   ('backups', '工作区备份', root / 'backups'),
                   ('exports', '导出文件', root / 'exports'), ('logs', '运行日志', root / 'logs'),
                   ('model', '模型内核与基础资料', SETTINGS['model']),
                   ('projects', '流域资料目录', SETTINGS['projects']),
                   ('frontend', '网页程序', SETTINGS['frontend'])]
    usage = shutil.disk_usage(root)
    return {'workspace': workspace_identity(root), 'version': APP_VERSION,
            'pythonVersion': sys.version.split()[0], 'schemaVersion': 2,
            'configFile': str(SETTINGS['configFile']), 'freeBytes': usage.free,
            'totalBytes': usage.total,
            'directories': [{'id': key, 'label': label, 'path': str(path), 'exists': path.exists()}
                            for key, label, path in directories]}


def valid_draft_key(key):
    return key == 'hydro_calibration_workspaces_v1' or bool(re.fullmatch(r'hydro_flood_event_overrides_v1:[a-z0-9-]{1,80}', key or ''))


class DraftRepository:
    def __init__(self, database):
        self.database = database

    def list(self):
        with self.database.connect() as db:
            return {row['key']: {'value': json.loads(row['payload']), 'revision': row['revision'],
                                  'updatedAt': row['updated_at']} for row in db.execute('SELECT * FROM workspace_drafts')}

    def save(self, body):
        if not isinstance(body, dict) or set(body) != {'key', 'value', 'revision'}:
            raise ValueError('请提供草稿名称、内容和修订号')
        key, value, revision = body['key'], body['value'], body['revision']
        if not isinstance(key, str) or not valid_draft_key(key) or not isinstance(value, dict):
            raise ValueError('草稿内容格式无效')
        if type(revision) is not int or revision < 0:
            raise ValueError('草稿修订号无效')
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > 1800000:
            raise ValueError('草稿超过 1.8 MB，请导出并分开保存')
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision, payload, updated_at FROM workspace_drafts WHERE key=?', (key,)).fetchone()
            # A refresh can discard the HTTP acknowledgement after SQLite commits.
            # Repeating that exact content acknowledges the existing revision only.
            canonical = lambda obj: json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if old and revision <= old['revision'] and canonical(json.loads(old['payload'])) == canonical(value):
                return {'value': value, 'revision': old['revision'], 'updatedAt': old['updated_at']}
            if revision != (old['revision'] if old else 0):
                raise RuntimeError('草稿已在另一页面更新，请导出本地草稿后刷新')
            row = {'value': value, 'revision': revision + 1, 'updatedAt': now()}
            db.execute('INSERT INTO workspace_drafts VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, revision=excluded.revision, updated_at=excluded.updated_at',
                       (key, payload, row['revision'], row['updatedAt']))
        return row
