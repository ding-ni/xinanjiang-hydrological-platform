"""Select a workspace in the installation config, preserving the previous config."""
import argparse
import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from platform_config import SETTINGS
from workspace_service import WorkspaceLock
from common import write_json


def configure(workspace, port=None):
    # No file movement: only switch the next startup's directory selection.
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError('工作区目录不能为空')
    config = SETTINGS['configFile']
    document = json.loads(config.read_text(encoding='utf-8-sig')) if config.exists() else {'schemaVersion': 1}
    document['workspace'] = workspace
    if port is not None: document['port'] = port
    # Validate without writing a temporary config or altering current data.
    target = Path(workspace)
    if not target.is_absolute(): target = SETTINGS['appRoot'] / target
    target = target.resolve()
    if target == SETTINGS['appRoot'] or target == Path(target.anchor): raise ValueError('请选择独立工作区目录')
    for key in ('model', 'frontend', 'projects'):
        path = SETTINGS[key]
        if target == path or target in path.parents or path in target.parents: raise ValueError('工作区与程序资料目录重叠')
    if port is not None and not 1024 <= port <= 65535: raise ValueError('端口须为 1024–65535')
    with WorkspaceLock(SETTINGS['workspace']):
        if config.exists(): shutil.copy2(config, config.with_name('platform.previous-' + uuid4().hex + '.json'))
        write_json(config, document)
    return {'config': str(config), 'workspace': str(target), 'port': document.get('port', SETTINGS['port'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    print(json.dumps(configure(args.workspace, args.port), ensure_ascii=False))
