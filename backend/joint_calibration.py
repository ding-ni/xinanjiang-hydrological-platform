"""Joint runoff, slope and channel search using the registered model kernels."""
from __future__ import annotations

import copy
import math

from calibration_space import definitions as runoff_definitions, coordinates as runoff_coordinates
from routing_calibration import definitions as slope_definitions
from common import MODEL, read_json


ALL_BOUNDS = {
    'KC': (0, 3), 'C': (0, 1), 'WM': (10, 1000), 'WUM': (1, 150), 'WLM': (1, 600),
    'B': (0, 3), 'SM': (.1, 200), 'EX': (0, 5), 'K_total_day': (0, .999),
    'groundwater_fraction': (0, 1), 'IM': (0, .999), 'RS_k_hours': (.05, 24),
    'RI_k_hours': (.1, 168), 'RG_k_hours': (.1, 720), 'muskingum_k_scale': (.1, 10),
    'muskingum_x': (0, .5),
}


def definitions(profile='reference-16'):
    from catalog import PARAMETERS
    rows = copy.deepcopy(runoff_definitions())
    for key in ('KC', 'C'):
        label, lo, hi, unit = PARAMETERS[key]
        rows.append({'key': key, 'label': label, 'symbol': key, 'unit': unit,
                     'minimum': .4 if key == 'KC' else .05, 'maximum': 1.5 if key == 'KC' else .5,
                     'limitMin': lo, 'limitMax': hi, 'group': 'evaporation'})
    routing = copy.deepcopy(slope_definitions())
    bounds = {'RS_k_hours': (.05, 3), 'RI_k_hours': (.5, 8), 'RG_k_hours': (8, 72)}
    for row in routing:
        row['minimum'], row['maximum'] = bounds[row['key']]
    routing += [
        {'key': 'muskingum_k_scale', 'label': '河段 K 相对所选方案倍数', 'symbol': 'K倍数', 'unit': '1',
         'minimum': .5, 'maximum': 2, 'limitMin': .05, 'limitMax': 10, 'group': 'channel'},
        {'key': 'muskingum_x', 'label': '各河段统一 Muskingum X', 'symbol': 'X', 'unit': '1',
         'minimum': .05, 'maximum': .45, 'limitMin': 0, 'limitMax': .5, 'group': 'channel'},
    ]
    result = {'runoff': rows, 'routing': routing}
    if profile == 'all-physical-16':
        for items in result.values():
            for row in items:
                row['minimum'], row['maximum'] = ALL_BOUNDS[row['key']]
                row['limitMin'], row['limitMax'] = ALL_BOUNDS[row['key']]
    return result


def profiles():
    return [{'id': name, 'label': label, 'definitions': definitions(name)} for name, label in (
        ('reference-16', '常用范围 · 16 个变量'),
        ('fixed-kc-c-14', '固定 KC/C · 14 个变量'),
        ('all-physical-16', '宽范围试验 · 16 个变量'))]


def validate(calibration, runoff=None, routing=None):
    if calibration.get('target') != 'joint':
        return
    if calibration.get('evaluationMode', 'automatic') != 'automatic':
        raise ValueError('联合率定仅用于自动搜索；人工试算使用所选完整方案')
    if not isinstance(calibration.get('optimizer'), dict) or calibration['optimizer'].get('strategy', 'joint') != 'joint':
        raise ValueError('产流、坡面与河道联合率定使用同一搜索阶段')
    profile = calibration.get('jointProfile', 'reference-16')
    if profile not in {'reference-16', 'fixed-kc-c-14', 'all-physical-16'}:
        raise ValueError('联合率定预设未登记')
    if 'wlmUpperBound' in calibration:
        raise ValueError('联合率定请通过参数搜索范围设置 WLM')
    defs = definitions(profile)
    for scope, field in (('runoff', 'searchParameters'), ('routing', 'routingSearchParameters')):
        selected = calibration.get(field)
        allowed = {row['key']: row for row in defs[scope]}
        if selected is None:
            continue
        if not isinstance(selected, dict) or not selected or set(selected) - set(allowed):
            raise ValueError('联合率定须选择已登记的' + scope + '参数')
        if profile == 'fixed-kc-c-14' and scope == 'runoff' and set(selected).intersection({'KC', 'C'}):
            raise ValueError('固定 KC/C 方案不能同时搜索 KC 或 C')
        current = runoff_coordinates(runoff) if runoff is not None and scope == 'runoff' else routing_coordinates(routing) if routing else {}
        for key, bounds in selected.items():
            if not isinstance(bounds, dict) or set(bounds) != {'minimum', 'maximum'}:
                raise ValueError(key + ' 需要完整的搜索下限和上限')
            lo, hi = bounds['minimum'], bounds['maximum']
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lo, hi)):
                raise ValueError(key + ' 搜索范围须为有限数值')
            if not allowed[key]['limitMin'] <= lo < hi <= allowed[key]['limitMax']:
                raise ValueError(key + ' 搜索范围超出登记边界')
            if current.get(key) is not None and not lo <= current[key] <= hi:
                raise ValueError(key + ' 当前参数须在搜索范围内')
    if routing is not None:
        if calibration.get('optimizer', {}).get('initializationPolicy') != 'SOURCE_INITIAL_CANDIDATES':
            validate_routing(routing)
        units = read_json(MODEL / 'data_interface/spatial/nan_an_topology_control_split_v2.json')['units']
        selected = calibration.get('routingSearchParameters')
        for component in ('RS', 'RI', 'RG'):
            key = component + '_k_hours'
            if (selected is None or key in selected) and all(component in routing['unit_component_slope_k_hours'].get(row['unit_id'], {}) for row in units):
                raise ValueError(key + ' 已全部被单元参数覆盖，请调整单元参数或取消该搜索项')


def validate_routing(payload):
    dt = float(payload.get('time_step_hours', 0))
    if dt != 1:
        raise ValueError('联合率定仅支持逐小时汇流')
    for name, row in payload['reach_muskingum'].items():
        k, x = float(row['k_hours']), float(row['x'])
        if not math.isfinite(k + x) or not k > 0 or not 0 <= x <= .5 or not 2 * k * x <= dt <= 2 * k * (1 - x):
            raise ValueError('河段 ' + name + ' 不满足逐小时 Muskingum 非负系数条件 2KX ≤ 1 ≤ 2K(1-X)')


def routing_coordinates(payload):
    xs = {float(row['x']) for row in payload['reach_muskingum'].values()}
    return {**{c + '_k_hours': payload['default_slope_k_hours'][c] for c in ('RS', 'RI', 'RG')},
            'muskingum_k_scale': 1.0, 'muskingum_x': next(iter(xs)) if len(xs) == 1 else None}


def resolve_space(calibration):
    result = []
    for scope, field in (('runoff', 'searchParameters'), ('routing', 'routingSearchParameters')):
        selected = calibration.get(field)
        for row in definitions(calibration.get('jointProfile', 'reference-16'))[scope]:
            key = row['key']
            if scope == 'runoff' and key in {'KC', 'C'} and calibration.get('jointProfile') == 'fixed-kc-c-14':
                continue
            if selected is None or key in selected:
                result.append({'name': scope + '.' + key, **(selected[key] if selected is not None else
                                {'minimum': row['minimum'], 'maximum': row['maximum']})})
    order = calibration.get('searchParameterOrder')
    if order is not None:
        if not isinstance(order, list) or len(order) != len(result) or set(order) != {row['name'] for row in result}:
            raise ValueError('搜索变量顺序与已选参数不一致')
        by_name = {row['name']: row for row in result}
        result = [by_name[name] for name in order]
    return result


def initial_candidate(params, routing):
    return {'runoff': copy.deepcopy(params), 'routing': copy.deepcopy(routing),
            'routingReference': copy.deepcopy(routing), 'kScale': 1.0}


def candidate_payload(base, updates):
    from run_formal_multievent_calibration import candidate_params
    result = copy.deepcopy(base)
    result['runoff'] = candidate_params(result['runoff'], {key: val for key, val in updates.items() if key.startswith('runoff.')})
    for component in ('RS', 'RI', 'RG'):
        key = 'routing.' + component + '_k_hours'
        if key in updates:
            result['routing']['default_slope_k_hours'][component] = float(updates[key])
    if 'routing.muskingum_k_scale' in updates:
        result['kScale'] = float(updates['routing.muskingum_k_scale'])
        for reach, row in result['routing']['reach_muskingum'].items():
            row['k_hours'] = result['routingReference']['reach_muskingum'][reach]['k_hours'] * result['kScale']
    if 'routing.muskingum_x' in updates:
        for row in result['routing']['reach_muskingum'].values():
            row['x'] = float(updates['routing.muskingum_x'])
    validate_routing(result['routing'])
    return result


def resolved(candidate):
    from run_formal_multievent_calibration import resolved_runoff_parameters
    result = resolved_runoff_parameters(candidate['runoff'])
    result.update({'runoff.' + key: val for key, val in runoff_coordinates(
        {key.split('.')[-1]: val for key, val in result.items()}).items()})
    result.update({'routing.' + key: val for key, val in routing_coordinates(candidate['routing']).items()})
    result['routing.muskingum_k_scale'] = candidate['kScale']
    return result


def summary(calibration, initial, best, specs):
    before, after = resolved(initial), resolved(best)
    active = {row['name']: row for row in specs}
    rows = []
    for scope, definitions_in_scope in definitions(calibration.get('jointProfile', 'reference-16')).items():
        for row in definitions_in_scope:
            key = scope + '.' + row['key']
            bound = active.get(key, {})
            rows.append({**{k: row[k] for k in ('key', 'label', 'symbol', 'unit')},
                         'scope': scope, 'active': key in active, 'initial': before[key], 'value': after[key],
                         'minimum': bound.get('minimum'), 'maximum': bound.get('maximum')})
    return {'target': 'joint', 'mode': calibration.get('jointProfile', 'reference-16'),
            'routing': 'calibrated', 'rows': rows, 'routingReferencePolicy': 'K_SCALE_RELATIVE_TO_SELECTED_VERSION',
            'hourlySource': {'KI': best['runoff'].KI, 'KG': best['runoff'].KG}}
