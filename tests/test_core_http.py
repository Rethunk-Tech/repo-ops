"""Scheme allowlist on the shared URL opener."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from repo_ops_core.http import UnsupportedScheme, open_url


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = self.headers.get("X-Echo", "").encode()
        self.send_response(200)
        self.send_header("X-Method", "GET")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("X-Method", "HEAD")
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence the per-request stderr line."""


@pytest.fixture
def server_url() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_open_url_passes_headers_and_method(server_url: str) -> None:
    with open_url(server_url, headers={"X-Echo": "hello"}, timeout=5) as resp:
        assert resp.read() == b"hello"
    with open_url(server_url, method="HEAD", timeout=5) as resp:
        assert resp.headers.get("X-Method") == "HEAD"


def test_open_url_refuses_non_http_schemes(tmp_path: Path) -> None:
    """A registry-supplied ``file:`` realm must never become a local read."""
    secret = tmp_path / "secret.txt"
    secret.write_text("token", encoding="utf-8")

    for url in (secret.as_uri(), "ftp://example.com/x", "/etc/hosts"):
        with pytest.raises(UnsupportedScheme):
            open_url(url, timeout=5)
