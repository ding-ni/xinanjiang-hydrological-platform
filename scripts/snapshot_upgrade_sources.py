"""Create a verified source-only recovery snapshot before an integration.

The operational workspace and original delivery directories are never written.
"""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    destination = ROOT / '历史归档/统一系统迭代快照' / (
        datetime.now().strftime('%Y%m%d-%H%M%S') + '-backend-20261012-source')
    destination.mkdir(parents=True, exist_ok=False)
    files = set()
    for relative in ('backend', 'scripts', 'config', 'frontend-demo/app/src',
                     'frontend-demo/app/tests', 'frontend-demo/app/scripts'):
        for path in (ROOT / relative).rglob('*'):
            if (path.is_file() and path.suffix in {'.py', '.json', '.md', '.ps1', '.vue', '.js', '.mjs', '.css', '.scss'}
                    and not set(path.parts).intersection({'.venv', '__pycache__', 'node_modules'})):
                files.add(path)
    files.update(path for path in ROOT.iterdir() if path.is_file())
    files.update(path for path in (ROOT / 'frontend-demo/app').iterdir() if path.is_file())
    records = []
    for source in sorted(files):
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise RuntimeError('Source snapshot verification failed: ' + str(relative))
        records.append({'path': relative.as_posix(), 'bytes': target.stat().st_size, 'sha256': checksum})
    manifest = {'createdAtLocal': datetime.now().isoformat(), 'scope': 'SOURCE_ONLY',
                'workspaceCopied': False, 'files': records}
    (destination / 'snapshot-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'path': str(destination), 'verifiedSourceFiles': len(records)}


if __name__ == '__main__':
    print(json.dumps(snapshot(), ensure_ascii=False))
