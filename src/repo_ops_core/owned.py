"""Only checkouts whose own origin org is ours may be applied to, committed in or pushed."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

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

_SCP_RE = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)$")


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
    if m := _SCP_RE.match(url):
        host, path = m.group(1), m.group(2)
    else:
        parsed = urlsplit(url)
        host, path = parsed.hostname or "", parsed.path
    host = host.lower()
    parts = [p for p in path.split("/") if p]
    if host in ("dev.azure.com", "ssh.dev.azure.com"):
        if parts[:1] == ["v3"]:
            parts = parts[1:]
    elif host != "github.com":
        return ""
    return parts[0].lower() if parts else ""


def foreign_repos(repos: list[Path]) -> dict[Path, str]:
    """Repos a sweep must leave alone, mapped to the reason.

    Not-our-org origins, and forks (an ``upstream`` remote). A repo with no
    origin cannot be pushed, so it stays.
    """
    allowed = owned_orgs()
    out: dict[Path, str] = {}
    for repo in repos:
        if not repo.is_dir():
            continue
        org = origin_org(repo)
        if org is not None and org not in allowed:
            out[repo] = f"not our org: {repo} ({org or 'unrecognised origin'})"
        elif run_git(repo, "config", "--get", "remote.upstream.url").stdout.strip():
            out[repo] = f"fork: {repo}"
    return out


def filter_owned(repos: list[Path]) -> list[Path]:
    """*repos* minus the ones :func:`foreign_repos` names; each skip is printed."""
    skipped = foreign_repos(repos)
    for reason in skipped.values():
        print(f"skipped: {reason}")
    return [r for r in repos if r not in skipped]
