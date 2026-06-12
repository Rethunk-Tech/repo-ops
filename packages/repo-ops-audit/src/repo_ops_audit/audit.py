#!/usr/bin/env python3
"""Fleet vulnerability sweep across local git repos.

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
    add_verbose_argument,
    resolve_scan_roots,
    wire_verbose,
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
class WorkspaceResult:
    label: str
    path: str
    ecosystem: str
    manager: str
    status: str  # "ok" | "skipped" | "error" | "vulns"
    skip_reason: str = ""
    counts: VulnCount = field(default_factory=VulnCount)
    error: str = ""


# ---------------------------------------------------------------------------
# Ecosystem detection
# ---------------------------------------------------------------------------


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


def _run(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    """Run *cmd* in *cwd*; return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"timeout after {_SUBPROCESS_TIMEOUT}s"
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
    rc, stdout, stderr = _run(["govulncheck", "-json", "./..."], ws)
    counts = VulnCount()
    # govulncheck --json emits one JSON object per line.
    # govulncheck does NOT emit per-finding severity; all findings are counted
    # in the ``unknown`` bucket.  Use --fail-on-unknown to gate on these.
    parse_ok = False
    try:
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            parse_ok = True
            if "finding" in obj:
                counts.unknown += 1
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
    return WorkspaceResult(
        label=label,
        path=ws,
        ecosystem="go",
        manager="go",
        status=status,
        counts=counts,
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
        "bun": ["bun", "audit"],  # bun audit has no --json as of 1.x
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
        # bun audit outputs human-readable text only; parse summary line
        # e.g. "3 vulnerabilities (1 critical, 2 high)"
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
        )

    # npm / pnpm / yarn all emit JSON
    try:
        data = json.loads(stdout)
        counts = _parse_npm_audit_json(data)
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


def _probe_pip_audit(ws: str) -> bool:
    """Return True when pip-audit is available via ``uv run pip-audit --version``."""
    rc, stdout, _stderr = _run(["uv", "run", "pip-audit", "--version"], ws)
    if rc != 0:
        return False
    # pip-audit --version outputs something like "pip-audit 2.x.y"
    return "pip-audit" in (stdout + _stderr).lower() or rc == 0


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
    )


def _cvss_to_score(cvss_vector: str) -> float:
    """Approximate CVSS base score from vector string.

    cargo audit embeds the CVSS v3 vector; extract the numeric score from the
    ``CVSS:3.x/AV:...`` prefix if present, else return 5.0 (moderate bucket).
    """
    # cargo audit --json includes "cvss" as e.g. "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    # The actual base score is NOT embedded in the vector — we parse severity from
    # the Confidentiality/Integrity/Availability impacts to approximate.
    impact_map = {"N": 0.0, "L": 0.22, "H": 0.56}
    m = re.search(r"C:([NLH])/I:([NLH])/A:([NLH])", cvss_vector)
    if not m:
        return 5.0  # moderate
    c_val = impact_map.get(m.group(1), 0.0)
    i_val = impact_map.get(m.group(2), 0.0)
    a_val = impact_map.get(m.group(3), 0.0)
    # Approximate: high triple-H → ~9.8; partial → proportional estimate
    approx = (c_val + i_val + a_val) / (3 * 0.56) * 10.0
    return round(approx, 1)


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


def build_audit_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops audit",
        description=(
            "Fleet vulnerability sweep: run the native auditor per ecosystem "
            "across all discovered workspaces (read-only)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
exit codes:
  0  no vulnerabilities at or above --fail-on threshold (default: high)
  1  at least one workspace has vulns at or above the threshold

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
    add_json_argument(parser, dest="json_out")
    add_verbose_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_audit_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)

    roots = resolve_scan_roots(list(args.roots), parser)
    follow_symlinks = bool(getattr(args, "follow_symlinks", False))
    fail_on_unknown = bool(getattr(args, "fail_on_unknown", False))
    results = run_audit(roots, follow_symlinks=follow_symlinks)

    if args.json_out:
        payload = {
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
        print(json.dumps(payload, indent=None if args.compact else 2))
        return _exit_code_for(results, args.fail_on, fail_on_unknown)

    print_audit_report(results, roots, fail_on_unknown=fail_on_unknown)
    return _exit_code_for(results, args.fail_on, fail_on_unknown)


if __name__ == "__main__":
    sys.exit(main())
