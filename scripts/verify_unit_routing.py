"""Real registered-unit routing, continuation and point-split acceptance."""
import argparse
import copy
import os
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True)
args = parser.parse_args()
runtime = Path(args.runtime).resolve()
os.environ['XAJ_RUNTIME_DIR'] = str(runtime)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'backend'), str(root / 'backend/tests')]

import numpy as np
from common import read_json, write_json
from diagnostic_jobs import make_point_engine
from server import TaskManager
from test_unified_backend import calibration, simulation
from xaj_routing.raster_formats import cell_center_xy

manager = TaskManager()
summary = {'status': 'failed', 'checks': [], 'tasks': {}, 'points': {}}


def check(name):
    summary['checks'].append(name)
    print('PASS', name, flush=True)


def run(request, label):
    request['requestId'] = 'UNIT-ACCEPT-' + label + '-' + str(time.time_ns())
    task = manager.submit(request)
    summary['tasks'][label] = task['taskId']
    print('START', label, task['taskId'], flush=True)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        status = manager.get(task['taskId'])
        if status['status'] not in {'queued', 'running', 'cancelling'}:
            break
        time.sleep(0.5)
    if status['status'] != 'completed':
        if status['status'] in {'queued', 'running', 'cancelling'}:
            manager.cancel(task['taskId'])
        log = (manager.root / task['taskId'] / 'task.log').read_text(encoding='utf-8')
        raise AssertionError(f'{label}: {status}\n{log[-10000:]}')
    result = manager.result(task['taskId'])
    assert result['productionEligible'] is False
    print('DONE', label, flush=True)
    return task, result


def values(result):
    return np.array([row['value'] for row in result['hydrograph']['forecast']])


try:
    reference = manager.routing_versions.resolve()
    bundle = manager.products.resolve()
    units = read_json(bundle['paths']['topology'])['units']
    parameters = copy.deepcopy(reference['parameters'])
    # Distinct test constants ensure unit arrays, not a global scalar, are bound.
    parameters['unit_component_slope_k_hours'] = {
        row['unit_id']: {'RS': 6 + index / 10} for index, row in enumerate(units)}
    parameters['unit_component_slope_k_hours'][units[0]['unit_id']]['RI'] = 30
    version = manager.routing_versions.create({
        'name': 'Acceptance independent unit K',
        'parentVersionId': reference['routingVersionId'], 'parameters': parameters})
    summary['routingVersionId'] = version['routingVersionId']

    def configured(request):
        request['routing'] = {'versionId': version['routingVersionId']}
        return request

    _, baseline = run(simulation(end='2017-06-21T11:00'), 'reference')
    whole_task, whole = run(configured(simulation(end='2017-06-21T11:00')), 'custom-whole')
    difference = float(np.max(np.abs(values(baseline) - values(whole))))
    assert difference > 1e-4, difference
    summary['referenceVsCustomMaxDifferenceCMS'] = difference
    assert whole['lineage']['routingVersionId'] == version['routingVersionId']
    manifest = read_json(manager.root / whole_task['taskId'] / 'input-manifest.json')
    assert 'routing_adapter.py' in manifest['routingImplementation']
    check('registered-distinct-unit-K-changes-real-control-flow')

    first_task, first = run(configured(simulation()), 'custom-first')
    continued = configured(simulation(start='2017-06-20T12:00', end='2017-06-21T11:00'))
    continued.update(runType='stateContinuation')
    continued['period']['initialStateVersionId'] = first['stateVersionId']
    second_task, second = run(continued, 'custom-second')
    combined = np.concatenate([values(first), values(second)])
    np.testing.assert_allclose(values(whole), combined, rtol=1e-12, atol=1e-12)
    whole_state = read_json(manager.root / whole_task['taskId'] / 'checkpoint.json')
    second_state = read_json(manager.root / second_task['taskId'] / 'checkpoint.json')
    for section in ('land', 'routing'):
        for key in whole_state[section]:
            np.testing.assert_allclose(whole_state[section][key], second_state[section][key], rtol=1e-12, atol=1e-12)
    summary['continuationMaxDifferenceCMS'] = float(np.max(np.abs(values(whole) - combined)))
    check('custom-unit-K-whole-and-continuation-states-match')

    control = {'lon': 119.52243074308532, 'lat': 30.099162690369454, 'channelWidthM': 20}
    _, answer = run({'runType': 'pointQuery', 'sourceTaskId': whole_task['taskId'], 'point': control}, 'control-point')
    actual = np.array(answer['pointQuery']['series']['flow_cms'])
    np.testing.assert_allclose(actual, values(whole), rtol=1e-7, atol=1e-6)
    summary['controlPointMaxDifferenceCMS'] = float(np.max(np.abs(actual - values(whole))))
    check('control-point-matches-custom-unit-worker')

    engine = make_point_engine(bundle, runtime / 'engine-probe', parameters)
    probes = [(next(reach.cells[len(reach.cells) // 2] for reach in engine.topology_result.reaches
                    if len(reach.cells) > 30 and not reach.reach_id.startswith('R0020')), 'river-interior')]
    for name in ('R0020_UP', 'R0020_DOWN'):
        reach = engine.reach_by_id[name]
        probes.append((reach.cells[len(reach.cells) // 2], name + '-interior'))
    probes.append((next((int(row), int(col)) for row, col in np.argwhere(engine.mask)
                       if not engine.topology_result.stream_raster[row, col]
                       and engine._nearest_stream((int(row), int(col)))[2] > 150), 'slope-point'))
    for cell, label in probes:
        lon, lat = engine.inverse_transformer.transform(*cell_center_xy(engine.grid, *cell))
        point = {'lon': lon, 'lat': lat, 'channelWidthM': 20}
        task, answer = run({'runType': 'pointQuery', 'sourceTaskId': whole_task['taskId'], 'point': point}, label)
        result = answer['pointQuery']
        assert np.isfinite(result['series']['flow_cms']).all()
        assert len(result['series']['flow_cms']) == len(values(whole))
        if label != 'slope-point':
            assert result['quality']['status'] == 'RERUN_COMPLETED', result
        else:
            assert result['classification']['point_type'] == 'SLOPE'
        summary['points'][label] = {'point': point, 'quality': result['quality'], 'classification': result['classification']}
        check(label + '-registered-parameters-inherited')

    _, calibrated = run(configured(calibration()), 'custom-calibration')
    assert calibrated['optimizer']['evaluations'] == 4
    assert {row['group'] for row in calibrated['eventMetrics']} == {'CALIBRATION', 'VALIDATION', 'INDEPENDENT_TEST'}
    check('custom-unit-K-three-group-real-calibration')

    task, result = run({'runType': 'productionWorkflow', 'baseRequest': configured(calibration()),
                        'wlmUpperBounds': [80, 120], 'seeds': [42, 101, 2026], 'warmupYear': 2015,
                        'yearGroups': {'CALIBRATION': [2016], 'VALIDATION': [2017], 'INDEPENDENT_TEST': [2018]}}, 'custom-workflow')
    workflow = result['workflow']
    assert len(workflow['gate']['checks']) == 12
    assert workflow['gate']['production_approved'] is False
    assert 'PET_PROVENANCE' in workflow['gate']['failed_check_ids']
    assert len(workflow['continuous']['annual']) == 3
    assert workflow['continuous']['state_carry_over'] == {'land': True, 'routing': True}
    assert workflow['continuous']['maximum_abs_internal_balance_error_mm'] < .01
    assert (manager.root / task['taskId'] / 'continuous-balance/continuous_hourly_flow.csv').stat().st_size > 1000
    manager.archive(task['taskId'])
    assert manager.result(task['taskId']) == result
    summary['workflow'] = {'gate': workflow['gate'], 'continuous': workflow['continuous']}
    check('custom-unit-K-four-stage-real-workflow-annual-carryover-12-gates')
    summary['status'] = 'passed'
finally:
    manager.close()
    write_json(runtime / 'unit-routing-acceptance.json', summary)
