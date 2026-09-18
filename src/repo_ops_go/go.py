"""Go module upgrade check/apply."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.holds import HoldRule, resolve_package_hold
from repo_ops_core.process import run
from repo_ops_core.version_utils import (
    hold_ceiling_spec,
    is_go_pseudo_version,
    version_satisfies_ceiling,
)
from repo_ops_models import Change, Workspace

_GO_GET_BATCH = 40

logger = logging.getLogger(__name__)

# Regex for ``go X.Y`` or ``go X.Y.Z`` directive lines in go.mod / go.work.
_GO_DIRECTIVE_RE = re.compile(r"^go\s+(\d+\.\d+(?:\.\d+)?)$", re.MULTILINE)

# Map: workspace path → go.work directory (go workspace file, not an OCI registry).
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
        return (
            f"[env] toolchain constraint not met"
            f" — install a newer Go, or export GOTOOLCHAIN=auto; {text}"
        )

    return text


def _valid_go_get_target(target: str) -> bool:
    """Reject module@version strings that could be parsed as go get flags."""
    t = (target or "").strip()
    if not t or t.startswith("-"):
        return False
    path = t.split("@", 1)[0]
    return bool(path) and not path.startswith("-")


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
    ``go mod tidy``, so reporting them produces ghost upgrades.
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
    return {r["Path"] for r in requires if isinstance(r, dict) and isinstance(r.get("Path"), str)}


# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# Go's own resolver never crosses a major-version boundary on its own (major
# >=2 is encoded in the import path, e.g. ``.../v2``, and none of the three
# upgrade modes ever propose a differently-majored path) — so a hold here
# caps a same-major minor/patch version, not a major bump. Still useful (e.g.
# "stay on 1.5.x for now"), just lower-stakes than the JS ecosystems.


def _resolve_module_hold(ws: Workspace, module_path: str) -> HoldRule | None:
    return resolve_package_hold(ws, module_path)


def _go_ceiling_query(max_version: str) -> str:
    """Build a ``go get`` version-query for a hold ceiling (e.g. ``<=v6.999999.999999``).

    Components the ceiling omits are padded with a very large sentinel so
    they're effectively unconstrained, matching ``version_satisfies_ceiling``'s
    "ceiling is a prefix" semantics (ceiling ``"6"`` allows any 6.x.y, not just
    exactly ``v6.0.0``).

    A pseudo-version ceiling pins that exact version: Go's comparison queries
    consider only tagged versions, so ``<=v0.0.0-<ts>-<rev>`` matches nothing.
    """
    if is_go_pseudo_version(max_version):
        return "v" + max_version.strip().lstrip("vV")
    return "<=v" + hold_ceiling_spec(max_version).removeprefix("<=")


def _resolved_go_module_version(ws: Workspace, module_path: str) -> str | None:
    """Read the currently resolved version of *module_path* via ``go list -m -json``."""
    proc = run(
        ["go", "list", "-m", "-json", module_path],
        ws.path,
        timeout=TIMEOUT_CHECK,
    )
    if proc.returncode != 0:
        return None
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, dict):
        return None
    version = doc.get("Version")
    return str(version) if version else None


def _filter_held_go_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    """Drop any Change whose module has an active hold rule its target violates."""
    kept: list[Change] = []
    for c in changes:
        rule = _resolve_module_hold(ws, c.name)
        if rule is not None and not version_satisfies_ceiling(c.to, rule.max_version):
            continue
        kept.append(c)
    return kept


def _enforce_version_ceilings(ws: Workspace) -> None:
    """Post-apply clamp-down: correct any direct dependency resolved above its hold ceiling.

    Mirrors the JS ecosystems' ``enforce_holds`` safety net: reads each held
    direct dependency's resolved version via ``go list -m -json``, and if it
    violates the ceiling, runs a targeted ``go get <path>@<=ceiling>`` to
    correct it, followed by ``go mod tidy``. Raises if the correction still
    can't satisfy the ceiling — a hold that silently didn't hold is worse
    than a loud failure. Also catches a hold added *after* the module was
    already installed above the ceiling (``go list -u`` would report it as
    already resolved, so it never appears in ``check_go``'s reported
    changes).
    """
    candidates = _gomod_require_paths(ws)
    if not candidates:
        return
    corrected_any = False
    for module_path in sorted(candidates):
        rule = _resolve_module_hold(ws, module_path)
        if rule is None:
            continue
        resolved = _resolved_go_module_version(ws, module_path)
        if resolved is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        proc = run(
            ["go", "get", f"{module_path}@{_go_ceiling_query(rule.max_version)}"],
            ws.path,
            timeout=TIMEOUT_APPLY,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"hold: failed to clamp {module_path} back to <={rule.max_version} "
                f"(go): {proc.stderr.strip()}"
            )
        landed = _resolved_go_module_version(ws, module_path)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            raise RuntimeError(
                f"hold: {module_path} still at {landed or resolved} after clamp "
                f"attempt, expected <= {rule.max_version} (go)"
            )
        corrected_any = True
    if corrected_any:
        proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            _raise_classified(proc.stderr, "go mod tidy failed", ws.path)


# ---------------------------------------------------------------------------
# go directive / toolchain parsing helpers
# ---------------------------------------------------------------------------


def _parse_go_directive(text: str) -> str | None:
    """Return the version string from the first ``go X.Y[.Z]`` directive line.

    Returns ``None`` when no matching line is found.
    """
    m = _GO_DIRECTIVE_RE.search(text)
    return m.group(1) if m else None


def _go_directive_version_tuple(ver: str) -> tuple[int, ...]:
    """Parse a dotted version string into an integer tuple for comparison."""
    try:
        return tuple(int(p) for p in ver.split("."))
    except ValueError:
        return (0,)


def _go_work_use_members(go_work_dir: Path) -> list[Path]:
    """Return module directories listed in the ``use (...)`` block of go.work.

    Handles both single-line ``use ./foo`` and multi-line ``use (...)`` forms.
    Returns paths resolved relative to *go_work_dir*.
    """
    go_work = go_work_dir / "go.work"
    try:
        text = go_work.read_text(encoding="utf-8")
    except OSError:
        return []
    members: list[Path] = []
    # Multi-line form: use (\n  ./a\n  ./b\n)
    block = re.search(r"\buse\s*\(([^)]*)\)", text, re.DOTALL)
    if block:
        for raw in block.group(1).splitlines():
            p = raw.strip()
            if p:
                members.append((go_work_dir / p).resolve())
    else:
        # Single-line form: use ./foo
        for m in re.finditer(r"^use\s+(\.\/\S+)", text, re.MULTILINE):
            members.append((go_work_dir / m.group(1)).resolve())
    return members


def _plan_go_work_directive_changes(go_work_dir: Path) -> list[Change]:
    """Return a list of Changes needed to bring go.work's ``go`` directive up to date.

    Reads go.work's current ``go`` directive and each member module's go.mod
    ``go`` directive.  When any member requires a newer version than go.work
    currently declares, a single Change is emitted:

        Change("go.work go directive", current_work_ver, required_ver)

    where ``required_ver`` is the highest version found among members.
    Returns ``[]`` when go.work is already current, has no ``go`` directive,
    or when member paths cannot be read.
    """
    go_work = go_work_dir / "go.work"
    try:
        work_text = go_work.read_text(encoding="utf-8")
    except OSError:
        return []
    work_ver = _parse_go_directive(work_text)
    if work_ver is None:
        return []
    work_tuple = _go_directive_version_tuple(work_ver)

    members = _go_work_use_members(go_work_dir)
    required_tuple = work_tuple
    required_ver = work_ver
    for member_dir in members:
        gomod = member_dir / "go.mod"
        try:
            mod_text = gomod.read_text(encoding="utf-8")
        except OSError:
            continue
        member_ver = _parse_go_directive(mod_text)
        if member_ver is None:
            continue
        member_tuple = _go_directive_version_tuple(member_ver)
        if member_tuple > required_tuple:
            required_tuple = member_tuple
            required_ver = member_ver

    if required_tuple > work_tuple:
        return [Change("go.work go directive", work_ver, required_ver)]
    return []


def _raise_classified(stderr: str, fallback: str, ws_path: Path) -> None:
    """Raise RuntimeError with a classified (possibly ``[env] ``-prefixed) message."""
    msg = classify_go_error(stderr or fallback, ws_path)
    raise RuntimeError(msg)


def _changes_from_go_list_json(ws: Workspace) -> list[Change]:
    proc = run(["go", "list", "-m", "-u", "-json", "all"], ws.path, timeout=TIMEOUT_CHECK)
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
        if not have_recorded:
            recorded = _gomod_require_paths(ws)
            have_recorded = True
        if recorded is not None and path not in recorded:
            continue
        changes.append(Change(path, cur, new))
    return changes


def _explicit_go_get_targets(ws: Workspace) -> list[str]:
    proc = run(["go", "list", "-m", "-u", "-json", "all"], ws.path, timeout=TIMEOUT_CHECK)
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
            held_rule = _resolve_module_hold(ws, path)
            if held_rule is not None and not version_satisfies_ceiling(
                version, held_rule.max_version
            ):
                continue
            spec = f"{path}@{version}"
            if _valid_go_get_target(spec):
                targets.append(spec)
    return targets


def check_go(ws: Workspace) -> list[Change]:
    dep_changes = _filter_held_go_changes(ws, _changes_from_go_list_json(ws))

    # Prepend go.work directive sync changes (textual parse, no subprocess).
    go_work_dir = _go_work_dirs.get(ws.path.resolve())
    work_changes: list[Change] = []
    if go_work_dir is not None:
        work_changes = _plan_go_work_directive_changes(go_work_dir)
    return work_changes + dep_changes


def _apply_go_work_directive(ws: Workspace) -> None:
    """Run ``go work use`` in the registered go.work directory for *ws* when needed.

    Called by ``apply_go`` before the dependency upgrade so that any directive
    mismatch that would block ``go list`` is resolved first.  Silently skips
    when no go.work directory is registered for this workspace.
    """
    go_work_dir = _go_work_dirs.get(ws.path.resolve())
    if go_work_dir is None:
        return
    work_changes = _plan_go_work_directive_changes(go_work_dir)
    if not work_changes:
        return
    proc = run(["go", "work", "use"], go_work_dir, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go work use failed", ws.path)


def apply_go(ws: Workspace) -> None:
    # 1. Sync go.work directive first (prevents downstream go list failures).
    _apply_go_work_directive(ws)

    targets = _explicit_go_get_targets(ws)
    for i in range(0, len(targets), _GO_GET_BATCH):
        batch = targets[i : i + _GO_GET_BATCH]
        proc = run(["go", "get", "--", *batch], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            _raise_classified(proc.stderr, "go get failed", ws.path)
    proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        _raise_classified(proc.stderr, "go mod tidy failed", ws.path)
    _enforce_version_ceilings(ws)
