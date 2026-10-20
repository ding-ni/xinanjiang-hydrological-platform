"""Disposable same-origin HTTP server for the grid-routing browser smoke test."""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import TaskManager, PlatformHTTPServer, handler


@contextmanager
def running_server(port=0):
    """Own the server and its temporary workspace in the test process."""
    # Load native DLLs on the owning thread before it waits on piped stdin.
    # Windows/Conda can otherwise block the first NumPy import in an HTTP thread.
    import numpy
    import pandas
    import xarray
    import pyproj
    frontend = Path(__file__).resolve().parents[2] / 'frontend/dist-unified'
    if not (frontend / 'index.html').is_file():
        raise RuntimeError('请先构建统一前端：npm run build:unified --prefix frontend')
    with tempfile.TemporaryDirectory(prefix='xaj-grid-routing-') as root:
        manager = TaskManager(Path(root) / 'tasks')
        server = None
        thread = None
        try:
            server = PlatformHTTPServer(('127.0.0.1', port), handler(manager, frontend))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            if server is not None:
                if thread is not None:
                    server.shutdown()
                    thread.join(timeout=5)
                server.server_close()
            manager.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    with running_server(args.port) as address:
        print(address, flush=True)
        try:
            # The parent owns stdin: a newline or pipe EOF closes the fixture.
            sys.stdin.readline()
        except KeyboardInterrupt:
            pass
