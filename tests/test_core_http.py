"""Scheme allowlist on the shared URL opener."""

from __future__ import annotations

import contextlib
import threading
import urllib.parse
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
    threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


class _RedirectHandler(BaseHTTPRequestHandler):
    """Answers ``?to=`` with a redirect to it; any other path echoes the auth seen."""

    seen_auth: list[str | None] = []

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        target = query.get("to", [""])[0]
        if target:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        type(self).seen_auth.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence the per-request stderr line."""


@contextlib.contextmanager
def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def redirect_servers() -> Iterator[tuple[str, str, type[_RedirectHandler]]]:
    """A redirecting origin and a separate receiving origin (a different port)."""
    handler = type("_RecordingRedirectHandler", (_RedirectHandler,), {"seen_auth": []})
    with contextlib.ExitStack() as stack:
        source = stack.enter_context(_serve(handler))
        target = stack.enter_context(_serve(handler))
        yield source, target, handler


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


def test_open_url_allowed_host_requires_https_and_pin(server_url: str) -> None:
    with pytest.raises(UnsupportedScheme):
        open_url(server_url, timeout=5, allowed_host="127.0.0.1")
    with pytest.raises(UnsupportedScheme):
        open_url("https://evil.example/token", timeout=5, allowed_host="quay.io")


def test_open_url_drops_authorization_on_cross_origin_redirect(
    redirect_servers: tuple[str, str, type],
) -> None:
    """A registry redirect to another origin must not receive the bearer/basic credential."""
    source, target, handler = redirect_servers
    with open_url(
        f"{source}?to={urllib.parse.quote(target + 'token', safe='')}",
        headers={"Authorization": "Basic c2VjcmV0"},
        timeout=5,
    ) as resp:
        resp.read()
    assert handler.seen_auth == [None]


def test_open_url_keeps_authorization_on_same_origin_redirect(
    redirect_servers: tuple[str, str, type],
) -> None:
    source, _target, handler = redirect_servers
    with open_url(
        f"{source}?to={urllib.parse.quote(source + 'final', safe='')}",
        headers={"Authorization": "Bearer keep"},
        timeout=5,
    ) as resp:
        resp.read()
    assert handler.seen_auth == ["Bearer keep"]


def test_open_url_refuses_redirect_that_leaves_the_allowed_scheme(
    redirect_servers: tuple[str, str, type],
) -> None:
    """The allowlist is re-applied to every hop, not only the URL first asked for."""
    source, _target, _handler = redirect_servers
    with pytest.raises(UnsupportedScheme):
        open_url(
            f"{source}?to=ftp%3A%2F%2F127.0.0.1%2Fsecret",
            timeout=5,
            headers={"Authorization": "Basic c2VjcmV0"},
        )
