"""Explicit search coordinates around the unchanged delivered optimizer."""
import copy
import math
import sys
from common import MODEL, read_json

SPACE_FILE = MODEL / 'config/parameter_space_runoff_multievent_stage2_daily_source.json'
STORAGE = {'WM', 'WUM', 'WLM', 'B', 'IM'}


def definitions():
    from catalog import PARAMETERS
    rows = []
    for spec in read_json(SPACE_FILE)['parameters']:
        key = spec['name'].split('.')[-1]
        if key == 'K_total_day':
            label, symbol, unit, low, high = '日自由水总出流比例', 'KΣ,d', '1', .30, .90
        elif key == 'groundwater_fraction':
            label, symbol, unit, low, high = '地下径流分配比例', 'fG', '1', .02, .80
        else:
            label, low, high, unit = PARAMETERS[key]
            symbol = key
        rows.append({'key': key, 'label': label, 'symbol': symbol, 'unit': unit,
                     'minimum': spec['minimum'], 'maximum': spec['maximum'], 'limitMin': low, 'limitMax': high,
                     'group': 'storage' if key in STORAGE else 'sources'})
    return rows


def coordinates(params):
    # Use the vendor's own conversion, including its edge-case conventions.
    vendor_code = str(MODEL / 'code')
    if vendor_code not in sys.path:
        sys.path.insert(0, vendor_code)
    from runoff_platform.core.types import hourly_source_separation_to_daily
    daily, fraction = hourly_source_separation_to_daily(params['KI'], params['KG'])
    return {**params, 'K_total_day': float(daily), 'groundwater_fraction': float(fraction)}


def validate_selection(calibration, initial):
    selected = calibration.get('searchParameters')
    if selected is None or calibration.get('evaluationMode') == 'manual':
        return
    defs = {row['key']: row for row in definitions()}
    if not isinstance(selected, dict) or not selected or set(selected) - set(defs):
        raise ValueError('请至少选择一个已登记的率定参数')
    if 'wlmUpperBound' in calibration:
        raise ValueError('WLM边界检验请使用预设参数范围，不与自定搜索范围混用')
    current = coordinates(initial)
    for key, bounds in selected.items():
        if not isinstance(bounds, dict) or set(bounds) != {'minimum', 'maximum'}:
            raise ValueError(f'{key} 需要完整的搜索下限和上限')
        lo, hi = bounds['minimum'], bounds['maximum']
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (lo, hi)):
            raise ValueError(f'{key} 搜索范围须为有限数值')
        if not defs[key]['limitMin'] <= lo < hi <= defs[key]['limitMax']:
            raise ValueError(f'{key} 搜索范围超出允许范围或下限不小于上限')
        if not lo <= current[key] <= hi:
            raise ValueError(f'{key} 当前参数须在搜索范围内')
    if calibration['optimizer'].get('strategy', 'joint') == 'staged':
        if not set(selected).intersection(STORAGE) or not set(selected).difference(STORAGE):
            raise ValueError('分阶段搜索需同时选中蓄水产流和水源划分参数；单组请使用联合搜索')


def resolve_space(calibration):
    specs = copy.deepcopy(read_json(SPACE_FILE)['parameters'])
    if 'wlmUpperBound' in calibration:
        next(row for row in specs if row['name'] == 'runoff.WLM')['maximum'] = calibration['wlmUpperBound']
    selected = calibration.get('searchParameters')
    if selected is not None and calibration.get('evaluationMode') != 'manual':
        specs = [{**row, **selected[row['name'].split('.')[-1]]} for row in specs if row['name'].split('.')[-1] in selected]
    return specs


def search_summary(calibration, initial, best, specs):
    before, after = coordinates(initial), coordinates(best)
    active = {} if calibration.get('evaluationMode') == 'manual' else {row['name'].split('.')[-1]: row for row in specs}
    defs = definitions()
    from catalog import PARAMETERS
    defs += [{'key': key, 'label': PARAMETERS[key][0], 'symbol': key, 'unit': PARAMETERS[key][3]} for key in ('KC', 'C')]
    rows = []
    for item in defs:
        key, bound = item['key'], active.get(item['key'])
        rows.append({**{field: item[field] for field in ('key', 'label', 'symbol', 'unit')},
                     'active': bound is not None, 'initial': before[key], 'value': after[key],
                     'minimum': bound['minimum'] if bound else None, 'maximum': bound['maximum'] if bound else None})
    return {'mode': 'manual' if calibration.get('evaluationMode') == 'manual' else 'custom' if calibration.get('searchParameters') is not None else 'default',
            'rows': rows, 'hourlySource': {'KI': best['KI'], 'KG': best['KG']}, 'routing': 'fixed'}
