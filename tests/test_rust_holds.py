"""Rust wiring for package version-ceiling ("hold") rules."""

import json
from pathlib import Path
from unittest.mock import patch

from repo_ops_rust.rust import (
    _cargo_registry_versions,
    cargo_manifests,
)

_REGISTRY_CONNECTION = "repo_ops_rust.rust.HTTPSConnection"
_CRATE = "serde"


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


def test_cargo_manifests_ignore_member_symlink_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n',
        encoding="utf-8",
    )
    victim = tmp_path / "outside" / "evil" / "Cargo.toml"
    victim.parent.mkdir(parents=True)
    victim.write_text('[package]\nname = "evil"\nversion = "0.1.0"\n', encoding="utf-8")
    (repo / "crates").symlink_to(tmp_path / "outside")

    manifests = cargo_manifests(repo)
    assert victim.resolve() not in {path.resolve() for path in manifests}
