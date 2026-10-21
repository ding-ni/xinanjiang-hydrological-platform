"""Replay one installed section query in a new isolated workspace, never in the user's workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Unsafe fixture identifier')
    return value


def prepare(args):
    source = args.installed_root.resolve() / 'workspace'
    target = args.workspace.resolve()
    if target.exists() or target == source or source in target.parents:
        raise ValueError('Acceptance workspace must be new and outside the installed workspace')
    original = source / 'tasks' / identifier(args.query_id) / 'request.json'
    request = read(original)
    if request.get('runType') != 'pointQuery':
        raise ValueError('Choose a section pointQuery task')
    source_id = identifier(request['sourceTaskId'])
    bundle = identifier(request['inputs']['bundleVersionId'])
    registry = source / 'inputs/registry' / (bundle + '.json')
    registration = read(registry)
    paths = [Path('tasks') / source_id,
             Path('inputs/products') / bundle,
             Path('inputs/registry') / (bundle + '.json'),
             Path('input-tasks') / identifier(registration['sourceTaskId']),
             Path('routing-versions') / (identifier(request['routing']['versionId']) + '.json'),
             Path('science-versions') / identifier(request['science']['versionId'])]
    source_request = read(source / 'tasks' / source_id / 'request.json')
    implementation = (source_request.get('lineage') or {}).get('modelImplementationSHA256')
    if implementation and re.fullmatch(r'[0-9a-f]{64}', implementation):
        snapshot = Path('implementations') / implementation
        if (source / snapshot).is_dir():
            paths.append(snapshot)
    target.mkdir(parents=True)
    for relative in paths:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (source / relative).is_dir():
            shutil.copytree(source / relative, destination)
        else:
            shutil.copy2(source / relative, destination)
    shutil.copy2(original, target / 'section-query-request.json')
    marker = {'purpose': 'isolated-packaged-section-acceptance', 'originalQueryId': args.query_id,
              'sourceTaskId': source_id, 'requestSHA256': digest(original),
              'sourceRequestSHA256': digest(source / 'tasks' / source_id / 'request.json')}
    (target / 'section-acceptance-fixture.json').write_text(json.dumps(marker, indent=2), encoding='utf-8')
    print(json.dumps({'prepared': str(target), 'copiedPaths': [str(path) for path in paths]}, ensure_ascii=False))


def verify(args):
    workspace, app = args.workspace.resolve(), args.app_root.resolve()
    marker = read(workspace / 'section-acceptance-fixture.json')
    if marker['purpose'] != 'isolated-packaged-section-acceptance':
        raise ValueError('Refusing to run in a non-fixture workspace')
    executable = app / '南安溪徐畈平台.exe'
    if not executable.is_file() or not (app / 'backend/worker.py').is_file():
        raise FileNotFoundError('Wait for staging to finish before running packaged acceptance')
    request_path = workspace / 'section-query-request.json'
    assert digest(request_path) == marker['requestSHA256']
    environment = {key: value for key, value in os.environ.items() if key not in {'PYTHONHOME', 'PYTHONPATH'}}
    environment.update(XAJ_APP_ROOT=str(app), XAJ_RUNTIME_DIR=str(workspace), XAJ_BUNDLED='1',
                       XAJ_CONFIG=str(app / 'config/platform.json'), PYTHONUTF8='1', MPLBACKEND='Agg')
    reports, first_series = [], None
    for mode in ('cold', 'warm'):
        task = workspace / 'tasks' / ('JOB-section-acceptance-' + mode)
        task.mkdir()
        shutil.copy2(request_path, task / 'request.json')
        started = time.monotonic()
        log_path = task / 'task.log'
        with log_path.open('w', encoding='utf-8') as log:
            process = subprocess.Popen([str(executable), '--xaj-script', str(app / 'backend/worker.py'),
                                        '--task', str(task)], cwd=str(app), env=environment,
                                       stdout=log, stderr=subprocess.STDOUT)
            last_phase = None
            while process.poll() is None:
                if time.monotonic() - started > 600:
                    process.kill()
                    process.wait()
                    raise TimeoutError('Section acceptance exceeded 600 seconds')
                try:
                    progress = read(task / 'progress.json')
                    if progress.get('phase') != last_phase:
                        last_phase = progress.get('phase')
                        print(json.dumps({'mode': mode, 'progress': progress}, ensure_ascii=False), flush=True)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
                time.sleep(1)
        if process.returncode:
            raise RuntimeError(log_path.read_text(encoding='utf-8', errors='replace'))
        result = read(task / 'result.json')
        series = result['pointQuery']['series']
        times, flows = series['time'], series['flow_cms']
        expected = read(request_path)['resultSelection']
        assert times[0][:16] == expected['start'] and times[-1][:16] == expected['end'], times
        count = int((datetime.fromisoformat(expected['end']) - datetime.fromisoformat(expected['start'])).total_seconds() / 3600) + 1
        assert len(times) == len(flows) == count and len(set(times)) == count
        assert all(isinstance(flow, (int, float)) and math.isfinite(flow) and flow >= 0 for flow in flows)
        assert max(flows) > 0
        assert result['preparationCacheHit'] is (mode == 'warm'), result.get('preparationCacheHit')
        if first_series is None:
            first_series = series
        else:
            assert series == first_series, 'Cached preparation changed section hydrograph'
        reports.append({'mode': mode, 'seconds': round(time.monotonic() - started, 2), 'timeSteps': count,
                        'start': times[0], 'end': times[-1], 'peakFlowM3s': max(flows),
                        'preparationCacheHit': result['preparationCacheHit'], 'log': str(log_path)})
    assert digest(workspace / 'tasks' / marker['sourceTaskId'] / 'request.json') == marker['sourceRequestSHA256']
    report = {'verified': True, 'originalQueryId': marker['originalQueryId'],
              'requestSHA256': marker['requestSHA256'], 'sourceConditionsPreserved': True,
              'coldAndWarmHydrographsEqual': True, 'runs': reports}
    (workspace.parent / 'section-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--installed-root', type=Path)
    parser.add_argument('--query-id')
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--app-root', type=Path)
    args = parser.parse_args()
    if args.prepare:
        if not args.installed_root or not args.query_id:
            parser.error('--prepare requires --installed-root and --query-id')
        prepare(args)
    else:
        if not args.app_root:
            parser.error('verification requires --app-root')
        verify(args)


if __name__ == '__main__':
    main()
