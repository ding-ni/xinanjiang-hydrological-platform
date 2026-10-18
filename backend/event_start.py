"""New event runs are independent; only verified frozen history may replay otherwise."""
from __future__ import annotations

from pathlib import Path
from common import read_json, safe_task


def validate_event_start(request, runtime_root):
    if request.get('runType') != 'calibration' or request.get('period', {}).get('mode') != 'event':
        return
    period, settings = request['period'], request.get('calibration', {})
    source_id = request.get('lineage', {}).get('replayOfTaskId')
    if not source_id and settings.get('statePolicy', 'independent-warmup') == 'independent-warmup' and not any(
            key in part for part in (period, settings) for key in ('continuousOrigin', 'initialStateVersionId')):
        return
    if request.get('sourcePlan'):
        from original_calibration_plan import validate_original_request
        validate_original_request(request, runtime_root)
        return
    if source_id and runtime_root:
        folder = safe_task(Path(runtime_root) / 'tasks', source_id)
        if not (folder / 'request.json').is_file() or not (folder / 'status.json').is_file():
            raise ValueError('原条件复算来源不存在')
        original = read_json(folder / 'request.json')
        status = read_json(folder / 'status.json')
        if status.get('status') in {'completed', 'archived'} and all(
                request.get(key) == original.get(key) for key in ('period', 'calibration', 'context', 'science')):
            if all(request.get(key, {}).get(field) == original.get(key, {}).get(field)
                   for key, field in (('inputs', 'bundleVersionId'), ('routing', 'versionId'))):
                return
    raise ValueError('新场次计算须逐场独立预热，不能携带连续起点或父状态；旧条件请从历史计算按原条件复算')
