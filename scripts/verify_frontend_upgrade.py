"""Save current frontend tests and build diagnostics as acceptance evidence."""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'frontend-demo/app'


def verify(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    reports = {}
    for label, command in (('frontend-tests', ['npm.cmd', 'test']),
                           ('frontend-build', ['npm.cmd', 'run', 'build:unified'])):
        process = subprocess.run(command, cwd=APP, env={**os.environ}, capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', timeout=300)
        transcript = process.stdout + '\n' + process.stderr
        (output / (label + '.log')).write_text(transcript, encoding='utf-8')
        reports[label] = {'command': command, 'returnCode': process.returncode, 'passed': process.returncode == 0}
        if label == 'frontend-tests':
            for line in transcript.splitlines():
                match = re.match(r'(?:#|ℹ) (tests|suites|pass|fail|cancelled|skipped|todo) (\d+)$', line.strip())
                if match:
                    reports[label][match[1]] = int(match[2])
        else:
            manifest = APP / 'dist-unified/.vite/manifest.json'
            if manifest.is_file():
                reports[label]['buildManifestSHA256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if process.returncode:
            print(transcript[-10000:])
            break
    result = {'passed': len(reports) == 2 and all(row['passed'] for row in reports.values()), 'checks': reports}
    (output / 'frontend-acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / '开发产物/后端升级验收_20261012'))
    args = parser.parse_args()
    raise SystemExit(verify(args.output))
