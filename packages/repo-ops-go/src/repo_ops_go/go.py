"""Go module upgrade check/apply."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import GO_UPGRADE_RE, TIMEOUT_APPLY, TIMEOUT_CHECK

GO_UPGRADE_MODE_ENV = "REPO_OPS_GO_UPGRADE_MODE"
_GO_GET_BATCH = 40


def _valid_go_get_target(target: str) -> bool:
    """Reject module@version strings that could be parsed as go get flags."""
    t = (target or "").strip()
    if not t or t.startswith("-"):
        return False
    path = t.split("@", 1)[0]
    return bool(path) and not path.startswith("-")


def _go_upgrade_mode() -> str:
    mode = os.environ.get(GO_UPGRADE_MODE_ENV, "patch").strip().lower()
    if mode in ("patch", "explicit", "mvs"):
        return mode
    return "patch"


def _is_patch_update(current: str, newer: str) -> bool:
    cur = semver_tuple(current)
    new = semver_tuple(newer)
    if len(cur) < 2 or len(new) < 2:
        return False
    return cur[:2] == new[:2] and new > cur


def _iter_go_list_modules(stdout: str) -> Iterator[dict]:
    """Yield module dicts from ``go list -m -u -json`` stdout.

    Go prints one JSON object per module. Objects may be compact (JSONL) or
    pretty-printed across multiple lines; decode with a streaming decoder.
    """
    decoder = json.JSONDecoder()
    idx = 0
    text = stdout
    length = len(text)
    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        obj, end = decoder.raw_decode(text, idx)
        yield obj
        idx = end


def _changes_from_go_list_json(ws: Workspace, *, patch_only: bool) -> list[Change]:
    proc = run(
        ["go", "list", "-m", "-u", "-json", "all"], ws.path, timeout=TIMEOUT_CHECK
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go list failed")
    changes: list[Change] = []
    for mod in _iter_go_list_modules(proc.stdout):
        update = mod.get("Update")
        if not update:
            continue
        path = mod.get("Path") or update.get("Path") or "?"
        cur = mod.get("Version") or "?"
        new = update.get("Version") or "?"
        if patch_only and not _is_patch_update(cur, new):
            continue
        changes.append(Change(path, cur, new))
    return changes


def _explicit_go_get_targets(ws: Workspace) -> list[str]:
    proc = run(
        ["go", "list", "-m", "-u", "-json", "all"], ws.path, timeout=TIMEOUT_CHECK
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go list failed")
    targets: list[str] = []
    for mod in _iter_go_list_modules(proc.stdout):
        update = mod.get("Update")
        if not update:
            continue
        path = mod.get("Path") or update.get("Path")
        version = update.get("Version")
        if path and version:
            spec = f"{path}@{version}"
            if _valid_go_get_target(spec):
                targets.append(spec)
    return targets


def check_go(ws: Workspace) -> list[Change]:
    mode = _go_upgrade_mode()
    if mode == "mvs":
        return _check_go_mvs(ws)
    return _changes_from_go_list_json(ws, patch_only=(mode == "patch"))


def _check_go_mvs(ws: Workspace) -> list[Change]:
    proc = run(["go", "list", "-u", "-m", "all"], ws.path, timeout=TIMEOUT_CHECK)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go list failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = GO_UPGRADE_RE.search(line)
        if m:
            parts = line.split()
            name = parts[0]
            cur = parts[1] if len(parts) > 1 and not parts[1].startswith("[") else "?"
            changes.append(Change(name, cur, m.group(1)))
    return changes


def apply_go(ws: Workspace) -> None:
    mode = _go_upgrade_mode()
    if mode == "mvs":
        _apply_go_mvs(ws)
        return
    if mode == "explicit":
        targets = _explicit_go_get_targets(ws)
        for i in range(0, len(targets), _GO_GET_BATCH):
            batch = targets[i : i + _GO_GET_BATCH]
            proc = run(["go", "get", "--", *batch], ws.path, timeout=TIMEOUT_APPLY)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "go get failed")
    else:
        proc = run(["go", "get", "-u=patch", "all"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "go get -u=patch all failed")
    proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go mod tidy failed")


def _apply_go_mvs(ws: Workspace) -> None:
    proc = run(["go", "get", "-u", "-t", "all"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go get -u -t all failed")
    proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go mod tidy failed")
