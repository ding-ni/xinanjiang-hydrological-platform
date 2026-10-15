"""Isolated acceptance workspace for the merged archive/scheme workflow."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace', required=True)
parser.add_argument('--serve', action='store_true')
parser.add_argument('--port', type=int, default=18898)
parser.add_argument('--extended', action='store_true', help='Also verify named versions and parameter-only frozen-input reuse')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
workspace = Path(args.workspace).resolve()
source = root / 'runtime-20261012'
if not workspace.is_relative_to(root / '开发产物') or workspace.exists():
    raise ValueError('Use a new isolated workspace under 开发产物')
os.environ['XAJ_RUNTIME_DIR'] = str(workspace)
os.environ['PYTHONUTF8'] = '1'
sys.path[:0] = [str(root / 'backend'), str(root / 'backend/tests')]
from common import read_json, write_json
from hydro_database import HydroDatabase
from server import TaskManager, InputTaskManager, PlatformHTTPServer, handler
from input_products import InputProducts
from delivered_schemes import migrate_delivered_schemes, scheme_assets
from hydrological_operations import HydrologicalOperations
from historical_inputs import HistoricalInputs
from platform_config import SETTINGS

database = HydroDatabase(workspace)
with sqlite3.connect((source / 'database/hydrology.sqlite3').as_uri() + '?mode=ro', uri=True) as original, database.connect() as copied:
    original.backup(copied)
for folder in ('inputs', 'input-tasks', 'parameters', 'routing-versions', 'science-versions', 'parameter-schemes'):
    if (source / folder).exists():
        shutil.copytree(source / folder, workspace / folder)
# Only the small lineage receipts are needed for migration. Original numerical
# arrays/results stay in the user workspace and are never changed by acceptance.
for path in (source / 'tasks').glob('*/request.json'):
    target = workspace / 'tasks' / path.parent.name
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target / path.name)
    if (path.parent / 'status.json').is_file():
        shutil.copy2(path.parent / 'status.json', target / 'status.json')
products = InputProducts(runtime_root=workspace)
manager = TaskManager(workspace / 'tasks', products=products)
inputs = InputTaskManager(workspace / 'input-tasks', products=products)
manager.input_tasks = inputs
manager.operations = HydrologicalOperations(manager, inputs)
try:
    issues = migrate_delivered_schemes(manager.schemes, products)
    before = manager.schemes.list()
    migrate_delivered_schemes(manager.schemes, products)
    assert manager.schemes.list() == before
    assets = scheme_assets(manager.schemes)
    assert len([row for row in assets if row['asset_id'].startswith('INPUT-')]) == 4
    archive = HistoricalInputs(database)
    catalog = archive.catalog()
    assert len(catalog['events']) == 226
    process = archive.process(event_id='xf-31070314')
    assert process['quality']['gridStatus'] == 'ready'
    assert process['summary']['rainfallCompleteness'] == 1
    assert all(value is not None for value in process['observationPreview']['river']['flows'])
    report = {'migrationIssues': issues, 'schemes': before['schemes'], 'assets': assets,
              'publicEventCount': len(catalog['events']), 'process2007': {'hours': process['summary']['windowHours'], 'quality': process['quality']}}
    write_json(workspace / 'acceptance.json', report)
    print(json.dumps({'schemes': len(before['schemes']), 'assets': len(assets), 'publicEventCount': 226}, ensure_ascii=True), flush=True)
    if args.serve:
        server = PlatformHTTPServer(('127.0.0.1', args.port), handler(manager, SETTINGS['frontend'], inputs, products))
        print('Acceptance ready http://127.0.0.1:' + str(args.port), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    else:
        from test_unified_backend import simulation
        def finish(operation):
            started = time.monotonic()
            while time.monotonic() - started < 240:
                row = manager.operations.get(operation['taskId'])
                if row['status'] not in {'queued', 'preparing', 'running', 'cancelling'}:
                    if row['status'] != 'completed':
                        raise AssertionError(json.dumps(row, ensure_ascii=True))
                    return manager.operations.result(operation['taskId'])
                time.sleep(.25)
            raise TimeoutError(operation['taskId'])
        model = simulation('2007-03-14T12:00', '2007-03-25T03:00')
        model['period']['warmupDays'] = 7
        model['inputs'].update(bundleVersionId='HISTORICAL_SPATIAL_V1', dataMode='historical-database', stationSourceId='LOCAL_OBSERVATIONS')
        defaults = manager.schemes.history_defaults()
        from station_network import local_station_network
        model['inputs']['stationNetwork'] = local_station_network(database)
        # Exercise the same saved-parameter workflow used by the UI.
        from science_context import freeze_science_selection
        scientific, descriptor = freeze_science_selection(model, workspace)
        import uuid
        parameter_id = 'PARAMSET-' + uuid.uuid4().hex
        write_json(workspace / 'parameter-schemes' / (parameter_id + '.json'), {
            'id': parameter_id, 'name': '2007验收参数', 'createdAt': '2026-10-16T00:00:00Z',
            'context': model['context'], 'contextKey': model['context']['contextKey'],
            'parameters': model['parameters'], 'routing': {'versionId': 'control-split-v2'},
            'science': scientific['science'], 'evaporation': {'mode': 'science-version'}, 'warmupDays': 7,
            'statePolicy': 'independent-warmup', 'initialization': {'strategy': 'FIXED_WARMUP'}})
        started = time.monotonic()
        first = finish(manager.operations.submit({'operation': 'eventSimulation', 'context': model['context'],
            'parameterSchemeId': parameter_id, 'events': ['xf-31070314']}))
        elapsed = time.monotonic() - started
        second = finish(manager.operations.submit({'operation': 'rerun', 'sourceTaskId': first['primaryTaskId']}))
        HydrologicalOperations.assert_same_flow(first['hydrographs']['xf-31070314'], second['hydrograph'])
        def same_state(left_id, right_id):
            left = read_json(manager.root / left_id / 'checkpoint.json')
            right = read_json(manager.root / right_id / 'checkpoint.json')
            for key in ('time', 'gridIds', 'land', 'routing'):
                assert left[key] == right[key], key
        same_state(first['primaryTaskId'], second['primaryTaskId'])
        assert manager.result(first['primaryTaskId'])['hydrograph']['metrics'] == second['hydrograph']['metrics']
        report.update(simulation2007={'taskId': first['primaryTaskId'], 'rerunTaskId': second.get('taskId'), 'elapsedSeconds': elapsed})
        if args.extended:
            from test_unified_backend import calibration
            from model_schemes import input_definition
            from hydro_database import hour
            from unittest.mock import patch
            selected = [row for row in catalog['events'] if row['id'] in {'xf-31070314', 'xf-31070422'}]
            selected.sort(key=lambda row: row['start'])
            request = calibration()
            request['inputs'] = copy.deepcopy(model['inputs'])
            request['inputs']['rainfallMethod'] = 'stationIdw'
            request['inputs']['evaporation'] = {'mode': 'monthly-climatology-assumption'}
            request['period'] = {'mode': 'event', 'warmupDays': 7,
                'events': [{key: row[key] for key in ('id', 'start', 'end')} | {'purpose': purpose}
                           for row, purpose in zip(selected, ('率定', '验证'))]}
            # Raw archive events may legitimately fail the original water-balance
            # QC. Preserve that rejection; use a continuous experiment to check
            # the version/input lifecycle without relaxing any event thresholds.
            request['period'] = {'mode': 'continuous', 'warmupDays': 7,
                'calibrationStart': '2007-03-14T12:00', 'calibrationEnd': '2007-03-15T12:00',
                'validationStart': '2007-03-15T13:00', 'validationEnd': '2007-03-16T13:00'}
            request['calibration'].update(statePolicy='continuous-origin', continuousOrigin='2007-03-07T12:00')
            request['calibration']['optimizer']['seed'] = 20261016
            scientific, _ = freeze_science_selection(request, workspace)
            request['science'] = scientific['science']
            saved = manager.schemes.save({'name': '历史库连续版本验收', 'definition': input_definition(request, 'stationIdw'), 'calculationDraft': request})
            request['scheme'] = {'id': saved['id'], 'revision': saved['revision']}
            result = finish(manager.operations.submit({'operation': 'calibration', 'request': request, 'rainfallMethod': 'stationIdw'}))
            frozen = manager.get(result['primaryTaskId'])['runConfig']
            # Change current archive after a successful calculation. A parameter
            # version must continue to use its first frozen observations.
            with database.connect() as db:
                db.execute("UPDATE observations SET value=value+1 WHERE variable='rain' AND time_hour=?", (hour('2007-03-14T12:00'),))
            changed = copy.deepcopy(request)
            changed['calibration']['initialParameters']['SM'] += 1
            saved2 = manager.schemes.save({'id': saved['id'], 'revision': saved['revision'], 'name': saved['name'],
                'definition': input_definition(changed, 'stationIdw'), 'calculationDraft': changed})
            changed['scheme']['revision'] = saved2['revision']
            with patch('scheme_inputs.local_observations_digest', side_effect=AssertionError('Parameter version reread the archive')):
                modified = finish(manager.operations.submit({'operation': 'calibration', 'request': changed, 'rainfallMethod': 'stationIdw'}))
            modified_frozen = manager.get(modified['primaryTaskId'])['runConfig']
            assert modified_frozen['inputs']['bundleVersionId'] == frozen['inputs']['bundleVersionId']
            assert manager.schemes.get(saved['id'], saved['revision'])['calculationDraft'] == saved['calculationDraft']
            repeated = finish(manager.operations.submit({'operation': 'rerun', 'sourceTaskId': result['primaryTaskId']}))
            assert result['hydrographs'] == repeated['hydrographs']
            assert result['eventMetrics'] == repeated['eventMetrics']
            for original, replay in zip(result['eventStates'], repeated['eventStates']):
                same_state(original['taskId'], replay['taskId'])
            report['versionAcceptance'] = {'schemeId': saved['id'], 'revisions': [saved['revision'], saved2['revision']],
                'inputBundleVersionId': frozen['inputs']['bundleVersionId'], 'parameterOnlyInputReused': True,
                'originalReplayIdentical': True, 'originalTaskId': result['primaryTaskId']}
        write_json(workspace / 'acceptance.json', report)
        print(json.dumps(report['simulation2007'], ensure_ascii=True), flush=True)
finally:
    manager.close()
