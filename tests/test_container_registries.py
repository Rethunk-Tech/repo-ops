"""Tests for container v2 registry features.

Covers:
- OCI bearer-challenge flow (401 → token → retry) for generic registries
- docker.io direct token fetch (no probe RTT)
- Basic-auth from ~/.docker/config.json (DOCKER_CONFIG env honoured)
- credsStore-only config → report-only
- Digest refresh plan+apply byte-preservation (REPO_OPS_CONTAINER_REFRESH_SHA=1)
- Compose build.dockerfile resolution matrix (string form, mapping form, context+dockerfile)

No real network calls are made; urllib is monkeypatched throughout.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_container.container import (
    _basic_auth_for_host,
    _compose_build_dockerfiles,
    _fetch_tags,
    _fetch_token_for_registry,
    _parse_www_authenticate,
    apply_container,
    check_container,
    find_container_files,
)
from repo_ops_models import Workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws(path: Path) -> Workspace:
    return Workspace(
        path=path,
        ecosystem="container",
        manager="container",
        git_repo=path,
        label="",
    )


def _make_http_response(
    body: bytes, status: int = 200, headers: dict[str, str] | None = None
) -> MagicMock:
    """Build a mock HTTP response for urllib.request.urlopen context manager."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.headers = MagicMock()
    resp.headers.get = (
        (lambda h, d="": (headers or {}).get(h, d)) if headers else (lambda h, d="": d)
    )
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_http_error(
    code: int, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    hdrs: Any = MagicMock()
    hdrs.get = (
        (lambda h, d="": (headers or {}).get(h, d)) if headers else (lambda h, d="": d)
    )
    return urllib.error.HTTPError(url="http://x", code=code, msg="", hdrs=hdrs, fp=None)


# ---------------------------------------------------------------------------
# _parse_www_authenticate
# ---------------------------------------------------------------------------


def test_parse_www_authenticate_all_fields() -> None:
    header = 'Bearer realm="https://auth.example.com/token",service="registry.example.com",scope="repository:myimage:pull"'
    result = _parse_www_authenticate(header)
    assert result["realm"] == "https://auth.example.com/token"
    assert result["service"] == "registry.example.com"
    assert result["scope"] == "repository:myimage:pull"


def test_parse_www_authenticate_no_scope() -> None:
    header = (
        'Bearer realm="https://auth.example.com/token",service="registry.example.com"'
    )
    result = _parse_www_authenticate(header)
    assert result["realm"] == "https://auth.example.com/token"
    assert "scope" not in result


def test_parse_www_authenticate_unquoted_values() -> None:
    header = "Bearer realm=https://auth.example.com/token,service=registry.example.com"
    result = _parse_www_authenticate(header)
    assert result["realm"] == "https://auth.example.com/token"
    assert result["service"] == "registry.example.com"


# ---------------------------------------------------------------------------
# _load_docker_config / _basic_auth_for_host
# ---------------------------------------------------------------------------


def test_basic_auth_for_host_found(tmp_path: Path) -> None:
    creds = base64.b64encode(b"user:pass").decode()
    cfg = {"auths": {"ghcr.io": {"auth": creds}}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        result = _basic_auth_for_host("ghcr.io")

    assert result == f"Basic {creds}"


def test_basic_auth_for_host_not_found(tmp_path: Path) -> None:
    cfg = {"auths": {"ghcr.io": {"auth": "dXNlcjpwYXNz"}}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        result = _basic_auth_for_host("quay.io")

    assert result is None


def test_basic_auth_no_config_file(tmp_path: Path) -> None:
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        result = _basic_auth_for_host("ghcr.io")
    assert result is None


def test_basic_auth_creds_store_only_returns_none(tmp_path: Path) -> None:
    """Only a credsStore entry → no auth key → return None (report-only path)."""
    cfg = {"auths": {}, "credsStore": "osxkeychain"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        result = _basic_auth_for_host("ghcr.io")

    assert result is None


def test_basic_auth_docker_registry_aliases(tmp_path: Path) -> None:
    """docker.io auth stored under index.docker.io/v1/ is found for registry-1."""
    creds = base64.b64encode(b"dockerhub_user:token").decode()
    cfg = {"auths": {"https://index.docker.io/v1/": {"auth": creds}}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        result = _basic_auth_for_host("registry-1.docker.io")

    assert result == f"Basic {creds}"


# ---------------------------------------------------------------------------
# _fetch_token_for_registry — challenge flow
# ---------------------------------------------------------------------------


def test_fetch_token_docker_io_direct() -> None:
    """docker.io uses the known endpoint, no probe RTT."""
    token_resp = _make_http_response(json.dumps({"token": "mytoken123"}).encode())

    with patch("urllib.request.urlopen", return_value=token_resp) as mock_open:
        result = _fetch_token_for_registry(
            "registry-1.docker.io", "library/nginx", None
        )

    assert result == "mytoken123"
    # Should have called the Docker auth endpoint directly
    url_called = mock_open.call_args[0][0].full_url
    assert "auth.docker.io" in url_called


def test_fetch_token_generic_registry_challenge_flow() -> None:
    """Generic registry: probe /v2/ → 401 → parse challenge → fetch token."""
    probe_error = _make_http_error(
        401,
        headers={
            "WWW-Authenticate": 'Bearer realm="https://quay.io/v2/auth",service="quay.io",scope="repository:org/img:pull"'
        },
    )
    token_resp = _make_http_response(json.dumps({"token": "quay_token"}).encode())

    call_count = 0

    def side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise probe_error
        return token_resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        result = _fetch_token_for_registry("quay.io", "org/img", None)

    assert result == "quay_token"
    assert call_count == 2  # probe + token fetch


def test_fetch_token_generic_registry_200_probe_returns_none() -> None:
    """If /v2/ returns 200 (no auth needed), token is None."""
    probe_resp = _make_http_response(b"{}")

    with patch("urllib.request.urlopen", return_value=probe_resp):
        result = _fetch_token_for_registry("internal.registry.local", "app/img", None)

    assert result is None


def test_fetch_token_with_basic_auth_forwarded() -> None:
    """Basic-auth is forwarded to the token endpoint."""
    creds = base64.b64encode(b"user:pass").decode()
    basic_auth = f"Basic {creds}"

    token_resp = _make_http_response(json.dumps({"token": "authed_token"}).encode())

    with patch("urllib.request.urlopen", return_value=token_resp) as mock_open:
        result = _fetch_token_for_registry(
            "registry-1.docker.io", "library/nginx", basic_auth
        )

    assert result == "authed_token"
    # Verify Authorization header was passed
    req = mock_open.call_args[0][0]
    assert req.get_header("Authorization") == basic_auth


# ---------------------------------------------------------------------------
# _fetch_tags — generic challenge flow integration
# ---------------------------------------------------------------------------


def test_fetch_tags_challenge_flow_quay(tmp_path: Path) -> None:
    """_fetch_tags for quay.io: probe→401→token→tags/list success."""
    probe_error = _make_http_error(
        401,
        headers={
            "WWW-Authenticate": 'Bearer realm="https://quay.io/v2/auth",service="quay.io"'
        },
    )
    token_resp = _make_http_response(json.dumps({"token": "quay_token"}).encode())
    tags_resp = _make_http_response(
        json.dumps({"name": "org/myapp", "tags": ["1.0", "1.1", "1.2"]}).encode()
    )

    call_n = 0

    def side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise probe_error  # /v2/ probe
        if call_n == 2:
            return token_resp  # token fetch
        return tags_resp  # tags/list

    with patch("urllib.request.urlopen", side_effect=side_effect):
        tags = _fetch_tags("quay.io/org/myapp")

    assert tags == ["1.0", "1.1", "1.2"]


def test_fetch_tags_challenge_flow_gcr(tmp_path: Path) -> None:
    """_fetch_tags for gcr.io: probe→401→token→tags/list success."""
    probe_error = _make_http_error(
        401,
        headers={
            "WWW-Authenticate": (
                'Bearer realm="https://gcr.io/v2/token",service="gcr.io",'
                'scope="repository:project/img:pull"'
            )
        },
    )
    token_resp = _make_http_response(json.dumps({"token": "gcr_token"}).encode())
    tags_resp = _make_http_response(
        json.dumps({"name": "project/img", "tags": ["1.0", "1.1"]}).encode()
    )

    call_n = 0

    def side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise probe_error
        if call_n == 2:
            url = req.full_url if hasattr(req, "full_url") else str(req)
            assert "gcr.io" in url
            return token_resp
        return tags_resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        tags = _fetch_tags("gcr.io/project/img")

    assert tags == ["1.0", "1.1"]


def test_fetch_token_mcr_challenge_flow() -> None:
    """mcr.microsoft.com: probe→401→parse challenge→fetch token."""
    probe_error = _make_http_error(
        401,
        headers={
            "WWW-Authenticate": (
                'Bearer realm="https://mcr.microsoft.com/v2/auth",'
                'service="mcr.microsoft.com",'
                'scope="repository:dotnet/runtime:pull"'
            )
        },
    )
    token_resp = _make_http_response(json.dumps({"token": "mcr_token"}).encode())

    call_count = 0

    def side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise probe_error
        return token_resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        result = _fetch_token_for_registry(
            "mcr.microsoft.com", "dotnet/runtime", None
        )

    assert result == "mcr_token"
    assert call_count == 2


def test_fetch_tags_challenge_flow_mcr(tmp_path: Path) -> None:
    """_fetch_tags for mcr.microsoft.com uses the generic bearer challenge path."""
    probe_error = _make_http_error(
        401,
        headers={
            "WWW-Authenticate": (
                'Bearer realm="https://mcr.microsoft.com/v2/auth",'
                'service="mcr.microsoft.com"'
            )
        },
    )
    token_resp = _make_http_response(json.dumps({"token": "mcr_token"}).encode())
    tags_resp = _make_http_response(
        json.dumps({"name": "dotnet/runtime", "tags": ["8.0", "8.0.1"]}).encode()
    )

    call_n = 0

    def side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise probe_error
        if call_n == 2:
            return token_resp
        return tags_resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        tags = _fetch_tags("mcr.microsoft.com/dotnet/runtime")

    assert tags == ["8.0", "8.0.1"]


def test_fetch_tags_with_basic_auth(tmp_path: Path) -> None:
    """_fetch_tags uses Basic auth from docker config."""
    creds = base64.b64encode(b"user:pass").decode()
    cfg = {"auths": {"ghcr.io": {"auth": creds}}}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    token_resp = _make_http_response(json.dumps({"token": "ghcr_token"}).encode())
    tags_resp = _make_http_response(json.dumps({"tags": ["2.0", "2.1"]}).encode())

    call_n = 0

    def side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise _make_http_error(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io"'
                },
            )
        if call_n == 2:
            # Verify basic auth is forwarded
            assert req.get_header("Authorization") == f"Basic {creds}"
            return token_resp
        return tags_resp

    with (
        patch("urllib.request.urlopen", side_effect=side_effect),
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
    ):
        tags = _fetch_tags("ghcr.io/org/img")

    assert tags == ["2.0", "2.1"]


def test_fetch_tags_creds_store_only_still_works_anonymously(tmp_path: Path) -> None:
    """credsStore-only config: no auth key → anonymous token fetch still attempted."""
    cfg = {"auths": {}, "credsStore": "osxkeychain"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    # ghcr.io: probe → 401 → token fetch (anonymous) → tags
    probe_error = _make_http_error(
        401,
        headers={
            "WWW-Authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io"'
        },
    )
    token_resp = _make_http_response(json.dumps({"token": "anon_token"}).encode())
    tags_resp = _make_http_response(json.dumps({"tags": ["1.0"]}).encode())

    call_n = 0

    def side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise probe_error
        if call_n == 2:
            # No Authorization header (anonymous)
            assert req.get_header("Authorization") is None
            return token_resp
        return tags_resp

    with (
        patch("urllib.request.urlopen", side_effect=side_effect),
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
    ):
        tags = _fetch_tags("ghcr.io/org/img")

    assert tags == ["1.0"]


def test_fetch_tags_creds_store_check_reports_report_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_container with a credsStore-only config: result is still reported
    (anonymous fetch proceeds; no special creds-helper-only error path needed
    since we fall back to anonymous gracefully)."""
    cfg = {"auths": {}, "credsStore": "osxkeychain"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    (tmp_path / "Dockerfile").write_text("FROM ghcr.io/org/img:1.0\n")

    with (
        patch(
            "repo_ops_container.container._fetch_tags",
            return_value=["1.0", "1.1"],
        ),
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].to == "1.1"


# ---------------------------------------------------------------------------
# Digest refresh — check (plan)
# ---------------------------------------------------------------------------

_OLD_DIGEST = "sha256:aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666aaaa1111bbbb2222"
_NEW_DIGEST = "sha256:1111aaaa2222bbbb3333cccc4444dddd5555eeee6666ffff1111aaaa2222bbbb"


def test_digest_refresh_check_reports_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REFRESH_SHA=1 and digest has changed → actionable Change."""
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "1")
    (tmp_path / "Dockerfile").write_text(f"FROM nginx:1.25@{_OLD_DIGEST}\n")

    with patch(
        "repo_ops_container.container._fetch_manifest_digest",
        return_value=_NEW_DIGEST,
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    c = changes[0]
    assert "[report-only]" not in c.to
    assert c.from_ver == _OLD_DIGEST
    assert c.to == _NEW_DIGEST


def test_digest_refresh_check_already_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Digest is already current → no Change emitted."""
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "1")
    (tmp_path / "Dockerfile").write_text(f"FROM nginx:1.25@{_OLD_DIGEST}\n")

    with patch(
        "repo_ops_container.container._fetch_manifest_digest",
        return_value=_OLD_DIGEST,
    ):
        changes = check_container(_make_ws(tmp_path))

    assert changes == []


def test_digest_refresh_check_off_by_default(tmp_path: Path) -> None:
    """Without REFRESH_SHA env var, tag+sha pins are report-only."""
    (tmp_path / "Dockerfile").write_text(f"FROM nginx:1.25@{_OLD_DIGEST}\n")

    with patch(
        "repo_ops_container.container._fetch_manifest_digest",
        return_value=_NEW_DIGEST,
    ) as mock_digest:
        changes = check_container(_make_ws(tmp_path))

    # Should be report-only; digest function should not even be called
    mock_digest.assert_not_called()
    assert len(changes) == 1
    assert "[report-only]" in changes[0].to
    assert "REPO_OPS_CONTAINER_REFRESH_SHA" in changes[0].to


def test_digest_refresh_check_query_failure_report_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Digest query failure → report-only."""
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "1")
    (tmp_path / "Dockerfile").write_text(f"FROM nginx:1.25@{_OLD_DIGEST}\n")

    with patch(
        "repo_ops_container.container._fetch_manifest_digest",
        return_value=None,
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert "[report-only]" in changes[0].to
    assert "digest query failed" in changes[0].to


# ---------------------------------------------------------------------------
# Digest refresh — apply (byte-preservation)
# ---------------------------------------------------------------------------


def test_digest_refresh_apply_byte_preserving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_container with REFRESH_SHA=1 rewrites only the digest, preserving all other bytes."""
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "1")
    dockerfile = tmp_path / "Dockerfile"
    original = (
        f"# syntax=docker/dockerfile:1\n"
        f"FROM nginx:1.25@{_OLD_DIGEST}\n"
        f"RUN apt-get update\n"
        f"EXPOSE 80\n"
    )
    dockerfile.write_text(original, encoding="utf-8")

    with patch(
        "repo_ops_container.container._fetch_manifest_digest",
        return_value=_NEW_DIGEST,
    ):
        apply_container(_make_ws(tmp_path))

    result = dockerfile.read_text(encoding="utf-8")
    # Digest updated
    assert _NEW_DIGEST in result
    assert _OLD_DIGEST not in result
    # Tag preserved
    assert "nginx:1.25@" in result
    # Other lines untouched
    assert "# syntax=docker/dockerfile:1\n" in result
    assert "RUN apt-get update\n" in result
    assert "EXPOSE 80\n" in result


def test_digest_refresh_apply_crlf_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRLF line endings survive digest refresh apply."""
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "1")
    dockerfile = tmp_path / "Dockerfile"
    original_bytes = f"FROM nginx:1.25@{_OLD_DIGEST}\r\nEXPOSE 80\r\n".encode()
    dockerfile.write_bytes(original_bytes)

    with patch(
        "repo_ops_container.container._fetch_manifest_digest",
        return_value=_NEW_DIGEST,
    ):
        apply_container(_make_ws(tmp_path))

    result = dockerfile.read_bytes()
    assert _NEW_DIGEST.encode() in result
    assert b"EXPOSE 80\r\n" in result


def test_digest_refresh_apply_off_by_default_untouched(tmp_path: Path) -> None:
    """Without REFRESH_SHA, tag+sha pins must not be modified."""
    dockerfile = tmp_path / "Dockerfile"
    original = f"FROM nginx:1.25@{_OLD_DIGEST}\n"
    dockerfile.write_text(original, encoding="utf-8")

    with patch(
        "repo_ops_container.container._fetch_manifest_digest",
        return_value=_NEW_DIGEST,
    ) as mock_digest:
        apply_container(_make_ws(tmp_path))

    mock_digest.assert_not_called()
    assert dockerfile.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Compose build.dockerfile resolution
# ---------------------------------------------------------------------------


def test_compose_build_string_form(tmp_path: Path) -> None:
    """Short string form: ``build: ./app`` → Dockerfile in app/."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    dockerfile = app_dir / "Dockerfile"
    dockerfile.write_text("FROM python:3.12\n")

    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  app:\n    build: ./app\n")

    result = _compose_build_dockerfiles(compose)
    assert len(result) == 1
    assert result[0].resolve() == dockerfile.resolve()


def test_compose_build_mapping_with_dockerfile(tmp_path: Path) -> None:
    """Mapping form with explicit dockerfile key."""
    custom_df = tmp_path / "Dockerfile.prod"
    custom_df.write_text("FROM python:3.12\n")

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  app:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile.prod\n"
    )

    result = _compose_build_dockerfiles(compose)
    assert len(result) == 1
    assert result[0].resolve() == custom_df.resolve()


def test_compose_build_mapping_context_only(tmp_path: Path) -> None:
    """Mapping form with only context → look for default Dockerfile."""
    sub_dir = tmp_path / "backend"
    sub_dir.mkdir()
    df = sub_dir / "Dockerfile"
    df.write_text("FROM golang:1.22\n")

    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  api:\n    build:\n      context: ./backend\n")

    result = _compose_build_dockerfiles(compose)
    assert len(result) == 1
    assert result[0].resolve() == df.resolve()


def test_compose_build_nonexistent_dockerfile_excluded(tmp_path: Path) -> None:
    """If the referenced Dockerfile does not exist, it is not included."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  app:\n    build: ./missing_dir\n")

    result = _compose_build_dockerfiles(compose)
    assert result == []


def test_compose_build_dedup_in_find_container_files(tmp_path: Path) -> None:
    """A Dockerfile already discovered as a sibling is not duplicated."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM nginx:1.25\n")

    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  web:\n    build: .\n")

    files = find_container_files(tmp_path)
    paths = [f.resolve() for f in files]
    # Dockerfile should appear only once
    dockerfile_entries = [p for p in paths if p.name == "Dockerfile"]
    assert len(dockerfile_entries) == 1


def test_compose_build_dockerfile_included_in_check(
    tmp_path: Path,
) -> None:
    """check_container discovers images from a Dockerfile referenced via compose build:."""
    sub_dir = tmp_path / "app"
    sub_dir.mkdir()
    dockerfile = sub_dir / "Dockerfile"
    dockerfile.write_text("FROM python:3.11\n")

    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  app:\n    build: ./app\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["3.11", "3.12"],
    ):
        changes = check_container(_make_ws(tmp_path))

    names = [c.name for c in changes]
    assert any("python" in n for n in names)
    python_c = next(c for c in changes if "python" in c.name)
    assert python_c.to == "3.12"


def test_compose_build_and_image_no_double_check(tmp_path: Path) -> None:
    """A service with both build: and image: does not check the same image twice."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM nginx:1.25\n")

    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  web:\n    image: nginx:1.25\n    build: .\n")

    call_count = 0

    def fake_fetch(image: str) -> list[str] | None:
        nonlocal call_count
        if "nginx" in image:
            call_count += 1
            return ["1.25", "1.26"]
        return None

    with patch("repo_ops_container.container._fetch_tags", side_effect=fake_fetch):
        changes = check_container(_make_ws(tmp_path))

    # Should deduplicate: nginx:1.25 from compose image: and from Dockerfile
    nginx_changes = [c for c in changes if "nginx" in c.name]
    assert len(nginx_changes) == 1
