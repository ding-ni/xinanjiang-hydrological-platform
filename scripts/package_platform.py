"""Build a new portable release directory, without workspaces or dev dependencies."""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SUFFIXES = {'.py', '.sql', '.txt', '.vbs'}
BACKEND_DOCUMENTS = {'API.md', 'INPUT_PRODUCTS_CONTRACT.md', 'SCIENCE_CONTEXT.md'}
RELEASE_SCRIPTS = (
    'start_unified.ps1', 'install_platform.ps1', 'workspace.ps1',
    'configure_workspace.py', 'package_platform.py', 'import_mdb_bundle.py',
    'promote_input_bundle.py',
)
RELEASE_ROOT_FILES = (
    '启动统一平台.cmd', '安装平台.cmd', '部署与维护手册.md',
    '方案与自动率定操作说明.md', 'docs/automatic-forecast.md',
    'docs/workbench.md',
    'docs/historical-database-schemes.md',
)
FORBIDDEN_RELEASE_PREFIXES = (
    '开发产物/', '历史归档/', '交付包/', '参考项目/', '验收证据/',
    'runtime-staff-acceptance-',
)
FORBIDDEN_RELEASE_FILES = {
    'import_20261012_candidates.py',
    '统一系统验收记录.md', '原后端功能融合清单.md',
}
sys.path.insert(0, str(ROOT / 'backend'))
from platform_config import SETTINGS, APP_VERSION
from common import now, write_json
from workspace_backup import digest


def current_web_assets(frontend):
    manifest_path = frontend / '.vite' / 'manifest.json'
    if not manifest_path.is_file():
        raise ValueError('请先运行 npm run build:unified 生成当前网页清单')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assets = set()
    for item in manifest.values():
        for candidate in [item.get('file'), *item.get('css', []), *item.get('assets', [])]:
            if not candidate:
                continue
            relative = Path(candidate)
            if relative.is_absolute() or '..' in relative.parts or not (frontend / relative).is_file():
                raise ValueError(f'网页构建清单无效：{candidate}')
            assets.add(relative.as_posix())
    if not assets:
        raise ValueError('网页构建清单为空')
    return assets


def support_sources(root=ROOT):
    """Return the maintained runtime, operator docs, and supported tools."""
    root = Path(root)
    sources = []
    for source in (root / 'backend').iterdir():
        if source.is_file() and source.suffix in BACKEND_SUFFIXES | {'.md'}:
            if source.suffix == '.md' and source.name not in BACKEND_DOCUMENTS:
                continue
            sources.append((source, Path('backend') / source.name))
    for name in RELEASE_SCRIPTS:
        sources.append((root / 'scripts' / name, Path('scripts') / name))
    for name in RELEASE_ROOT_FILES:
        sources.append((root / name, Path(name)))
    missing = [str(source) for source, _ in sources if not source.is_file()]
    if missing:
        raise ValueError('发布所需源码或说明缺失：' + ', '.join(missing))
    return sources


def package(destination):
    destination = Path(destination).resolve()
    if destination.exists() or destination == ROOT or destination in ROOT.parents:
        raise ValueError('发布目标必须为新的目录')
    if not (SETTINGS['frontend'] / 'index.html').is_file():
        raise ValueError('请先构建统一网页')
    web_assets = current_web_assets(SETTINGS['frontend'])
    sources = []
    for base, target in [(SETTINGS['frontend'], 'frontend/dist-unified'),
                         (SETTINGS['projects'], 'frontend/public/data/projects'),
                         (SETTINGS['model'], 'backend/vendor/model')]:
        if destination.is_relative_to(base): raise ValueError('发布目标不能位于资料目录中')
        for source in base.rglob('*'):
            if source.is_symlink(): raise ValueError('发布资料包含外部链接')
            if source.is_file() and '__pycache__' not in source.parts and source.suffix not in {'.pyc', '.log'}:
                relative = source.relative_to(base)
                if base == SETTINGS['frontend']:
                    if relative.parts[:1] == ('.vite',):
                        continue
                    if relative.parts[:1] == ('assets',) and relative.as_posix() not in web_assets:
                        continue
                sources.append((source, Path(target) / source.relative_to(base)))
    sources.extend(support_sources())
    records = []
    destination.mkdir(parents=True)
    for source, relative in sources:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(source) != digest(target): raise RuntimeError('发布复制检查失败')
        records.append({'path': relative.as_posix(), 'sha256': digest(target), 'bytes': target.stat().st_size})
    config = destination / 'config/platform.json'
    write_json(config, {'schemaVersion': 1, 'workspace': 'workspace', 'model': 'backend/vendor/model',
                        'projects': 'frontend/public/data/projects', 'frontend': 'frontend/dist-unified', 'port': 8878})
    records.append({'path': 'config/platform.json', 'sha256': digest(config), 'bytes': config.stat().st_size})
    manifest = {'version': APP_VERSION, 'createdAt': now(), 'files': records,
                'bytes': sum(row['bytes'] for row in records), 'python': '3.9', 'platform': 'Windows x64'}
    write_json(destination / 'release-manifest.json', manifest)
    return {'path': str(destination), 'files': len(records), 'bytes': manifest['bytes'], 'version': APP_VERSION}


def verify(destination):
    destination = Path(destination).resolve()
    manifest_path = destination / 'release-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    records = manifest['files']
    if not isinstance(records, list) or not records:
        raise ValueError('发布清单为空')
    seen = set()
    total = 0
    for record in records:
        name = record['path']
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or name in seen or '\\' in name:
            raise ValueError(f'发布清单路径无效：{name}')
        if name in FORBIDDEN_RELEASE_FILES or name.startswith(FORBIDDEN_RELEASE_PREFIXES):
            raise ValueError(f'发布包不能包含历史验收或本机工作区内容：{name}')
        seen.add(name)
        source = destination / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'发布文件缺失：{name}')
        size = source.stat().st_size
        if not re.fullmatch(r'[0-9a-f]{64}', record['sha256']) or size != record['bytes'] or digest(source) != record['sha256']:
            raise ValueError(f'发布文件校验失败：{name}')
        total += size
    if total != manifest['bytes']:
        raise ValueError('发布总字节数不符')
    expected = {'启动统一平台.cmd', '安装平台.cmd', 'config/platform.json',
                'frontend/dist-unified/index.html', 'backend/server.py'}
    if not expected.issubset(seen):
        raise ValueError('发布目录缺少必要入口')
    return {'path': str(destination), 'files': len(records), 'bytes': total, 'version': manifest['version'], 'verified': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--destination')
    mode.add_argument('--verify')
    args = parser.parse_args()
    print(json.dumps(verify(args.verify) if args.verify else package(args.destination), ensure_ascii=False))
