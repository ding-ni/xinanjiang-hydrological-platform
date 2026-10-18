"""Recoverable user retention, separate from immutable numerical artifacts."""
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
                   'running': [row['id'] for row in rows if row['status'] in {'queued', 'running', 'cancelling'}],
                   'protectedResults': [row['name'] for row in rows if row['retention'] == 'saved'],
                   'protectedParameters': [row['name'] for row in parameters]}
        payload['previewSHA256'] = manifest_digest(payload)
        return payload

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
