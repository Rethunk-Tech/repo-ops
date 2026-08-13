"""Lightweight unit tests for inventory counting helpers."""

from pathlib import Path

import pytest
from repo_ops_inventory.counting import (
    _python_edges_for_mdir,
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
    (tmp_path / "Cargo.lock").write_text(
        "[[package]]\n"
        'name = "a"\n'
        'version = "1.0"\n'
        'dependencies = [{ name = "nested", version = "9.9" }]\n'
    )
    d, v = count_rust(tmp_path)
    assert d == 1
    assert "a@1.0" in v
    assert "nested@9.9" not in v

    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["b"]')
    d2, _v2, tag = count_python(tmp_path)
    assert d2 >= 1
    assert "pyproject" in tag


def test_count_rust_workspace_dependencies(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text(
        "[workspace.dependencies]\n"
        'workspace-only = "1"\n'
        'shared = "1"\n'
        "[dependencies]\n"
        "shared = { workspace = true }\n"
        'package-only = "1"\n'
    )
    direct, _versions = count_rust(tmp_path)
    assert direct == 3


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


def test_count_pdm_lock_edges(tmp_path: Path):
    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.32.0"\n'
    )
    _direct, versions, _tag = count_python(tmp_path)
    assert versions == {"requests@2.32.0"}
    assert count_dep_edges(tmp_path) == len(versions)
    assert _python_edges_for_mdir(tmp_path) == len(versions)


def test_count_pipenv_lock_edges(tmp_path: Path):
    (tmp_path / "Pipfile.lock").write_text(
        '{"default": {"flask": {"version": "==3.0.0"}}, "develop": {}}'
    )
    _direct, versions, _tag = count_python(tmp_path)
    assert versions == {"flask@3.0.0"}
    assert count_dep_edges(tmp_path) == len(versions)
    assert _python_edges_for_mdir(tmp_path) == len(versions)


def test_count_python_pinned_requirements_versions(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("foo==1.2.3\nbar>=2.0\n")
    (tmp_path / "requirements.in").write_text("unpinned\n")
    _direct, versions, _tag = count_python(tmp_path)
    assert versions == {"foo@1.2.3"}


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


def test_count_dep_edges_skips_vendor_and_yarn(tmp_path: Path) -> None:
    """#96: count_dep_edges must not descend into vendor/ or .yarn/ dirs.

    Layout:
      uv.lock                          <- root lock, 1 edge (counted)
      vendor/
        sub/
          uv.lock                      <- vendored lock, should NOT be counted
      .yarn/
        sub/
          package.json                 <- vendored node manifest, should NOT be counted
    """
    import json

    # Root-level lock with 1 dependency edge
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "root"\nversion = "0.0.1"\ndependencies = [\n    { name = "dep-a" },\n]\n'
    )

    # vendor/ subtree — should be skipped entirely
    vendor_sub = tmp_path / "vendor" / "sub"
    vendor_sub.mkdir(parents=True)
    (vendor_sub / "uv.lock").write_text(
        '[[package]]\nname = "vendored"\nversion = "1.0.0"\ndependencies = [\n    { name = "x" },\n    { name = "y" },\n]\n'
    )

    # .yarn/ subtree — should be skipped entirely
    yarn_sub = tmp_path / ".yarn" / "sub"
    yarn_sub.mkdir(parents=True)
    (yarn_sub / "package.json").write_text(
        json.dumps({"dependencies": {"a": "^1.0", "b": "^2.0", "c": "^3.0"}})
    )

    # Only the 1 root edge must be counted; vendor/ and .yarn/ are pruned
    assert count_dep_edges(tmp_path) == 1


def test_go_cache_roundtrip_and_invalid(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    data = {"k": {"direct": 1, "versions": ["x@1"]}}
    save_go_cache(cache_path, data)
    loaded = load_go_cache(cache_path)
    assert loaded == data

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_go_cache(bad) == {}


# ---------- count_node lockfile precedence + bun.lockb (F014, F085) ----------


def test_count_node_lockfile_precedence(tmp_path: Path) -> None:
    import json

    from repo_ops_inventory.counting import count_node

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"a": "^1.0.0"},
                "devDependencies": {"b": "^2.0.0"},
            }
        )
    )
    # npm wins over pnpm/bun/yarn when package-lock.json is present.
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/a": {"version": "1.0.1"},
                    "node_modules/b": {"version": "2.0.1"},
                }
            }
        )
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (tmp_path / "bun.lock").write_text('{"packages":{}}\n')
    (tmp_path / "yarn.lock").write_text("")
    direct, versions, lock = count_node(tmp_path)
    assert direct == 2
    assert lock == "npm"
    assert "a@1.0.1" in versions
    assert "b@2.0.1" in versions


def test_count_node_bun_lockb_dump_success(tmp_path: Path, monkeypatch) -> None:
    """bun.lockb: successful bun dump → versions via yarn-compatible stdout."""
    import json
    from types import SimpleNamespace

    from repo_ops_inventory import counting

    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "1.3.0"}})
    )
    (tmp_path / "bun.lockb").write_bytes(b"BUN\x00fake-binary")

    yarn_dump = 'left-pad@1.3.0:\n  version "1.3.0"\n'

    def fake_run(cmd, cwd, timeout=300, env=None):
        assert cmd[0] == "bun"
        return SimpleNamespace(returncode=0, stdout=yarn_dump, stderr="")

    monkeypatch.setattr(counting, "run", fake_run)
    direct, versions, lock = counting.count_node(tmp_path)
    assert direct == 1
    assert lock == "bun(bin)"
    assert "left-pad@1.3.0" in versions


def test_count_node_bun_lockb_unsupported_when_bun_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """bun.lockb without bun → explicit bun(bin:unsupported), not silent zeros kind."""
    import json

    from repo_ops_inventory import counting

    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "1.3.0"}})
    )
    (tmp_path / "bun.lockb").write_bytes(b"BUN\x00fake")

    def boom(*_a, **_k):
        raise FileNotFoundError("bun")

    monkeypatch.setattr(counting, "run", boom)
    direct, versions, lock = counting.count_node(tmp_path)
    assert direct == 1
    assert versions == set()
    assert lock == "bun(bin:unsupported)"


def test_count_node_bun_lockb_unsupported_on_invalid_dump(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    from types import SimpleNamespace

    from repo_ops_inventory import counting

    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"a": "1"}}))
    (tmp_path / "bun.lockb").write_bytes(b"BUN\x00")

    monkeypatch.setattr(
        counting,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="", stderr="error parsing lockfile: InvalidLockfile"
        ),
    )
    _d, versions, lock = counting.count_node(tmp_path)
    assert versions == set()
    assert lock == "bun(bin:unsupported)"


# ---------- eco filter recomputes metrics (F080) ----------


def test_filter_recomputes_metrics_for_matching_eco_only(
    tmp_path: Path, capsys
) -> None:
    """A node+precommit project filtered to precommit must drop node counts."""
    import json

    from repo_ops_inventory.scan import main as inventory_main

    proj = tmp_path / "org" / "mixed"
    proj.mkdir(parents=True)
    (proj / "package.json").write_text(
        json.dumps({"dependencies": {"a": "^1", "b": "^2", "c": "^3"}})
    )
    (proj / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/a": {"version": "1.0.0"},
                    "node_modules/b": {"version": "2.0.0"},
                    "node_modules/c": {"version": "3.0.0"},
                }
            }
        )
    )
    (proj / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v4.5.0\n"
        "    hooks: [{id: trailing-whitespace}]\n",
        encoding="utf-8",
    )

    rc = inventory_main([str(tmp_path), "--json", "--filter", "precommit"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["kinds"] == ["precommit"]
    assert row["direct"] == 1
    assert row["versions"] == 1
    assert row["on_disk"] == 0
    # Node edges must not leak into the filtered precommit row.
    assert row["edges"] == 0


# ---------- JSON schema: manifests + summary (F081, F082) ----------


def test_inventory_json_includes_manifests_and_summary(tmp_path: Path, capsys) -> None:
    import json

    from repo_ops_inventory.scan import main as inventory_main

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["a"]\n'
    )
    rc = inventory_main([str(tmp_path), "--flat", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert "manifests" in payload["rows"][0]
    assert payload["rows"][0]["manifests"] >= 1
    assert "manifests" in payload["totals"]
    assert "summary" in payload
    assert payload["summary"]["projects"] == 1
    assert payload["summary"]["direct"] == payload["totals"]["direct"]
    assert payload["summary"]["manifests"] == payload["totals"]["manifests"]


def test_inventory_json_out_requires_json(tmp_path: Path) -> None:
    """F083: --json-out without --json is a parser error."""
    import pytest
    from repo_ops_inventory.scan import main as inventory_main

    with pytest.raises(SystemExit):
        inventory_main([str(tmp_path), "--json-out", str(tmp_path / "out.json")])


def test_inventory_json_out_writes_path_only(tmp_path: Path, capsys) -> None:
    """F087: --json --json-out writes atomically; stdout is the path only."""
    import json

    from repo_ops_inventory.scan import main as inventory_main

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    out_file = tmp_path / "inv.json"
    rc = inventory_main(
        [str(tmp_path), "--flat", "--json", "--json-out", str(out_file)]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(out_file.resolve())
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "summary" in payload
    assert len(payload["rows"]) == 1


def test_inventory_compact_schema_version_first(tmp_path: Path, capsys) -> None:
    """F086: --compact is single-line; schema_version is the first key."""
    import json

    from repo_ops_inventory.scan import main as inventory_main

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    rc = inventory_main([str(tmp_path), "--flat", "--json", "--compact"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0
    payload = json.loads(out)
    assert next(iter(payload)) == "schema_version"
    assert payload["schema_version"] == 1


# ---------- count_go pool failure + sort/top/jobs/no-cache (F084, F173, F174) ----------


def test_scan_go_pool_failure_yields_zero_deps(tmp_path: Path, monkeypatch) -> None:
    """F084: ThreadPoolExecutor swallows count_go exceptions as zero deps."""
    from repo_ops_inventory import counting

    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")

    def boom(*_a, **_k):
        raise RuntimeError("go unavailable")

    monkeypatch.setattr(counting, "count_go", boom)
    rows = counting.scan(tmp_path, flat=True, jobs=2)
    assert len(rows) == 1
    assert rows[0]["direct"] == 0
    assert rows[0]["versions"] == set()
    assert "go" in rows[0]["kinds"]


def test_inventory_sort_direct_descending(tmp_path: Path, capsys) -> None:
    import json

    from repo_ops_inventory.scan import main as inventory_main

    org = tmp_path / "org"
    low = org / "low"
    high = org / "high"
    low.mkdir(parents=True)
    high.mkdir(parents=True)
    (low / "pyproject.toml").write_text('[project]\ndependencies = ["a"]\n')
    (high / "pyproject.toml").write_text('[project]\ndependencies = ["a", "b", "c"]\n')

    rc = inventory_main([str(tmp_path), "--json", "--sort", "direct"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert [r["name"] for r in rows] == ["org/high", "org/low"]
    assert rows[0]["direct"] >= rows[1]["direct"]


@pytest.mark.parametrize(
    ("sort_key", "row_key", "expected_names"),
    [
        ("name", "name", ["org/alpha", "org/zeta"]),
        ("versions", "versions", ["org/zeta", "org/alpha"]),
        ("on-disk", "on_disk", ["org/alpha", "org/zeta"]),
        ("edges", "edges", ["org/zeta", "org/alpha"]),
    ],
)
def test_inventory_sort_remaining_columns(
    tmp_path: Path, capsys, sort_key: str, row_key: str, expected_names: list[str]
) -> None:
    import json

    from repo_ops_inventory.scan import main as inventory_main

    org = tmp_path / "org"
    alpha = org / "alpha"
    zeta = org / "zeta"
    alpha.mkdir(parents=True)
    zeta.mkdir(parents=True)

    (alpha / "package.json").write_text(json.dumps({"dependencies": {"a": "^1.0.0"}}))
    (alpha / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/a": {"version": "1.0.0"}}})
    )
    for package in ("a", "b"):
        package_dir = alpha / "node_modules" / package
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(json.dumps({"name": package}))

    (zeta / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "a": "^1.0.0",
                    "b": "^2.0.0",
                    "c": "^3.0.0",
                }
            }
        )
    )
    (zeta / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/a": {"version": "1.0.0"},
                    "node_modules/b": {"version": "2.0.0"},
                    "node_modules/c": {"version": "3.0.0"},
                }
            }
        )
    )
    package_dir = zeta / "node_modules" / "a"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(json.dumps({"name": "a"}))

    rc = inventory_main([str(tmp_path), "--json", "--sort", sort_key])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert [row["name"] for row in rows] == expected_names
    if sort_key == "name":
        assert rows[0][row_key] <= rows[1][row_key]
    else:
        assert rows[0][row_key] > rows[1][row_key]


def test_inventory_top_truncates_human_output(tmp_path: Path, capsys) -> None:
    from repo_ops_inventory.scan import main as inventory_main

    org = tmp_path / "org"
    for name in ("a", "b", "c"):
        p = org / name
        p.mkdir(parents=True)
        (p / "pyproject.toml").write_text('[project]\nname = "x"\n')

    rc = inventory_main([str(tmp_path), "--top", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "... and 1 more" in out
    assert "TOTAL" in out


def test_inventory_no_cache_bypasses_go_cache(tmp_path: Path, monkeypatch) -> None:
    from repo_ops_inventory import counting
    from repo_ops_inventory.scan import main as inventory_main

    calls = {"load": 0, "save": 0}
    monkeypatch.setattr(
        counting,
        "load_go_cache",
        lambda *_a, **_k: calls.__setitem__("load", calls["load"] + 1) or {},
    )
    monkeypatch.setattr(
        counting,
        "save_go_cache",
        lambda *_a, **_k: calls.__setitem__("save", calls["save"] + 1),
    )
    # Also patch the names imported into scan module.
    import repo_ops_inventory.scan as scan_mod

    monkeypatch.setattr(scan_mod, "load_go_cache", counting.load_go_cache)
    monkeypatch.setattr(scan_mod, "save_go_cache", counting.save_go_cache)

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert inventory_main([str(tmp_path), "--flat", "--no-cache", "--json"]) == 0
    assert calls["load"] == 0
    assert calls["save"] == 0


def test_inventory_jobs_flag_accepted(tmp_path: Path) -> None:
    """F174: --jobs is accepted and forwarded to scan pool sizing."""
    from repo_ops_inventory.scan import build_inventory_parser
    from repo_ops_inventory.scan import main as inventory_main

    args = build_inventory_parser().parse_args(["--jobs", "1"])
    assert args.jobs == 1
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert inventory_main([str(tmp_path), "--flat", "--jobs", "1", "--json"]) == 0


def test_count_python_pipfile_pdm_requirements_in_and_uv_lock(
    tmp_path: Path,
) -> None:
    (tmp_path / "Pipfile").write_text(
        '[packages]\nflask = "*"\nrequests = "*"\n[dev-packages]\npytest = "*"\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pdm.dev-dependencies]\ndev = ["ruff", "mypy"]\ntest = ["pytest"]\n'
    )
    (tmp_path / "requirements.in").write_text(
        "httpx\n\n# local-only note\n-r requirements-base.in\nrich>=13\n"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "httpx"\nversion = "0.28.1"\n'
    )

    direct, versions, tag = count_python(tmp_path)

    assert direct == 8
    assert versions == {"httpx@0.28.1"}
    assert "pipfile" in tag
    assert "req" in tag
    assert "uv" in tag


# ---------- count_python pdm/pipenv (F175) + deno jsonc (F176) ----------


def test_count_python_pdm_and_pipenv_locks(tmp_path: Path) -> None:
    from repo_ops_inventory.counting import count_python

    pdm = tmp_path / "pdm"
    pdm.mkdir()
    (pdm / "pyproject.toml").write_text('[project]\ndependencies = ["requests"]\n')
    (pdm / "pdm.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.32.0"\n'
    )
    d, v, tag = count_python(pdm)
    assert d >= 1
    assert "pdm" in tag
    assert "requests@2.32.0" in v

    pipenv = tmp_path / "pipenv"
    pipenv.mkdir()
    (pipenv / "Pipfile.lock").write_text(
        '{"default": {"flask": {"version": "==3.0.0"}}, "develop": {}}'
    )
    _d2, v2, tag2 = count_python(pipenv)
    assert "pipenv" in tag2
    assert "flask@3.0.0" in v2


def test_count_deno_jsonc_preserves_url_with_slashes(tmp_path: Path) -> None:
    """F176: // inside import URL strings must not be treated as comments."""
    from repo_ops_inventory.counting import count_deno

    (tmp_path / "deno.jsonc").write_text(
        "{\n"
        "  // real comment\n"
        '  "imports": {\n'
        '    "x": "https://example.com/path//pkg"\n'
        "  }\n"
        "}\n"
    )
    direct, _versions = count_deno(tmp_path)
    assert direct == 1
