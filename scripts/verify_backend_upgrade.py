"""Run the complete backend acceptance in a separate workspace and save evidence."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_identity():
    roots = (ROOT / 'backend', ROOT / 'scripts')
    records = []
    for base in roots:
        for path in sorted(base.rglob('*.py')):
            if not set(path.parts).intersection({'.venv', '__pycache__'}):
                records.append((path.relative_to(ROOT).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def verify(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / '开发产物'):
        raise ValueError('Acceptance output must be in the development artifacts directory')
    output.mkdir(parents=True, exist_ok=True)
    before = source_identity()
    environment = {**os.environ, 'XAJ_RUNTIME_DIR': str(output / 'backend-suite-workspace'),
                   'XAJ_TEST_REAL_MODEL': '1', 'XAJ_TEST_REAL_INPUTS': '1',
                   'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'backend/tests', '-v']
    started = datetime.now().isoformat()
    process = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=1200)
    transcript = process.stdout + '\n' + process.stderr
    (output / 'backend-tests.log').write_text(transcript, encoding='utf-8')
    counts = re.search(r'Ran (\d+) tests in ([0-9.]+)s', transcript)
    skipped = re.search(r'skipped=(\d+)', transcript)
    after = source_identity()
    result = {'startedAtLocal': started, 'completedAtLocal': datetime.now().isoformat(),
              'python': sys.executable, 'command': command, 'returnCode': process.returncode,
              'sourceSHA256Before': before, 'sourceSHA256After': after, 'sourceStable': before == after,
              'realModelEnabled': True, 'realInputsEnabled': True,
              'workspace': environment['XAJ_RUNTIME_DIR'],
              'tests': int(counts[1]) if counts else None, 'seconds': float(counts[2]) if counts else None,
              'skipped': int(skipped[1]) if skipped else 0,
              'passed': process.returncode == 0 and before == after}
    (output / 'backend-tests.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['passed']:
        print(transcript[-18000:])
        return 1
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / '开发产物/后端升级验收_20261012'))
    args = parser.parse_args()
    raise SystemExit(verify(args.output))
