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
        if isinstance(body, dict) and 'sourceId' in body:
            return self.register_product(body)
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
        return self.store(content)

    def store(self, content):
        digest = fingerprint(content)
        version = 'QPF-' + digest
        record = {'versionId': version, 'manifestSHA256': digest, 'createdAt': now(), **content}
        with self.lock:
            path = self.root / f'{version}.json'
            if path.exists():
                return self.resolve(version)
            write_json(path, record)
        return record

    def register_product(self, body):
        """Explicit import/producer API. No substitution with future observations."""
        required = {'name', 'contextKey', 'sourceId', 'sourceLabel', 'issueTime', 'unit', 'timeConvention'}
        if (set(body) - required - {'hourlyRainfallMm', 'gridIds', 'hourlyGridRainfallMm'}
                or not required <= set(body)):
            raise ValueError('降雨产品须说明来源、起报时刻、单位及逐小时雨量')
        if (body['contextKey'] != CONTEXT_KEY or body['unit'] != 'mm'
                or body['timeConvention'] != 'hour-ending'):
            raise ValueError('降雨产品须对应南安溪1 km，单位mm、时标hour-ending（小时末）')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(body['sourceId'])):
            raise ValueError('产品来源编号限1–80个字母、数字、下划线或短横线')
        for key in ('name', 'sourceLabel'):
            if not isinstance(body[key], str) or not 1 <= len(body[key].strip()) <= 80:
                raise ValueError('产品名称和来源说明为1–80个字符')
        if not isinstance(body['issueTime'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:00(?::00)?', body['issueTime']):
            raise ValueError('请提供北京时间整点起报时刻，不带时区后缀')
        issue = timestamp(body['issueTime'])
        gridded = 'hourlyGridRainfallMm' in body
        if gridded:
            ids, rows = body.get('gridIds'), body['hourlyGridRainfallMm']
            if (not isinstance(ids, list) or not ids or len(ids) != len(set(str(i) for i in ids))
                    or any(not isinstance(i, str) or not i for i in ids) or 'hourlyRainfallMm' in body):
                raise ValueError('网格产品须提供不重复的网格编号，且不能同时提供均匀雨量')
            if not isinstance(rows, list) or not 1 <= len(rows) <= 72:
                raise ValueError('产品需提供1–72小时雨量')
            if any(not isinstance(row, list) or len(row) != len(ids) for row in rows):
                raise ValueError('每小时雨量列数须与网格编号一致')
            values = [[finite_number(v, '网格雨量') for v in row] for row in rows]
            flattened = [v for row in values for v in row]
        else:
            if 'gridIds' in body:
                raise ValueError('网格编号须配合逐小时网格雨量')
            rows = body.get('hourlyRainfallMm')
            if not isinstance(rows, list) or not 1 <= len(rows) <= 72:
                raise ValueError('产品需提供1–72小时雨量')
            values = [finite_number(v, '逐小时雨量') for v in rows]
            flattened = values
        if any(v < 0 or v > 1000 for v in flattened):
            raise ValueError('逐小时雨量须在0–1000 mm之间')
        content = {**{key: body[key].strip() for key in required}, 'schemaVersion': '2.0',
                   'issueTime': issue.isoformat(), 'sourceType': 'imported-product',
                   'spatialMethod': 'model-grids' if gridded else 'uniform-model-grids',
                   'times': [(issue + timedelta(hours=i+1)).isoformat() for i in range(len(values))],
                   'leadHours': len(values)}
        content.update({'gridIds': body['gridIds'], 'hourlyGridRainfallMm': values} if gridded
                       else {'hourlyRainfallMm': values, 'totalMm': sum(values)})
        return self.store(content)

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
        if row.get('schemaVersion') == '2.0':
            # Reuse validation without creating or rewriting an immutable record.
            class Validate(ForecastProducts):
                def store(self, content):
                    return content
            keys = {'name', 'contextKey', 'sourceId', 'sourceLabel', 'issueTime', 'unit', 'timeConvention',
                    'hourlyRainfallMm', 'gridIds', 'hourlyGridRainfallMm'}
            content = Validate().register_product({k: v for k, v in row.items() if k in keys})
            return all(row.get(k) == v for k, v in content.items())
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

    def window(self, row, issue_time, lead):
        issue = timestamp(issue_time)
        origin = timestamp(row['issueTime'])
        offset = int((issue-origin).total_seconds() / 3600)
        if (issue < origin or (issue-origin).total_seconds() != offset*3600
                or (row['schemaVersion'] == '1.0' and offset != 0)
                or offset+lead > row['leadHours']):
            raise ValueError('降雨产品未覆盖本次起报的完整预见期，等待新产品')
        result = {**row, 'productIssueTime': row['issueTime'], 'issueTime': issue.isoformat(),
                  'times': row['times'][offset:offset+lead], 'leadHours': lead}
        key = 'hourlyGridRainfallMm' if row.get('spatialMethod') == 'model-grids' else 'hourlyRainfallMm'
        result[key] = row[key][offset:offset+lead]
        return result

    def latest(self, source_id, issue_time, lead):
        candidates = []
        for row in self.list():
            if row.get('sourceId', row['versionId']) != source_id:
                continue
            try:
                self.window(row, issue_time, lead)
                candidates.append(row)
            except ValueError:
                continue
        return max(candidates, key=lambda row: (row['issueTime'], row['createdAt'])) if candidates else None

    @staticmethod
    def precipitation(row, grid_ids):
        import numpy as np
        if row.get('spatialMethod') != 'model-grids':
            return np.repeat(np.asarray(row['hourlyRainfallMm'], dtype=float)[:, None], len(grid_ids), axis=1)
        if len(grid_ids) != len(row['gridIds']) or set(grid_ids) != set(row['gridIds']):
            raise ValueError('降雨产品网格与模型不一致，须先按模型网格处理后导入')
        indexes = {key: i for i, key in enumerate(row['gridIds'])}
        return np.asarray(row['hourlyGridRainfallMm'], dtype=float)[:, [indexes[key] for key in grid_ids]]

    def freeze(self, request):
        selection = request['inputs'].get('forecastPrecipitation')
        if not isinstance(selection, dict) or set(selection) - {'versionId', 'manifestSHA256'}:
            raise ValueError('预报降雨只接受登记版本与摘要')
        row = self.resolve(selection.get('versionId'))
        if selection.get('manifestSHA256', row['manifestSHA256']) != row['manifestSHA256']:
            raise ValueError('预报降雨摘要与登记版本不一致')
        issue = request['issue']
        if row['contextKey'] != request['context']['contextKey']:
            raise ValueError('降雨产品的流域配置不一致')
        window = self.window(row, issue['issueTime'], issue['leadHours'])
        request['inputs']['forecastPrecipitation'] = {'versionId': row['versionId'], 'manifestSHA256': row['manifestSHA256']}
        values = {'forecastRainVersionId': row['versionId'], 'forecastRainManifestSHA256': row['manifestSHA256']}
        if any(key in request['lineage'] and request['lineage'][key] != value for key, value in values.items()):
            raise ValueError('预报降雨版本与任务记录不一致')
        request['lineage'].update(values)
        return window


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
    saved = None
    if str(version).startswith('PARAMSET-'):
        saved = manager.parameter_schemes.get(version)
        if (saved['parameters'] != actual or saved['context'] != request['context']
                or saved['routing']['versionId'] != checkpoint.get('routingVersionId')
                or saved['science']['versionId'] != request.get('science', {}).get('versionId')):
            raise ValueError('已保存参数、汇流或科学条件与起报状态不一致，请重新初始化')
    state_parameter_version = checkpoint.get('parameterVersionId') or 'STATE-PARAMETERS-' + checkpoint['stateVersionId']
    if version:
        if saved or version == state_parameter_version:
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
        if saved is None:
            raise ValueError('自动预报请选择已保存参数；不需要审核或发布')
    version = version or state_parameter_version
    for key, value in {'parameterVersionId': version, 'stateVersionId': checkpoint['stateVersionId']}.items():
        if key in request['lineage'] and request['lineage'][key] is not None and request['lineage'][key] != value:
            raise ValueError('预报参数或状态与任务版本记录不一致')
    request['parameters'] = actual
    request['inputs']['parameterVersionId'] = version
    request['lineage'].update(parameterVersionId=version, stateVersionId=checkpoint['stateVersionId'])
