"""The portable release must follow the current web build, not old chunks."""

import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'package_platform.py'
SPEC = importlib.util.spec_from_file_location('package_platform', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleasePackagingTests(unittest.TestCase):
    def test_support_sources_exclude_iteration_artifacts_and_superseded_docs(self):
        paths = {target.as_posix() for _, target in MODULE.support_sources()}
        self.assertTrue({
            '部署与维护手册.md', '方案与自动率定操作说明.md',
            'docs/automatic-forecast.md', 'docs/workbench.md', 'backend/API.md',
            'backend/INPUT_PRODUCTS_CONTRACT.md', 'backend/SCIENCE_CONTEXT.md',
        }.issubset(paths))
        self.assertFalse(any(
            part in {'开发产物', '历史归档', '交付包', '参考项目', '验收证据'}
            for path in paths for part in Path(path).parts
        ))
        self.assertTrue({
            '统一系统验收记录.md',
            'import_20261012_candidates.py', 'backend/统一融合审计-旧网页与API.md',
        }.isdisjoint(paths))

    def test_current_build_assets_are_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            frontend = Path(folder)
            (frontend / '.vite').mkdir()
            (frontend / 'assets').mkdir()
            for name in ('index-new.js', 'panel-new.js', 'panel-new.css'):
                (frontend / 'assets' / name).write_text(name, encoding='utf-8')
            manifest = {
                'index.html': {'file': 'assets/index-new.js', 'css': ['assets/panel-new.css']},
                '_panel.vue': {'file': 'assets/panel-new.js'},
            }
            (frontend / '.vite' / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.assertEqual(MODULE.current_web_assets(frontend), {
                'assets/index-new.js', 'assets/panel-new.js', 'assets/panel-new.css',
            })

    def test_manifest_cannot_reference_missing_or_outside_files(self):
        with tempfile.TemporaryDirectory() as folder:
            frontend = Path(folder)
            (frontend / '.vite').mkdir()
            for file_name in ('../outside.js', 'assets/missing.js'):
                (frontend / '.vite' / 'manifest.json').write_text(
                    json.dumps({'index.html': {'file': file_name}}), encoding='utf-8',
                )
                with self.assertRaises(ValueError):
                    MODULE.current_web_assets(frontend)

    def test_release_verification_checks_content_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            entries = []
            for name in ('启动统一平台.cmd', '安装平台.cmd', 'config/platform.json',
                         'frontend/dist-unified/index.html', 'backend/server.py'):
                source = destination / name
                source.parent.mkdir(parents=True, exist_ok=True)
                content = name.encode('utf-8')
                source.write_bytes(content)
                entries.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
            manifest = {'files': entries, 'bytes': sum(entry['bytes'] for entry in entries), 'version': 'test'}
            (destination / 'release-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.assertTrue(MODULE.verify(destination)['verified'])
            (destination / 'backend/server.py').write_text('changed', encoding='utf-8')
            with self.assertRaises(ValueError):
                MODULE.verify(destination)
            entries[-1]['path'] = '../outside.py'
            (destination / 'release-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaises(ValueError):
                MODULE.verify(destination)

    def test_release_verification_rejects_iteration_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            (destination / 'release-manifest.json').write_text(json.dumps({
                'files': [{'path': '验收证据/legacy.json', 'sha256': '0' * 64, 'bytes': 1}],
                'bytes': 1,
                'version': 'test',
            }), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '历史验收或本机工作区'):
                MODULE.verify(destination)


if __name__ == '__main__':
    unittest.main()
