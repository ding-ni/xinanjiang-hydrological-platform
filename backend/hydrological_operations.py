"""One staff action prepares data and completes a durable water-model job."""
from __future__ import annotations

import copy
import math
import traceback
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from common import DEFAULT_INPUT_BUNDLE, now, read_json, write_json, safe_task
from input_products import manifest_digest, hourly_time


LABELS = {'calibration': '参数率定', 'manualTrial': '人工试调', 'eventSimulation': '洪水模拟',
          'continuousSimulation': '连续模拟', 'hindcast': '历史回算',
          'rollingHindcast': '滚动回算', 'automaticForecast': '自动预报', 'manualForecast': '手动预报',
          'continuation': '继续模拟', 'stateReplay': '计算状态', 'rerun': '按原条件复算'}
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

    def get(self, identifier, include_replay=False):
        row=read_json(safe_task(self.root, identifier) / 'status.json')
        if include_replay: row['replayability']=self.replayability(identifier)
        return row

    def replayability(self,identifier,audit_context=None):
        path=safe_task(self.root,identifier)/'result.json'
        if not path.is_file(): return {'available':False,'reasons':['原成果文件缺失或计算尚未完成']}
        result=read_json(path)
        sources=[row['taskId'] for row in result.get('eventStates',[]) if row.get('taskId')] if result.get('hydrographs') and not result.get('bestParameters') else []
        sources=sources or [result.get('primaryTaskId') or result.get('taskId')]
        audits=[self.manager.replayability(source,audit_context=audit_context) for source in dict.fromkeys(sources)]
        return {'available':all(row['available'] for row in audits),'reasons':list(dict.fromkeys(reason for row in audits for reason in row['reasons'])),'verification':'availability-only; content hashes checked before calculation'}

    def list(self, include_replay=False):
        rows = sorted([read_json(path) for path in self.root.glob('*/status.json')], key=lambda row: row['createdAt'], reverse=True)
        if include_replay:
            audit_context={}
            for row in rows:
                row['replayability']=self.replayability(row['taskId'],audit_context)
        return {'operations': rows}

    def update(self, identifier, **changes):
        with self.lock:
            path = safe_task(self.root, identifier) / 'status.json'
            row = {**read_json(path), **changes}
            write_json(path, row)
            if row.get('status') == 'completed' and hasattr(self.manager,'results'):
                self.manager.results.ensure_name(row)
            return row

    def result(self, identifier):
        if self.get(identifier)['status'] != 'completed':
            raise RuntimeError('计算尚未完成')
        return read_json(safe_task(self.root, identifier) / 'result.json')

    def submit(self, body, *, preparation_defaults=None, parameter_snapshot=None):
        allowed = {'operation', 'requestId', 'request', 'rainfallMethod', 'context', 'parameterSchemeId',
                   'events', 'period', 'sourceTaskId', 'issueTime', 'endTime', 'lastIssueTime', 'leadHours', 'name', 'trialContext', 'routingParameters', 'parameterSourceResultId',
                   'forecastRainVersionId', 'observationStateVersionId'}
        if not isinstance(body, dict) or set(body) - allowed or body.get('operation') not in LABELS:
            raise ValueError('请选择计算方式')
        request = copy.deepcopy(body)
        self.manager.results.lifecycle.assert_scheme_active(request)
        context = request.get('context') or (request.get('request') or {}).get('context') or CONTEXT
        if any(context.get(key) != value for key, value in CONTEXT.items()):
            raise ValueError('当前计算对应南安溪徐畈1 km模型')
        request['context'] = context
        kind = request['operation']
        if kind == 'rerun':
            source_id = request.get('sourceTaskId')
            source_result = None
            if str(source_id).startswith('OP-'):
                source_result = self.result(source_id)
                source_id = source_result.get('primaryTaskId') or source_result.get('taskId')
            model = self.frozen_request(source_id)
            if not self.manager.available_artifact(source_id):
                raise ValueError('原计算成果不可用')
            if model.get('runType') not in {'calibration', 'simulation', 'stateInitialization'}:
                raise ValueError('该历史记录不含可复算的完整计算请求')
            missing = [key for key in ('context', 'inputs', 'routing', 'science', 'period') if not model.get(key)]
            if missing:
                raise ValueError('原计算条件缺少：' + '、'.join(missing))
            if any(model['context'].get(key)!=context.get(key) for key in CONTEXT):
                raise ValueError('请选择当前流域的计算成果')
            # The frozen product is authoritative. A replay never prepares new
            # station data or substitutes today's defaults or editor settings.
            model.pop('scheme', None)
            for key in ('sourceBundleVersionId', 'preparationTaskId'):
                model['inputs'].pop(key, None)
            model.setdefault('lineage', {})['replayOfTaskId'] = source_id
            request['frozenRequest'] = model
            if source_result and source_result.get('hydrographs') and source_result.get('eventStates') and not source_result.get('bestParameters'):
                request['sourceEventResult']=source_result
                frozen_events=[]
                for task_id in dict.fromkeys(row['taskId'] for row in source_result['eventStates']):
                    child=self.frozen_request(task_id)
                    if not self.manager.available_artifact(task_id) or child.get('runType')!='simulation':
                        raise ValueError('原洪水模拟任务不完整：'+task_id)
                    if any(child.get('context',{}).get(key)!=context.get(key) for key in CONTEXT):
                        raise ValueError('原洪水模拟包含其他流域的任务')
                    child.pop('scheme',None)
                    for key in ('sourceBundleVersionId','preparationTaskId'): child.get('inputs',{}).pop(key,None)
                    child.setdefault('lineage',{})['replayOfTaskId']=task_id
                    frozen_events.append({'taskId':task_id,'request':child})
                request['frozenEventRequests']=frozen_events
        elif kind in {'calibration', 'manualTrial'}:
            model = request.get('request')
            if not isinstance(model, dict) or model.get('runType') != 'calibration':
                raise ValueError('请选定率定方案与洪水')
            from event_start import validate_event_start
            validate_event_start(model, self.manager.runtime_root)
            if kind == 'manualTrial':
                model.pop('sourcePlan', None)
                settings = model.setdefault('calibration', {})
                settings['evaluationMode'] = 'manual'
                settings['optimizer'] = {'algorithm': 'FIXED_PARAMETERS', 'maxEvaluations': 1}
                for key in ('searchParameters', 'routingSearchParameters', 'target', 'jointProfile'):
                    settings.pop(key, None)
                if request.get('routingParameters') is not None:
                    routing = self.manager.routing_versions.create({'name': '人工试调计算参数', 'parentVersionId': model.get('routing', {}).get('versionId'), 'parameters': request.pop('routingParameters')})
                    model['routing'] = {'versionId': routing['routingVersionId']}
                    for key in ('routingVersionId', 'routingManifestSHA256', 'routingParametersSHA256', 'routingParameterSHA256'):
                        model.get('lineage', {}).pop(key, None)
                model = self.manager.schemes.freeze_trial(model, request.get('trialContext'))
            elif model.get('calibration', {}).get('evaluationMode', 'automatic') != 'automatic':
                raise ValueError('自动率定须使用参数搜索设置')
            request['request'] = model if kind == 'manualTrial' else self.manager.schemes.freeze(model, strict=True)
        elif kind == 'stateReplay':
            source_id = request.get('sourceTaskId')
            if not self.manager.available_artifact(source_id):
                raise ValueError('请选择已完成的计算成果')
            source = self.frozen_request(source_id)
            result = self.manager.result(source_id)
            if source.get('runType') == 'candidateImport' and source.get('originalCalibrationPlan'):
                from original_calibration_plan import original_plan_payload
                parameter_id = source.get('lineage', {}).get('parameterVersionId') or result.get('parameterVersionId')
                request['frozenReplay'] = original_plan_payload(parameter_id, self.manager.runtime_root,
                    products=self.manager.products, operation='fixed-replay')['request']
                source = request['frozenReplay']
            missing = [label for key, label in [('inputs', '雨流资料'), ('routing', '汇流参数'), ('science', '蒸散发与起算条件'), ('period', '计算时段')] if not source.get(key)]
            if not (result.get('bestParameters') or source.get('parameters')):
                missing.append('产流参数')
            if missing:
                raise ValueError('该成果缺少' + '、'.join(missing) + '，请恢复计算条件后模拟')
            if any(source.get('context',{}).get(key)!=context.get(key) for key in CONTEXT):
                raise ValueError('请在该成果所属流域计算状态')
            request['requestId'] = 'STATE-' + source_id
        elif kind == 'continuation':
            self.source_result(request.get('sourceTaskId'))
        else:
            if request.get('parameterSourceResultId'):
                if kind not in {'eventSimulation','continuousSimulation'} or request.get('parameterSchemeId'):
                    raise ValueError('请选择一个明确的参数来源')
                parameter = self.manager.parameter_schemes.from_result(request['parameterSourceResultId'])
                resolved = self.manager.routing_versions.resolve(parameter['routing']['versionId'])
                if resolved['parameters'] != parameter['routingParameters']:
                    actual = self.manager.routing_versions.create({'name':parameter['name'][:65]+' · 汇流',
                        'parentVersionId':parameter['routing']['versionId'],'parameters':parameter['routingParameters']})
                    parameter['routing'] = {'versionId':actual['routingVersionId']}
            else:
                parameter = copy.deepcopy(parameter_snapshot) if parameter_snapshot else self.manager.parameter_schemes.get(request.get('parameterSchemeId'))
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
                    selected.append({key: row[key] for key in ('id', 'name', 'start', 'end')})
                request['events'] = selected
        if kind in {'continuousSimulation', 'continuation'}:
            period = request.get('period') or {}
            end = hourly_time(period.get('endTime'))
            if kind == 'continuousSimulation' and hourly_time(period.get('startTime')) > end:
                raise ValueError('结束时间不能早于开始时间')
        if kind in {'automaticForecast', 'manualForecast'}:
            hourly_time(request.get('issueTime'))
            if type(request.get('leadHours')) is not int or not 1 <= request['leadHours'] <= 72:
                raise ValueError('预见期须为1–72小时整数')
            rain = self.manager.forecast_products.resolve(request.get('forecastRainVersionId'))
            self.manager.forecast_products.window(rain, request['issueTime'], request['leadHours'])
            if request.get('observationStateVersionId'):
                checkpoint = self.manager.states.checkpoint(request['observationStateVersionId'])
                source = self.manager.get(checkpoint['sourceTaskId'])
                if source.get('stateRole') == 'forecastBranch' or source.get('runType') == 'forecast':
                    raise ValueError('预报分支末状态不能用于推进实测状态')
                if hourly_time(checkpoint['time']) > hourly_time(request['issueTime']):
                    raise ValueError('起报不能使用未来状态')
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
        if kind not in {'stateReplay', 'rerun'}:
            request['preparationDefaults'] = copy.deepcopy(preparation_defaults) if preparation_defaults else self.manager.schemes.history_defaults()
            if kind not in {'calibration', 'manualTrial'} and not preparation_defaults:
                from station_network import local_station_network
                request['preparationDefaults']['stationNetwork'] = local_station_network(self.manager.database)
        key = request.get('requestId')
        digest = manifest_digest(request)
        with self.lock:
            for row in self.list()['operations']:
                if key and row.get('requestId') == key:
                    if kind == 'stateReplay' and row['status'] in {'failed', 'cancelled'}:
                        continue
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
        previous = None
        while True:
            self.check_cancelled(identifier)
            current = service.get(task['taskId'])
            changes = dict(stageLabel=stage,
                           progress=min(99, offset + round(span * current.get('progress', 0) / 100)),
                           **({'searchProgress': current['searchProgress']} if current.get('searchProgress') else {}))
            if changes != previous:
                self.update(identifier, **changes)
                previous = changes
            if current['status'] not in ACTIVE:
                if current['status'] not in {'completed', 'archived'}:
                    if current['status'] == 'cancelled':
                        raise OperationCancelled()
                    raise ValueError(current.get('errorMessage') or stage + '未完成')
                return service.result(task['taskId'])
            time.sleep(.5)

    def prepare(self, identifier, request, method, purpose='calibration', origin=None, preceding_hour=False):
        preparation_started = time.perf_counter()
        from scheme_inputs import reuse_preparation
        if request['inputs'].get('dataMode') == 'historical-database':
            method = 'stationIdw'
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
        if request.get('sourcePlan'):
            options['sourcePlan'] = copy.deepcopy(request['sourcePlan'])
        if request['inputs'].get('stationSourceId'):
            options['stationSourceId'] = request['inputs']['stationSourceId']
        if method == 'stationIdw' and request['inputs'].get('stationNetwork'):
            options['stationNetwork'] = request['inputs']['stationNetwork']
        if request['inputs'].get('dataMode') == 'historical-database':
            options['stationSourceId'] = 'LOCAL_OBSERVATIONS'
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
        # Draft trials can have different flood windows from their saved scheme.
        # Their actual preparation options already provide the reusable cache key.
        selection = request.get('scheme')
        binding = None
        if selection and request['inputs'].get('dataMode') == 'historical-database':
            from model_schemes import preparation_binding_key
            record = self.manager.schemes.get(selection['id'], selection['revision'])
            binding_key = preparation_binding_key(record, request)
            with self.manager.database.connect() as db:
                binding = db.execute('SELECT * FROM model_scheme_prepared_inputs WHERE scheme_id=? AND definition_sha256=?',
                                     (selection['id'], binding_key)).fetchone()
        with self.lock:
            self.check_cancelled(identifier)
            if binding:
                self.manager.products.resolve(binding['bundle_version_id'])
                task = self.inputs.get(binding['preparation_task_id'])
            else:
                task = reuse_preparation(self.inputs, options)
            self.update(identifier, preparationTaskId=task['taskId'])
        result = self.wait(identifier, task, self.inputs, '正在备齐雨流资料', 0, 18)
        automatic_source = None
        if (method=='reuse' and not request.get('sourcePlan') and result.get('issues')
                and any(row.get('missingIntervals') or row.get('flowMissingIntervals') for row in result['issues'])):
            parent=self.manager.products.resolve(source)
            automatic_source=parent['manifest'].get('sourceId') or (parent['manifest'].get('sourceOrigin') or {}).get('sourceId')
            if automatic_source:
                # Keep the source declared by this input; defaults cannot fill it.
                options.update(rainfallMethod='stationIdw',stationSourceId=automatic_source,requestId='PREP-'+uuid.uuid4().hex)
                with self.lock:
                    self.check_cancelled(identifier)
                    task=reuse_preparation(self.inputs,options)
                    self.update(identifier,preparationTaskId=task['taskId'])
                result=self.wait(identifier,task,self.inputs,'网格资料缺过程：正在用同一来源站网自动准备',0,18)
        if result.get('issues'):
            raise PreparationIssues(result['issues'])
        status = self.inputs.get(task['taskId'])
        ready = status.get('registeredBundleVersionId')
        if not ready:
            raise ValueError('雨流资料未准备完成')
        if selection and request['inputs'].get('dataMode') == 'historical-database' and not binding:
            with self.manager.database.connect() as db:
                db.execute('INSERT OR IGNORE INTO model_scheme_prepared_inputs VALUES (?,?,?,?)',
                           (selection['id'], binding_key, task['taskId'], ready))
        prepared = copy.deepcopy(request)
        prepared['inputs'].update(bundleVersionId=ready, rainfallMethod=method)
        if automatic_source:
            prepared['inputs']['stationSourceId']=automatic_source
            prepared.setdefault('lineage',{})['automaticInputPreparation']={'rainfallMethod':'stationIdw','sourceId':automatic_source,'preparationTaskId':task['taskId']}
        # The completed queue product has already been verified. Child windows
        # can differ from the preparation's encompassing interval.
        for key in ('sourceBundleVersionId', 'preparationTaskId', 'bundleManifestSHA256'):
            prepared['inputs'].pop(key, None)
        for key in ('inputBundleVersionId', 'inputBundleManifestSHA256', 'inputManifestSHA256',
                    'forcingVersion', 'observationVersion', 'bundleVersionId', 'bundleManifestSHA256'):
            prepared.setdefault('lineage', {}).pop(key, None)
        prepared['lineage']['preparationTaskId'] = task['taskId']
        status_path=safe_task(self.root,identifier)/'status.json'
        previous = (read_json(status_path).get('stageTimings') or {}) if status_path.is_file() else {}
        self.update(identifier,stageTimings={**previous,'inputPreparationSeconds':round(previous.get('inputPreparationSeconds',0)+time.perf_counter()-preparation_started,3)})
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
                 'inputs': {'bundleVersionId': defaults['sourceBundleVersionId'], 'dataMode': 'historical-database',
                            'stationNetwork': defaults.get('stationNetwork'),
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
        groups = [{'start': row['start'], 'end': row['end'], 'memberEventIds': [row['id']]}
                  for row in request['events']]
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
                'frozenRequest':prepared,
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
        preparation_started = time.perf_counter()
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
        if (model.get('scheme') or model.get('lineage', {}).get('trialContext')) and not source_plan:
            model['inputs']['sourceBundleVersionId'] = request['request']['inputs'].get('sourceBundleVersionId') or request['request']['inputs']['bundleVersionId']
            model['inputs']['preparationTaskId'] = model['lineage']['preparationTaskId']
        preparation_seconds = time.perf_counter() - preparation_started
        output = self.child(identifier, model, '正在率定参数' if request['operation'] == 'calibration' else '正在试算参数')
        frozen = self.manager.get(output['taskId'])['runConfig']
        result = self.final_states(identifier, output, frozen)
        result.setdefault('timings', {})['inputPreparationSeconds'] = round(preparation_seconds, 3)
        return result

    def final_states(self, identifier, output, frozen):
        from calibration_groups import group_windows
        # All entry points, including replay of a completed search, must use
        # its actual channel optimum when generating the final model state.
        if output.get('bestRoutingParameters') and not output.get('routingCandidate'):
            output['routingCandidate'] = self.manager.routing_versions.create({'name':'率定计算汇流',
                'parentVersionId':frozen.get('routing',{}).get('versionId','control-split-v2'),
                'parameters':output['bestRoutingParameters']})
            write_json(safe_task(self.manager.root, output['taskId']) / 'result.json', output)
        state_started = time.perf_counter()
        science = self.manager.science_versions.resolve(frozen['science']['versionId'])
        settings = frozen.get('calibration') or {}
        policy = output.get('statePolicy') or settings.get('statePolicy') or science['statePolicy']['strategy']
        rows = frozen['period'].get('events') or frozen['period'].get('windows') or []
        rows = [row for row in rows if row.get('purpose') != '未使用' and row.get('status') != 'inactive']
        if not rows or not output.get('hydrographs'):
            raise ValueError('该成果缺少洪水时段或原流量过程，请恢复计算条件后模拟')
        base = {key: copy.deepcopy(frozen[key]) for key in ('context', 'inputs', 'routing', 'science') if key in frozen}
        base['inputs']['evaporation'] = {'mode': 'science-version'}
        base.update(runType='simulation', parameters=output['bestParameters'], lineage=copy.deepcopy(output.get('lineage') or frozen.get('lineage') or {}))
        for key in ('sourceBundleVersionId', 'preparationTaskId'):
            base['inputs'].pop(key, None)
        if output.get('routingCandidate'):
            base['routing'] = {'versionId': output['routingCandidate']['routingVersionId']}
            for key in ('routingVersionId', 'routingManifestSHA256'):
                base['lineage'].pop(key, None)
        states = []
        if policy == 'continuous-origin':
            groups = [{'start': min(row['start'] for row in rows), 'end': max(row['end'] for row in rows),
                       'memberEventIds': [row['id'] for row in rows]}]
        else:
            groups = ([{'start': row['start'], 'end': row['end'], 'memberEventIds': [row['id']]} for row in rows]
                      if output.get('evaluationPolicy') == 'EQUAL_INDEPENDENT_EVENTS' else group_windows(rows))
        for index, group in enumerate(groups):
            replay = copy.deepcopy(base)
            replay['period'] = {'startTime': group['start'], 'endTime': group['end'], 'warmupDays': frozen['period'].get('warmupDays', 7)}
            if policy == 'continuous-origin':
                origin = output.get('continuousOrigin') or settings.get('continuousOrigin') or frozen['period'].get('continuousOrigin') or science['statePolicy'].get('continuousOrigin')
                if not origin:
                    raise ValueError('该成果缺少连续起点，请恢复计算条件后模拟')
                replay['period']['continuousOrigin'] = origin
            state = self.child(identifier, replay, f'正在保存最终参数状态 {index + 1}/{len(groups)}', 'calibrationFinal')
            for event_id in group['memberEventIds']:
                original = next(row for row in frozen['period'].get('events', frozen['period'].get('windows', [])) if row['id'] == event_id)
                expected = output['hydrographs'].get(event_id)
                actual = self.slice_hydrograph(state['hydrograph'], original['start'], original['end'])
                self.assert_same_flow(expected, actual)
                states.append({'eventId': event_id, 'label': original.get('name') or event_id,
                               'taskId': state['taskId'], 'endStateVersionId': state['endStateVersionId'],
                               'start': original['start'], 'end': original['end'],
                               'trajectoryStart': group['start'], 'trajectoryEnd': group['end']})
        links = {'eventStates': states, 'stateCalculationTaskId': states[0]['taskId'] if states else None,
                 'endStateVersionId': states[0]['endStateVersionId'] if states else None}
        output.setdefault('timings', {})['stateSavingSeconds'] = round(time.perf_counter() - state_started, 3)
        self.check_cancelled(identifier)
        write_json(safe_task(self.manager.root, output['taskId']) / 'result.json', {**output, **links})
        return {**output, 'frozenRequest': frozen, 'primaryTaskId': output['taskId'], 'sourceTaskId': output['taskId'], 'eventStates': states,
                'stateCalculationTaskId': states[0]['taskId'] if states else None,
                'endStateVersionId': states[0]['endStateVersionId'] if states else None}

    @staticmethod
    def assert_same_flow(expected, actual):
        left, right = (expected or {}).get('forecast', []), (actual or {}).get('forecast', [])
        if not left or len(left) != len(right) or any(
            hourly_time(a['time']) != hourly_time(b['time']) or not math.isclose(a['value'], b['value'], rel_tol=1e-10, abs_tol=1e-8)
            for a, b in zip(left, right)
        ):
            raise ValueError('补算流量与原成果不一致，请检查原参数、资料及起算条件')

    def has_state_history(self, task_id):
        from task_diagnostics import TaskDiagnostics
        try:
            TaskDiagnostics(self.manager).catalog(task_id)
            return True
        except (ValueError, RuntimeError, KeyError, FileNotFoundError):
            return False

    def frozen_request(self, task_id):
        source = self.manager.get(task_id).get('runConfig')
        if not source:
            path = safe_task(self.manager.root, task_id) / 'request.json'
            if not path.is_file():
                raise ValueError('该成果缺少保存的计算条件，请恢复计算条件后模拟')
            source = read_json(path)
        if not isinstance(source, dict) or not source:
            raise ValueError('该成果缺少保存的计算条件，请恢复计算条件后模拟')
        return copy.deepcopy(source)

    def rerun_event_result(self,identifier,request):
        original=request['sourceEventResult']
        outputs={}
        for index,row in enumerate(request['frozenEventRequests']):
            outputs[row['taskId']]=self.child(identifier,row['request'],f'正在按原条件复算洪水过程 {index+1}/{len(request["frozenEventRequests"])}','event')
        graphs,states={},[]
        for row in original['eventStates']:
            output=outputs[row['taskId']]
            graph=self.slice_hydrograph(output['hydrograph'],row['start'],row['end'])
            self.assert_same_flow(original['hydrographs'][row['eventId']],graph)
            graphs[row['eventId']]=graph
            states.append({**row,'taskId':output['taskId'],'endStateVersionId':output['endStateVersionId']})
        primary=outputs[original['primaryTaskId']]['taskId']
        result={key:copy.deepcopy(value) for key,value in original.items() if key not in {'operationId','taskId','timings','frozenRequest'}}
        result.update(primaryTaskId=primary,sourceTaskId=primary,hydrographs=graphs,hydrograph=next(iter(graphs.values())),eventStates=states,
                      eventMetrics=[{'eventId':key,**(value.get('metrics') or {})} for key,value in graphs.items()],
                      frozenRequest=copy.deepcopy(original.get('frozenRequest') or self.manager.get(primary)['runConfig']))
        return result

    def state_replay(self, identifier, request):
        source_id = request['sourceTaskId']
        frozen = self.frozen_request(source_id)
        output = self.manager.result(source_id)
        linked = output.get('eventStates') or []
        if (linked and all(self.has_state_history(row['taskId']) for row in linked)) or (
            frozen['runType'] != 'calibration' and self.has_state_history(output.get('stateCalculationTaskId') or source_id)
        ):
            return {**output, 'primaryTaskId': source_id, 'sourceTaskId': source_id}
        if frozen['runType'] == 'calibration':
            result = self.final_states(identifier, output, frozen)
        elif request.get('frozenReplay'):
            replay = self.child(identifier, request['frozenReplay'], '正在按保存参数计算状态', 'stateReplay')
            replay = self.final_states(identifier, replay, self.manager.get(replay['taskId'])['runConfig'])
            result = {**output, **{key: replay[key] for key in ('eventStates', 'stateCalculationTaskId', 'endStateVersionId')},
                      'primaryTaskId': source_id, 'sourceTaskId': source_id}
        else:
            # Reuse the submitted forcing, initial state, science and parameters.
            # This path does not prepare new rain or perform parameter search.
            frozen.pop('scheme', None)
            result = self.child(identifier, frozen, '正在计算逐时状态', 'stateReplay')
            self.assert_same_flow(output.get('hydrograph'), result.get('hydrograph'))
            result = {**output, 'primaryTaskId': source_id, 'sourceTaskId': source_id,
                      'stateCalculationTaskId': result['taskId'], 'endStateVersionId': result['endStateVersionId']}
        self.check_cancelled(identifier)
        links = {key: result[key] for key in ('eventStates', 'stateCalculationTaskId', 'endStateVersionId') if key in result}
        write_json(safe_task(self.manager.root, source_id) / 'result.json', {**output, **links})
        # Existing staff operations continue to point at their original process.
        for row in self.list()['operations']:
            if row['taskId'] == identifier or row['status'] != 'completed':
                continue
            path = safe_task(self.root, row['taskId']) / 'result.json'
            original = read_json(path)
            if original.get('primaryTaskId') == source_id:
                write_json(path, {**original, **links})
        return result

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

    def automatic_forecast(self, identifier, request):
        """Observe up to issue time, then fork a QPF-only branch. Never re-calibrate."""
        issue = hourly_time(request['issueTime'])
        parent = request.get('observationStateVersionId')
        checkpoint = self.manager.states.checkpoint(parent) if parent else None
        start = hourly_time(checkpoint['time']) + timedelta(hours=1) if checkpoint else issue
        model = self.daily_request(request, {'startTime': min(start, issue).isoformat(), 'endTime': issue.isoformat()})
        origin = None
        if checkpoint:
            origin = (checkpoint.get('drivingIdentity') or {}).get('start')
            if not origin:
                raise ValueError('起报状态缺少实测驱动来源，请重新启动初始化')
            if checkpoint['parameters'] != {'runoff.'+k: v for k, v in model['parameters'].items()}:
                raise ValueError('参数已改变，请重新启动初始化')
            model['period'] = {'startTime': min(start, issue).isoformat(), 'endTime': issue.isoformat(),
                               'warmupDays': 0, 'initialStateVersionId': parent}
        prepared = self.prepare(identifier, model, request['preparationDefaults']['rainfallMethod'],
                                'simulation', origin=origin)
        if checkpoint and hourly_time(checkpoint['time']) == issue:
            state = {'taskId': checkpoint['sourceTaskId'], 'endStateVersionId': parent, 'stateTime': checkpoint['time']}
        elif checkpoint:
            state = self.child(identifier, prepared, '正在用新实测资料推进状态', 'observationBase')
        else:
            state = self.initialize_saved_rule(identifier, prepared, request['parameterSnapshot'], issue, 'observationBase')
            if state is None:
                state = self.child(identifier, prepared, '正在预热并形成起报状态', 'observationBase')
        self.update(identifier, observationStateVersionId=state['endStateVersionId'])
        end = issue + timedelta(hours=request['leadHours'])
        forecast = copy.deepcopy(prepared)
        forecast.update(runType='forecast', issue={'mode': 'manual' if request['operation'] == 'manualForecast' else 'automatic', 'sectionId': '70115800',
            'issueTime': issue.isoformat(), 'leadHours': request['leadHours'], 'forecastEndTime': end.isoformat()})
        forecast['period'] = {'startTime': (issue+timedelta(hours=1)).isoformat(), 'endTime': end.isoformat(),
                              'warmupDays': 0, 'initialStateVersionId': state['endStateVersionId']}
        forecast['inputs'].update(parameterVersionId=request['parameterSnapshot']['id'],
            forecastPrecipitation={'versionId': request['forecastRainVersionId']})
        result = self.child(identifier, forecast, '正在计算降雨产品预报', 'forecastBranch')
        return {**result, 'primaryTaskId': result['taskId'], 'sourceTaskId': result['taskId'],
                'stateCalculationTaskId': result['taskId'], 'observationStateVersionId': state['endStateVersionId'],
                'observationTaskId': state['taskId'], 'parameterSchemeId': request['parameterSnapshot']['id'],
                'parameterName': request['parameterSnapshot']['name'], 'rainfallMode': 'forecast-product'}

    def execute(self, identifier):
        folder = safe_task(self.root, identifier)
        try:
            with self.lock:
                self.check_cancelled(identifier)
                request = read_json(folder / 'request.json')
                instant=now()
                created=datetime.fromisoformat((self.get(identifier).get('createdAt') or instant).replace('Z','+00:00'))
                queue_seconds=max(0,(datetime.fromisoformat(instant.replace('Z','+00:00'))-created).total_seconds())
                self.update(identifier, status='running', stageLabel='正在准备计算', startedAt=instant, stageTimings={'queueSeconds':round(queue_seconds,3)})
            kind = request['operation']
            started = time.perf_counter()
            if kind == 'rerun' and request.get('frozenEventRequests'):
                result=self.rerun_event_result(identifier,request)
            elif kind == 'rerun':
                output = self.child(identifier, request['frozenRequest'], '正在按冻结条件复算')
                frozen = self.manager.get(output['taskId'])['runConfig']
                if frozen['runType'] == 'calibration':
                    result = self.final_states(identifier, output, frozen)
                else:
                    result = {**output, 'primaryTaskId': output['taskId'], 'stateCalculationTaskId': output['taskId'], 'frozenRequest': frozen}
            elif kind in {'calibration', 'manualTrial'}:
                result = self.calibration(identifier, request)
            elif kind == 'stateReplay':
                result = self.state_replay(identifier, request)
            elif kind == 'eventSimulation':
                result = self.event_simulation(identifier, request)
            elif kind == 'continuation':
                result = self.continuation(identifier, request)
            elif kind in {'hindcast', 'rollingHindcast'}:
                result = self.hindcast(identifier, request)
            elif kind in {'automaticForecast', 'manualForecast'}:
                result = self.automatic_forecast(identifier, request)
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
                result.setdefault('timings', {})['operationSeconds'] = round(time.perf_counter() - started, 3)
                result['timings'].update(self.get(identifier).get('stageTimings') or {})
                # Child timings cover the same frozen model runs used for charts
                # and full end states, not additional diagnostic simulations.
                phases = ('loadingSeconds','warmupSeconds','runoffSeconds','routingSeconds','statePersistenceSeconds','checkpointSeconds','runoffFirstCallSeconds')
                child_timings = [self.manager.result(child).get('timings',{}) for child in self.get(identifier)['childTaskIds']
                    if self.manager.get(child)['status'] in {'completed','archived'}]
                for phase in phases:
                    if any(phase in row for row in child_timings):
                        result['timings'][phase] = round(sum(row.get(phase,0) for row in child_timings),3)
                write_json(folder / 'result.json', result)
                self.update(identifier, status='completed', progress=100, stageLabel='计算完成', completedAt=now(),
                            primaryTaskId=result.get('primaryTaskId'), lineage=result.get('lineage', {}))
        except OperationCancelled:
            self.update(identifier, status='cancelled', stageLabel='已停止', completedAt=now())
        except Exception as error:
            (folder / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
            self.update(identifier, status='failed', stageLabel='计算未完成', errorMessage=str(error),
                        issues=getattr(error, 'issues', []), completedAt=now())
