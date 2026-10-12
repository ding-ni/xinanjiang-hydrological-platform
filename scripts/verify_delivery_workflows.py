"""Run existing browser acceptance workflows sequentially on one test workspace."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'stages': {}}
    environment = {**os.environ, 'PYTHONUTF8': '1', 'UI_BASE_URL': args.url,
                   'UI_OUTPUT_DIR': str(output / 'grid')}
    stages = [
        ('manual', ['scripts/verify_manual_trials_browser.py', '--url', args.url, '--output', str(output / 'manual')]),
        ('forecast', ['scripts/verify_forecast_browser.py', '--url', args.url, '--output', str(output / 'forecast')]),
        ('routing', ['scripts/verify_routing_calibration_browser.py', '--url', args.url, '--output', str(output / 'routing')]),
        ('grid', ['frontend-demo/app/tests/grid_routing_ui_smoke.py']),
        ('database', ['scripts/verify_database_browser.py', '--url', args.url, '--runtime', str(output / 'database')]),
        ('pages', ['scripts/verify_release_browser.py', '--url', args.url, '--output', str(output / 'pages')]),
    ]
    try:
        for name, command in stages:
            print('START ' + name, flush=True)
            with (output / (name + '.log')).open('w', encoding='utf-8') as log:
                result = subprocess.run([sys.executable, '-u', *command], cwd=root, env=environment,
                                        stdout=log, stderr=subprocess.STDOUT)
            evidence = output / name / ('browser/database-browser.json' if name == 'database' else 'acceptance.json')
            detail = json.loads(evidence.read_text(encoding='utf-8')) if evidence.is_file() else {}
            report['stages'][name] = {'exitCode': result.returncode, 'status': detail.get('status'),
                                     'checks': len(detail.get('checks', [])), 'evidence': str(evidence)}
            if result.returncode or detail.get('status') != 'passed':
                raise RuntimeError('Failed browser workflow: ' + name + '; see ' + str(output / (name + '.log')))
            print('PASS ' + name + ': ' + str(len(detail['checks'])) + ' checks', flush=True)
        report['status'] = 'passed'
    finally:
        (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
