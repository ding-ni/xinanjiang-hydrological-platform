"""Source-bound station networks, persisted with the workspace database."""
import copy
import json
import math
import re
from common import read_json
from hydro_database import HydroDatabase


def observation_adapter(products, parent, source_id):
    adapter = copy.deepcopy(parent)
    if not source_id:
        return adapter
    if source_id != parent['manifest'].get('sourceId'):
        adapter['files'] = [row for row in adapter['files'] if not row['path'].endswith('/station-mapping.json')]
    if source_id == 'LOCAL_OBSERVATIONS':
        adapter['manifest'].pop('sourceId', None)
    else:
        products.source(source_id)
        adapter['manifest']['sourceId'] = source_id
    return adapter


def validate_network(body):
    if not isinstance(body, dict) or set(body) != {'stations', 'flowStation'}:
        raise ValueError('站网须包含雨量站及控制断面站号')
    stations = body['stations']
    if not isinstance(stations, list) or not 1 <= len(stations) <= 32:
        raise ValueError('请选择1至32个雨量站')
    ids = set()
    for row in stations:
        if not isinstance(row, dict) or set(row) != {'id', 'lon', 'lat'}:
            raise ValueError('雨量站须包含站号、经度及纬度')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(row['id'])) or str(row['id']) in ids:
            raise ValueError('雨量站号无效或重复')
        ids.add(str(row['id']))
        for key, low, high in [('lon', -180, 180), ('lat', -90, 90)]:
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('雨量站坐标无效')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(body['flowStation'])):
        raise ValueError('控制断面站号无效')
    return {'stations': [{**row, 'id': str(row['id'])} for row in stations], 'flowStation': str(body['flowStation'])}


def station_network(products, parent, body=None):
    database = HydroDatabase(products.runtime_root)
    source = parent['manifest'].get('sourceId') or 'LOCAL_OBSERVATIONS'
    with database.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS station_networks (source_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        if body is not None:
            body = validate_network(body)
            if source != 'LOCAL_OBSERVATIONS':
                record = products.source(source)
                if record['manifest'].get('sourceType') != 'mdb':
                    available = {str(row['id']) for row in read_json(record['path'])['stations']}
                    if any(row['id'] not in available for row in body['stations']):
                        raise ValueError('所选资料中没有该雨量站')
                    if body['flowStation'] != 'PARENT_INPUT':
                        raise ValueError('该雨量资料配用当前资料的实测流量')
            db.execute('INSERT INTO station_networks VALUES (?,?) ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload',
                       (source, json.dumps(body, ensure_ascii=False, allow_nan=False)))
        saved = db.execute('SELECT payload FROM station_networks WHERE source_id=?', (source,)).fetchone()
    if saved:
        return json.loads(saved['payload'])
    if source != 'LOCAL_OBSERVATIONS':
        record = products.source(source)
        data = read_json(record['path'])
        if record['manifest'].get('sourceType') == 'mdb':
            # Use the immutable mapping delivered with this input, not module constants.
            mapping = next((entry for entry in parent['files'] if entry['path'].replace('\\', '/').endswith('/station-mapping.json')), None)
            if not mapping:
                raise ValueError('该MDB来源尚未配置站网，请在模型与资料中保存站网')
            data = read_json(products._path({key: mapping[key] for key in ('root', 'path')}))
            return validate_network({'stations': [{'id': key, 'lon': value[0], 'lat': value[1]} for key, value in data['coordinates'].items()],
                                     'flowStation': data['flowStation']})
        # Registered station sources already carry their own coordinates. Their
        # outlet flow remains the explicitly frozen parent input's flow file.
        return {'stations': [{key: row[key] for key in ('id', 'lon', 'lat')} for row in data['stations']], 'flowStation': 'PARENT_INPUT'}
    rows = database.stations()
    stations = [{'id': row['id'], 'lon': row['lon'], 'lat': row['lat']} for row in rows
                if row['variable'] == 'rain' and row['lon'] is not None and row['lat'] is not None]
    outlets = [row for row in rows if row['variable'] == 'flow']
    if len(outlets) != 1 or not stations:
        raise ValueError('请在模型与资料中配置雨量站和控制断面')
    return validate_network({'stations': stations, 'flowStation': outlets[0]['id']})
