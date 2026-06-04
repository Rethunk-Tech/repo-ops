"""GitHub Actions workflow pin audit and apply."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_models import ActionBump

from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    WorkflowParseError,
    parse_workflow_uses,
    update_workflow_pins,
)

_TAG_CACHE: dict[str, dict | None] = {}

_MAJOR_TAG_RE = re.compile(r"^v(\d+)(?:\.|$)")

# Full 40-hex SHA or short sha<hex> prefix
_SHA_RE = re.compile(r"^(?:sha[0-9a-f]+|[0-9a-f]{7,40})$", re.IGNORECASE)

TagStyle = Literal["major", "exact"]


def discover_workflow_files(repo: Path) -> list[Path]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def action_tag_info(
    action: str, *, cache: dict[str, dict | None] | None = None
) -> dict | None:
    """Return tag info dict for a GitHub Action via ``gh api repos/<action>/tags``.

    Returns a dict with:
      - ``bare_majors``: set of integer majors that have a bare ``vN`` tag.
      - ``max_major``: maximum integer major across all version tags, or None.
      - ``sha_to_tags``: mapping of commit SHA → list of tag names (for SHA unpin).
        Each line from the API is parsed as "name sha" or "name" (name-only for
        backwards compat with existing test mocks that return names only).

    Returns None when ``gh`` is absent or the API call fails (caller should skip
    the pin — mirrors old graceful degradation).
    """
    if not shutil.which("gh"):
        return None
    store = _TAG_CACHE if cache is None else cache
    if action in store:
        return store[action]
    try:
        proc = run(
            [
                "gh",
                "api",
                f"repos/{action}/tags",
                "--paginate",
                "--jq",
                r'.[] | "\(.name) \(.commit.sha)"',
            ],
            Path.cwd(),
            timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            store[action] = None
            return None
        bare_majors: set[int] = set()
        all_majors: set[int] = set()
        sha_to_tags: dict[str, list[str]] = {}
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            name = parts[0]
            sha = parts[1] if len(parts) > 1 else None
            if sha:
                sha_to_tags.setdefault(sha.lower(), []).append(name)
            # Bare major: exactly "vN"
            if re.fullmatch(r"v\d+", name):
                try:
                    bare_majors.add(int(name[1:]))
                except ValueError:
                    pass
            m = _MAJOR_TAG_RE.match(name)
            if m:
                try:
                    all_majors.add(int(m.group(1)))
                except ValueError:
                    pass
        max_major = max(all_majors) if all_majors else None
        result: dict = {
            "bare_majors": bare_majors,
            "max_major": max_major,
            "sha_to_tags": sha_to_tags,
        }
        store[action] = result
        return result
    except subprocess.TimeoutExpired:
        print(f"WARN: gh api timed out for {action}", file=sys.stderr)
    store[action] = None
    return None


def _resolve_sha_target(
    sha: str,
    *,
    comment_hint: str | None,
    info: dict | None,
    tag_style: TagStyle,
) -> tuple[str, str] | None:
    """Resolve a SHA pin to a target tag string and a human description.

    Returns ``(target_tag, description)`` or ``None`` when unresolvable.

    Resolution order:
    1. Comment hint ``# vN[.N.N]``: use major N (e.g. ``v4``) as the major.
    2. SHA→tag mapping from the tags API: find tags pointing at this SHA, pick
       the highest semver one to determine the major.

    When ``tag_style='exact'``, ``target_tag`` is the highest ``vN.M.P`` within
    the resolved major (or ``@vN`` fallback when no exact tags exist).
    When ``tag_style='major'``, ``target_tag`` is ``vN``.
    """
    # --- Step 1: resolve major ---
    resolved_major: int | None = None
    source_desc = ""

    if comment_hint:
        m = re.match(r"^v(\d+)", comment_hint)
        if m:
            resolved_major = int(m.group(1))
            source_desc = f"comment hint {comment_hint}"

    if resolved_major is None and info is not None:
        sha_lower = sha.lower()
        # Normalise sha prefix-form: "sha<hex>" → strip "sha" prefix
        if sha_lower.startswith("sha"):
            sha_lower = sha_lower[3:]
        tags_for_sha = info.get("sha_to_tags", {}).get(sha_lower, [])
        if tags_for_sha:
            # Pick the highest semver among tags pointing at this SHA.
            best: str | None = None
            best_tuple: tuple[int, ...] = ()
            for t in tags_for_sha:
                if not _MAJOR_TAG_RE.match(t):
                    continue
                stripped = t.lstrip("v")
                tup = semver_tuple(stripped)
                if tup > best_tuple:
                    best_tuple = tup
                    best = t
            if best:
                m2 = _MAJOR_TAG_RE.match(best)
                if m2:
                    resolved_major = int(m2.group(1))
                    source_desc = f"SHA resolves to {best}"

    if resolved_major is None:
        return None

    major_tag = f"v{resolved_major}"

    # --- Step 2: pick target based on tag_style ---
    if tag_style == "major":
        return major_tag, f"unpin SHA to {major_tag} (via {source_desc})"

    # exact: find highest vN.M.P within this major
    exact_tag: str | None = None
    if info is not None:
        best_exact: str | None = None
        best_exact_tup: tuple[int, ...] = ()
        for sha_tags in info.get("sha_to_tags", {}).values():
            for t in sha_tags:
                m3 = _MAJOR_TAG_RE.match(t)
                if not m3 or int(m3.group(1)) != resolved_major:
                    continue
                if re.fullmatch(r"v\d+\.\d+\.\d+.*", t):
                    stripped = t.lstrip("v")
                    tup = semver_tuple(stripped)
                    if tup > best_exact_tup:
                        best_exact_tup = tup
                        best_exact = t
        # Also scan all_majors tags from sha_to_tags full names list
        # (may include tags on different commits within the same major)
        # We need ALL tags for the major, not just ones mapping to this SHA.
        # The info dict doesn't directly provide this, so we build it from sha_to_tags.
        all_in_major: list[str] = []
        for tags_list in info.get("sha_to_tags", {}).values():
            for t in tags_list:
                m4 = _MAJOR_TAG_RE.match(t)
                if m4 and int(m4.group(1)) == resolved_major:
                    if re.fullmatch(r"v\d+\.\d+\.\d+.*", t):
                        all_in_major.append(t)
        if all_in_major:
            best_in_major = max(all_in_major, key=lambda t: semver_tuple(t.lstrip("v")))
            best_in_major_tup = semver_tuple(best_in_major.lstrip("v"))
            if best_in_major_tup > best_exact_tup:
                best_exact = best_in_major
        exact_tag = best_exact

    target = exact_tag if exact_tag else major_tag
    style_note = "exact" if exact_tag else "major (no exact tag found)"
    return target, f"unpin SHA to {target} ({style_note}; via {source_desc})"


def _run_actionlint(wf: Path, repo: Path) -> str | None:
    """Return a lint error string, or None when lint passes or is unavailable."""
    if not shutil.which("actionlint"):
        return None
    try:
        proc = run(["actionlint", str(wf)], repo, timeout=60)
    except subprocess.TimeoutExpired:
        return "actionlint timed out"
    if proc.returncode != 0:
        return proc.stderr.strip() or proc.stdout.strip() or "actionlint failed"
    return None


def scan_actions(
    repo: Path,
    apply: bool,
    *,
    keep_sha: bool = True,
    tag_style: TagStyle = "major",
    tag_cache: dict[str, dict | None] | None = None,
) -> tuple[list[ActionBump], int]:
    """Scan GitHub Actions workflow files and produce bump recommendations.

    Parameters
    ----------
    repo:
        Repository root (parent of ``.github/workflows/``).
    apply:
        When True, rewrite workflow files for actionable bumps.
    keep_sha:
        When True (the default), SHA-pinned actions are reported only and never
        rewritten — preserving supply-chain hardening.  Pass ``keep_sha=False``
        (CLI: ``--actions-unpin-sha``) to opt in to resolving SHA pins to
        version tags.
    tag_style:
        ``"major"`` (default) → rewrite SHA pins to ``@vN``.
        ``"exact"`` → rewrite to the highest ``@vN.M.P`` within the resolved major.
    tag_cache:
        Optional shared cache dict mapping action name → tag info (or None on
        failure).  When provided, entries are reused across calls so each
        (action, ref) is resolved at most once per run.  When omitted, a fresh
        per-call dict is used (preserves test isolation).
    """
    if not shutil.which("actionlint"):
        print(
            "WARN: actionlint not on PATH; skipping action validation", file=sys.stderr
        )
    workflows = discover_workflow_files(repo)
    bumps: list[ActionBump] = []
    _cache: dict[str, dict | None] = tag_cache if tag_cache is not None else {}
    for wf in workflows:
        rel = str(wf.relative_to(repo))
        lint_err = _run_actionlint(wf, repo)
        if lint_err:
            bumps.append(
                ActionBump(
                    workflow=rel,
                    action="(actionlint)",
                    pin="",
                    latest="",
                    status="fail",
                    detail=lint_err,
                )
            )
            print(
                f"WARN: {rel}: actionlint failed; pin bumps skipped for this workflow",
                file=sys.stderr,
            )
            continue
        try:
            pins = parse_workflow_uses(wf, workflow_rel=rel)
        except WorkflowParseError as exc:
            bumps.append(
                ActionBump(
                    workflow=rel,
                    action="(yaml)",
                    pin="",
                    latest="",
                    status="fail",
                    detail=str(exc),
                )
            )
            continue
        replacements: list[PinReplacement] = []
        for uses in pins:
            action, pin = uses.action, uses.pin
            # SHA-pinned: attempt to resolve to a version tag (unless keep_sha).
            if _SHA_RE.fullmatch(pin):
                if keep_sha:
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=pin,
                            status="ok",
                            detail="SHA-pinned; report only",
                        )
                    )
                    continue

                info = action_tag_info(action, cache=_cache)
                resolution = _resolve_sha_target(
                    pin,
                    comment_hint=uses.comment,
                    info=info,
                    tag_style=tag_style,
                )
                if resolution is None:
                    # Cannot resolve: keep reporting, do not rewrite.
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=pin,
                            status="ok",
                            detail="SHA-pinned; report only (unresolvable: no tag maps to this SHA)",
                        )
                    )
                    continue

                target_tag, detail = resolution
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action=action,
                        pin=pin,
                        latest=target_tag,
                        status="ok",
                        detail=detail,
                    )
                )
                if apply:
                    replacements.append(
                        PinReplacement(
                            action=action,
                            pin=pin,
                            latest=target_tag,
                            line=uses.line or None,
                            drop_comment=False,  # preserve the # vN audit hint
                        )
                    )
                continue

            # Parse current major from pin (vN, vN.x.y). Skip non-version refs.
            m = re.match(r"^v(\d+)", pin)
            if not m:
                continue
            M = int(m.group(1))
            desired = f"v{M}"

            info = action_tag_info(action, cache=_cache)
            if info is None:
                # gh absent or API failed — skip this pin.
                continue

            bare_major_exists = M in info["bare_majors"]
            max_major = info["max_major"]

            if pin == desired:
                # Already at floating major. Report newer major if available.
                if max_major is not None and max_major > M:
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=f"v{max_major}",
                            status="ok",
                            detail=f"newer major v{max_major} available; not auto-applied (report only)",
                        )
                    )
                # else: up to date, emit nothing
            else:
                # Exact/minor pin (e.g. v3.19.0) — try to collapse to floating major.
                if bare_major_exists:
                    detail = f"normalize to floating major v{M}"
                    if max_major is not None and max_major > M:
                        detail += f"; newer major v{max_major} available"
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=desired,
                            status="ok",
                            detail=detail,
                        )
                    )
                    if apply:
                        replacements.append(
                            PinReplacement(
                                action=action,
                                pin=pin,
                                latest=desired,
                                line=uses.line or None,
                            )
                        )
                else:
                    # No bare major tag upstream — cannot safely collapse.
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=pin,
                            status="ok",
                            detail="no floating major tag upstream; report only",
                        )
                    )
        if apply and replacements:
            try:
                update_workflow_pins(wf, replacements)
            except WorkflowParseError as exc:
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action="(yaml)",
                        pin="",
                        latest="",
                        status="fail",
                        detail=str(exc),
                    )
                )
    return bumps, len(workflows)
