"""Replay the imported frozen candidate on its actual registered MDB inputs."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from common import RUNTIME, read_json, write_json
from catalog import case_catalog
from server import TaskManager
from science_context import model_implementation_identity


def verify(output, source_runtime=None, isolated_runtime=None, all_events=False):
    import pandas as pd
    import numpy as np
    current = model_implementation_identity()['modelImplementationSHA256']
    source_root = Path(source_runtime or RUNTIME).resolve()
    candidates = [read_json(path) for path in (source_root / 'parameters').glob('*.json')]
    candidate = next(row for row in candidates if row.get('candidateKind') == 'frozen-calibration'
                     and row.get('requiredSpatialTargetPolicy') == 'LEGACY_CLOSED_RING_VERTEX_MEAN'
                     and not row.get('requiresInputRegistration')
                     and (not all_events or row.get('originalCalibrationPlan'))
                     and (isolated_runtime or row.get('lineage', {}).get('modelImplementationSHA256') == current))
    runtime = Path(isolated_runtime).resolve() if isolated_runtime else RUNTIME
    if isolated_runtime:
        import shutil
        if runtime.exists(): raise ValueError('隔离重放目录须为新目录')
        runtime.mkdir(parents=True)
        for part in ('inputs', 'input-tasks', 'routing-versions', 'science-versions', 'parameters'):
            if (source_root / part).exists(): shutil.copytree(source_root / part, runtime / part)
    manager = TaskManager(runtime / 'tasks')
    try:
        bundle = manager.products.resolve(candidate['requiredInputBundleVersionId'])
        catalog = case_catalog(bundle)['cases']['nananxi-xufan:1km']
        selected = [dict(row) for row in catalog['events'] if all_events or row['id'] in {'xf-31210811', 'xf-31220317'}]
        if not selected or not all_events and len(selected) != 2:
            raise ValueError('Required real flood windows are missing')
        selected.sort(key=lambda row: row['start'])
        for index, row in enumerate(selected):
            row['purpose'] = '率定' if index == 0 else '验证'
        request = {'runType': 'calibration', 'context': candidate['context'],
                   'inputs': {'bundleVersionId': bundle['bundleVersionId'], 'evaporation': {'mode': 'science-version'}},
                   'routing': {'versionId': candidate['routingCandidate']['routingVersionId']},
                   'science': candidate['science'],
                   'period': {'mode': 'event', 'warmupDays': candidate['actualWarmupHours'] / 24,
                              'events': selected, 'eventSet': {'selectedEventIds': [row['id'] for row in selected]}},
                   'calibration': {'evaluationMode': 'manual', 'initialParameters': candidate['bestParameters'],
                                   'statePolicy': candidate['statePolicy'], 'continuousOrigin': candidate['continuousOrigin'],
                                   'optimizer': {'algorithm': 'FIXED_PARAMETERS', 'maxEvaluations': 1, 'populationSize': 4},
                                   'objectives': {'metrics': {'nse': True}, 'weights': {'nse': 1}}}}
        if all_events:
            from original_calibration_plan import build_original_request
            request = build_original_request(candidate['parameterVersionId'], runtime, 'fixed-replay', manager.products)
            selected = request['period']['events']
        task = manager.submit(request)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = manager.get(task['taskId'])
            if status['status'] not in {'queued', 'running'}:
                if status['status'] != 'completed':
                    raise RuntimeError(status.get('errorMessage'))
                break
            time.sleep(.2)
        else:
            raise RuntimeError('Real candidate replay timed out')
        result = manager.result(task['taskId'])
        reference = ROOT / '后端文件/南安溪产流汇流模型_20261012终结版/南安溪产流汇流模型_20261012终结版/最新对比结果/20261011_17场洪水三方案对比(最优）/17场三方案过程数据.csv'
        frame = pd.read_csv(reference, dtype={'flood_code': str})
        frame['time'] = pd.to_datetime(frame['time'])
        comparisons = []
        for row in selected:
            points = result['hydrographs'][row['id']]['forecast']
            actual = pd.Series([point['value'] for point in points], index=pd.to_datetime([point['time'] for point in points]))
            original = frame[frame.flood_code == row['sourceFloodNo']].set_index('time')['our_simulated_m3_s'].reindex(actual.index)
            if original.isna().any():
                raise ValueError('Frozen source reference does not cover the replay')
            difference = float(np.max(np.abs(actual.to_numpy() - original.to_numpy())))
            comparisons.append({'eventId': row['id'], 'hours': len(actual), 'maxDifferenceFromSourceM3s': difference})
        report = {'taskId': task['taskId'], 'inputBundleVersionId': bundle['bundleVersionId'],
                  'sourceCandidate': candidate['parameterVersionId'], 'scientificSelection': result['science'],
                  'continuousOrigin': result['continuousOrigin'], 'actualWarmupHours': result['actualWarmupHours'],
                  'sourceReferenceComparisons': comparisons, 'productionEligible': False,
                  'maximumAllowedDifferenceM3s': 1e-6,
                  'sourceReferenceMatched': all(row['maxDifferenceFromSourceM3s'] <= 1e-6 for row in comparisons),
                  'eventCount': len(selected), 'modelImplementationSHA256': current,
                  'scope': 'REAL_MDB_FIXED_PARAMETERS_ALL_REGISTERED_EVENTS' if all_events else 'REAL_MDB_TWO_WINDOWS_NO_OPTIMIZATION_NOT_NEW_GENERALIZATION_EVIDENCE'}
        write_json(Path(output) / 'MDB候选重放.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['sourceReferenceMatched'] else 1
    finally:
        manager.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / '开发产物/后端升级验收_20261012'))
    parser.add_argument('--source-runtime')
    parser.add_argument('--isolated-runtime')
    parser.add_argument('--all-events', action='store_true')
    args = parser.parse_args()
    raise SystemExit(verify(args.output, args.source_runtime, args.isolated_runtime, args.all_events))
