"""Container apply-verify: digest-check only refs in the apply diff."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_models import OwnedEdit
from repo_ops_upgrade.verify import verify_workspace


def _resp(headers: dict[str, str] | None = None, body: bytes = b"{}") -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers = MagicMock()
    resp.headers.get = lambda h, d="": (headers or {}).get(h, d)
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(
    url: str, code: int, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    hdrs: Any = MagicMock()
    hdrs.get = (lambda h, d="": (headers or {}).get(h, d)) if headers else (lambda h, d="": d)
    return urllib.error.HTTPError(url=url, code=code, msg="", hdrs=hdrs, fp=None)


def _open_ok(
    url: str, *, timeout: float, headers: dict[str, str] | None = None, method: str | None = None
) -> MagicMock:
    del timeout, headers
    if url.rstrip("/").endswith("/v2"):
        return _resp()
    if "/token" in url:
        return _resp(body=b'{"token":"anon"}')
    if method in (None, "HEAD", "GET") and "/manifests/" in url:
        return _resp(headers={"Docker-Content-Digest": "sha256:abc"})
    raise AssertionError(f"unexpected {method} {url}")


def _open_manifest_404(
    url: str, *, timeout: float, headers: dict[str, str] | None = None, method: str | None = None
) -> MagicMock:
    del timeout, headers
    if url.rstrip("/").endswith("/v2"):
        return _resp()
    if "/manifests/" in url:
        raise _http_error(url, 404)
    raise AssertionError(f"unexpected {method} {url}")


def _open_offline(
    url: str, *, timeout: float, headers: dict[str, str] | None = None, method: str | None = None
) -> MagicMock:
    del timeout, headers, method
    raise urllib.error.URLError("offline")


def _open_token_then_digest(
    url: str, *, timeout: float, headers: dict[str, str] | None = None, method: str | None = None
) -> MagicMock:
    del timeout
    if url.rstrip("/").endswith("/v2"):
        raise _http_error(
            url,
            401,
            {
                "WWW-Authenticate": (
                    'Bearer realm="https://auth.test/token",service="registry.test"'
                )
            },
        )
    if url.startswith("https://auth.test/token"):
        return _resp(body=b'{"token":"anon"}')
    if "/manifests/" in url:
        assert headers and headers.get("Authorization") == "Bearer anon"
        return _resp(headers={"Docker-Content-Digest": "sha256:abc"})
    raise AssertionError(f"unexpected {method} {url}")


def _open_recording(seen: list[str]) -> Any:
    def _open(
        url: str,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
        method: str | None = None,
    ) -> MagicMock:
        del timeout, headers
        seen.append(url)
        return _open_ok(url, timeout=1, method=method)

    return _open


@pytest.mark.parametrize(
    "rel,before,after,opener,expect_substr",
    [
        (
            "Dockerfile",
            b"FROM registry.test/app:1\n",
            b"FROM registry.test/app:2\n",
            _open_ok,
            None,
        ),
        (
            "Containerfile",
            b"FROM registry.test/app:1\n",
            b"FROM registry.test/app:2\n",
            _open_token_then_digest,
            None,
        ),
        (
            "compose.yml",
            b"image: registry.test/app:1\n",
            b"image: registry.test/app:2\n",
            _open_manifest_404,
            "registry.test/app:2: registry error: HTTP 404",
        ),
        (
            "Dockerfile",
            b"FROM registry.test/app:1\n",
            b"FROM registry.test/app:2\n",
            _open_offline,
            "registry.test/app:2: network error: offline",
        ),
    ],
)
def test_container_verify_digest_checks_rewritten_refs(
    tmp_path: Path,
    make_workspace: Any,
    rel: str,
    before: bytes,
    after: bytes,
    opener: Any,
    expect_substr: str | None,
) -> None:
    ws = make_workspace(tmp_path, manager="container", ecosystem="container")
    edits = {rel: OwnedEdit(before=before, from_head=True, applied=after)}
    with (
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
        patch("repo_ops_container.container.open_url", side_effect=opener),
    ):
        err = verify_workspace(ws, owned_edits=edits)
    if expect_substr is None:
        assert err is None
    else:
        assert err is not None
        assert expect_substr in err


def test_container_verify_skips_unchanged_from_lines(tmp_path: Path, make_workspace: Any) -> None:
    """Only the rewritten FROM is resolved; a sibling pin in the same file is not."""
    ws = make_workspace(tmp_path, manager="container", ecosystem="container")
    before = b"FROM registry.test/keep:1\nFROM registry.test/bump:1\n"
    after = b"FROM registry.test/keep:1\nFROM registry.test/bump:2\n"
    seen: list[str] = []
    edits = {"Dockerfile": OwnedEdit(before=before, from_head=True, applied=after)}
    with (
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
        patch("repo_ops_container.container.open_url", side_effect=_open_recording(seen)),
    ):
        assert verify_workspace(ws, owned_edits=edits) is None
    joined = "\n".join(seen)
    assert "/keep/manifests/" not in joined
    assert "/bump/manifests/2" in joined


def test_container_verify_empty_diff_is_ok(tmp_path: Path, make_workspace: Any) -> None:
    ws = make_workspace(tmp_path, manager="container", ecosystem="container")
    with patch("repo_ops_container.container.open_url") as mock_open:
        assert verify_workspace(ws) is None
    mock_open.assert_not_called()
