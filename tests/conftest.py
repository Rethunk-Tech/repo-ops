"""Shared pytest fixtures for the top-level test suite.

Isolates the persistent state store: ``process_workspace`` records
behind-latest history through ``repo_ops_core.state`` (atexit-flushed to
``$XDG_CACHE_HOME/repo-ops/state.json``), so without a per-test cache dir
any test driving it would pollute the developer's real state file.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_go import vulndb
from repo_ops_models.models import Change, Workspace


@pytest.fixture
def make_change() -> Any:
    """Factory fixture: build a ``Change`` for tests that only care about ``.name``.

    Same conftest/fixture-not-import rationale as ``make_workspace`` above.
    """

    def _factory(name: str, from_ver: str = "1.0.0", to: str = "2.0.0") -> Change:
        return Change(name=name, from_ver=from_ver, to=to)

    return _factory


@pytest.fixture
def make_proc() -> Any:
    """Factory fixture: a real ``subprocess.CompletedProcess`` for ``run()`` patches.

    Stands in for what ``run()`` actually returns — a hand-rolled
    ``MagicMock``/``type()`` shim only coincidentally has the right shape
    and drifts silently if the code under test starts reading another
    ``CompletedProcess`` attribute.
    """

    def _factory(
        returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)

    return _factory


@pytest.fixture
def init_git_repo() -> Any:
    """Factory fixture: ``git init`` + a throwaway committer identity.

    Same conftest/fixture-not-import rationale as ``make_workspace`` above.

    With ``commit=True``, also stages and commits *filename* and returns the
    resulting HEAD SHA; otherwise returns ``None``. The committer identity is
    always configured (even when the caller never commits) because it is
    cheap and lets a test add its own commit later without repeating it.
    """

    def _factory(
        path: Path,
        *,
        commit: bool = False,
        filename: str = "README.md",
        content: str = "init\n",
    ) -> str | None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        if not commit:
            return None
        (path / filename).write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", filename], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return _factory


@pytest.fixture
def make_workspace() -> Any:
    """Factory fixture: build a ``Workspace`` with common test defaults.

    A plain module-level helper would collide at import time with the other
    ``conftest.py`` under ``tests/integration`` (both resolve to the bare
    name ``conftest``, and whichever loads last wins in ``sys.modules``) —
    so this is exposed as a fixture instead; tests take ``make_workspace``
    as a parameter rather than importing it.

    Call as ``make_workspace(tmp_path, **overrides)`` — any ``Workspace``
    field can be overridden by keyword; ``path``/``git_repo`` default to the
    first positional argument.
    """

    def _factory(root: Path, **overrides: Any) -> Workspace:
        fields: dict[str, Any] = {
            "path": root,
            "ecosystem": "node",
            "manager": "npm",
            "git_repo": root,
            "label": "test",
        }
        fields.update(overrides)
        return Workspace(**fields)

    return _factory


@pytest.fixture(autouse=True)
def _no_repo_holds(request: pytest.FixtureRequest) -> Iterator[None]:
    """Patch away repo-level hold overrides for any module defining ``_REPO_HOLDS_CACHE``.

    Each ecosystem's holds tests patch a different ``<pkg>._repo_holds_cached``
    target (already a module-level constant in every such file, reused
    elsewhere in the same module); this fixture reads that constant off the
    test module itself so one shared autouse fixture covers all of them
    instead of an identical 3-line copy per file. A module with no such
    constant is unaffected (no-op) — most test modules don't define one.
    """
    target = getattr(request.module, "_REPO_HOLDS_CACHE", None)
    if target is None:
        yield
        return
    with patch(target, return_value=()):
        yield


@pytest.fixture(autouse=True)
def _isolate_state_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the XDG cache (and thus the repo-ops state store) at a tmp dir.

    Tests that care about the store's location still override
    ``XDG_CACHE_HOME`` themselves; this just guarantees no test ever reads
    or flushes into the real ``~/.cache/repo-ops/state.json``. The module's
    in-memory cache is also reset — the path redirect alone would let one
    test's loaded state leak into the next via ``_CACHE``.
    """
    from repo_ops_core import state as state_mod

    state_mod._CACHE = None
    state_mod._DIRTY = False
    state_mod._BEHIND_RUN_SEEN.clear()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    # Same reasoning for the config dir: the operator's real
    # ~/.config/repo-ops/config.toml must never reach a test's settings.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    # Every plan/apply writes a run dir under XDG_STATE_HOME.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin the run dir plan/apply write their log and report.json into."""
    d = tmp_path / "run"
    d.mkdir()
    monkeypatch.setattr("repo_ops_upgrade.run.new_run_dir", lambda _command: d)
    return d


@pytest.fixture(autouse=True)
def _disable_ambient_color() -> Iterator[None]:
    """Neutralise colour-forcing env so help-text assertions are deterministic.

    Python 3.14's argparse colourises usage output and honours ``FORCE_COLOR``,
    so a developer whose terminal exports it sees ANSI-wrapped help while CI
    sees plain text, and substring assertions pass in one place and fail in the
    other.
    """
    names = ("FORCE_COLOR", "CLICOLOR_FORCE", "COLORTERM", "NO_COLOR")
    before = {k: os.environ[k] for k in names if k in os.environ}
    for name in names:
        os.environ.pop(name, None)
    os.environ["NO_COLOR"] = "1"
    try:
        yield
    finally:
        for name in names:
            os.environ.pop(name, None)
        os.environ.update(before)


@pytest.fixture
def upgrade_args() -> Any:
    """Build real plan/apply args from the parser, so tests cannot drift from it.

    Defaults to ``jobs=1`` and no verify/commit (a unit test of the run body
    should not shell out to gates or git); pass overrides to change any field.
    """
    from repo_ops_core.cli import build_upgrade_parser

    def _build(**overrides: Any) -> Any:
        args = build_upgrade_parser(apply=True).parse_args([])
        args.jobs = 1
        args.no_verify = True
        args.no_commit = True
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    return _build


class _FakeRun:
    """A ``run()`` stand-in that records each argv in ``calls`` and succeeds."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, stdout="", stderr="")


@pytest.fixture
def fake_run() -> _FakeRun:
    """Patch target for ``run()`` when a test only needs the commands it issued.

    The ~100 local ``fake_run`` definitions left in tests script per-command
    output; folding them into a scripted fixture was measured and declined.
    """
    return _FakeRun()


@pytest.fixture(autouse=True)
def _offline_pm_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the packageManager pin remediation off the npm registry."""
    monkeypatch.setattr(
        "repo_ops_upgrade_remediations_js.remediations._pm_registry_latest", lambda name: None
    )


@pytest.fixture(autouse=True)
def _offline_go_vulndb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Go upgrade screening off vuln.go.dev: no advisories unless a test supplies them."""
    monkeypatch.setattr("repo_ops_go.vulndb._fetch_json", lambda url: [])
    vulndb._module_advisories.cache_clear()
    vulndb._affected_ranges.cache_clear()
