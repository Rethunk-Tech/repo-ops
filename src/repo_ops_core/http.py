"""Stdlib HTTP fetch with an enforced scheme allowlist.

Registry URLs are not always ours: an OCI ``WWW-Authenticate`` challenge names
its own token realm, so the URL that gets opened comes off the wire. Every
outbound open goes through :func:`open_url`, which refuses anything that is not
http/https before urllib can turn ``file:``/``ftp:`` into a local read.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsupportedScheme(ValueError):
    """Raised when a URL's scheme is outside :data:`ALLOWED_SCHEMES`."""


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
    before the request is built. When *allowed_host* is set the URL must be
    https and its hostname must match (case-insensitive). Everything else
    behaves as ``urllib.request.urlopen``, including raising
    ``urllib.error.HTTPError`` for a non-2xx status.
    """
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
    # S310 asks for exactly the allowlist enforced above; this is where it lives.
    request = urllib.request.Request(url, headers=headers or {}, method=method)  # noqa: S310
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
