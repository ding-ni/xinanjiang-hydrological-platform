"""User annotations are separate from immutable model calculations."""
from common import now


class ResultRepository:
    def __init__(self, manager):
        self.manager = manager

    def metadata(self):
        with self.manager.database.connect() as db:
            return {r['task_id']: {k: (bool(r[k]) if k == 'favorite' else r[k])
                    for k in ('name', 'collection', 'notes', 'favorite', 'revision', 'updated_at')}
                    for r in db.execute('SELECT * FROM result_metadata')}

    def update(self, task_id, body):
        self.manager.result(task_id)
        if not isinstance(body, dict) or set(body) != {'name', 'collection', 'notes', 'favorite', 'revision'}:
            raise ValueError('请提供成果名称、分组、备注、收藏与修订号')
        for key, limit in [('name', 80), ('collection', 40), ('notes', 2000)]:
            if not isinstance(body[key], str) or len(body[key].strip()) > limit:
                raise ValueError(f'{key} 最多 {limit} 个字符')
        if type(body['favorite']) is not bool or type(body['revision']) is not int or body['revision'] < 0:
            raise ValueError('收藏或修订号格式无效')
        with self.manager.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision FROM result_metadata WHERE task_id=?', (task_id,)).fetchone()
            if body['revision'] != (old[0] if old else 0):
                raise RuntimeError('成果信息已更新，请刷新后保存')
            row = {**body, **{k: body[k].strip() for k in ('name', 'collection', 'notes')},
                   'revision': body['revision'] + 1, 'updated_at': now()}
            db.execute('INSERT INTO result_metadata VALUES (?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET name=excluded.name, collection=excluded.collection, notes=excluded.notes, favorite=excluded.favorite, revision=excluded.revision, updated_at=excluded.updated_at',
                       (task_id, row['name'], row['collection'], row['notes'], int(row['favorite']), row['revision'], row['updated_at']))
        return row
