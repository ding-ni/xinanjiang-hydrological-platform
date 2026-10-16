"""User-named parameters for daily hydrological calculations."""
from __future__ import annotations

import copy
import re
import threading
import uuid
from common import read_json, write_json, now, safe_task


class ParameterSchemes:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.runtime_root / 'parameter-schemes'
        self.lock = threading.RLock()

    def get(self, identifier):
        if not re.fullmatch(r'PARAMSET-[a-f0-9]{32}', str(identifier)):
            raise ValueError('请选择已保存的参数')
        return read_json(self.root / (identifier + '.json'))

    def list(self):
        return {'schemes': sorted([read_json(path) for path in self.root.glob('*.json')],
                                  key=lambda row: row['createdAt'], reverse=True)}

    def rename(self, identifier, body):
        with self.lock:
            record = self.get(identifier)
            name = str(body.get('name') or '').strip()
            if set(body) != {'name'} or not name or len(name) > 80:
                raise ValueError('参数名称须为1至80个字符')
            # The values and their provenance remain byte-for-byte equivalent.
            record.update(name=name, updatedAt=now())
            write_json(self.root / (identifier + '.json'), record)
            return record

    def initialization_rule(self, request, source, descriptor):
        initial = request.get('initialization') or source.get('initialization')
        if initial and initial.get('strategy'):
            return {'strategy': initial['strategy']}
        previous = (source.get('lineage') or {}).get('parameterVersionId')
        if isinstance(previous, str) and previous.startswith('PARAMSET-'):
            return {'strategy': self.get(previous)['initialization']['strategy']}
        state_id = source.get('endStateVersionId') or source.get('stateVersionId') or request.get('period', {}).get('initialStateVersionId')
        seen = set()
        while state_id and state_id not in seen:
            seen.add(state_id)
            checkpoint = self.manager.states.checkpoint(state_id)
            initial = checkpoint.get('initialization')
            if initial and initial.get('strategy'):
                return {'strategy': initial['strategy']}
            if (checkpoint.get('drivingIdentity') or {}).get('observedFlowAnchor'):
                return {'strategy': 'OBSERVED_FLOW_ANCHORED'}
            state_id = checkpoint.get('parentStateVersionId')
        return {'strategy': 'CONTINUOUS' if descriptor['statePolicy']['strategy'] == 'continuous-origin' else 'FIXED_WARMUP'}

    def save(self, body):
        # A named set and any routing version it creates are one backup unit.
        with self.lock:
            return self._save(body)

    def _save(self, body):
        if not isinstance(body, dict) or set(body) - {'name', 'sourceTaskId', 'requestId', 'schemeId', 'adoptionRevision'}:
            raise ValueError('请填写参数名称并选择计算成果')
        name = str(body.get('name') or '').strip()
        if not name or len(name) > 80:
            raise ValueError('请为参数命名，最多80个字符')
        if body.get('requestId'):
            for path in self.root.glob('PARAMSET-*.json'):
                old = read_json(path)
                if old.get('requestId') == body['requestId']:
                    identity = lambda value: {key: item for key, item in value.items() if key != 'adoptionRevision'}
                    if identity(old.get('saveRequest', {})) != identity(body):
                        raise ValueError('该保存请求已用于另一组参数')
                    return self._adopt(old, body)
        task_id = body.get('sourceTaskId')
        if str(task_id).startswith('OP-'):
            result = self.manager.operations.result(task_id)
            task_id = result.get('primaryTaskId') or result.get('sourceTaskId')
        if not self.manager.available_artifact(task_id):
            raise ValueError('请选择已完成的计算成果')
        source = self.manager.result(task_id)
        request = read_json(safe_task(self.manager.root, task_id) / 'request.json')
        if body.get('schemeId'):
            scheme = self.manager.schemes.get(body['schemeId'])
            if scheme['definition'].get('context', {}).get('contextKey') != request['context']['contextKey']:
                raise ValueError('参数和方案所属流域不一致')
        parameters = source.get('bestParameters') or source.get('parameters')
        if not isinstance(parameters, dict) or not parameters:
            raise ValueError('该成果没有可保存的参数')
        routing = source.get('routingCandidate') or request.get('routing') or {}
        routing_id = routing.get('routingVersionId') or routing.get('versionId')
        if not routing_id:
            routing_id = request.get('lineage', {}).get('routingVersionId')
        resolved = self.manager.routing_versions.resolve(routing_id)
        if source.get('bestRoutingParameters') and resolved['parameters'] != source['bestRoutingParameters']:
            resolved = self.manager.routing_versions.create({'name': name + ' · 汇流',
                'parentVersionId': routing_id, 'parameters': source['bestRoutingParameters']})
        science = source.get('science') or request.get('science')
        if not science or not science.get('versionId'):
            raise ValueError('该成果缺少蒸散发与起算条件')
        descriptor = self.manager.science_versions.resolve(science['versionId'])
        initial = self.initialization_rule(request, source, descriptor)
        # Warmup is a rule of the parameter set, never the elapsed historic age
        # of a continuous trajectory or the zero of a resumed simulation.
        warmup = request.get('period', {}).get('warmupDays', descriptor['statePolicy'].get('warmupHours', 168) / 24)
        if request.get('period', {}).get('initialStateVersionId') or warmup == 0:
            previous = (source.get('lineage') or {}).get('parameterVersionId')
            if isinstance(previous, str) and previous.startswith('PARAMSET-'):
                warmup = self.get(previous)['warmupDays']
            else:
                warmup = descriptor['statePolicy'].get('warmupHours', 168) / 24
        identifier = 'PARAMSET-' + uuid.uuid4().hex
        record = {'id': identifier, 'name': name, 'createdAt': now(), 'sourceTaskId': task_id,
                  'sourceScheme': copy.deepcopy(request.get('scheme') or {'id': request.get('lineage', {}).get('schemeId')}),
                  'generationMethod': 'manual' if request.get('calibration', {}).get('evaluationMode') == 'manual' else 'automatic' if request.get('runType') == 'calibration' else 'simulation',
                  'requestId': body.get('requestId'), 'saveRequest': copy.deepcopy(body),
                  'context': copy.deepcopy(request['context']), 'contextKey': request['context']['contextKey'],
                  'parameters': copy.deepcopy(parameters), 'routing': {'versionId': resolved['routingVersionId']},
                  'routingParameters': resolved['parameters'], 'science': copy.deepcopy(science),
                  'scienceSnapshot': self.manager.science_versions.export(science['versionId']),
                  'evaporation': {'mode': 'science-version'}, 'warmupDays': warmup,
                  'statePolicy': descriptor['statePolicy']['strategy'], 'initialization': initial,
                  'sourceInputs': copy.deepcopy(request['inputs']), 'sourceLineage': source.get('lineage', {}),
                  'metrics': {key: source.get(key) for key in ('calibrationMetrics', 'validationMetrics', 'independentTest')}}
        write_json(self.root / (identifier + '.json'), record)
        return self._adopt(record, body)

    def _adopt(self, record, body):
        if body.get('schemeId'):
            scheme = self.manager.schemes.get(body['schemeId'])
            if scheme['definition'].get('context', {}).get('contextKey') != record['contextKey']:
                raise ValueError('参数和方案所属流域不一致')
            try:
                adopted = self.manager.schemes.adopt_parameter(body['schemeId'], record['id'], body.get('adoptionRevision', 0))
                return {**record, 'adoptedParameter': adopted, 'adoptionError': None}
            except RuntimeError as error:
                return {**record, 'adoptedParameter': None, 'adoptionError': str(error)}
        return record
