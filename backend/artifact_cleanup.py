"""Preview and recycle owned numerical outputs, preserving external consumers.

Generated checkpoints are part of their producer, not independent consumers.
The planner is read-only. Execution rechecks a content-bound preview under the
platform queue locks. Observations, registered inputs and model/GIS files are
never deletion candidates.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from collections import deque
from pathlib import Path

from common import now, read_json, write_json
from input_products import manifest_digest

TASK_ID = re.compile(r'(?:JOB|OP)-[A-Za-z0-9_-]{1,100}\Z')
REFERENCE = re.compile(r'(?<![A-Za-z0-9_-])(?:STATE-JOB|JOB|OP)-[A-Za-z0-9_-]+')
ACTIVE = {'queued', 'running', 'cancelling'}


def references(value):
    if isinstance(value, dict):
        return set().union(*(references(child) for child in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(references(child) for child in value)) if value else set()
    return set(REFERENCE.findall(value)) if isinstance(value, str) else set()


class ArtifactCleanup:
    def __init__(self, manager):
        self.manager = manager
        self.root = Path(manager.runtime_root).resolve()

    def checked(self, path):
        path = Path(path).absolute()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError('拒绝清理工作区以外的路径')
        for part in [path, *path.parents]:
            if part == self.root:
                break
            if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0)
                                     & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError('清理路径含符号链接或目录联接，请先人工核对：' + str(part))
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('清理路径越过工作区边界')
        return path

    def files(self, path):
        path = self.checked(path)
        if not path.exists():
            return []
        if path.is_file():
            return [path]
        found = []
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                self.checked(Path(directory) / name)
            found.extend(Path(directory) / name for name in files)
        return sorted(found)

    def node(self, identifier, path, kind):
        files = self.files(path)
        fingerprints, refs, documents = [], set(), {}
        for file in files:
            info = file.stat()
            row = [file.relative_to(self.root).as_posix(), info.st_size, info.st_mtime_ns]
            if file.suffix.lower() == '.json':
                try:
                    document = read_json(file)
                except (OSError, ValueError) as exc:
                    raise RuntimeError('无法核对引用，未执行清理：' + str(file)) from exc
                refs.update(references(document))
                row.append(manifest_digest(document))
                documents[file.name] = document
            fingerprints.append(row)
        return {'id': identifier, 'path': str(path), 'relativePath': path.relative_to(self.root).as_posix(),
                'kind': kind, 'bytes': sum(row[1] for row in fingerprints), 'fileCount': len(files),
                'fingerprint': manifest_digest(fingerprints), 'refs': refs,
                'status': documents.get('status.json', {}), 'documents': documents}

    def inventory(self):
        nodes = {}
        for root_name, prefix, kind in [('operations', 'OP-', 'operation'), ('tasks', 'JOB-', 'task')]:
            root = self.checked(self.root / root_name)
            if not root.exists():
                continue
            for folder in sorted(root.iterdir()):
                self.checked(folder)
                if not folder.is_dir() or not TASK_ID.fullmatch(folder.name) or not folder.name.startswith(prefix):
                    continue
                node = self.node(folder.name, folder, kind)
                status = node['status']
                if not status or status.get('taskId') != folder.name:
                    raise RuntimeError('任务目录与状态记录不一致，未执行清理：' + str(folder))
                nodes[folder.name] = node
        root = self.checked(self.root / 'states')
        for path in sorted(root.glob('*.json')):
            node = self.node(path.stem, path, 'state')
            document = node['documents'][path.name]
            source = document.get('sourceTaskId')
            # Only the worker's exact, unambiguous generated checkpoint is owned.
            node['owner'] = source if path.stem == 'STATE-' + str(source) else None
            nodes[path.stem] = node
            if node['owner'] in nodes:
                # Keep producer and its generated checkpoint together when an
                # external consumer protects either side of this output pair.
                nodes[node['owner']]['refs'].add(path.stem)
        return nodes

    def database_roots(self, excluded_scheme=None):
        roots = {}
        with self.manager.database.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # task_index is an index of files, not a consumer. Historical/editor
            # configurations ARE consumers, including schemes in platform trash.
            for table in ('schemes', 'model_scheme_revisions', 'model_scheme_editors',
                          'model_scheme_snapshots', 'scheme_input_assets', 'workspace_drafts'):
                if table not in tables:
                    continue
                for record in db.execute('SELECT * FROM "' + table + '"'):
                    row = dict(record)
                    if excluded_scheme and row.get('scheme_id') == excluded_scheme:
                        continue
                    refs = references(list(row.values()))
                    if refs:
                        roots['已保存配置/' + table + '/' + str(row.get('id') or row.get('scheme_id') or row.get('key'))] = refs
            for record in db.execute('SELECT * FROM state_metadata'):
                if record['name'].strip() or record['favorite']:
                    roots['已命名或收藏状态/' + record['state_id']] = {record['state_id']}
            for record in db.execute("SELECT id FROM result_retention WHERE retention='saved'"):
                roots['正式成果/' + record['id']] = {record['id']}
            for record in db.execute("SELECT task_id FROM result_metadata WHERE favorite=1 AND task_id NOT IN (SELECT id FROM result_retention)"):
                roots['升级前收藏成果/' + record['task_id']] = {record['task_id']}
        return roots

    def plan(self, identifiers=None, *, orphaned=False, excluded_scheme=None):
        nodes = self.inventory()
        roots = self.database_roots(excluded_scheme)
        if orphaned:
            identifiers = sorted({node['status'].get('operationId') for node in nodes.values()
                                  if node['kind'] == 'task'
                                  and TASK_ID.fullmatch(str(node['status'].get('operationId', '')))
                                  and str(node['status'].get('operationId')).startswith('OP-')
                                  and node['status']['operationId'] not in nodes
                                  and not (self.root / 'operations' / node['status']['operationId']).exists()})
        identifiers = sorted(set(identifiers or []))
        if any(not isinstance(key, str) or not TASK_ID.fullmatch(key) for key in identifiers):
            raise ValueError('无效的计算记录编号')
        candidates = set(identifiers) & nodes.keys()
        for node in nodes.values():
            if node['kind'] == 'task' and node['status'].get('operationId') in identifiers:
                candidates.add(node['id'])
        for key in identifiers:
            if key.startswith('OP-') and key in nodes:
                for child in nodes[key]['status'].get('childTaskIds', []):
                    if child not in nodes or nodes[child]['kind'] != 'task':
                        continue
                    owner = nodes[child]['status'].get('operationId')
                    if owner and owner != key:
                        raise RuntimeError('子任务属于另一次计算，未执行清理：' + child)
                    candidates.add(child)
        for node in nodes.values():
            if node['kind'] == 'state' and node.get('owner') in candidates:
                candidates.add(node['id'])
        # Outputs outside the group are true external consumers. A state that
        # belongs to the group is NOT a root merely because it names its producer.
        for key, node in nodes.items():
            if key not in candidates:
                roots[node['relativePath']] = {key}
            if node['status'].get('status') in ACTIVE:
                roots['运行中的任务/' + key] = {key}
        for root_name in ('parameters', 'parameter-schemes', 'routing-versions', 'science-versions',
                          'forecast-rain', 'automatic-forecasts', 'section-cache', 'inputs', 'input-tasks'):
            for path in self.files(self.root / root_name):
                if path.suffix.lower() != '.json':
                    continue
                try:
                    refs = references(read_json(path))
                except (ValueError, OSError) as exc:
                    raise RuntimeError('无法核对引用，未执行清理：' + str(path)) from exc
                if refs:
                    roots[path.relative_to(self.root).as_posix()] = refs
        protected = {}
        queue = deque((key, label) for label, refs in sorted(roots.items()) for key in sorted(refs))
        visited = set()
        while queue:
            key, reason = queue.popleft()
            if key in visited or key not in nodes:
                continue
            visited.add(key)
            if key in candidates:
                protected[key] = reason
            queue.extend((child, reason) for child in sorted(nodes[key]['refs']))
        def public(key):
            return {k: nodes[key][k] for k in ('id', 'relativePath', 'kind', 'bytes', 'fileCount', 'fingerprint')}
        rows = [public(key) for key in sorted(candidates - protected.keys())]
        kept = [{**public(key), 'reason': protected[key]} for key in sorted(protected)]
        payload = {'scope': 'orphaned' if orphaned else 'selected', 'identifiers': identifiers,
                   'artifacts': rows, 'protected': kept, 'bytes': sum(row['bytes'] for row in rows),
                   'taskCount': sum(row['kind'] == 'task' for row in rows),
                   'stateCount': sum(row['kind'] == 'state' for row in rows),
                   'operationCount': sum(row['kind'] == 'operation' for row in rows)}
        # Hash every semantic dependency (including protected references), not
        # merely the selected ID. Detect new states/saved parameters after preview.
        payload['inventorySHA256'] = manifest_digest({
            'nodes': {key: node['fingerprint'] for key, node in sorted(nodes.items())},
            'roots': {key: sorted(value) for key, value in sorted(roots.items())}})
        payload['previewSHA256'] = manifest_digest(payload)
        return payload

    def execute(self, plan, recycle):
        if not plan['artifacts']:
            return []
        journal = self.root / 'cleanup-journal' / ('CLEANUP-' + uuid.uuid4().hex + '.json')
        audit = {'createdAt': now(), 'plan': plan, 'moved': [], 'status': 'running'}
        write_json(journal, audit)
        purged = []
        # Keep operation envelopes until all their owned numerical files moved.
        order = {'state': 0, 'task': 1, 'operation': 2}
        try:
            for row in sorted(plan['artifacts'], key=lambda r: (order[r['kind']], r['id'])):
                path = self.checked(self.root / row['relativePath'])
                current = self.node(row['id'], path, row['kind'])
                if current['fingerprint'] != row['fingerprint']:
                    raise RuntimeError('文件在预览后发生变化：' + row['relativePath'])
                recycle(path)
                if path.exists():
                    raise RuntimeError('Windows 回收站操作未移走文件：' + row['relativePath'])
                with self.manager.database.connect() as db:
                    if row['kind'] == 'state':
                        db.execute('DELETE FROM state_metadata WHERE state_id=?', (row['id'],))
                    else:
                        for table, column in [('task_index', 'id'), ('result_metadata', 'task_id'), ('result_retention', 'id')]:
                            db.execute(f'DELETE FROM {table} WHERE {column}=?', (row['id'],))
                        purged.append(row['id'])
                audit['moved'].append(row['relativePath'])
                write_json(journal, audit)
            audit['status'] = 'completed'
        except Exception as exc:
            audit.update(status='partial' if audit['moved'] else 'failed', error=str(exc))
            raise RuntimeError(f'清理未全部完成（已移走 {len(audit["moved"])} 项），详情见 {journal.name}：{exc}') from exc
        finally:
            write_json(journal, audit)
        return sorted(purged)
