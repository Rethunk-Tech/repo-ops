"""Only checkouts whose own origin org is ours may be applied to, committed in or pushed."""

from __future__ import annotations

import re
from pathlib import Path

from repo_ops_core.config import load_global_config
from repo_ops_core.git import run_git

DEFAULT_OWNED_ORGS = (
    "Rethunk-Tech",
    "Rethunk-AI",
    "Albino-Geek-Services",
    "Citadel-Inc",
    "PeleOs-LLC",
    "AlbinoGeek",
    "LethalModding",
    "Totative",
)

_AZURE_RE = re.compile(r"dev\.azure\.com[:/](?:v3/)?([^/\s]+)")


def owned_orgs() -> set[str]:
    """Lowercase allow-list: global config ``owned_orgs`` or :data:`DEFAULT_OWNED_ORGS`."""
    configured = load_global_config().get("owned_orgs")
    orgs = configured if isinstance(configured, list) else DEFAULT_OWNED_ORGS
    return {str(o).lower() for o in orgs}


def origin_org(repo: Path) -> str | None:
    """Org (or Azure DevOps namespace) of *repo*'s ``remote.origin.url``, lowercase."""
    proc = run_git(repo, "config", "--get", "remote.origin.url")
    url = proc.stdout.strip() if proc.returncode == 0 else ""
    if not url:
        return None
    m = _AZURE_RE.search(url) or re.search(r"github\.com[:/]([^/\s]+)", url)
    return m.group(1).lower() if m else ""


def foreign_repos(repos: list[Path]) -> dict[Path, str]:
    """Repos whose origin org is not owned, mapped to that org ("" when unrecognised).

    A repo with no origin cannot be pushed, so it stays.
    """
    allowed = owned_orgs()
    out: dict[Path, str] = {}
    for repo in repos:
        org = origin_org(repo)
        if org is not None and org not in allowed:
            out[repo] = org
    return out
