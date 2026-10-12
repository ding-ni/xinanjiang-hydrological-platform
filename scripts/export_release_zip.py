"""Zip only verified release-manifest entries, excluding runtime Python caches."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from package_platform import verify


def export(release, destination):
    release, destination = Path(release).resolve(), Path(destination).resolve()
    verified = verify(release)
    if destination.exists():
        raise ValueError('压缩包目标已存在，请使用新文件名')
    manifest = json.loads((release / 'release-manifest.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for row in manifest['files']:
            archive.write(release / row['path'], row['path'])
        archive.write(release / 'release-manifest.json', 'release-manifest.json')
    with zipfile.ZipFile(destination) as archive:
        if set(archive.namelist()) != {row['path'] for row in manifest['files']} | {'release-manifest.json'}:
            raise ValueError('压缩包目录与发布清单不同')
        for row in manifest['files']:
            value = archive.read(row['path'])
            if len(value) != row['bytes'] or hashlib.sha256(value).hexdigest() != row['sha256']:
                raise ValueError('压缩包文件校验失败：' + row['path'])
    return {**verified, 'zip': str(destination), 'zipBytes': destination.stat().st_size,
            'zipSHA256': hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.release, args.output), ensure_ascii=False))
