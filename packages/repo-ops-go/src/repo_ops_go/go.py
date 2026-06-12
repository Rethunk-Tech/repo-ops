"""Go module upgrade check/apply."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import GO_UPGRADE_RE, TIMEOUT_APPLY, TIMEOUT_CHECK

GO_UPGRADE_MODE_ENV = "REPO_OPS_GO_UPGRADE_MODE"
GO_WORK_SYNC_ENV = "REPO_OPS_GO_WORK_SYNC"
_GO_GET_BATCH = 40

# Registry: workspace path → go.work directory.
# Populated by discovery (register_go_work_dir); consulted by classify_go_error
# so that remediation hints can name the go.work file location.
_go_work_dirs: dict[Path, Path] = {}


def register_go_work_dir(ws_path: Path, go_work_dir: Path) -> None:
    """Record that *ws_path* sits under the go.work file in *go_work_dir*.

    Called by upgrade discovery when a go.work file is found between the
    module directory and the git root.  Used by ``classify_go_error`` to
    include an actionable directory path in the remediation hint.
    """
    _go_work_dirs[ws_path.resolve()] = go_work_dir.resolve()


def classify_go_error(stderr: str, ws_path: Path | None = None) -> str:
    """Return an error string, prefixed with ``[env] `` for environment errors.

    Environment errors are failures caused by the local setup (go.work version
    mismatch, missing replace target, missing module cache, missing go.sum
    entry, toolchain mismatch) rather than by the upgrade operation itself.
    Callers downstream may key on the ``[env] `` prefix to separate env
    failures from upgrade failures in reports.

    Pattern catalogue
    -----------------
    go.work directive older than member requirement
        ``listed in go.work file requires go >=`` / ``reading go.work``
    Missing replace target (sibling clone absent)
        ``replace ... points outside`` or ``reading ../<x>/go.mod: ... no such file``
    Module not found in any registry
        ``cannot find module``
    Missing go.sum entry
        ``missing go.sum entry``
    Toolchain constraint not met
        ``go: toolchain``
    """
    text = stderr.strip()
    if not text:
        return text

    go_work_dir: Path | None = None
    if ws_path is not None:
        go_work_dir = _go_work_dirs.get(ws_path.resolve())

    go_work_label = str(go_work_dir) if go_work_dir else "the workspace root"

    # go.work go-directive older than a member module's requirement.
    if "listed in go.work file requires go >=" in text or (
        "reading go.work" in text and "requires go" in text
    ):
        return (
            f"[env] go.work go directive older than member requirement"
            f" — run `go work use` in {go_work_label}; {text}"
        )

    # replace directive points at a sibling that does not exist on disk.
    if "replace" in text and "points outside" in text:
        return (
            f"[env] replace directive points outside the module root"
            f" — clone the missing sibling or remove the replace directive; {text}"
        )
    if (
        "reading" in text
        and "go.mod" in text
        and ("no such file" in text or "does not exist" in text)
    ):
        return (
            f"[env] replace target go.mod missing"
            f" — clone the missing sibling or remove the replace directive; {text}"
        )

    # Module not available in any registry (private module, proxy unreachable, …).
    if "cannot find module" in text:
        return (
            f"[env] module not found — check GOPROXY / GONOSUMCHECK or"
            f" that the module path is correct; {text}"
        )

    # go.sum entry required but absent.
    if "missing go.sum entry" in text:
        return (
            f"[env] missing go.sum entry"
            f" — run `go mod tidy` or `go mod download` in {ws_path or 'the module dir'}; {text}"
        )

    # Toolchain version constraint not satisfied.
    if text.startswith("go: toolchain") or "\ngo: toolchain" in text:
        return f"[env] toolchain constraint not met — update Go or set GOTOOLCHAIN=auto; {text}"  # noqa: S608

    return text


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


def _gomod_require_paths(ws: Workspace) -> set[str] | None:
    """Module paths recorded in the workspace go.mod (direct and indirect).

    ``go list -m -u all`` enumerates the full module graph, including modules
    no package of the build actually needs; upgrading those is undone by
    ``go mod tidy``, so reporting them produces ghost upgrades (issue #113).
    Returns None when go.mod cannot be read (callers then skip filtering).
    """
    proc = run(["go", "mod", "edit", "-json"], ws.path, timeout=TIMEOUT_CHECK)
    if proc.returncode != 0:
        return None
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, dict):
        return None
    requires = doc.get("Require") or []
    return {
        r["Path"]
        for r in requires
        if isinstance(r, dict) and isinstance(r.get("Path"), str)
    }


def _go_work_sync_enabled() -> bool:
    return os.environ.get(GO_WORK_SYNC_ENV, "").strip() == "1"


def _try_go_work_use(ws: Workspace) -> bool:
    """Run ``go work use`` in the registered go.work directory for *ws*.

    Returns True when the command succeeds (exit 0), False otherwise.
    Only called when ``REPO_OPS_GO_WORK_SYNC=1`` is set and a go.work
    directory is registered for this workspace.
    """
    go_work_dir = _go_work_dirs.get(ws.path.resolve())
    if go_work_dir is None:
        return False
    proc = run(["go", "work", "use"], go_work_dir, timeout=TIMEOUT_APPLY)
    return proc.returncode == 0


def _raise_classified(stderr: str, fallback: str, ws_path: Path) -> None:
    """Raise RuntimeError with a classified (possibly ``[env] ``-prefixed) message."""
    msg = classify_go_error(stderr or fallback, ws_path)
    raise RuntimeError(msg)


def _run_with_go_work_retry(
    cmd: list[str], ws: Workspace, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* and, on go.work-version-mismatch error, optionally retry once.

    When ``REPO_OPS_GO_WORK_SYNC=1`` is set and the first run fails with the
    go.work directive mismatch pattern, run ``go work use`` in the registered
    go.work directory and re-issue the same command a single time.  If the
    retry also fails, the retry stderr is used for classification.
    """
    proc = run(cmd, ws.path, timeout=timeout)
    if proc.returncode == 0:
        return proc
    stderr = proc.stderr.strip()
    if (
        _go_work_sync_enabled()
        and (
            "listed in go.work file requires go >=" in stderr
            or ("reading go.work" in stderr and "requires go" in stderr)
        )
        and _try_go_work_use(ws)
    ):
        proc = run(cmd, ws.path, timeout=timeout)
    return proc


def _changes_from_go_list_json(ws: Workspace, *, patch_only: bool) -> list[Change]:
    proc = _run_with_go_work_retry(
        ["go", "list", "-m", "-u", "-json", "all"], ws, timeout=TIMEOUT_CHECK
    )
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go list failed", ws.path)
    recorded: set[str] | None = None
    have_recorded = False
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
        if not have_recorded:
            recorded = _gomod_require_paths(ws)
            have_recorded = True
        if recorded is not None and path not in recorded:
            continue
        changes.append(Change(path, cur, new))
    return changes


def _explicit_go_get_targets(ws: Workspace) -> list[str]:
    proc = _run_with_go_work_retry(
        ["go", "list", "-m", "-u", "-json", "all"], ws, timeout=TIMEOUT_CHECK
    )
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go list failed", ws.path)
    recorded: set[str] | None = None
    have_recorded = False
    targets: list[str] = []
    for mod in _iter_go_list_modules(proc.stdout):
        update = mod.get("Update")
        if not update:
            continue
        path = mod.get("Path") or update.get("Path")
        version = update.get("Version")
        if path and version:
            if not have_recorded:
                recorded = _gomod_require_paths(ws)
                have_recorded = True
            if recorded is not None and path not in recorded:
                continue
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
    proc = _run_with_go_work_retry(
        ["go", "list", "-u", "-m", "all"], ws, timeout=TIMEOUT_CHECK
    )
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go list failed", ws.path)
    recorded: set[str] | None = None
    have_recorded = False
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = GO_UPGRADE_RE.search(line)
        if m:
            parts = line.split()
            name = parts[0]
            cur = parts[1] if len(parts) > 1 and not parts[1].startswith("[") else "?"
            if not have_recorded:
                recorded = _gomod_require_paths(ws)
                have_recorded = True
            if recorded is not None and name not in recorded:
                continue
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
                _raise_classified(proc.stderr, "go get failed", ws.path)
    else:
        proc = _run_with_go_work_retry(
            ["go", "get", "-u=patch", "all"], ws, timeout=TIMEOUT_APPLY
        )
        if proc.returncode != 0:
            _raise_classified(proc.stderr, "go get -u=patch all failed", ws.path)
    proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go mod tidy failed", ws.path)


def _apply_go_mvs(ws: Workspace) -> None:
    proc = _run_with_go_work_retry(
        ["go", "get", "-u", "-t", "all"], ws, timeout=TIMEOUT_APPLY
    )
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go get -u -t all failed", ws.path)
    proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go mod tidy failed", ws.path)
