"""The launcher must parse Chinese paths in Windows PowerShell's code page."""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_SCRIPT = ROOT / 'backend/platform_config.py'


class LauncherEncodingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / '中文 空格 工作区'
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in {'XAJ_CONFIG', 'XAJ_RUNTIME_DIR'}}
        self.environment.update(XAJ_RUNTIME_DIR=str(self.workspace), PYTHONUTF8='1',
                                PYTHONDONTWRITEBYTECODE='1')

    def test_configuration_stdout_survives_cp936_and_utf8_without_path_changes(self):
        process = subprocess.run([sys.executable, '-B', str(CONFIG_SCRIPT)],
                                 env=self.environment, capture_output=True, timeout=30,
                                 check=True)
        self.assertTrue(process.stdout.isascii())
        decoded = json.loads(process.stdout.decode('cp936'))
        self.assertEqual(decoded, json.loads(process.stdout.decode('utf-8')))
        self.assertEqual(decoded['workspace'], str(self.workspace.resolve()))
        self.assertEqual(decoded['appRoot'], str(ROOT.resolve()))
        self.assertEqual(decoded['model'], str(ROOT / 'backend/vendor/model'))
        self.assertIn(b'\\u', process.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher contract')
    def test_windows_powershell_51_converts_native_utf8_output_with_cp936_console(self):
        powershell = shutil.which('powershell.exe')
        self.assertIsNotNone(powershell)
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        script = '\n'.join([
            "$ErrorActionPreference = 'Stop'",
            "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(936)",
            "$env:PYTHONUTF8 = '1'",
            f"$settingsText = & {quote(sys.executable)} -B {quote(CONFIG_SCRIPT)}",
            "if ($LASTEXITCODE -ne 0) { throw 'Configuration failed' }",
            "$settings = $settingsText | ConvertFrom-Json",
            f"if ($settings.workspace -cne {quote(self.workspace.resolve())}) {{ throw 'Workspace path changed' }}",
            f"if ($settings.appRoot -cne {quote(ROOT.resolve())}) {{ throw 'Installation path changed' }}",
            "Write-Output 'CONFIG_JSON_OK'",
        ])
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        process = subprocess.run([powershell, '-NoProfile', '-NonInteractive',
                                  '-EncodedCommand', encoded], env=self.environment,
                                 capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', errors='replace'))
        self.assertIn(b'CONFIG_JSON_OK', process.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
