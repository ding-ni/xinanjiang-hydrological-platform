"""Run delivered diagnostic kernels in a separate persistent acceptance runtime."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True)
args = parser.parse_args()
os.environ['XAJ_RUNTIME_DIR'] = str(Path(args.runtime).resolve())
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))
sys.path.insert(0, str(root / 'backend/tests'))
from common import read_json, write_json
from server import TaskManager
from diagnostic_jobs import make_point_engine
from test_unified_backend import calibration, simulation
from xaj_routing.raster_formats import cell_center_xy
import numpy as np

manager = TaskManager()
summary = {'checks': [], 'tasks': {}}

def run(request, label):
    request['requestId'] = f'ACCEPT-{label}-{time.time_ns()}'
    task = manager.submit(request)
    summary['tasks'][label] = task['taskId']
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        status = manager.get(task['taskId'])
        if status['status'] not in {'queued', 'running'}:
            break
        time.sleep(0.3)
    if status['status'] != 'completed':
        raise AssertionError(f"{label}: {status}; log={(manager.root / task['taskId'] / 'task.log').read_text(encoding='utf-8')[-8000:]}")
    result = manager.result(task['taskId'])
    assert result['productionEligible'] is False
    summary['checks'].append(label + ':completed-nonproduction')
    print(label, task['taskId'], 'completed', flush=True)
    return task, result

try:
    source, source_result = run(simulation(), 'independent-simulation')
    control = {'lon': 119.52243074308532, 'lat': 30.099162690369454, 'channelWidthM': 20}
    def query(point, label):
        return run({'runType': 'pointQuery', 'sourceTaskId': source['taskId'], 'point': point}, label)
    _, answer = query(control, 'control-point')
    actual = answer['pointQuery']['series']['flow_cms']
    expected = [row['value'] for row in source_result['hydrograph']['forecast']]
    np.testing.assert_allclose(actual, expected, rtol=1e-7, atol=0.000001)
    summary['controlMaxDifferenceCMS'] = float(np.max(np.abs(np.array(actual) - expected)))
    engine = make_point_engine(manager.products.resolve(), Path(args.runtime) / 'engine-probe', manager.routing_versions.resolve()['parameters'])
    interior = next(reach.cells[len(reach.cells) // 2] for reach in engine.topology_result.reaches if len(reach.cells) > 30 and not reach.reach_id.startswith('R0020'))
    slope = next((int(row), int(col)) for row, col in np.argwhere(engine.mask) if not engine.topology_result.stream_raster[row, col] and engine._nearest_stream((int(row), int(col)))[2] > 150)
    for cell, label in [(interior, 'river-interior'), (slope, 'slope-point')]:
        lon, lat = engine.inverse_transformer.transform(*cell_center_xy(engine.grid, *cell))
        _, answer = query({'lon': lon, 'lat': lat, 'channelWidthM': 20}, label)
        summary[label] = {'point': {'lon': lon, 'lat': lat}, 'quality': answer['pointQuery']['quality'], 'classification': answer['pointQuery']['classification']}
    _, multi = run({'runType': 'diagnosticExperiment', 'mode': 'multiSeed', 'values': [42, 101, 2026], 'baseRequest': calibration()}, 'multiseed')
    _, boundary = run({'runType': 'diagnosticExperiment', 'mode': 'wlmBoundary', 'values': [80, 120, 160], 'baseRequest': calibration()}, 'wlm-boundary')
    assert len(multi['experiment']['runs']) == len(boundary['experiment']['runs']) == 3
    summary['experimentEvaluations'] = {'multiSeed': multi['experiment']['evaluation'], 'wlmBoundary': boundary['experiment']['evaluation']}
    summary['status'] = 'passed'
finally:
    manager.close()
    write_json(Path(args.runtime) / 'diagnostic-acceptance.json', summary)
