"""Recoverable user retention, separate from immutable numerical artifacts."""
import json
import ctypes
import os
from pathlib import Path
from common import now, read_json
from input_products import manifest_digest


class ResultLifecycle:
    def __init__(self, repository):
        self.repository = repository
        self.manager = repository.manager
        with self.manager.database.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS result_retention (
                    id TEXT PRIMARY KEY, retention TEXT NOT NULL, previous TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scheme_retention (
                    id TEXT PRIMARY KEY, trashed INTEGER NOT NULL, revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS result_retention_settings (id INTEGER PRIMARY KEY, enabled_at TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO result_retention_settings VALUES(1,?)', (now(),))
            self.enabled_at = db.execute('SELECT enabled_at FROM result_retention_settings WHERE id=1').fetchone()[0]

    def records(self):
        with self.manager.database.connect() as db:
            return {row['id']: dict(row) for row in db.execute('SELECT * FROM result_retention')}

    def assert_scheme_active(self, request):
        model = request.get('request') or request
        scheme = model.get('scheme') or (request.get('trialContext') or {}).get('scheme') or {}
        if self.scheme_records().get(scheme.get('id'), {}).get('trashed'):
            raise ValueError('该方案已在回收站，请恢复方案或另建方案后再计算')

    def value(self, status, annotation, records=None):
        legacy = str(status.get('createdAt') or '') < self.enabled_at
        record = (records if records is not None else self.records()).get(status['taskId'])
        if record:
            return {**record, 'legacy': legacy}
        state = 'saved' if annotation.get('favorite') else 'temporary'
        if legacy:
            # Freeze the upgrade decision once. Later changes to the star must
            # never save or unsave a historical computation implicitly.
            with self.manager.database.connect() as db:
                db.execute('INSERT OR IGNORE INTO result_retention VALUES(?,?,?,?,?)',
                           (status['taskId'], state, state, 0, now()))
                record = db.execute('SELECT * FROM result_retention WHERE id=?', (status['taskId'],)).fetchone()
                return {**dict(record), 'legacy': True}
        return {'id': status['taskId'], 'retention': state, 'revision': 0,
                'previous': state, 'legacy': False}

    def change(self, identifier, action, body):
        if action not in {'save', 'discard', 'restore'} or not isinstance(body, dict) or set(body) != {'revision'} or type(body['revision']) is not int:
            raise ValueError('请提供保留状态修订号')
        status = self.repository.status(identifier)
        if status.get('status') in {'queued', 'running', 'cancelling'}:
            raise RuntimeError('请先完成或停止计算，再处理结果')
        if action == 'save':
            self.repository.result(identifier)
        annotation = self.repository.annotation(identifier)
        self.value(status, annotation)
        with self.manager.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            records = {row['id']: dict(row) for row in db.execute('SELECT * FROM result_retention WHERE id=?', (identifier,))}
            old = self.value(status, annotation, records)
            if body['revision'] != old['revision']:
                raise RuntimeError('结果保留状态已更新，请刷新后重试')
            if action == 'save' and old['retention'] == 'trashed':
                raise ValueError('请先从回收站恢复计算')
            state = {'save': 'saved', 'discard': 'trashed', 'restore': old['previous']}[action]
            if action == 'restore' and old['retention'] != 'trashed':
                raise ValueError('该计算不在回收站')
            previous = old['retention'] if action == 'discard' and old['retention'] != 'trashed' else old['previous']
            record = {'id': identifier, 'retention': state, 'previous': previous,
                      'revision': old['revision'] + 1, 'updated_at': now()}
            db.execute('INSERT OR REPLACE INTO result_retention VALUES(?,?,?,?,?)', tuple(record.values()))
        return record

    def scheme_records(self):
        with self.manager.database.connect() as db:
            return {row['id']: dict(row) for row in db.execute('SELECT * FROM scheme_retention')}

    def scheme_preview(self, identifier):
        scheme = self.manager.schemes.get(identifier)
        rows = [row for row in self.repository.catalog(include_trashed=True)['results'] if row.get('schemeId') == identifier]
        parameters = [row for row in self.manager.parameter_schemes.list()['schemes']
                      if (row.get('sourceScheme') or {}).get('id') == identifier]
        state = self.scheme_records().get(identifier, {'revision': 0, 'trashed': 0})
        payload = {'id': identifier, 'name': scheme['name'], 'schemeRevision': scheme['latestRevision'],
                   'revision': state['revision'], 'trashed': bool(state['trashed']),
                   'temporary': [{'id': row['id'], 'name': row['name'], 'revision': row['retentionRevision']}
                                 for row in rows if row['retention'] == 'temporary' and row['status'] not in {'queued', 'running', 'cancelling'}],
                   'trashedTemporary': [{'id': row['id'], 'name': row['name'], 'revision': row['retentionRevision']}
                                        for row in rows if row['retention'] == 'trashed'],
                   'running': [row['id'] for row in rows if row['status'] in {'queued', 'running', 'cancelling'}],
                   'protectedResults': [row['name'] for row in rows if row['retention'] == 'saved'],
                   'protectedParameters': [row['name'] for row in parameters]}
        payload['previewSHA256'] = manifest_digest(payload)
        return payload

    @staticmethod
    def _strings(value):
        if isinstance(value, dict):
            return {item for child in value.values() for item in ResultLifecycle._strings(child)}
        if isinstance(value, list):
            return {item for child in value for item in ResultLifecycle._strings(child)}
        return {value} if isinstance(value, str) else set()

    @staticmethod
    def _move_to_recycle_bin(path):
        """Move one runtime artifact to the Windows Recycle Bin.

        The application database can forget a trashed record, but its numeric
        files remain recoverable through the operating system's recycle bin.
        """
        path = str(path)
        if os.name != 'nt':
            raise RuntimeError('永久删除只支持 Windows 回收站')
        if not os.path.exists(path):
            return
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ('hwnd', ctypes.c_void_p),
                ('wFunc', ctypes.c_uint),
                ('pFrom', ctypes.c_wchar_p),
                ('pTo', ctypes.c_wchar_p),
                ('fFlags', ctypes.c_ushort),
                ('fAnyOperationsAborted', ctypes.c_int),
                ('hNameMappings', ctypes.c_void_p),
                ('lpszProgressTitle', ctypes.c_wchar_p),
            ]
        # FO_DELETE + FOF_ALLOWUNDO + silent/no-confirmation/no-error UI.
        operation = SHFILEOPSTRUCTW(None, 3, path + '\0\0', None,
                                    0x40 | 0x10 | 0x4 | 0x400,
                                    0, None, None)
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        if result != 0 or operation.fAnyOperationsAborted:
            raise OSError(result or '回收站操作被取消', path)

    def _purge_artifacts(self, identifiers):
        identifiers = set(identifiers)
        folders = {}
        for identifier in identifiers:
            prefix = 'OP-' if str(identifier).startswith('OP-') else 'JOB-'
            if not str(identifier).startswith(prefix) or Path(str(identifier)).name != str(identifier):
                raise ValueError('无效的计算记录编号')
            root = self.manager.runtime_root / ('operations' if str(identifier).startswith('OP-') else 'tasks')
            folder = root / str(identifier)
            if folder.is_dir():
                folders[identifier] = folder
        children = set()
        for folder in folders.values():
            path = folder / 'status.json'
            if path.is_file():
                try:
                    children.update(item for item in read_json(path).get('childTaskIds', [])
                                    if isinstance(item, str) and item.startswith('JOB-')
                                    and Path(item).name == item)
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
        # A parent operation may be moved to the OS recycle bin before its
        # children are purged. Recover ownership from each child status too.
        for task_path in (self.manager.runtime_root / 'tasks').glob('*/status.json'):
            try:
                task_status = read_json(task_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if task_status.get('operationId') in identifiers:
                children.add(task_status.get('taskId') or task_path.parent.name)
        references = set()
        candidate_folders = [self.manager.runtime_root / 'tasks' / item for item in children]
        for root_name in ('operations', 'tasks', 'states', 'parameters'):
            root = self.manager.runtime_root / root_name
            if not root.is_dir():
                continue
            for path in root.glob('**/*.json'):
                if any(path.is_relative_to(folder) for folder in [*folders.values(), *candidate_folders]):
                    continue
                try:
                    references.update(self._strings(read_json(path)))
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
        deletable_children = {item for item in children if item not in references}
        purged_children = set()
        for identifier in deletable_children:
            folder = self.manager.runtime_root / 'tasks' / identifier
            if folder.is_dir():
                self._move_to_recycle_bin(folder)
                purged_children.add(identifier)
        for folder in folders.values():
            if folder.is_dir():
                self._move_to_recycle_bin(folder)
        purged_ids = identifiers | purged_children
        with self.manager.database.connect() as db:
            for identifier in purged_ids:
                db.execute('DELETE FROM task_index WHERE id=?', (identifier,))
                db.execute('DELETE FROM result_metadata WHERE task_id=?', (identifier,))
                db.execute('DELETE FROM result_retention WHERE id=?', (identifier,))
        return sorted(purged_ids)

    def purge_result(self, identifier, body):
        if not isinstance(body, dict) or set(body) != {'revision'} or type(body['revision']) is not int:
            raise ValueError('请提供回收站结果修订号')
        status = self.repository.status(identifier)
        record = self.value(status, self.repository.annotation(identifier))
        if record['retention'] != 'trashed':
            raise ValueError('只有回收站中的结果可以永久删除')
        if body['revision'] != record['revision']:
            raise RuntimeError('回收站结果已更新，请刷新后重试')
        if status.get('status') in {'queued', 'running', 'cancelling'}:
            raise RuntimeError('计算仍在运行，不能永久删除')
        return {'id': identifier, 'purgedIds': self._purge_artifacts([identifier])}

    def purge_spatial_task(self, identifier, body):
        """Remove one obsolete GIS task while retaining its input version."""
        if not isinstance(body, dict) or set(body) != {'requestDigest'} or not isinstance(body['requestDigest'], str):
            raise ValueError('请提供图件任务的请求摘要')
        status = self.manager.get(identifier)
        if status.get('runType') != 'spatialExport':
            raise ValueError('只有空间图件任务可以从这里移除')
        if status.get('status') in {'queued', 'running', 'cancelling'}:
            raise RuntimeError('图件仍在制作，不能移除')
        if body['requestDigest'] != status.get('requestDigest'):
            raise RuntimeError('图件任务已更新，请刷新后重试')
        return {'id': identifier, 'purgedIds': self._purge_artifacts([identifier])}

    def purge_scheme(self, identifier, body):
        if not isinstance(body, dict) or set(body) != {'previewSHA256'}:
            raise ValueError('请先预览方案永久删除范围')
        preview = self.scheme_preview(identifier)
        if body['previewSHA256'] != preview['previewSHA256']:
            raise RuntimeError('方案或关联成果已更新，请重新预览')
        if not preview['trashed']:
            raise ValueError('只有回收站中的方案可以永久删除')
        if preview['running']:
            raise RuntimeError('方案仍有运行中的计算，请先完成或停止')
        if preview['protectedResults']:
            raise RuntimeError('方案仍有正式成果，请先处理正式成果后再删除方案')
        ids = [row['id'] for row in preview['trashedTemporary']]
        purged = self._purge_artifacts(ids) if ids else []
        with self.manager.database.connect() as db:
            for table in ('model_scheme_revisions', 'model_scheme_editors', 'model_scheme_snapshots',
                          'model_scheme_draft_versions', 'model_scheme_prepared_inputs', 'scheme_input_assets',
                          'model_scheme_history_notes', 'model_scheme_parameters', 'model_schemes', 'scheme_retention'):
                db.execute(f'DELETE FROM {table} WHERE scheme_id=?' if table != 'model_schemes' and table != 'scheme_retention' else f'DELETE FROM {table} WHERE id=?', (identifier,))
        return {'id': identifier, 'purgedIds': purged, 'schemeDeleted': True}

    def scheme_change(self, identifier, action, body):
        from contextlib import nullcontext
        # Queue submissions use these same locks. Check and mutate atomically
        # with respect to both numerical tasks and operation envelopes.
        with getattr(getattr(self.manager, 'operations', None), 'lock', nullcontext()), self.manager.lock:
            preview = self.scheme_preview(identifier)
            if action not in {'discard', 'restore'} or not isinstance(body, dict) or set(body) != {'previewSHA256', 'temporaryIds'}:
                raise ValueError('请先预览删除范围')
            if body['previewSHA256'] != preview['previewSHA256']:
                raise RuntimeError('方案或关联计算已更新，请重新预览')
            if preview['running']:
                raise RuntimeError('方案仍有运行中的计算，请先完成或停止')
            ids = body['temporaryIds']
            if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids) or len(ids) != len(set(ids)) or not set(ids).issubset({row['id'] for row in preview['temporary']}):
                raise ValueError('临时计算范围已改变')
            with self.manager.database.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT revision FROM scheme_retention WHERE id=?', (identifier,)).fetchone()
                if (current[0] if current else 0) != preview['revision']:
                    raise RuntimeError('方案保留状态已更新')
                if action == 'discard':
                    for row in preview['temporary']:
                        if row['id'] not in ids:
                            continue
                        old = db.execute('SELECT revision FROM result_retention WHERE id=?', (row['id'],)).fetchone()
                        if (old[0] if old else 0) != row['revision']:
                            raise RuntimeError('临时计算已更新，请重新预览')
                        db.execute('INSERT OR REPLACE INTO result_retention VALUES(?,?,?,?,?)',
                                   (row['id'], 'trashed', 'temporary', row['revision'] + 1, now()))
                db.execute('INSERT OR REPLACE INTO scheme_retention VALUES(?,?,?,?)',
                           (identifier, int(action == 'discard'), preview['revision'] + 1, now()))
            return {'id': identifier, 'trashed': action == 'discard', 'discardedIds': ids if action == 'discard' else []}
