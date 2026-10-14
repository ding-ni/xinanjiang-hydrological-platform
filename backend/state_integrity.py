"""Validate persisted state handoff against its successfully completed task."""
from __future__ import annotations

import re
import copy
import csv
import hashlib
import json
from common import DEFAULT_INPUT_BUNDLE, ROUTING_VERSION, read_json, safe_task
from science_context import IDENTITY_KEYS, assert_science_compatibility


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def forcing_definition(bundle, products=None):
    """The rain source and rules, excluding evaluation data and window names."""
    manifest = bundle.get('manifest', {})
    if isinstance(manifest.get('forcingDefinition'), dict):
        return copy.deepcopy(manifest['forcingDefinition'])
    processing = manifest.get('processing') or {}
    method = processing.get('rainfallMethod')
    if method == 'reuse' and manifest.get('parentBundleVersionId'):
        if products is None:
            from input_products import InputProducts
            products = InputProducts()
        return forcing_definition(products.resolve(manifest['parentBundleVersionId']), products)
    if method == 'stationIdw':
        network = processing.get('stationNetwork') or {}
        return {'sourceId': (manifest.get('sourceOrigin') or {}).get('sourceId', manifest.get('sourceId')),
                'method': 'HOURLY_VALID_STATION_IDW',
                'idwPower': processing.get('idwPower'),
                'minimumStations': processing.get('minimumStations'),
                'stations': network.get('stations', [])}
    return {'sourceId': manifest.get('sourceId'), 'method': manifest.get('operation', 'fixedBaseline'),
            'sourceOriginType': (manifest.get('sourceOrigin') or {}).get('type'),
            **{key: manifest.get(key) for key in ('forcingValueDtype', 'targetCoordinatePolicy', 'sourceQcPolicy')}}


def forcing_grid_digest(bundle, grids):
    return _digest({'grids': grids,
                    'geometrySHA256': hashlib.sha256(bundle['paths']['geometry'].read_bytes()).hexdigest()})


def forcing_values_digest(times, values, counts):
    """Hash the actual time/grid values at their original numerical precision."""
    import numpy as np
    hasher = hashlib.sha256()
    hasher.update(_digest([time.isoformat() for time in times]).encode())
    for array in (np.ascontiguousarray(values, dtype='<f8'), np.ascontiguousarray(counts, dtype='<i4')):
        hasher.update(str(array.shape).encode())
        hasher.update(array.tobytes())
    return hasher.hexdigest()


def build_state_driving_identity(bundle, grids, times, values, counts, parent=None, products=None):
    """Save the executed trajectory, including warm-up and inherited segments."""
    identity = copy.deepcopy(parent) if parent is not None else {
        'schemaVersion': '1.0', 'gridSHA256': forcing_grid_digest(bundle, grids),
        'sourceSHA256': _digest(forcing_definition(bundle, products)), 'segments': []}
    if (identity.get('gridSHA256') != forcing_grid_digest(bundle, grids)
            or identity.get('sourceSHA256') != _digest(forcing_definition(bundle, products))):
        raise ValueError('续算网格或降雨处理条件与父状态不一致')
    start, end = times[0].isoformat(), times[-1].isoformat()
    if identity['segments']:
        from datetime import datetime, timedelta
        if datetime.fromisoformat(start) != datetime.fromisoformat(identity['end']) + timedelta(hours=1):
            raise ValueError('状态驱动时段未连续衔接')
    identity['segments'].append({'start': start, 'end': end,
                                 'forcingSHA256': forcing_values_digest(times, values, counts)})
    identity.update(start=identity['segments'][0]['start'], end=end)
    return identity


def verify_state_driving_identity(checkpoint, request, products=None):
    """Verify the old trajectory in a new bundle; client assertions are unused."""
    from datetime import datetime, timedelta
    from strict_forcing import load_hourly_forcing
    if products is None:
        from input_products import InputProducts
        products = InputProducts()
    identity = checkpoint.get('drivingIdentity')
    if not isinstance(identity, dict) or identity.get('schemaVersion') != '1.0':
        raise ValueError('父状态缺少逐时驱动记录，请重新预热')
    segments = identity.get('segments')
    if not isinstance(segments, list) or not segments or identity.get('end') != checkpoint.get('time'):
        raise ValueError('父状态驱动时段与状态时刻不一致')
    bundle = products.resolve(request.get('inputs', {}).get('bundleVersionId', DEFAULT_INPUT_BUNDLE))
    if bundle['manifestSHA256'] != request.get('inputs', {}).get('bundleManifestSHA256'):
        raise ValueError('续算资料摘要与登记记录不一致')
    with bundle['paths']['grids'].open(encoding='utf-8-sig', newline='') as stream:
        grids = list(csv.DictReader(stream))
    if (identity.get('gridSHA256') != forcing_grid_digest(bundle, grids)
            or checkpoint.get('gridIds') != [row['unit_id'] for row in grids]):
        raise ValueError('续算网格与父状态不一致')
    if identity.get('sourceSHA256') != _digest(forcing_definition(bundle, products)):
        raise ValueError('续算降雨来源或处理条件与父状态不一致')
    anchor = identity.get('observedFlowAnchor')
    if anchor:
        import pandas as pd
        flow = pd.read_csv(bundle['paths']['flow'])
        hours = pd.to_datetime(flow['time'])
        observed = pd.to_numeric(flow.loc[hours == pd.Timestamp(anchor['time']), 'flow_m3_s'], errors='coerce')
        if len(observed) != 1 or observed.iloc[0] != anchor['flowCms']:
            raise ValueError('父状态使用的起算实测流量已改变，请重新预热')
    previous = None
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or not re.fullmatch(r'[a-f0-9]{64}', str(segment.get('forcingSHA256'))):
            raise ValueError('父状态逐时驱动摘要不完整')
        start, end = datetime.fromisoformat(segment['start']), datetime.fromisoformat(segment['end'])
        if start > end or (previous is not None and start != previous + timedelta(hours=1)):
            raise ValueError('父状态驱动时段不连续')
        if index == 0 and segment['start'] != identity.get('start'):
            raise ValueError('父状态驱动起点不一致')
        times, values, counts, _ = load_hourly_forcing(bundle, grids, start, end)
        if forcing_values_digest(times, values, counts) != segment['forcingSHA256']:
            raise ValueError(f'父状态已演算降雨发生变化：{segment["start"]} 至 {segment["end"]}，请重新预热')
        previous = end
    if segments[-1]['end'] != identity['end']:
        raise ValueError('父状态驱动终点不一致')
    return True


def assert_state_input_compatibility(checkpoint, request, products=None):
    """A new time window may inherit a verified, unchanged dynamic trajectory."""
    lineage = request.get("lineage", {})
    selection = request.get("inputs", {})
    version = selection.get("bundleVersionId", DEFAULT_INPUT_BUNDLE)
    parent_version = checkpoint.get("inputBundleVersionId", DEFAULT_INPUT_BUNDLE)
    same_bundle = version == parent_version
    if not same_bundle:
        verify_state_driving_identity(checkpoint, request, products)
    digest = selection.get("bundleManifestSHA256")
    parent_digest = checkpoint.get("inputBundleManifestSHA256")
    if same_bundle and parent_digest is not None and parent_digest != digest:
        raise ValueError("父状态的输入产品摘要与登记版本不一致")
    if parent_digest is None and parent_version != DEFAULT_INPUT_BUNDLE:
        raise ValueError("父状态缺少输入产品摘要，不能继承未确认的资料版本")
    defaults = {"forcingVersion": "precipitation_qc_20260819",
                "observationVersion": "xufan_outlet_hourly_qc_20260819"}
    for key, default in defaults.items():
        if not same_bundle or key == 'observationVersion' and checkpoint.get('drivingIdentity'):
            continue
        if checkpoint.get(key, default) != lineage.get(key, default):
            raise ValueError("父状态与当前驱动或观测来源不一致；请重新初始化")
    if checkpoint.get("petMode", "MONTHLY_CLIMATOLOGY_ASSUMPTION") != lineage.get("petMode", "MONTHLY_CLIMATOLOGY_ASSUMPTION"):
        raise ValueError("父状态 PET 方案不兼容，不能自动转换")
    if checkpoint.get("routingVersionId", ROUTING_VERSION) != lineage.get("routingVersionId", ROUTING_VERSION):
        raise ValueError("父状态汇流版本不兼容，不能自动转换")
    parent_routing_digest = checkpoint.get("routingManifestSHA256")
    # Legacy compatibility applies only when the entire added bundle identity
    # is absent. An explicit default ID (or a present-but-null digest) is still
    # new-format metadata and cannot be downgraded by deleting routing evidence.
    has_bundle_identity = any(key in checkpoint for key in ("inputBundleVersionId", "inputBundleManifestSHA256"))
    if has_bundle_identity and parent_routing_digest is None:
        raise ValueError("父状态缺少汇流文件摘要；新格式输入版本必须重新初始化")
    if parent_routing_digest is not None and parent_routing_digest != lineage.get("routingManifestSHA256"):
        raise ValueError("父状态的冻结汇流文件摘要不一致，必须重新初始化")
    assert_science_compatibility(checkpoint, request)


def validated_parent_state(state_path, tasks_root, state_version_id):
    if not re.fullmatch(r"STATE-JOB-[A-Za-z0-9_-]+", str(state_version_id)):
        raise ValueError("只能使用后端生成的真实状态版本")
    checkpoint = read_json(state_path)
    source_id = checkpoint.get("sourceTaskId")
    if source_id != state_version_id.removeprefix("STATE-") or checkpoint.get("stateVersionId") != state_version_id:
        raise ValueError("状态版本与来源任务编号不一致")
    folder = safe_task(tasks_root, source_id)
    status = read_json(folder / "status.json")
    if status.get("taskId") != source_id or status.get("status") not in {"completed", "archived"}:
        raise ValueError("父状态来源任务尚未成功完成；失败、取消或运行中状态不能续算")
    result = read_json(folder / "result.json")
    stored = read_json(folder / "checkpoint.json")
    if checkpoint != stored:
        raise ValueError("状态版本与来源任务保存的检查点不一致")
    if status.get('runType') == 'stateImport':
        from state_import import fingerprint
        provenance = checkpoint.get('importProvenance', {})
        source = read_json(folder / 'source-checkpoint.json')
        if (provenance.get('sourceSHA256') != fingerprint(source)
                or checkpoint.get('inputManifestSHA256') != fingerprint(provenance)
                or result.get('importProvenance') != provenance):
            raise ValueError('导入状态与原始文件摘要不一致')
        # Imported values must remain exactly those in the preserved source file.
        for key in ('time', 'context', 'parameters', 'land', 'routing', 'gridIds', 'gridGeometry'):
            if source.get(key) != checkpoint.get(key):
                raise ValueError('导入状态数值与原始文件不一致')
    digest = checkpoint.get("inputManifestSHA256")
    # Older successful jobs recorded contextKey but retain the full frozen
    # request. Verify that authoritative request; do not invent missing context.
    result_context = result.get("context")
    if result_context is None:
        result_context = read_json(folder / "request.json").get("context")
        if not isinstance(result_context, dict) or result.get("contextKey") != result_context.get("contextKey"):
            raise ValueError("旧任务结果与冻结请求上下文不一致")
    if (result.get("taskId") != source_id or result.get("stateVersionId") != state_version_id
            or result.get("resultSource") != "model-backend"
            or result_context != checkpoint.get("context")
            or not digest or result.get("lineage", {}).get("inputManifestSHA256") != digest
            or result.get("stateTime") != checkpoint.get("time")):
        raise ValueError("父状态与成功结果、上下文或冻结输入哈希不一致")
    if checkpoint.get("inputBundleVersionId") is not None:
        for key in ("inputBundleVersionId", "inputBundleManifestSHA256", "forcingVersion", "observationVersion", "routingVersionId", "routingManifestSHA256"):
            if result.get("lineage", {}).get(key) != checkpoint.get(key):
                raise ValueError("父状态的输入产品或汇流版本与成功结果血缘不一致")
    if checkpoint.get('drivingIdentity') is not None:
        driving_record = source.get('drivingIdentity') if status.get('runType') == 'stateImport' else result.get('drivingIdentity')
        if driving_record != checkpoint['drivingIdentity']:
            raise ValueError('父状态逐时驱动记录与成功成果不一致')
    # Reading historical results remains valid. New-format identities must match
    # their own successful result; continuation additionally requires current
    # implementation/science compatibility in assert_state_input_compatibility.
    if any(key in checkpoint for key in IDENTITY_KEYS):
        if any(checkpoint.get(key) != result.get("lineage", {}).get(key) for key in IDENTITY_KEYS):
            raise ValueError("父状态的模型实现或科学配置与成功结果血缘不一致")
    return checkpoint
