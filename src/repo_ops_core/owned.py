"""Only checkouts whose own origin org is ours may be applied to, committed in or pushed."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from repo_ops_core.config import ConfigError, global_config_path, load_global_config
from repo_ops_core.git import run_git

_SCP_RE = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)$")


def owned_orgs() -> set[str]:
    """Lowercase allow-list from global config ``owned_orgs``; no key, no owned org."""
    configured = load_global_config().get("owned_orgs")
    if configured is None:
        raise ConfigError(global_config_path(), "owned_orgs is not set")
    return {str(o).lower() for o in configured}


def adopted_repos() -> set[str]:
    """Lowercase ``org/repo`` slugs from global config ``adopted``: forks swept like our own."""
    configured = load_global_config().get("adopted")
    return {str(s).lower() for s in configured} if isinstance(configured, list) else set()


def _origin_parts(repo: Path) -> list[str] | None:
    """Path segments of *repo*'s origin URL (lowercase), or None with no origin."""
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
        return []
    return [p.lower() for p in parts]


def origin_org(repo: Path) -> str | None:
    """Org (or Azure DevOps namespace) of *repo*'s ``remote.origin.url``, lowercase."""
    parts = _origin_parts(repo)
    if parts is None:
        return None
    return parts[0] if parts else ""


def origin_slug(repo: Path) -> str:
    """``org/repo`` of *repo*'s origin, lowercase, without ``.git``; empty when unknown."""
    parts = _origin_parts(repo) or []
    return "/".join(parts[:2]).removesuffix(".git") if len(parts) >= 2 else ""


def foreign_repos(repos: list[Path]) -> dict[Path, str]:
    """Repos a sweep must leave alone, mapped to the reason.

    Not-our-org origins, and forks (an ``upstream`` remote) unless the global
    ``adopted`` list names them. A repo with no origin cannot be pushed, so it stays.
    """
    allowed = owned_orgs()
    adopted = adopted_repos()
    out: dict[Path, str] = {}
    for repo in repos:
        if not repo.is_dir():
            continue
        org = origin_org(repo)
        if org is not None and org not in allowed:
            out[repo] = f"not our org: {repo} ({org or 'unrecognised origin'})"
        elif origin_slug(repo) in adopted:
            continue
        elif run_git(repo, "config", "--get", "remote.upstream.url").stdout.strip():
            out[repo] = f"fork: {repo}"
    return out


def filter_owned(repos: list[Path]) -> list[Path]:
    """*repos* minus the ones :func:`foreign_repos` names; each skip is printed."""
    skipped = foreign_repos(repos)
    for reason in skipped.values():
        print(f"skipped: {reason}")
    return [r for r in repos if r not in skipped]
