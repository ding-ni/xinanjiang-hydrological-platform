"""Copy verified existing calibration records into an isolated acceptance workspace."""
import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', required=True)
parser.add_argument('--port', type=int, default=18896)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = root / 'runtime-20261012'
workspace = Path(args.workspace).resolve()
if not workspace.is_relative_to(root) or workspace in {root, source}:
    raise ValueError('Use a separate acceptance workspace inside the project')
os.environ['XAJ_RUNTIME_DIR'] = str(workspace)
os.environ['PYTHONUTF8'] = '1'
sys.path.insert(0, str(root / 'backend'))
from common import read_json
from hydro_database import HydroDatabase
from server import TaskManager, handler, PlatformHTTPServer
from platform_config import SETTINGS
from workspace_service import WorkspaceLock, workspace_identity


def copy_file(relative):
    origin, target = (source / relative).resolve(), (workspace / relative).resolve()
    if not origin.is_relative_to(source) or not target.is_relative_to(workspace) or not origin.is_file():
        raise ValueError('Invalid acceptance copy reference')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(origin.read_bytes()).digest():
            raise ValueError('An existing acceptance record differs: ' + str(relative))
    else:
        shutil.copy2(origin, target)


with WorkspaceLock(workspace):
    workspace_identity(workspace)
    database = HydroDatabase(workspace)
    with sqlite3.connect((source / 'database/hydrology.sqlite3').as_uri() + '?mode=ro', uri=True) as original, database.connect() as copied:
        for table in ('projects', 'stations', 'source_files', 'station_aliases', 'flood_events'):
            rows = original.execute('SELECT * FROM ' + table).fetchall()
            if rows:
                copied.executemany('INSERT OR IGNORE INTO ' + table + ' VALUES (' + ','.join('?' for _ in rows[0]) + ')', rows)
    records = ['PARAM-JOB-import-20261012-3ec0d6785b8da64ea30fd172', 'PARAM-JOB-import-20261012-39013d16d86532c839e2ddff']
    for identifier in records:
        relative = Path('parameters') / (identifier + '.json')
        copy_file(relative)
        record = read_json(source / relative)
        for path in (source / 'tasks' / record['taskId']).rglob('*'):
            if path.is_file():
                copy_file(path.relative_to(source))
        for routing in {record['routingCandidate']['routingVersionId'], record['originalCalibrationPlan']['originalRoutingVersionId']}:
            if routing != 'control-split-v2':
                copy_file(Path('routing-versions') / (routing + '.json'))
        science = record['science']['versionId']
        for path in (source / 'science-versions' / science).rglob('*'):
            if path.is_file():
                copy_file(path.relative_to(source))
    bundle = 'INPUT-MDB-4b0ebc43b1144d46a2a85b850e9a0528'
    copy_file(Path('inputs/registry') / (bundle + '.json'))
    producing_task = read_json(source / 'inputs/registry' / (bundle + '.json'))['sourceTaskId']
    for path in (source / 'input-tasks' / producing_task).rglob('*'):
        if path.is_file():
            copy_file(path.relative_to(source))
    manifest_path = Path('inputs/products') / bundle / 'manifest.json'
    copy_file(manifest_path)
    for row in read_json(source / manifest_path)['files']:
        if row['root'] == 'runtime':
            copy_file(Path(row['path']))
    manager = TaskManager(workspace / 'tasks')
    server = PlatformHTTPServer(('127.0.0.1', args.port), handler(manager, SETTINGS['frontend']))
    print('Legacy acceptance ready http://127.0.0.1:' + str(args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.close()
