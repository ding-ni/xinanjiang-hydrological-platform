"""Build a new portable release directory, without workspaces or dev dependencies."""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


def package(destination):
    destination = Path(destination).resolve()
    if destination.exists() or destination == ROOT or destination in ROOT.parents:
        raise ValueError('发布目标必须为新的目录')
    if not (SETTINGS['frontend'] / 'index.html').is_file():
        raise ValueError('请先构建统一网页')
    web_assets = current_web_assets(SETTINGS['frontend'])
    sources = []
    for base, target in [(SETTINGS['frontend'], 'frontend-demo/app/dist-unified'),
                         (SETTINGS['projects'], 'frontend-demo/app/public/data/projects'),
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
    for source in (ROOT / 'backend').iterdir():
        if source.is_file() and source.suffix in {'.py', '.sql', '.txt', '.md', '.vbs'}:
            sources.append((source, Path('backend') / source.name))
    for name in ('start_unified.ps1', 'install_platform.ps1', 'workspace.ps1', 'configure_workspace.py', 'package_platform.py',
                 'import_mdb_bundle.py', 'import_20261012_candidates.py', 'promote_input_bundle.py'):
        sources.append((ROOT / 'scripts' / name, Path('scripts') / name))
    for name in ('启动统一平台.cmd', '启动20261012升级版.cmd', '安装平台.cmd', '统一系统说明.md', '统一系统验收记录.md',
                 '部署与维护手册.md', '平台实操流程.md', '平台后续开发清单.md', '原后端功能融合清单.md',
                 '20261012后端升级说明.md', '同事方案接入与平台操作说明.md'):
        sources.append((ROOT / name, Path(name)))
    sources.append((ROOT / 'config/scientific_validation_20261012.json', Path('config/scientific_validation_20261012.json')))
    acceptance = ROOT / '开发产物/后端升级验收_20261012'
    # A clean program release carries concise evidence; runtime data is backed up separately.
    for name in ('后端升级实施与验收记录.md', 'backend-tests.json', 'frontend-acceptance.json', 'MDB候选重放.json', '原件与内核来源核验.json'):
        if (acceptance / name).is_file():
            sources.append((acceptance / name, Path('验收证据') / name))
    for name in ('浏览器验收', '迁入候选浏览器复验'):
        if (acceptance / name / 'acceptance.json').is_file():
            sources.append((acceptance / name / 'acceptance.json', Path('验收证据') / name / 'acceptance.json'))
    current_acceptance = ROOT / '开发产物/同事方案完整接入验收_20261012'
    for name in ('backend-tests.json', 'frontend-acceptance.json', '验收汇总.json',
                 '原件与内核来源核验.json', 'frontend-final-label-note.json'):
        if (current_acceptance / name).is_file():
            sources.append((current_acceptance / name, Path('验收证据/1.11.0') / name))
    for section, filename in (('17场原方案重放', 'acceptance.json'), ('平台业务浏览器', '最终验收.json'),
                              ('原方案页面闭环验收', 'acceptance.json')):
        if (current_acceptance / section / filename).is_file():
            sources.append((current_acceptance / section / filename, Path('验收证据/1.11.0') / section / 'acceptance.json'))
    launcher_acceptance = ROOT / '开发产物/启动编码修复验收_20261013'
    if (launcher_acceptance / '启动修复验收.json').is_file():
        sources.append((launcher_acceptance / '启动修复验收.json', Path('验收证据/1.11.1/启动修复验收.json')))
    for section, filename in (('升级入口实际启动服务', 'server-receipt.json'), ('升级入口网页验收', 'acceptance.json'),
                              ('统一入口实际启动服务', 'server-receipt.json'), ('统一入口网页验收', 'acceptance.json')):
        if (launcher_acceptance / section / filename).is_file():
            sources.append((launcher_acceptance / section / filename, Path('验收证据/1.11.1') / section / filename))
    startup_acceptance = ROOT / '开发产物/启动体验与文案验收_20261013'
    if (startup_acceptance / '启动体验与文案验收.json').is_file():
        sources.append((startup_acceptance / '启动体验与文案验收.json', Path('验收证据/1.11.2/启动体验与文案验收.json')))
    for relative in ('升级入口实际启动复验/server-receipt.json', '升级入口网页复验/acceptance.json',
                     '升级入口网页复验/browser-dispatch.json', '升级入口网页复验/main-pages/acceptance.json',
                     '统一入口实际启动服务/server-receipt.json', '统一入口网页验收/acceptance.json',
                     '统一入口网页验收/browser-dispatch.json', '统一入口网页验收/main-pages/acceptance.json'):
        if (startup_acceptance / relative).is_file():
            sources.append((startup_acceptance / relative, Path('验收证据/1.11.2') / relative))
    rain_acceptance = ROOT / '开发产物/平滑降雨显示验收_20261013'
    for relative in ('平滑降雨显示验收.json', '最终实际启动服务/server-receipt.json', '最终浏览器验收/acceptance.json'):
        if (rain_acceptance / relative).is_file():
            sources.append((rain_acceptance / relative, Path('验收证据/1.11.3') / relative))
    data_acceptance = ROOT / '开发产物/雨水情与资料库清晰化验收_20261013'
    for relative in ('清晰化验收.json', '初次实际启动服务/server-receipt.json', '初次浏览器验收/acceptance.json'):
        if (data_acceptance / relative).is_file():
            sources.append((data_acceptance / relative, Path('验收证据/1.11.4') / relative))
    generic_acceptance = ROOT / '开发产物/资料库通用整理验收_20261013'
    for relative in ('整理验收.json', '实际启动服务发布前复验/server-receipt.json', '浏览器发布前复验/acceptance.json',
                     '雨水情衔接实际服务/server-receipt.json', '雨水情衔接验收/acceptance.json'):
        if (generic_acceptance / relative).is_file():
            sources.append((generic_acceptance / relative, Path('验收证据/1.11.5') / relative))
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
                        'projects': 'frontend-demo/app/public/data/projects', 'frontend': 'frontend-demo/app/dist-unified', 'port': 8878})
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
                'frontend-demo/app/dist-unified/index.html', 'backend/server.py'}
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
