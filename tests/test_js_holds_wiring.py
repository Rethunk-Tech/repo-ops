"""Per-manager wiring for package version-ceiling ("hold") rules.

Parametrized over (bun, npm, pnpm, yarn): each manager's ``targeted_bump_behind``/
``check_*``/``apply_*`` calls into the same shared precedence and clamp-down
logic, unit-tested once in ``test_js_holds_shared.py``. What's genuinely
per-manager is the CLI shape (``pnpm update --latest`` vs ``npm install
pkg@spec``), the pin field (flat ``overrides`` vs nested ``pnpm.overrides``
vs ``resolutions``), and yarn's isolated-copy check flow and optional-``ws``
signature — those stay as manager-specific cases below rather than forced
into the shared table.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest

from repo_ops_js_bun.bun import (
    apply_bun,
    check_bun,
)
from repo_ops_js_bun.bun import targeted_bump_behind as bun_targeted_bump_behind
from repo_ops_js_npm.npm import (
    apply_npm,
    check_npm,
)
from repo_ops_js_npm.npm import targeted_bump_behind as npm_targeted_bump_behind
from repo_ops_js_pnpm.pnpm import (
    apply_pnpm,
    check_pnpm,
)
from repo_ops_js_pnpm.pnpm import targeted_bump_behind as pnpm_targeted_bump_behind
from repo_ops_js_yarn.yarn import (
    apply_yarn,
    check_yarn_snapshot,
)
from repo_ops_js_yarn.yarn import targeted_bump_behind as yarn_targeted_bump_behind
from repo_ops_models import Change

_GLOBAL_HOLDS_CACHE = "repo_ops_core.holds._global_holds_cached"


def _held_rule(**overrides):
    from repo_ops_core.holds import HoldRule

    defaults = {"package": "typescript", "max_version": "6"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def _module(name: str) -> str:
    return f"repo_ops_js_{name}.{name}"


def _run_patch(name: str) -> str:
    return f"{_module(name)}.run"


def _substr(cmds: list[list[str]], marker: str) -> bool:
    return any(marker in " ".join(c) for c in cmds)


def bun_call(tmp_path: Path, ws, changes: list[Change]):
    return bun_targeted_bump_behind(ws, changes)


def npm_call(tmp_path: Path, ws, changes: list[Change]):
    return npm_targeted_bump_behind(ws, changes)


def pnpm_call(tmp_path: Path, ws, changes: list[Change]):
    return pnpm_targeted_bump_behind(ws, changes)


def yarn_call(tmp_path: Path, ws, changes: list[Change]):
    return yarn_targeted_bump_behind(tmp_path, changes, ws=ws)


def _lock_with_typescript(version: str) -> str:
    return (
        "__metadata:\n  version: 8\n\n"
        f'"typescript@npm:^6.0.0":\n  version: {version}\n'
        f'  resolution: "typescript@npm:{version}"\n'
    )


def _seed_bun(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"typescript": "^6.0.0"}}), encoding="utf-8"
    )


def _seed_npm(tmp_path: Path) -> None:
    _seed_bun(tmp_path)
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")


def _seed_pnpm(tmp_path: Path) -> None:
    _seed_bun(tmp_path)
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")


def _seed_yarn(tmp_path: Path) -> None:
    _seed_bun(tmp_path)
    (tmp_path / "yarn.lock").write_text(_lock_with_typescript("6.5.0"), encoding="utf-8")


class BumpCase(NamedTuple):
    name: str
    seed: object
    call_bump: object
    ceiling_ok: object
    latest_absent: object


# pnpm's non-catalog bump command is a different subcommand shape entirely
# (``pnpm update --latest`` vs ``pnpm add pkg@spec``), not just quoting —
# real manager CLI difference, so its predicates check the exact argv.
BUMP_CASES = [
    BumpCase(
        "bun",
        _seed_bun,
        bun_call,
        lambda c: _substr(c, "typescript@<=6"),
        lambda c: not _substr(c, "typescript@latest"),
    ),
    BumpCase(
        "npm",
        _seed_npm,
        npm_call,
        lambda c: _substr(c, "typescript@<=6"),
        lambda c: not _substr(c, "typescript@latest"),
    ),
    BumpCase(
        "pnpm",
        _seed_pnpm,
        pnpm_call,
        lambda c: ["pnpm", "add", "typescript@<=6"] in c,
        lambda c: not any(cmd[:2] == ["pnpm", "update"] for cmd in c),
    ),
    BumpCase(
        "yarn",
        _seed_yarn,
        yarn_call,
        lambda c: _substr(c, "typescript@<=6"),
        lambda c: not _substr(c, "typescript@latest"),
    ),
]


@pytest.mark.parametrize("case", BUMP_CASES, ids=[c.name for c in BUMP_CASES])
def test_targeted_bump_uses_hold_ceiling_instead_of_latest(
    case: BumpCase, tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    """The still-behind fallback targets `<=ceiling`, not the registry latest."""
    case.seed(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager=case.name, label="demo")
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=None, env=None):
        captured_cmds.append(list(cmd))
        return make_proc()

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(_run_patch(case.name), side_effect=_fake_run),
    ):
        case.call_bump(tmp_path, ws, [Change("typescript", "6.5.0", "7.0.0")])

    assert case.ceiling_ok(captured_cmds), captured_cmds
    assert case.latest_absent(captured_cmds), captured_cmds


def _bun_check(tmp_path, monkeypatch, make_workspace, make_proc, latest):
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    table = f"│ Package │ Current │ Update │ Latest │\n│ typescript │ 6.5.0 │ 6.5.0 │ {latest} │\n"

    def _fake_run(cmd, cwd, timeout=None, env=None):
        return make_proc(stdout=table)

    with patch(_run_patch("bun"), side_effect=_fake_run):
        return check_bun(ws)


def _npm_check(tmp_path, monkeypatch, make_workspace, make_proc, latest):
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    with patch(
        f"{_module('npm')}._npm_outdated",
        return_value=([], [Change("typescript", "6.5.0", latest)]),
    ):
        return check_npm(ws)


def _pnpm_check(tmp_path, monkeypatch, make_workspace, make_proc, latest):
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    with patch(
        f"{_module('pnpm')}._pnpm_outdated",
        return_value=([], [Change("typescript", "6.5.0", latest)]),
    ):
        return check_pnpm(ws)


# yarn's check flow (check_yarn_snapshot) runs through an isolated-copy +
# snapshot/diff pipeline with its own 3-tuple return — a structural
# difference from the other three, not test duplication — so it keeps its
# own standalone case near the bottom of this file instead of joining here.
CHECK_CASES = [("bun", _bun_check), ("npm", _npm_check), ("pnpm", _pnpm_check)]


@pytest.mark.parametrize("case", CHECK_CASES, ids=[c[0] for c in CHECK_CASES])
def test_check_does_not_promote_held_package_past_ceiling(
    case, tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    _name, run_check = case
    with patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)):
        changes, behind = run_check(tmp_path, monkeypatch, make_workspace, make_proc, "7.0.0")
    assert "typescript" not in {c.name for c in changes}
    assert behind == [Change("typescript", "6.5.0", "7.0.0")]


@pytest.mark.parametrize("case", CHECK_CASES, ids=[c[0] for c in CHECK_CASES])
def test_check_promotes_held_package_within_ceiling(
    case, tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    """A target that stays within the ceiling is still promoted into changes."""
    _name, run_check = case
    with patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)):
        changes, behind = run_check(tmp_path, monkeypatch, make_workspace, make_proc, "6.9.0")
    assert "typescript" in {c.name for c in changes}
    assert behind == []


class ClampCase(NamedTuple):
    name: str
    outdated_patch: str | None
    corepack_patch: str | None
    apply_fn: object
    is_hold_install: object
    lock_name: str | None
    lock_content: str


CLAMP_CASES = [
    ClampCase(
        "bun",
        f"{_module('bun')}.check_bun",
        None,
        apply_bun,
        lambda cmd: cmd[:2] == ["bun", "add"] and any("typescript@" in c for c in cmd),
        None,
        "",
    ),
    ClampCase(
        "npm",
        f"{_module('npm')}._npm_outdated",
        None,
        apply_npm,
        lambda cmd: "install" in cmd and any("typescript@" in c for c in cmd),
        "package-lock.json",
        "{}",
    ),
    ClampCase(
        "pnpm",
        f"{_module('pnpm')}._pnpm_outdated",
        None,
        apply_pnpm,
        lambda cmd: "add" in cmd and any("typescript@" in c for c in cmd),
        "pnpm-lock.yaml",
        "lockfileVersion: '6.0'\n",
    ),
    ClampCase(
        "yarn",
        None,
        f"{_module('yarn')}.corepack_prepare",
        apply_yarn,
        lambda cmd: cmd[:2] == ["yarn", "up"] and len(cmd) > 2 and "typescript@" in cmd[2],
        "yarn.lock",
        _lock_with_typescript("6.5.0"),
    ),
]


def _seed_over_ceiling(tmp_path: Path, case: ClampCase) -> Path:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"typescript": "^7.0.0"}}), encoding="utf-8"
    )
    if case.lock_name:
        (tmp_path / case.lock_name).write_text(case.lock_content, encoding="utf-8")
    nm_dir = tmp_path / "node_modules" / "typescript"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text(json.dumps({"version": "7.2.0"}), encoding="utf-8")
    return nm_dir


def _apply_patches(case: ClampCase, fake_run) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)))
    if case.outdated_patch:
        stack.enter_context(patch(case.outdated_patch, return_value=([], [])))
    if case.corepack_patch:
        stack.enter_context(patch(case.corepack_patch))
    stack.enter_context(patch(_run_patch(case.name), side_effect=fake_run))
    return stack


@pytest.mark.parametrize("case", CLAMP_CASES, ids=[c.name for c in CLAMP_CASES])
def test_apply_clamps_package_already_past_ceiling(
    case: ClampCase, tmp_path: Path, make_workspace, make_proc
) -> None:
    """A package already on 7.x when a hold is added gets clamped back down.

    Simulates the retroactive-hold case: the outdated/check report is empty
    (the manifest range already matches what's installed) but the resolved
    version on disk violates a hold ceiling added after the fact.
    """
    nm_dir = _seed_over_ceiling(tmp_path, case)
    ws = make_workspace(tmp_path, ecosystem="node", manager=case.name, label="demo")

    def _fake_run(cmd, cwd, timeout=None, env=None):
        if case.is_hold_install(list(cmd)):
            (nm_dir / "package.json").write_text(json.dumps({"version": "6.9.0"}), encoding="utf-8")
        return make_proc()

    with _apply_patches(case, _fake_run):
        behind = case.apply_fn(ws)

    assert len(behind) == 1
    assert behind[0].name == "typescript"
    assert behind[0].from_ver == "6.9.0"
    assert behind[0].to == "7.2.0"


@pytest.mark.parametrize("case", CLAMP_CASES, ids=[c.name for c in CLAMP_CASES])
def test_apply_raises_when_clamp_cannot_satisfy_ceiling(
    case: ClampCase, tmp_path: Path, make_workspace, make_proc
) -> None:
    """A corrective install that still can't land under ceiling is a loud failure."""
    _seed_over_ceiling(tmp_path, case)
    ws = make_workspace(tmp_path, ecosystem="node", manager=case.name, label="demo")

    def _fake_run(cmd, cwd, timeout=None, env=None):
        # Corrective install "succeeds" but node_modules is never rewritten —
        # simulates a ceiling no published version can satisfy.
        return make_proc()

    with _apply_patches(case, _fake_run), pytest.raises(RuntimeError, match="hold"):
        case.apply_fn(ws)


class PinCase(NamedTuple):
    name: str
    seed_pin: object
    call_bump: object
    pin_of: object
    mark_landed: object
    release_pin_patch: str | None
    expected_cmds: list[list[str]] | None


def _seed_pin_flat(tmp_path: Path) -> None:
    pkg = json.dumps(
        {"dependencies": {"is-odd": ">=0.1.0 <2.0.0"}, "overrides": {"is-odd": "0.1.2"}}
    )
    (tmp_path / "package.json").write_text(pkg, encoding="utf-8")
    nm_dir = tmp_path / "node_modules" / "is-odd"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text(json.dumps({"version": "0.1.2"}), encoding="utf-8")


def _seed_pin_npm(tmp_path: Path) -> None:
    _seed_pin_flat(tmp_path)
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")


def _seed_pin_pnpm(tmp_path: Path) -> None:
    pkg = json.dumps(
        {
            "dependencies": {"is-odd": ">=0.1.0 <2.0.0"},
            "pnpm": {"overrides": {"is-odd": "0.1.2"}},
        }
    )
    (tmp_path / "package.json").write_text(pkg, encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")
    nm_dir = tmp_path / "node_modules" / "is-odd"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text(json.dumps({"version": "0.1.2"}), encoding="utf-8")


def _lock_with_is_odd(version: str) -> str:
    return (
        "__metadata:\n  version: 8\n\n"
        f'"is-odd@npm:>=0.1.0 <2.0.0":\n  version: {version}\n'
        f'  resolution: "is-odd@npm:{version}"\n'
    )


def _seed_pin_yarn(tmp_path: Path) -> None:
    pkg = json.dumps(
        {
            "dependencies": {"is-odd": ">=0.1.0 <2.0.0"},
            "resolutions": {"is-odd": "0.1.2"},
        }
    )
    (tmp_path / "package.json").write_text(pkg, encoding="utf-8")
    (tmp_path / "yarn.lock").write_text(_lock_with_is_odd("0.1.2"), encoding="utf-8")


def _mark_landed_nm(cmd: list[str], tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "is-odd" / "package.json").write_text(
        json.dumps({"version": "1.0.0"}), encoding="utf-8"
    )


def _mark_landed_yarn_lock(cmd: list[str], tmp_path: Path) -> None:
    if cmd[:2] == ["yarn", "up"]:
        (tmp_path / "yarn.lock").write_text(_lock_with_is_odd("1.0.0"), encoding="utf-8")


PIN_CASES = [
    PinCase(
        "bun",
        _seed_pin_flat,
        bun_call,
        lambda pkg: pkg.get("overrides", {}),
        _mark_landed_nm,
        f"{_module('bun')}.release_unheld_pin",
        [["bun", "add", "is-odd@latest", "--ignore-scripts"]],
    ),
    PinCase(
        "npm",
        _seed_pin_npm,
        npm_call,
        lambda pkg: pkg.get("overrides", {}),
        _mark_landed_nm,
        f"{_module('npm')}.release_unheld_pin",
        [["npm", "install", "--save", "is-odd@latest"]],
    ),
    PinCase(
        "pnpm",
        _seed_pin_pnpm,
        pnpm_call,
        lambda pkg: pkg.get("pnpm", {}).get("overrides", {}),
        _mark_landed_nm,
        f"{_module('pnpm')}.release_unheld_pin",
        [["pnpm", "update", "--latest", "is-odd"]],
    ),
    PinCase(
        "yarn",
        _seed_pin_yarn,
        yarn_call,
        lambda pkg: pkg.get("resolutions", {}),
        _mark_landed_yarn_lock,
        None,  # yarn's own test never asserted on release_unheld_pin
        None,  # ...nor on the exact command sequence
    ),
]


@pytest.mark.parametrize("case", PIN_CASES, ids=[c.name for c in PIN_CASES])
def test_targeted_bump_releases_unheld_pin(
    case: PinCase, tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    """No hold covers is-odd: its legacy version-pin field is released so the
    retry can actually move the version, instead of ghost-bumping forever."""
    case.seed_pin(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager=case.name, label="demo")
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=None, env=None):
        captured_cmds.append(list(cmd))
        case.mark_landed(list(cmd), tmp_path)
        return make_proc()

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=()),
        patch(_run_patch(case.name), side_effect=_fake_run),
    ):
        applied, behind = case.call_bump(tmp_path, ws, [Change("is-odd", "0.1.2", "1.0.0")])

    assert not behind
    assert applied == [Change("is-odd", "0.1.2", "1.0.0")]
    if case.expected_cmds is not None:
        assert captured_cmds == case.expected_cmds
    pkg_after = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert "is-odd" not in case.pin_of(pkg_after)


@pytest.mark.parametrize("case", PIN_CASES, ids=[c.name for c in PIN_CASES])
def test_targeted_bump_keeps_held_pin(
    case: PinCase, tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    """An active hold leaves its version-pin field untouched during retry —
    the pin-release path only fires for packages with no tracked hold."""
    case.seed_pin(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager=case.name, label="demo")

    def _fake_run(cmd, cwd, timeout=None, env=None):
        return make_proc()

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                _GLOBAL_HOLDS_CACHE,
                return_value=(_held_rule(package="is-odd", max_version="0.1.2"),),
            )
        )
        stack.enter_context(patch(_run_patch(case.name), side_effect=_fake_run))
        release_mock = (
            stack.enter_context(patch(case.release_pin_patch)) if case.release_pin_patch else None
        )
        case.call_bump(tmp_path, ws, [Change("is-odd", "0.1.2", "1.0.0")])

    if release_mock is not None:
        release_mock.assert_not_called()
    pkg_after = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert case.pin_of(pkg_after)["is-odd"] == "0.1.2"


# ---------------------------------------------------------------------------
# Manager-specific cases: a genuine behavioral or signature difference, not
# duplication, so each stays a standalone test rather than joining a table.
# ---------------------------------------------------------------------------


def test_apply_bun_target_loop_uses_hold_ceiling_between_override_and_to(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    """apply_bun's own per-package target loop: hold ceiling > ch.to.

    Bun runs this precedence twice — once in its per-package loop inside
    ``apply_bun``, once via the shared ``targeted_bump_behind`` fallback — so
    it gets its own case on top of the shared ``BUMP_CASES`` coverage above.
    """
    _seed_bun(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=None, env=None):
        captured_cmds.append(list(cmd))
        return make_proc()

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(_run_patch("bun"), side_effect=_fake_run),
        patch(
            f"{_module('bun')}.check_bun",
            return_value=([Change("typescript", "6.5.0", "7.0.0")], []),
        ),
    ):
        apply_bun(ws)

    update_cmds = [c for c in captured_cmds if c[:2] == ["bun", "update"]]
    assert update_cmds, captured_cmds
    assert "typescript@<=6" in update_cmds[0]
    assert "typescript@7.0.0" not in update_cmds[0]


def test_targeted_bump_without_ws_keeps_latest_fallback(
    tmp_path: Path, monkeypatch, make_proc
) -> None:
    """yarn's ``ws`` is optional and keyword-only: direct callers that omit it
    keep the ``@latest`` target."""
    _seed_yarn(tmp_path)
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=None, env=None):
        captured_cmds.append(list(cmd))
        return make_proc()

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(_run_patch("yarn"), side_effect=_fake_run),
    ):
        yarn_targeted_bump_behind(tmp_path, [Change("typescript", "6.5.0", "7.0.0")])

    assert _substr(captured_cmds, "typescript@latest")


def test_check_yarn_snapshot_does_not_promote_held_package_past_ceiling(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    """yarn's check runs via an isolated-copy + snapshot/diff pipeline (a real
    structural difference from bun/npm/pnpm's direct outdated report), so its
    "don't promote past ceiling" case can't share the CHECK_CASES table above.

    ``yarn npm info`` reports latest 7.0.0 (over the ceiling); the targeted
    bump attempt installs `typescript@"<=6"`, which cannot move past the
    already-installed 6.5.0 (nothing newer satisfies the ceiling) — so the
    bump doesn't advance, and the gap stays reported as still-behind instead
    of being promoted into the plan preview.
    """
    _seed_yarn(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager="yarn", label="demo")

    def _fake_run(cmd, cwd, timeout=None, env=None):
        if cmd[:3] == ["yarn", "npm", "info"]:
            return make_proc(
                stdout=json.dumps({"version": "7.0.0", "dist-tags": {"latest": "7.0.0"}})
            )
        if cmd[:2] == ["yarn", "up"] and len(cmd) > 2 and "latest" in cmd[2]:
            # Would only be reached if the hold were NOT applied.
            (tmp_path / "yarn.lock").write_text(_lock_with_typescript("7.0.0"), encoding="utf-8")
        # Hold-ceiling install or `yarn install`: 6.5.0 already satisfies <=6
        # and nothing newer is under the ceiling, so the lockfile is unchanged.
        return make_proc()

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(_run_patch("yarn"), side_effect=_fake_run),
        patch(f"{_module('yarn')}.corepack_prepare"),
        patch(f"{_module('yarn')}._copy_yarn_workspace_to_temp", return_value=tmp_path),
        patch(f"{_module('yarn')}.snapshot_workspace_files", return_value={}),
        patch(f"{_module('yarn')}.diff_workspace_snapshots", return_value=[]),
        patch(f"{_module('yarn')}.overrides_audit", return_value=[]),
    ):
        changes, _overrides, behind = check_yarn_snapshot(ws)

    assert "typescript" not in {c.name for c in changes}
    assert behind == [Change("typescript", "6.5.0", "7.0.0")]
