"""Keep Go upgrades off versions the Go vulnerability database lists as affected.

The latest release is not always a fixed one: a fix can land in older release
lines and on the main branch before the next minor ships. Upgrading to such a
latest turns a clean module vulnerable, and the verify gate's govulncheck then
fails the whole workspace. The sweep instead takes the newest release that is
not affected, never going below the version already required.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_core.http import open_url
from repo_ops_core.version_utils import semver_precedence

logger = logging.getLogger(__name__)

VULNDB = "https://vuln.go.dev"

# One affected range as (introduced, fixed); "" introduced means from the start, "" fixed means open.
Range = tuple[str, str]


def _fetch_json(url: str) -> object:
    with open_url(url, timeout=TIMEOUT_CHECK) as resp:
        return json.load(resp)


@functools.cache
def _module_advisories() -> dict[str, tuple[str, ...]] | None:
    """Module path -> advisory IDs, or None when the database is unreachable."""
    try:
        index = _fetch_json(f"{VULNDB}/index/modules.json")
    except (OSError, ValueError) as e:
        logger.warning("go vulndb unreachable, upgrades are not screened: %s", e)
        return None
    if not isinstance(index, list):
        return None
    out: dict[str, tuple[str, ...]] = {}
    for entry in index:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            ids = [v.get("id") for v in entry.get("vulns") or [] if isinstance(v, dict)]
            out[entry["path"]] = tuple(i for i in ids if isinstance(i, str))
    return out


@functools.cache
def _affected_ranges(module_path: str) -> tuple[Range, ...]:
    advisories = _module_advisories()
    if not advisories:
        return ()
    ranges: list[Range] = []
    for vuln_id in advisories.get(module_path, ()):
        try:
            doc = _fetch_json(f"{VULNDB}/ID/{vuln_id}.json")
        except (OSError, ValueError) as e:
            logger.warning("go vulndb: could not read %s: %s", vuln_id, e)
            continue
        for affected in doc.get("affected", []) if isinstance(doc, dict) else []:
            if (affected.get("package") or {}).get("name") != module_path:
                continue
            for r in affected.get("ranges") or []:
                ranges.extend(_ranges_from_events(r.get("events") or []))
    return tuple(ranges)


def _ranges_from_events(events: list[dict[str, str]]) -> list[Range]:
    out: list[Range] = []
    introduced: str | None = None
    for ev in events:
        if "introduced" in ev:
            introduced = "" if ev["introduced"] == "0" else ev["introduced"]
        elif "fixed" in ev and introduced is not None:
            out.append((introduced, ev["fixed"]))
            introduced = None
    if introduced is not None:
        out.append((introduced, ""))
    return out


def is_affected(module_path: str, version: str) -> bool:
    v = semver_precedence(version)
    return any(
        (not lo or semver_precedence(lo) <= v) and (not hi or v < semver_precedence(hi))
        for lo, hi in _affected_ranges(module_path)
    )


def safe_go_target(
    module_path: str,
    current: str,
    proposed: str,
    released: Callable[[], list[str]],
) -> str | None:
    """The version to upgrade *module_path* to, or None to leave it where it is.

    *proposed* when it is clean; otherwise the newest clean release above
    *current* and no newer than *proposed*. *released* lists the module's
    tagged versions and is only called when *proposed* is affected.
    """
    if not is_affected(module_path, proposed):
        return proposed
    floor, ceiling = semver_precedence(current), semver_precedence(proposed)
    candidates = [
        v
        for v in released()
        if floor < semver_precedence(v) <= ceiling and not is_affected(module_path, v)
    ]
    if candidates:
        best = max(candidates, key=semver_precedence)
        logger.info("%s: %s is affected, taking %s", module_path, proposed, best)
        return best
    logger.info(
        "%s: %s is affected and no clean release is newer than %s", module_path, proposed, current
    )
    return None
