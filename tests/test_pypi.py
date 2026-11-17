"""Latest-version lookup against the PyPI JSON API."""

import json
from unittest.mock import patch

from repo_ops_py.pypi import pypi_latest

_CONNECTION = "repo_ops_py.pypi.HTTPSConnection"


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


def _latest(package: str, body: bytes | BaseException) -> str | None:
    class FakeConnection:
        def __init__(self, host: str, timeout: float) -> None:
            assert host == "pypi.org"
            assert timeout > 0

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            if isinstance(body, BaseException):
                raise body
            assert method == "GET"
            assert path == f"/pypi/{package}/json"
            assert headers["User-Agent"] == "repo-ops"

        def getresponse(self) -> _FakeResponse:
            assert isinstance(body, bytes)
            return _FakeResponse(body)

        def close(self) -> None:
            pass

    with patch(_CONNECTION, side_effect=FakeConnection):
        return pypi_latest(package)


def test_pypi_latest_network_error_returns_none() -> None:
    assert _latest("requests", OSError("timed out")) is None


def test_pypi_latest_rejects_non_object_payload() -> None:
    assert _latest("requests", b'["not", "an", "object"]') is None


def test_pypi_latest_picks_highest_unyanked_release_without_info_version() -> None:
    body = json.dumps(
        {
            "info": {"version": 1},
            "releases": {
                "1.0.0": [{"yanked": False}],
                "2.0.0": [{"yanked": True}],
                "1.5.0": [{"yanked": True}, {"yanked": False}],
                "0.9.0": "not-a-list",
                "1.1.0": [],
                "1.2.0": ["not-a-file"],
            },
        }
    ).encode("utf-8")
    assert _latest("requests", body) == "1.5.0"


def test_pypi_latest_missing_releases_returns_none() -> None:
    assert _latest("requests", b'{"info": {}}') is None


def test_pypi_latest_all_releases_yanked_returns_none() -> None:
    body = json.dumps({"releases": {"1.0.0": [{"yanked": True}]}}).encode("utf-8")
    assert _latest("requests", body) is None
