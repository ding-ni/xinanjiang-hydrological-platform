"""Portable local server for the built Xinanjiang frontend demo.

This launcher uses only Python's standard library. It serves the current
frontend build (`app/dist`) by default, with `site` kept as a compatibility
fallback. The whole folder can therefore be moved to another computer without
editing a drive letter or installing Node.js for previewing a built site.
"""

from __future__ import annotations

import argparse
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SpaHandler(SimpleHTTPRequestHandler):
    """Serve static files and fall back to index.html for SPA routes."""

    def do_GET(self) -> None:  # noqa: N802 - required HTTP handler name
        requested = Path(self.directory) / self.path.split("?", 1)[0].lstrip("/")
        if not requested.is_file() and not requested.is_dir():
            self.path = "/index.html"
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="启动新安江水文模型前端演示")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机访问")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    default_root = Path(__file__).resolve().parent
    default_dist = default_root / "app" / "dist"
    compatibility_site = default_root / "site"
    default_site = default_dist if default_dist.is_dir() else compatibility_site
    parser.add_argument("--site", type=Path, default=default_site, help="静态站点目录，默认优先使用 app/dist")
    args = parser.parse_args()

    site = args.site.expanduser().resolve()
    if not site.is_dir():
        raise SystemExit(f"找不到静态站点目录：{site}")

    handler = lambda *handler_args, **handler_kwargs: SpaHandler(  # noqa: E731
        *handler_args, directory=os.fspath(site), **handler_kwargs
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"演示已启动：http://{args.host}:{args.port}/")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n演示已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
