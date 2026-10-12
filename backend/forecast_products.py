"""Immutable, explicitly supplied forecast rainfall; never future observations."""
from datetime import timedelta
import hashlib
import json
import re
import threading
from pathlib import Path

from common import RUNTIME, now, read_json, write_json
from contracts import timestamp, finite_number

CONTEXT_KEY = 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


class ForecastProducts:
    def __init__(self, runtime_root=None):
        self.root = Path(runtime_root or RUNTIME) / 'forecast-rain'
        self.lock = threading.RLock()

    def register(self, body):
        if not isinstance(body, dict) or set(body) != {'name', 'contextKey', 'issueTime', 'hourlyRainfallMm'}:
            raise ValueError('请提供情景名称、流域配置、起报时刻和逐小时雨量')
        if body['contextKey'] != CONTEXT_KEY:
            raise ValueError('降雨情景对应南安溪 1 km 模型')
        name = body['name']
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('降雨情景名称为 1–80 个字符')
        if not isinstance(body['issueTime'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:00(?::00)?', body['issueTime']):
            raise ValueError('请提供本地整点起报时刻')
        issue = timestamp(body['issueTime'])
        rainfall = body['hourlyRainfallMm']
        if not isinstance(rainfall, list) or not 1 <= len(rainfall) <= 72:
            raise ValueError('请给定 1–72 小时的逐小时雨量')
        values = [finite_number(value, '逐小时雨量') for value in rainfall]
        if any(value < 0 or value > 1000 for value in values):
            raise ValueError('逐小时雨量须在 0–1000 mm 之间')
        content = {'schemaVersion': '1.0', 'contextKey': CONTEXT_KEY, 'name': name.strip(),
                   'issueTime': issue.isoformat(), 'sourceType': 'manual-scenario',
                   'sourceLabel': '人工给定逐小时降雨 · 全域均匀分配', 'unit': 'mm',
                   'timeConvention': 'hour-ending', 'spatialMethod': 'uniform-model-grids',
                   'times': [(issue + timedelta(hours=i + 1)).isoformat() for i in range(len(values))],
                   'hourlyRainfallMm': values, 'leadHours': len(values), 'totalMm': sum(values)}
        digest = fingerprint(content)
        version = 'QPF-' + digest
        record = {'versionId': version, 'manifestSHA256': digest, 'createdAt': now(), **content}
        with self.lock:
            path = self.root / f'{version}.json'
            if path.exists():
                return self.resolve(version)
            write_json(path, record)
        return record

    def resolve(self, version):
        if not isinstance(version, str) or not re.fullmatch(r'QPF-[a-f0-9]{64}', version):
            raise ValueError('请选择已保存的降雨情景版本')
        path = self.root / f'{version}.json'
        if not path.is_file():
            raise ValueError('找不到降雨情景版本')
        row = read_json(path)
        content = {key: value for key, value in row.items() if key not in {'versionId', 'manifestSHA256', 'createdAt'}}
        digest = fingerprint(content)
        if row.get('versionId') != version or row.get('manifestSHA256') != digest or version != 'QPF-' + digest:
            raise ValueError('降雨情景与登记摘要不一致')
        # Recheck the immutable contents, including timestamp convention.
        expected = self._content_check(row)
        if not expected:
            raise ValueError('降雨情景内容不完整')
        return row

    @staticmethod
    def _content_check(row):
        issue = timestamp(row.get('issueTime'))
        values = row.get('hourlyRainfallMm')
        if not isinstance(values, list) or not 1 <= len(values) <= 72:
            return False
        return (row.get('contextKey') == CONTEXT_KEY
                and row.get('sourceType') == 'manual-scenario'
                and row.get('spatialMethod') == 'uniform-model-grids'
                and row.get('timeConvention') == 'hour-ending'
                and row.get('unit') == 'mm'
                and row.get('leadHours') == len(values)
                and all(0 <= finite_number(value, '雨量') <= 1000 for value in values)
                and row.get('totalMm') == sum(values)
                and row.get('times') == [(issue + timedelta(hours=i + 1)).isoformat() for i in range(len(values))])

    def list(self):
        return sorted([self.resolve(path.stem) for path in self.root.glob('QPF-*.json')],
                      key=lambda row: row['createdAt'], reverse=True)

    def freeze(self, request):
        selection = request['inputs'].get('forecastPrecipitation')
        if not isinstance(selection, dict) or set(selection) - {'versionId', 'manifestSHA256'}:
            raise ValueError('预报降雨只接受登记版本与摘要')
        row = self.resolve(selection.get('versionId'))
        if selection.get('manifestSHA256', row['manifestSHA256']) != row['manifestSHA256']:
            raise ValueError('预报降雨摘要与登记版本不一致')
        issue = request['issue']
        if row['contextKey'] != request['context']['contextKey'] or timestamp(row['issueTime']) != timestamp(issue['issueTime']):
            raise ValueError('降雨情景的流域配置或起报时刻不一致')
        if row['leadHours'] < issue['leadHours']:
            raise ValueError('降雨情景未覆盖全部预见期')
        request['inputs']['forecastPrecipitation'] = {'versionId': row['versionId'], 'manifestSHA256': row['manifestSHA256']}
        values = {'forecastRainVersionId': row['versionId'], 'forecastRainManifestSHA256': row['manifestSHA256']}
        if any(key in request['lineage'] and request['lineage'][key] != value for key, value in values.items()):
            raise ValueError('预报降雨版本与任务记录不一致')
        request['lineage'].update(values)
        return row


def freeze_forecast_parameters(request, checkpoint, manager):
    """A forecast continues its issue state; changing parameters requires reinitialization."""
    from catalog import PARAMETERS, case_catalog
    from common import safe_task
    from state_integrity import assert_state_input_compatibility
    assert_state_input_compatibility(checkpoint, request)
    if timestamp(checkpoint['time']) != timestamp(request['issue']['issueTime']):
        raise ValueError('请先将实测状态补算至起报时刻')
    actual = {key: float(checkpoint['parameters']['runoff.' + key]) for key in PARAMETERS}
    provided = request.get('parameters', {})
    if provided and ({key: finite_number(value, '预报参数') for key, value in provided.items()} != actual):
        raise ValueError('预报参数与起报状态不一致，请按所选参数重新初始化')
    version = request['inputs'].get('parameterVersionId')
    state_parameter_version = checkpoint.get('parameterVersionId') or 'STATE-PARAMETERS-' + checkpoint['stateVersionId']
    if version:
        if version == state_parameter_version:
            pass
        elif version == 'NANANXI_XAJ_REFERENCE_V1' and actual == case_catalog()['cases']['nananxi-xufan:1km']['parameters']:
            pass
        else:
            if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', version):
                raise ValueError('参数版本编号无效')
            path = manager.runtime_root / 'parameters' / f'{version}.json'
            if not path.is_file():
                raise ValueError('请选择起报状态参数或已登记的率定参数')
            row = read_json(path)
            source = read_json(safe_task(manager.root, row['taskId']) / 'result.json')
            if (not manager.available_artifact(row['taskId'])
                    or any(row.get('context', {}).get(key) != request['context'].get(key) for key in ('contextKey', 'projectId', 'resolutionId', 'modelConfigId'))
                    or row.get('bestParameters') != actual or source.get('bestParameters') != actual):
                raise ValueError('参数版本与起报状态或来源成果不一致')
    if request['issue']['mode'] == 'automatic':
        # Production release is a server-owned record, not a client flag.
        if not version or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', version):
            raise ValueError('自动预报须选择已发布生产参数')
        path = manager.runtime_root / 'parameters' / f'{version}.json'
        row = read_json(path) if path.is_file() else {}
        source_id = row.get('taskId')
        if (row.get('status') != 'production' or row.get('productionEligible') is not True
                or row.get('bestParameters') != actual
                or any(row.get('context', {}).get(key) != request['context'].get(key) for key in ('contextKey', 'projectId', 'resolutionId', 'modelConfigId'))
                or not source_id or not manager.available_artifact(source_id)
                or row.get('review', {}).get('status') != '审核通过'):
            raise ValueError('自动预报须选择已发布生产参数')
        source = read_json(safe_task(manager.root, source_id) / 'result.json')
        if source.get('bestParameters') != actual or source.get('productionEligible') is not True:
            raise ValueError('生产参数与率定来源成果不一致')
    version = version or state_parameter_version
    for key, value in {'parameterVersionId': version, 'stateVersionId': checkpoint['stateVersionId']}.items():
        if key in request['lineage'] and request['lineage'][key] is not None and request['lineage'][key] != value:
            raise ValueError('预报参数或状态与任务版本记录不一致')
    request['parameters'] = actual
    request['inputs']['parameterVersionId'] = version
    request['lineage'].update(parameterVersionId=version, stateVersionId=checkpoint['stateVersionId'])
