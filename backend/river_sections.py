"""Named river locations; numerical queries stay bound to one saved process."""
import copy
import json
import math
import re
import tempfile
import uuid
from pathlib import Path
from common import MODEL, now, read_json
from input_products import HISTORICAL_SPATIAL_ID, file_sha256, manifest_digest


def spatial_identity(bundle):
    return manifest_digest({**{key: file_sha256(bundle['paths'][key]) for key in ('topology', 'mapping', 'geometry')},
        'dem': file_sha256(MODEL / 'data_interface/spatial/nan_an_dem_utm50_30m_clipped.asc'),
        'control': file_sha256(MODEL / 'data_interface/spatial/xufan_70115800_control_point_snapped_v1.json')})


class RiverSections:
    def __init__(self, manager):
        self.manager = manager
        with manager.database.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS river_sections (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, project_id TEXT NOT NULL,
                definition TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')

    def outlet(self, bundle):
        control = read_json(MODEL / 'data_interface/spatial/xufan_70115800_control_point_snapped_v1.json')
        row = control['snapped']
        return {'id': 'SECTION-XUFAN', 'name': '徐畈（二）', 'project_id': 'nananxi-xufan', 'revision': 0, 'predefined': True,
            'definition': {'clicked': [row['lon'], row['lat']], 'queried': [row['lon'], row['lat']], 'reachId': 'R0020_UP',
                           'distanceM': 0, 'spatialSHA256': spatial_identity(bundle)}}

    def bundle(self, result_id=None, process_id=None):
        if result_id:
            process = self.manager.results.process(result_id, process_id)
            if not process['canQuery']:
                raise ValueError(process['queryUnavailableReason'])
            request = self.manager.get(process['sourceTaskId'])['runConfig']
            return self.manager.products.resolve(request['inputs']['bundleVersionId'])
        return self.manager.products.resolve(HISTORICAL_SPATIAL_ID)

    def list(self, result_id=None, process_id=None):
        bundle = self.bundle(result_id, process_id)
        signature = spatial_identity(bundle)
        with self.manager.database.connect() as db:
            saved = [{**dict(row), 'definition': json.loads(row['definition'])} for row in db.execute('SELECT * FROM river_sections WHERE project_id=? ORDER BY updated_at DESC', ('nananxi-xufan',))]
        return {'sections': [{**row, 'compatible': row['definition']['spatialSHA256'] == signature} for row in [self.outlet(bundle), *saved]]}

    def get(self, section_id, bundle):
        if section_id == 'SECTION-XUFAN':
            return self.outlet(bundle)
        if not re.fullmatch(r'SECTION-[a-f0-9]{32}', str(section_id)):
            raise ValueError('断面编号无效')
        with self.manager.database.connect() as db:
            row = db.execute('SELECT * FROM river_sections WHERE id=?', (section_id,)).fetchone()
        if not row:
            raise KeyError('断面不存在')
        return {**dict(row), 'definition': json.loads(row['definition'])}

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
            raise ValueError('断面名称须为1至80个字符')
        return value.strip()

    def locate(self, body):
        if not isinstance(body, dict) or set(body) not in ({'resultId', 'processId', 'point'}, {'projectId', 'point'}):
            raise ValueError('请选择流域与地图位置')
        if body.get('projectId', 'nananxi-xufan') != 'nananxi-xufan':
            raise ValueError('当前仅支持南安溪断面')
        point = body['point']
        if not isinstance(point, dict) or set(point) != {'lon', 'lat'}:
            raise ValueError('请提供地图经纬度')
        if any(type(point[key]) not in (int, float) or not math.isfinite(point[key]) or not lo <= point[key] <= hi for key, lo, hi in [('lon', -180, 180), ('lat', -90, 90)]):
            raise ValueError('地图经纬度无效')
        bundle = self.bundle(body.get('resultId'), body.get('processId'))
        # Snapping uses the frozen spatial network; routing coefficients do not
        # determine location. No calculation result is required to save a point.
        from common import ROUTING_VERSION
        parameters = self.manager.routing_versions.resolve(ROUTING_VERSION)['parameters']
        from diagnostic_jobs import make_point_engine, locate_river_point
        with tempfile.TemporaryDirectory(prefix='xaj-section-') as folder:
            engine = make_point_engine(bundle, Path(folder), parameters)
            _, _, location, _ = locate_river_point(engine, point)
        return {**location, 'spatialSHA256': spatial_identity(bundle)}

    def create(self, body):
        if not isinstance(body, dict) or set(body) not in ({'name', 'resultId', 'processId', 'point'}, {'name', 'projectId', 'point'}):
            raise ValueError('请填写断面名称并选择地图位置')
        name = self.name(body['name'])
        definition = self.locate({key: value for key, value in body.items() if key != 'name'})
        identifier, instant = 'SECTION-' + uuid.uuid4().hex, now()
        with self.manager.database.connect() as db:
            db.execute('INSERT INTO river_sections VALUES (?,?,?,?,?,?,?)', (identifier, name, 'nananxi-xufan', json.dumps(definition), 1, instant, instant))
        return {'id': identifier, 'name': name, 'project_id': 'nananxi-xufan', 'definition': definition, 'revision': 1, 'compatible': True}

    def rename(self, section_id, body):
        if not isinstance(body, dict) or set(body) != {'name', 'revision'} or type(body['revision']) is not int:
            raise ValueError('请填写断面名称')
        if not re.fullmatch(r'SECTION-[a-f0-9]{32}', str(section_id)):
            raise ValueError('预置出口名称不支持修改')
        name = self.name(body['name'])
        with self.manager.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM river_sections WHERE id=?', (section_id,)).fetchone()
            if not old:
                raise KeyError('断面不存在')
            if old['revision'] != body['revision']:
                raise RuntimeError('断面名称已在其他页面修改，请刷新')
            db.execute('UPDATE river_sections SET name=?,revision=revision+1,updated_at=? WHERE id=?', (name, now(), section_id))
        return {**dict(old), 'definition': json.loads(old['definition']), 'name': name, 'revision': old['revision'] + 1}

    def query(self, body):
        # Selection and enqueue are one operation even when two browser tabs
        # request the same section simultaneously.
        with self.manager.lock:
            return self._query(body)

    def _query(self, body):
        if not isinstance(body, dict) or set(body) - {'resultId', 'processId', 'sectionId', 'requestId'} or not all(body.get(key) for key in ('resultId', 'processId', 'sectionId')):
            raise ValueError('请明确选择成果、洪水和断面')
        selected = self.manager.results.process(body['resultId'], body['processId'])
        bundle = self.bundle(body['resultId'], body['processId'])
        section = self.get(body['sectionId'], bundle)
        if section['definition']['spatialSHA256'] != spatial_identity(bundle):
            raise ValueError('该断面与成果河网不同，请在当前河网上重新定位并另存断面')
        context = {'resultId': body['resultId'], 'processId': body['processId'], 'sectionId': section['id'],
                   'sectionName': section['name'], 'start': selected['start'], 'end': selected['end'],
                   'spatialSHA256': section['definition']['spatialSHA256']}
        if section['id'] == 'SECTION-XUFAN':
            graph = selected['hydrograph']
            rows = [row for row in graph.get('forecast', []) if selected['start'][:16] <= row['time'].replace(' ','T')[:16] <= selected['end'][:16]]
            if not rows or rows[0]['time'].replace(' ','T')[:16] != selected['start'][:16] or rows[-1]['time'].replace(' ','T')[:16] != selected['end'][:16]:
                raise ValueError('保存的出口过程未完整覆盖选定洪水')
            return {'status':'completed', 'cacheHit':True, 'taskId':None, 'result':{
                'pointQuery':{'method':'PERSISTED_CONTROL_HYDROGRAPH','series':{'time':[row['time'] for row in rows],
                    'flow_cms':[row['value'] for row in rows], 'unit':'m3/s'}}, 'selection':context}}
        from common import safe_task
        source=safe_task(self.manager.root,selected['sourceTaskId'])
        files=('request.json','result.json','routing-runoff.npz','routing-evidence.json')
        missing=[name for name in files if not (source/name).is_file()]
        if missing:
            raise ValueError('该成果未保存完整产流过程，无法查询中间断面：'+ '、'.join(missing))
        request=read_json(source/'request.json')
        evidence=read_json(source/'routing-evidence.json')
        from diagnostic_jobs import point_assets, sha
        # No terrain construction or large-array hashing in the HTTP thread.
        if request.get('period',{}).get('initialStateVersionId') or evidence.get('initialRoutingState') is not None:
            raise ValueError('该成果采用续算状态；河道中间断面需独立复算，控制断面可直接查询')
        source_stat={name:[(source/name).stat().st_size,(source/name).stat().st_mtime_ns,(source/name).stat().st_ctime_ns] for name in files}
        from section_cache import query_implementation
        program=query_implementation()
        query_identity=manifest_digest({'selection':{key:value for key,value in context.items() if key!='sectionName'},
            'source':source_stat,'program':program})
        for status in self.manager.list():
            request = status.get('runConfig') or {}
            if request.get('queryIdentity') == query_identity and status.get('status') in {'completed', 'archived', 'queued', 'running'}:
                return {**status, 'cacheHit': status['status'] in {'completed', 'archived'}}
        lon, lat = section['definition']['queried']
        original=read_json(source/'request.json')
        result=read_json(source/'result.json')
        payload={**{key:copy.deepcopy(original[key]) for key in ('inputs','routing','context','lineage')},
            'runType': 'pointQuery', 'requestId': body.get('requestId') or 'SECTION-' + uuid.uuid4().hex,
            'sourceTaskId': selected['sourceTaskId'], 'riverOnly': True,
            'point': {'lon': lon, 'lat': lat, 'channelWidthM': 20}, 'resultSelection': context,
            'queryIdentity':query_identity,'queryImplementationSHA256':program,'sourceResultSHA256':sha(source/'result.json'),
            'sourceRequestSHA256':sha(source/'request.json'),'sourceRoutingEvidence':result.get('routingEvidence'),
            'spatialEvidence':point_assets()}
        if original.get('science'):payload['science']=copy.deepcopy(original['science'])
        return self.manager._enqueue(payload,run_type='pointQuery',label='断面流量查询')

    def query_result(self, task_id):
        status, result = self.manager.get(task_id), copy.deepcopy(self.manager.result(task_id))
        context = (status.get('runConfig') or {}).get('resultSelection')
        if not context:
            raise ValueError('该查询未关联成果与洪水')
        series = result['pointQuery']['series']
        indexes = [i for i, time in enumerate(series['time']) if context['start'][:16] <= time.replace(' ', 'T')[:16] <= context['end'][:16]]
        if not indexes or series['time'][indexes[0]].replace(' ','T')[:16] != context['start'][:16] or series['time'][indexes[-1]].replace(' ','T')[:16] != context['end'][:16]:
            raise ValueError('断面过程未完整覆盖选定洪水，不能扩大时段或补造流量')
        for key in ('time', 'flow_cms'):
            series[key] = [series[key][i] for i in indexes]
        return {**result, 'selection': context}
