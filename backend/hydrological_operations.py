"""One staff action prepares data and completes a durable water-model job."""
from __future__ import annotations

import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from common import DEFAULT_INPUT_BUNDLE, now, read_json, write_json, safe_task
from input_products import manifest_digest, hourly_time


LABELS = {'calibration': '参数率定', 'manualTrial': '人工试调', 'eventSimulation': '洪水模拟',
          'continuousSimulation': '连续模拟', 'hindcast': '历史回算',
          'rollingHindcast': '滚动回算', 'continuation': '继续模拟'}
ACTIVE = {'queued', 'running', 'cancelling'}
CONTEXT = {'contextKey': 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km',
           'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'modelConfigId': 'nananxi-xufan-1km'}


def flood_catalog(database):
    events = []
    for record in database.events():
        observed = record.get('observedInputs') or {}
        identifier = record.get('eventId') or record.get('id')
        events.append({**record, 'id': identifier, 'eventId': identifier,
                       'name': record.get('name') or str(record.get('floodCode') or identifier) + ' 号洪水',
                       'sourceFloodNo': record.get('floodCode') or record.get('sourceFloodNo'),
                       'observedPeak': observed.get('observedPeakFlowM3s', record.get('observedPeak')),
                       'peakTime': observed.get('observedPeakTime', record.get('peakTime')),
                       'sectionId': '70115800', 'purpose': '未使用',
                       'status': 'active', 'runtimeRegistered': True,
                       'rainCompleteness': observed.get('rainCompleteness'),
                       'flowCompleteness': observed.get('riverCompleteness')})
    return {'events': events, 'count': len(events)}


class OperationCancelled(Exception):
    pass


class PreparationIssues(ValueError):
    def __init__(self, issues):
        super().__init__('所选资料未通过检查')
        self.issues = issues


class HydrologicalOperations:
    def __init__(self, manager, inputs):
        self.manager, self.inputs = manager, inputs
        self.root = manager.runtime_root / 'operations'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cancelled = set()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hydrological-operation')
        for path in self.root.glob('*/status.json'):
            if read_json(path).get('status') in ACTIVE:
                write_json(path, {**read_json(path), 'status': 'failed', 'stageLabel': '计算已中断',
                                  'errorMessage': '服务关闭时本次计算尚未完成', 'completedAt': now()})

    def get(self, identifier):
        return read_json(safe_task(self.root, identifier) / 'status.json')

    def list(self):
        return {'operations': sorted([read_json(path) for path in self.root.glob('*/status.json')],
                                     key=lambda row: row['createdAt'], reverse=True)}

    def update(self, identifier, **changes):
        with self.lock:
            path = safe_task(self.root, identifier) / 'status.json'
            row = {**read_json(path), **changes}
            write_json(path, row)
            return row

    def result(self, identifier):
        if self.get(identifier)['status'] != 'completed':
            raise RuntimeError('计算尚未完成')
        return read_json(safe_task(self.root, identifier) / 'result.json')

    def submit(self, body):
        allowed = {'operation', 'requestId', 'request', 'rainfallMethod', 'context', 'parameterSchemeId',
                   'events', 'period', 'sourceTaskId', 'issueTime', 'endTime', 'lastIssueTime', 'leadHours', 'name'}
        if not isinstance(body, dict) or set(body) - allowed or body.get('operation') not in LABELS:
            raise ValueError('请选择计算方式')
        request = copy.deepcopy(body)
        context = request.get('context') or (request.get('request') or {}).get('context') or CONTEXT
        if any(context.get(key) != value for key, value in CONTEXT.items()):
            raise ValueError('当前计算对应南安溪徐畈1 km模型')
        request['context'] = context
        kind = request['operation']
        if kind in {'calibration', 'manualTrial'}:
            model = request.get('request')
            if not isinstance(model, dict) or model.get('runType') != 'calibration':
                raise ValueError('请选定率定方案与洪水')
            if kind == 'manualTrial':
                model.setdefault('calibration', {})['evaluationMode'] = 'manual'
                model['calibration'].setdefault('optimizer', {}).update(algorithm='FIXED_PARAMETERS', maxEvaluations=1)
            elif model.get('calibration', {}).get('evaluationMode', 'automatic') != 'automatic':
                raise ValueError('自动率定须使用参数搜索设置')
            request['request'] = self.manager.schemes.freeze(model)
        elif kind == 'continuation':
            self.source_result(request.get('sourceTaskId'))
        else:
            parameter = self.manager.parameter_schemes.get(request.get('parameterSchemeId'))
            if any(parameter['context'].get(key) != context.get(key) for key in CONTEXT):
                raise ValueError('参数与当前流域不一致')
            request['parameterSnapshot'] = parameter
            if kind == 'eventSimulation':
                rows = request.get('events')
                if not isinstance(rows, list) or not rows or len(rows) > 500:
                    raise ValueError('请选择需要模拟的洪水')
                known = {row['id']: row for row in flood_catalog(self.manager.database)['events']}
                selected = []
                for item in rows:
                    identifier = item.get('id') if isinstance(item, dict) else item
                    if identifier not in known or any(row['id'] == identifier for row in selected):
                        raise ValueError('所选洪水不存在或重复')
                    row = known[identifier]
                    if isinstance(item, dict) and any(item.get(key, row[key]) != row[key] for key in ('start', 'end')):
                        raise ValueError('洪水时段与资料库不一致')
                    selected.append({key: row[key] for key in ('id', 'start', 'end')})
                request['events'] = selected
        if kind in {'continuousSimulation', 'continuation'}:
            period = request.get('period') or {}
            end = hourly_time(period.get('endTime'))
            if kind == 'continuousSimulation' and hourly_time(period.get('startTime')) > end:
                raise ValueError('结束时间不能早于开始时间')
        if kind in {'hindcast', 'rollingHindcast'}:
            first = hourly_time(request.get('issueTime'))
            if kind == 'hindcast':
                if hourly_time(request.get('endTime')) <= first:
                    raise ValueError('回算终点须晚于起报时刻')
            else:
                if hourly_time(request.get('lastIssueTime')) < first:
                    raise ValueError('最后起报时刻不能早于首次起报时刻')
                if type(request.get('leadHours')) is not int or request['leadHours'] < 1:
                    raise ValueError('预见期须为正整数小时')
        request['preparationDefaults'] = self.manager.schemes.defaults()
        key = request.get('requestId')
        digest = manifest_digest(request)
        with self.lock:
            for row in self.list()['operations']:
                if key and row.get('requestId') == key:
                    if row['requestDigest'] != digest:
                        raise ValueError('同次提交的计算条件已改变')
                    return row
            identifier = 'OP-' + uuid.uuid4().hex
            folder = safe_task(self.root, identifier)
            folder.mkdir()
            row = {'taskId': identifier, 'operationId': identifier, 'operation': kind,
                   'runType': kind, 'runTypeLabel': LABELS[kind],
                   'name': request.get('name') or (request.get('request') or {}).get('lineage', {}).get('schemeName') or LABELS[kind],
                   'contextKey': context['contextKey'], 'context': context, 'status': 'queued',
                   'progress': 0, 'stageLabel': '等待计算', 'errorMessage': '', 'issues': [],
                   'childTaskIds': [], 'createdAt': now(), 'completedAt': None,
                   'requestId': key, 'requestDigest': digest, 'runConfig': request,
                   'lineage': (request.get('request') or {}).get('lineage', {}), 'resultSource': 'model-backend'}
            if request.get('parameterSnapshot'):
                row.update(parameterSchemeId=request['parameterSnapshot']['id'],
                           parameterSchemeName=request['parameterSnapshot']['name'])
            write_json(folder / 'request.json', request)
            write_json(folder / 'status.json', row)
            self.executor.submit(self.execute, identifier)
            return row

    def check_cancelled(self, identifier):
        if identifier in self.cancelled:
            raise OperationCancelled()

    def cancel(self, identifier):
        with self.lock:
            row = self.get(identifier)
            if row['status'] not in ACTIVE:
                return row
            self.cancelled.add(identifier)
            for task_id in row.get('childTaskIds', []):
                if self.manager.get(task_id)['status'] in ACTIVE:
                    self.manager.cancel(task_id)
            preparation_id = row.get('preparationTaskId')
            if preparation_id and self.inputs.get(preparation_id)['status'] in ACTIVE:
                self.inputs.cancel(preparation_id)
            return self.update(identifier, status='cancelled', stageLabel='已停止', completedAt=now())

    def close(self):
        for row in self.list()['operations']:
            if row['status'] in ACTIVE:
                self.cancel(row['taskId'])
        self.executor.shutdown(wait=True)

    def wait(self, identifier, task, service, stage, offset=0, span=100):
        while True:
            self.check_cancelled(identifier)
            current = service.get(task['taskId'])
            self.update(identifier, stageLabel=stage,
                        progress=max(self.get(identifier).get('progress', 0), min(99, offset + round(span * current.get('progress', 0) / 100))),
                        **({'searchProgress': current['searchProgress']} if current.get('searchProgress') else {}))
            if current['status'] not in ACTIVE:
                if current['status'] not in {'completed', 'archived'}:
                    if current['status'] == 'cancelled':
                        raise OperationCancelled()
                    raise ValueError(current.get('errorMessage') or stage + '未完成')
                return service.result(task['taskId'])
            time.sleep(.15)

    def prepare(self, identifier, request, method, purpose='calibration', origin=None, preceding_hour=False):
        from scheme_inputs import reuse_preparation
        source = request['inputs'].get('sourceBundleVersionId') or request['inputs']['bundleVersionId']
        options = {'operation': 'schemeInput', 'requestId': 'PREP-' + uuid.uuid4().hex,
                   'name': '水文计算资料', 'projectId': request['context']['projectId'],
                   'resolutionId': request['context']['resolutionId'], 'parentBundleVersionId': source,
                   'period': request['period'], 'rainfallMethod': method,
                   'idwPower': request['inputs'].get('idwPower', 2),
                   'minimumStations': request['inputs'].get('minimumStations', 1),
                   'science': request.get('science', {}), 'evaporation': request['inputs'].get('evaporation', {}),
                   'statePolicy': request.get('calibration', {}).get('statePolicy') or ('continuous-origin' if request['period'].get('continuousOrigin') else 'independent-warmup'),
                   'continuousOrigin': origin or request.get('calibration', {}).get('continuousOrigin') or request['period'].get('continuousOrigin'),
                   'operationPurpose': purpose}
        if request['inputs'].get('stationSourceId'):
            options['stationSourceId'] = request['inputs']['stationSourceId']
        # Include the saved trajectory when checking a newly extended forcing
        # product, while the numeric child still begins at the next hour.
        if origin and purpose == 'simulation':
            options['period'] = copy.deepcopy(request['period'])
            options['period'].pop('initialStateVersionId', None)
            options['statePolicy'] = 'continuous-origin'
        if preceding_hour:
            options['period'] = copy.deepcopy(options['period'])
            if options['period'].get('mode') == 'event':
                for event in options['period']['events']:
                    event['start'] = (hourly_time(event['start']) - timedelta(hours=1)).isoformat()
            else:
                options['period']['startTime'] = (hourly_time(options['period']['startTime']) - timedelta(hours=1)).isoformat()
            if options.get('continuousOrigin'):
                options['continuousOrigin'] = (hourly_time(options['continuousOrigin']) - timedelta(hours=1)).isoformat()
        with self.lock:
            self.check_cancelled(identifier)
            task = reuse_preparation(self.inputs, options)
            self.update(identifier, preparationTaskId=task['taskId'])
        result = self.wait(identifier, task, self.inputs, '正在备齐雨流资料', 0, 18)
        if result.get('issues'):
            raise PreparationIssues(result['issues'])
        status = self.inputs.get(task['taskId'])
        ready = status.get('registeredBundleVersionId')
        if not ready:
            raise ValueError('雨流资料未准备完成')
        prepared = copy.deepcopy(request)
        prepared['inputs'].update(bundleVersionId=ready, rainfallMethod=method)
        # The completed queue product has already been verified. Child windows
        # can differ from the preparation's encompassing interval.
        for key in ('sourceBundleVersionId', 'preparationTaskId', 'bundleManifestSHA256'):
            prepared['inputs'].pop(key, None)
        for key in ('inputBundleVersionId', 'inputBundleManifestSHA256', 'inputManifestSHA256',
                    'forcingVersion', 'observationVersion', 'bundleVersionId', 'bundleManifestSHA256'):
            prepared.setdefault('lineage', {}).pop(key, None)
        prepared['lineage']['preparationTaskId'] = task['taskId']
        return prepared

    def child(self, identifier, request, stage, role='calculation'):
        request = copy.deepcopy(request)
        request['requestId'] = 'RUN-' + uuid.uuid4().hex
        request.setdefault('lineage', {})['operationId'] = identifier
        with self.lock:
            self.check_cancelled(identifier)
            task = self.manager.submit(request, operation_id=identifier, state_role=role)
            children = self.get(identifier)['childTaskIds'] + [task['taskId']]
            self.update(identifier, childTaskIds=children)
        result = self.wait(identifier, task, self.manager, stage, 18, 78)
        return {**result, 'taskId': task['taskId']}

    def daily_request(self, request, period):
        parameter, defaults = request['parameterSnapshot'], request['preparationDefaults']
        model = {'schemaVersion': '2.0', 'runType': 'simulation', 'context': request['context'],
                 'period': {**period, 'warmupDays': parameter['warmupDays']},
                 'parameters': parameter['parameters'], 'routing': parameter['routing'],
                 'science': parameter['science'], 'lineage': {'parameterSchemeId': parameter['id'],
                    'parameterVersionId': parameter['id'], 'parameterName': parameter['name']},
                 'inputs': {'bundleVersionId': defaults['sourceBundleVersionId'],
                            'evaporation': parameter['evaporation'], 'idwPower': defaults['idwPower'],
                            'minimumStations': defaults['minimumStations']}}
        if defaults.get('stationSourceId'):
            model['inputs']['stationSourceId'] = defaults['stationSourceId']
        if parameter['statePolicy'] == 'continuous-origin' and not period.get('initialStateVersionId'):
            model['period']['continuousOrigin'] = (hourly_time(period['startTime']) - timedelta(days=parameter['warmupDays'])).isoformat()
        return model

    def initialize_saved_rule(self, identifier, model, parameter, state_time, role):
        """Apply the saved runoff/routing start rule at this calculation's hour."""
        strategy = (parameter.get('initialization') or {}).get('strategy', 'FIXED_WARMUP')
        if strategy != 'OBSERVED_FLOW_ANCHORED':
            return None
        initial = copy.deepcopy(model)
        initial.update(runType='stateInitialization', initialization={'strategy': strategy})
        initial['period'] = {'startTime': state_time.isoformat(), 'endTime': state_time.isoformat(),
                             'warmupDays': parameter['warmupDays']}
        return self.child(identifier, initial, '正在形成起算状态', role)

    def source_result(self, identifier):
        if str(identifier).startswith('OP-'):
            result = self.result(identifier)
            identifier = result.get('stateCalculationTaskId') or result.get('primaryTaskId')
        if not self.manager.available_artifact(identifier):
            raise ValueError('请选择已完成的模拟成果')
        result = self.manager.result(identifier)
        if not result.get('endStateVersionId'):
            raise ValueError('该成果没有可承接的末状态')
        return identifier, result

    def continuation(self, identifier, request):
        from state_integrity import validated_parent_state
        source_id, result = self.source_result(request['sourceTaskId'])
        source = read_json(safe_task(self.manager.root, source_id) / 'request.json')
        checkpoint = validated_parent_state(self.manager.runtime_root / 'states' / (result['endStateVersionId'] + '.json'), self.manager.root, result['endStateVersionId'])
        model = {key: copy.deepcopy(source[key]) for key in ('context', 'parameters', 'inputs', 'routing', 'science')}
        start = hourly_time(checkpoint['time']) + timedelta(hours=1)
        end = hourly_time(request['period']['endTime'])
        if end < start:
            raise ValueError('续算终点须晚于已有状态时刻')
        model.update(runType='simulation', period={'startTime': start.isoformat(), 'endTime': end.isoformat(),
                     'warmupDays': 0, 'initialStateVersionId': result['endStateVersionId']},
                     lineage={'sourceTaskId': source_id, 'parameterVersionId': checkpoint.get('parameterVersionId')})
        source_bundle = self.manager.products.resolve(model['inputs']['bundleVersionId'])
        processing = source_bundle['manifest'].get('processing') or {}
        method = processing.get('rainfallMethod', 'reuse')
        if method == 'stationIdw':
            model['inputs'].update(bundleVersionId=source_bundle['manifest']['parentBundleVersionId'],
                stationSourceId=(source_bundle['manifest'].get('sourceOrigin') or {}).get('sourceId'),
                idwPower=processing['idwPower'], minimumStations=processing['minimumStations'])
        origin = (checkpoint.get('drivingIdentity') or {}).get('start')
        model = self.prepare(identifier, model, method, 'simulation', origin)
        output = self.child(identifier, model, '正在继续模拟', 'continuousBase')
        return {**output, 'primaryTaskId': output['taskId'], 'sourceTaskId': source_id,
                'stateCalculationTaskId': output['taskId']}

    def event_simulation(self, identifier, request):
        from calibration_groups import group_windows
        groups = group_windows([{**row, 'purpose': '率定'} for row in request['events']])
        first, last = min(row['start'] for row in groups), max(row['end'] for row in groups)
        model = self.daily_request(request, {'startTime': first, 'endTime': last})
        model['period'].update(mode='event', events=[{**row, 'purpose': '率定'} for row in request['events']])
        # Flood simulations have independent warm-up intervals; unused years
        # between events are not a continuous driving requirement.
        model['period'].pop('continuousOrigin', None)
        anchored = (request['parameterSnapshot'].get('initialization') or {}).get('strategy') == 'OBSERVED_FLOW_ANCHORED'
        prepared = self.prepare(identifier, model, request['preparationDefaults']['rainfallMethod'], 'simulation', preceding_hour=anchored)
        graphs, metrics, states, primary = {}, [], [], None
        for index, group in enumerate(groups):
            child = copy.deepcopy(prepared)
            child['period'] = {'startTime': group['start'], 'endTime': group['end'],
                               'warmupDays': request['parameterSnapshot']['warmupDays']}
            if request['parameterSnapshot']['statePolicy'] == 'continuous-origin':
                child['period']['continuousOrigin'] = (hourly_time(group['start']) - timedelta(days=child['period']['warmupDays'])).isoformat()
            initial = self.initialize_saved_rule(identifier, child, request['parameterSnapshot'],
                                                  hourly_time(group['start']) - timedelta(hours=1), 'eventInitial')
            if initial:
                child['period'].update(warmupDays=0, initialStateVersionId=initial['endStateVersionId'])
                child['period'].pop('continuousOrigin', None)
            output = self.child(identifier, child, f'正在模拟洪水 {index + 1}/{len(groups)}', 'event')
            primary = primary or output['taskId']
            for event_id in group['memberEventIds']:
                window = next(row for row in request['events'] if row['id'] == event_id)
                quality = read_json(self.manager.products.resolve(self.manager.get(output['taskId'])['runConfig']['inputs']['bundleVersionId'])['paths']['quality'])
                graphs[event_id] = self.slice_hydrograph(output['hydrograph'], window['start'], window['end'], quality)
                metrics.append({'eventId': event_id, **(graphs[event_id].get('metrics') or {})})
                states.append({'eventId': event_id, 'taskId': output['taskId'], 'endStateVersionId': output['endStateVersionId'],
                               'start': window['start'], 'end': window['end']})
        return {'primaryTaskId': primary, 'sourceTaskId': primary, 'hydrographs': graphs,
                'hydrograph': next(iter(graphs.values())), 'eventMetrics': metrics, 'eventStates': states,
                'parameters': request['parameterSnapshot']['parameters'], 'parameterSchemeId': request['parameterSnapshot']['id']}

    @staticmethod
    def slice_hydrograph(graph, start, end, quality=None):
        first, last = hourly_time(start), hourly_time(end)
        result = copy.deepcopy(graph)
        indexes = [index for index, row in enumerate(graph.get('forecast', [])) if first <= hourly_time(row['time']) <= last]
        for key in ('forecast', 'obs', 'forcing'):
            if isinstance(result.get(key), list):
                result[key] = [row for row in result[key] if first <= hourly_time(row['time']) <= last]
        if isinstance(result.get('forcing'), dict):
            for key in ('precipitation', 'temperature'):
                values = result['forcing'].get(key)
                if isinstance(values, list) and len(values) == len(graph.get('forecast', [])):
                    result['forcing'][key] = [values[index] for index in indexes]
        values = result.get('forecast', [])
        if values:
            peak = max(values, key=lambda row: row['value'])
            result.update(peak=peak['value'], peakTime=peak['time'], volume=sum(row['value'] for row in values) * 3600,
                          startMark=values[0]['time'])
        # Recompute subwindow scores against paired valid observations, without
        # allowing them to alter any simulation state.
        from worker import kernels
        import pandas as pd
        if result.get('obs'):
            from observation_quality import evaluate_observation_coverage, masked_volume_metrics
            observed = pd.Series([row['value'] for row in result['obs']], index=pd.to_datetime([row['time'] for row in result['obs']]))
            simulated = pd.Series([row['value'] for row in result['forecast']], index=pd.to_datetime([row['time'] for row in result['forecast']]))
            raw = kernels.metrics(observed, simulated) if observed.notna().all() else {}
            if quality:
                coverage = evaluate_observation_coverage(simulated.index, observed, quality)
                result['observationCoverage'] = coverage
                if not coverage['accepted'] or len(observed.dropna()) < 3 or observed.dropna().var() == 0:
                    raw['NSE'] = None
            result['volumeEvaluation'] = masked_volume_metrics(simulated.index, observed, simulated)
            result['metrics'] = {'nse': raw.get('NSE'), 'peakErr': raw.get('peak_error_percent'),
                                 'timeErrH': raw.get('peak_time_error_hours'), 'volErr': raw.get('volume_error_percent')}
        return result

    def calibration(self, identifier, request):
        from contracts import continuous_windows
        model = copy.deepcopy(request['request'])
        if model['period'].get('mode') == 'continuous' and not model['calibration'].get('statePolicy'):
            model['calibration']['statePolicy'] = 'continuous-origin'
            windows = continuous_windows(model['period'])
            model['calibration']['continuousOrigin'] = (min(hourly_time(row['start']) for row in windows) - timedelta(days=model['period'].get('warmupDays', 7))).isoformat()
        source_plan = model.get('sourcePlan')
        if source_plan:
            from original_calibration_plan import validate_original_request
            model = validate_original_request(model, self.manager.runtime_root)
            if request.get('rainfallMethod') not in {None, 'reuse'}:
                raise ValueError('原方案沿用其已登记网格降雨')
            # The preparation validates coverage and QC. It cannot redefine the
            # immutable input identity of a restored source-calibration plan.
            for key in ('sourceBundleVersionId', 'preparationTaskId'):
                model['inputs'].pop(key, None)
            checked = self.prepare(identifier, model, 'reuse')
            model.setdefault('lineage', {})['preparationTaskId'] = checked['lineage']['preparationTaskId']
        else:
            model = self.prepare(identifier, model, request.get('rainfallMethod') or 'reuse')
        # Keep the scheme's source identity while binding the actual prepared
        # product in the immutable request.
        if model.get('scheme') and not source_plan:
            model['inputs']['sourceBundleVersionId'] = request['request']['inputs'].get('sourceBundleVersionId') or request['request']['inputs']['bundleVersionId']
            model['inputs']['preparationTaskId'] = model['lineage']['preparationTaskId']
        output = self.child(identifier, model, '正在率定参数' if request['operation'] == 'calibration' else '正在试算参数')
        frozen = self.manager.get(output['taskId'])['runConfig']
        if output.get('bestRoutingParameters') and not output.get('routingCandidate'):
            candidate = self.manager.routing_versions.create({'name': '率定计算汇流',
                'parentVersionId': model.get('routing', {}).get('versionId', 'control-split-v2'), 'parameters': output['bestRoutingParameters']})
            output['routingCandidate'] = candidate
            write_json(safe_task(self.manager.root, output['taskId']) / 'result.json', output)
        base = {key: copy.deepcopy(frozen[key]) for key in ('context', 'inputs', 'routing', 'science') if key in frozen}
        base['inputs']['evaporation'] = {'mode': 'science-version'}
        base.update(runType='simulation', parameters=output['bestParameters'], lineage=copy.deepcopy(output['lineage']))
        for key in ('sourceBundleVersionId', 'preparationTaskId'):
            base['inputs'].pop(key, None)
        if output.get('routingCandidate'):
            base['routing'] = {'versionId': output['routingCandidate']['routingVersionId']}
            for key in ('routingVersionId', 'routingManifestSHA256'):
                base['lineage'].pop(key, None)
        states = []
        if output['statePolicy'] == 'continuous-origin':
            rows = frozen['period'].get('events') or frozen['period']['windows']
            rows = [row for row in rows if row.get('purpose') != '未使用' and row.get('status') != 'inactive']
            groups = [{'start': min(row['start'] for row in rows), 'end': max(row['end'] for row in rows),
                       'memberEventIds': [row['id'] for row in rows]}]
        else:
            groups = output['overlapGroups']
        for index, group in enumerate(groups):
            replay = copy.deepcopy(base)
            replay['period'] = {'startTime': group['start'], 'endTime': group['end'], 'warmupDays': model['period'].get('warmupDays', 7)}
            if output['statePolicy'] == 'continuous-origin':
                replay['period']['continuousOrigin'] = output['continuousOrigin']
            state = self.child(identifier, replay, f'正在保存最终参数状态 {index + 1}/{len(groups)}', 'calibrationFinal')
            for event_id in group['memberEventIds']:
                original = next(row for row in frozen['period'].get('events', frozen['period'].get('windows', [])) if row['id'] == event_id)
                states.append({'eventId': event_id, 'taskId': state['taskId'], 'endStateVersionId': state['endStateVersionId'],
                               'start': original['start'], 'end': original['end'],
                               'trajectoryStart': group['start'], 'trajectoryEnd': group['end']})
        return {**output, 'frozenRequest': frozen, 'primaryTaskId': output['taskId'], 'sourceTaskId': output['taskId'], 'eventStates': states,
                'stateCalculationTaskId': states[0]['taskId'] if states else None,
                'endStateVersionId': states[0]['endStateVersionId'] if states else None}

    def hindcast(self, identifier, request):
        first = hourly_time(request['issueTime'])
        last = hourly_time(request.get('lastIssueTime') or request['issueTime'])
        final = hourly_time(request['endTime']) if request['operation'] == 'hindcast' else last + timedelta(hours=request['leadHours'])
        model = self.daily_request(request, {'startTime': first.isoformat(), 'endTime': final.isoformat()})
        prepared = self.prepare(identifier, model, request['preparationDefaults']['rainfallMethod'], 'simulation')
        base_request = copy.deepcopy(prepared)
        base_request['period']['endTime'] = first.isoformat()
        state = self.initialize_saved_rule(identifier, prepared, request['parameterSnapshot'], first, 'hindcastBase')
        if state is None:
            state = self.compatible_issue_state(prepared, first)
        if state is None:
            state = self.child(identifier, base_request, '正在形成起报状态', 'hindcastBase')
        elif hourly_time(state['stateTime']) < first:
            base_request['period'] = {'startTime': (hourly_time(state['stateTime']) + timedelta(hours=1)).isoformat(),
                'endTime': first.isoformat(), 'warmupDays': 0, 'initialStateVersionId': state['endStateVersionId']}
            state = self.child(identifier, base_request, '正在衔接起报状态', 'hindcastBase')
        issues = []
        issue = first
        while issue <= last:
            self.check_cancelled(identifier)
            branch = copy.deepcopy(prepared)
            branch['period'] = {'startTime': (issue + timedelta(hours=1)).isoformat(),
                'endTime': final.isoformat() if request['operation'] == 'hindcast' else (issue + timedelta(hours=request['leadHours'])).isoformat(),
                'warmupDays': 0, 'initialStateVersionId': state['endStateVersionId']}
            result = self.child(identifier, branch, '正在回算 ' + issue.strftime('%Y-%m-%d %H:%M'), 'forecastBranch')
            issues.append({'issueTime': issue.isoformat(), 'taskId': result['taskId'],
                           'hydrograph': result['hydrograph'], 'metrics': result.get('metrics'),
                           'endStateVersionId': result['endStateVersionId'], 'issueStateVersionId': state['endStateVersionId']})
            if issue == last:
                break
            following = copy.deepcopy(prepared)
            next_hour = issue + timedelta(hours=1)
            following['period'] = {'startTime': next_hour.isoformat(), 'endTime': next_hour.isoformat(),
                                  'warmupDays': 0, 'initialStateVersionId': state['endStateVersionId']}
            state = self.child(identifier, following, '正在推进起报状态', 'hindcastBase')
            issue = next_hour
        return {'primaryTaskId': issues[0]['taskId'], 'sourceTaskId': issues[0]['taskId'],
                'stateCalculationTaskId': issues[0]['taskId'], 'hydrograph': issues[0]['hydrograph'],
                'metrics': issues[0]['metrics'], 'endStateVersionId': issues[0]['endStateVersionId'],
                'issues': issues, 'parameterSchemeId': request['parameterSnapshot']['id'],
                'parameterName': request['parameterSnapshot']['name'], 'parameters': request['parameterSnapshot']['parameters'],
                'rainfallMode': 'historical-grid', 'issueStepHours': 1}

    def compatible_issue_state(self, prepared, issue):
        """Use saved observation-driven states; forecast branches are excluded."""
        from state_integrity import assert_state_input_compatibility
        from contracts import freeze_input_selection, freeze_routing_selection
        from science_context import freeze_science_selection
        verified, bundle = freeze_input_selection(copy.deepcopy(prepared), self.manager.products)
        verified = freeze_routing_selection(verified, bundle, self.manager.routing_versions)
        verified, _ = freeze_science_selection(verified, self.manager.runtime_root)
        earliest = hourly_time(prepared['period'].get('continuousOrigin') or prepared['period']['startTime'])
        if not prepared['period'].get('continuousOrigin'):
            earliest -= timedelta(days=prepared['period'].get('warmupDays', 7))
        candidates = []
        for path in (self.manager.runtime_root / 'states').glob('*.json'):
            checkpoint = read_json(path)
            instant = hourly_time(checkpoint['time'])
            if not earliest <= instant <= issue or not self.manager.available_artifact(checkpoint['sourceTaskId']):
                continue
            source = self.manager.get(checkpoint['sourceTaskId'])
            if source.get('stateRole') == 'forecastBranch' or source.get('runType') == 'forecast':
                continue
            if checkpoint.get('parameters') != {'runoff.' + key: value for key, value in prepared['parameters'].items()}:
                continue
            if checkpoint.get('context') != prepared['context'] or (checkpoint.get('drivingIdentity') or {}).get('start') != earliest.isoformat():
                continue
            try:
                checkpoint = self.manager.states.checkpoint(checkpoint['stateVersionId'])
                assert_state_input_compatibility(checkpoint, verified, self.manager.products)
                candidates.append(checkpoint)
            except (ValueError, RuntimeError, KeyError, FileNotFoundError):
                continue
        if not candidates:
            return None
        selected = max(candidates, key=lambda row: row['time'])
        return {'endStateVersionId': selected['stateVersionId'], 'stateTime': selected['time'],
                'taskId': selected['sourceTaskId']}

    def execute(self, identifier):
        folder = safe_task(self.root, identifier)
        try:
            with self.lock:
                self.check_cancelled(identifier)
                request = read_json(folder / 'request.json')
                self.update(identifier, status='running', stageLabel='正在准备计算', startedAt=now())
            kind = request['operation']
            if kind in {'calibration', 'manualTrial'}:
                result = self.calibration(identifier, request)
            elif kind == 'eventSimulation':
                result = self.event_simulation(identifier, request)
            elif kind == 'continuation':
                result = self.continuation(identifier, request)
            elif kind in {'hindcast', 'rollingHindcast'}:
                result = self.hindcast(identifier, request)
            else:
                model = self.daily_request(request, request['period'])
                anchored = (request['parameterSnapshot'].get('initialization') or {}).get('strategy') == 'OBSERVED_FLOW_ANCHORED'
                model = self.prepare(identifier, model, request['preparationDefaults']['rainfallMethod'], 'simulation', preceding_hour=anchored)
                initial = self.initialize_saved_rule(identifier, model, request['parameterSnapshot'],
                    hourly_time(model['period']['startTime']) - timedelta(hours=1), 'continuousInitial')
                if initial:
                    model['period'].update(warmupDays=0, initialStateVersionId=initial['endStateVersionId'])
                    model['period'].pop('continuousOrigin', None)
                result = self.child(identifier, model, '正在连续模拟', 'continuousBase')
                result.update(primaryTaskId=result['taskId'], sourceTaskId=result['taskId'], stateCalculationTaskId=result['taskId'])
            with self.lock:
                self.check_cancelled(identifier)
                result.update(operationId=identifier, operation=kind, context=request['context'], resultSource='model-backend',
                              name=self.get(identifier)['name'])
                write_json(folder / 'result.json', result)
                self.update(identifier, status='completed', progress=100, stageLabel='计算完成', completedAt=now(),
                            primaryTaskId=result.get('primaryTaskId'), lineage=result.get('lineage', {}))
        except OperationCancelled:
            self.update(identifier, status='cancelled', stageLabel='已停止', completedAt=now())
        except Exception as error:
            self.update(identifier, status='failed', stageLabel='计算未完成', errorMessage=str(error),
                        issues=getattr(error, 'issues', []), completedAt=now())
