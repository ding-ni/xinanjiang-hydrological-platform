"""Register selective vendor upgrades without altering the original baseline."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / '历史归档/统一系统接入基线/20261009-133316/baseline-manifest.json'
MODEL = ROOT / 'backend/vendor/model'
SOURCE = ROOT / ('后端文件/南安溪产流汇流模型_20261012终结版/'
                 '南安溪产流汇流模型_20261012终结版/空间可视化人工调参版/model')
MANIFEST = MODEL / 'upgrade-manifest.json'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest().upper()


def register(reasons=None):
    baseline = json.loads(BASELINE.read_text(encoding='utf-8-sig'))
    original = {row['path']: row for row in baseline['vendor']}
    previous = json.loads(MANIFEST.read_text(encoding='utf-8-sig')) if MANIFEST.is_file() else {}
    prior = {row['path']: row for row in previous.get('files', [])}
    reasons = reasons or {}
    # Generated numerical caches are deliberately excluded from source provenance.
    discovered = {path.relative_to(MODEL).as_posix(): path for path in MODEL.rglob('*')
                  if path.is_file() and path != MANIFEST
                  and '__pycache__' not in path.parts
                  and path.suffix.lower() not in {'.pyc', '.pyo', '.nbc', '.nbi'}}
    rows = []
    missing = []
    for relative in sorted(set(original) | set(discovered)):
        current = discovered.get(relative)
        before = original.get(relative)
        if current is None:
            missing.append(relative)
            continue
        checksum = digest(current)
        if before and checksum == before['sha256'].upper():
            continue
        source = SOURCE / relative
        source_digest = digest(source) if source.is_file() else None
        kind = '20261012_IMPORT' if checksum == source_digest else 'UNIFIED_CORRECTION'
        row = {'path': relative, 'operation': 'MODIFIED' if before else 'ADDED',
               'baselineSha256': before['sha256'].upper() if before else None,
               'sha256': checksum, 'bytes': current.stat().st_size,
               'origin': kind,
               'sourcePath': source.relative_to(ROOT).as_posix() if source.is_file() else None,
               'sourceSha256': source_digest,
               'reason': reasons.get(relative) or prior.get(relative, {}).get('reason')
                         or ('Selective import from 2026-10-12 delivery' if kind == '20261012_IMPORT'
                             else 'Unified integration correction; original baseline retained')}
        rows.append(row)
    if missing:
        raise ValueError('Baseline files must not be removed: ' + ', '.join(missing))
    document = {'schemaVersion': 1, 'upgradeId': 'BACKEND-20261012-SELECTIVE',
                'baselineManifest': BASELINE.relative_to(ROOT).as_posix(),
                'baselineManifestSha256': digest(BASELINE),
                'sourceDeliveryRoot': SOURCE.relative_to(ROOT).as_posix(),
                'policy': 'Unlisted baseline files retain original hashes; listed files use explicit upgraded hashes.',
                'baselineFileCount': len(original), 'changedFileCount': len(rows), 'files': rows}
    MANIFEST.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'manifest': str(MANIFEST), 'baselineFiles': len(original),
            'registeredFiles': len(rows), 'missingFiles': missing}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reason', action='append', default=[], help='relative/path=reviewable reason')
    args = parser.parse_args()
    reasons = dict(value.split('=', 1) for value in args.reason)
    print(json.dumps(register(reasons), ensure_ascii=False, indent=2))
