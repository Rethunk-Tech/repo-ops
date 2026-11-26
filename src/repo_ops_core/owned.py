"""Only checkouts whose own origin org is ours may be applied to, committed in or pushed."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from repo_ops_core.config import ConfigError, global_config_path, load_global_config
from repo_ops_core.exclude import merge_exclude_patterns, repo_matches_exclude
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


def _origin(repo: Path) -> tuple[str, list[str]] | None:
    """Host and path segments (lowercase) of *repo*'s origin URL, or None with no origin."""
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
        return host, []
    return host, [p.lower() for p in parts]


def origin_org(repo: Path) -> str | None:
    """Org (or Azure DevOps namespace) of *repo*'s ``remote.origin.url``, lowercase."""
    origin = _origin(repo)
    if origin is None:
        return None
    return origin[1][0] if origin[1] else ""


def origin_slug(repo: Path) -> str:
    """``org/repo`` of *repo*'s origin, lowercase, without ``.git``; empty when unknown."""
    parts = (_origin(repo) or ("", []))[1]
    return "/".join(parts[:2]).removesuffix(".git") if len(parts) >= 2 else ""


def _github_slug(repo: Path) -> str:
    """:func:`origin_slug` for a github.com origin only (an Azure DevOps path is no GitHub repo)."""
    origin = _origin(repo)
    return origin_slug(repo) if origin and origin[0] == "github.com" else ""


def _fork_cache() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "repo-ops" / "forks.json"


def gh_fork_lookup(slugs: set[str]) -> dict[str, bool]:
    """Whether GitHub says each ``org/repo`` is a fork; a slug gh cannot answer for is absent.

    Whether a repo is a fork never changes, so every answer is kept on disk and
    one aliased query asks for the rest.
    """
    cache_path = _fork_cache()
    try:
        cached = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cached = {}
    known: dict[str, bool] = cached if isinstance(cached, dict) else {}
    todo = sorted(s for s in slugs if s not in known)
    if todo:
        fields = " ".join(
            f'r{i}: repository(owner: "{s.split("/")[0]}", name: "{s.split("/")[1]}") {{ isFork }}'
            for i, s in enumerate(todo)
        )
        try:
            proc = subprocess.run(
                ["gh", "api", "graphql", "-f", f"query=query {{ {fields} }}"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            data = json.loads(proc.stdout).get("data") or {}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            data = {}
        for i, slug in enumerate(todo):
            node = data.get(f"r{i}")
            if isinstance(node, dict) and isinstance(node.get("isFork"), bool):
                known[slug] = node["isFork"]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(known))
        tmp.replace(cache_path)
    return {s: known[s] for s in slugs if s in known}


def foreign_repos(
    repos: list[Path], fork_lookup: Callable[[set[str]], dict[str, bool]] | None = None
) -> dict[Path, str]:
    """Repos a sweep must leave alone, mapped to the reason.

    Not-our-org origins, and forks (an ``upstream`` remote, or a GitHub origin
    GitHub says is a fork) unless the global ``adopted`` list names them. A repo
    with no origin cannot be pushed, so it stays.
    """
    allowed = owned_orgs()
    adopted = adopted_repos()
    ask = fork_lookup or gh_fork_lookup
    out: dict[Path, str] = {}
    undecided: list[Path] = []
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
        elif _github_slug(repo):
            undecided.append(repo)
    forks = ask({_github_slug(r) for r in undecided}) if undecided else {}
    for repo in undecided:
        if forks.get(_github_slug(repo)):
            out[repo] = f"fork: {repo}"
    return out


def filter_owned(repos: list[Path]) -> list[Path]:
    """*repos* minus the ones :func:`foreign_repos` names; each skip is printed."""
    skipped = foreign_repos(repos)
    for reason in skipped.values():
        print(f"skipped: {reason}")
    return [r for r in repos if r not in skipped]


def build_owned_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops owned",
        description="Say which checkouts a sweep would touch: ours, or skipped as excluded, "
        "not our org or a fork. Reads the same config as plan and apply.",
    )
    parser.add_argument("paths", nargs="+", help="Checkout roots to judge")
    parser.add_argument(
        "--json",
        action="store_true",
        help='Print {"<path>": {"owned": bool, "reason": str|null}} for every path',
    )
    return parser


def main_owned(argv: list[str] | None = None) -> int:
    args = build_owned_parser().parse_args(argv)
    try:
        patterns = merge_exclude_patterns(None, load_global_config().get("exclude"))
        paths = [Path(p) for p in args.paths]
        excluded = {p for p in paths if repo_matches_exclude(str(p), str(p), patterns)}
        skipped = foreign_repos([p for p in paths if p not in excluded])
    except ConfigError as exc:
        print(f"repo-ops owned: {exc}", file=sys.stderr)
        return 2
    verdicts: dict[str, dict[str, object]] = {}
    for path in paths:
        reason = "excluded" if path in excluded else skipped.get(path, "").split(":")[0] or None
        verdicts[str(path)] = {"owned": reason is None, "reason": reason}
    if args.json:
        print(json.dumps(verdicts))
    else:
        for path, v in verdicts.items():
            print(f"{'owned' if v['owned'] else 'skipped: ' + str(v['reason'])}\t{path}")
    return 0
