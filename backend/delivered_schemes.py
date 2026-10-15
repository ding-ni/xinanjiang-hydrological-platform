"""Idempotent links from delivered selections to ordinary project schemes."""
import copy
import json
import uuid

from common import now, read_json
from input_products import manifest_digest
from model_schemes import calculation_identity, input_definition


def migrate_delivered_schemes(schemes, products):
    database = schemes.database
    with database.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS scheme_input_assets (
            asset_id TEXT PRIMARY KEY, scheme_id TEXT, scheme_revision INTEGER,
            role TEXT NOT NULL, payload TEXT NOT NULL)''')
    report = []
    # Prefer original calibration records; manual parameter variants are children.
    candidates = {}
    for path in (database.runtime / 'parameters').glob('*.json'):
        record = read_json(path)
        plan = record.get('originalCalibrationPlan') or {}
        if plan.get('planSHA256') and record.get('candidateKind') in {'frozen-calibration', 'manual-edit'}:
            key = plan['planSHA256']
            if key not in candidates or record.get('candidateKind') == 'frozen-calibration':
                candidates[key] = record
    for digest, record in sorted(candidates.items()):
        asset = 'PLAN-' + digest
        with database.connect() as db:
            if db.execute('SELECT 1 FROM scheme_input_assets WHERE asset_id=? AND scheme_id IS NOT NULL', (asset,)).fetchone():
                continue
        try:
            from original_calibration_plan import original_plan_payload
            payload = original_plan_payload(record['parameterVersionId'], database.runtime, products, 'search')
            request, plan = payload['request'], payload['plan']
            # Never change the immutable colleague conditions while importing.
            purposes = {row['id']: row['purpose'] for row in plan.get('sourceEvents', [])}
            editor = {'originalCalibrationPlan': plan, 'context': request['context'],
                      'events': [{**row, 'purpose': purposes.get(row['id'], '未使用'), 'status': 'active'}
                                 for row in plan.get('sourceEventWindows', request['period']['events'])]}
            linked = None
            for row in schemes.list()['schemes']:
                if row.get('originalPlanSHA256') != digest:
                    continue
                saved = schemes.get(row['id'])
                if manifest_digest(calculation_identity(saved['calculationDraft'])) == manifest_digest(calculation_identity(request)):
                    linked = row
                    break
            with database.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM scheme_input_assets WHERE asset_id=? AND scheme_id IS NOT NULL', (asset,)).fetchone():
                    continue
                if linked:
                    identifier, revision = linked['id'], linked['revision']
                else:
                    identifier, revision = 'SCHEME-' + uuid.uuid4().hex, 1
                    definition = input_definition(request)
                    name = '同事优选洪水 · ' + digest[:8]
                    db.execute('INSERT INTO model_schemes VALUES (?,?,?,?)', (identifier, name[:80], revision, now()))
                    db.execute('INSERT INTO model_scheme_revisions VALUES (?,?,?,?,?,?)',
                        (identifier, revision, json.dumps(definition), manifest_digest(definition), json.dumps(request), now()))
                    db.execute('INSERT INTO model_scheme_snapshots VALUES (?,?,?,?)',
                        (identifier, revision, json.dumps(editor), manifest_digest(calculation_identity(request))))
                db.execute('INSERT OR REPLACE INTO scheme_input_assets VALUES (?,?,?,?,?)',
                    (asset, identifier, revision, 'delivered-plan', json.dumps({'planSHA256': digest,
                    'bundleVersionId': request['inputs']['bundleVersionId'], 'parameterVersionId': record['parameterVersionId']})))
        except (ValueError, KeyError, FileNotFoundError) as error:
            with database.connect() as db:
                db.execute('INSERT OR REPLACE INTO scheme_input_assets VALUES (?,?,?,?,?)',
                    (asset, None, None, 'unlinked', json.dumps({'name': record.get('name'), 'reason': str(error)})))
            report.append({'assetId': asset, 'reason': str(error)})

    # Every registered selection is retained, including those without parameters.
    for path in sorted(products.registry_root.glob('INPUT-*.json')):
        asset = path.stem
        with database.connect() as db:
            if db.execute('SELECT 1 FROM scheme_input_assets WHERE asset_id=? AND scheme_id IS NOT NULL', (asset,)).fetchone():
                continue
        try:
            bundle = products.resolve(asset)
            manifest = bundle['manifest']
            task_path = products.tasks_root / manifest['sourceTaskId'] / 'request.json'
            processing = read_json(task_path)
            # FULL_FL_CODE_TABLE is a catalogue, not evidence that every flood
            # was selected. Only explicit schemeInput windows are selections.
            windows = processing.get('windows', []) if processing.get('operation') == 'schemeInput' else processing.get('selectedEventWindows', [])
            if not windows and processing.get('operation') == 'schemeInput':
                windows = processing.get('period', {}).get('events', [])
            context = {'contextKey': 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km',
                       'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'modelConfigId': 'nananxi-xufan-1km'}
            # Match actual frozen references, never just equal event numbers.
            existing = []
            for row in schemes.list()['schemes']:
                saved = schemes.get(row['id'])
                inputs = saved['definition'].get('inputs', {})
                if asset in {inputs.get('bundleVersionId'), saved.get('editor', {}).get('schemeSourceBundleVersionId')}:
                    existing.append(row)
            # A generated child product can be referenced only by a frozen
            # numerical task. Its explicit scheme/revision is valid lineage;
            # a matching label or event number is not.
            linked_revisions = []
            for task_file in (database.runtime / 'tasks').glob('*/request.json'):
                task = read_json(task_file)
                if task.get('inputs', {}).get('bundleVersionId') != asset:
                    continue
                selection = task.get('scheme') or {}
                if not selection and task.get('lineage', {}).get('schemeId'):
                    selection = {'id': task['lineage']['schemeId'], 'revision': task['lineage'].get('schemeRevision')}
                if selection.get('id') and selection.get('revision'):
                    try:
                        saved = schemes.get(selection['id'], selection['revision'])
                    except KeyError:
                        continue
                    linked_revisions.append({'id': saved['id'], 'revision': saved['revision']})
            existing.extend(row for row in linked_revisions if row not in existing)
            with database.connect() as db:
                if not existing:
                    linked = db.execute("SELECT scheme_id,scheme_revision FROM scheme_input_assets WHERE json_extract(payload,'$.bundleVersionId')=? AND role='delivered-plan'", (asset,)).fetchone()
                    if linked:
                        existing = [{'id': linked[0], 'revision': linked[1]}]
                identifier = existing[0]['id'] if existing else None
                revision = existing[0]['revision'] if existing else 0
                role = 'input-snapshot'
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM scheme_input_assets WHERE asset_id=? AND scheme_id IS NOT NULL', (asset,)).fetchone():
                    continue
                if not identifier and windows:
                    identifier = 'SCHEME-' + uuid.uuid4().hex
                    # The delivery carries windows but no reliable purpose/parameter
                    # settings. Retain the selection and make the missing work explicit.
                    events = [{**copy.deepcopy(row), 'id': row.get('id') or row['eventId'], 'purpose': '未使用', 'status': 'active',
                               'selectedForScheme': True} for row in windows]
                    editor = {'context': context, 'events': events, 'schemeSourceBundleVersionId': asset,
                              'schemeDataMode': 'frozen-input', 'rainfallMethod': 'reuse',
                              'deliverySelection': events, 'configurationPending': True,
                              'deliveryProcessing': copy.deepcopy(processing)}
                    # These are known preparation conditions, not guessed model
                    # parameters. Keep them with the incomplete selection.
                    for key in ('idwPower', 'minimumStations', 'statePolicy', 'continuousOrigin', 'stationSourceId'):
                        if key in processing:
                            editor[key] = copy.deepcopy(processing[key])
                    if processing.get('stationNetwork'):
                        editor['schemeStationNetwork'] = copy.deepcopy(processing['stationNetwork'])
                    if 'warmupHours' in processing:
                        editor['warmupDays'] = processing['warmupHours'] / 24
                    if processing.get('science', {}).get('versionId'):
                        editor['scienceVersionId'] = processing['science']['versionId']
                    if processing.get('evaporation', {}).get('mode'):
                        editor['evaporationMode'] = processing['evaporation']['mode']
                    db.execute('INSERT INTO model_schemes VALUES (?,?,0,?)', (identifier, manifest['name'][:80], now()))
                    db.execute('INSERT INTO model_scheme_editors VALUES (?,?,?)', (identifier, json.dumps(editor), now()))
                    role = 'selection-draft'
                db.execute('INSERT OR REPLACE INTO scheme_input_assets VALUES (?,?,?,?,?)', (asset, identifier, revision,
                    role if identifier else 'unlinked', json.dumps({'name': manifest['name'], 'bundleVersionId': asset,
                    'operation': manifest.get('operation'), 'eventCount': len(windows), 'sourceTaskId': manifest.get('sourceTaskId'),
                    'parentBundleVersionId': processing.get('parentBundleVersionId'),
                    'processing': processing,
                    'linkedSchemeIds': [row['id'] for row in existing],
                    'reason': None if identifier else '仅能核实加工文件，未找到完整选取及所属方案，保留待关联'})))
        except (ValueError, KeyError, FileNotFoundError) as error:
            with database.connect() as db:
                db.execute('INSERT OR REPLACE INTO scheme_input_assets VALUES (?,?,?,?,?)',
                    (asset, None, None, 'unlinked', json.dumps({'bundleVersionId': asset, 'reason': str(error)})))
            report.append({'assetId': asset, 'reason': str(error)})
    # Explicit processing parentage can establish ownership of an intermediate
    # package even when no numerical task references that ancestor directly.
    # Shared names, flood numbers and equal MDB hashes never establish ownership.
    with database.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = {row['asset_id']: dict(row) for row in db.execute('SELECT * FROM scheme_input_assets')}
        for child in rows.values():
            if not child['scheme_id']:
                continue
            details = json.loads(child['payload'])
            parent_id = details.get('parentBundleVersionId')
            visited = set()
            while parent_id in rows and parent_id not in visited:
                visited.add(parent_id)
                parent = rows[parent_id]
                parent_details = json.loads(parent['payload'])
                linked_ids = set(parent_details.get('linkedSchemeIds', []))
                linked_ids.add(child['scheme_id'])
                parent_details['linkedSchemeIds'] = sorted(linked_ids)
                parent_details.setdefault('derivedInputIds', [])
                if child['asset_id'] not in parent_details['derivedInputIds']:
                    parent_details['derivedInputIds'].append(child['asset_id'])
                if not parent['scheme_id']:
                    parent.update(scheme_id=child['scheme_id'], scheme_revision=child['scheme_revision'], role='processing-ancestor')
                    parent_details['reason'] = '由原加工请求的明确父子引用关联，保留为方案加工前序记录'
                parent['payload'] = json.dumps(parent_details)
                db.execute('UPDATE scheme_input_assets SET scheme_id=?,scheme_revision=?,role=?,payload=? WHERE asset_id=?',
                           (parent['scheme_id'], parent['scheme_revision'], parent['role'], parent['payload'], parent_id))
                parent_id = parent_details.get('parentBundleVersionId')
    return report


def scheme_assets(schemes, identifier=None):
    with schemes.database.connect() as db:
        rows = db.execute('SELECT * FROM scheme_input_assets').fetchall()
    assets = [{**dict(row), 'details': json.loads(row['payload'])} for row in rows]
    return [row for row in assets if not identifier or row['scheme_id'] == identifier or identifier in row['details'].get('linkedSchemeIds', [])]
