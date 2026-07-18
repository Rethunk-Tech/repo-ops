"""Reusable GitHub step-summary and markdown rollup writer for repo-ops commands.

Usage
-----
Call :func:`write_step_summary` with a markdown string; it writes to the path
held in ``$GITHUB_STEP_SUMMARY`` when that env var is set (GitHub Actions), and
is a no-op otherwise.  Each subcommand may build its own markdown via the
``build_*_summary`` helpers below; only ``build_audit_summary`` is wired now
but the module is kept generic for future subcommands.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def write_step_summary(content: str, *, path: str | None = None) -> bool:
    """Append *content* to the GitHub step-summary file.

    When *path* is ``None`` (default) the ``$GITHUB_STEP_SUMMARY`` environment
    variable is used.  The file is *appended* so multiple calls within the same
    step accumulate output.

    Returns ``True`` when the file was written, ``False`` when the target path
    is unknown (env var absent and no explicit *path*).
    """
    target = path or os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return False
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(content)
        if content and not content.endswith("\n"):
            fh.write("\n")
    return True


def build_audit_summary(
    results: Sequence[object],
    remediations: Sequence[object],
    *,
    roots: list[str] | None = None,
) -> str:
    """Build a concise audit findings + remediations markdown block.

    Parameters
    ----------
    results:
        List of ``WorkspaceResult`` objects from :mod:`repo_ops_audit.audit`.
    remediations:
        List of ``RemediationItem`` objects (output of ``collect_all_remediations``).
    roots:
        Optional list of scanned root directories for context.

    Returns a markdown string suitable for ``$GITHUB_STEP_SUMMARY``.
    """
    # Import here to keep report.py dependency-free at module load time;
    # audit is not a dependency of repo-ops-core.
    lines: list[str] = []

    # ---- Header -------------------------------------------------------------
    lines.append("## repo-ops audit")
    if roots:
        roots_str = ", ".join(f"`{r}`" for r in roots[:3])
        suffix = f" (and {len(roots) - 3} more)" if len(roots) > 3 else ""
        lines.append(f"**Roots:** {roots_str}{suffix}")
    lines.append("")

    # ---- Summary counts -----------------------------------------------------
    total = len(results)
    ok_count = sum(1 for r in results if getattr(r, "status", None) == "ok")
    vuln_count = sum(1 for r in results if getattr(r, "status", None) == "vulns")
    skip_count = sum(1 for r in results if getattr(r, "status", None) == "skipped")
    err_count = sum(1 for r in results if getattr(r, "status", None) == "error")

    lines.append("| Total | Clean | Vulns | Skipped | Errors |")
    lines.append("|-------|-------|-------|---------|--------|")
    lines.append(
        f"| {total} | {ok_count} | {vuln_count} | {skip_count} | {err_count} |"
    )
    lines.append("")

    # ---- Vulnerable workspaces table ----------------------------------------
    vuln_results = [r for r in results if getattr(r, "status", None) == "vulns"]
    if vuln_results:
        lines.append("### Vulnerable workspaces")
        lines.append("")
        lines.append("| Workspace | Ecosystem | Crit | High | Mod | Low | Unk |")
        lines.append("|-----------|-----------|------|------|-----|-----|-----|")
        for r in vuln_results:
            c = getattr(r, "counts", None)
            crit = getattr(c, "critical", "-") if c else "-"
            high = getattr(c, "high", "-") if c else "-"
            mod = getattr(c, "moderate", "-") if c else "-"
            low = getattr(c, "low", "-") if c else "-"
            unk = getattr(c, "unknown", "-") if c else "-"
            label = getattr(r, "label", getattr(r, "path", "?"))
            eco = getattr(r, "ecosystem", "?")
            lines.append(
                f"| `{label}` | {eco} | {crit} | {high} | {mod} | {low} | {unk} |"
            )
        lines.append("")

    # ---- Remediations table -------------------------------------------------
    if remediations:
        lines.append("### Actionable remediations")
        lines.append("")
        lines.append("| Package | Safe version | Ecosystem | Workspace |")
        lines.append("|---------|-------------|-----------|-----------|")
        for item in remediations:
            pkg = getattr(item, "package", "?")
            ver = getattr(item, "safe_version", "?")
            eco = getattr(item, "ecosystem", "?")
            ws = getattr(item, "workspace", "?")
            lines.append(f"| `{pkg}` | `{ver}` | {eco} | `{ws}` |")
        lines.append("")
    else:
        lines.append("_No actionable remediations found._")
        lines.append("")

    return "\n".join(lines)
