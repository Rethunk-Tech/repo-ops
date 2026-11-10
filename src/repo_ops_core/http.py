"""Stdlib HTTP fetch with an enforced scheme allowlist.

Registry URLs are not always ours: an OCI ``WWW-Authenticate`` challenge names
its own token realm, so the URL that gets opened comes off the wire. Every
outbound open goes through :func:`open_url`, which refuses anything that is not
http/https before urllib can turn ``file:``/``ftp:`` into a local read, and
holds that refusal across redirects — urllib copies every request header but
``content-*`` onto a redirect target, so an ``Authorization`` header must not
follow a registry's redirect to another origin.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsupportedScheme(ValueError):
    """Raised when a URL's scheme is outside :data:`ALLOWED_SCHEMES`."""


def _check_url(url: str, allowed_host: str | None) -> None:
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if allowed_host is not None:
        if scheme != "https":
            raise UnsupportedScheme(f"refusing to open {scheme or 'scheme-less'} URL: {url}")
        host = (parsed.hostname or "").lower()
        if host != allowed_host.lower():
            raise UnsupportedScheme(f"refusing to open host {host or 'empty'}: {url}")
    elif scheme not in ALLOWED_SCHEMES:
        raise UnsupportedScheme(f"refusing to open {scheme or 'scheme-less'} URL: {url}")


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-apply the allowlist to every hop and drop credentials on origin change."""

    def __init__(self, allowed_host: str | None) -> None:
        self._allowed_host = allowed_host

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _check_url(newurl, self._allowed_host)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        old = urllib.parse.urlsplit(req.full_url)
        hop = urllib.parse.urlsplit(newurl)
        if (old.scheme.lower(), old.hostname, old.port) != (
            hop.scheme.lower(),
            hop.hostname,
            hop.port,
        ):
            new.remove_header("Authorization")
        return new


def open_url(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    allowed_host: str | None = None,
) -> Any:
    """Open *url* over http/https and return the urllib response.

    Raises :class:`UnsupportedScheme` (a ``ValueError``) for any other scheme,
    before the request is built, and for any redirect hop that leaves the
    allowed scheme (or, with *allowed_host*, the allowed https host). When
    *allowed_host* is set the URL must be https and its hostname must match
    (case-insensitive). An ``Authorization`` header is dropped on a hop that
    changes origin. Everything else behaves as ``urllib.request.urlopen``,
    including raising ``urllib.error.HTTPError`` for a non-2xx status.
    """
    _check_url(url, allowed_host)
    # S310 asks for exactly the allowlist enforced above; this is where it lives.
    request = urllib.request.Request(url, headers=headers or {}, method=method)  # noqa: S310
    opener = urllib.request.build_opener(_GuardedRedirectHandler(allowed_host))
    return opener.open(request, timeout=timeout)  # noqa: S310
