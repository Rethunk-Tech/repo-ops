"""Lightweight tests for discovery module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_core.discovery import discover_repos
from repo_ops_core.walk import iter_scanned_dirs
from repo_ops_upgrade.discovery import (
    DiscoveryDiagnostics,
    cargo_workspace_members,
    discover_all,
)


def test_discover_repos_skips_non_dirs_and_returns_dict(capsys):
    result = discover_repos(["/nonexistent/path/that/does/not/exist"])
    assert isinstance(result, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())
    err = capsys.readouterr().err
    assert "root is not a directory" in err
    assert "nonexistent/path/that/does/not/exist" in err


def test_discover_repos_with_tmp_layout_and_monkeypatch(tmp_path: Path):
    repo_dir = tmp_path / "myorg" / "myrepo"
    (repo_dir / ".git").mkdir(parents=True)
    common = str((repo_dir / ".git").resolve())

    def fake_git_common_dir(p):
        return common

    with patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir):
        result = discover_repos([str(tmp_path)])
    assert isinstance(result, dict)
    assert common in result
    assert result[common] == str(repo_dir.resolve())


def test_discover_all_finds_repo_below_org_proj_depth(tmp_path: Path, init_git_repo):
    """A repo three levels below the root (``<root>/com.github/<org>/<repo>``)
    must be discovered in non-flat mode."""
    repo = tmp_path / "com.github" / "Acme" / "widget"
    init_git_repo(repo)
    (repo / "pyproject.toml").write_text("[project]\nname = 'widget'\n")
    (repo / "uv.lock").write_text("")

    found = discover_all([str(tmp_path)])

    paths = {ws.path for ws in found}
    assert repo.resolve() in paths, f"deep repo not discovered; found={paths}"


def test_discover_all_enumerates_multiple_and_subdir_workspaces(tmp_path: Path, init_git_repo):
    """A single repo with a Go module at its root AND a node app in ``web/``
    yields BOTH workspaces, and a repo whose only manifest sits in a subdir is
    still discovered."""
    org = tmp_path / "com.github" / "Acme"

    multi = org / "kui"
    init_git_repo(multi)
    (multi / "go.mod").write_text("module example.com/kui\n\ngo 1.22\n")
    (multi / "web").mkdir()
    (multi / "web" / "package.json").write_text('{"name":"kui-web"}')
    (multi / "web" / "yarn.lock").write_text("")

    subdir_only = org / "interviewer"
    init_git_repo(subdir_only)
    (subdir_only / "web").mkdir()
    (subdir_only / "web" / "package.json").write_text('{"name":"iv-web"}')
    (subdir_only / "web" / "yarn.lock").write_text("")

    found = discover_all([str(tmp_path)])
    by_path = {ws.path: ws for ws in found}

    assert (multi).resolve() in by_path, "root Go module not found"
    assert by_path[(multi).resolve()].ecosystem == "go"
    assert (multi / "web").resolve() in by_path, "web/ node app not found"
    assert (subdir_only / "web").resolve() in by_path, "subdir-only repo missed"


def test_discover_all_skips_go_mod_without_module_directive(tmp_path: Path, init_git_repo):
    """A go.mod holding only a ``go`` line (a pattern artifact) is not a module."""
    repo = tmp_path / "com.github" / "Acme" / "canon"
    init_git_repo(repo)
    (repo / "go.mod").write_text("go 1.27.1\n")

    diag = DiscoveryDiagnostics()
    assert discover_all([str(tmp_path)], diagnostics=diag) == []
    assert any("no module directive" in s["reason"] for s in diag.skipped)


def test_discover_all_suppresses_uv_workspace_members(tmp_path: Path, init_git_repo):
    """uv workspace members (governed by the root ``uv.lock``) must NOT each
    surface as standalone workspaces."""
    repo = tmp_path / "com.github" / "Acme" / "monorepo"
    init_git_repo(repo)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'root'\n\n[tool.uv.workspace]\nmembers = ['packages/*']\n"
    )
    (repo / "uv.lock").write_text("")
    member = repo / "packages" / "lib"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        "[project]\nname = 'lib'\n\n[build-system]\nbuild-backend = 'uv_build'\n"
    )

    found = discover_all([str(tmp_path)])
    py = [ws for ws in found if ws.ecosystem == "py"]
    assert len(py) == 1, f"expected only the uv root, got {[str(w.path) for w in py]}"
    assert py[0].path == repo.resolve()


def test_discover_all_records_skip_for_unactionable_manifest(tmp_path: Path, init_git_repo):
    """A manifest found but not actionable is recorded as a counted skip with a
    reason, never silently dropped."""
    repo = tmp_path / "com.github" / "Acme" / "nolock"
    init_git_repo(repo)
    # package.json with neither a lockfile nor a packageManager field.
    (repo / "package.json").write_text('{"name":"nolock"}')

    diags = DiscoveryDiagnostics()
    found = discover_all([str(tmp_path)], diagnostics=diags)

    assert not [ws for ws in found if ws.path == repo.resolve()]
    assert any(
        s["path"] == str(repo.resolve()) and s["ecosystem"] == "node" for s in diags.skipped
    ), f"unactionable manifest not recorded as skip; skipped={diags.skipped}"


def test_discover_all_records_skip_for_requirements_txt_only(tmp_path: Path, init_git_repo):
    """A repo with requirements.txt but no requirements.in
    must be recorded as a discovery skip (unactionable), NOT silently ok.

    pip-tools cannot compute upgrades without requirements.in.
    """
    repo = tmp_path / "com.github" / "Acme" / "legacy"
    init_git_repo(repo)
    (repo / "requirements.txt").write_text("requests==2.31.0\n")

    diags = DiscoveryDiagnostics()
    found = discover_all([str(tmp_path)], diagnostics=diags)

    py_found = [ws for ws in found if ws.ecosystem == "py"]
    assert py_found == [], (
        f"requirements.txt-only repo must not produce a workspace; got {py_found}"
    )
    assert any(
        s["path"] == str(repo.resolve())
        and s["ecosystem"] == "py"
        and "requirements.in" in s["reason"]
        for s in diags.skipped
    ), f"expected a py skip with requirements.in mention; skipped={diags.skipped}"


def test_iter_scanned_dirs_skips_node_modules(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "package.json").write_text("{}")
    (root / "package.json").write_text("{}")
    dirs = [mdir for mdir, _ in iter_scanned_dirs(root)]
    assert root in dirs
    assert not any("node_modules" in str(p) for p in dirs)


def test_iter_scanned_dirs_skips_wailsjs(tmp_path: Path) -> None:
    """Regression: generated Wails wailsjs/ binding dirs must be pruned by the walker."""
    from repo_ops_core.constants import SKIP_DIRS

    assert "wailsjs" in SKIP_DIRS, "wailsjs must be in SKIP_DIRS"
    root = tmp_path / "myapp"
    # Simulate a Wails app layout: root package.json + generated wailsjs/runtime/package.json
    (root / "wailsjs" / "runtime").mkdir(parents=True)
    (root / "wailsjs" / "runtime" / "package.json").write_text(
        '{"name":"@wailsapp/runtime"}', encoding="utf-8"
    )
    (root / "package.json").write_text('{"name":"myapp"}', encoding="utf-8")
    dirs = [mdir for mdir, _ in iter_scanned_dirs(root)]
    assert root in dirs, "root must be yielded"
    assert not any("wailsjs" in str(p) for p in dirs), f"wailsjs/ must be pruned; dirs={dirs}"


def test_discover_node_workspace_member_not_recorded_as_skip(tmp_path: Path, init_git_repo):
    """A yarn/npm workspace member (no own lockfile, no
    packageManager field) must be silently suppressed, NOT recorded as a skip."""
    repo = tmp_path / "com.github" / "Acme" / "frontend"
    init_git_repo(repo)
    # Root package.json with yarn workspace declaration and a yarn.lock.
    (repo / "package.json").write_text(
        '{"name":"root","workspaces":["packages/*"]}',
    )
    (repo / "yarn.lock").write_text("")
    member = repo / "packages" / "ui"
    member.mkdir(parents=True)
    # Member has package.json but no lockfile and no packageManager field.
    (member / "package.json").write_text('{"name":"ui"}')

    diags = DiscoveryDiagnostics()
    found = discover_all([str(tmp_path)], diagnostics=diags)

    member_skips = [
        s for s in diags.skipped if Path(s["path"]) == member.resolve() and s["ecosystem"] == "node"
    ]
    assert member_skips == [], f"workspace member must not be recorded as skip; got {member_skips}"
    node_found = [ws for ws in found if ws.ecosystem == "node"]
    assert len(node_found) == 1, (
        f"expected only the workspace root; found={[str(w.path) for w in node_found]}"
    )
    assert node_found[0].path == repo.resolve()


def test_discover_cargo_workspace_members_not_enumerated_standalone(tmp_path: Path, init_git_repo):
    """Cargo workspace members must NOT surface as separate
    rust workspaces; only the root (which owns Cargo.lock) should be returned."""
    repo = tmp_path / "com.github" / "Acme" / "crates"
    init_git_repo(repo)
    # Root: Cargo workspace with Cargo.lock.
    (repo / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n\n[package]\nname = "root"\nversion = "0.1.0"\n'
    )
    (repo / "Cargo.lock").write_text("")
    member = repo / "crates" / "util"
    member.mkdir(parents=True)
    # Member: Cargo.toml with [package] but no own Cargo.lock.
    (member / "Cargo.toml").write_text('[package]\nname = "util"\nversion = "0.1.0"\n')

    found = discover_all([str(tmp_path)])
    rust_found = [ws for ws in found if ws.ecosystem == "rust"]

    assert len(rust_found) == 1, (
        f"expected only the cargo workspace root; found={[str(w.path) for w in rust_found]}"
    )
    assert rust_found[0].path == repo.resolve()


def test_discover_cargo_collapses_excluded_and_implicit_path_members(
    tmp_path: Path,
    init_git_repo,
):
    """Cargo metadata must collapse path-dependency members and exclusions."""
    repo = tmp_path / "com.github" / "Acme" / "cargo-layout"
    init_git_repo(repo)
    (repo / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/app"]\nexclude = ["crates/excluded"]\nresolver = "2"\n'
    )

    app = repo / "crates" / "app"
    (app / "src").mkdir(parents=True)
    (app / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        '[dependencies]\nshared = { path = "../shared" }\n'
    )
    (app / "src" / "lib.rs").write_text("")

    shared = repo / "crates" / "shared"
    (shared / "src").mkdir(parents=True)
    (shared / "Cargo.toml").write_text('[package]\nname = "shared"\nversion = "0.1.0"\n')
    (shared / "src" / "lib.rs").write_text("")

    excluded = repo / "crates" / "excluded"
    (excluded / "src").mkdir(parents=True)
    (excluded / "Cargo.toml").write_text('[package]\nname = "excluded"\nversion = "0.1.0"\n')
    (excluded / "src" / "lib.rs").write_text("")

    found = discover_all([str(tmp_path)])
    rust_found = [ws for ws in found if ws.ecosystem == "rust"]

    assert [ws.path for ws in rust_found] == [repo.resolve()]


def test_discover_cargo_missing_binary_falls_back_to_member_globs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init_git_repo,
):
    """Manifest member globs remain usable when Cargo is unavailable."""
    repo = tmp_path / "com.github" / "Acme" / "cargo-fallback"
    init_git_repo(repo)
    (repo / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n')
    member = repo / "crates" / "util"
    (member / "src").mkdir(parents=True)
    (member / "Cargo.toml").write_text('[package]\nname = "util"\nversion = "0.1.0"\n')
    (member / "src" / "lib.rs").write_text("")
    monkeypatch.setattr("repo_ops_upgrade.discovery.shutil.which", lambda _: None)

    found = discover_all([str(tmp_path)])
    rust_found = [ws for ws in found if ws.ecosystem == "rust"]

    assert [ws.path for ws in rust_found] == [repo.resolve()]


def test_discover_cargo_missing_binary_expands_path_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init_git_repo,
):
    """Fallback discovery recursively collapses path dependencies."""
    repo = tmp_path / "com.github" / "Acme" / "cargo-path-fallback"
    init_git_repo(repo)
    (repo / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/app"]\n')

    app = repo / "crates" / "app"
    (app / "src").mkdir(parents=True)
    (app / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n\n'
        '[dependencies]\nshared = { path = "../shared" }\n'
    )
    (app / "src" / "lib.rs").write_text("")

    shared = repo / "crates" / "shared"
    (shared / "src").mkdir(parents=True)
    (shared / "Cargo.toml").write_text(
        '[package]\nname = "shared"\nversion = "0.1.0"\n\n'
        '[dependencies]\nnested = { path = "../nested" }\n'
    )
    (shared / "src" / "lib.rs").write_text("")

    nested = repo / "crates" / "nested"
    (nested / "src").mkdir(parents=True)
    (nested / "Cargo.toml").write_text('[package]\nname = "nested"\nversion = "0.1.0"\n')
    (nested / "src" / "lib.rs").write_text("")
    monkeypatch.setattr("repo_ops_upgrade.discovery.shutil.which", lambda _: None)

    found = discover_all([str(tmp_path)])
    rust_found = [ws for ws in found if ws.ecosystem == "rust"]

    assert [ws.path for ws in rust_found] == [repo.resolve()]


def test_cargo_metadata_timeout_falls_back_to_manifest_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A timed-out metadata call uses the manifest fallback."""
    repo = tmp_path / "cargo-timeout"
    repo.mkdir()
    (repo / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n')
    monkeypatch.setattr(
        "repo_ops_upgrade.discovery.shutil.which",
        lambda _: "/usr/bin/cargo",
    )

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=60)

    monkeypatch.setattr("repo_ops_upgrade.discovery.subprocess.run", timeout)

    assert cargo_workspace_members(repo) == {"crates/*"}


def test_discover_repos_git_common_dir_timeout_skips_and_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TimeoutExpired from git_common_dir must be caught,
    emit a WARN to stderr, and allow discover_repos to continue (not raise)."""
    repo_dir = tmp_path / "myorg" / "myrepo"
    (repo_dir / ".git").mkdir(parents=True)

    def fake_git_common_dir(p: object) -> str:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=10)

    with patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir):
        result = discover_repos([str(tmp_path)])

    assert result == {}, "timed-out repo must be skipped (not added)"
    captured = capsys.readouterr()
    assert "WARN" in captured.err and "timeout" in captured.err, (
        f"expected WARN+timeout on stderr; got: {captured.err!r}"
    )


def test_discover_repos_find_nonzero_with_stdout_still_discovers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When find exits non-zero (e.g. permission-denied subdir)
    but still prints .git paths to stdout, those repos must be discovered and a
    WARN must be emitted to stderr."""
    repo_dir = tmp_path / "myorg" / "myrepo"
    (repo_dir / ".git").mkdir(parents=True)
    git_path = str(repo_dir / ".git")
    common = str((repo_dir / ".git").resolve())

    def fake_git_common_dir(p: object) -> str:
        return common

    mock_run = MagicMock(returncode=1, stdout=f"{git_path}\n")

    with (
        patch("repo_ops_core.discovery.subprocess.run", return_value=mock_run),
        patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir),
    ):
        result = discover_repos([str(tmp_path)])

    assert common in result, "repo must be discovered despite find exit code 1"
    captured = capsys.readouterr()
    assert "WARN" in captured.err, (
        f"expected WARN on stderr for non-zero find exit; got: {captured.err!r}"
    )


def test_discover_repos_git_common_dir_fnf_skips_and_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FileNotFoundError from git_common_dir (repo vanished
    between find and git_common_dir) must be caught, emit a WARN, and allow the
    scan to continue — other repos in the same root must still be returned."""
    # Two repos under the same root.
    vanished_dir = tmp_path / "myorg" / "vanished"
    (vanished_dir / ".git").mkdir(parents=True)
    survivor_dir = tmp_path / "myorg" / "survivor"
    (survivor_dir / ".git").mkdir(parents=True)
    survivor_common = str((survivor_dir / ".git").resolve())

    def fake_git_common_dir(p: object) -> str:
        if "vanished" in str(p):
            raise FileNotFoundError(f"git -C target is not a directory: {p}")
        return survivor_common

    with patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir):
        result = discover_repos([str(tmp_path)])

    assert survivor_common in result, "surviving repo must still be discovered"
    assert len(result) == 1, f"only the survivor should be in result; got {result}"
    captured = capsys.readouterr()
    assert "WARN" in captured.err, (
        f"expected WARN on stderr for vanished repo; got: {captured.err!r}"
    )


def test_workspace_member_match_is_separator_bound():
    """Member prefix matching must respect a path boundary.

    A sibling dir (``packages/foobar``) must not be matched as a child of the
    member pattern ``packages/foo``; the same for ``apps`` vs ``apps-legacy``.
    Real children must still match.
    """
    from repo_ops_core.filesystem import is_workspace_member as is_child

    root = Path("/r")

    # Exact-name member: sibling rejected, exact + nested accepted.
    assert is_child(root / "packages/foo", root, {"packages/foo"}) is True
    assert is_child(root / "packages/foo/sub", root, {"packages/foo"}) is True
    assert is_child(root / "packages/foobar", root, {"packages/foo"}) is False

    # Bare member with no glob: 'apps' must not match 'apps-legacy'.
    assert is_child(root / "apps", root, {"apps"}) is True
    assert is_child(root / "apps-legacy", root, {"apps"}) is False

    # Glob member: real children still match.
    assert is_child(root / "packages/foo", root, {"packages/*"}) is True
    assert is_child(root / "packages/foobar", root, {"packages/*"}) is True
