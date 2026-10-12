"""Import a portable checkpoint, preserving its numerical values and origin.

Import is an explicit provenance operation, never a reconstructed simulation.
Only a completed commit exposes the checkpoint to the existing continuation API.
"""
import copy
import hashlib
import json
import math
import re

from common import MODEL, now, read_json, write_json
from catalog import PARAMETERS, case_catalog
from contracts import freeze_input_selection, freeze_routing_selection, timestamp
from state_integrity import assert_state_input_compatibility


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def validate_checkpoint(manager, checkpoint):
    if not isinstance(checkpoint, dict) or checkpoint.get('schemaVersion') != '2.0':
        raise ValueError('请选择本平台导出的 2.0 版状态 JSON')
    # Reject NaN, infinity and malformed numeric types before any clean/coercion.
    fingerprint(checkpoint)
    context = checkpoint.get('context')
    expected = {'projectId': 'nananxi-xufan', 'resolutionId': '1km', 'modelConfigId': 'nananxi-xufan-1km',
                'contextKey': 'nananxi-xufan:distributed-xinanjiang:nananxi-xufan-1km'}
    if context != expected:
        raise ValueError('状态所属流域、尺度或模型配置不匹配')
    time = timestamp(checkpoint.get('time'))
    if not 1960 <= time.year <= 2024:
        raise ValueError('状态时刻超出已登记资料年份')
    if not re.fullmatch(r'JOB-[A-Za-z0-9_-]+', str(checkpoint.get('sourceTaskId', ''))) or checkpoint.get('stateVersionId') != 'STATE-' + checkpoint['sourceTaskId']:
        raise ValueError('来源任务与状态编号不一致')
    required = ('inputBundleVersionId', 'inputBundleManifestSHA256', 'routingVersionId', 'routingManifestSHA256',
                'forcingVersion', 'observationVersion', 'inputManifestSHA256', 'petMode')
    if any(not isinstance(checkpoint.get(k), str) or not checkpoint[k] for k in required):
        raise ValueError('状态文件缺少输入、汇流或参数版本信息')
    if checkpoint.get('resultSource') != 'model-backend':
        raise ValueError('请选择模型计算导出的状态')
    if not re.fullmatch(r'[0-9a-f]{64}', checkpoint['inputManifestSHA256']):
        raise ValueError('来源输入摘要格式无效')
    if checkpoint.get('parameterVersionId') is not None and not isinstance(checkpoint['parameterVersionId'], str):
        raise ValueError('参数版本格式无效')
    request, bundle = freeze_input_selection({'runType': 'stateImport', 'context': context,
        'inputs': {'bundleVersionId': checkpoint['inputBundleVersionId']},
        'routing': {'versionId': checkpoint['routingVersionId']}}, manager.products)
    request = freeze_routing_selection(request, bundle, manager.routing_versions)
    assert_state_input_compatibility(checkpoint, request)
    params = checkpoint.get('parameters')
    if not isinstance(params, dict) or set(params) != {f'runoff.{k}' for k in PARAMETERS}:
        raise ValueError('状态参数表不完整')
    for key, (_, low, high, _) in PARAMETERS.items():
        v = params[f'runoff.{key}']
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f'{key} 超出参数范围')
    if params['runoff.WM'] <= params['runoff.WUM'] + params['runoff.WLM'] or params['runoff.KI'] + params['runoff.KG'] >= .98:
        raise ValueError('状态参数的容量或出流系数关系无效')
    # Reuse delivered loaders and physical validation; no equations are copied.
    from worker import kernels, validate_land_state_arrays, load_parameter_set, read_topology_json, np
    grids = kernels.load_grids(bundle['paths']['grids'])
    ids = [g['unit_id'] for g in grids]
    if checkpoint.get('gridIds') != ids:
        raise ValueError('网格编号或顺序与登记资料不一致')
    geometry = read_json(bundle['paths']['geometry'])
    positions = {(f['properties']['row'], f['properties']['col']): f['geometry']['coordinates'][0] for f in geometry['features']}
    expected_geometry = [{'id': g['unit_id'], 'bounds': positions[(int(g['source_row']), int(g['source_col']))],
                          'areaKm2': float(g['area_km2'])} for g in grids]
    if checkpoint.get('gridGeometry') != expected_geometry:
        raise ValueError('状态网格空间信息与登记资料不一致')

    def array(value, shape, label):
        if not shape:
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{label} 必须为非负有限数值')
        elif not isinstance(value, list) or len(value) != shape[0]:
            raise ValueError(f'{label} 数组维度不匹配')
        else:
            for item in value:
                array(item, shape[1:], label)

    land = checkpoint.get('land')
    fields = {'swe', 'snow_liquid', 'WU', 'WL', 'WD', 'S', 'FR'}
    if not isinstance(land, dict) or set(land) != fields:
        raise ValueError('陆面状态字段不完整')
    for key in fields:
        array(land[key], (len(ids),), key)
    native = kernels.candidate_params(load_parameter_set(MODEL / 'config/parameter_set_land.json'), params)
    validate_land_state_arrays({k: np.asarray(v) for k, v in land.items()}, native, len(ids))
    units, _ = kernels.build_mapping(bundle['paths']['mapping'], bundle['paths']['topology'], grids)
    reaches = read_topology_json(bundle['paths']['topology']).reaches_upstream_first()
    routing = checkpoint.get('routing')
    shapes = {'slope_state': (len(units), 3), 'prev_in': (len(reaches),), 'prev_out': (len(reaches),)}
    if not isinstance(routing, dict) or set(routing) != set(shapes):
        raise ValueError('汇流状态字段不完整')
    for key, shape in shapes.items():
        array(routing[key], shape, key)
    if not case_catalog(bundle)['cases']['nananxi-xufan:1km']['ready']:
        raise ValueError('请先完成模型运行环境安装')
    return request


def import_checkpoint(manager, body):
    if not isinstance(body, dict) or set(body) - {'checkpoint', 'name'} or 'checkpoint' not in body:
        raise ValueError('请提供状态文件')
    name = body.get('name', '')
    if not isinstance(name, str) or len(name.strip()) > 80:
        raise ValueError('状态名称最多 80 个字符')
    source = body['checkpoint']
    request = validate_checkpoint(manager, source)
    digest = fingerprint(source)
    task_id = f'JOB-import-{digest}'
    state_id = f'STATE-{task_id}'
    with manager.lock:
        folder = manager.root / task_id
        if (folder / 'status.json').exists() and manager.available_artifact(task_id):
            return {'state': manager.states.checkpoint(state_id), 'task': manager.get(task_id), 'reused': True}
        folder.mkdir(exist_ok=True)
        # Same digest can resume a interrupted import; a completed import is never overwritten.
        provenance = {'operation': 'stateImport', 'sourceSHA256': digest,
                      'sourceStateVersionId': source['stateVersionId'], 'sourceTaskId': source['sourceTaskId']}
        manifest_digest = fingerprint(provenance)
        checkpoint = {**copy.deepcopy(source), 'sourceTaskId': task_id, 'stateVersionId': state_id,
                      'inputManifestSHA256': manifest_digest, 'importProvenance': provenance, 'productionEligible': False}
        lineage = {**request['lineage'], 'inputManifestSHA256': manifest_digest,
                   'parameterVersionId': source.get('parameterVersionId')}
        request.update({'period': {'endTime': source['time']}, 'parameters': {k.removeprefix('runoff.'): v for k,v in source['parameters'].items()},
                        'lineage': lineage, 'importProvenance': provenance})
        result = {'taskId': task_id, 'resultVersionId': f'RESULT-{task_id}', 'stateVersionId': state_id,
                  'stateTime': source['time'], 'context': source['context'], 'contextKey': source['context']['contextKey'],
                  'resultSource': 'model-backend', 'lineage': lineage, 'importProvenance': provenance,
                  'gridCount': len(source['gridIds']), 'parameters': request['parameters'], 'productionEligible': False}
        status = {'taskId': task_id, 'runType': 'stateImport', 'runTypeLabel': '状态导入', 'status': 'running',
                  'progress': 0, 'stageLabel': '登记状态', 'createdAt': now(), 'runConfig': request,
                  'contextKey': source['context']['contextKey'], 'resultSource': 'model-backend', 'lineage': lineage}
        write_json(folder / 'status.json', status)
        for file, value in [('request.json', request), ('source-checkpoint.json', source), ('input-manifest.json', provenance),
                            ('checkpoint.json', checkpoint), ('result.json', result)]:
            write_json(folder / file, value)
        write_json(manager.runtime_root / 'states' / f'{state_id}.json', checkpoint)
        status = manager.update(task_id, status='completed', progress=100, stageLabel='状态导入完成',
                                completedAt=now(), resultVersionId=result['resultVersionId'])
        if name.strip():
            old = manager.states.metadata().get(state_id, {})
            manager.states.update(state_id, {'name': name.strip(), 'favorite': old.get('favorite', False), 'revision': old.get('revision', 0)})
        return {'state': checkpoint, 'task': status, 'reused': False}
