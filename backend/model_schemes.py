"""Named hydrological schemes; immutable input revisions and many calculations."""
from __future__ import annotations

import copy
import json
import re
import uuid
from common import now, read_json
from input_products import manifest_digest


def calculation_identity(request):
    """Calculation settings, excluding request routing and display metadata."""
    result = copy.deepcopy(request)
    for key in ('requestId', 'createdAt', 'scheme', 'lineage', 'name'):
        result.pop(key, None)
    return result

def editor_identity(editor):
    return {key:value for key,value in editor.items() if key not in {'eventId','editorTrialMode','activeOperationId','activeOperationRequest'}}


def change_summary(before, after):
    if not before:
        return '首次保存'
    changes = []
    for key, label in (('period', '洪水与时段'), ('inputs', '输入与处理'), ('parameters', '初始参数'),
                       ('routing', '汇流参数'), ('science', '蒸散发与起算')):
        if before.get(key) != after.get(key):
            changes.append(label)
    old, new = before.get('calibration') or {}, after.get('calibration') or {}
    if old.get('initialParameters') != new.get('initialParameters'):
        changes.append('初始参数')
    if {k:v for k,v in old.items() if k != 'initialParameters'} != {k:v for k,v in new.items() if k != 'initialParameters'}:
        changes.append('率定设置')
    return '、'.join(dict.fromkeys(changes)) or '方案设置'


def history_name(before, after):
    if not before:
        return '首次保存'
    if before.get('period') != after.get('period'):
        return '洪水与用途调整'
    return '率定设置调整'


def preparation_binding_key(record, request):
    # Parameter-only versions share inputs; PET/science changes require their
    # own coverage check even when the selected floods have not changed.
    definition = {'definition': record['definitionSHA256'],
                  'science': request.get('science', {}),
                  'evaporation': request.get('inputs', {}).get('evaporation', {})}
    if request.get('period', {}).get('mode') == 'event' and request.get('runType') == 'calibration' and not request.get('sourcePlan'):
        from strict_forcing import EVENT_QUALITY_POLICY
        definition['eventQualityPolicy'] = EVENT_QUALITY_POLICY
    return manifest_digest(definition)


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
            **({'dataMode': request['inputs']['dataMode']} if request['inputs'].get('dataMode') else {}),
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
                CREATE TABLE IF NOT EXISTS model_scheme_editors (
                    scheme_id TEXT PRIMARY KEY REFERENCES model_schemes(id),
                    payload TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_scheme_snapshots (
                    scheme_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    editor TEXT NOT NULL, calculation_sha256 TEXT NOT NULL,
                    PRIMARY KEY(scheme_id,revision));
                CREATE TABLE IF NOT EXISTS model_scheme_draft_versions (
                    scheme_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS model_scheme_prepared_inputs (
                    scheme_id TEXT NOT NULL, definition_sha256 TEXT NOT NULL,
                    preparation_task_id TEXT NOT NULL, bundle_version_id TEXT NOT NULL,
                    PRIMARY KEY(scheme_id,definition_sha256));
                CREATE TABLE IF NOT EXISTS scheme_input_assets (
                    asset_id TEXT PRIMARY KEY, scheme_id TEXT, scheme_revision INTEGER,
                    role TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_scheme_history_notes (
                    scheme_id TEXT NOT NULL, revision INTEGER NOT NULL, note TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(scheme_id,revision));
                CREATE TABLE IF NOT EXISTS model_scheme_parameters (
                    scheme_id TEXT PRIMARY KEY REFERENCES model_schemes(id),
                    parameter_id TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
            ''')
            # Older workspaces predate named history entries.  Keep their
            # notes and add the presentation-only name in place.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(model_scheme_history_notes)')}
            if 'display_name' not in columns:
                db.execute("ALTER TABLE model_scheme_history_notes ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")

    def defaults(self, body=None):
        standard = {'idwPower': 2, 'minimumStations': 1, 'rainfallMethod': 'stationIdw',
                    'stationSourceId': 'LOCAL_OBSERVATIONS', 'sourceBundleVersionId': 'NANANXI_QC_20260819_CONTROL_V2'}
        if body is not None:
            if not isinstance(body, dict) or set(body) - set(standard):
                raise ValueError('默认资料设置无效')
            body = {**self.defaults(), **body}
            if isinstance(body['idwPower'], bool) or not isinstance(body['idwPower'], (int, float)) or not 1 <= body['idwPower'] <= 4 or type(body['minimumStations']) is not int or not 1 <= body['minimumStations'] <= 32:
                raise ValueError('插值幂次须为1至4，最少有效站须为1至32')
            if body['rainfallMethod'] not in {'stationIdw', 'reuse'} or not isinstance(body['sourceBundleVersionId'], str):
                raise ValueError('默认降雨资料无效')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,100}', body['sourceBundleVersionId']):
                raise ValueError('默认计算资料无效')
            source = body.get('stationSourceId')
            if source is not None and source != 'LOCAL_OBSERVATIONS' and not re.fullmatch(r'SRC-[A-Za-z0-9_-]{1,96}', str(source)):
                raise ValueError('默认站点资料无效')
            with self.database.connect() as db:
                db.execute('INSERT INTO preparation_defaults VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(body),))
        with self.database.connect() as db:
            row = db.execute('SELECT payload FROM preparation_defaults WHERE id=1').fetchone()
            return {**standard, **json.loads(row['payload'])} if row else standard

    def list(self):
        with self.database.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT s.*, e.payload AS editor_payload, p.editor AS saved_editor, r.definition, r.calculation_draft FROM model_schemes s LEFT JOIN model_scheme_editors e ON e.scheme_id=s.id LEFT JOIN model_scheme_revisions r ON r.scheme_id=s.id AND r.revision=s.revision LEFT JOIN model_scheme_snapshots p ON p.scheme_id=s.id AND p.revision=s.revision ORDER BY s.updated_at DESC')]
        latest = {}
        for path in (self.database.runtime / 'operations').glob('*/status.json'):
            operation = read_json(path)
            request = (operation.get('runConfig') or {}).get('request') or {}
            scheme_id = request.get('scheme', {}).get('id') or operation.get('lineage', {}).get('schemeId')
            if scheme_id and operation.get('createdAt', '') > latest.get(scheme_id, {}).get('createdAt', ''):
                latest[scheme_id] = {key: operation.get(key) for key in ('taskId', 'status', 'createdAt', 'completedAt')}
        for row in rows:
            editor = json.loads(row.pop('editor_payload') or '{}')
            saved_editor = json.loads(row.pop('saved_editor') or '{}')
            definition = json.loads(row.pop('definition') or '{}')
            draft = json.loads(row.pop('calculation_draft') or '{}')
            events = definition.get('period', {}).get('events', []) if row['revision'] else editor.get('events', []) + editor.get('schemeWindows', [])
            selected = {event['id']: event for event in events if event.get('purpose') in {'率定', '验证', '独立检验'} and event.get('status') != 'inactive'}
            row['summary'] = {'mode': definition.get('period', {}).get('mode', editor.get('calibrationMode', 'event')),
                'selectedCount': len(selected), **{key: sum(event['purpose'] == purpose for event in selected.values()) for key, purpose in [('calibration', '率定'), ('validation', '验证'), ('independent', '独立检验')]}}
            source = saved_editor.get('originalCalibrationPlan') or editor.get('originalCalibrationPlan') or draft.get('sourcePlan') or {}
            row['originalPlanSHA256'] = source.get('planSHA256')
            row['context'] = editor.get('context') or definition.get('context', {})
            row['latestCalculation'] = latest.get(row['id'])
            with self.database.connect() as db:
                assets = [dict(asset) for asset in db.execute('SELECT role,payload FROM scheme_input_assets WHERE scheme_id=?', (row['id'],))]
            row['origin'] = '同事交付' if any(asset['role'] == 'delivered-plan' for asset in assets) else '历史选取' if assets else '自建方案'
            row['configurationPending'] = bool(editor.get('configurationPending')) and row['revision'] == 0
            row['deliverySelectionCount'] = len(editor.get('deliverySelection', []))
        return {'schemes': rows}

    def get(self, scheme_id, revision=None):
        if not re.fullmatch(r'SCHEME-[a-f0-9]{32}', str(scheme_id)):
            raise ValueError('方案编号无效')
        with self.database.connect() as db:
            scheme = db.execute('SELECT * FROM model_schemes WHERE id=?', (scheme_id,)).fetchone()
            if not scheme:
                raise KeyError('方案不存在')
            selected = scheme['revision'] if revision is None else revision
            row = db.execute('SELECT * FROM model_scheme_revisions WHERE scheme_id=? AND revision=?',
                             (scheme_id, selected)).fetchone()
            if not row and selected != 0:
                raise KeyError('方案修订不存在')
            editor = db.execute('SELECT payload FROM model_scheme_editors WHERE scheme_id=?', (scheme_id,)).fetchone()
            editing = json.loads(editor['payload']) if editor else {}
            snapshot = db.execute('SELECT * FROM model_scheme_snapshots WHERE scheme_id=? AND revision=?', (scheme_id, selected)).fetchone()
            draft_version = db.execute('SELECT revision FROM model_scheme_draft_versions WHERE scheme_id=?', (scheme_id,)).fetchone()
            history = list(db.execute('SELECT r.revision,r.created_at,r.calculation_draft,n.note,n.display_name FROM model_scheme_revisions r LEFT JOIN model_scheme_history_notes n ON n.scheme_id=r.scheme_id AND n.revision=r.revision WHERE r.scheme_id=? ORDER BY r.revision', (scheme_id,)))
            revisions, previous = [], {}
            for item in history:
                request = json.loads(item['calculation_draft'])
                summary = change_summary(previous, request)
                revisions.append({'revision': item['revision'], 'created_at': item['created_at'],
                                  'name': item['display_name'] or history_name(previous, request),
                                  'note': item['note'] or '', 'summary': summary})
                previous = request
            revisions.reverse()
            adopted = db.execute('SELECT * FROM model_scheme_parameters WHERE scheme_id=?', (scheme_id,)).fetchone()
            return {**dict(scheme), 'latestRevision': scheme['revision'], 'revision': selected,
                    'adoptedParameter': dict(adopted) if adopted else None,
                    'definition': json.loads(row['definition']) if row else {'context': editing.get('context', {}), 'period': {}},
                    'calculationDraft': json.loads(row['calculation_draft']) if row else {},
                    'definitionSHA256': row['definition_sha256'] if row else None,
                    'editor': editing if selected == scheme['revision'] else {},
                    'savedEditor': json.loads(snapshot['editor']) if snapshot else {},
                    'calculationSHA256': snapshot['calculation_sha256'] if snapshot else None,
                    'draftRevision': draft_version[0] if draft_version else 0,
                    'hasDraft': bool(editing), 'legacyDraft': bool(editing) and not snapshot,
                    'revisions': revisions}

    def adopt_parameter(self, scheme_id, parameter_id, revision):
        self.get(scheme_id)
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM model_scheme_parameters WHERE scheme_id=?', (scheme_id,)).fetchone()
            if old and old['parameter_id'] == parameter_id:
                return dict(old)
            if type(revision) is not int or revision != (old['revision'] if old else 0):
                raise RuntimeError('方案采用参数已被另一页面修改；参数已入库，请刷新方案后重新采用')
            db.execute('INSERT INTO model_scheme_parameters VALUES(?,?,?,?) ON CONFLICT(scheme_id) DO UPDATE SET parameter_id=excluded.parameter_id,revision=excluded.revision,updated_at=excluded.updated_at',
                       (scheme_id, parameter_id, revision + 1, now()))
        return self.get(scheme_id)['adoptedParameter']

    def freeze_trial(self, request, context):
        """A trial freezes its own conditions, without saving a scheme revision."""
        request = copy.deepcopy(request)
        context = copy.deepcopy(context or {})
        selection = context.get('scheme') or request.pop('scheme', None)
        request.pop('scheme', None)
        if selection:
            record = self.get(selection['id'], selection.get('revision'))
            if record['definition'].get('context', {}).get('contextKey') != request.get('context', {}).get('contextKey'):
                raise ValueError('试调与方案所属流域不一致')
            context['scheme'] = {'id': record['id'], 'revision': record['revision'], 'name': record['name']}
            request.setdefault('lineage', {}).update(schemeId=record['id'], schemeRevision=record['revision'], schemeName=record['name'])
        request.setdefault('lineage', {})['trialContext'] = context
        return request

    def rename(self, scheme_id, body):
        self.get(scheme_id)
        name = str(body.get('name') or '').strip()
        if set(body) != {'name'} or not name or len(name) > 80:
            raise ValueError('方案名称须为1至80个字符')
        with self.database.connect() as db:
            db.execute('UPDATE model_schemes SET name=?,updated_at=? WHERE id=?', (name, now(), scheme_id))
        return self.get(scheme_id)

    def history_note(self, scheme_id, body):
        if not isinstance(body, dict) or set(body) - {'revision', 'note', 'name'} or 'revision' not in body or type(body['revision']) is not int:
            raise ValueError('设置历史内容无效')
        note = body.get('note', '')
        name = body.get('name', '')
        if not isinstance(note, str) or len(note.strip()) > 160 or not isinstance(name, str) or len(name.strip()) > 80:
            raise ValueError('设置历史名称最多80个字符，说明最多160个字符')
        self.get(scheme_id, body['revision'])
        with self.database.connect() as db:
            old=db.execute('SELECT note,display_name FROM model_scheme_history_notes WHERE scheme_id=? AND revision=?',(scheme_id,body['revision'])).fetchone()
            if old:
                note=body.get('note',old['note'])
                name=body.get('name',old['display_name'])
            db.execute('INSERT INTO model_scheme_history_notes(scheme_id,revision,note,display_name) VALUES (?,?,?,?) ON CONFLICT(scheme_id,revision) DO UPDATE SET note=excluded.note,display_name=excluded.display_name', (scheme_id, body['revision'], note.strip(), name.strip()))
        return self.get(scheme_id)

    def discard_editor(self, scheme_id, body):
        record = self.get(scheme_id)
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._check_draft(db, scheme_id, body)
            db.execute('UPDATE model_scheme_editors SET payload=?,updated_at=? WHERE scheme_id=?', ('{}', now(), scheme_id))
            self._bump_draft(db, scheme_id)
        return self.get(record['id'])

    @staticmethod
    def _check_draft(db, scheme_id, body):
        current = db.execute('SELECT revision FROM model_scheme_draft_versions WHERE scheme_id=?', (scheme_id,)).fetchone()
        if 'draftRevision' in body and body['draftRevision'] != (current[0] if current else 0):
            raise RuntimeError('草稿已在另一页面修改，请重新读取后保存')
        saved = db.execute('SELECT revision FROM model_schemes WHERE id=?', (scheme_id,)).fetchone()
        if 'revision' in body and body['revision'] != (saved[0] if saved else 0):
            raise RuntimeError('方案已在另一页面保存，请重新读取后编辑')

    @staticmethod
    def _bump_draft(db, scheme_id):
        db.execute('INSERT INTO model_scheme_draft_versions VALUES (?,1) ON CONFLICT(scheme_id) DO UPDATE SET revision=revision+1', (scheme_id,))

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {'name', 'context', 'editor'}:
            raise ValueError('请填写方案名称')
        name = str(body.get('name') or '').strip()
        if not name or len(name) > 80:
            raise ValueError('请为方案命名，最多80个字符')
        context = body.get('context', {})
        if not isinstance(context, dict):
            raise ValueError('方案流域无效')
        identifier, instant = 'SCHEME-' + uuid.uuid4().hex, now()
        editor = copy.deepcopy(body.get('editor') or {})
        if not isinstance(editor, dict):
            raise ValueError('方案内容无效')
        editor['context'] = context
        payload = self._editor_payload(editor)
        with self.database.connect() as db:
            db.execute('INSERT INTO model_schemes VALUES (?,?,?,?)', (identifier, name, 0, instant))
            db.execute('INSERT INTO model_scheme_editors VALUES (?,?,?)', (identifier, payload, instant))
        return self.get(identifier)

    @staticmethod
    def _editor_payload(editor):
        if not isinstance(editor, dict):
            raise ValueError('方案内容无效')
        payload = json.dumps(editor, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > 1800000:
            raise ValueError('方案内容过大')
        return payload

    def save_editor(self, scheme_id, body):
        self.get(scheme_id)
        if not isinstance(body, dict) or set(body) - {'editor', 'revision', 'draftRevision'} or 'editor' not in body:
            raise ValueError('方案内容无效')
        payload, instant = self._editor_payload(body['editor']), now()
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._check_draft(db, scheme_id, body)
            db.execute('INSERT INTO model_scheme_editors VALUES (?,?,?) ON CONFLICT(scheme_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at', (scheme_id, payload, instant))
            self._bump_draft(db, scheme_id)
        return self.get(scheme_id)

    def save(self, body):
        if isinstance(body, dict) and 'definition' not in body and not body.get('id'):
            return self.create(body)
        if not isinstance(body, dict) or set(body) - {'id', 'revision', 'name', 'definition', 'calculationDraft', 'editor', 'draftRevision'}:
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
            self._check_draft(db, scheme_id, body)
            previous = db.execute('SELECT definition_sha256,calculation_draft FROM model_scheme_revisions WHERE scheme_id=? AND revision=?', (scheme_id, expected)).fetchone()
            calculation_digest = manifest_digest(calculation_identity(draft))
            unchanged = previous and previous['definition_sha256'] == digest and manifest_digest(calculation_identity(json.loads(previous['calculation_draft']))) == calculation_digest
            prior_snapshot=db.execute('SELECT editor FROM model_scheme_snapshots WHERE scheme_id=? AND revision=?',(scheme_id,expected)).fetchone()
            prior_editor=json.loads(prior_snapshot['editor']) if prior_snapshot else {}
            editor=self._editor_payload(body.get('editor') if 'editor' in body else prior_editor)
            if prior_snapshot and manifest_digest(editor_identity(json.loads(editor)))!=manifest_digest(editor_identity(prior_editor)):
                unchanged=False
            revision = expected if unchanged else expected + 1
            db.execute('INSERT INTO model_schemes VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,revision=excluded.revision,updated_at=excluded.updated_at', (scheme_id, name, revision, instant))
            if revision != expected:
                db.execute('INSERT INTO model_scheme_revisions VALUES (?,?,?,?,?,?)', (scheme_id, revision, payload, digest, draft_payload, instant))
            if not unchanged or not db.execute('SELECT 1 FROM model_scheme_snapshots WHERE scheme_id=? AND revision=?', (scheme_id, revision)).fetchone():
                db.execute('INSERT OR IGNORE INTO model_scheme_snapshots VALUES (?,?,?,?)', (scheme_id, revision, editor, calculation_digest))
            db.execute('INSERT INTO model_scheme_editors VALUES (?,?,?) ON CONFLICT(scheme_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at', (scheme_id, '{}', instant))
            self._bump_draft(db, scheme_id)
        return self.get(scheme_id)

    def freeze(self, request, *, strict=False):
        request = copy.deepcopy(request)
        selection = request.get('scheme')
        if selection:
            if not isinstance(selection, dict) or set(selection) != {'id', 'revision'} or type(selection['revision']) is not int:
                raise ValueError('请明确方案和修订号')
            record = self.get(selection['id'], selection['revision'])
            if record['revision'] == 0:
                raise ValueError('请先选定洪水或计算时段')
            if record['definition']['context'].get('contextKey') != request.get('context', {}).get('contextKey'):
                raise ValueError('方案与计算流域或尺度不一致')
            if input_definition(request, request.get('inputs', {}).get('rainfallMethod', record['definition']['inputs'].get('rainfallMethod', 'reuse'))) != record['definition']:
                raise ValueError('提交输入与方案修订不一致，请保存当前方案后重新计算')
            if strict and record['calculationSHA256'] and manifest_digest(calculation_identity(request)) != record['calculationSHA256']:
                raise ValueError('计算参数与方案版本不一致，请保存新版本后重新计算')
            request['lineage'] = {**request.get('lineage', {}), 'schemeId': record['id'],
                                  'schemeRevision': record['revision'], 'schemeName': record['name'],
                                  'schemeDefinitionSHA256': record['definitionSHA256']}
        return request

    def history_defaults(self, body=None):
        """New work always uses the archive; old saved requests stay untouched."""
        if body is not None and set(body) - {'idwPower', 'minimumStations'}:
            raise ValueError('历史库默认规则只包含插值幂次和最少有效站')
        if body is not None:
            self.defaults(body)
        return {**self.defaults(), 'rainfallMethod': 'stationIdw', 'stationSourceId': 'LOCAL_OBSERVATIONS',
                'sourceBundleVersionId': 'HISTORICAL_SPATIAL_V1', 'dataMode': 'historical-database'}
