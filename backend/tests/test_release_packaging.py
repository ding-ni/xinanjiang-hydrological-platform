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
                         'frontend-demo/app/dist-unified/index.html', 'backend/server.py'):
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


if __name__ == '__main__':
    unittest.main()
