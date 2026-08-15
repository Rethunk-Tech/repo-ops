"""Container image pin hold enforcement."""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from repo_ops_container import container
from repo_ops_core.holds import HoldRule


def _dockerfile(
    tmp_path, ref: str = "1.0.0", image: str = "docker.io/library/node"
) -> object:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM {image}:{ref}\n",
        encoding="utf-8",
    )
    return dockerfile


def _held_rule(**overrides) -> HoldRule:
    defaults = {
        "package": "docker.io/library/node",
        "max_version": "1.4",
        "ecosystem": "container",
    }
    defaults.update(overrides)
    return HoldRule(**defaults)


@contextmanager
def _held_holds():
    with (
        patch.object(
            container,
            "_global_holds_cached",
            return_value=(_held_rule(),),
        ),
        patch.object(container, "_repo_holds_cached", return_value=()),
    ):
        yield


def test_check_container_surfaces_clamp_when_upstream_latest_exceeds_ceiling(
    tmp_path,
    make_workspace,
) -> None:
    _dockerfile(tmp_path, ref="1.5.0")
    ws = make_workspace(
        tmp_path, ecosystem="container", manager="container", label="repo"
    )

    with (
        _held_holds(),
        patch.object(
            container,
            "_fetch_tags",
            return_value=["1.0.0", "1.4.0", "1.5.0", "1.6.0"],
        ),
    ):
        changes = container.check_container(ws)

    assert len(changes) == 1
    assert changes[0].name == "docker.io/library/node:1.5.0"
    assert changes[0].from_ver == "1.5.0"
    assert changes[0].to == "1.4.0"


def test_check_container_matches_fully_qualified_hold_for_short_image(
    tmp_path,
    make_workspace,
) -> None:
    _dockerfile(tmp_path, ref="1.5.0", image="node")
    ws = make_workspace(
        tmp_path, ecosystem="container", manager="container", label="repo"
    )

    with (
        _held_holds(),
        patch.object(
            container,
            "_fetch_tags",
            return_value=["1.0.0", "1.4.0", "1.5.0"],
        ),
    ):
        changes = container.check_container(ws)

    assert len(changes) == 1
    assert changes[0].name == "node:1.5.0"
    assert changes[0].from_ver == "1.5.0"
    assert changes[0].to == "1.4.0"


def test_check_container_filters_digest_refresh_above_ceiling(
    tmp_path, monkeypatch, make_workspace
) -> None:
    digest = "a" * 64
    _dockerfile(tmp_path, ref=f"1.5.0@sha256:{digest}")
    ws = make_workspace(
        tmp_path, ecosystem="container", manager="container", label="repo"
    )
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "1")

    with (
        _held_holds(),
        patch.object(
            container,
            "_fetch_manifest_digest",
            return_value=f"sha256:{'b' * 64}",
        ),
    ):
        assert container.check_container(ws) == []


def test_apply_container_clamps_tag_and_digest_to_ceiling(
    tmp_path, monkeypatch, make_workspace
) -> None:
    old_digest = "a" * 64
    new_digest = f"sha256:{'b' * 64}"
    dockerfile = _dockerfile(tmp_path, ref=f"1.5.0@sha256:{old_digest}")
    ws = make_workspace(
        tmp_path, ecosystem="container", manager="container", label="repo"
    )
    monkeypatch.delenv("REPO_OPS_CONTAINER_REFRESH_SHA", raising=False)

    with (
        _held_holds(),
        patch.object(
            container,
            "_fetch_tags",
            return_value=["1.0.0", "1.4.0", "1.5.0"],
        ),
        patch.object(
            container,
            "_fetch_manifest_digest",
            return_value=new_digest,
        ),
    ):
        container.apply_container(ws)

    assert dockerfile.read_text(encoding="utf-8") == (
        f"FROM docker.io/library/node:1.4.0@{new_digest}\n"
    )


def test_apply_container_raises_when_no_tag_satisfies_ceiling(
    tmp_path, make_workspace
) -> None:
    _dockerfile(tmp_path, ref="1.5.0")
    ws = make_workspace(
        tmp_path, ecosystem="container", manager="container", label="repo"
    )

    with (
        _held_holds(),
        patch.object(
            container,
            "_fetch_tags",
            return_value=["1.5.0", "1.6.0"],
        ),
        pytest.raises(
            RuntimeError,
            match=r"hold: failed to clamp docker\.io/library/node back to <=1\.4 \(container\)",
        ),
    ):
        container.apply_container(ws)


def test_apply_container_ignores_expired_hold_without_rewriting_config(
    tmp_path,
    make_workspace,
) -> None:
    dockerfile = _dockerfile(tmp_path, ref="1.5.0")
    hold_config = tmp_path / ".repo-ops.toml"
    hold_config.write_text(
        '[[hold]]\npackage = "docker.io/library/node"\nmax_version = "1.0"\n'
        'until = "2020-01-01"\necosystem = "container"\n',
        encoding="utf-8",
    )
    original_hold_config = hold_config.read_bytes()
    ws = make_workspace(
        tmp_path, ecosystem="container", manager="container", label="repo"
    )

    with patch.object(
        container,
        "_fetch_tags",
        return_value=["1.4.0", "1.5.0"],
    ):
        container.apply_container(ws)

    assert dockerfile.read_text(encoding="utf-8") == (
        "FROM docker.io/library/node:1.5.0\n"
    )
    assert hold_config.read_bytes() == original_hold_config
