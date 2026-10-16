"""User annotations are separate from immutable model calculations."""
import copy
from common import now, read_json, safe_task


class ResultRepository:
    def __init__(self, manager):
        self.manager = manager

    def metadata(self):
        with self.manager.database.connect() as db:
            return {r['task_id']: {k: (bool(r[k]) if k == 'favorite' else r[k])
                    for k in ('name', 'collection', 'notes', 'favorite', 'revision', 'updated_at')}
                    for r in db.execute('SELECT * FROM result_metadata')}

    def update(self, task_id, body):
        self.result(task_id)
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

    def status(self, identifier):
        if str(identifier).startswith('OP-'):
            return read_json(safe_task(self.manager.runtime_root / 'operations', identifier) / 'status.json')
        return self.manager.get(identifier)

    def annotation(self, identifier):
        metadata = self.metadata()
        if identifier in metadata:
            return metadata[identifier]
        status = self.status(identifier)
        inherited = metadata.get(status.get('primaryTaskId'), {})
        return {**inherited, 'revision': 0} if inherited else {}

    def result(self, identifier):
        if str(identifier).startswith('OP-'):
            row = self.status(identifier)
            if row.get('status') not in {'completed', 'archived'}:
                raise RuntimeError('计算尚未成功完成')
            return read_json(safe_task(self.manager.runtime_root / 'operations', identifier) / 'result.json')
        return self.manager.result(identifier)

    def processes(self, identifier):
        result, status = self.result(identifier), self.status(identifier)
        frozen = result.get('frozenRequest') or (status.get('runConfig') or {}).get('request') or status.get('runConfig') or {}
        windows = {row['id']: row for row in frozen.get('period', {}).get('events', frozen.get('period', {}).get('windows', frozen.get('events', [])))}
        states = {row.get('eventId', row.get('id')): row for row in result.get('eventStates', [])}
        graphs = result.get('hydrographs') or {}
        if isinstance(graphs, list):
            graphs = {row.get('id', str(i)): row.get('hydrograph', row) for i, row in enumerate(graphs)}
        if not graphs and isinstance(result.get('issues'), list):
            graphs = {row.get('issueTime', str(i)): row.get('hydrograph', {}) for i, row in enumerate(result['issues'])}
            states.update({row.get('issueTime', str(i)): row for i, row in enumerate(result['issues'])})
        if not graphs and isinstance(result.get('hydrograph'), dict):
            graphs = {'process': result['hydrograph']}
        processes = []
        for key, graph in graphs.items():
            state, window = states.get(key, {}), windows.get(key, {})
            times = graph.get('forecast') or graph.get('obs') or []
            first = state.get('start') or window.get('start') or (times[0].get('time') if times else None)
            last = state.get('end') or window.get('end') or (times[-1].get('time') if times else None)
            task_id = state.get('stateCalculationTaskId') or state.get('taskId') or result.get('stateCalculationTaskId') or result.get('primaryTaskId') or result.get('taskId') or identifier
            folder = safe_task(self.manager.root, task_id)
            can_query = (folder / 'routing-evidence.json').is_file()
            label = window.get('name') or state.get('label')
            if not label or label == key or str(label).startswith('xf-'):
                label = str(first or '').replace('T', ' ')[:16] + (' 连续过程' if key == 'process' else ' 洪水')
            processes.append({'id': key, 'name': label, 'start': first, 'end': last, 'purpose': window.get('purpose'),
                'sourceTaskId': task_id, 'endStateVersionId': state.get('endStateVersionId') or result.get('endStateVersionId'),
                'canQuery': can_query, 'queryUnavailableReason': None if can_query else '该历史成果未保留完整汇流过程，请按原条件复算',
                'hydrograph': graph})
        return processes

    def process(self, identifier, process_id=None):
        rows = self.processes(identifier)
        if process_id is None and len(rows) != 1:
            raise ValueError('请明确选择一场洪水或连续过程')
        selected = next((row for row in rows if row['id'] == process_id), None) if process_id is not None else (rows[0] if rows else None)
        if selected is None:
            raise KeyError('该成果中没有所选洪水过程')
        return selected

    def scheme_request(self, identifier):
        """Recover editable numerical conditions, never an orchestration envelope."""
        result, status = self.result(identifier), self.status(identifier)
        envelope = status.get('runConfig') or {}
        request = copy.deepcopy(result.get('frozenRequest') or envelope.get('request') or {})
        if not request:
            task_id = result.get('primaryTaskId') or result.get('taskId') or identifier
            request = copy.deepcopy(self.manager.get(task_id).get('runConfig') or {})
        if not request.get('period') or not request.get('inputs'):
            raise ValueError('该成果缺少完整计算条件，无法另存方案')
        if request.get('runType') == 'simulation':
            parameter = envelope.get('parameterSnapshot') or {}
            if request['period'].get('initialStateVersionId') or (parameter.get('initialization') or {}).get('strategy') == 'OBSERVED_FLOW_ANCHORED':
                raise ValueError('该成果承接了特定初态，请在继续模拟中沿用；另建率定方案需明确设置起算规则')
            events = envelope.get('events')
            if events:
                request['period'] = {'mode':'event','events':[{**row,'purpose':'率定'} for row in events],
                    'warmupDays':parameter.get('warmupDays',request['period'].get('warmupDays',7))}
            else:
                period = request['period']
                period.update(mode='continuous', windows=[{'id':'CALIBRATION','name':'模拟时段','start':period['startTime'],'end':period['endTime'],'purpose':'率定'}])
            request['runType'] = 'calibration'
            request['calibration'] = {'initialParameters':request.pop('parameters'), 'statePolicy':parameter.get('statePolicy','independent-warmup'),
                'evaluationMode':'manual', 'optimizer':{'algorithm':'FIXED_PARAMETERS','maxEvaluations':1},
                'objectives':{'metrics':{'nse':True,'kge':False,'peak':False,'time':False,'volume':False},'weights':{'nse':1,'kge':0,'peak':0,'time':0,'volume':0}}}
        if request.get('runType') != 'calibration':
            raise ValueError('此类成果请从原计算条件建立独立方案')
        for key in ('scheme','requestId','lineage'):
            request.pop(key,None)
        return request

    def catalog(self):
        metadata = self.metadata()
        operations = [read_json(path) for path in (self.manager.runtime_root / 'operations').glob('*/status.json')]
        children = {identifier for row in operations for identifier in row.get('childTaskIds', [])}
        tasks = [row for row in self.manager.list() if row['taskId'] not in children and not row.get('internal')
                 and (row.get('runConfig') or {}).get('runType') in {'calibration', 'simulation', 'stateInitialization', 'stateContinuation', 'forecast'}]
        rows = []
        for status in operations + tasks:
            identifier = status['taskId']
            annotation = metadata.get(identifier)
            # Read pre-existing primary-task annotations without rewriting them.
            if annotation is None and status.get('primaryTaskId'):
                annotation = metadata.get(status['primaryTaskId'])
            annotation = copy.deepcopy(annotation or {})
            if identifier not in metadata:
                annotation['revision'] = 0
            request = status.get('runConfig') or {}
            model = request.get('request') or request
            scheme = model.get('scheme') or {}
            period = model.get('period') or {}
            selected = request.get('events') or [row for row in period.get('events', []) if row.get('purpose') != '未使用']
            suggestion = request.get('parameterSnapshot', {}).get('name') or (status.get('lineage') or {}).get('schemeName') or status.get('name') or status.get('runTypeLabel') or '水文计算'
            date = (selected[0].get('start') if selected and isinstance(selected[0], dict) else None) or period.get('startTime') or status.get('createdAt') or ''
            suggestion = str(date)[:10] + ' · ' + suggestion
            rows.append({'id': identifier, 'name': annotation.get('name') or suggestion, 'suggestedName': suggestion,
                'saved': bool(annotation.get('name')), 'metadata': annotation, 'status': status['status'],
                'type': status.get('operation') or status.get('runType'), 'createdAt': status.get('createdAt'),
                'completedAt': status.get('completedAt'), 'contextKey': status.get('contextKey') or model.get('context', {}).get('contextKey'),
                'schemeId': scheme.get('id') or status.get('lineage', {}).get('schemeId'),
                'parameterName': request.get('parameterSnapshot', {}).get('name') or status.get('lineage', {}).get('parameterName'),
                'processCount': len(selected) or len(period.get('windows', [])) or 1,
                'error': status.get('errorMessage'), 'statusLabel': status.get('stageLabel')})
            rows[-1]['sourceTaskIds'] = status.get('childTaskIds', [])
        return {'metadata': metadata, 'results': sorted(rows, key=lambda row: str(row.get('createdAt') or ''), reverse=True)}
