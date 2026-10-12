"""Serve a test workspace with durable logs and close the owned process tree.

The generic skill helper was tried first. This Windows adaptation avoids
undrained HTTP log pipes and stops the real venv launcher descendants.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--port', type=int, default=18892)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if listening(args.port):
        raise RuntimeError('Test port already in use; existing process is never replaced')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('A verification command is required')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'XAJ_RUNTIME_DIR': str(Path(args.runtime).resolve()),
                   'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    server_command = [str(ROOT / 'backend/.venv/Scripts/python.exe'), '-B', '-u', str(ROOT / 'backend/server.py'), '--port', str(args.port)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    receipt = {'port': args.port, 'workspace': environment['XAJ_RUNTIME_DIR'], 'command': server_command}
    with (output / 'server-stdout.log').open('w', encoding='utf-8') as stdout, (output / 'server-stderr.log').open('w', encoding='utf-8') as stderr:
        process = subprocess.Popen(server_command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr, creationflags=flags)
        receipt['processId'] = process.pid
        try:
            # The service reserves its port before importing a fresh database.
            # Wait for HTTP readiness, not just a successful TCP connection.
            deadline = time.monotonic() + 600
            ready = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Test service failed; see server logs')
                try:
                    with urlopen(f'http://127.0.0.1:{args.port}/api/model/health', timeout=5) as response:
                        if response.status == 200:
                            ready = True
                            break
                except (HTTPError, URLError, TimeoutError, OSError):
                    pass
                time.sleep(.2)
            if not ready:
                raise RuntimeError('Test service startup timed out')
            print('Test service ready: ' + str(args.port), flush=True)
            result = subprocess.run(command, cwd=ROOT, env=environment, timeout=600)
            receipt['verificationReturnCode'] = result.returncode
            return result.returncode
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    stopped = subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10, creationflags=flags)
                    receipt['stopReturnCode'] = stopped.returncode
                else:
                    process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                raise RuntimeError('Owned test process did not stop; inspect saved process receipt')
            receipt['testPortClosed'] = not listening(args.port)
            (output / 'server-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
