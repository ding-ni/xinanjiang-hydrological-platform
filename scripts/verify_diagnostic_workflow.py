"""Actual four-stage acceptance in a separate runtime; never touches live tasks."""
import argparse
import os
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
os.environ['XAJ_RUNTIME_DIR'] = str(Path(args.runtime).resolve())
sys.path[:0] = [str(root / 'backend'), str(root / 'backend/tests')]
from common import read_json, write_json
from server import TaskManager
from test_unified_backend import calibration

manager = TaskManager()
summary = {'status': 'failed', 'checks': []}
try:
    task = manager.submit({'runType': 'productionWorkflow', 'requestId': f'WORKFLOW-ACCEPT-{time.time_ns()}',
                           'baseRequest': calibration(), 'wlmUpperBounds': [80, 120, 160], 'seeds': [42, 101, 2026],
                           'warmupYear': 2014, 'yearGroups': {'CALIBRATION': [2015, 2016, 2017, 2018], 'VALIDATION': [2019, 2020], 'INDEPENDENT_TEST': [2021]}})
    summary['taskId'] = task['taskId']
    print('Submitted', task['taskId'], flush=True)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        status = manager.get(task['taskId'])
        if status['status'] not in {'queued', 'running'}:
            break
        time.sleep(0.5)
    folder = manager.root / task['taskId']
    if status['status'] != 'completed':
        raise AssertionError(f"{status}\n{(folder / 'task.log').read_text(encoding='utf-8')[-10000:]}")
    result = manager.result(task['taskId'])
    workflow = result['workflow']
    assert result['productionEligible'] is False
    assert workflow['gate']['production_approved'] is False
    assert 'PET_PROVENANCE' in workflow['gate']['failed_check_ids']
    assert len(workflow['gate']['checks']) == 12
    continuous = workflow['continuous']
    assert len(continuous['annual']) == 7
    assert continuous['state_carry_over'] == {'land': True, 'routing': True}
    assert continuous['maximum_abs_internal_balance_error_mm'] < 0.01
    chosen = workflow['boundary']['evaluation']['selected_upper_bound']
    for path in (folder / 'JOB-stability').glob('JOB-*/request.json'):
        assert read_json(path)['calibration']['wlmUpperBound'] == chosen
    nested_ids = {path.parent.name for path in folder.rglob('JOB-*/result.json')}
    registry = list((Path(args.runtime) / 'parameters').glob('*.json'))
    assert all(read_json(path)['taskId'] not in nested_ids for path in registry)
    assert (folder / 'continuous-balance/continuous_hourly_flow.csv').stat().st_size > 1000
    manager.archive(task['taskId'])
    assert manager.result(task['taskId']) == result
    summary.update(status='passed', checks=['four-stages-real-kernels', 'fixed-boundary-in-seeds', 'seven-evaluation-years', 'continuous-land-routing', 'internal-balance', '12-gate-checks', 'assumed-pet-rejected', 'no-nested-parameter-publication', 'hourly-csv', 'archive-readable'], gate=workflow['gate'], continuous=continuous)
    print('PASSED', task['taskId'], summary['checks'], flush=True)
finally:
    manager.close()
    write_json(Path(args.runtime) / 'workflow-acceptance.json', summary)
