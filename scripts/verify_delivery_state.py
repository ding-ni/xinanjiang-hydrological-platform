"""Compare durable release data across service restart and workspace restore."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from platform_config import APP_VERSION


COLLECTIONS = ('tasks', 'input-tasks', 'workspace/drafts', 'database/schemes',
               'states', 'routing-versions', 'parameter-versions', 'forecast-rain',
               'science-versions', 'result-catalog')
ACTIVE = {'queued', 'running', 'cancelling'}


def checksum(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def inspect(base, installation):
    def api(path, body=None):
        request = Request(base.rstrip('/') + '/api/model/' + path,
                          data=None if body is None else json.dumps(body).encode('utf-8'),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=180) as response:
            return json.load(response)

    info = api('workspace')
    directories = {row['id']: Path(row['path']).resolve() for row in info['directories']}
    expected = {'model': 'backend/vendor/model',
                'projects': 'frontend-demo/app/public/data/projects',
                'frontend': 'frontend-demo/app/dist-unified'}
    for key, relative in expected.items():
        assert directories[key] == installation / relative, (key, directories[key])
    collections = {name: api(name) for name in COLLECTIONS}
    for name in ('tasks', 'input-tasks'):
        assert not any(row['status'] in ACTIVE for row in collections[name]['tasks']), name
    results = {}
    for row in collections['tasks']['tasks']:
        if row['status'] in {'completed', 'archived'}:
            results[row['taskId']] = checksum(api('tasks/' + row['taskId'] + '/result'))
    checkpoints = {row['stateVersionId']: checksum(api('states/' + row['stateVersionId'] + '/checkpoint'))
                   for row in collections['states']['states']}
    summary = api('database/summary')
    assert summary['events'] == 226 and summary['sourceFiles'] == 128, summary
    return api, {'version': info['version'], 'workspaceId': info['workspace']['id'],
                 'workspacePath': str(directories['workspace']),
                 'installation': str(installation), 'pythonVersion': info['pythonVersion'],
                 'collections': {name: checksum(value) for name, value in collections.items()},
                 'results': results, 'checkpoints': checkpoints,
                 'taskCount': len(collections['tasks']['tasks']), 'database': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['capture', 'compare'])
    parser.add_argument('--url', required=True)
    parser.add_argument('--installation', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--backup', action='store_true')
    parser.add_argument('--replay-scheme', action='store_true')
    args = parser.parse_args()
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    baseline = Path(args.baseline).resolve()
    report = {'status': 'failed', 'mode': args.mode}
    try:
        api, current = inspect(args.url, Path(args.installation).resolve())
        assert current['version'] == APP_VERSION, current['version']
        assert current['taskCount'] > 0 and current['checkpoints']
        report['current'] = current
        if args.mode == 'capture':
            if baseline.exists():
                raise ValueError('Use a new baseline file')
            if args.backup:
                report['backup'] = api('workspace/backups', {})
            baseline.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            original = json.loads(baseline.read_text(encoding='utf-8'))
            report['equal'] = {key: original[key] == current[key]
                               for key in ('version', 'workspaceId', 'collections', 'results', 'checkpoints')}
            assert all(report['equal'].values()), report['equal']
        if args.replay_scheme:
            schemes = api('database/schemes')['schemes']
            assert schemes, 'No saved calculation scheme'
            scheme = schemes[0]
            original_result = api('tasks/' + scheme['sourceTaskId'] + '/result')
            task = api('database/schemes/' + scheme['id'] + '/run', {})
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                status = api('tasks/' + task['taskId'])
                if status['status'] not in ACTIVE:
                    assert status['status'] == 'completed', status
                    break
                time.sleep(0.5)
            else:
                raise AssertionError('Restored scheme calculation timed out')
            repeated = api('tasks/' + task['taskId'] + '/result')
            assert repeated['hydrograph'] == original_result['hydrograph']
            assert repeated['metrics'] == original_result['metrics']
            assert api('tasks/' + scheme['sourceTaskId'] + '/result') == original_result
            report['schemeReplay'] = {'schemeId': scheme['id'], 'taskId': task['taskId'],
                                      'hydrographSHA256': checksum(repeated['hydrograph']),
                                      'identicalHydrographAndMetrics': True}
        report['status'] = 'passed'
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
