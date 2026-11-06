"""Serve this repository on 127.0.0.1 with HTTP Range support, for the viewer.

PMTiles archives are read with Range requests, which python's http.server
does not honour. This server does, sends no-cache headers so live/*.geojson
is refetched, and refuses paths outside the repository. With
--screenshots it also accepts POST /__screenshot?name=<file>.png with a
data: URL body and writes it under docs/img/, for the browser check in
docs/log/step5.md. Nothing else is writable.

Usage: uv run python serve.py [--port 8080] [--screenshots]
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import re
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
RANGE = re.compile(r"bytes=(\d*)-(\d*)")
mimetypes.add_type("application/vnd.pmtiles", ".pmtiles")
mimetypes.add_type("application/geo+json", ".geojson")


class Handler(SimpleHTTPRequestHandler):
    screenshots = False

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)  # type: ignore[arg-type]

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self) -> None:
        path = Path(self.translate_path(self.path))
        rng = self.headers.get("Range")
        if rng is None or not path.is_file():
            super().do_GET()
            return
        m = RANGE.match(rng)
        size = path.stat().st_size
        if m is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "bad Range")
            return
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if not self.screenshots or url.path != "/__screenshot":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        name = parse_qs(url.query).get("name", [""])[0]
        if not re.fullmatch(r"[a-z0-9_-]+\.png", name):
            self.send_error(HTTPStatus.BAD_REQUEST, "name must be [a-z0-9_-]+.png")
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        if not body.startswith("data:image/png;base64,"):
            self.send_error(HTTPStatus.BAD_REQUEST, "expected a PNG data URL")
            return
        out = ROOT / "docs" / "img" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(body.split(",", 1)[1]))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"wrote {out.relative_to(ROOT)} {out.stat().st_size} bytes\n".encode())

    def log_message(self, format: str, *args: object) -> None:
        if "/live/" not in str(args[0]) and "/__screenshot" not in str(args[0]):
            super().log_message(format, *args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--screenshots", action="store_true")
    args = ap.parse_args()
    Handler.screenshots = args.screenshots
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving {ROOT} at http://127.0.0.1:{args.port}/viewer/ (screenshots {'on' if args.screenshots else 'off'})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
