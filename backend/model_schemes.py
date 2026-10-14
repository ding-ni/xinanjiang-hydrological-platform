"""Named hydrological schemes; immutable input revisions and many calculations."""
from __future__ import annotations

import copy
import json
import re
import uuid
from common import now
from input_products import manifest_digest


def input_definition(request, rainfall_method='reuse'):
    period = copy.deepcopy(request['period'])
    if period.get('mode') == 'event':
        period = {'mode': 'event', 'warmupDays': period.get('warmupDays'),
                  'events': sorted([{key: row[key] for key in ('id', 'start', 'end', 'purpose')}
                                    for row in period.get('events', []) if row.get('purpose') != '未使用' and row.get('status') != 'inactive'], key=lambda row: row['id'])}
    else:
        for key in ('windows', 'events', 'eventSet', 'eventAssignment'):
            period.pop(key, None)
    calibration = request.get('calibration') or {}
    return {'context': request['context'], 'inputs': {'bundleVersionId': request['inputs'].get('sourceBundleVersionId') or request['inputs']['bundleVersionId'], 'rainfallMethod': rainfall_method,
            **({'stationSourceId': request['inputs']['stationSourceId']} if rainfall_method == 'stationIdw' and request['inputs'].get('stationSourceId') else {}),
            **({'stationNetwork': request['inputs']['stationNetwork']} if rainfall_method == 'stationIdw' and request['inputs'].get('stationNetwork') else {}),
            'idwPower': request['inputs'].get('idwPower', 2), 'minimumStations': request['inputs'].get('minimumStations', 1)},
            'period': period, 'statePolicy': calibration.get('statePolicy') or ('continuous-origin' if period.get('continuousOrigin') else 'independent-warmup'),
            'continuousOrigin': calibration.get('continuousOrigin') or period.get('continuousOrigin') or None}


class ModelSchemes:
    def __init__(self, database):
        self.database = database
        with database.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS model_schemes (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_scheme_revisions (
                    scheme_id TEXT NOT NULL REFERENCES model_schemes(id), revision INTEGER NOT NULL,
                    definition TEXT NOT NULL, definition_sha256 TEXT NOT NULL,
                    calculation_draft TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(scheme_id,revision));
                CREATE TABLE IF NOT EXISTS preparation_defaults (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            ''')

    def defaults(self, body=None):
        if body is not None:
            if not isinstance(body, dict) or set(body) != {'idwPower', 'minimumStations'} or isinstance(body['idwPower'], bool) or not isinstance(body['idwPower'], (int, float)) or not 1 <= body['idwPower'] <= 4 or type(body['minimumStations']) is not int or not 1 <= body['minimumStations'] <= 32:
                raise ValueError('插值幂次须为1至4，最少有效站须为1至32')
            with self.database.connect() as db:
                db.execute('INSERT INTO preparation_defaults VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(body),))
        with self.database.connect() as db:
            row = db.execute('SELECT payload FROM preparation_defaults WHERE id=1').fetchone()
            return json.loads(row['payload']) if row else {'idwPower': 2, 'minimumStations': 1}

    def list(self):
        with self.database.connect() as db:
            return {'schemes': [dict(row) for row in db.execute('SELECT * FROM model_schemes ORDER BY updated_at DESC')]}

    def get(self, scheme_id, revision=None):
        if not re.fullmatch(r'SCHEME-[a-f0-9]{32}', str(scheme_id)):
            raise ValueError('方案编号无效')
        with self.database.connect() as db:
            scheme = db.execute('SELECT * FROM model_schemes WHERE id=?', (scheme_id,)).fetchone()
            if not scheme:
                raise KeyError('方案不存在')
            row = db.execute('SELECT * FROM model_scheme_revisions WHERE scheme_id=? AND revision=?',
                             (scheme_id, revision or scheme['revision'])).fetchone()
            if not row:
                raise KeyError('方案修订不存在')
            revisions = [dict(r) for r in db.execute('SELECT revision,created_at FROM model_scheme_revisions WHERE scheme_id=? ORDER BY revision DESC', (scheme_id,))]
            return {**dict(scheme), 'latestRevision': scheme['revision'], 'revision': row['revision'], 'definition': json.loads(row['definition']),
                    'calculationDraft': json.loads(row['calculation_draft']), 'definitionSHA256': row['definition_sha256'],
                    'revisions': revisions}

    def save(self, body):
        if not isinstance(body, dict) or set(body) - {'id', 'revision', 'name', 'definition', 'calculationDraft'}:
            raise ValueError('方案保存字段无效')
        name = body.get('name', '').strip()
        if not name or len(name) > 80:
            raise ValueError('请为方案命名，最多80个字符')
        definition, draft = body.get('definition'), body.get('calculationDraft', {})
        if not isinstance(definition, dict) or not isinstance(draft, dict):
            raise ValueError('方案输入与计算草稿须为对象')
        if not isinstance(definition.get('context'), dict) or not isinstance(definition.get('period'), dict):
            raise ValueError('方案须包含流域及场次或连续时段')
        try:
            canonical = input_definition(draft, definition.get('inputs', {}).get('rainfallMethod', 'reuse'))
        except (KeyError, TypeError, AttributeError):
            raise ValueError('方案计算草稿须包含完整的输入与时段')
        if canonical != definition:
            raise ValueError('方案输入与计算草稿不一致，请重新保存当前条件')
        payload = json.dumps(definition, ensure_ascii=False, allow_nan=False)
        draft_payload = json.dumps(draft, ensure_ascii=False, allow_nan=False)
        if len((payload + draft_payload).encode('utf-8')) > 1800000:
            raise ValueError('方案超过1.8MB，请减少场次草稿')
        digest, instant = manifest_digest(definition), now()
        scheme_id = body.get('id') or 'SCHEME-' + uuid.uuid4().hex
        if not re.fullmatch(r'SCHEME-[a-f0-9]{32}', str(scheme_id)):
            raise ValueError('方案编号无效')
        expected = body.get('revision', 0)
        if type(expected) is not int or expected < 0:
            raise ValueError('方案修订号无效')
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision FROM model_schemes WHERE id=?', (scheme_id,)).fetchone()
            if expected != (old['revision'] if old else 0):
                raise RuntimeError('方案已在另一页面修改，请重新读取后保存')
            previous = db.execute('SELECT definition_sha256 FROM model_scheme_revisions WHERE scheme_id=? AND revision=?', (scheme_id, expected)).fetchone()
            revision = expected if previous and previous['definition_sha256'] == digest else expected + 1
            db.execute('INSERT INTO model_schemes VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,revision=excluded.revision,updated_at=excluded.updated_at', (scheme_id, name, revision, instant))
            if revision != expected:
                db.execute('INSERT INTO model_scheme_revisions VALUES (?,?,?,?,?,?)', (scheme_id, revision, payload, digest, draft_payload, instant))
            else:
                db.execute('UPDATE model_scheme_revisions SET calculation_draft=? WHERE scheme_id=? AND revision=?', (draft_payload, scheme_id, revision))
        return self.get(scheme_id)

    def freeze(self, request):
        request = copy.deepcopy(request)
        selection = request.get('scheme')
        if selection:
            if not isinstance(selection, dict) or set(selection) != {'id', 'revision'} or type(selection['revision']) is not int:
                raise ValueError('请明确方案和修订号')
            record = self.get(selection['id'], selection['revision'])
            if record['definition']['context'].get('contextKey') != request.get('context', {}).get('contextKey'):
                raise ValueError('方案与计算流域或尺度不一致')
            if input_definition(request, request.get('inputs', {}).get('rainfallMethod', record['definition']['inputs'].get('rainfallMethod', 'reuse'))) != record['definition']:
                raise ValueError('提交输入与方案修订不一致，请保存当前方案后重新计算')
            request['lineage'] = {**request.get('lineage', {}), 'schemeId': record['id'],
                                  'schemeRevision': record['revision'], 'schemeName': record['name'],
                                  'schemeDefinitionSHA256': record['definitionSHA256']}
        return request
