"""Rust wiring for package version-ceiling ("hold") rules."""

import json
from unittest.mock import patch

from repo_ops_core.holds import HoldRule
from repo_ops_rust.rust import (
    _cargo_registry_versions,
)

_GLOBAL_HOLDS_CACHE = "repo_ops_core.holds._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_core.holds._repo_holds_cached"
_REGISTRY_VERSIONS = "repo_ops_rust.rust._cargo_registry_versions"
_REGISTRY_CONNECTION = "repo_ops_rust.rust.HTTPSConnection"
_CRATE = "serde"


def _held_rule(**overrides) -> HoldRule:
    defaults = {"package": _CRATE, "max_version": "1.5"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def _metadata(crate_versions: str | tuple[str, ...]) -> str:
    if isinstance(crate_versions, str):
        crate_versions = (crate_versions,)
    packages = [
        {
            "id": f"registry+https://example.invalid#{_CRATE}@{version}",
            "name": _CRATE,
            "version": version,
        }
        for version in crate_versions
    ]
    return json.dumps(
        {
            "packages": packages,
            "resolve": {"nodes": [{"id": package["id"]} for package in packages]},
        }
    )


def test_cargo_registry_versions_follows_seek_pagination() -> None:
    responses = [
        {
            "versions": [
                {"num": "1.5.7", "yanked": False},
                {"num": "1.5.6", "yanked": True},
            ],
            "meta": {
                "next_page": "?per_page=100&seek=WyIxLjUuNiIsMTIzXQ==",
            },
        },
        {
            "versions": [{"num": "1.5.5", "yanked": False}],
            "meta": {"next_page": None},
        },
    ]
    requests: list[tuple[str, str, dict[str, str]]] = []

    class FakeResponse:
        status = 200

        def __init__(self, document):
            self.document = document

        def read(self):
            return json.dumps(self.document).encode("utf-8")

    class FakeConnection:
        def __init__(self, host, timeout):
            assert host == "crates.io"
            assert timeout > 0

        def request(self, method, path, headers):
            requests.append((method, path, headers))

        def getresponse(self):
            return FakeResponse(responses.pop(0))

        def close(self):
            pass

    with patch(_REGISTRY_CONNECTION, side_effect=FakeConnection):
        versions = _cargo_registry_versions(_CRATE)

    assert [method for method, _, _ in requests] == ["GET", "GET"]
    assert requests[0][1] == "/api/v1/crates/serde/versions?per_page=100"
    assert requests[1][1] == (
        "/api/v1/crates/serde/versions?per_page=100&seek=WyIxLjUuNiIsMTIzXQ=="
    )
    assert "?page=" not in requests[0][1]
    assert "?page=" not in requests[1][1]
    assert requests[0][2]["User-Agent"] == "repo-ops"
    assert set(versions) == {"1.5.7", "1.5.5"}
