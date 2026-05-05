from __future__ import annotations

import json
import mimetypes
import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .data_sources import DashboardPaths, load_dashboard_snapshot


DASHBOARD_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = DASHBOARD_ROOT / "static"
OVERVIEW_CACHE_TTL_SEC = 1.0


class OverviewSnapshotCache:
    def __init__(self, ttl_sec: float = OVERVIEW_CACHE_TTL_SEC) -> None:
        self.ttl_sec = max(0.0, float(ttl_sec))
        self._expires_at = 0.0
        self._key: tuple[str, str] | None = None
        self._payload: dict | None = None

    def get(self, paths: DashboardPaths) -> dict:
        now = time.monotonic()
        key = (str(paths.bot_root), str(paths.quant_root))
        if self._payload is not None and self._key == key and now < self._expires_at:
            return self._payload
        payload = load_dashboard_snapshot(paths)
        self._payload = payload
        self._key = key
        self._expires_at = now + self.ttl_sec
        return payload


OVERVIEW_CACHE = OverviewSnapshotCache()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "EthDashboard/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/overview":
            self._send_json(OVERVIEW_CACHE.get(DashboardPaths.from_env()))
            return
        if parsed.path in {"", "/"}:
            self._send_file(STATIC_ROOT / "index.html")
            return
        candidate = (STATIC_ROOT / parsed.path.lstrip("/")).resolve()
        if STATIC_ROOT.resolve() not in candidate.parents and candidate != STATIC_ROOT.resolve():
            self.send_error(404)
            return
        self._send_file(candidate)

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store" if path.suffix == ".html" else "max-age=60")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"ETH dashboard: http://{host}:{port}")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the read-only ETH runtime dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
