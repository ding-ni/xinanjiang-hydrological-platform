"""Three global slope-routing coordinates; delivered equations stay unchanged."""
import copy
import math
from common import MODEL, read_json


def definitions():
    specs = read_json(MODEL / 'config/calibration_routing_2017.json')['parameter_specs']
    labels = {'RS': ('地表径流汇流时间', 'Kₛ'), 'RI': ('壤中流汇流时间', 'Kᵢ'), 'RG': ('地下径流汇流时间', 'K𝗀')}
    return [{'key': component + '_k_hours', 'label': labels[component][0], 'symbol': labels[component][1],
             'unit': 'h', 'minimum': spec['minimum'], 'maximum': spec['maximum'],
             'limitMin': .05, 'limitMax': 720, 'group': 'routing'}
            for spec in specs for component in labels if spec['name'] == 'slope_k.' + component]


def coordinates(payload):
    return {component + '_k_hours': payload['default_slope_k_hours'][component] for component in ('RS', 'RI', 'RG')}


def validate(calibration, payload=None):
    if calibration.get('target', 'runoff') != 'routing':
        if calibration.get('routingSearchParameters') is not None:
            raise ValueError('坡面汇流搜索范围仅用于坡面汇流率定')
        return
    if calibration.get('evaluationMode', 'automatic') != 'automatic':
        raise ValueError('人工试算使用当前产流参数与已选汇流方案')
    if calibration.get('searchParameters') is not None or 'wlmUpperBound' in calibration:
        raise ValueError('坡面汇流率定固定产流参数，不同时搜索产流范围')
    if not isinstance(calibration.get('optimizer'), dict) or calibration['optimizer'].get('strategy', 'joint') != 'joint':
        raise ValueError('坡面汇流参数使用联合搜索')
    rows = {row['key']: row for row in definitions()}
    selected = calibration.get('routingSearchParameters')
    if selected is None:
        selected = {key: {'minimum': row['minimum'], 'maximum': row['maximum']} for key, row in rows.items()}
    if not isinstance(selected, dict) or not selected or set(selected) - set(rows):
        raise ValueError('请至少选择一个坡面汇流参数')
    for key, bounds in selected.items():
        if not isinstance(bounds, dict) or set(bounds) != {'minimum', 'maximum'}:
            raise ValueError(f'{key} 需要完整的搜索下限和上限')
        lo, hi = bounds['minimum'], bounds['maximum']
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (lo, hi)) or not .05 <= lo < hi <= 720:
            raise ValueError(f'{key} 搜索范围须在 0.05–720 小时内且下限小于上限')
        if payload is not None:
            if calibration.get('routingSearchParameters') is not None and not lo <= coordinates(payload)[key] <= hi:
                raise ValueError(f'{key} 当前参数须在搜索范围内')
            units = read_json(MODEL / 'data_interface/spatial/nan_an_topology_control_split_v2.json')['units']
            component = key.split('_')[0]
            if all(component in payload['unit_component_slope_k_hours'].get(row['unit_id'], {}) for row in units):
                raise ValueError(f'{key} 已全部由单元参数覆盖，请在汇流工作台调整单元参数')


def resolve_space(calibration):
    selected = calibration.get('routingSearchParameters')
    return [{'name': 'routing.' + row['key'], **(selected[row['key']] if selected is not None else
             {'minimum': row['minimum'], 'maximum': row['maximum']})}
            for row in definitions() if selected is None or row['key'] in selected]


def candidate_payload(base, updates):
    payload = copy.deepcopy(base)
    for component in ('RS', 'RI', 'RG'):
        key = 'routing.' + component + '_k_hours'
        if key in updates:
            payload['default_slope_k_hours'][component] = float(updates[key])
    return payload


def resolved(payload):
    return {'routing.' + key: value for key, value in coordinates(payload).items()}


def summary(calibration, initial, best, specs):
    active = {row['name'].split('.')[-1]: row for row in specs}
    before, after = coordinates(initial), coordinates(best)
    return {'target': 'routing', 'mode': 'custom' if calibration.get('routingSearchParameters') is not None else 'default',
            'routing': 'calibrated', 'rows': [
                {**{key: row[key] for key in ('key', 'label', 'symbol', 'unit')},
                 'active': row['key'] in active, 'initial': before[row['key']], 'value': after[row['key']],
                 'minimum': active.get(row['key'], {}).get('minimum'), 'maximum': active.get(row['key'], {}).get('maximum')}
                for row in definitions()]}
