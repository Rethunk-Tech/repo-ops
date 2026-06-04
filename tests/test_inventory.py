"""Lightweight unit tests for inventory counting helpers."""

from pathlib import Path

from repo_ops_inventory.counting import (
    count_dep_edges,
    count_installed_node,
    count_python,
    count_rust,
    load_go_cache,
    save_go_cache,
    scan,
)


def test_count_rust_and_python_minimal(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n[dependencies]\na = "1"')
    (tmp_path / "Cargo.lock").write_text('[[package]]\nname = "a"\nversion = "1.0"\n')
    d, v = count_rust(tmp_path)
    assert d == 1
    assert "a@1.0" in v

    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["b"]')
    d2, _v2, tag = count_python(tmp_path)
    assert d2 >= 1
    assert "pyproject" in tag


def test_count_uv_lock_edges(tmp_path: Path):
    (tmp_path / "uv.lock").write_text(
        """[[package]]
name = "root"
version = "0.0.1"

[[package]]
name = "child"
version = "1.0.0"
dependencies = [
    { name = "leaf-a" },
    { name = "leaf-b" },
]
"""
    )
    assert count_dep_edges(tmp_path) == 2


def test_count_pyproject_edges_without_lock(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["a", "b"]\n'
    )
    assert count_dep_edges(tmp_path) == 2


def test_scan_flat_current_directory_name(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    rows = scan(Path("."), flat=True)
    assert len(rows) == 1
    assert rows[0]["name"] == tmp_path.name


def test_scan_python_edges_from_uv_lock(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (tmp_path / "uv.lock").write_text(
        """[[package]]
name = "x"
version = "0.0.1"
dependencies = [
    { name = "dep-one" },
]
"""
    )
    rows = scan(tmp_path, flat=True)
    assert len(rows) == 1
    assert rows[0]["edges"] == 1


def test_scan_non_flat_missing_root_returns_empty(tmp_path: Path):
    missing = tmp_path / "no-such-root"
    assert not missing.exists()
    assert scan(missing, flat=False) == []


def test_nested_node_modules_counting(tmp_path: Path) -> None:
    """#69: count_installed_node descends into nested node_modules.
    #75: count_dep_edges does NOT descend into node_modules.

    Fixture layout:
      package.json          <- root manifest, 2 direct deps
      node_modules/
        pkg-a/
          package.json      <- installed package with 1 dep (nested level 1)
          node_modules/
            pkg-b/
              package.json  <- installed package with 1 dep (nested level 2)
    """
    import json

    # Root manifest with 2 direct dependencies
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"pkg-a": "^1.0.0", "pkg-b": "^2.0.0"}})
    )

    # Nested installed packages, each carrying 1 dep
    nm = tmp_path / "node_modules"
    pkg_a = nm / "pkg-a"
    pkg_a.mkdir(parents=True)
    (pkg_a / "package.json").write_text(
        json.dumps({"name": "pkg-a", "dependencies": {"lodash": "^4.0.0"}})
    )
    pkg_b = pkg_a / "node_modules" / "pkg-b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "package.json").write_text(
        json.dumps({"name": "pkg-b", "dependencies": {"semver": "^7.0.0"}})
    )

    # #69: both nested packages are counted (unfixed = 1, pkg-a only)
    assert count_installed_node(tmp_path) == 2

    # #75: only root's 2 direct deps count; node_modules is pruned entirely
    # (unfixed = 4: root 2 + pkg-a 1 + pkg-b 1)
    assert count_dep_edges(tmp_path) == 2


def test_go_cache_roundtrip_and_invalid(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    data = {"k": {"direct": 1, "versions": ["x@1"]}}
    save_go_cache(cache_path, data)
    loaded = load_go_cache(cache_path)
    assert loaded == data

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_go_cache(bad) == {}
