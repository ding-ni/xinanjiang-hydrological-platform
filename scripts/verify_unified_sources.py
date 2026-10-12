"""Read-only audit of untouched deliveries and explicitly registered upgrades."""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest().upper()


def audit():
    baseline = ROOT / '历史归档/统一系统接入基线/20261009-133316'
    manifest = json.loads((baseline / 'baseline-manifest.json').read_text(encoding='utf-8-sig'))
    failures = []
    checked = {}
    upgraded = ROOT / 'backend/vendor/model/upgrade-manifest.json'
    changes = {}
    if upgraded.is_file():
        upgrade = json.loads(upgraded.read_text(encoding='utf-8-sig'))
        if upgrade.get('schemaVersion') != 1 or upgrade.get('baselineManifestSha256') != digest(baseline / 'baseline-manifest.json'):
            failures.append('Upgrade baseline identity changed')
        originals = {row['path']: row['sha256'].upper() for row in manifest['vendor']}
        for row in upgrade.get('files', []):
            relative = Path(row['path'])
            if relative.is_absolute() or '..' in relative.parts or row['path'] in changes or not row.get('reason'):
                failures.append('Invalid registered upgrade: ' + row['path'])
                continue
            if row.get('baselineSha256') != originals.get(row['path']):
                failures.append('Upgrade original hash changed: ' + row['path'])
            path = ROOT / 'backend/vendor/model' / relative
            if not path.is_file() or digest(path) != row['sha256'] or path.stat().st_size != row['bytes']:
                failures.append('Registered upgrade hash mismatch: ' + row['path'])
            source = row.get('sourcePath')
            if source and (not (ROOT / source).is_file() or digest(ROOT / source) != row['sourceSha256']):
                failures.append('Upgrade source delivery changed: ' + source)
            changes[row['path']] = row
    for key, directory in [('frontend', baseline / 'frontend-demo/app'), ('vendor', ROOT / 'backend/vendor/model')]:
        for row in manifest[key]:
            path = directory / row['path']
            expected = changes[row['path']]['sha256'] if key == 'vendor' and row['path'] in changes else row['sha256'].upper()
            if not path.is_file() or digest(path) != expected:
                failures.append(str(path))
        checked[key] = len(manifest[key])
    allowed = {row['path'] for row in manifest['vendor']} | set(changes) | {'upgrade-manifest.json'}
    for path in (ROOT / 'backend/vendor/model').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix not in {'.pyc', '.pyo', '.nbc', '.nbi'} and path.relative_to(ROOT / 'backend/vendor/model').as_posix() not in allowed:
            failures.append('Unregistered vendor addition: ' + str(path))
    checked['registeredUpgrades'] = len(changes)
    archive = ROOT / '后端文件/南安溪产流汇流模型_20261008.zip'
    archive_digest = digest(archive)
    if archive_digest != manifest['archiveSha256'].upper():
        failures.append('Original delivery ZIP changed')
    extraction = ROOT / '后端文件/南安溪产流汇流模型_20261008'
    with zipfile.ZipFile(archive) as package:
        files = [row for row in package.infolist() if not row.is_dir()]
        for row in files:
            filename = row.filename
            if not row.flag_bits & 0x800:
                try:
                    filename = filename.encode('cp437').decode('gbk')
                except (UnicodeError, LookupError):
                    pass
            target = extraction / filename
            if not target.is_file() or digest(target) != hashlib.sha256(package.read(row)).hexdigest().upper():
                failures.append(str(target))
    checked['deliveryFiles'] = len(files)
    weight_relative = Path('data_interface/spatial/grid_to_unit_weight_control_split_v2.csv')
    original_weight = extraction / '南安溪产流汇流模型_20261008/空间可视化人工调参版/model/validation/integrated_2017_control_split_v2/grid_to_unit_weight.csv'
    vendor_weight = ROOT / 'backend/vendor/model' / weight_relative
    if digest(vendor_weight) != digest(original_weight):
        failures.append('Supplementary routing weights changed')
    return {'checked': checked, 'originalArchiveSHA256': archive_digest, 'supplementaryWeightSHA256': digest(vendor_weight), 'failures': failures}


if __name__ == '__main__':
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(bool(result['failures']))
