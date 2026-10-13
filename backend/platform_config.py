"""One installation-relative configuration for serving, computing and backup."""
import json
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = '1.11.3'


def load_settings(root=None, environ=None):
    root = Path(root or APP_ROOT).resolve()
    env = os.environ if environ is None else environ
    path = Path(env.get('XAJ_CONFIG', 'config/platform.json'))
    if not path.is_absolute():
        path = root / path
    defaults = dict(schemaVersion=1, workspace='runtime', model='backend/vendor/model',
                    projects='frontend-demo/app/public/data/projects',
                    frontend='frontend-demo/app/dist-unified', port=8878)
    if path.exists():
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(document, dict) or set(document) - set(defaults):
            raise ValueError('平台配置包含未知字段')
        defaults.update(document)
    elif 'XAJ_CONFIG' in env:
        raise ValueError('指定的平台配置文件不存在')
    if type(defaults['schemaVersion']) is not int or defaults['schemaVersion'] != 1:
        raise ValueError('不支持的平台配置版本')
    if type(defaults['port']) is not int or not 1024 <= defaults['port'] <= 65535:
        raise ValueError('服务端口应为 1024–65535')
    defaults['workspace'] = env.get('XAJ_RUNTIME_DIR') or defaults['workspace']
    for key in ('workspace', 'model', 'projects', 'frontend'):
        value = defaults[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{key} 目录不能为空')
        target = Path(value)
        defaults[key] = (target if target.is_absolute() else root / target).resolve()
    workspace = defaults['workspace']
    if workspace == root or workspace == Path(workspace.anchor):
        raise ValueError('工作区须为独立子目录')
    for key in ('model', 'projects', 'frontend'):
        target = defaults[key]
        if workspace == target or workspace in target.parents or target in workspace.parents:
            raise ValueError(f'工作区与 {key} 目录不能相互包含')
    return {**defaults, 'configFile': path.resolve(), 'appRoot': root, 'version': APP_VERSION}


SETTINGS = load_settings()

if __name__ == '__main__':
    # Windows PowerShell 5.1 may decode native stdout with CP936. ASCII JSON
    # keeps path escapes intact; ConvertFrom-Json restores the Unicode paths.
    print(json.dumps(SETTINGS, default=str, ensure_ascii=True))
