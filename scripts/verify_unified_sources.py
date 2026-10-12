"""Read-only hash audit of delivery, frozen frontend and unchanged model vendor."""
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
    for key, directory in [('frontend', baseline / 'frontend-demo/app'), ('vendor', ROOT / 'backend/vendor/model')]:
        for row in manifest[key]:
            path = directory / row['path']
            if not path.is_file() or digest(path) != row['sha256'].upper():
                failures.append(str(path))
        checked[key] = len(manifest[key])
    archive = ROOT / '后端文件/南安溪产流汇流模型_20261008.zip'
    archive_digest = digest(archive)
    if archive_digest != manifest['archiveSha256'].upper():
        failures.append('Original delivery ZIP changed')
    extraction = ROOT / '后端文件/南安溪产流汇流模型_20261008'
    with zipfile.ZipFile(archive) as package:
        files = [row for row in package.infolist() if not row.is_dir()]
        for row in files:
            target = extraction / row.filename
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
