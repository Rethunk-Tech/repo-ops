"""Container image pin hold enforcement."""

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_container import container
from repo_ops_core.holds import clear_hold_caches


@pytest.fixture(autouse=True)
def _isolated_hold_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the global hold config at a tmp dir and drop the process cache.

    ``_global_holds_cached``/``_repo_holds_cached`` are ``@cache``d for the
    process lifetime, so without this every test after the first would keep
    reusing whatever the first one loaded.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    clear_hold_caches()
    yield
    clear_hold_caches()


def _write_global_hold(tmp_path: Path, **overrides) -> None:
    defaults = {
        "package": "docker.io/library/node",
        "max_version": "1.4",
        "ecosystem": "container",
    }
    defaults.update(overrides)
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir(exist_ok=True)
    lines = ["[[hold]]", *(f'{key} = "{value}"' for key, value in defaults.items())]
    (cfg_dir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dockerfile(tmp_path, ref: str = "1.0.0", image: str = "docker.io/library/node") -> object:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM {image}:{ref}\n",
        encoding="utf-8",
    )
    return dockerfile


def test_check_container_surfaces_clamp_when_upstream_latest_exceeds_ceiling(
    tmp_path,
    make_workspace,
) -> None:
    _dockerfile(tmp_path, ref="1.5.0")
    ws = make_workspace(tmp_path, ecosystem="container", manager="container", label="repo")
    _write_global_hold(tmp_path)

    with patch.object(
        container,
        "_fetch_tags",
        return_value=["1.0.0", "1.4.0", "1.5.0", "1.6.0"],
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
    ws = make_workspace(tmp_path, ecosystem="container", manager="container", label="repo")
    _write_global_hold(tmp_path)

    with patch.object(
        container,
        "_fetch_tags",
        return_value=["1.0.0", "1.4.0", "1.5.0"],
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
    ws = make_workspace(tmp_path, ecosystem="container", manager="container", label="repo")
    _write_global_hold(tmp_path)

    with patch.object(
        container,
        "_fetch_manifest_digest",
        return_value=f"sha256:{'b' * 64}",
    ):
        assert container.check_container(ws) == []


def test_apply_container_raises_when_no_tag_satisfies_ceiling(tmp_path, make_workspace) -> None:
    _dockerfile(tmp_path, ref="1.5.0")
    ws = make_workspace(tmp_path, ecosystem="container", manager="container", label="repo")
    _write_global_hold(tmp_path)

    with (
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
    ws = make_workspace(tmp_path, ecosystem="container", manager="container", label="repo")

    with patch.object(
        container,
        "_fetch_tags",
        return_value=["1.4.0", "1.5.0"],
    ):
        container.apply_container(ws)

    assert dockerfile.read_text(encoding="utf-8") == ("FROM docker.io/library/node:1.5.0\n")
    assert hold_config.read_bytes() == original_hold_config
