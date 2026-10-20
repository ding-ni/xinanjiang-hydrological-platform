"""Serve an isolated copy of the local records needed for staff acceptance."""
import argparse
import os
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', required=True)
parser.add_argument('--port', type=int, default=18894)
parser.add_argument('--existing', action='store_true', help='Open saved work without reseeding its records')
parser.add_argument('--forecast-clock', help='Fixed Beijing scheduler hour for isolated forecast acceptance only')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
workspace = Path(args.workspace).resolve()
if workspace == root or not workspace.is_relative_to(root):
    raise ValueError('Acceptance workspace must be a separate project subdirectory')
os.environ['XAJ_RUNTIME_DIR'] = str(workspace)
os.environ['PYTHONUTF8'] = '1'
sys.path.insert(0, str(root / 'backend'))
from common import read_json
from hydro_database import HydroDatabase, hour, encoded
from server import TaskManager, handler, PlatformHTTPServer
from workspace_service import WorkspaceLock, workspace_identity
from platform_config import SETTINGS

with WorkspaceLock(workspace):
    workspace_identity(workspace)
    database = HydroDatabase(workspace)
    if not args.existing:
        archive = root / 'runtime/database/hydrology.sqlite3'
        with sqlite3.connect(archive.as_uri() + '?mode=ro', uri=True) as source, database.connect() as target:
            for name in ('projects', 'stations', 'source_files', 'station_aliases', 'flood_events'):
                rows = source.execute('SELECT * FROM ' + name).fetchall()
                if rows:
                    target.executemany('INSERT OR IGNORE INTO ' + name + ' VALUES (' + ','.join('?' for _ in rows[0]) + ')', rows)
            intervals = [('2017-03-11T00:00', '2017-06-30T23:00'), ('2024-06-24T00:00', '2024-07-06T08:00')]
            stations = source.execute('SELECT project_id,station_id FROM stations').fetchall()
            for project, station in stations:
                for variable in ('rain', 'flow', 'level'):
                    for start, end in intervals:
                        records = source.execute('SELECT * FROM observations WHERE project_id=? AND station_id=? AND variable=? AND time_hour BETWEEN ? AND ?',
                                                 (project, station, variable, hour(start), hour(end))).fetchall()
                        target.executemany('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?)', records)
            target.execute('DELETE FROM observation_coverage')
            target.execute('''INSERT INTO observation_coverage
                SELECT source_id,project_id,station_id,variable,count(*),count(value),
                sum(CASE WHEN value=0 THEN 1 ELSE 0 END),min(time_hour),max(time_hour)
                FROM observations GROUP BY source_id,project_id,station_id,variable''')
    manager = TaskManager(workspace / 'tasks')
    if not args.existing:
        manager.schemes.defaults({'rainfallMethod': 'reuse', 'stationSourceId': None})
    application = handler(manager, SETTINGS['frontend'])
    if args.forecast_clock:
        instant = datetime.fromisoformat(args.forecast_clock)
        manager.automatic_forecasts.clock = lambda: instant
    server = PlatformHTTPServer(('127.0.0.1', args.port), application)
    print('Acceptance server ready http://127.0.0.1:' + str(args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.close()
