"""Tests for container credential-helper resolution (sub-task 8b).

Covers:
  * _cred_helper_name_for_host: credHelpers per-host, credsStore global fallback
  * _run_cred_helper: happy path, binary not found, non-zero exit, bad JSON
  * _ecr_region_for_host: ECR URL parsing
  * _ecr_get_login_password: aws CLI happy path, missing binary, non-zero exit
  * _resolve_auth_for_host: priority order (inline auth → helper → ECR)
  * _fetch_tags integration: cred-helper credentials flow through bearer challenge

No real subprocesses or network calls are made.
"""

from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_container.container import (
    _cred_helper_name_for_host,
    _ecr_get_login_password,
    _ecr_region_for_host,
    _fetch_tags,
    _resolve_auth_for_host,
    _run_cred_helper,
    check_container,
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


def _docker_cfg(tmp_path: Path, cfg: dict) -> None:
    """Write *cfg* to tmp_path/config.json and set DOCKER_CONFIG."""
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def _make_http_response(
    body: bytes, headers: dict[str, str] | None = None
) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers = MagicMock()
    resp.headers.get = lambda h, d="": (headers or {}).get(h, d)
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_http_error(
    code: int, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    hdrs: Any = MagicMock()
    hdrs.get = lambda h, d="": (headers or {}).get(h, d)
    return urllib.error.HTTPError(url="http://x", code=code, msg="", hdrs=hdrs, fp=None)


# ---------------------------------------------------------------------------
# _cred_helper_name_for_host
# ---------------------------------------------------------------------------


def test_cred_helper_per_host_override(tmp_path: Path) -> None:
    _docker_cfg(tmp_path, {"credHelpers": {"ghcr.io": "github-app"}})
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        assert _cred_helper_name_for_host("ghcr.io") == "github-app"


def test_cred_helper_per_host_https_prefix(tmp_path: Path) -> None:
    """Keys stored with https:// prefix are matched."""
    _docker_cfg(tmp_path, {"credHelpers": {"https://ghcr.io": "myhelper"}})
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        assert _cred_helper_name_for_host("ghcr.io") == "myhelper"


def test_cred_helper_creds_store_fallback(tmp_path: Path) -> None:
    """No per-host match falls back to credsStore."""
    _docker_cfg(tmp_path, {"credsStore": "osxkeychain"})
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        assert _cred_helper_name_for_host("any.registry.io") == "osxkeychain"


def test_cred_helper_per_host_overrides_creds_store(tmp_path: Path) -> None:
    _docker_cfg(
        tmp_path,
        {
            "credHelpers": {"special.io": "special-helper"},
            "credsStore": "osxkeychain",
        },
    )
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        assert _cred_helper_name_for_host("special.io") == "special-helper"


def test_cred_helper_none_when_nothing_configured(tmp_path: Path) -> None:
    _docker_cfg(tmp_path, {"auths": {}})
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        assert _cred_helper_name_for_host("ghcr.io") is None


def test_cred_helper_docker_io_alias_for_registry_1(tmp_path: Path) -> None:
    """credHelpers keyed as docker.io resolve when host is registry-1.docker.io."""
    _docker_cfg(tmp_path, {"credHelpers": {"docker.io": "desktop"}})
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        assert _cred_helper_name_for_host("registry-1.docker.io") == "desktop"


def test_cred_helper_index_docker_io_v1_alias(tmp_path: Path) -> None:
    """credHelpers keyed as https://index.docker.io/v1/ resolve for Hub OCI host."""
    _docker_cfg(
        tmp_path, {"credHelpers": {"https://index.docker.io/v1/": "osxkeychain"}}
    )
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        assert _cred_helper_name_for_host("registry-1.docker.io") == "osxkeychain"


def test_resolve_auth_docker_io_cred_helper_uses_hub_login_url(
    tmp_path: Path,
) -> None:
    """Hub helper lookup passes https://index.docker.io/v1/ on stdin."""
    _docker_cfg(tmp_path, {"credHelpers": {"docker.io": "desktop"}})
    helper_proc = MagicMock(spec=subprocess.CompletedProcess)
    helper_proc.returncode = 0
    helper_proc.stdout = json.dumps({"Username": "user", "Secret": "tok"})

    with (
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
        patch("subprocess.run", return_value=helper_proc) as mock_run,
    ):
        result = _resolve_auth_for_host("registry-1.docker.io")

    mock_run.assert_called_once_with(
        ["docker-credential-desktop", "get"],
        input="https://index.docker.io/v1/",
        capture_output=True,
        text=True,
        timeout=5,
    )
    expected = base64.b64encode(b"user:tok").decode()
    assert result == f"Basic {expected}"


def test_cred_helper_no_config_file(tmp_path: Path) -> None:
    with patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}):
        # config.json missing — _load_docker_config returns {} — no helper
        assert _cred_helper_name_for_host("ghcr.io") is None


# ---------------------------------------------------------------------------
# _run_cred_helper
# ---------------------------------------------------------------------------


def _cred_helper_response(username: str, secret: str) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = json.dumps({"Username": username, "Secret": secret})
    return proc


def test_run_cred_helper_happy_path() -> None:
    creds = {"Username": "token", "Secret": "s3cr3t"}
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = json.dumps(creds)

    with patch("subprocess.run", return_value=proc) as mock_run:
        result = _run_cred_helper("myhelper", "ghcr.io")

    mock_run.assert_called_once_with(
        ["docker-credential-myhelper", "get"],
        input="ghcr.io",
        capture_output=True,
        text=True,
        timeout=5,
    )
    expected_b64 = base64.b64encode(b"token:s3cr3t").decode()
    assert result == f"Basic {expected_b64}"


def test_run_cred_helper_binary_not_found() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _run_cred_helper("missing-helper", "ghcr.io") is None


def test_run_cred_helper_timeout() -> None:
    with patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5)
    ):
        assert _run_cred_helper("slow-helper", "ghcr.io") is None


def test_run_cred_helper_nonzero_exit() -> None:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 1
    proc.stdout = "error: not logged in"
    with patch("subprocess.run", return_value=proc):
        assert (
            _run_cred_helper("ecr-login", "123.dkr.ecr.us-east-1.amazonaws.com") is None
        )


def test_run_cred_helper_bad_json() -> None:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = "not json"
    with patch("subprocess.run", return_value=proc):
        assert _run_cred_helper("myhelper", "ghcr.io") is None


def test_run_cred_helper_empty_fields() -> None:
    """JSON with empty Username or Secret → None."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = json.dumps({"Username": "", "Secret": "abc"})
    with patch("subprocess.run", return_value=proc):
        assert _run_cred_helper("myhelper", "ghcr.io") is None


# ---------------------------------------------------------------------------
# _ecr_region_for_host
# ---------------------------------------------------------------------------


def test_ecr_region_standard_host() -> None:
    assert (
        _ecr_region_for_host("123456789012.dkr.ecr.us-east-1.amazonaws.com")
        == "us-east-1"
    )


def test_ecr_region_eu_west() -> None:
    assert _ecr_region_for_host("999.dkr.ecr.eu-west-2.amazonaws.com") == "eu-west-2"


def test_ecr_region_not_ecr() -> None:
    assert _ecr_region_for_host("ghcr.io") is None
    assert _ecr_region_for_host("registry-1.docker.io") is None
    assert _ecr_region_for_host("quay.io") is None


# ---------------------------------------------------------------------------
# _ecr_get_login_password
# ---------------------------------------------------------------------------


def test_ecr_get_login_password_happy_path() -> None:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = "super-secret-ecr-token\n"
    with patch("subprocess.run", return_value=proc) as mock_run:
        result = _ecr_get_login_password("us-east-1")

    mock_run.assert_called_once_with(
        ["aws", "ecr", "get-login-password", "--region", "us-east-1"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    expected_b64 = base64.b64encode(b"AWS:super-secret-ecr-token").decode()
    assert result == f"Basic {expected_b64}"


def test_ecr_get_login_password_aws_not_found() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _ecr_get_login_password("us-east-1") is None


def test_ecr_get_login_password_nonzero() -> None:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 1
    proc.stdout = ""
    with patch("subprocess.run", return_value=proc):
        assert _ecr_get_login_password("us-east-1") is None


def test_ecr_get_login_password_empty_output() -> None:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = ""
    with patch("subprocess.run", return_value=proc):
        assert _ecr_get_login_password("us-east-1") is None


# ---------------------------------------------------------------------------
# _resolve_auth_for_host — priority ordering
# ---------------------------------------------------------------------------


def test_resolve_auth_inline_wins(tmp_path: Path) -> None:
    """Inline auths[host].auth takes priority over credential helpers."""
    b64 = base64.b64encode(b"user:inline").decode()
    _docker_cfg(
        tmp_path,
        {
            "auths": {"ghcr.io": {"auth": b64}},
            "credsStore": "osxkeychain",
        },
    )
    with (
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
        patch("subprocess.run") as mock_run,
    ):
        result = _resolve_auth_for_host("ghcr.io")

    # subprocess must NOT be called when inline auth is available
    mock_run.assert_not_called()
    assert result == f"Basic {b64}"


def test_resolve_auth_helper_when_no_inline(tmp_path: Path) -> None:
    """When inline auth is absent, credential helper is tried."""
    _docker_cfg(tmp_path, {"credHelpers": {"ghcr.io": "gh-token"}})
    helper_proc = MagicMock(spec=subprocess.CompletedProcess)
    helper_proc.returncode = 0
    helper_proc.stdout = json.dumps({"Username": "x-token", "Secret": "ghs_abc"})

    with (
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
        patch("subprocess.run", return_value=helper_proc),
    ):
        result = _resolve_auth_for_host("ghcr.io")

    expected = base64.b64encode(b"x-token:ghs_abc").decode()
    assert result == f"Basic {expected}"


def test_resolve_auth_ecr_fallback(tmp_path: Path) -> None:
    """ECR host with no config → tries aws ecr get-login-password."""
    _docker_cfg(tmp_path, {})  # empty config
    aws_proc = MagicMock(spec=subprocess.CompletedProcess)
    aws_proc.returncode = 0
    aws_proc.stdout = "ecr-password\n"

    ecr_host = "111122223333.dkr.ecr.ap-southeast-1.amazonaws.com"

    with (
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
        patch("subprocess.run", return_value=aws_proc),
    ):
        result = _resolve_auth_for_host(ecr_host)

    expected = base64.b64encode(b"AWS:ecr-password").decode()
    assert result == f"Basic {expected}"


def test_resolve_auth_none_when_everything_fails(tmp_path: Path) -> None:
    """No config, no helper, no AWS → None (graceful fallback to anonymous)."""
    _docker_cfg(tmp_path, {})
    with (
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        result = _resolve_auth_for_host("private.registry.example.com")

    assert result is None


# ---------------------------------------------------------------------------
# Integration: _fetch_tags flows helper creds through bearer challenge
# ---------------------------------------------------------------------------


def test_fetch_tags_uses_cred_helper_for_private_registry(tmp_path: Path) -> None:
    """_fetch_tags: credential-helper auth is used when bearer token is needed."""
    cred_b64 = base64.b64encode(b"myuser:mytoken").decode()
    _docker_cfg(tmp_path, {"credHelpers": {"private.reg.io": "myhelper"}})

    helper_proc = MagicMock(spec=subprocess.CompletedProcess)
    helper_proc.returncode = 0
    helper_proc.stdout = json.dumps({"Username": "myuser", "Secret": "mytoken"})

    # Sequence: probe /v2/ → 401 challenge, token fetch, then tags/list
    probe_error = _make_http_error(
        401,
        headers={
            "WWW-Authenticate": 'Bearer realm="https://private.reg.io/token",service="private.reg.io"'
        },
    )
    token_resp = _make_http_response(json.dumps({"token": "bearer123"}).encode())
    tags_resp = _make_http_response(json.dumps({"tags": ["1.0", "1.1"]}).encode())

    call_n = 0

    def http_side_effect(req: Any, timeout: int = 10) -> Any:
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise probe_error
        if call_n == 2:
            # Verify Basic auth forwarded to token endpoint
            assert req.get_header("Authorization") == f"Basic {cred_b64}"
            return token_resp
        return tags_resp

    with (
        patch("urllib.request.urlopen", side_effect=http_side_effect),
        patch("subprocess.run", return_value=helper_proc),
        patch.dict("os.environ", {"DOCKER_CONFIG": str(tmp_path)}),
    ):
        tags = _fetch_tags("private.reg.io/myapp/img")

    assert tags == ["1.0", "1.1"]


def test_check_container_ecr_private_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ECR image: aws ecr get-login-password is used when no docker config."""
    ecr_host = "123456789012.dkr.ecr.us-west-2.amazonaws.com"
    (tmp_path / "Dockerfile").write_text(f"FROM {ecr_host}/myapp:1.0\n")

    aws_proc = MagicMock(spec=subprocess.CompletedProcess)
    aws_proc.returncode = 0
    aws_proc.stdout = "ecr-password\n"

    # Empty docker config (no auths, no helpers configured)
    (tmp_path / ".docker").mkdir()
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / ".docker"))
    (tmp_path / ".docker" / "config.json").write_text("{}")

    with (
        patch("subprocess.run", return_value=aws_proc),
        patch(
            "repo_ops_container.container._fetch_tags",
            return_value=["1.0", "1.1", "1.2"],
        ),
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].to == "1.2"
