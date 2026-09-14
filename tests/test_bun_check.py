"""Regression tests for ``check_bun`` outdated parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_js_bun.bun import check_bun


def test_check_bun_prefers_json(tmp_path: Path, make_workspace) -> None:
    payload = {"vite": {"current": "5.0.0", "latest": "6.0.0"}}
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        if "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        raise AssertionError("text outdated should not run when JSON succeeds")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        changes, behind_latest = check_bun(
            make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
        )

    assert calls == [["bun", "outdated", "--json"]]
    assert len(changes) == 1
    assert changes[0].name == "vite"
    assert changes[0].from_ver == "5.0.0"
    assert changes[0].to == "6.0.0"
    assert behind_latest == []


# Real `bun outdated` (>=1.3) output: no `--json` support, an ANSI-coloured box
# table led by a `bun outdated vX (hash)` banner. The banner must NOT be parsed
# as a package "bun" at version "(hash)" — the original `bun update bun@(hash)`
# hard-failure bug.
_REAL_BUN_TABLE = (
    "\x1b[0m\x1b[1mbun outdated \x1b[0m\x1b[2mv1.3.14 (0d9b296a)\x1b[0m\n"
    "  \U0001f50d Resolving... ┌──┐\n"
    "│ \x1b[1m\x1b[34mPackage\x1b[0m │ \x1b[1mCurrent\x1b[0m "
    "│ \x1b[1mUpdate\x1b[0m │ \x1b[1mLatest\x1b[0m │\n"
    "├────│\n"
    "│ @hono/node-server\x1b[2m\x1b[0m │ 1.19.14 │ "
    "\x1b[2m1.19.14\x1b[0m │ \x1b[1m\x1b[31m2.0.4\x1b[0m │\n"
    "│ ai\x1b[2m\x1b[0m │ 6.0.193 │ "
    "\x1b[2m6.0.\x1b[1m\x1b[32m195\x1b[0m │ 6.0.195 │\n"
    "│ vitest\x1b[2m (dev)\x1b[0m │ 4.1.7 │ 4.1.8 │ 4.1.8 │\n"
)


def _patched_check(table_stdout: str, tmp_path: Path, make_workspace):
    def fake_run(cmd, cwd, timeout=600, env=None):
        if "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=table_stdout, stderr="")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        return check_bun(make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo"))


def test_check_bun_parses_real_ansi_table_in_range(
    monkeypatch, tmp_path: Path, make_workspace
) -> None:
    """Range-respecting (Update column) parsing — pin latest-mode off, since the
    sweep now defaults it on and this test is specifically about the
    non-latest bucketing branch."""
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "0")
    changes, behind_latest = _patched_check(_REAL_BUN_TABLE, tmp_path, make_workspace)

    # Default: range-respecting (Update column). @hono is skipped — its in-range
    # Update (1.19.14) equals Current; only ai and vitest have in-range bumps.
    assert [(c.name, c.from_ver, c.to) for c in changes] == [
        ("ai", "6.0.193", "6.0.195"),
        ("vitest", "4.1.7", "4.1.8"),
    ]
    # The banner must never leak through as a bogus "bun@(hash)" target.
    assert "bun" not in [c.name for c in changes]
    assert all("0d9b296a" not in c.to for c in changes)
    # @hono has a newer latest (2.0.4) but in-range update == current, so behind_latest.
    assert ("@hono/node-server", "1.19.14", "2.0.4") in [
        (c.name, c.from_ver, c.to) for c in behind_latest
    ]


def test_check_bun_latest_column_when_opted_in(monkeypatch, tmp_path: Path, make_workspace) -> None:
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "1")
    changes, behind_latest = _patched_check(_REAL_BUN_TABLE, tmp_path, make_workspace)

    # Latest column: @hono now upgrades out-of-range to 2.0.4.
    assert ("@hono/node-server", "2.0.4") in [(c.name, c.to) for c in changes]
    # When latest-mode is on, no behind_latest entries are emitted.
    assert behind_latest == []


# a package where current < update < latest (all distinct) must
# land in EXACTLY ONE bucket — changes — matching the JSON parser's if/elif
# semantics.  The old text parser appended to changes AND then also to
# behind_latest via a separate trailing `if`, causing double-counting.
_TABLE_CURRENT_LT_UPDATE_LT_LATEST = (
    "│ \x1b[1m\x1b[34mPackage\x1b[0m │ \x1b[1mCurrent\x1b[0m "
    "│ \x1b[1mUpdate\x1b[0m │ \x1b[1mLatest\x1b[0m │\n"
    "├────│\n"
    # react: current=18.0.0, update=18.3.0 (in-range bump), latest=19.0.0
    "│ react │ 18.0.0 │ 18.3.0 │ 19.0.0 │\n"
)


def test_text_parser_no_double_count_when_current_lt_update_lt_latest(
    monkeypatch, tmp_path: Path, make_workspace
) -> None:
    """Current < update < latest → changes only, not both buckets."""
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "0")
    changes, behind_latest = _patched_check(
        _TABLE_CURRENT_LT_UPDATE_LT_LATEST, tmp_path, make_workspace
    )

    # The in-range bump (18.0.0 → 18.3.0) lands in changes.
    assert len(changes) == 1
    assert changes[0].name == "react"
    assert changes[0].from_ver == "18.0.0"
    assert changes[0].to == "18.3.0"
    # It must NOT also appear in behind_latest (double-count regression).
    assert behind_latest == [], f"expected empty behind_latest, got {behind_latest}"


# a real npm dependency literally named ``bun`` must be detected
# as a Change, while the ``bun outdated vX (hash)`` banner on the same output is
# still NOT emitted. The discriminator is structural — the banner is not a
# ``│``-delimited table row — not a by-name exclusion of "bun".
_TABLE_WITH_REAL_BUN_DEP = (
    "\x1b[0m\x1b[1mbun outdated \x1b[0m\x1b[2mv1.3.14 (0d9b296a)\x1b[0m\n"
    "│ \x1b[1m\x1b[34mPackage\x1b[0m │ \x1b[1mCurrent\x1b[0m "
    "│ \x1b[1mUpdate\x1b[0m │ \x1b[1mLatest\x1b[0m │\n"
    "├────│\n"
    # A genuine dependency named `bun` with an in-range bump 1.0.0 -> 1.1.0.
    "│ bun │ 1.0.0 │ 1.1.0 │ 1.1.0 │\n"
)


def test_check_bun_detects_real_bun_dep_but_not_banner(tmp_path: Path, make_workspace) -> None:
    """Real `bun` dep detected; `(hash)` banner still filtered."""
    changes, _behind = _patched_check(_TABLE_WITH_REAL_BUN_DEP, tmp_path, make_workspace)

    # (a) The real `bun` dependency is detected as an in-range upgrade.
    assert ("bun", "1.0.0", "1.1.0") in [(c.name, c.from_ver, c.to) for c in changes]
    # (b) The banner never leaks through as a bogus `bun@(hash)` change.
    assert all("0d9b296a" not in c.to for c in changes)


# on a non-UTF-8 terminal (GitHub Actions runners, minimal
# containers) bun renders the SAME outdated table with ASCII ``|`` separators
# instead of Unicode ``│`` box drawing.  The old parser only recognised ``│``
# rows, so CI parsed ZERO changes and apply_bun returned early — the ghost-bump
# guard never ran.  Fixture is verbatim `bun outdated` v1.3.14 (0d9b296a)
# output captured in an ubuntu:24.04 container (no locales installed).
_ASCII_PIPE_TABLE = (
    "bun outdated v1.3.14 (0d9b296a)\n"
    "|--------------------------------------|\n"
    "| Package  | Current | Update | Latest |\n"
    "|----------|---------|--------|--------|\n"
    "| is-odd   | 0.1.2   | 1.0.0  | 3.0.1  |\n"
    "|--------------------------------------|\n"
)


def test_check_bun_parses_ascii_pipe_table(monkeypatch, tmp_path: Path, make_workspace) -> None:
    """ASCII-pipe table (CI/non-UTF-8 terminal) must parse."""
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "0")
    changes, behind_latest = _patched_check(_ASCII_PIPE_TABLE, tmp_path, make_workspace)

    # The single data row parses; update column (1.0.0) is the in-range target.
    assert [(c.name, c.from_ver, c.to) for c in changes] == [("is-odd", "0.1.2", "1.0.0")]
    # Border/header rows must not leak: no dash-led or "Package" pseudo-names.
    assert behind_latest == []


def test_check_bun_passes_timeout_check(tmp_path: Path, make_workspace) -> None:
    """Both bun outdated calls must use TIMEOUT_CHECK (600s), not the default 300s."""
    captured: list[dict] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append({"cmd": list(cmd), "timeout": timeout})
        if "--json" in cmd:
            # Simulate bun >= 1.3 not supporting --json: empty output, returncode=1.
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        check_bun(make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo"))

    assert len(captured) == 2, f"expected 2 run() calls, got {len(captured)}"
    assert captured[0]["cmd"] == ["bun", "outdated", "--json"]
    assert captured[0]["timeout"] == TIMEOUT_CHECK, (
        f"--json call used timeout={captured[0]['timeout']!r}, want {TIMEOUT_CHECK}"
    )
    assert captured[1]["cmd"] == ["bun", "outdated"]
    assert captured[1]["timeout"] == TIMEOUT_CHECK, (
        f"text call used timeout={captured[1]['timeout']!r}, want {TIMEOUT_CHECK}"
    )


def test_check_bun_checks_each_workspace_member(
    monkeypatch, tmp_path: Path, make_workspace
) -> None:
    """`bun outdated` reports only the cwd's workspace; members are checked from their dirs."""
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "1")
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["apps/*"]}))
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text(json.dumps({"dependencies": {"next": "16.3.0"}}))
    tables = {web: "│ Package │ Current │ Update │ Latest │\n│ next │ 16.3.0 │ 16.3.0 │ 16.3.5 │\n"}

    def fake_run(cmd, cwd, timeout=600, env=None):
        if "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=tables.get(cwd, ""), stderr="")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        changes, _behind = check_bun(make_workspace(tmp_path, manager="bun"))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [("next", "16.3.0", "16.3.5")]


def test_check_bun_proposes_private_member_peer_lockstep(
    monkeypatch, tmp_path: Path, make_workspace
) -> None:
    """A private member's peer pin follows the version its own manifest depends on."""
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "1")
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
    (tmp_path / ".repo-ops.toml").write_text(
        '[[hold]]\npackage = "react-native"\nmax_version = "0.86"\nworkspace = "packages/ui"\n'
    )
    manifests = {
        "auth": {"private": True, "devDependencies": {"next": "16.3.5"}},
        "lib": {"devDependencies": {"next": "16.3.5"}},
        "ui": {
            "private": True,
            "devDependencies": {
                "react": "^19.3.0",
                "react-dom": "^19.2.8",
                "react-native": "0.87.1",
            },
            "peerDependencies": {
                "react": "^19.2.8",
                "react-dom": "^19.2.8",
                "react-native": "0.86.0",
            },
        },
    }
    manifests["auth"]["peerDependencies"] = manifests["lib"]["peerDependencies"] = {
        "next": "16.3.1"
    }
    for name, data in manifests.items():
        (tmp_path / "packages" / name).mkdir(parents=True)
        (tmp_path / "packages" / name / "package.json").write_text(json.dumps(data))
    auth = tmp_path / "packages" / "auth"
    tables = {
        auth: "│ Package │ Current │ Update │ Latest │\n│ next │ 16.3.5 │ 16.3.5 │ 16.3.6 │\n"
    }

    def fake_run(cmd, cwd, timeout=600, env=None):
        if "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=tables.get(cwd, ""), stderr="")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        changes, _behind = check_bun(make_workspace(tmp_path, manager="bun"))

    assert {(c.name, c.from_ver, c.to, c.member) for c in changes} == {
        ("next", "16.3.5", "16.3.6", "packages/auth"),
        ("next", "16.3.1", "16.3.6", "packages/auth"),
        ("react", "^19.2.8", "^19.3.0", "packages/ui"),
    }
