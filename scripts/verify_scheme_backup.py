"""Restore an acceptance backup into a new workspace and compare saved links."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from workspace_backup import restore_backup, verify_backup


def database_rows(root, table):
    path = Path(root) / 'database/hydrology.sqlite3'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as database:
        return sorted(database.execute('SELECT * FROM ' + table).fetchall())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    original = json.loads(Path(args.report).read_text(encoding='utf-8'))
    assert original['status'] == 'passed'
    backup = Path(original['backup']['path']).resolve()
    manifest = verify_backup(backup)
    restored = restore_backup(backup, args.destination)
    tables = ['model_schemes', 'model_scheme_revisions', 'station_networks',
              'result_metadata', 'workspace_drafts', 'task_index']
    checked = {}
    # Restore already checks every manifest byte; here verify the meaningful SQL
    # relationships as well, without reopening or changing the source workspace.
    for table in tables:
        expected = database_rows(backup, table)
        assert database_rows(restored['path'], table) == expected, table
        checked[table] = len(expected)
    for row in manifest['files']:
        if row['path'].startswith(('tasks/', 'states/')) and row['path'].endswith('.json'):
            assert json.loads((backup / row['path']).read_text(encoding='utf-8')) == json.loads(
                (Path(restored['path']) / row['path']).read_text(encoding='utf-8'))
    receipt = {'status': 'passed', 'restored': restored, 'sqlRows': checked,
               'verifiedFiles': len(manifest['files']), 'verifiedBytes': manifest['bytes']}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
