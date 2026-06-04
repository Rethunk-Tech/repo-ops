"""Regression tests for ``check_bun`` outdated parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_bun.bun import check_bun
from repo_ops_models import Workspace


def _ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )


def test_check_bun_prefers_json() -> None:
    payload = {"vite": {"current": "5.0.0", "latest": "6.0.0"}}
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        if "--json" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        raise AssertionError("text outdated should not run when JSON succeeds")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        changes, behind_latest = check_bun(_ws())

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


def _patched_check(table_stdout: str):
    def fake_run(cmd, cwd, timeout=600, env=None):
        if "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=table_stdout, stderr="")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        return check_bun(_ws())


def test_check_bun_parses_real_ansi_table_in_range() -> None:
    changes, behind_latest = _patched_check(_REAL_BUN_TABLE)

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


def test_check_bun_latest_column_when_opted_in(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "1")
    changes, behind_latest = _patched_check(_REAL_BUN_TABLE)

    # Latest column: @hono now upgrades out-of-range to 2.0.4.
    assert ("@hono/node-server", "2.0.4") in [(c.name, c.to) for c in changes]
    # When latest-mode is on, no behind_latest entries are emitted.
    assert behind_latest == []


# Regression #45: a package where current < update < latest (all distinct) must
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


def test_text_parser_no_double_count_when_current_lt_update_lt_latest() -> None:
    """Regression #45: current < update < latest → changes only, not both buckets."""
    changes, behind_latest = _patched_check(_TABLE_CURRENT_LT_UPDATE_LT_LATEST)

    # The in-range bump (18.0.0 → 18.3.0) lands in changes.
    assert len(changes) == 1
    assert changes[0].name == "react"
    assert changes[0].from_ver == "18.0.0"
    assert changes[0].to == "18.3.0"
    # It must NOT also appear in behind_latest (double-count regression).
    assert behind_latest == [], f"expected empty behind_latest, got {behind_latest}"


# Regression #67: a real npm dependency literally named ``bun`` must be detected
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


def test_check_bun_detects_real_bun_dep_but_not_banner() -> None:
    """Regression #67: real `bun` dep detected; `(hash)` banner still filtered."""
    changes, _behind = _patched_check(_TABLE_WITH_REAL_BUN_DEP)

    # (a) The real `bun` dependency is detected as an in-range upgrade.
    assert ("bun", "1.0.0", "1.1.0") in [(c.name, c.from_ver, c.to) for c in changes]
    # (b) The banner never leaks through as a bogus `bun@(hash)` change.
    assert all("0d9b296a" not in c.to for c in changes)
