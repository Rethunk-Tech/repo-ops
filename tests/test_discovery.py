"""Lightweight tests for discovery module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_core.discovery import FIND_TIMEOUT_ENV, discover_repos
from repo_ops_core.walk import iter_scanned_dirs, manifest_kinds
from repo_ops_upgrade.discovery import DiscoveryDiagnostics, discover_all


def test_discover_repos_skips_non_dirs_and_returns_dict():
    result = discover_repos(["/nonexistent/path/that/does/not/exist"])
    assert isinstance(result, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())


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


def test_manifest_kinds_detects_ecosystems():
    assert manifest_kinds({"package.json"}) == ["node"]
    assert manifest_kinds({"go.mod"}) == ["go"]
    assert manifest_kinds({"Cargo.toml"}) == ["rust"]
    assert manifest_kinds({"requirements.txt"}) == ["py"]
    assert manifest_kinds(set()) == []


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_discover_all_finds_repo_below_org_proj_depth(tmp_path: Path):
    """Regression for #23: a repo sitting deeper than the old two-level
    ``<root>/<org>/<proj>`` walk (here ``<root>/com.github/<org>/<repo>``,
    three levels down) must still be discovered in non-flat mode."""
    repo = tmp_path / "com.github" / "Acme" / "widget"
    _git_init(repo)
    (repo / "pyproject.toml").write_text("[project]\nname = 'widget'\n")
    (repo / "uv.lock").write_text("")

    found = discover_all([str(tmp_path)], flat=False)

    paths = {ws.path for ws in found}
    assert repo.resolve() in paths, f"deep repo not discovered; found={paths}"


def test_discover_all_enumerates_multiple_and_subdir_workspaces(tmp_path: Path):
    """Regression for #24 (points 3 + subdir-only): a single repo with a Go
    module at its root AND a node app in ``web/`` yields BOTH workspaces, and a
    repo whose only manifest sits in a subdir is still discovered."""
    org = tmp_path / "com.github" / "Acme"

    multi = org / "kui"
    _git_init(multi)
    (multi / "go.mod").write_text("module example.com/kui\n\ngo 1.22\n")
    (multi / "web").mkdir()
    (multi / "web" / "package.json").write_text('{"name":"kui-web"}')
    (multi / "web" / "yarn.lock").write_text("")

    subdir_only = org / "interviewer"
    _git_init(subdir_only)
    (subdir_only / "web").mkdir()
    (subdir_only / "web" / "package.json").write_text('{"name":"iv-web"}')
    (subdir_only / "web" / "yarn.lock").write_text("")

    found = discover_all([str(tmp_path)], flat=False)
    by_path = {ws.path: ws for ws in found}

    assert (multi).resolve() in by_path, "root Go module not found"
    assert by_path[(multi).resolve()].ecosystem == "go"
    assert (multi / "web").resolve() in by_path, "web/ node app not found"
    assert (subdir_only / "web").resolve() in by_path, "subdir-only repo missed"


def test_discover_all_suppresses_uv_workspace_members(tmp_path: Path):
    """Regression for #24 (explosion guard): uv workspace members (governed by
    the root ``uv.lock``) must NOT each surface as standalone workspaces."""
    repo = tmp_path / "com.github" / "Acme" / "monorepo"
    _git_init(repo)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'root'\n\n[tool.uv.workspace]\nmembers = ['packages/*']\n"
    )
    (repo / "uv.lock").write_text("")
    member = repo / "packages" / "lib"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        "[project]\nname = 'lib'\n\n[build-system]\nbuild-backend = 'uv_build'\n"
    )

    found = discover_all([str(tmp_path)], flat=False)
    py = [ws for ws in found if ws.ecosystem == "py"]
    assert len(py) == 1, f"expected only the uv root, got {[str(w.path) for w in py]}"
    assert py[0].path == repo.resolve()


def test_discover_all_records_skip_for_unactionable_manifest(tmp_path: Path):
    """Regression for #24 (point 2): a manifest found but not actionable is
    recorded as a counted skip with a reason, never silently dropped."""
    repo = tmp_path / "com.github" / "Acme" / "nolock"
    _git_init(repo)
    # package.json with neither a lockfile nor a packageManager field.
    (repo / "package.json").write_text('{"name":"nolock"}')

    diags = DiscoveryDiagnostics()
    found = discover_all([str(tmp_path)], flat=False, diagnostics=diags)

    assert not [ws for ws in found if ws.path == repo.resolve()]
    assert any(
        s["path"] == str(repo.resolve()) and s["ecosystem"] == "node"
        for s in diags.skipped
    ), f"unactionable manifest not recorded as skip; skipped={diags.skipped}"


def test_discover_all_finds_requirements_txt_only_repo(tmp_path: Path):
    """Regression for #32: a repo whose only Python manifest is requirements.txt
    must be discovered as a pip-tools workspace in non-flat mode.  The trigger
    set on the dir-iter loop previously omitted 'requirements.txt', so these
    repos were silently ignored before consider_py was ever reached."""
    repo = tmp_path / "com.github" / "Acme" / "legacy"
    _git_init(repo)
    (repo / "requirements.txt").write_text("requests==2.31.0\n")

    found = discover_all([str(tmp_path)], flat=False)
    py = [ws for ws in found if ws.ecosystem == "py"]

    assert len(py) == 1, f"expected one py workspace, got {[str(w.path) for w in py]}"
    assert py[0].path == repo.resolve()
    assert py[0].manager == "pip-tools"


def test_iter_scanned_dirs_skips_node_modules(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "package.json").write_text("{}")
    (root / "package.json").write_text("{}")
    dirs = [mdir for mdir, _ in iter_scanned_dirs(root)]
    assert root in dirs
    assert not any("node_modules" in str(p) for p in dirs)


@pytest.mark.parametrize(
    "env_val, param_val, expected",
    [
        # default: no env, no param → 60
        (None, None, 60.0),
        # env var overrides default
        ("30", None, 30.0),
        # explicit param overrides default
        (None, 5.0, 5.0),
        # explicit param wins over env var (param takes precedence)
        ("30", 10.0, 10.0),
        # invalid env var is silently ignored → default 60
        ("not-a-number", None, 60.0),
        # non-positive env var is ignored → default 60
        ("0", None, 60.0),
    ],
)
def test_discover_repos_find_timeout_is_tunable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_val: str | None,
    param_val: float | None,
    expected: float,
) -> None:
    """Setting REPO_OPS_FIND_TIMEOUT env var or timeout= param changes the
    subprocess timeout; explicit param takes precedence; default stays 60 s."""
    # Ensure the env var is absent unless the test case sets it
    monkeypatch.delenv(FIND_TIMEOUT_ENV, raising=False)
    if env_val is not None:
        monkeypatch.setenv(FIND_TIMEOUT_ENV, env_val)

    mock_run = MagicMock()
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    kwargs: dict = {}
    if param_val is not None:
        kwargs["timeout"] = param_val

    with patch("repo_ops_core.discovery.subprocess.run", mock_run):
        discover_repos([str(tmp_path)], **kwargs)

    mock_run.assert_called_once()
    actual_timeout = mock_run.call_args.kwargs["timeout"]
    assert actual_timeout == expected, (
        f"env={env_val!r} param={param_val!r}: expected timeout={expected}, got {actual_timeout}"
    )
