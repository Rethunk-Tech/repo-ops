#!/usr/bin/env python3
"""Fleet vulnerability sweep across local git repos (read-only unless --fix).

Discovers workspaces using the same git-repo discovery machinery as the other
subcommands, then runs the native auditor for each ecosystem:

  go      → govulncheck ./...
  node    → npm audit --json / yarn npm audit --json / pnpm audit --json / bun audit
  py-uv   → uv run pip-audit --format json (if pip-audit is available), else skipped
  rust    → cargo audit --json

If the required tool is not on PATH the workspace is recorded as "skipped".
Subprocess calls are always mocked in tests; no network traffic in unit tests.

Severity normalization
----------------------
Each ecosystem maps its own severity vocabulary to the canonical four buckets:

  critical / high / moderate / low / **unknown**

  npm / pnpm / yarn
    JSON key "critical" → critical
    JSON key "high"     → high
    JSON key "moderate" / "medium" → moderate
    anything else       → low

  cargo-audit
    CVSS score ≥9.0 → critical, ≥7.0 → high, ≥4.0 → moderate, <4.0 → low
    no CVSS vector  → moderate (score defaults to 5.0)

  govulncheck
    No per-finding severity is emitted.  Every finding counts as **unknown**,
    reported in a separate ``unknown`` bucket.  Use ``--fail-on-unknown`` to
    treat unknown-severity findings as failures.

  pip-audit
    CVE alias present, no fix   → critical
    CVE alias present, fix known → high
    no CVE alias                → moderate

  bun audit
    Text output extracted by regex ("N critical/high/moderate/low").

Console entry point: ``repo-ops audit`` (see ``--help`` for flags).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_ops_core.cli import (
    add_follow_symlinks_argument,
    add_json_argument,
    add_roots_argument,
    add_sarif_argument,
    add_verbose_argument,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.config import (
    apply_config_to_env,
    load_global_config,
    load_repo_config,
)
from repo_ops_core.discovery import discover_repos
from repo_ops_core.git import repo_label
from repo_ops_core.table import column_width, format_cell, join_row
from repo_ops_core.walk import iter_scanned_dirs, manifest_kinds

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_SEV_ORDER = ("critical", "high", "moderate", "low")
_FAIL_ON_CHOICES = ("critical", "high", "moderate", "low", "never")

# Thresholds: the numeric code of the worst severity (lower = worse).
_SEV_RANK: dict[str | None, int] = {
    "critical": 0,
    "high": 1,
    "moderate": 2,
    "low": 3,
    None: 4,
}


def _worse(a: str | None, b: str | None) -> str | None:
    """Return the more-severe of two severity strings (or None)."""
    if _SEV_RANK.get(a, 4) <= _SEV_RANK.get(b, 4):
        return a
    return b


@dataclass
class VulnCount:
    critical: int = 0
    high: int = 0
    moderate: int = 0
    low: int = 0
    unknown: int = 0  # govulncheck: no per-finding severity

    @property
    def total(self) -> int:
        return self.critical + self.high + self.moderate + self.low

    @property
    def total_with_unknown(self) -> int:
        return self.total + self.unknown

    @property
    def worst(self) -> str | None:
        for sev in _SEV_ORDER:
            if getattr(self, sev) > 0:
                return sev
        return None

    def as_dict(self) -> dict[str, int]:
        return {
            "critical": self.critical,
            "high": self.high,
            "moderate": self.moderate,
            "low": self.low,
            "unknown": self.unknown,
        }


@dataclass
class RemediationItem:
    """A single actionable fix surfaced by --fix-plan.

    ``safe_version`` is the known-safe version string emitted by the auditor:
    an exact version for pip-audit and npm v7+, or a version *constraint*
    (e.g. ``">= 1.5.5"``) for cargo-audit and npm v6 advisories.
    """

    package: str
    safe_version: str
    ecosystem: str
    workspace: str

    def as_dict(self) -> dict[str, str]:
        return {
            "package": self.package,
            "safe_version": self.safe_version,
            "ecosystem": self.ecosystem,
            "workspace": self.workspace,
        }


@dataclass
class ApplyOutcome:
    """Result of a single workspace apply in ``--fix`` mode."""

    workspace: str
    status: str  # "ok" | "skip" | "fail"
    error: str = ""
    changes_count: int = 0

    def as_dict(self) -> dict[str, str | int]:
        return {
            "workspace": self.workspace,
            "status": self.status,
            "error": self.error,
            "changes_count": self.changes_count,
        }


@dataclass
class WorkspaceResult:
    label: str
    path: str
    ecosystem: str
    manager: str
    status: str  # "ok" | "skipped" | "error" | "vulns"
    skip_reason: str = ""
    counts: VulnCount = field(default_factory=VulnCount)
    error: str = ""
    remediations: list[RemediationItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ecosystem detection
# ---------------------------------------------------------------------------


def _eco_to_manager(ecosystem: str) -> str:
    """Map an audit ecosystem name to the upgrade-registry manager name."""
    return {
        "py-uv": "uv",
        "node": "npm",  # default; detect actual manager from lockfile at call site
        "rust": "cargo",
        "go": "go",
    }.get(ecosystem, ecosystem)


def _try_register_manager(manager: str) -> bool:
    """Lazy-import and register a manager's checker/applier with the upgrade registry.

    Uses the same per-ecosystem ``register()`` entry points as ``repo_ops._register``
    (the main CLI), but loads only the manager that is actually needed to avoid
    pulling all ecosystem packages into the audit import tree unconditionally.

    Returns ``True`` if the manager is (or was) registered successfully.
    Returns ``False`` if the package for that manager is not installed.
    """
    from repo_ops_upgrade import registry

    if manager in registry.APPLIERS:
        return True
    try:
        if manager == "npm":
            from repo_ops_js_npm import register as _r
        elif manager == "yarn":
            from repo_ops_js_yarn import register as _r
        elif manager == "pnpm":
            from repo_ops_js_pnpm import register as _r
        elif manager == "bun":
            from repo_ops_js_bun import register as _r
        elif manager == "uv":
            from repo_ops_py_uv import register as _r
        elif manager == "cargo":
            from repo_ops_rust import register as _r
        elif manager == "go":
            from repo_ops_go import register as _r
        else:
            return False
        _r()
        return True
    except ImportError:
        return False


def _detect_node_manager(ws: Path) -> str | None:
    """Return the Node package manager for *ws*, or None if not a Node project."""
    if not (ws / "package.json").exists():
        return None
    for lockfile, manager in (
        ("package-lock.json", "npm"),
        ("pnpm-lock.yaml", "pnpm"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
        ("yarn.lock", "yarn"),
    ):
        if (ws / lockfile).exists():
            return manager
    # package.json with no lockfile → default npm
    return "npm"


def _detect_workspaces(repo: str) -> list[tuple[str, str, str]]:
    """Return list of (workspace_path, ecosystem, manager) for a repo.

    Walks the repo tree and yields one entry per discovered manifest kind.
    A single directory may contribute multiple (path, eco, mgr) tuples (e.g. a
    mixed Go + Node repo at the same root).
    """
    root = Path(repo)
    results: list[tuple[str, str, str]] = []
    for mdir, names in iter_scanned_dirs(root):
        kinds = manifest_kinds(names)
        for kind in kinds:
            if kind == "node":
                mgr = _detect_node_manager(mdir) or "npm"
                results.append((str(mdir), "node", mgr))
            elif kind == "go":
                results.append((str(mdir), "go", "go"))
            elif kind == "rust":
                results.append((str(mdir), "rust", "cargo"))
            elif kind == "py":
                # Only audit uv workspaces (pip-audit via uv)
                if (mdir / "uv.lock").exists() or (mdir / "pyproject.toml").exists():
                    results.append((str(mdir), "py-uv", "uv"))
    return results


# ---------------------------------------------------------------------------
# Per-ecosystem auditors
# ---------------------------------------------------------------------------

_SUBPROCESS_TIMEOUT = 120
_GOVULNCHECK_TIMEOUT = 60


def _run(
    cmd: list[str], cwd: str, *, timeout: float | None = None
) -> tuple[int, str, str]:
    """Run *cmd* in *cwd*; return (returncode, stdout, stderr)."""
    cap = timeout if timeout is not None else _SUBPROCESS_TIMEOUT
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=cap,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"timeout after {cap}s"
    except FileNotFoundError as exc:
        return 1, "", str(exc)


def _audit_go(ws: str) -> WorkspaceResult:
    label = ws
    if shutil.which("govulncheck") is None:
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="go",
            manager="go",
            status="skipped",
            skip_reason="govulncheck not on PATH",
        )
    rc, stdout, stderr = _run(
        ["govulncheck", "-json", "./..."], ws, timeout=_GOVULNCHECK_TIMEOUT
    )
    counts = VulnCount()
    # govulncheck --json emits one JSON object per line.
    # govulncheck does NOT emit per-finding severity; all findings are counted
    # in the ``unknown`` bucket.  Use --fail-on-unknown to gate on these.
    parse_ok = False
    findings: list[dict[str, Any]] = []
    try:
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            parse_ok = True
            if "finding" in obj:
                counts.unknown += 1
                findings.append(obj["finding"])
    except (json.JSONDecodeError, ValueError):
        pass

    if not parse_ok and rc != 0:
        # Fallback: non-zero exit with unparseable output → 1 unknown finding
        counts.unknown += 1

    if counts.total_with_unknown == 0 and rc != 0:
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="go",
            manager="go",
            status="error",
            error=(stderr or stdout or "govulncheck exited non-zero").strip()[:200],
        )
    has_findings = counts.total_with_unknown > 0
    status = "vulns" if has_findings else "ok"
    remediations = _extract_remediations_govulncheck_json(findings, ws)
    return WorkspaceResult(
        label=label,
        path=ws,
        ecosystem="go",
        manager="go",
        status=status,
        counts=counts,
        remediations=remediations,
    )


def _probe_bun_audit(ws: str) -> bool:
    """Return True when the installed bun supports the ``audit`` subcommand.

    Bun versions prior to a certain release do not have ``bun audit``.  We probe
    by running ``bun audit --version`` (or the subcommand help) with a short
    timeout.  A non-zero exit with an "unknown subcommand" style message →
    False.
    """
    rc, stdout, stderr = _run(["bun", "audit", "--help"], ws)
    combined = (stdout + stderr).lower()
    # bun emits "unknown command" / "unrecognized subcommand" when audit is absent;
    # any other non-zero exit (e.g. no package.json) means the subcommand exists.
    return not (
        rc != 0
        and any(
            kw in combined
            for kw in ("unknown command", "unrecognized", "no such subcommand")
        )
    )


def _audit_node(ws: str, manager: str) -> WorkspaceResult:
    label = ws
    cmd_map: dict[str, list[str]] = {
        "npm": ["npm", "audit", "--json"],
        "yarn": ["yarn", "npm", "audit", "--json"],
        "pnpm": ["pnpm", "audit", "--json"],
        # bun audit --json supported since ~1.2.x: prints raw npm-advisory JSON.
        # Older bun versions that lack --json emit ANSI text; JSON parse failure
        # triggers the _parse_bun_audit_text fallback below.
        "bun": ["bun", "audit", "--json"],
    }
    bin_map: dict[str, str] = {
        "npm": "npm",
        "yarn": "yarn",
        "pnpm": "pnpm",
        "bun": "bun",
    }
    tool = bin_map.get(manager, manager)
    if shutil.which(tool) is None:
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="node",
            manager=manager,
            status="skipped",
            skip_reason=f"{tool} not on PATH",
        )

    # bun: probe for audit subcommand availability before running
    if manager == "bun" and not _probe_bun_audit(ws):
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="node",
            manager=manager,
            status="skipped",
            skip_reason="bun audit subcommand not available in this bun version",
        )

    cmd = cmd_map.get(manager, ["npm", "audit", "--json"])
    rc, stdout, stderr = _run(cmd, ws)
    counts = VulnCount()

    if manager == "bun":
        # bun audit --json emits npm-advisory JSON (same schema as npm audit --json).
        # Older bun versions that predate --json emit ANSI text; json.loads failure
        # triggers the _parse_bun_audit_text fallback so both paths are handled.
        # The text fallback has no per-package fix-version data, so remediations
        # stay empty in that path (only the JSON path is actionable for --fix).
        _json_ok = False
        bun_remediations: list[RemediationItem] = []
        if stdout.strip():
            try:
                _bun_data = json.loads(stdout)
                counts = _parse_npm_audit_json(_bun_data)
                bun_remediations = _extract_remediations_npm_json(_bun_data, ws)
                _json_ok = True
            except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                pass
        if not _json_ok:
            counts = _parse_bun_audit_text(stdout + stderr)
        status = "vulns" if counts.total > 0 else ("error" if rc != 0 else "ok")
        if status == "error":
            return WorkspaceResult(
                label=label,
                path=ws,
                ecosystem="node",
                manager=manager,
                status="error",
                error=(stderr or stdout or "bun audit exited non-zero").strip()[:200],
            )
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="node",
            manager=manager,
            status=status,
            counts=counts,
            remediations=bun_remediations,
        )

    # npm / pnpm / yarn all emit JSON
    remediations: list[RemediationItem] = []
    try:
        data = json.loads(stdout)
        counts = _parse_npm_audit_json(data)
        remediations = _extract_remediations_npm_json(data, ws)
    except (json.JSONDecodeError, ValueError, KeyError):
        if rc != 0:
            return WorkspaceResult(
                label=label,
                path=ws,
                ecosystem="node",
                manager=manager,
                status="error",
                error=(stderr or stdout or "audit parse error").strip()[:200],
            )
        return WorkspaceResult(
            label=label, path=ws, ecosystem="node", manager=manager, status="ok"
        )

    status = "vulns" if counts.total > 0 else "ok"
    return WorkspaceResult(
        label=label,
        path=ws,
        ecosystem="node",
        manager=manager,
        status=status,
        counts=counts,
        remediations=remediations,
    )


def _parse_npm_audit_json(data: dict[str, Any]) -> VulnCount:
    """Parse npm/pnpm/yarn audit JSON into VulnCount.

    Severity mapping
    ----------------
    npm/pnpm/yarn JSON severity keys → canonical buckets:

      "critical"           → critical
      "high"               → high
      "moderate" / "medium" → moderate
      (anything else)      → low

    Handles both npm v6 (metadata.vulnerabilities) and v7+ (vulnerabilities map).
    pnpm emits the same v7-style format. yarn npm audit --json uses the same schema.
    """
    counts = VulnCount()
    # v7+ format: top-level "metadata" → "vulnerabilities" dict
    meta = data.get("metadata", {})
    vulns = meta.get("vulnerabilities", {})
    if isinstance(vulns, dict) and vulns:
        counts.critical = int(vulns.get("critical", 0))
        counts.high = int(vulns.get("high", 0))
        counts.moderate = int(vulns.get("moderate", 0))
        counts.low = int(vulns.get("low", 0))
        return counts

    # v7+ alternative: top-level "vulnerabilities" map of advisory objects
    top_vulns = data.get("vulnerabilities", {})
    if isinstance(top_vulns, dict):
        seen: set[str] = set()
        for _pkg, adv in top_vulns.items():
            via = adv.get("via", [])
            for entry in via:
                if isinstance(entry, dict):
                    adv_id = str(entry.get("url", entry.get("source", "")))
                    if adv_id and adv_id in seen:
                        continue
                    seen.add(adv_id)
                    sev = str(entry.get("severity", "moderate")).lower()
                    if sev == "critical":
                        counts.critical += 1
                    elif sev == "high":
                        counts.high += 1
                    elif sev in ("moderate", "medium"):
                        counts.moderate += 1
                    else:
                        counts.low += 1
        if counts.total > 0:
            return counts

    # Summarise from top-level "advisories" (npm v6)
    advisories = data.get("advisories", {})
    if isinstance(advisories, dict):
        for _adv_id, adv in advisories.items():
            sev = str(adv.get("severity", "moderate")).lower()
            if sev == "critical":
                counts.critical += 1
            elif sev == "high":
                counts.high += 1
            elif sev in ("moderate", "medium"):
                counts.moderate += 1
            else:
                counts.low += 1

    return counts


_BUN_AUDIT_RE = re.compile(r"(\d+)\s+(critical|high|moderate|low)", re.IGNORECASE)


def _parse_bun_audit_text(text: str) -> VulnCount:
    counts = VulnCount()
    for m in _BUN_AUDIT_RE.finditer(text):
        n = int(m.group(1))
        sev = m.group(2).lower()
        if sev == "critical":
            counts.critical += n
        elif sev == "high":
            counts.high += n
        elif sev == "moderate":
            counts.moderate += n
        else:
            counts.low += n
    return counts


# ---------------------------------------------------------------------------
# Remediation extraction helpers
# ---------------------------------------------------------------------------


def _extract_min_version_from_constraint(constraint: str) -> str | None:
    """Extract the minimum exact version from a semver constraint string.

    Handles:
    - Exact version: ``"1.5.5"`` → ``"1.5.5"``
    - Lower-bound constraint: ``">= 1.5.5"`` or ``">=1.5.5, <2.0"`` → ``"1.5.5"``

    Returns ``None`` when the constraint cannot be resolved to a concrete version.
    This function is used by ``_apply_cargo_targeted`` to convert cargo-audit
    ``patched.requirements`` constraints to the exact version accepted by
    ``cargo update --precise``.
    """
    constraint = constraint.strip()
    # Already an exact version — starts with a digit.
    if re.match(r"^\d+\.\d+", constraint):
        return constraint.split()[0].split(",")[0].strip().rstrip(".,;")
    # Extract ">=" lower bounds (e.g. ">= 1.5.5" or ">=1.5.5").
    geqs = re.findall(r">=?\s*([\d]+\.[\d]+(?:\.[\d]+)*)", constraint)
    if geqs:
        try:
            return max(geqs, key=lambda v: tuple(int(x) for x in v.split(".")))
        except (ValueError, TypeError):
            return geqs[-1]
    return None


def _apply_py_uv_targeted(ws_path: str, items: list[RemediationItem]) -> ApplyOutcome:
    """Pin advisory-vulnerable packages to their exact safe versions via ``uv add``.

    Runs ``uv add <pkg>==<safe_version>`` for each item.  Stops and returns a
    ``"fail"`` outcome on the first non-zero exit.

    Unlike the broad ``uv sync --upgrade`` path used by the regular upgrade
    orchestration, this targets only the specific packages with known
    vulnerabilities, at exactly the advisory-safe minimum version — avoiding
    unnecessary version jumps on unrelated dependencies.
    """
    added: list[str] = []
    for item in items:
        safe = item.safe_version.strip()
        if not safe:
            continue
        rc, _stdout, stderr = _run(["uv", "add", f"{item.package}=={safe}"], ws_path)
        if rc != 0:
            return ApplyOutcome(
                workspace=ws_path,
                status="fail",
                error=(
                    f"uv add {item.package}=={safe}: "
                    f"{(stderr or _stdout or 'failed')[:200]}"
                ),
            )
        added.append(item.package)
    return ApplyOutcome(workspace=ws_path, status="ok", changes_count=len(added))


def _apply_cargo_targeted(ws_path: str, items: list[RemediationItem]) -> ApplyOutcome:
    """Pin advisory-vulnerable Rust crates to their safe versions via ``cargo update``.

    Parses each item's ``safe_version`` constraint (e.g. ``">= 1.5.5"``) to an
    exact version and runs ``cargo update -p <crate> --precise <version>``.

    Unlike a broad ``cargo update`` that bumps every dependency, this targets
    only the specific vulnerable crates identified by ``cargo audit``.

    Items whose constraint cannot be resolved to a concrete version are skipped
    (non-fatal); if no items can be resolved the outcome is ``"ok"`` with zero
    changes rather than an error.
    """
    updated: list[str] = []
    for item in items:
        ver = _extract_min_version_from_constraint(item.safe_version)
        if not ver:
            continue  # constraint cannot be resolved to an exact version; skip
        rc, _stdout, stderr = _run(
            ["cargo", "update", "-p", item.package, "--precise", ver], ws_path
        )
        if rc != 0:
            return ApplyOutcome(
                workspace=ws_path,
                status="fail",
                error=(
                    f"cargo update -p {item.package} --precise {ver}: "
                    f"{(stderr or _stdout or 'failed')[:200]}"
                ),
            )
        updated.append(item.package)
    return ApplyOutcome(workspace=ws_path, status="ok", changes_count=len(updated))


def _apply_go_targeted(ws_path: str, items: list[RemediationItem]) -> ApplyOutcome:
    """Bump vulnerable Go modules to their fixed versions via ``go get`` + ``go mod tidy``.

    Runs ``go get <module>@<fixed_version>`` for each item, then a single
    ``go mod tidy`` to reconcile ``go.sum``/indirect requires after retargeting.

    Unlike cargo's constraint strings, govulncheck's ``fixed_version`` is
    already an exact version — no constraint parsing needed. Stops and
    returns a ``"fail"`` outcome on the first non-zero ``go get`` exit.
    ``go mod tidy`` only runs when at least one ``go get`` succeeded, so a
    call with no resolvable items is a no-op ``"ok"`` outcome with zero
    changes.
    """
    updated: list[str] = []
    for item in items:
        ver = item.safe_version.strip()
        if not ver:
            continue
        rc, _stdout, stderr = _run(["go", "get", f"{item.package}@{ver}"], ws_path)
        if rc != 0:
            return ApplyOutcome(
                workspace=ws_path,
                status="fail",
                error=(
                    f"go get {item.package}@{ver}: "
                    f"{(stderr or _stdout or 'failed')[:200]}"
                ),
            )
        updated.append(item.package)
    if updated:
        rc, _stdout, stderr = _run(["go", "mod", "tidy"], ws_path)
        if rc != 0:
            return ApplyOutcome(
                workspace=ws_path,
                status="fail",
                error=f"go mod tidy: {(stderr or _stdout or 'failed')[:200]}",
            )
    return ApplyOutcome(workspace=ws_path, status="ok", changes_count=len(updated))


def _extract_remediations_pip_audit(data: list[Any], ws: str) -> list[RemediationItem]:
    """Extract (package → highest known-safe fix_version) from pip-audit JSON.

    pip-audit JSON structure::

        [{"name": "requests", "version": "2.28.0",
          "vulns": [{"id": "GHSA-...",
                     "aliases": ["CVE-..."],
                     "fix_versions": ["2.31.0"]}]}]

    A package may have multiple vulnerabilities each with their own
    ``fix_versions``.  We take the highest version across all vulns as the
    single safe target (a package bumped past the highest fix satisfies every
    advisory simultaneously).
    """
    # package name → highest safe version seen
    best: dict[str, str] = {}
    for pkg in data:
        if not isinstance(pkg, dict):
            continue
        name = str(pkg.get("name") or "")
        if not name:
            continue
        for vuln in pkg.get("vulns", []):
            if not isinstance(vuln, dict):
                continue
            for fv in vuln.get("fix_versions", []):
                fv = str(fv).strip()
                if not fv:
                    continue
                prev = best.get(name)
                if prev is None:
                    best[name] = fv
                else:
                    # Pick higher version; fall back to string compare on error
                    try:
                        if tuple(int(x) for x in fv.split(".")) > tuple(
                            int(x) for x in prev.split(".")
                        ):
                            best[name] = fv
                    except (ValueError, TypeError):
                        if fv > prev:
                            best[name] = fv
    return [
        RemediationItem(package=pkg, safe_version=ver, ecosystem="py-uv", workspace=ws)
        for pkg, ver in sorted(best.items())
    ]


def _extract_remediations_npm_json(
    data: dict[str, Any], ws: str
) -> list[RemediationItem]:
    """Extract known-safe fix versions from npm/pnpm/yarn audit JSON.

    Handles two formats:

    v7+ vulnerabilities map
        ``vulnerabilities.<pkg>.fixAvailable`` can be a dict with ``version``
        (exact safe version) or ``True``/``False``.

    v6 advisories map
        ``advisories.<id>.patched_versions`` is a version constraint string
        (e.g. ``">=4.17.21"``).

    Only entries with a concrete version (or constraint) are included; bare
    ``fixAvailable: true`` (no version) is not actionable without a registry
    lookup so it is skipped.
    """
    items: dict[str, str] = {}  # package → safe_version

    # v7+ top-level "vulnerabilities" map
    top_vulns = data.get("vulnerabilities", {})
    if isinstance(top_vulns, dict):
        for pkg_name, adv in top_vulns.items():
            if not isinstance(adv, dict):
                continue
            fix = adv.get("fixAvailable")
            if isinstance(fix, dict):
                ver = str(fix.get("version") or "").strip()
                name = str(fix.get("name") or pkg_name).strip()
                if ver and name:
                    items.setdefault(name, ver)

    # v6 advisories fallback
    advisories = data.get("advisories", {})
    if isinstance(advisories, dict) and not items:
        for _adv_id, adv in advisories.items():
            if not isinstance(adv, dict):
                continue
            pkg_name = str(adv.get("module_name") or "").strip()
            patched = str(adv.get("patched_versions") or "").strip()
            if pkg_name and patched and patched not in ("<none>", ""):
                items.setdefault(pkg_name, patched)

    return [
        RemediationItem(package=pkg, safe_version=ver, ecosystem="node", workspace=ws)
        for pkg, ver in sorted(items.items())
    ]


def _extract_remediations_cargo_json(
    data: dict[str, Any], ws: str
) -> list[RemediationItem]:
    """Extract patched-version requirements from cargo audit JSON.

    cargo audit JSON structure::

        {"vulnerabilities": {"list": [{
            "advisory": {
                "package": "regex",
                "patched": {"requirements": [">= 1.5.5"]}
            },
            "package": {"name": "regex", "version": "1.5.4"}
        }]}}

    ``patched.requirements`` is a list of semver requirement strings.  We join
    them as a comma-separated constraint (most advisories have exactly one).
    """
    items: dict[str, str] = {}
    vuln_section = data.get("vulnerabilities", {})
    for vuln in vuln_section.get("list", []):
        if not isinstance(vuln, dict):
            continue
        advisory = vuln.get("advisory", {})
        if not isinstance(advisory, dict):
            continue
        # Package name: prefer advisory.package (str), fall back to package.name
        pkg_section = vuln.get("package", {})
        name = str(
            advisory.get("package") or (pkg_section or {}).get("name") or ""
        ).strip()
        if not name:
            continue
        patched = advisory.get("patched") or {}
        reqs = patched.get("requirements", []) if isinstance(patched, dict) else []
        if not reqs:
            continue
        constraint = ", ".join(str(r) for r in reqs if r)
        if constraint:
            items.setdefault(name, constraint)
    return [
        RemediationItem(package=pkg, safe_version=ver, ecosystem="rust", workspace=ws)
        for pkg, ver in sorted(items.items())
    ]


def _extract_remediations_govulncheck_json(
    findings: list[dict[str, Any]], ws: str
) -> list[RemediationItem]:
    """Extract fixed-version remediations from govulncheck ``finding`` objects.

    govulncheck ``-json`` emits one JSON object per line; ``finding`` entries
    look like::

        {"finding": {"osv": "GO-2023-1795", "fixed_version": "v1.9.1",
                      "trace": [{"module": "golang.org/x/net", "version": "v1.8.0"}]}}

    ``fixed_version`` is the OSV-recommended safe version; ``trace[0].module``
    is the vulnerable module (the frame closest to the vulnerable symbol
    itself). Findings missing either field are skipped (non-fatal) —
    govulncheck sometimes reports findings with no resolvable fix (e.g. an
    unfixed advisory) or no trace (module-only scans).
    """
    items: dict[str, str] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        fixed = str(finding.get("fixed_version") or "").strip()
        if not fixed:
            continue
        trace = finding.get("trace") or []
        if not trace or not isinstance(trace[0], dict):
            continue
        module = str(trace[0].get("module") or "").strip()
        if not module:
            continue
        items.setdefault(module, fixed)
    return [
        RemediationItem(package=pkg, safe_version=ver, ecosystem="go", workspace=ws)
        for pkg, ver in sorted(items.items())
    ]


def _probe_pip_audit(ws: str) -> bool:
    """Return True when pip-audit is available via ``uv run pip-audit --version``."""
    rc, stdout, _stderr = _run(["uv", "run", "pip-audit", "--version"], ws)
    if rc != 0:
        return False
    # pip-audit --version outputs something like "pip-audit 2.x.y"
    return "pip-audit" in (stdout + _stderr).lower()


def _audit_py_uv(ws: str) -> WorkspaceResult:
    label = ws
    if shutil.which("uv") is None:
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="py-uv",
            manager="uv",
            status="skipped",
            skip_reason="uv not on PATH",
        )
    # Probe: is pip-audit available in this uv environment?
    if not _probe_pip_audit(ws):
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="py-uv",
            manager="uv",
            status="skipped",
            skip_reason="pip-audit not available (uv run pip-audit --version failed)",
        )
    rc, stdout, stderr = _run(
        ["uv", "run", "pip-audit", "--format", "json", "--skip-editable"], ws
    )
    counts = VulnCount()
    # pip-audit severity mapping:
    #   CVE alias present, no fix_versions  → critical
    #   CVE alias present, fix_versions known → high
    #   no CVE alias                        → moderate
    remediations: list[RemediationItem] = []
    try:
        data = json.loads(stdout)
        # pip-audit JSON: list of {"name": ..., "version": ..., "vulns": [...]}
        for pkg in data:
            for vuln in pkg.get("vulns", []):
                fix_versions = vuln.get("fix_versions", [])
                aliases = vuln.get("aliases", [])
                is_cve = any(str(a).startswith("CVE-") for a in aliases)
                if is_cve and not fix_versions:
                    counts.critical += 1
                elif is_cve:
                    counts.high += 1
                else:
                    counts.moderate += 1
        remediations = _extract_remediations_pip_audit(data, ws)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        if rc != 0:
            return WorkspaceResult(
                label=label,
                path=ws,
                ecosystem="py-uv",
                manager="uv",
                status="error",
                error=(stderr or stdout or "pip-audit parse error").strip()[:200],
            )

    status = "vulns" if counts.total > 0 else "ok"
    return WorkspaceResult(
        label=label,
        path=ws,
        ecosystem="py-uv",
        manager="uv",
        status=status,
        counts=counts,
        remediations=remediations,
    )


def _audit_rust(ws: str) -> WorkspaceResult:
    label = ws
    if shutil.which("cargo") is None:
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="rust",
            manager="cargo",
            status="skipped",
            skip_reason="cargo not on PATH",
        )
    # Check cargo-audit is installed (subcommand of cargo)
    rc_check, _, _ = _run(["cargo", "audit", "--version"], ws)
    if rc_check != 0:
        return WorkspaceResult(
            label=label,
            path=ws,
            ecosystem="rust",
            manager="cargo",
            status="skipped",
            skip_reason="cargo-audit not installed (cargo audit --version failed)",
        )
    rc, stdout, stderr = _run(["cargo", "audit", "--json"], ws)
    counts = VulnCount()
    # cargo-audit severity mapping (via CVSS v3 vector → approximated score):
    #   score ≥9.0 → critical
    #   score ≥7.0 → high
    #   score ≥4.0 → moderate
    #   score < 4.0 → low
    #   no CVSS vector → moderate (default score 5.0)
    remediations: list[RemediationItem] = []
    try:
        data = json.loads(stdout)
        # cargo audit --json: {"vulnerabilities": {"list": [...], "count": N, ...}}
        vuln_section = data.get("vulnerabilities", {})
        for vuln in vuln_section.get("list", []):
            advisory = vuln.get("advisory", {})
            cvss = advisory.get("cvss", "") or ""
            # Parse CVSS score to severity bucket (no CVSS = moderate)
            score = _cvss_to_score(cvss)
            if score >= 9.0:
                counts.critical += 1
            elif score >= 7.0:
                counts.high += 1
            elif score >= 4.0:
                counts.moderate += 1
            else:
                counts.low += 1
        remediations = _extract_remediations_cargo_json(data, ws)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        if rc != 0:
            return WorkspaceResult(
                label=label,
                path=ws,
                ecosystem="rust",
                manager="cargo",
                status="error",
                error=(stderr or stdout or "cargo audit parse error").strip()[:200],
            )

    status = "vulns" if counts.total > 0 else "ok"
    return WorkspaceResult(
        label=label,
        path=ws,
        ecosystem="rust",
        manager="cargo",
        status=status,
        counts=counts,
        remediations=remediations,
    )


# CVSS v3.x base-score metric weights (see the official CVSS v3.1 specification,
# section 7.4 / Annex A). ``PR`` (Privileges Required) has two weight tables
# depending on Scope (Unchanged vs Changed).
_CVSS_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_CVSS_AC = {"L": 0.77, "H": 0.44}
_CVSS_PR_SCOPE_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}
_CVSS_PR_SCOPE_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.5}
_CVSS_UI = {"N": 0.85, "R": 0.62}
_CVSS_CIA = {"N": 0.0, "L": 0.22, "H": 0.56}

_CVSS_VECTOR_RE = re.compile(
    r"AV:([NALP]).*?AC:([LH]).*?PR:([NLH]).*?UI:([NR]).*?S:([UC]).*?"
    r"C:([NLH]).*?I:([NLH]).*?A:([NLH])"
)


def _cvss_roundup(value: float) -> float:
    """CVSS spec ``Roundup()``: round UP to the nearest 0.1 (not nearest)."""
    int_input = round(value * 100000)
    if int_input % 10000 == 0:
        return int_input / 100000.0
    return (int_input // 10000 + 1) / 10.0


def _cvss_to_score(cvss_vector: str) -> float:
    """Compute the CVSS v3.x base score from a full vector string.

    cargo-audit embeds the advisory's CVSS vector (e.g.
    ``CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N``) in its JSON output but
    does not emit a precomputed numeric base score, so it is derived here per
    the official CVSS v3.1 base-score formula using the FULL
    exploitability + impact vector (AV/AC/PR/UI/Scope + C/I/A) — not just an
    average of the Confidentiality/Integrity/Availability impact letters.

    A C/I/A-only average previously under-scored network-exploitable,
    no-privilege, no-interaction vulnerabilities: e.g.
    ``C:H/I:N/A:N`` with ``AV:N/AC:L/PR:N/UI:N/S:U`` computed to ~3.3
    (moderate bucket) under the old approximation, when its real CVSS v3.1
    base score is ~7.5 (high) — allowing a real High-severity vuln to pass
    ``--fail-on high`` undetected (repo-ops#142).

    Returns ``5.0`` (moderate bucket) when the vector is missing or cannot be
    parsed.
    """
    m = _CVSS_VECTOR_RE.search(cvss_vector)
    if not m:
        return 5.0  # moderate
    av, ac, pr, ui, scope, c, i, a = m.groups()
    scope_changed = scope == "C"
    av_val = _CVSS_AV.get(av, 0.0)
    ac_val = _CVSS_AC.get(ac, 0.0)
    pr_val = (
        _CVSS_PR_SCOPE_CHANGED if scope_changed else _CVSS_PR_SCOPE_UNCHANGED
    ).get(pr, 0.0)
    ui_val = _CVSS_UI.get(ui, 0.0)
    c_val = _CVSS_CIA.get(c, 0.0)
    i_val = _CVSS_CIA.get(i, 0.0)
    a_val = _CVSS_CIA.get(a, 0.0)

    iss = 1.0 - (1.0 - c_val) * (1.0 - i_val) * (1.0 - a_val)
    exploitability = 8.22 * av_val * ac_val * pr_val * ui_val
    if scope_changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss
    if impact <= 0:
        return 0.0
    if scope_changed:
        base = _cvss_roundup(min(1.08 * (impact + exploitability), 10.0))
    else:
        base = _cvss_roundup(min(impact + exploitability, 10.0))
    return round(base, 1)


# ---------------------------------------------------------------------------
# Workspace scanner
# ---------------------------------------------------------------------------


def audit_workspace(ws: str, ecosystem: str, manager: str) -> WorkspaceResult:
    """Run the appropriate auditor and return a WorkspaceResult."""
    if ecosystem == "go":
        return _audit_go(ws)
    if ecosystem == "node":
        return _audit_node(ws, manager)
    if ecosystem == "py-uv":
        return _audit_py_uv(ws)
    if ecosystem == "rust":
        return _audit_rust(ws)
    return WorkspaceResult(
        label=ws,
        path=ws,
        ecosystem=ecosystem,
        manager=manager,
        status="skipped",
        skip_reason=f"no auditor for ecosystem {ecosystem!r}",
    )


def run_audit(
    roots: list[str],
    *,
    follow_symlinks: bool = False,
) -> list[WorkspaceResult]:
    """Discover repos under *roots* and audit every workspace found."""
    common_to_repo = discover_repos(roots, follow_symlinks=follow_symlinks)
    results: list[WorkspaceResult] = []
    for repo in sorted(common_to_repo.values()):
        workspaces = _detect_workspaces(repo)
        if not workspaces:
            continue
        repo_roots = list(roots)
        for ws_path, eco, mgr in workspaces:
            result = audit_workspace(ws_path, eco, mgr)
            result.label = repo_label(ws_path, repo_roots)
            results.append(result)
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _worst_severity(results: list[WorkspaceResult]) -> str | None:
    worst: str | None = None
    for r in results:
        worst = _worse(worst, r.counts.worst)
    return worst


def _flag_explicit(argv_list: list[str], flag: str) -> bool:
    """Return True when *flag* (e.g. ``"--fail-on"``) was explicitly passed in argv.

    Used to distinguish "user passed the flag" from "argparse default kicked
    in" without changing the parser's own defaults (which existing tests
    assert directly against ``build_audit_parser().parse_args([])``). Matches
    both ``--flag value`` and ``--flag=value`` forms.
    """
    return any(a == flag or a.startswith(flag + "=") for a in argv_list)


def _exit_code_for(
    results: list[WorkspaceResult],
    fail_on: str,
    fail_on_unknown: bool = False,
) -> int:
    """Return 1 when any result's worst severity >= fail_on threshold, else 0.

    When *fail_on_unknown* is True, any workspace with ``counts.unknown > 0``
    also triggers a non-zero exit, regardless of *fail_on*.
    """
    if fail_on == "never" and not fail_on_unknown:
        return 0
    threshold = _SEV_RANK.get(fail_on, 4)
    for r in results:
        if fail_on != "never":
            w = r.counts.worst
            if w is not None and _SEV_RANK.get(w, 4) <= threshold:
                return 1
        if fail_on_unknown and r.counts.unknown > 0:
            return 1
    return 0


def print_audit_report(
    results: list[WorkspaceResult], roots: list[str], *, fail_on_unknown: bool = False
) -> None:
    vulns = [r for r in results if r.status == "vulns"]
    skipped = [r for r in results if r.status == "skipped"]
    errors = [r for r in results if r.status == "error"]
    ok = [r for r in results if r.status == "ok"]

    print(f"Vulnerability sweep across {len(roots)} root(s)")
    print(
        f"Workspaces: {len(results)} total, {len(ok)} clean, "
        f"{len(vulns)} with vulns, {len(skipped)} skipped, {len(errors)} errors"
    )
    print()

    if not results:
        print("No workspaces found.")
        return

    # Build table
    all_results = sorted(
        results, key=lambda r: (_SEV_RANK.get(r.counts.worst, 4), r.label)
    )
    labels = [r.label for r in all_results]
    ecosystems = [r.ecosystem for r in all_results]
    label_w = column_width(labels, "workspace")
    eco_w = column_width(ecosystems, "ecosystem")
    crit_w = max(len("crit"), 4)
    high_w = max(len("high"), 4)
    mod_w = max(len("mod"), 3)
    low_w = max(len("low"), 3)
    unk_w = max(len("unk"), 3)
    worst_w = max(len("worst"), 8)
    status_w = max(len("status"), 7)

    header = join_row(
        [
            format_cell("workspace", label_w),
            format_cell("ecosystem", eco_w),
            format_cell("crit", crit_w, align="right"),
            format_cell("high", high_w, align="right"),
            format_cell("mod", mod_w, align="right"),
            format_cell("low", low_w, align="right"),
            format_cell("unk", unk_w, align="right"),
            format_cell("worst", worst_w),
            format_cell("status", status_w),
        ]
    )
    print(header)
    print("-" * len(header))

    for r in all_results:
        worst_str = r.counts.worst or "-"
        status_str = r.status
        if r.status == "skipped":
            status_str = f"skip:{r.skip_reason[:20]}" if r.skip_reason else "skipped"
        elif r.status == "error":
            status_str = f"error:{r.error[:20]}" if r.error else "error"
        print(
            join_row(
                [
                    format_cell(r.label, label_w),
                    format_cell(r.ecosystem, eco_w),
                    format_cell(
                        str(r.counts.critical) if r.status == "vulns" else "-",
                        crit_w,
                        align="right",
                    ),
                    format_cell(
                        str(r.counts.high) if r.status == "vulns" else "-",
                        high_w,
                        align="right",
                    ),
                    format_cell(
                        str(r.counts.moderate) if r.status == "vulns" else "-",
                        mod_w,
                        align="right",
                    ),
                    format_cell(
                        str(r.counts.low) if r.status == "vulns" else "-",
                        low_w,
                        align="right",
                    ),
                    format_cell(
                        str(r.counts.unknown) if r.status == "vulns" else "-",
                        unk_w,
                        align="right",
                    ),
                    format_cell(worst_str, worst_w),
                    format_cell(status_str, status_w),
                ]
            )
        )

    worst_overall = _worst_severity(results)
    if worst_overall:
        print()
        print(f"Worst severity across fleet: {worst_overall}")

    total_unknown = sum(r.counts.unknown for r in results)
    if total_unknown > 0:
        print()
        print(
            f"Note: {total_unknown} finding(s) with unknown severity (govulncheck). "
            "Use --fail-on-unknown to gate on these."
        )

    if skipped:
        print()
        print(f"Skipped ({len(skipped)}):")
        for r in skipped:
            print(f"  {r.label}: {r.skip_reason}")


def collect_all_remediations(results: list[WorkspaceResult]) -> list[RemediationItem]:
    """Flatten per-workspace remediation lists into a single deduplicated list.

    Deduplication key: (package, ecosystem).  When the same package appears in
    multiple workspaces the *first* entry (by workspace path) is kept so the
    caller always receives one actionable item per package-per-ecosystem, not
    one per workspace.  The workspace field therefore records the first
    workspace where the fixable vulnerability was detected.
    """
    seen: set[tuple[str, str]] = set()
    out: list[RemediationItem] = []
    for r in results:
        for item in r.remediations:
            key = (item.package, item.ecosystem)
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def print_fix_plan(remediations: list[RemediationItem]) -> None:
    """Print a human-readable remediation table to stdout."""
    if not remediations:
        print("No actionable remediations found.")
        return
    print(f"Fix plan — {len(remediations)} remediation(s):")
    print()
    pkg_w = max(len("package"), max(len(r.package) for r in remediations))
    ver_w = max(len("safe_version"), max(len(r.safe_version) for r in remediations))
    eco_w = max(len("ecosystem"), max(len(r.ecosystem) for r in remediations))
    ws_w = max(len("workspace"), max(len(r.workspace) for r in remediations))
    header = join_row(
        [
            format_cell("package", pkg_w),
            format_cell("safe_version", ver_w),
            format_cell("ecosystem", eco_w),
            format_cell("workspace", ws_w),
        ]
    )
    print(header)
    print("-" * len(header))
    for r in remediations:
        print(
            join_row(
                [
                    format_cell(r.package, pkg_w),
                    format_cell(r.safe_version, ver_w),
                    format_cell(r.ecosystem, eco_w),
                    format_cell(r.workspace, ws_w),
                ]
            )
        )
    print()
    print(
        "Note: --fix-plan is read-only. Apply fixes with: repo-ops audit --fix"
    )


def apply_remediations(
    remediations: list[RemediationItem],
    *,
    allow_dirty: bool = False,
) -> list[ApplyOutcome]:
    """Apply each remediation's workspace via the upgrade apply machinery.

    For each unique workspace in *remediations*, constructs a :class:`Workspace`
    object (from ``repo_ops_models``), lazily registers the ecosystem manager
    with the upgrade registry, and delegates to
    ``orchestration.process_workspace`` with ``do_apply=True``.

    Only the managers needed by the supplied remediations are imported and
    registered.  Workspaces whose manager package is unavailable are recorded
    as ``"skip"`` outcomes rather than raising.

    Default behavior (detect-only, ``--fix-plan``) is unchanged — this
    function is only called when the user passes ``--fix``.
    """
    from repo_ops_core.git import git_repo_for
    from repo_ops_models import Workspace as _Workspace
    from repo_ops_models import WorkspaceResult as _UpgradeResult
    from repo_ops_upgrade import orchestration

    # Deduplicate by workspace path; carry (ecosystem, manager) for each.
    # For node workspaces, detect the actual package manager from lockfiles.
    seen: dict[str, tuple[str, str]] = {}
    for item in remediations:
        if item.workspace not in seen:
            eco = item.ecosystem
            mgr = _eco_to_manager(eco)
            if eco == "node":
                detected = _detect_node_manager(Path(item.workspace))
                if detected:
                    mgr = detected
            seen[item.workspace] = (eco, mgr)

    # Build a per-workspace dict of advisory-safe version overrides for JS managers.
    # These are injected into the targeted-bump module before calling process_workspace
    # so that targeted_bump_behind uses pkg@safe_version instead of pkg@latest.
    _js_ws_overrides: dict[str, dict[str, str]] = {}
    for item in remediations:
        if item.ecosystem == "node":
            _js_ws_overrides.setdefault(item.workspace, {})[item.package] = (
                item.safe_version
            )

    # Go workspaces bypass the registry/orchestration path entirely: apply
    # directly via `go get`/`go mod tidy` (mirrors the standalone
    # `_apply_cargo_targeted` pattern) rather than going through
    # `repo_ops_go`'s registered applier, which has no targeted-bump concept.
    _go_ws_items: dict[str, list[RemediationItem]] = {}
    for item in remediations:
        if item.ecosystem == "go":
            _go_ws_items.setdefault(item.workspace, []).append(item)

    outcomes: list[ApplyOutcome] = []
    for ws_path, (ecosystem, manager) in seen.items():
        if ecosystem == "go":
            if shutil.which("go") is None:
                outcomes.append(ApplyOutcome(ws_path, "skip", "go not on PATH"))
            else:
                outcomes.append(
                    _apply_go_targeted(ws_path, _go_ws_items.get(ws_path, []))
                )
            continue

        if not _try_register_manager(manager):
            outcomes.append(
                ApplyOutcome(
                    ws_path, "skip", f"manager {manager!r} package not available"
                )
            )
            continue

        # For JS workspaces: inject advisory-safe version overrides so the
        # targeted-bump path uses `pkg@safe_version` instead of `pkg@latest`.
        _js_clear = None
        if ecosystem == "node":
            ws_overrides = _js_ws_overrides.get(ws_path, {})
            if ws_overrides:
                if manager == "npm":
                    from repo_ops_js_npm.npm import (
                        clear_npm_audit_version_overrides,
                        set_npm_audit_version_overrides,
                    )

                    set_npm_audit_version_overrides(ws_overrides)
                    _js_clear = clear_npm_audit_version_overrides
                elif manager == "pnpm":
                    from repo_ops_js_pnpm.pnpm import (
                        clear_pnpm_audit_version_overrides,
                        set_pnpm_audit_version_overrides,
                    )

                    set_pnpm_audit_version_overrides(ws_overrides)
                    _js_clear = clear_pnpm_audit_version_overrides
                elif manager == "yarn":
                    from repo_ops_js_yarn.yarn import (
                        clear_yarn_audit_version_overrides,
                        set_yarn_audit_version_overrides,
                    )

                    set_yarn_audit_version_overrides(ws_overrides)
                    _js_clear = clear_yarn_audit_version_overrides
                elif manager == "bun":
                    from repo_ops_js_bun.bun import (
                        clear_bun_audit_version_overrides,
                        set_bun_audit_version_overrides,
                    )

                    set_bun_audit_version_overrides(ws_overrides)
                    _js_clear = clear_bun_audit_version_overrides

        path = Path(ws_path)
        git_root = git_repo_for(path) or path
        ws = _Workspace(
            path=path,
            ecosystem=ecosystem,
            manager=manager,
            git_repo=git_root,
            label=ws_path,
        )
        up_result = _UpgradeResult(workspace=ws)
        try:
            final = orchestration.process_workspace(
                up_result,
                do_apply=True,
                do_verify=False,
                verify_test=False,
                skip_remediations=set(),
                allow_dirty=allow_dirty,
                on_verify_fail="keep",
                quiet=True,
            )
            outcomes.append(
                ApplyOutcome(
                    ws_path,
                    final.status,
                    final.error or "",
                    len(final.changes),
                )
            )
        except Exception as exc:
            outcomes.append(ApplyOutcome(ws_path, "fail", str(exc)))
        finally:
            # Always clear JS overrides so they don't bleed into subsequent calls.
            if _js_clear is not None:
                _js_clear()

    return outcomes


# ---------------------------------------------------------------------------
# SARIF 2.1.0 report builder
# ---------------------------------------------------------------------------

_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/"
    "master/Schemata/sarif-schema-2.1.0.json"
)

_SEV_TO_SARIF_LEVEL: dict[str | None, str] = {
    "critical": "error",
    "high": "error",
    "moderate": "warning",
    "low": "note",
    None: "warning",
}


def build_sarif_report(
    results: list[WorkspaceResult],
    remediations: list[RemediationItem],
) -> dict[str, Any]:
    """Build a SARIF 2.1.0 document for the audit findings.

    One rule is emitted per unique (advisory-id, ecosystem) pair, where the
    advisory-id is derived from the package name.  One result is emitted per
    :class:`RemediationItem`.  Severity is drawn from the worst severity seen
    in the workspace that produced the finding.

    The SARIF document validates against the official 2.1.0 schema and is
    suitable for upload to GitHub code-scanning via
    ``github/codeql-action/upload-sarif``: ``artifactLocation.uri`` is emitted
    repo-relative (never an absolute host path) with a matching
    ``run.originalUriBaseIds`` entry for the ``%SRCROOT%`` base, since GitHub
    code-scanning ingestion resolves ``uriBaseId``-qualified locations against
    that mapping.
    """
    # Build workspace-path → (worst-severity, repo-relative label) indexes.
    # ``r.label`` is already computed relative to the scan roots by
    # ``repo_label`` (see run_audit), so it doubles as a repo-relative URI —
    # an absolute host path here would not resolve against %SRCROOT% on
    # GitHub's code-scanning ingestion.
    ws_severity: dict[str, str | None] = {r.path: r.counts.worst for r in results}
    ws_label: dict[str, str] = {r.path: r.label for r in results}

    rules: list[dict[str, Any]] = []
    sarif_results: list[dict[str, Any]] = []
    seen_rules: set[str] = set()

    for item in remediations:
        rule_id = f"audit/{item.ecosystem}/{item.package}"
        if rule_id not in seen_rules:
            seen_rules.add(rule_id)
            rules.append(
                {
                    "id": rule_id,
                    "name": item.package,
                    "shortDescription": {
                        "text": (
                            f"Vulnerable {item.ecosystem} dependency: {item.package}"
                        )
                    },
                    "helpUri": "",
                    "properties": {
                        "ecosystem": item.ecosystem,
                        "fix_version": item.safe_version,
                    },
                }
            )

        severity = ws_severity.get(item.workspace)
        level = _SEV_TO_SARIF_LEVEL.get(severity, "warning")
        # Repo-relative URI: prefer the pre-computed scan-root-relative label;
        # fall back to a path stripped of any leading slash so an unmatched
        # workspace never emits an absolute host path.
        rel_uri = ws_label.get(item.workspace) or item.workspace.lstrip("/")
        rel_uri = rel_uri.replace("\\", "/")
        sarif_results.append(
            {
                "ruleId": rule_id,
                "level": level,
                "message": {
                    "text": (
                        f"{item.package} has a known vulnerability in "
                        f"{item.ecosystem} workspace '{item.workspace}'; "
                        f"safe version: {item.safe_version}"
                    )
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": rel_uri,
                                "uriBaseId": "%SRCROOT%",
                            }
                        }
                    }
                ],
            }
        )

    return {
        "$schema": _SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "repo-ops-audit",
                        "informationUri": ("https://github.com/Rethunk-Tech/repo-ops"),
                        "rules": rules,
                    }
                },
                # Maps the %SRCROOT% uriBaseId used by every result's
                # artifactLocation to the checkout root, per SARIF 2.1.0
                # §3.14. GitHub's code-scanning ingestion substitutes the
                # actual checkout path for %SRCROOT% at upload time; this
                # entry keeps the document self-consistent and schema-valid.
                "originalUriBaseIds": {"%SRCROOT%": {"uri": "file:///"}},
                "results": sarif_results,
            }
        ],
    }


def build_audit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops audit",
        description=(
            "Fleet vulnerability sweep: run the native auditor per ecosystem "
            "across all discovered workspaces (read-only unless --fix)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
exit codes:
  0  no vulnerabilities at or above --fail-on threshold (default: high)
     and no actionable remediations when --fix-plan is active
  1  at least one workspace has vulns at or above the threshold,
     or --fix-plan is active and at least one remediation is available

severity buckets:
  critical / high / moderate / low  — standard severities
  unknown  — govulncheck findings (no per-finding severity from this tool)
             use --fail-on-unknown to treat these as failures

tools used (must be on PATH or workspace is skipped):
  go      govulncheck
  node    npm / yarn / pnpm / bun (per lockfile)
  py-uv   uv run pip-audit --format json
  rust    cargo audit
""",
    )
    add_roots_argument(parser)
    add_follow_symlinks_argument(parser)
    parser.add_argument(
        "--fail-on",
        choices=list(_FAIL_ON_CHOICES),
        default="high",
        dest="fail_on",
        metavar="SEVERITY",
        help=(
            "Exit 1 when any workspace has a vulnerability at or above SEVERITY "
            "(critical/high/moderate/low/never). Default: high."
        ),
    )
    parser.add_argument(
        "--fail-on-unknown",
        action="store_true",
        default=False,
        dest="fail_on_unknown",
        help=(
            "Exit 1 when any workspace has findings with unknown severity "
            "(govulncheck). Default: off."
        ),
    )
    parser.add_argument(
        "--fix-plan",
        action="store_true",
        default=False,
        dest="fix_plan",
        help=(
            "Emit a machine-readable remediation list: for each vulnerable "
            "dependency where the auditor reports a known-safe fix version, "
            "print (package, safe_version, ecosystem, workspace). "
            "Combined with --json the list is included in the JSON payload "
            "under the 'remediations' key. "
            "Exits 1 when any actionable remediations are found. "
            "Read-only: no dependencies are modified."
        ),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        default=False,
        dest="fix",
        help=(
            "Apply remediations: for each workspace that has a known-safe fix "
            "version, delegate to the upgrade apply machinery "
            "(repo-ops-upgrade orchestration.process_workspace). "
            "Each affected workspace is upgraded via its native package manager; "
            "no commit is made automatically. "
            "Mutually exclusive intent with --fix-plan (--fix implies the plan). "
            "Skips workspaces with dirty git worktrees unless --allow-dirty is set."
        ),
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        default=False,
        dest="allow_dirty",
        help=(
            "With --fix: apply remediations even when the workspace has "
            "uncommitted changes (default: skip dirty worktrees)."
        ),
    )
    add_sarif_argument(parser)
    add_json_argument(parser, dest="json_out")
    add_verbose_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    parser = build_audit_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)

    # Config-driven fail-on gates: promote TOML config-file values (global +
    # per-repo) into REPO_OPS_AUDIT_FAIL_ON / REPO_OPS_AUDIT_FAIL_ON_UNKNOWN
    # env vars (never overriding an already-set env var), then resolve with
    # precedence CLI flag > env > default. The parser's own defaults
    # ("high" / False) are left untouched so callers using build_audit_parser()
    # directly still see them; the env layer is applied here in main() only.
    apply_config_to_env(
        global_cfg=load_global_config(), repo_cfg=load_repo_config(Path.cwd())
    )
    fail_on = args.fail_on
    if not _flag_explicit(argv_list, "--fail-on"):
        env_fail_on = os.environ.get("REPO_OPS_AUDIT_FAIL_ON", "").strip()
        if env_fail_on:
            fail_on = env_fail_on

    roots = resolve_scan_roots(list(args.roots), parser)
    follow_symlinks = bool(getattr(args, "follow_symlinks", False))
    fail_on_unknown = bool(getattr(args, "fail_on_unknown", False))
    if not _flag_explicit(argv_list, "--fail-on-unknown"):
        fail_on_unknown = os.environ.get("REPO_OPS_AUDIT_FAIL_ON_UNKNOWN", "") == "1"
    fix_plan = bool(getattr(args, "fix_plan", False))
    do_fix = bool(getattr(args, "fix", False))
    allow_dirty = bool(getattr(args, "allow_dirty", False))
    sarif_out: str | None = getattr(args, "sarif_out", None)

    # --fix implies --fix-plan (collect remediations regardless)
    if do_fix:
        fix_plan = True

    results = run_audit(roots, follow_symlinks=follow_symlinks)

    sev_exit = _exit_code_for(results, fail_on, fail_on_unknown)

    # Collect remediations when either fix-plan or fix is active.
    remediations: list[RemediationItem] = []
    if fix_plan or sarif_out:
        remediations = collect_all_remediations(results)

    # --- SARIF output -------------------------------------------------------
    if sarif_out:
        sarif_doc = build_sarif_report(results, remediations)
        Path(sarif_out).write_text(json.dumps(sarif_doc, indent=2), encoding="utf-8")

    # --- GitHub step-summary (auto when GITHUB_STEP_SUMMARY env var is set) --
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        from repo_ops_core.report import (
            build_audit_summary,
            write_step_summary,
        )

        summary_md = build_audit_summary(results, remediations, roots=roots)
        write_step_summary(summary_md)

    # --- JSON output --------------------------------------------------------
    if args.json_out:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "roots": roots,
            "workspaces": [
                {
                    "label": r.label,
                    "path": r.path,
                    "ecosystem": r.ecosystem,
                    "manager": r.manager,
                    "status": r.status,
                    "skip_reason": r.skip_reason or None,
                    "error": r.error or None,
                    "counts": r.counts.as_dict(),
                    "worst": r.counts.worst,
                }
                for r in results
            ],
            "summary": {
                "total": len(results),
                "ok": sum(1 for r in results if r.status == "ok"),
                "vulns": sum(1 for r in results if r.status == "vulns"),
                "skipped": sum(1 for r in results if r.status == "skipped"),
                "errors": sum(1 for r in results if r.status == "error"),
                "worst": _worst_severity(results),
                "total_unknown": sum(r.counts.unknown for r in results),
            },
        }
        if fix_plan:
            payload["remediations"] = [r.as_dict() for r in remediations]
            plan_exit = 1 if remediations else 0
        else:
            plan_exit = 0

        # --fix: apply and include outcomes in JSON
        if do_fix and remediations:
            apply_outcomes = apply_remediations(remediations, allow_dirty=allow_dirty)
            payload["apply_outcomes"] = [o.as_dict() for o in apply_outcomes]

        print(json.dumps(payload, indent=None if args.compact else 2))
        return sev_exit or plan_exit

    # --- Human output -------------------------------------------------------
    print_audit_report(results, roots, fail_on_unknown=fail_on_unknown)
    plan_exit = 0
    if fix_plan:
        print()
        print_fix_plan(remediations)
        plan_exit = 1 if remediations else 0

    if do_fix and remediations:
        apply_outcomes = apply_remediations(remediations, allow_dirty=allow_dirty)
        print()
        print(f"Fix apply — {len(apply_outcomes)} workspace(s) processed:")
        for outcome in apply_outcomes:
            status_str = outcome.status
            if outcome.error:
                status_str = f"{outcome.status}: {outcome.error[:60]}"
            elif outcome.status == "ok" and outcome.changes_count:
                status_str = f"ok ({outcome.changes_count} change(s))"
            print(f"  {outcome.workspace}: {status_str}")

    return sev_exit or plan_exit


if __name__ == "__main__":
    sys.exit(main())
